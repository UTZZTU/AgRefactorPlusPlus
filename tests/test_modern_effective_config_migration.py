from __future__ import annotations

import inspect
import json
import os
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agrefactor.cli as cli_module
import agrefactor.models.candidate_adapter as candidate_adapter_module
import agrefactor.runtime.candidate_repair_integration as integration_module
from agrefactor.config import TaskSpec
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    EffectiveModelConfig,
    ModelCapabilityTag,
    ModelFamilyProfile,
    ModelPricingSnapshot,
    ModelProvider,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
    ReasoningPolicy,
    TokenUsage,
    UnknownModelFamilyProfileError,
    UnknownProviderError,
)
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    build_candidate_compile_repair_prompt,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CandidateRepairOrchestrationRequest,
    CandidateRepairValidationOrchestrator,
    RunContext,
    TraceRecorder,
)


BASE = 'extern "C" int top(int x) { return x; }\n'
REPAIRED = 'extern "C" int top(int x) { return x + 1; }\n'
TESTBENCH = "int main() { return 0; }\n"
SOURCE_HASH = "b" * 64


class FakeProvider(ModelProvider):
    def __init__(
        self,
        *,
        response_code: str = REPAIRED,
        provider_name: str = "fake",
        cost_usd: float | None = None,
    ) -> None:
        self.response_code = response_code
        self.provider_name = provider_name
        self.cost_usd = cost_usd
        self.calls: list[tuple[ModelSpec, ModelRequest]] = []

    @property
    def name(self) -> str:
        return self.provider_name

    def generate(
        self,
        model: ModelSpec,
        request: ModelRequest,
    ) -> ModelResponse:
        self.calls.append((model, request))
        return ModelResponse(
            text=f"```cpp\n{self.response_code}\n```",
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=self.cost_usd,
            ),
            finish_reason="stop",
            metadata={"request_id": "modern-consumer-test"},
        )


def pricing_snapshot() -> ModelPricingSnapshot:
    return ModelPricingSnapshot(
        provider="fake",
        model_id="fake-model",
        official_source_identity="Official pricing",
        official_source_url=(
            "https://example.invalid/fake-pricing"
        ),
        retrieved_at="2026-07-23T00:00:00+00:00",
        verification_status=(
            PricingVerificationStatus.OFFICIAL_VERIFIED
        ),
        applicability=PricingApplicability(
            region="global",
            billing_mode="real-time",
        ),
        currency="CNY",
        billing_unit_tokens=1_000_000,
        rates=(
            PricingRate(
                token_category="input",
                amount_per_billing_unit=Decimal("1"),
            ),
            PricingRate(
                token_category="output",
                amount_per_billing_unit=Decimal("2"),
            ),
        ),
        effective_date="2026-07-23",
        source_content_sha256=SOURCE_HASH,
    )


def modern_profile() -> ModelFamilyProfile:
    return ModelFamilyProfile(
        name="modern-profile",
        capabilities=frozenset(
            {ModelCapabilityTag.STRICT_INSTRUCTION}
        ),
        safe_default_parameters={
            "temperature": 0.2,
            "nested": {"items": [1, 2]},
        },
        reasoning_policy=ReasoningPolicy.mapped(
            low="provider-low",
            medium="provider-medium",
            high="provider-high",
        ),
    )


def make_registry(
    *,
    provider: FakeProvider | None = None,
) -> tuple[ModelRegistry, FakeProvider]:
    actual_provider = provider or FakeProvider()
    registry = ModelRegistry()
    registry.register_provider(actual_provider)
    registry.register_family_profile(modern_profile())
    registry.register_model(
        ModelSpec(
            name="logical-model",
            provider=actual_provider.name,
            model="fake-model",
            family="modern-profile",
            base_url="https://api.example.invalid",
            api_key_env="FAKE_API_KEY",
            default_parameters={
                "max_tokens": 4096,
                "nested": {"items": [3, 4]},
            },
        )
    )
    return registry, actual_provider


