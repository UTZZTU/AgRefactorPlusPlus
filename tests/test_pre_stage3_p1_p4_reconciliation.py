from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import (
    EvaluationSplit,
    OverallTestSourceMode,
    TaskSpec,
    TestFeedbackVisibility,
    TestQualificationStatus,
    TestSourceKind,
    TestSourcePlan,
    TestSourceSelection,
    TestSourceSpec,
    TestSuiteSpec,
    resolve_test_source,
)
from agrefactor.evaluation import CsimSuiteEvaluator
from agrefactor.models import (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    KNOWN_MODEL_FAMILY_PROFILES,
    ModelArtifactKind,
    ModelFamilyProfile,
    ModelOutputPolicy,
    ModelProfileVerificationStatus,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.prompts import (
    HiddenTestSourceIsolationError,
    PromptArtifact,
    assert_hidden_test_sources_absent,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeProvider(ModelProvider):
    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        return ModelResponse(
            text="ok",
            model=model.model,
            usage=TokenUsage(),
        )


class FrozenProfileContractTests(unittest.TestCase):
    def test_frozen_verification_status_exists(self):
        self.assertEqual(
            ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED.value,
            "deterministically_tested",
        )

    def test_known_profiles_use_frozen_verification_states(self):
        self.assertTrue(KNOWN_MODEL_FAMILY_PROFILES)
        self.assertTrue(
            all(
                p.verification_status
                is ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED
                for p in KNOWN_MODEL_FAMILY_PROFILES
            )
        )
        self.assertIn(
            "deepseek-v4-flash",
            DEEPSEEK_MODEL_FAMILY_PROFILE.verification_note,
        )

    def test_each_known_profile_expresses_full_frozen_schema(self):
        for profile in KNOWN_MODEL_FAMILY_PROFILES:
            with self.subTest(profile=profile.name):
                self.assertTrue(profile.supported_parameters)
                self.assertEqual(
                    set(profile.artifact_default_parameters),
                    {item.value for item in ModelArtifactKind},
                )
                self.assertIsInstance(profile.output_policy, ModelOutputPolicy)
                self.assertGreater(profile.request_timeout_s, 0)
                self.assertTrue(profile.prompt_profile)
                manifest = profile.to_manifest()
                for key in (
                    "supported_parameters",
                    "artifact_default_kinds",
                    "output_policy",
                    "request_timeout_s",
                    "prompt_profile",
                    "verification_status",
                ):
                    self.assertIn(key, manifest)

    def test_artifact_output_default_is_typed(self):
        profile = GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
        effective = profile.merge_parameters(
            artifact_kind=ModelArtifactKind.CANDIDATE_REPAIR,
        )
        self.assertEqual(effective["max_tokens"], 16384)

    def test_explicit_output_override_beats_artifact_default(self):
        profile = GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
        effective = profile.merge_parameters(
            {"max_tokens": 2048},
            {"max_tokens": 1024},
            artifact_kind=ModelArtifactKind.CANDIDATE_REPAIR,
        )
        self.assertEqual(effective["max_tokens"], 1024)

    def test_supported_parameters_are_declarative_not_exhaustive(self):
        profile = ModelFamilyProfile(
            name="extensible",
            supported_parameters=frozenset({"temperature"}),
        )
        effective = profile.merge_parameters(
            {
                "temperature": 0.1,
                "nested": {"provider_extension": True},
            }
        )
        self.assertEqual(effective["temperature"], 0.1)
        self.assertEqual(
            effective["nested"],
            {"provider_extension": True},
        )
        self.assertEqual(
            profile.to_manifest()["supported_parameters"],
            ["temperature"],
        )

    def test_rejected_parameters_remain_hard_blocks(self):
        profile = ModelFamilyProfile(
            name="reject-explicit",
            supported_parameters=frozenset({"temperature"}),
            rejected_parameters=frozenset({"unsafe_vendor_flag"}),
        )
        with self.assertRaisesRegex(
            ValueError,
            "unsafe_vendor_flag",
        ):
            profile.merge_parameters(
                {"unsafe_vendor_flag": True}
            )

    def test_output_safety_ceiling_is_enforced(self):
        profile = ModelFamilyProfile(
            name="ceiling",
            supported_parameters=frozenset({"max_tokens"}),
            output_policy=ModelOutputPolicy(safety_ceiling=100),
        )
        with self.assertRaisesRegex(ValueError, "safety ceiling"):
            profile.merge_parameters({"max_tokens": 101})

    def test_registry_resolves_artifact_specific_policy(self):
        registry = ModelRegistry(include_known_family_profiles=False)
        registry.register_provider(FakeProvider())
        registry.register_family_profile(
            ModelFamilyProfile(
                name="artifact",
                supported_parameters=frozenset({"max_tokens"}),
                artifact_default_parameters={
                    ModelArtifactKind.TESTBENCH: {},
                },
                output_policy=ModelOutputPolicy(
                    per_artifact_limits={
                        ModelArtifactKind.TESTBENCH: 2048,
                    }
                ),
                verification_status=(
                    ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED
                ),
            )
        )
        registry.register_model(
            ModelSpec(
                name="fixed",
                provider="fake",
                model="fixed-model",
                family="artifact",
            )
        )
        config = registry.resolve_effective_config(
            "fixed",
            artifact_kind=ModelArtifactKind.TESTBENCH,
        )
        self.assertEqual(config.parameters["max_tokens"], 2048)


class FrozenSourceContractTests(unittest.TestCase):
    def generated_source(self, content: str) -> TestSourceSpec:
        return TestSourceSpec(
            source_id="generated",
            source_revision="v1",
            source_kind=TestSourceKind.GENERATED,
            expected_content_sha256=sha(content),
            operator_artifact_path="/operator/tests/generated.cpp",
            generation_model="logical-model",
            generation_profile="profile-a",
            prompt_sha256="a" * 64,
            trajectory_id="trajectory-7",
            round_index=2,
        )

    def test_four_frozen_source_kinds_exist(self):
        self.assertEqual(
            {
                TestSourceKind.PROVIDED.value,
                TestSourceKind.GENERATED.value,
                TestSourceKind.DERIVED.value,
                TestSourceKind.CACHED.value,
            },
            {"provided", "generated", "derived", "cached"},
        )

    def test_generated_source_requires_full_generation_provenance(self):
        with self.assertRaisesRegex(ValueError, "requires provenance"):
            TestSourceSpec(
                source_id="generated",
                source_kind=TestSourceKind.GENERATED,
            )

    def test_materialized_generated_source_resolves_locally(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "generated.cpp"
            path.write_text("TB", encoding="utf-8")
            provenance = resolve_test_source(
                self.generated_source("TB"),
                path,
                execution_content="TB",
                suite_id="hidden-generated",
                suite_version="v3",
                split="hidden",
                coverage={"branches": 4},
                qualification_status=TestQualificationStatus.QUALIFIED,
                feedback_visibility=TestFeedbackVisibility.OPERATOR_ONLY,
            )
        payload = provenance.to_dict()
        self.assertEqual(payload["suite_id"], "hidden-generated")
        self.assertEqual(payload["generation_model"], "logical-model")
        self.assertEqual(payload["generation_profile"], "profile-a")
        self.assertEqual(payload["prompt_sha256"], "a" * 64)
        self.assertEqual(payload["trajectory_id"], "trajectory-7")
        self.assertEqual(payload["round_index"], 2)
        self.assertEqual(payload["coverage"], {"branches": 4})
        self.assertEqual(payload["qualification_status"], "qualified")
        self.assertEqual(payload["feedback_visibility"], "operator_only")

    def test_hidden_agent_provenance_redacts_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "hidden.cpp"
            path.write_text("SECRET", encoding="utf-8")
            provenance = resolve_test_source(
                TestSourceSpec(
                    source_id="hidden",
                    source_kind=TestSourceKind.PROVIDED,
                    expected_content_sha256=sha("SECRET"),
                ),
                path,
                execution_content="SECRET",
                suite_id="hidden-suite",
                split="hidden",
                feedback_visibility=TestFeedbackVisibility.OPERATOR_ONLY,
            )
        encoded = json.dumps(provenance.to_hidden_agent_dict(), sort_keys=True)
        for forbidden in (
            provenance.content_sha256,
            provenance.resolved_path,
            provenance.operator_artifact_path,
            "generation_model",
            "prompt_sha256",
            "coverage",
            "qualification_status",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_csim_derives_qualification_and_coverage(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "generated.cpp"
            path.write_text("TB", encoding="utf-8")
            suite = TestSuiteSpec(
                suite_id="public-generated",
                suite_version="v1",
                split=EvaluationSplit.PUBLIC,
                case_count=2,
                testbench_path=str(path),
                source=self.generated_source("TB"),
            )
            result = CsimSuiteEvaluator(
                executor=lambda *args, **kwargs: ("succeeded", "")
            ).evaluate(
                work_dir=root,
                context_variables={"testbench": "TB"},
                suite=suite,
            )
        provenance = result.evidence.source_provenance
        self.assertEqual(
            provenance.qualification_status,
            TestQualificationStatus.QUALIFIED,
        )
        self.assertEqual(provenance.coverage["declared_case_count"], 2)
        self.assertEqual(provenance.coverage["passed_cases"], 2)


class FrozenSelectionContractTests(unittest.TestCase):
    def test_provided_mode_and_multiple_suites_are_derived(self):
        plan = TestSourcePlan(
            public=TestSourceSelection.provided(
                EvaluationSplit.PUBLIC,
                ("p1.cpp", "p2.cpp"),
            ),
            hidden=TestSourceSelection.provided(
                EvaluationSplit.HIDDEN,
                ("h1.cpp",),
            ),
        )
        self.assertIs(plan.overall_mode, OverallTestSourceMode.PROVIDED)
        self.assertEqual(plan.to_operator_dict()["public"]["suite_count"], 2)

    def test_auto_mode_is_derived(self):
        plan = TestSourcePlan(
            public=TestSourceSelection.auto(EvaluationSplit.PUBLIC),
            hidden=TestSourceSelection.auto(EvaluationSplit.HIDDEN),
        )
        self.assertIs(plan.overall_mode, OverallTestSourceMode.AUTO)

    def test_both_hybrid_orders_are_derived(self):
        pairs = (
            (
                TestSourceSelection.provided(
                    EvaluationSplit.PUBLIC, ("public.cpp",)
                ),
                TestSourceSelection.auto(EvaluationSplit.HIDDEN),
            ),
            (
                TestSourceSelection.auto(EvaluationSplit.PUBLIC),
                TestSourceSelection.provided(
                    EvaluationSplit.HIDDEN, ("hidden.cpp",)
                ),
            ),
        )
        for public, hidden in pairs:
            with self.subTest(public=public.mode, hidden=hidden.mode):
                self.assertIs(
                    TestSourcePlan(public=public, hidden=hidden).overall_mode,
                    OverallTestSourceMode.HYBRID,
                )

    def test_hidden_agent_plan_never_exposes_paths(self):
        plan = TestSourcePlan(
            public=TestSourceSelection.auto(EvaluationSplit.PUBLIC),
            hidden=TestSourceSelection.provided(
                EvaluationSplit.HIDDEN,
                ("/private/one.cpp", "/private/two.cpp"),
            ),
        )
        encoded = json.dumps(plan.to_agent_dict(), sort_keys=True)
        self.assertNotIn("/private/one.cpp", encoded)
        self.assertNotIn("/private/two.cpp", encoded)
        self.assertIn('"suite_count": 2', encoded)


class HiddenPromptIsolationTests(unittest.TestCase):
    def task(self, hidden_text: str) -> TaskSpec:
        hidden_path = "/private/hidden.cpp"
        return TaskSpec(
            task_id="isolation",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=(
                TestSuiteSpec(
                    suite_id="hidden",
                    split=EvaluationSplit.HIDDEN,
                    testbench_path=hidden_path,
                    source=TestSourceSpec(
                        source_id="hidden-source",
                        source_kind=TestSourceKind.PROVIDED,
                        expected_content_sha256=sha(hidden_text),
                        operator_artifact_path=hidden_path,
                    ),
                ),
            ),
        )

    def test_safe_candidate_context_passes(self):
        assert_hidden_test_sources_absent(
            task=self.task("SECRET"),
            messages=("candidate task only",),
            artifacts=(
                PromptArtifact(
                    name="candidate",
                    content="int top(){return 0;}",
                ),
            ),
        )

    def test_hidden_path_is_rejected(self):
        with self.assertRaises(HiddenTestSourceIsolationError):
            assert_hidden_test_sources_absent(
                task=self.task("SECRET"),
                messages=("read /private/hidden.cpp",),
            )

    def test_hidden_digest_is_rejected(self):
        hidden = "SECRET"
        with self.assertRaises(HiddenTestSourceIsolationError):
            assert_hidden_test_sources_absent(
                task=self.task(hidden),
                messages=(sha(hidden),),
            )

    def test_exact_hidden_content_artifact_is_rejected(self):
        hidden = "SECRET"
        with self.assertRaises(HiddenTestSourceIsolationError):
            assert_hidden_test_sources_absent(
                task=self.task(hidden),
                messages=("safe",),
                artifacts=(PromptArtifact(name="leak", content=hidden),),
            )


if __name__ == "__main__":
    unittest.main()
