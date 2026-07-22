import inspect
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    CandidateResponseError,
    ChatMessage,
    CostEstimate,
    CostEstimationQuality,
    ModelFamilyProfile,
    ModelPricingSnapshot,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
    TokenUsage,
)
from agrefactor.repair.candidate_loop import (
    BoundedCandidateRepairLoop,
)
from agrefactor.repair.protocol import (
    RepairModelObservation,
    RepairObservedUsage,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    BudgetUsage,
    RunResult,
    RunStatus,
)
import agrefactor.runtime.budget as budget_module
import agrefactor.repair.candidate_loop as candidate_loop_module
import agrefactor.runtime.candidate_repair_integration as candidate_integration_module
import agrefactor.smoke.stage2_fault_matrix as stage2_fault_matrix_module
import agrefactor.smoke.stage2_pass_matrix as stage2_pass_matrix_module


SOURCE_HASH = "c" * 64
CURRENT = 'extern "C" int top(int x) { return x; }\n'
REPAIRED = 'extern "C" int top(int x) { return x + 1; }\n'

TASK = TaskSpec(
    task_id="native-cost-accounting",
    kernel_path="/operator/private/top.cpp",
    kernel_name="top",
    target=TargetProfile(
        name="native-cost-target",
        toolchain="vitis_hls",
        toolchain_version="2023.2",
    ),
)


def pricing_snapshot(
    *,
    model_id="billing-model-v1",
    currency="CNY",
    status=PricingVerificationStatus.OFFICIAL_VERIFIED,
    cache_partition=False,
):
    rates = (
        (
            PricingRate(
                token_category="cache_hit_input",
                amount_per_billing_unit="0.1",
            ),
            PricingRate(
                token_category="cache_miss_input",
                amount_per_billing_unit="1",
            ),
            PricingRate(
                token_category="output",
                amount_per_billing_unit="2",
            ),
        )
        if cache_partition
        else (
            PricingRate(
                token_category="input",
                amount_per_billing_unit="1",
            ),
            PricingRate(
                token_category="output",
                amount_per_billing_unit="2",
            ),
        )
    )
    return ModelPricingSnapshot(
        provider="billing-provider",
        model_id=model_id,
        official_source_identity="Official billing page",
        official_source_url=(
            "https://example.invalid/official-pricing"
        ),
        retrieved_at="2026-07-22T00:00:00+00:00",
        verification_status=status,
        applicability=PricingApplicability(
            region="global",
            billing_mode="real-time",
        ),
        currency=currency,
        billing_unit_tokens=1_000_000,
        rates=rates,
        effective_date="2026-07-22",
        source_content_sha256=SOURCE_HASH,
    )


def estimate(
    amount,
    currency,
    *,
    quality=CostEstimationQuality.VERIFIED,
):
    return CostEstimate(
        quality=quality,
        amount=amount,
        currency=currency,
        pricing_snapshot_sha256="d" * 64,
    )


def unavailable_estimate(currency="CNY"):
    return CostEstimate(
        quality=CostEstimationQuality.UNAVAILABLE,
        currency=currency,
        pricing_snapshot_sha256="d" * 64,
        unpriced_token_categories=("input",),
    )


def model_usage(
    *,
    prompt=1000,
    completion=100,
    cost_usd=None,
    estimated_cost=None,
    breakdown=None,
):
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost_usd,
        estimated_cost=estimated_cost,
        breakdown=breakdown,
    )


def budget_usage(
    *,
    cost_usd=0.0,
    costs_by_currency=None,
):
    return BudgetUsage(
        llm_calls=0,
        tool_calls=0,
        compile_calls=0,
        csim_calls=0,
        csynth_calls=0,
        tokens=0,
        cost_usd=cost_usd,
        elapsed_s=0.0,
        costs_by_currency=(
            {} if costs_by_currency is None else costs_by_currency
        ),
    )



