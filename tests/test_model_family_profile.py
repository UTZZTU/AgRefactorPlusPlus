from __future__ import annotations

import inspect
import json
from pathlib import Path
import unittest

from agrefactor.config import TargetProfile, TaskSpec
from agrefactor.evaluation import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
    ValidationState,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    CandidateResponseContract,
    CandidateResponseError,
    ModelCapabilityTag,
    ModelFamilyProfile,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    NEUTRAL_MODEL_FAMILY_PROFILE,
    TokenUsage,
    UnknownModelFamilyProfileError,
)
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    build_candidate_compile_repair_prompt,
)
from agrefactor.repair import (
    BoundedCandidateRepairLoop,
    CandidateRepairLoopRequest,
    CandidateRepairStopReason,
    CandidateValidationResult,
)
from agrefactor.runtime import BudgetManager
from agrefactor.testing import TestbenchRepairRequest
from agrefactor.testing.model_testbench_repairer import (
    ModelTestbenchRepairer,
)


TASK = TaskSpec(
    task_id="model-family-profile",
    kernel_path="candidate.cpp",
    kernel_name="candidate_top",
    target=TargetProfile(
        name="family-target",
        toolchain="vitis_hls",
        toolchain_version="2023.2",
        device="xcu200-fsgd2104-2-e",
        clock_period_ns=4.0,
    ),
)
ORIGINAL = (
    'extern "C" int original_top(int x) '
    "{ return x + 1; }\n"
)
CURRENT = (
    'extern "C" int candidate_top(int x) '
    "{ return x; }\n"
)
REPAIRED = (
    'extern "C" int candidate_top(int x) '
    "{ return x + 1; }\n"
)
CURRENT_TB = (
    'extern "C" int candidate_top(int);\n'
    "int main() { return candidate_top(1) == 2 ? 0 : 1; }\n"
)
REPAIRED_TB = CURRENT_TB + "// declaration context repaired\n"


def feedback() -> FeedbackReport:
    return FeedbackReport(
        report_id="family-feedback",
        source="deterministic-test",
        items=(
            FeedbackItem(
                feedback_id="family-feedback.item",
                stage=FeedbackStage.COMPILE,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate compile failure",
            ),
        ),
        metadata={"evidence_view": "agent_safe"},
    )


def route(report: FeedbackReport) -> FeedbackRouteDecision:
    return FeedbackRouteDecision(
        decision_id="family-route",
        action=FeedbackRouteAction.REPAIR_CANDIDATE,
        reason="candidate-owned compile failure",
        source_report_id=report.report_id,
        blocking_feedback_ids=tuple(
            item.feedback_id
            for item in report.items
            if item.blocking
        ),
        selected_feedback_ids=tuple(
            item.feedback_id
            for item in report.items
            if item.blocking
        ),
        metadata={"evidence_view": "agent_safe"},
    )


def profile() -> ModelFamilyProfile:
    return ModelFamilyProfile(
        name="reasoning-code-strict",
        capabilities=frozenset(
            {
                ModelCapabilityTag.REASONING_MODEL,
                ModelCapabilityTag.CODE_SPECIALIZED,
                ModelCapabilityTag.STRICT_INSTRUCTION,
                ModelCapabilityTag.THINKING_TAG_POSSIBLE,
                ModelCapabilityTag.STRICT_COMPLETION,
            }
        ),
        safe_default_parameters={
            "temperature": 0.1,
            "max_tokens": 2048,
        },
    )


class FakeProvider(ModelProvider):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        value = self.responses.pop(0)
        return ModelResponse(
            text=value,
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=0.0,
            ),
            finish_reason="stop",
        )


def registry_with_profile(
    provider: FakeProvider,
    *,
    defaults=None,
) -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_family_profile(profile())
    registry.register_model(
        ModelSpec(
            name="fixed-repair-model",
            provider="fake",
            model="fake-code-model",
            family="reasoning-code-strict",
            default_parameters=(
                defaults
                or {
                    "temperature": 0.2,
                    "top_p": 0.9,
                }
            ),
        )
    )
    return registry


def candidate_prompt(
    *,
    family_profile=None,
    family_instruction=None,
):
    return build_candidate_compile_repair_prompt(
        CandidateRepairPromptInputs(
            task=TASK,
            feedback=feedback(),
            candidate_code=CURRENT,
            original_code=ORIGINAL,
            family_profile=family_profile,
            family_instruction=family_instruction,
        )
    )


def preflight() -> TestbenchPreflightResult:
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.FAILED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=TestbenchFailureKind.UNDECLARED_TYPE,
        failure_owner=TestbenchFailureOwner.TESTBENCH,
        return_code=1,
        command=("g++", "testbench.cpp"),
        stdout="",
        stderr="signature mismatch",
        artifacts=(),
    )