def make_effective(
    registry: ModelRegistry,
    *,
    snapshot: ModelPricingSnapshot | None = None,
    allow_approximate_cost: bool = False,
) -> EffectiveModelConfig:
    return registry.resolve_effective_config(
        "logical-model",
        parameters={
            "temperature": 0,
            "reasoning_effort": "low",
        },
        pricing_snapshot=snapshot,
        allow_approximate_cost=allow_approximate_cost,
    )


def make_task() -> TaskSpec:
    return TaskSpec(
        task_id="p1c2-modern-consumer",
        kernel_path="candidate.cpp",
        kernel_name="top",
    )


def make_model_request(task: TaskSpec) -> CandidateModelRequest:
    feedback = FeedbackReport(
        report_id="p1c2-feedback",
        source="deterministic-test",
        items=(
            FeedbackItem(
                feedback_id="p1c2-feedback.item",
                stage=FeedbackStage.COMPILE,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate compile repair required",
            ),
        ),
        metadata={"evidence_view": "agent_safe"},
    )
    prompt = build_candidate_compile_repair_prompt(
        CandidateRepairPromptInputs(
            task=task,
            feedback=feedback,
            candidate_code=BASE,
            original_code=BASE,
            attempt=1,
            max_attempts=1,
        )
    )
    return CandidateModelRequest(
        prompt=prompt,
        task=task,
        current_candidate=BASE,
    )


def pass_report(report_id: str) -> FeedbackReport:
    return FeedbackReport(
        report_id=report_id,
        source="deterministic-pass",
        items=(),
        metadata={"evidence_view": "agent_safe"},
    )


def fail_report(report_id: str) -> FeedbackReport:
    return FeedbackReport(
        report_id=report_id,
        source="deterministic-fail",
        items=(
            FeedbackItem(
                feedback_id=f"{report_id}.item",
                stage=FeedbackStage.COMPILE,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate compile repair required",
            ),
        ),
        metadata={"evidence_view": "agent_safe"},
    )


class ScenarioFactory:
    def __init__(self, *, fail_initial: bool = False) -> None:
        self.fail_initial = fail_initial
        self.build_calls = 0

    def build(self, request):
        self.build_calls += 1

        def preflight(context):
            if self.fail_initial and request.attempt == 0:
                return fail_report(
                    f"{request.validation_id}.compile"
                )
            return pass_report(
                f"{request.validation_id}.preflight"
            )

        def csynth(context):
            return pass_report(
                f"{request.validation_id}.csynth"
            )

        return {
            ValidationState.PREFLIGHT: preflight,
            ValidationState.CSYNTH: csynth,
        }


def make_context(task: TaskSpec) -> RunContext:
    return RunContext(
        run_id="p1c2-run",
        task=task,
        budget=BudgetManager(BudgetLimits()),
        trace=TraceRecorder(
            "p1c2-run",
            task_id=task.task_id,
        ),
    )


def make_orchestration_request(
    *,
    family_instruction: str | None = None,
) -> CandidateRepairOrchestrationRequest:
    return CandidateRepairOrchestrationRequest(
        initial_candidate=BASE,
        original_code=BASE,
        preflight_testbench_code=TESTBENCH,
        suite_testbench_codes={},
        prompt_public_testbench_code=None,
        max_attempts=1,
        family_instruction=family_instruction,
    )


