from __future__ import annotations

from decimal import Decimal
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import TaskSpec
from agrefactor.evaluation import TestbenchPreflight
from agrefactor.models import (
    EffectiveModelConfig,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    ModelPricingSnapshot,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    PricingRate,
    PricingVerificationStatus,
    TokenUsage,
)
from agrefactor.runtime import (
    BudgetManager,
    RunContext,
    TraceRecorder,
)
from agrefactor.testing import (
    ModelTestbenchRepairer,
    TestbenchRepairRequest,
    build_openai_compatible_testbench_repairer,
)

import agrefactor.compat.legacy_refactor as legacy_module
import agrefactor.testing.factory as factory_module
import agrefactor.testing.model_testbench_repairer as repairer_module
import flow.new as flow_new_module
import flow.tools.general as general_module


ORIGINAL = """
extern "C" void top(int *out) {
    out[0] = 7;
}
"""

CANDIDATE = """
extern "C" void top_hls(int *out) {
    out[0] = 7;
}
"""

BROKEN_TB = """
extern "C" void top(int *);
extern "C" void top_hls(int *);
extern node *root;
int main() {
    root = nullptr;
    int a[1] = {};
    int b[1] = {};
    top(a);
    top_hls(b);
    return a[0] == b[0] ? 0 : 1;
}
"""

FIXED_TB = """
extern "C" void top(int *);
extern "C" void top_hls(int *);
int main() {
    int a[1] = {};
    int b[1] = {};
    top(a);
    top_hls(b);
    return a[0] == b[0] ? 0 : 1;
}
"""


class FakeProvider(ModelProvider):
    def __init__(self, *, usage=None, text=None):
        self.calls = []
        self.usage = usage or TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
        )
        self.text = text or f"```cpp\n{FIXED_TB}\n```"

    @property
    def name(self):
        return "openai-compatible"

    def generate(self, model, request):
        self.calls.append((model, request))
        return ModelResponse(
            text=self.text,
            model=model.model,
            usage=self.usage,
            finish_reason="stop",
        )


def make_snapshot(currency: str) -> ModelPricingSnapshot:
    return ModelPricingSnapshot(
        provider="openai-compatible",
        model_id="fake-model",
        official_source_identity=f"test-{currency}",
        official_source_url="https://example.invalid/pricing",
        retrieved_at="2026-07-23T00:00:00Z",
        verification_status=(
            PricingVerificationStatus.OFFICIAL_VERIFIED
        ),
        currency=currency,
        billing_unit_tokens=1000,
        rates=(
            PricingRate(
                token_category="input",
                amount_per_billing_unit="1",
            ),
            PricingRate(
                token_category="output",
                amount_per_billing_unit="2",
            ),
        ),
        source_content_sha256="a" * 64,
    )


def make_effective_config(
    *,
    currency: str | None = None,
    provider_name: str = "openai-compatible",
    model_id: str = "fake-model",
) -> EffectiveModelConfig:
    snapshot = None
    if currency is not None:
        snapshot = make_snapshot(currency)
        if model_id != "fake-model":
            raise ValueError("priced test config requires fake-model")
    return EffectiveModelConfig(
        logical_model_name="legacy-main",
        provider_name=provider_name,
        model_id=model_id,
        family_profile=(
            GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
        ),
        effective_parameters={"temperature": 0},
        requested_family_name="generic-openai-compatible",
        base_url="https://example.invalid/v1",
        api_key_env="FAKE_API_KEY",
        pricing_snapshot=snapshot,
    )


def make_factory_config() -> EffectiveModelConfig:
    return make_effective_config(
        provider_name="openai-compatible",
    )