# CandidateRepairPromptInputs validates FeedbackReport, so construct a valid
# prompt once through a minimal direct LayeredPrompt-compatible helper.
def candidate_request():
    from agrefactor.prompts import LayeredPrompt

    return CandidateModelRequest(
        prompt=LayeredPrompt(
            messages=(
                ChatMessage(
                    role="system",
                    content="Return one replacement.",
                ),
                ChatMessage(
                    role="user",
                    content="Repair the candidate.",
                ),
            ),
            manifest={
                "purpose": "candidate_compile_repair",
                "task_id": TASK.task_id,
                "kernel_name": TASK.kernel_name,
                "target_profile": TASK.target.name,
                "editable_artifacts": ["candidate_kernel"],
                "output_contract": {
                    "artifact_name": "candidate_kernel",
                    "language": "cpp",
                    "complete_replacement": True,
                    "fenced_code_block": True,
                    "commentary_allowed": False,
                },
            },
        ),
        task=TASK,
        current_candidate=CURRENT,
    )


class FakeProvider(ModelProvider):
    def __init__(
        self,
        usage,
        *,
        response_text=None,
    ):
        self.usage = usage
        self.response_text = (
            response_text
            if response_text is not None
            else f"```cpp\n{REPAIRED}\n```"
        )
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        return ModelResponse(
            text=self.response_text,
            model=model.model,
            usage=self.usage,
            finish_reason="stop",
            metadata={"request_id": "fake-request"},
        )


def adapter(
    provider,
    *,
    snapshot=None,
    allow_approximate=False,
):
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_family_profile(
        ModelFamilyProfile(name="generic")
    )
    registry.register_model(
        ModelSpec(
            name="billing-model",
            provider="fake",
            model="billing-model-v1",
            family="generic",
        )
    )
    return CandidateModelAdapter(
        registry=registry,
        model_name="billing-model",
        pricing_snapshot=snapshot,
        allow_approximate_cost=allow_approximate,
    )