class CandidateAdapterEffectiveConfigTests(unittest.TestCase):
    def test_effective_config_path_preserves_same_object(self):
        registry, _ = make_registry()
        config = make_effective(registry)
        adapter = CandidateModelAdapter(
            registry=registry,
            effective_config=config,
        )
        self.assertIs(adapter.effective_config, config)

    def test_effective_config_path_uses_transport_spec(self):
        registry, _ = make_registry()
        adapter = CandidateModelAdapter(
            registry=registry,
            effective_config=make_effective(registry),
        )
        spec = adapter.model_spec
        self.assertEqual(spec.name, "logical-model")
        self.assertEqual(spec.provider, "fake")
        self.assertEqual(spec.model, "fake-model")
        self.assertEqual(spec.default_parameters, {})

    def test_effective_config_path_does_not_merge_again(self):
        registry, _ = make_registry()
        config = make_effective(registry)
        with patch.object(
            ModelFamilyProfile,
            "merge_parameters",
            side_effect=AssertionError("second merge"),
        ):
            adapter = CandidateModelAdapter(
                registry=registry,
                effective_config=config,
            )
        self.assertIs(adapter.effective_config, config)

    def test_effective_config_preserves_pricing_policy(self):
        registry, _ = make_registry()
        snapshot = pricing_snapshot()
        config = make_effective(
            registry,
            snapshot=snapshot,
            allow_approximate_cost=True,
        )
        adapter = CandidateModelAdapter(
            registry=registry,
            effective_config=config,
        )
        self.assertIs(adapter.pricing_snapshot, snapshot)
        self.assertTrue(adapter.allow_approximate_cost)

    def test_effective_config_rejects_parallel_model_name(self):
        registry, _ = make_registry()
        with self.assertRaisesRegex(ValueError, "model_name"):
            CandidateModelAdapter(
                registry=registry,
                model_name="logical-model",
                effective_config=make_effective(registry),
            )

    def test_effective_config_rejects_parallel_parameters(self):
        registry, _ = make_registry()
        with self.assertRaisesRegex(ValueError, "parameters"):
            CandidateModelAdapter(
                registry=registry,
                effective_config=make_effective(registry),
                parameters={},
            )

    def test_effective_config_rejects_parallel_pricing_policy(self):
        registry, _ = make_registry()
        with self.assertRaisesRegex(
            ValueError,
            "pricing_snapshot",
        ):
            CandidateModelAdapter(
                registry=registry,
                effective_config=make_effective(registry),
                pricing_snapshot=pricing_snapshot(),
            )
        with self.assertRaisesRegex(
            ValueError,
            "allow_approximate_cost",
        ):
            CandidateModelAdapter(
                registry=registry,
                effective_config=make_effective(registry),
                allow_approximate_cost=True,
            )

    def test_effective_config_rejects_wrong_type(self):
        registry, _ = make_registry()
        with self.assertRaises(TypeError):
            CandidateModelAdapter(
                registry=registry,
                effective_config={},
            )

    def test_adapter_requires_one_model_selection(self):
        registry, _ = make_registry()
        with self.assertRaisesRegex(ValueError, "model_name"):
            CandidateModelAdapter(registry=registry)

    def test_effective_config_requires_registered_provider(self):
        config = EffectiveModelConfig(
            logical_model_name="logical",
            provider_name="missing",
            model_id="provider-model",
            family_profile=modern_profile(),
        )
        with self.assertRaises(UnknownProviderError):
            CandidateModelAdapter(
                registry=ModelRegistry(),
                effective_config=config,
            )

    def test_old_constructor_delegates_to_effective_resolver(self):
        registry, _ = make_registry()
        with patch.object(
            registry,
            "resolve_effective_config",
            wraps=registry.resolve_effective_config,
        ) as resolver:
            adapter = CandidateModelAdapter(
                registry=registry,
                model_name="logical-model",
                parameters={"reasoning_effort": "low"},
            )
        resolver.assert_called_once()
        self.assertIsInstance(
            adapter.effective_config,
            EffectiveModelConfig,
        )

    def test_old_constructor_preserves_precedence(self):
        registry, _ = make_registry()
        adapter = CandidateModelAdapter(
            registry=registry,
            model_name="logical-model",
            parameters={
                "temperature": 0,
                "reasoning_effort": "medium",
            },
        )
        self.assertEqual(
            adapter.effective_parameters["temperature"],
            0,
        )
        self.assertEqual(
            adapter.effective_parameters["max_tokens"],
            4096,
        )
        self.assertEqual(
            adapter.effective_parameters["reasoning_effort"],
            "provider-medium",
        )

    def test_family_instruction_comes_from_effective_config(self):
        registry, _ = make_registry()
        adapter = CandidateModelAdapter(
            registry=registry,
            effective_config=make_effective(registry),
        )
        self.assertEqual(
            adapter.family_instruction,
            adapter.effective_config.family_instruction,
        )

    def test_effective_parameters_are_detached(self):
        registry, _ = make_registry()
        adapter = CandidateModelAdapter(
            registry=registry,
            effective_config=make_effective(registry),
        )
        first = adapter.effective_parameters
        first["nested"]["items"][0] = 99
        self.assertEqual(
            adapter.effective_parameters["nested"]["items"],
            [3, 4],
        )

    def test_generate_uses_resolved_parameters_and_identity(self):
        registry, provider = make_registry()
        config = make_effective(registry)
        result = CandidateModelAdapter(
            registry=registry,
            effective_config=config,
        ).generate(make_model_request(make_task()))
        _, request = provider.calls[0]
        self.assertEqual(request.parameters, config.parameters)
        self.assertEqual(
            result.logical_model_name,
            config.logical_model_name,
        )
        self.assertEqual(
            result.provider_name,
            config.provider_name,
        )

    def test_generate_preserves_snapshot_hash(self):
        registry, _ = make_registry()
        snapshot = pricing_snapshot()
        result = CandidateModelAdapter(
            registry=registry,
            effective_config=make_effective(
                registry,
                snapshot=snapshot,
            ),
        ).generate(make_model_request(make_task()))
        self.assertEqual(
            result.response.metadata[
                "pricing_snapshot_sha256"
            ],
            snapshot.pricing_snapshot_sha256,
        )

    def test_adapter_constructor_has_no_duplicate_resolver(self):
        source = inspect.getsource(
            candidate_adapter_module.CandidateModelAdapter.__init__
        )
        self.assertIn("registry.resolve_effective_config(", source)
        self.assertNotIn("resolve_with_profile(", source)
        self.assertNotIn("merge_parameters(", source)

    def test_adapter_exposes_one_effective_config_authority(self):
        source = inspect.getsource(
            candidate_adapter_module.CandidateModelAdapter.__init__
        )
        self.assertIn(
            "effective_config: EffectiveModelConfig | None",
            source,
        )
        self.assertIn(
            "self._effective_config = resolved_config",
            source,
        )