def typed_summary(
    *,
    model_name="fake-model",
    prompt=100,
    completion=50,
    total=None,
):
    total_tokens = (
        prompt + completion
        if total is None
        else total
    )
    unavailable = {
        "kind": "unavailable",
        "amount": None,
        "currency": None,
        "quality": "unavailable",
        "source": "test",
        "ledger_eligible": False,
        "complete": False,
        "assumptions": [],
    }
    return {
        "agents": 1,
        "models": {
            model_name: {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "framework_reported_cost": unavailable,
                "estimated_cost": None,
                "costs_by_currency": {},
                "cost_usd": None,
                "cost": None,
                "cost_complete": False,
            }
        },
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total_tokens,
        "source": "test-summary",
        "framework_reported_cost": unavailable,
        "estimated_cost": None,
        "costs_by_currency": {},
        "cost_usd": None,
        "total_cost": None,
        "cost_complete": False,
    }


def make_context():
    task = TaskSpec(
        task_id="integrated-legacy-usage",
        kernel_path="candidate.cpp",
        kernel_name="top_hls",
    )
    return RunContext(
        run_id="integrated-legacy-usage",
        task=task,
        budget=BudgetManager(),
        trace=TraceRecorder("integrated-legacy-usage"),
    )


class IntegratedLegacyUsageClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.preflight = TestbenchPreflight().compile_and_link(
            work_dir=Path(cls._temp.name) / "preflight",
            testbench_code=BROKEN_TB,
            original_code=ORIGINAL,
            candidate_code=CANDIDATE,
        )

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def make_request(self):
        return TestbenchRepairRequest(
            attempt=1,
            max_attempts=2,
            current_testbench=BROKEN_TB,
            original_code=ORIGINAL,
            candidate_code=CANDIDATE,
            preflight=self.preflight,
            task=TaskSpec(
                task_id="repair",
                kernel_path="candidate.cpp",
                kernel_name="top_hls",
            ),
        )

    def test_normalize_without_snapshot_records_tokens_only(self):
        metadata, usages, residual = legacy_module._normalize_usage(
            typed_summary(),
            effective_config=make_effective_config(),
        )
        self.assertEqual(metadata["tokens"], 150)
        self.assertEqual(len(usages), 1)
        self.assertIsNone(usages[0].estimated_cost)
        self.assertEqual(residual, 0)

    def test_normalize_usd_snapshot_estimates_exact_bucket(self):
        metadata, usages, residual = legacy_module._normalize_usage(
            typed_summary(),
            effective_config=make_effective_config(currency="USD"),
        )
        self.assertEqual(residual, 0)
        self.assertEqual(usages[0].estimated_cost.currency, "USD")
        self.assertEqual(
            usages[0].estimated_cost.amount,
            Decimal("0.2"),
        )
        self.assertEqual(usages[0].cost_usd, 0.2)
        self.assertEqual(
            metadata["costs_by_currency"],
            {"USD": "0.2"},
        )

    def test_normalize_non_usd_never_populates_cost_usd(self):
        metadata, usages, _ = legacy_module._normalize_usage(
            typed_summary(),
            effective_config=make_effective_config(currency="CNY"),
        )
        self.assertEqual(usages[0].estimated_cost.currency, "CNY")
        self.assertIsNone(usages[0].cost_usd)
        self.assertEqual(metadata["cost_usd"], 0.0)
        self.assertEqual(
            metadata["costs_by_currency"],
            {"CNY": "0.2"},
        )

    def test_normalize_requires_exact_model_identity_for_pricing(self):
        metadata, usages, _ = legacy_module._normalize_usage(
            typed_summary(model_name="other-model"),
            effective_config=make_effective_config(currency="USD"),
        )
        self.assertIsNone(usages[0].estimated_cost)
        self.assertEqual(
            metadata["models"]["other-model"][
                "pricing_attribution"
            ],
            "model_id_mismatch",
        )

    def test_normalize_preserves_unattributed_residual_tokens(self):
        metadata, usages, residual = legacy_module._normalize_usage(
            typed_summary(total=200),
            effective_config=make_effective_config(currency="USD"),
        )
        self.assertEqual(sum(x.total_tokens for x in usages), 150)
        self.assertEqual(residual, 50)
        self.assertEqual(metadata["unattributed_tokens"], 50)

    def test_adapter_records_usd_native_ledger(self):
        context = make_context()
        adapter = LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=(
                    make_effective_config(currency="USD")
                )
            ),
            backend=lambda **kwargs: (True, None),
            usage_supplier=typed_summary,
        )
        result = adapter(context)
        usage = context.budget.snapshot()
        self.assertEqual(usage.tokens, 150)
        self.assertAlmostEqual(usage.cost_usd, 0.2)
        self.assertEqual(
            usage.to_dict()["costs_by_currency"],
            {"USD": "0.2"},
        )
        self.assertEqual(
            result.metadata["legacy_usage"][
                "costs_by_currency"
            ],
            {"USD": "0.2"},
        )

    def test_adapter_records_non_usd_native_ledger(self):
        context = make_context()
        adapter = LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=(
                    make_effective_config(currency="CNY")
                )
            ),
            backend=lambda **kwargs: (True, None),
            usage_supplier=typed_summary,
        )
        adapter(context)
        usage = context.budget.snapshot()
        self.assertEqual(usage.tokens, 150)
        self.assertEqual(usage.cost_usd, 0.0)
        self.assertEqual(
            usage.to_dict()["costs_by_currency"],
            {"CNY": "0.2"},
        )

    def test_adapter_records_partial_usage_after_backend_error(self):
        context = make_context()

        def backend(**kwargs):
            raise RuntimeError("backend failed")

        adapter = LegacyRefactorAdapter(
            LegacyRefactorSettings(
                effective_model_config=(
                    make_effective_config(currency="CNY")
                )
            ),
            backend=backend,
            usage_supplier=typed_summary,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "backend failed",
        ):
            adapter(context)
        self.assertEqual(context.budget.snapshot().tokens, 150)

    def test_framework_reported_unknown_cost_never_enters_ledger(self):
        summary = typed_summary()
        summary["framework_reported_cost"] = {
            "kind": "framework_reported",
            "amount": "99",
            "currency": None,
            "quality": "reported_unverified_currency",
            "source": "test",
            "ledger_eligible": False,
            "complete": True,
            "assumptions": [],
        }
        context = make_context()
        LegacyRefactorAdapter(
            backend=lambda **kwargs: (True, None),
            usage_supplier=lambda: summary,
        )(context)
        self.assertEqual(
            context.budget.snapshot().to_dict()[
                "costs_by_currency"
            ],
            {},
        )

    def test_repairer_effective_config_is_authoritative(self):
        provider = FakeProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        repairer = ModelTestbenchRepairer(
            registry=registry,
            effective_config=make_effective_config(),
        )
        self.assertEqual(
            repairer.effective_config.model_id,
            "fake-model",
        )
        self.assertEqual(
            repairer.effective_parameters,
            {"temperature": 0},
        )

    def test_repairer_rejects_parallel_model_name(self):
        provider = FakeProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        with self.assertRaises(ValueError):
            ModelTestbenchRepairer(
                registry=registry,
                model_name="parallel",
                effective_config=make_effective_config(),
            )

    def test_repairer_records_llm_call_and_usd_usage(self):
        provider = FakeProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        budget = BudgetManager()
        repairer = ModelTestbenchRepairer(
            registry=registry,
            effective_config=make_effective_config(
                currency="USD"
            ),
            budget=budget,
        )
        repairer.repair(self.make_request())
        usage = budget.snapshot()
        self.assertEqual(usage.llm_calls, 1)
        self.assertEqual(usage.tokens, 150)
        self.assertAlmostEqual(usage.cost_usd, 0.2)

    def test_repairer_records_non_usd_usage(self):
        provider = FakeProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        budget = BudgetManager()
        repairer = ModelTestbenchRepairer(
            registry=registry,
            effective_config=make_effective_config(
                currency="CNY"
            ),
            budget=budget,
        )
        repairer.repair(self.make_request())
        usage = budget.snapshot()
        self.assertEqual(usage.llm_calls, 1)
        self.assertEqual(usage.cost_usd, 0.0)
        self.assertEqual(
            usage.to_dict()["costs_by_currency"],
            {"CNY": "0.2"},
        )

    def test_repairer_records_usage_before_response_rejection(self):
        provider = FakeProvider(
            text="```cpp\nint main() { return 0; }\n```"
        )
        registry = ModelRegistry()
        registry.register_provider(provider)
        budget = BudgetManager()
        repairer = ModelTestbenchRepairer(
            registry=registry,
            effective_config=make_effective_config(
                currency="USD"
            ),
            budget=budget,
        )
        with self.assertRaises(
            repairer_module.TestbenchRepairResponseError
        ):
            repairer.repair(self.make_request())
        self.assertEqual(budget.snapshot().llm_calls, 1)
        self.assertEqual(budget.snapshot().tokens, 150)

    def test_repairer_response_is_enriched(self):
        provider = FakeProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        repairer = ModelTestbenchRepairer(
            registry=registry,
            effective_config=make_effective_config(
                currency="USD"
            ),
        )
        repairer.repair(self.make_request())
        response = repairer.last_response
        self.assertEqual(
            response.usage.estimated_cost.amount,
            Decimal("0.2"),
        )
        self.assertTrue(
            response.metadata["pricing_estimation_attempted"]
        )

    def test_factory_raw_path_uses_generic_profile_not_inference(self):
        repairer = build_openai_compatible_testbench_repairer(
            model="deepseek-chat",
        )
        self.assertEqual(
            repairer.family_profile.name,
            "generic-openai-compatible",
        )

    def test_factory_active_builder_does_not_call_infer(self):
        source = inspect.getsource(
            factory_module
            .build_openai_compatible_testbench_repairer
        )
        self.assertNotIn("infer_model_family(", source)

    def test_factory_accepts_effective_config_and_budget(self):
        budget = BudgetManager()
        repairer = build_openai_compatible_testbench_repairer(
            effective_config=make_factory_config(),
            budget=budget,
        )
        self.assertIs(repairer.budget, budget)
        self.assertTrue(repairer.records_budget_usage)

    def test_factory_rejects_parallel_raw_model(self):
        with self.assertRaises(ValueError):
            build_openai_compatible_testbench_repairer(
                model="parallel",
                effective_config=make_factory_config(),
            )

    def test_settings_inherit_main_config_for_repair(self):
        config = make_effective_config()
        settings = LegacyRefactorSettings(
            effective_model_config=config,
            enable_testbench_repair=True,
        )
        kwargs = build_legacy_refactor_kwargs(
            make_context().task,
            settings,
        )
        self.assertIs(
            kwargs["testbench_repair_effective_config"],
            config,
        )

    def test_settings_accept_dedicated_repair_config(self):
        main = make_effective_config()
        dedicated = make_effective_config(
            model_id="repair-model",
        )
        settings = LegacyRefactorSettings(
            effective_model_config=main,
            enable_testbench_repair=True,
            testbench_repair_model="repair-model",
            testbench_repair_effective_config=dedicated,
        )
        kwargs = build_legacy_refactor_kwargs(
            make_context().task,
            settings,
        )
        self.assertIs(
            kwargs["testbench_repair_effective_config"],
            dedicated,
        )

    def test_settings_reject_conflicting_repair_identity(self):
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(
                enable_testbench_repair=True,
                testbench_repair_model="other",
                testbench_repair_effective_config=(
                    make_effective_config()
                ),
            )

    def test_kwargs_carry_main_effective_config_object(self):
        config = make_effective_config()
        kwargs = build_legacy_refactor_kwargs(
            make_context().task,
            LegacyRefactorSettings(
                effective_model_config=config
            ),
        )
        self.assertIs(kwargs["effective_model_config"], config)

    def test_flow_source_inherits_main_config_for_repair(self):
        source = inspect.getsource(
            flow_new_module.hls_refactor_with_rag
        )
        self.assertIn(
            "resolved_repair_effective_config",
            source,
        )
        self.assertIn(
            "effective_model_config",
            source,
        )

    def test_flow_source_passes_budget_to_repair_factory(self):
        source = inspect.getsource(
            flow_new_module.hls_refactor_with_rag
        )
        self.assertIn("budget=budget", source)

    def test_general_repair_usage_serializes_responses(self):
        provider = FakeProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        budget = BudgetManager()
        repairer = ModelTestbenchRepairer(
            registry=registry,
            effective_config=make_effective_config(
                currency="CNY"
            ),
            budget=budget,
        )
        repairer.repair(self.make_request())
        payload = general_module._collect_testbench_repair_usage(
            repairer
        )
        self.assertTrue(payload["budget_recorded"])
        self.assertEqual(len(payload["responses"]), 1)
        self.assertEqual(
            payload["costs_by_currency"],
            {"CNY": "0.2"},
        )
        json.dumps(payload, sort_keys=True)

    def test_live_repair_usage_is_not_double_recorded(self):
        context = make_context()
        context.budget.consume(llm_calls=1)
        context.budget.record_model_usage(
            TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
            )
        )
        repair = {
            "artifact_path": "/tmp/live-repair.json",
            "model_usage": {
                "budget_recorded": True,
                "calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "responses": [],
                "models": ["repair-model"],
                "costs_by_currency": {},
                "cost_usd": None,
                "cost_complete": False,
            },
        }
        LegacyRefactorAdapter(
            backend=lambda **kwargs: (
                True,
                {"testbench_repair": repair},
            ),
            usage_supplier=lambda: typed_summary(
                prompt=0,
                completion=0,
            ),
        )(context)
        usage = context.budget.snapshot()
        self.assertEqual(usage.llm_calls, 1)
        self.assertEqual(usage.tokens, 15)

    def test_legacy_repair_fallback_is_recorded_once(self):
        context = make_context()
        repair = {
            "artifact_path": "/tmp/fallback-repair.json",
            "model_usage": {
                "budget_recorded": False,
                "calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost_usd": 0.01,
                "models": ["repair-model"],
            },
        }
        LegacyRefactorAdapter(
            backend=lambda **kwargs: (
                True,
                {
                    "testbench_repair": repair,
                    "csynth_csim_history": [
                        {"testbench_repair": repair}
                    ],
                },
            ),
            usage_supplier=lambda: typed_summary(
                prompt=0,
                completion=0,
            ),
        )(context)
        usage = context.budget.snapshot()
        self.assertEqual(usage.llm_calls, 1)
        self.assertEqual(usage.tokens, 15)
        self.assertAlmostEqual(usage.cost_usd, 0.01)

    def test_repair_artifact_deduplication_remains_by_path(self):
        repair = {
            "artifact_path": "/tmp/same.json",
            "model_usage": {
                "budget_recorded": True,
                "calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "responses": [],
                "models": ["repair-model"],
                "costs_by_currency": {},
                "cost_usd": None,
                "cost_complete": False,
            },
        }
        metadata, fallback_usages, fallback_calls = (
            legacy_module._collect_testbench_repair_usage(
                (
                    True,
                    {
                        "testbench_repair": repair,
                        "csynth_csim_history": [
                            {"testbench_repair": repair}
                        ],
                    },
                )
            )
        )
        self.assertEqual(metadata["artifacts"], ["/tmp/same.json"])
        self.assertEqual(metadata["calls"], 1)
        self.assertEqual(fallback_usages, ())
        self.assertEqual(fallback_calls, 0)

    def test_budget_module_is_not_modified_by_integrated_closure(self):
        budget_path = Path(
            inspect.getsourcefile(BudgetManager)
        )
        source = budget_path.read_text(encoding="utf-8")
        self.assertIn("def record_model_usage(", source)
        self.assertNotIn(
            "integrated_legacy_usage_closure",
            source,
        )

if __name__ == "__main__":
    unittest.main()