class PassingValidator:
    def validate(self, request):
        return CandidateValidationResult(
            passed=True,
            completed_stages=(
                ValidationState.PREFLIGHT,
            ),
            summary="validation passed",
        )


class ModelFamilyProfileTests(unittest.TestCase):
    def test_capability_values_are_stable(self):
        self.assertEqual(
            [tag.value for tag in ModelCapabilityTag],
            [
                "reasoning_model",
                "code_specialized",
                "strict_instruction",
                "thinking_tag_possible",
                "strict_completion",
            ],
        )

    def test_neutral_profile_has_no_instruction(self):
        self.assertIsNone(
            NEUTRAL_MODEL_FAMILY_PROFILE.render_instruction()
        )
        self.assertEqual(
            NEUTRAL_MODEL_FAMILY_PROFILE.capability_tags,
            (),
        )

    def test_instruction_order_is_enum_order(self):
        rendered = profile().render_instruction()
        self.assertLess(
            rendered.index("Reason internally"),
            rendered.index("Prefer precise"),
        )
        self.assertLess(
            rendered.index("Prefer precise"),
            rendered.index("Follow the supplied"),
        )

    def test_string_capabilities_are_normalized(self):
        value = ModelFamilyProfile(
            name="normalized",
            capabilities=frozenset(
                {"strict_completion", "reasoning_model"}
            ),
        )
        self.assertEqual(
            value.capability_tags,
            ("reasoning_model", "strict_completion"),
        )

    def test_capabilities_reject_one_string(self):
        with self.assertRaises(TypeError):
            ModelFamilyProfile(
                name="bad",
                capabilities="reasoning_model",
            )

    def test_unknown_capability_is_rejected(self):
        with self.assertRaises(ValueError):
            ModelFamilyProfile(
                name="bad",
                capabilities=frozenset({"model_routing"}),
            )

    def test_safe_defaults_are_deep_copied(self):
        defaults = {"nested": {"value": 1}}
        value = ModelFamilyProfile(
            name="copy",
            safe_default_parameters=defaults,
        )
        defaults["nested"]["value"] = 9
        self.assertEqual(
            value.safe_default_parameters["nested"]["value"],
            1,
        )

    def test_top_level_secret_key_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "credential-like",
        ):
            ModelFamilyProfile(
                name="bad-secret",
                safe_default_parameters={
                    "api_key": "must-not-be-here"
                },
            )

    def test_nested_secret_key_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "credential-like",
        ):
            ModelFamilyProfile(
                name="bad-nested-secret",
                safe_default_parameters={
                    "transport": {
                        "access_token": "must-not-be-here"
                    }
                },
            )

    def test_non_json_defaults_are_rejected(self):
        with self.assertRaises(ValueError):
            ModelFamilyProfile(
                name="bad-json",
                safe_default_parameters={
                    "value": object()
                },
            )

    def test_parameter_precedence_is_explicit(self):
        effective = profile().merge_parameters(
            {
                "temperature": 0.2,
                "top_p": 0.9,
            },
            {
                "temperature": 0,
            },
        )
        self.assertEqual(effective["temperature"], 0)
        self.assertEqual(effective["max_tokens"], 2048)
        self.assertEqual(effective["top_p"], 0.9)

    def test_parameter_merge_does_not_mutate_inputs(self):
        model = {"temperature": 0.2}
        call = {"top_p": 0.8}
        profile().merge_parameters(model, call)
        self.assertEqual(model, {"temperature": 0.2})
        self.assertEqual(call, {"top_p": 0.8})

    def test_manifest_omits_parameter_values(self):
        manifest = profile().to_manifest()
        self.assertNotIn(
            "safe_default_parameters",
            manifest,
        )
        self.assertEqual(
            manifest["name"],
            "reasoning-code-strict",
        )

    def test_profile_dict_is_json_serializable(self):
        encoded = json.dumps(
            profile().to_dict(),
            sort_keys=True,
        )
        self.assertIn("strict_completion", encoded)