class CliEffectiveConfigTests(unittest.TestCase):
    def make_args(
        self,
        *,
        reasoning_effort: str | None = "low",
        model_family: str | None = "deepseek",
    ):
        return SimpleNamespace(
            model="deepseek-v4-flash",
            model_family=model_family,
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com",
            reasoning_effort=reasoning_effort,
        )

    def test_cli_builder_returns_effective_config_adapter(self):
        adapter = cli_module._build_cli_candidate_adapter(
            self.make_args()
        )
        self.assertIsInstance(
            adapter.effective_config,
            EffectiveModelConfig,
        )

    def test_cli_reasoning_override_is_resolved(self):
        adapter = cli_module._build_cli_candidate_adapter(
            self.make_args(reasoning_effort="low")
        )
        self.assertEqual(
            adapter.effective_parameters["reasoning_effort"],
            "high",
        )

    def test_cli_omits_unset_reasoning_override(self):
        adapter = cli_module._build_cli_candidate_adapter(
            self.make_args(reasoning_effort=None)
        )
        self.assertNotIn(
            "reasoning_effort",
            adapter.effective_parameters,
        )

    def test_cli_preserves_transport_identity_without_secret(self):
        os.environ["DEEPSEEK_API_KEY"] = "must-not-leak"
        try:
            adapter = cli_module._build_cli_candidate_adapter(
                self.make_args()
            )
            manifest = adapter.effective_config.to_manifest()
            encoded = json.dumps(manifest, sort_keys=True)
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        self.assertEqual(
            manifest["base_url"],
            "https://api.deepseek.com",
        )
        self.assertEqual(
            manifest["api_key_env"],
            "DEEPSEEK_API_KEY",
        )
        self.assertNotIn("must-not-leak", encoded)

    def test_cli_unknown_family_fails_during_resolution(self):
        with self.assertRaises(
            UnknownModelFamilyProfileError
        ):
            cli_module._build_cli_candidate_adapter(
                self.make_args(model_family="not-registered")
            )

    def test_cli_builder_resolves_once_and_passes_config(self):
        source = inspect.getsource(
            cli_module._build_cli_candidate_adapter
        )
        self.assertEqual(
            source.count("resolve_effective_config("),
            1,
        )
        return_block = source.split(
            "return CandidateModelAdapter(",
            1,
        )[1]
        self.assertIn(
            "effective_config=effective_config",
            return_block,
        )
        self.assertNotIn("model_name=", return_block)