class NativeCostAccountingTests(unittest.TestCase):
    def test_budget_usage_legacy_usd_creates_currency_entry(self):
        usage = budget_usage(cost_usd=0.25)
        self.assertEqual(
            usage.costs_by_currency,
            {"USD": Decimal("0.25")},
        )
        self.assertEqual(usage.cost_usd, 0.25)

    def test_budget_usage_currency_map_derives_usd_view(self):
        usage = budget_usage(
            costs_by_currency={
                "USD": Decimal("1.5"),
                "CNY": Decimal("2.25"),
            }
        )
        self.assertEqual(usage.cost_usd, 1.5)
        self.assertEqual(
            usage.costs_by_currency["CNY"],
            Decimal("2.25"),
        )

    def test_budget_usage_rejects_conflicting_usd_views(self):
        with self.assertRaisesRegex(
            ValueError,
            "cost_usd",
        ):
            budget_usage(
                cost_usd=1.0,
                costs_by_currency={"USD": Decimal("2")},
            )

    def test_budget_usage_rejects_invalid_currency(self):
        with self.assertRaisesRegex(
            ValueError,
            "currency",
        ):
            budget_usage(
                costs_by_currency={"yuan": Decimal("1")},
            )

    def test_budget_usage_cost_mapping_is_immutable(self):
        usage = budget_usage(
            costs_by_currency={"CNY": Decimal("1")}
        )
        with self.assertRaises(TypeError):
            usage.costs_by_currency["CNY"] = Decimal("2")

    def test_budget_usage_to_dict_preserves_legacy_fields(self):
        payload = budget_usage(
            cost_usd=0.5,
            costs_by_currency={
                "USD": Decimal("0.5"),
                "CNY": Decimal("1.25"),
            },
        ).to_dict()
        self.assertEqual(payload["cost_usd"], 0.5)
        self.assertEqual(
            payload["costs_by_currency"],
            {"CNY": "1.25", "USD": "0.5"},
        )
        json.dumps(payload, sort_keys=True)

    def test_budget_manager_legacy_cost_uses_single_usd_ledger(self):
        manager = BudgetManager()
        manager.consume(cost_usd=0.25)
        manager.record_observed(cost_usd=0.5)
        usage = manager.snapshot()
        self.assertEqual(
            usage.costs_by_currency["USD"],
            Decimal("0.75"),
        )
        self.assertEqual(usage.cost_usd, 0.75)
        self.assertFalse(hasattr(manager, "_cost_usd"))

    def test_budget_manager_records_native_verified_estimate(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate("0.0012", "CNY"),
            )
        )
        usage = manager.snapshot()
        self.assertEqual(
            usage.costs_by_currency,
            {"CNY": Decimal("0.0012")},
        )
        self.assertEqual(usage.cost_usd, 0.0)
        self.assertEqual(usage.tokens, 1100)

    def test_budget_manager_sums_same_currency_exactly(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                prompt=1,
                completion=0,
                estimated_cost=estimate("0.1", "CNY"),
            )
        )
        manager.record_model_usage(
            model_usage(
                prompt=2,
                completion=0,
                estimated_cost=estimate("0.2", "CNY"),
            )
        )
        self.assertEqual(
            manager.snapshot().costs_by_currency["CNY"],
            Decimal("0.3"),
        )

    def test_budget_manager_tracks_multiple_currencies(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate("1", "CNY"),
            )
        )
        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate("2", "JPY"),
            )
        )
        self.assertEqual(
            manager.snapshot().costs_by_currency,
            {
                "CNY": Decimal("1"),
                "JPY": Decimal("2"),
            },
        )

    def test_budget_manager_usd_estimate_updates_legacy_view_once(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                cost_usd=0.25,
                estimated_cost=estimate("0.25", "USD"),
            )
        )
        usage = manager.snapshot()
        self.assertEqual(
            usage.costs_by_currency,
            {"USD": Decimal("0.25")},
        )
        self.assertEqual(usage.cost_usd, 0.25)

    def test_budget_manager_rejects_conflicting_usd_estimate(self):
        manager = BudgetManager()
        with self.assertRaisesRegex(
            ValueError,
            "conflicting",
        ):
            manager.record_model_usage(
                model_usage(
                    cost_usd=0.5,
                    estimated_cost=estimate("0.25", "USD"),
                )
            )

    def test_budget_manager_rejects_non_usd_with_legacy_usd(self):
        manager = BudgetManager()
        with self.assertRaisesRegex(
            ValueError,
            "non-USD",
        ):
            manager.record_model_usage(
                model_usage(
                    cost_usd=0.5,
                    estimated_cost=estimate("1", "CNY"),
                )
            )

    def test_unavailable_estimate_does_not_add_native_amount(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                estimated_cost=unavailable_estimate(),
            )
        )
        self.assertEqual(
            manager.snapshot().costs_by_currency,
            {},
        )

    def test_approximate_estimate_is_observed_and_recorded(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate(
                    "0.75",
                    "CNY",
                    quality=CostEstimationQuality.APPROXIMATE,
                ),
            )
        )
        self.assertEqual(
            manager.snapshot().costs_by_currency["CNY"],
            Decimal("0.75"),
        )

    def test_non_usd_cost_does_not_exhaust_usd_limit(self):
        manager = BudgetManager(
            BudgetLimits(max_cost_usd=0.25)
        )
        self.assertFalse(manager.exhausted())

        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate("100", "CNY"),
            )
        )

        usage = manager.snapshot()
        self.assertEqual(usage.cost_usd, 0.0)
        self.assertEqual(
            usage.costs_by_currency,
            {"CNY": Decimal("100")},
        )
        self.assertFalse(manager.exhausted())

    def test_usd_estimate_can_exhaust_legacy_usd_limit(self):
        manager = BudgetManager(
            BudgetLimits(max_cost_usd=0.25)
        )
        manager.record_model_usage(
            model_usage(
                cost_usd=0.25,
                estimated_cost=estimate("0.25", "USD"),
            )
        )
        self.assertTrue(manager.exhausted())

    def test_record_observed_remains_soft_after_completed_call(self):
        manager = BudgetManager(
            BudgetLimits(max_cost_usd=0.1)
        )
        usage = manager.record_observed(cost_usd=0.2)
        self.assertEqual(usage.cost_usd, 0.2)
        self.assertTrue(manager.exhausted())

    def test_adapter_without_snapshot_is_unchanged(self):
        original = model_usage(prompt=10, completion=2)
        model_adapter = adapter(FakeProvider(original))
        result = model_adapter.generate(candidate_request())
        self.assertIs(result.response.usage, original)
        self.assertIsNone(
            result.response.usage.estimated_cost
        )
        self.assertNotIn(
            "pricing_estimation_attempted",
            result.response.metadata,
        )

    def test_adapter_rejects_snapshot_model_mismatch(self):
        with self.assertRaisesRegex(
            ValueError,
            "model_id",
        ):
            adapter(
                FakeProvider(model_usage()),
                snapshot=pricing_snapshot(
                    model_id="different-model"
                ),
            )

    def test_adapter_rejects_non_boolean_approximate_flag(self):
        with self.assertRaises(TypeError):
            registry = ModelRegistry()
            provider = FakeProvider(model_usage())
            registry.register_provider(provider)
            registry.register_family_profile(
                ModelFamilyProfile(name="generic")
            )
            registry.register_model(
                ModelSpec(
                    name="billing-model",
                    provider="fake",
                    model="billing-model-v1",
                    family="generic",
                )
            )
            CandidateModelAdapter(
                registry=registry,
                model_name="billing-model",
                allow_approximate_cost=1,
            )

    def test_adapter_attaches_verified_native_estimate(self):
        model_adapter = adapter(
            FakeProvider(
                model_usage(
                    prompt=1000,
                    completion=100,
                )
            ),
            snapshot=pricing_snapshot(),
        )
        result = model_adapter.generate(candidate_request())
        cost = result.response.usage.estimated_cost
        self.assertIsNotNone(cost)
        self.assertIs(
            cost.quality,
            CostEstimationQuality.VERIFIED,
        )
        self.assertEqual(cost.amount, Decimal("0.0012"))
        self.assertEqual(cost.currency, "CNY")
        self.assertIsNone(result.response.usage.cost_usd)

    def test_adapter_preserves_exact_snapshot_identity(self):
        snapshot = pricing_snapshot()
        result = adapter(
            FakeProvider(model_usage()),
            snapshot=snapshot,
        ).generate(candidate_request())
        self.assertEqual(
            result.response.usage.estimated_cost
            .pricing_snapshot_sha256,
            snapshot.pricing_snapshot_sha256,
        )
        self.assertEqual(
            result.response.metadata[
                "pricing_snapshot_sha256"
            ],
            snapshot.pricing_snapshot_sha256,
        )

    def test_adapter_metadata_records_quality_not_hidden_text(self):
        result = adapter(
            FakeProvider(model_usage()),
            snapshot=pricing_snapshot(),
        ).generate(candidate_request())
        self.assertEqual(
            result.response.metadata[
                "pricing_estimation_quality"
            ],
            "verified",
        )
        self.assertTrue(
            result.response.metadata[
                "pricing_estimation_attempted"
            ]
        )
        self.assertNotIn(
            "reasoning",
            json.dumps(result.response.metadata).lower(),
        )

    def test_adapter_default_missing_partition_is_unavailable(self):
        result = adapter(
            FakeProvider(model_usage()),
            snapshot=pricing_snapshot(
                cache_partition=True
            ),
        ).generate(candidate_request())
        self.assertIs(
            result.response.usage.estimated_cost.quality,
            CostEstimationQuality.UNAVAILABLE,
        )

    def test_adapter_explicit_approximation_is_recorded(self):
        result = adapter(
            FakeProvider(model_usage()),
            snapshot=pricing_snapshot(
                cache_partition=True
            ),
            allow_approximate=True,
        ).generate(candidate_request())
        cost = result.response.usage.estimated_cost
        self.assertIs(
            cost.quality,
            CostEstimationQuality.APPROXIMATE,
        )
        self.assertTrue(cost.assumptions)

    def test_adapter_callback_observes_enriched_response(self):
        observed = []
        result = adapter(
            FakeProvider(model_usage()),
            snapshot=pricing_snapshot(),
        ).generate(
            candidate_request(),
            after_provider_response=observed.append,
        )
        self.assertEqual(len(observed), 1)
        self.assertIs(observed[0], result.response)
        self.assertIsNotNone(
            observed[0].usage.estimated_cost
        )

    def test_adapter_records_enriched_response_before_contract_failure(self):
        provider = FakeProvider(
            model_usage(),
            response_text=f"```cpp\n{CURRENT}\n```",
        )
        model_adapter = adapter(
            provider,
            snapshot=pricing_snapshot(),
        )
        with self.assertRaises(CandidateResponseError):
            model_adapter.generate(candidate_request())
        self.assertEqual(len(model_adapter.responses), 1)
        self.assertIsNotNone(
            model_adapter.last_response.usage.estimated_cost
        )

    def test_candidate_loop_records_model_usage_through_budget_api(self):
        manager = BudgetManager()
        holder = SimpleNamespace(_budget=manager)
        response = ModelResponse(
            text="response",
            model="billing-model-v1",
            usage=model_usage(
                estimated_cost=estimate("1.25", "CNY"),
            ),
        )
        BoundedCandidateRepairLoop._record_model_usage(
            holder,
            response,
        )
        usage = manager.snapshot()
        self.assertEqual(usage.tokens, 1100)
        self.assertEqual(
            usage.costs_by_currency["CNY"],
            Decimal("1.25"),
        )

    def test_repair_observed_usage_carries_budget_currency_delta(self):
        manager = BudgetManager()
        before = manager.snapshot()
        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate("1.25", "CNY"),
            )
        )
        after = manager.snapshot()
        observed = RepairObservedUsage.from_observations(
            before,
            after,
        )
        self.assertEqual(
            observed.costs_by_currency,
            {"CNY": Decimal("1.25")},
        )
        self.assertEqual(
            observed.to_dict()["costs_by_currency"],
            {"CNY": "1.25"},
        )

    def test_repair_observed_usage_can_recover_model_only_estimate(self):
        response = ModelResponse(
            text="response",
            model="billing-model-v1",
            usage=model_usage(
                estimated_cost=estimate("2.5", "JPY"),
            ),
        )
        observation = RepairModelObservation.from_response(
            prompt_manifest={},
            response=response,
            model_call_observed=True,
        )
        observed = RepairObservedUsage.from_observations(
            None,
            None,
            observation,
        )
        self.assertEqual(
            observed.costs_by_currency,
            {"JPY": Decimal("2.5")},
        )

    def test_run_result_serializes_native_currency_ledger(self):
        usage = budget_usage(
            costs_by_currency={"CNY": Decimal("1.25")}
        )
        result = RunResult(
            run_id="run-native-cost",
            task_id="task-native-cost",
            mode=RunMode.REFACTOR,
            status=RunStatus.SUCCEEDED,
            phases=(),
            budget_usage=usage,
        )
        payload = result.to_dict()
        self.assertEqual(
            payload["budget_usage"]["costs_by_currency"],
            {"CNY": "1.25"},
        )
        json.dumps(payload, sort_keys=True)

    def test_candidate_loop_serializers_use_budget_usage_to_dict(self):
        source = inspect.getsource(candidate_loop_module)
        self.assertIn(
            '"budget_before": self.budget_before.to_dict()',
            source,
        )
        self.assertIn(
            '"budget_after": self.budget_after.to_dict()',
            source,
        )
        self.assertIn(
            '"budget_usage": self.budget_usage.to_dict()',
            source,
        )
        self.assertNotIn(
            "asdict(self.budget",
            source,
        )

    def test_budget_manager_has_no_parallel_usd_counter(self):
        source = inspect.getsource(budget_module)
        self.assertNotIn("self._cost_usd =", source)
        self.assertIn("self._costs_by_currency", source)

    def test_all_budget_usage_consumers_use_shared_serializer(self):
        candidate_source = inspect.getsource(
            candidate_integration_module
        )
        self.assertIn(
            '"budget_usage": self.budget_usage.to_dict()',
            candidate_source,
        )
        self.assertNotIn(
            "asdict(self.budget_usage)",
            candidate_source,
        )

        fault_source = inspect.getsource(
            stage2_fault_matrix_module
        )
        self.assertIn(
            '"total_usage": self.total_usage.to_dict()',
            fault_source,
        )
        self.assertNotIn(
            "asdict(self.total_usage)",
            fault_source,
        )

        pass_source = inspect.getsource(
            stage2_pass_matrix_module
        )
        for token in (
            '"budget_before": self.budget_before.to_dict()',
            '"budget_after": self.budget_after.to_dict()',
            '"total_usage": self.total_usage.to_dict()',
        ):
            self.assertIn(token, pass_source)
        self.assertNotIn(
            "asdict(self.budget",
            pass_source,
        )
        self.assertNotIn(
            "asdict(self.total_usage)",
            pass_source,
        )

    def test_native_cost_payload_is_json_serializable(self):
        manager = BudgetManager()
        manager.record_model_usage(
            model_usage(
                estimated_cost=estimate("1.2345", "CNY"),
            )
        )
        payload = manager.snapshot().to_dict()
        json.dumps(payload, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