class ModelFamilyRegistryTests(unittest.TestCase):
    def test_model_without_family_gets_neutral_profile(self):
        provider = FakeProvider([])
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelSpec(
                name="neutral-model",
                provider="fake",
                model="neutral",
            )
        )
        self.assertIs(
            registry.resolve_family_profile("neutral-model"),
            NEUTRAL_MODEL_FAMILY_PROFILE,
        )

    def test_legacy_unregistered_family_gets_typed_neutral_alias(self):
        provider = FakeProvider([])
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelSpec(
                name="legacy-model",
                provider="fake",
                model="legacy",
                family="legacy-family",
            )
        )
        resolved = registry.resolve_family_profile(
            "legacy-model"
        )
        self.assertEqual(resolved.name, "legacy-family")
        self.assertEqual(resolved.capability_tags, ())

    def test_registered_profile_is_resolved(self):
        provider = FakeProvider([])
        registry = registry_with_profile(provider)
        self.assertEqual(
            registry.resolve_family_profile(
                "fixed-repair-model"
            ),
            profile(),
        )

    def test_duplicate_profile_is_rejected(self):
        registry = ModelRegistry()
        registry.register_family_profile(profile())
        with self.assertRaises(ValueError):
            registry.register_family_profile(profile())

    def test_profile_can_be_replaced_explicitly(self):
        registry = ModelRegistry()
        registry.register_family_profile(profile())
        replacement = ModelFamilyProfile(
            name="reasoning-code-strict",
            capabilities=frozenset(
                {ModelCapabilityTag.STRICT_COMPLETION}
            ),
        )
        registry.register_family_profile(
            replacement,
            replace=True,
        )
        self.assertEqual(
            registry.get_family_profile(
                "reasoning-code-strict"
            ),
            replacement,
        )

    def test_unknown_explicit_profile_lookup_fails(self):
        with self.assertRaises(
            UnknownModelFamilyProfileError
        ):
            ModelRegistry().get_family_profile("missing")

    def test_resolve_with_profile_preserves_fixed_model(self):
        provider = FakeProvider([])
        registry = registry_with_profile(provider)
        spec, resolved_provider, resolved_profile = (
            registry.resolve_with_profile(
                "fixed-repair-model"
            )
        )
        self.assertEqual(spec.name, "fixed-repair-model")
        self.assertIs(resolved_provider, provider)
        self.assertEqual(
            resolved_profile.name,
            "reasoning-code-strict",
        )

    def test_profile_names_are_sorted(self):
        registry = ModelRegistry()
        registry.register_family_profile(
            ModelFamilyProfile(name="z-profile")
        )
        registry.register_family_profile(
            ModelFamilyProfile(name="a-profile")
        )
        self.assertEqual(
            registry.family_profile_names(),
            ("a-profile", "default", "z-profile"),
        )


class CandidateFamilyIntegrationTests(unittest.TestCase):
    def test_candidate_adapter_merges_profile_defaults(self):
        provider = FakeProvider(
            [f"```cpp\n{REPAIRED}\n```"]
        )
        adapter = CandidateModelAdapter(
            registry=registry_with_profile(provider),
            model_name="fixed-repair-model",
            parameters={"temperature": 0},
        )
        result = adapter.generate(
            CandidateModelRequest(
                prompt=candidate_prompt(
                    family_profile=adapter.family_profile
                ),
                task=TASK,
                current_candidate=CURRENT,
            )
        )
        request = provider.calls[0][1]
        self.assertEqual(
            request.parameters,
            {
                "max_tokens": 2048,
                "temperature": 0,
                "top_p": 0.9,
            },
        )
        self.assertEqual(
            result.logical_model_name,
            "fixed-repair-model",
        )

    def test_candidate_adapter_exposes_profile_and_effective_parameters(self):
        provider = FakeProvider([])
        adapter = CandidateModelAdapter(
            registry=registry_with_profile(provider),
            model_name="fixed-repair-model",
        )
        self.assertEqual(
            adapter.family_profile.name,
            "reasoning-code-strict",
        )
        self.assertEqual(
            adapter.effective_parameters["temperature"],
            0.2,
        )
        copied = adapter.effective_parameters
        copied["temperature"] = 99
        self.assertEqual(
            adapter.effective_parameters["temperature"],
            0.2,
        )

    def test_candidate_contract_is_not_relaxed_for_thinking_tags(self):
        contract = CandidateResponseContract.from_candidate(
            TASK,
            CURRENT,
        )
        with self.assertRaises(CandidateResponseError):
            contract.extract_and_validate(
                "<think>private reasoning</think>\n"
                f"```cpp\n{REPAIRED}\n```"
            )

    def test_candidate_prompt_manifest_records_profile_without_parameters(self):
        prompt_value = candidate_prompt(
            family_profile=profile()
        )
        manifest = prompt_value.manifest[
            "model_family_profile"
        ]
        self.assertEqual(
            manifest["capability_tags"],
            list(profile().capability_tags),
        )
        self.assertNotIn(
            "safe_default_parameters",
            json.dumps(manifest),
        )
        self.assertEqual(
            prompt_value.manifest[
                "family_instruction_source"
            ],
            "profile",
        )

    def test_explicit_instruction_is_appended_to_profile_instruction(self):
        prompt_value = candidate_prompt(
            family_profile=profile(),
            family_instruction=(
                "Preserve the exact public ABI."
            ),
        )
        system = prompt_value.messages[0].content
        self.assertIn(
            "Apply these model-capability safeguards",
            system,
        )
        self.assertIn(
            "Preserve the exact public ABI.",
            system,
        )
        self.assertEqual(
            prompt_value.manifest[
                "family_instruction_source"
            ],
            "profile+explicit",
        )

    def test_profile_does_not_change_output_contract(self):
        prompt_value = candidate_prompt(
            family_profile=profile()
        )
        contract = prompt_value.manifest[
            "output_contract"
        ]
        self.assertTrue(contract["complete_replacement"])
        self.assertTrue(contract["fenced_code_block"])
        self.assertFalse(contract["commentary_allowed"])

    def test_candidate_loop_derives_profile_from_fixed_adapter(self):
        provider = FakeProvider(
            [f"```cpp\n{REPAIRED}\n```"]
        )
        adapter = CandidateModelAdapter(
            registry=registry_with_profile(provider),
            model_name="fixed-repair-model",
        )
        loop = BoundedCandidateRepairLoop(
            model_adapter=adapter,
            validator=PassingValidator(),
            budget=BudgetManager(),
        )
        report = feedback()
        result = loop.run(
            CandidateRepairLoopRequest(
                task=TASK,
                initial_candidate=CURRENT,
                original_code=ORIGINAL,
                feedback=report,
                route_decision=route(report),
                failure_state=ValidationState.PREFLIGHT,
                max_attempts=1,
            )
        )
        self.assertIs(
            result.stop_reason,
            CandidateRepairStopReason.VALIDATED,
        )
        self.assertIn(
            "Do not emit <think>",
            provider.calls[0][1].messages[0].content,
        )
        self.assertEqual(
            result.attempts[0].prompt_manifest[
                "model_family_profile"
            ]["name"],
            "reasoning-code-strict",
        )