class OrchestrationEffectiveConfigTests(unittest.TestCase):
    def make_adapter(self):
        registry, provider = make_registry()
        adapter = CandidateModelAdapter(
            registry=registry,
            effective_config=make_effective(registry),
        )
        return adapter, provider

    def test_selector_uses_resolved_and_rejects_conflict(self):
        selected, source = (
            integration_module._select_family_instruction(
                "resolved",
                None,
            )
        )
        self.assertEqual(selected, "resolved")
        self.assertEqual(source, "effective_model_config")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            integration_module._select_family_instruction(
                "resolved",
                "different",
            )

    def test_selector_preserves_request_only_compatibility(self):
        selected, source = (
            integration_module._select_family_instruction(
                None,
                "compatibility",
            )
        )
        self.assertEqual(selected, "compatibility")
        self.assertEqual(source, "request_compatibility")

    def test_initial_acceptance_records_safe_manifest(self):
        task = make_task()
        adapter, provider = self.make_adapter()
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(),
        ).run(
            make_context(task),
            make_orchestration_request(),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        self.assertEqual(provider.calls, [])
        self.assertEqual(
            result.metadata["effective_model_config"],
            adapter.effective_config.to_manifest(),
        )
        self.assertEqual(
            result.metadata["family_instruction_source"],
            "effective_model_config",
        )
        json.dumps(result.to_dict(), sort_keys=True)

    def test_repair_prompt_uses_resolved_instruction(self):
        task = make_task()
        adapter, provider = self.make_adapter()
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(
                fail_initial=True
            ),
        ).run(
            make_context(task),
            make_orchestration_request(),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        self.assertEqual(len(provider.calls), 1)
        _, model_request = provider.calls[0]
        combined = "\n".join(
            message.content
            for message in model_request.messages
        )
        self.assertIn(
            "Apply these model-capability safeguards:",
            combined,
        )
        self.assertIn(
            "Follow the supplied modification scope",
            combined,
        )

    def test_conflict_fails_before_validation_or_provider(self):
        task = make_task()
        adapter, provider = self.make_adapter()
        factory = ScenarioFactory()
        with self.assertRaisesRegex(ValueError, "conflicts"):
            CandidateRepairValidationOrchestrator(
                model_adapter=adapter,
                handler_factory=factory,
            ).run(
                make_context(task),
                make_orchestration_request(
                    family_instruction="different"
                ),
                validation_id="validation",
            )
        self.assertEqual(factory.build_calls, 0)
        self.assertEqual(provider.calls, [])

    def test_runtime_has_no_model_resolution_authority(self):
        source = inspect.getsource(integration_module)
        for forbidden in (
            "ModelRegistry",
            "ModelSpec",
            "resolve_effective_config(",
            "resolve_with_profile(",
            "merge_parameters(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