class TestbenchFamilyIntegrationTests(unittest.TestCase):
    def test_testbench_repairer_uses_profile_defaults_and_instruction(self):
        provider = FakeProvider(
            [f"```cpp\n{REPAIRED_TB}\n```"]
        )
        repairer = ModelTestbenchRepairer(
            registry=registry_with_profile(provider),
            model_name="fixed-repair-model",
            parameters={"temperature": 0},
        )
        repaired = repairer.repair(
            TestbenchRepairRequest(
                attempt=1,
                max_attempts=1,
                current_testbench=CURRENT_TB,
                original_code=ORIGINAL,
                candidate_code=CURRENT,
                preflight=preflight(),
                task=TASK,
            )
        )
        self.assertEqual(
            repaired.strip(),
            REPAIRED_TB.strip(),
        )
        request = provider.calls[0][1]
        self.assertEqual(
            request.parameters["max_tokens"],
            2048,
        )
        self.assertEqual(
            request.parameters["temperature"],
            0,
        )
        self.assertIn(
            "Do not emit <think>",
            request.messages[0].content,
        )
        self.assertEqual(
            repairer.family_profile.name,
            "reasoning-code-strict",
        )

    def test_legacy_family_instruction_is_appended(self):
        provider = FakeProvider(
            [f"```cpp\n{REPAIRED_TB}\n```"]
        )
        repairer = ModelTestbenchRepairer(
            registry=registry_with_profile(provider),
            model_name="fixed-repair-model",
            family_instructions={
                "reasoning-code-strict": (
                    "Preserve all test cases exactly."
                )
            },
        )
        repairer.repair(
            TestbenchRepairRequest(
                attempt=1,
                max_attempts=1,
                current_testbench=CURRENT_TB,
                original_code=ORIGINAL,
                candidate_code=CURRENT,
                preflight=preflight(),
                task=TASK,
            )
        )
        system = provider.calls[0][1].messages[0].content
        self.assertIn(
            "Apply these model-capability safeguards",
            system,
        )
        self.assertIn(
            "Preserve all test cases exactly.",
            system,
        )

    def test_family_core_has_no_vendor_or_routing_branches(self):
        import agrefactor.models.family as family_module
        import agrefactor.models.registry as registry_module

        source = (
            inspect.getsource(family_module)
            + inspect.getsource(registry_module)
        ).lower()
        for forbidden in (
            "deep" + "seek",
            "open" + "ai",
            "anthro" + "pic",
            "auto_model",
            "model_router",
            "switch_model",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("requests", source)


if __name__ == "__main__":
    unittest.main()
