from __future__ import annotations

from hashlib import sha256
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSourceKind,
    TestSourceSpec,
    TestSuiteSpec,
)
from agrefactor.models import (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    KNOWN_MODEL_FAMILY_PROFILES,
    ModelArtifactKind,
    resolve_model_runtime,
)
from agrefactor.testing import (
    build_testbench_repair_prompt,
)
from agrefactor.repair.protocol import RepairModelObservation
from agrefactor.product import source_bootstrap as module
from agrefactor.runtime import BudgetLimits, BudgetManager


class DeterministicPublicTestbenchRepairer:
    def __init__(self):
        self.last_prompt = None
        self.prompts = ()
        self.responses = ()
        self.audit_events = ()

    def repair(self, request):
        self.last_prompt = build_testbench_repair_prompt(
            request,
            family_profile=(
                DEEPSEEK_MODEL_FAMILY_PROFILE
            ),
        )
        self.prompts = (self.last_prompt,)
        self.audit_events = (
            RepairModelObservation(
                prompt_manifest=self.last_prompt.manifest,
                model_call_observed=True,
            ),
        )
        return request.current_testbench.replace(
            "extern node *root;\n",
            "",
        )


class P0PublicTestbenchRepairRoutingTests(unittest.TestCase):
    def test_all_known_output_limits_are_32768(self):
        for profile in KNOWN_MODEL_FAMILY_PROFILES:
            with self.subTest(profile=profile.name):
                policy = profile.output_policy
                self.assertEqual(
                    policy.safety_ceiling,
                    65536,
                )
                for kind in ModelArtifactKind:
                    self.assertEqual(
                        policy.limit_for(kind),
                        32768,
                    )

    def test_runtime_manifest_uses_32768(self):
        selected = resolve_model_runtime(
            "deepseek-v4-flash"
        )
        self.assertEqual(
            selected.effective_config.parameters[
                "max_tokens"
            ],
            32768,
        )
        limits = (
            selected.effective_config.family_profile
            .output_policy.per_artifact_limits
        )
        self.assertEqual(
            set(limits.values()),
            {32768},
        )

    def test_public_repair_loop_fixes_private_global_without_hidden_leak(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original_path = root / "original.cpp"
            hidden_path = root / "hidden.cpp"
            original_code = (
                "struct btnode { int value; btnode *left; "
                "btnode *right; };\n"
                "typedef struct btnode node;\n"
                "node *root = nullptr;\n"
                "void process_top(int *in, int *out) { "
                "out[0] = in[0]; }\n"
            )
            candidate_code = (
                "void process_top_hls(int *in, int *out) { "
                "out[0] = in[0]; }\n"
            )
            public_code = (
                "extern node *root;\n"
                "void process_top(int *in, int *out);\n"
                "void process_top_hls(int *in, int *out);\n"
                "int main() {\n"
                "  int in[1] = {7};\n"
                "  int a[1] = {0};\n"
                "  int b[1] = {0};\n"
                "  process_top(in, a);\n"
                "  process_top_hls(in, b);\n"
                "  return a[0] == b[0] ? 0 : 1;\n"
                "}\n"
            )
            hidden_sentinel = (
                "HIDDEN_SENTINEL_MUST_NOT_ENTER_PROMPT"
            )
            original_path.write_text(
                original_code,
                encoding="utf-8",
            )
            hidden_path.write_text(
                hidden_sentinel,
                encoding="utf-8",
            )
            task = TaskSpec(
                task_id="public-repair-test",
                kernel_path=str(original_path),
                kernel_name="process_top_hls",
                mode=RunMode.REFACTOR,
                test_suites=(
                    TestSuiteSpec(
                        suite_id="public-001",
                        split=EvaluationSplit.PUBLIC,
                    ),
                    TestSuiteSpec(
                        suite_id="hidden-001",
                        split=EvaluationSplit.HIDDEN,
                        testbench_path=str(hidden_path),
                    ),
                ),
            )
            repairer = (
                DeterministicPublicTestbenchRepairer()
            )
            budget = BudgetManager(
                BudgetLimits(
                    max_llm_calls=2,
                    max_tool_calls=5,
                    max_compile_calls=5,
                )
            )
            with patch.object(
                module,
                "build_openai_compatible_testbench_repairer",
                return_value=repairer,
            ):
                result = module._prepare_public_testbench(
                    task=task,
                    testbench_code=public_code,
                    original_code=original_code,
                    candidate_code=candidate_code,
                    effective_model_config=(
                        resolve_model_runtime(
                            "deepseek-v4-flash"
                        ).effective_config
                    ),
                    budget=budget,
                    work_dir=root / "repair",
                    max_repair_attempts=1,
                )

            self.assertTrue(result.succeeded)
            self.assertTrue(result.changed)
            self.assertEqual(
                result.repair_attempts_used,
                1,
            )
            self.assertNotIn(
                "extern node *root;",
                result.testbench_code,
            )
            self.assertRegex(
                result.prompt_sha256 or "",
                r"^[0-9a-f]{64}$",
            )
            self.assertEqual(len(result.prompt_evidence), 1)
            evidence = dict(result.prompt_evidence[0])
            self.assertEqual(
                evidence["metadata"]["source"],
                "testbench_repair",
            )
            self.assertEqual(
                evidence["metadata"]["attempt"],
                1,
            )
            self.assertTrue(
                evidence["provider_call_observed"]
            )
            self.assertEqual(
                evidence["message_sequence_sha256"],
                result.prompt_sha256,
            )
            self.assertEqual(
                result.to_dict()["prompt_evidence_count"],
                1,
            )
            rendered = "\n".join(
                message.content
                for message
                in repairer.last_prompt.messages
            )
            self.assertNotIn(
                hidden_sentinel,
                rendered,
            )
            self.assertEqual(
                budget.snapshot().tool_calls,
                5,
            )
            self.assertEqual(
                budget.snapshot().compile_calls,
                5,
            )

    def test_prompt_identity_aggregates_testbench_repair_call(self):
        phase = object.__new__(module.SourceBootstrapPhase)
        phase._last_formal_phase = None
        phase._public_testbench_prompt_evidence = (
            {
                "schema_version": 1,
                "template_id": "layered:testbench_repair",
                "template_version": 1,
                "system_message_sha256": "a" * 64,
                "invocation_sha256": "b" * 64,
                "message_sequence_sha256": "c" * 64,
                "provider_call_observed": True,
                "metadata": {
                    "source": "testbench_repair",
                    "attempt": 1,
                },
            },
        )

        with patch.object(
            module,
            "get_model_prompt_evidence",
            return_value={
                "schema_version": 1,
                "actual_call_count": 0,
                "calls": [],
                "aggregate_sha256": "d" * 64,
            },
        ):
            payload = phase._collect_prompt_evidence()

        self.assertEqual(payload["actual_call_count"], 1)
        self.assertEqual(len(payload["calls"]), 1)
        call = payload["calls"][0]
        self.assertEqual(call["call_index"], 1)
        self.assertEqual(
            call["metadata"]["source"],
            "testbench_repair",
        )
        self.assertEqual(
            call["message_sequence_sha256"],
            "c" * 64,
        )

    def test_repaired_public_suite_gets_derived_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public_path = root / "public.cpp"
            hidden_path = root / "hidden.cpp"
            public_path.write_text(
                "int main(){return 1;}\n",
                encoding="utf-8",
            )
            hidden_code = "HIDDEN_UNCHANGED\n"
            hidden_path.write_text(
                hidden_code,
                encoding="utf-8",
            )
            old_public = TestSuiteSpec(
                suite_id="public-001",
                suite_version="1",
                split=EvaluationSplit.PUBLIC,
                testbench_path=str(public_path),
                source=TestSourceSpec(
                    source_id="public-generated",
                    source_revision="1",
                    source_kind=TestSourceKind.GENERATED,
                    expected_content_sha256=sha256(
                        public_path.read_bytes()
                    ).hexdigest(),
                    operator_artifact_path=(
                        str(public_path)
                    ),
                    generation_model="deepseek-v4-flash",
                    generation_profile="deepseek",
                    prompt_sha256="a" * 64,
                    trajectory_id="generated",
                    round_index=0,
                ),
            )
            hidden = TestSuiteSpec(
                suite_id="hidden-001",
                suite_version="1",
                split=EvaluationSplit.HIDDEN,
                testbench_path=str(hidden_path),
            )
            preparation = (
                module.PublicTestbenchPreparationResult(
                    status="passed",
                    testbench_code=(
                        "int main(){return 0;}\n"
                    ),
                    reason="passed after repair",
                    repair_attempts_used=1,
                    prompt_sha256="b" * 64,
                    trajectory_id="public-repair",
                    artifact_path="repair.json",
                    repair_artifact_manifest_path=(
                        "manifest.json"
                    ),
                )
            )
            suites, codes, replacement = (
                module._apply_repaired_public_suite(
                    suites=(old_public, hidden),
                    suite_codes={
                        "public-001": (
                            public_path.read_text(
                                encoding="utf-8"
                            )
                        ),
                        "hidden-001": hidden_code,
                    },
                    public_suite=old_public,
                    preparation=preparation,
                    effective_model_config=(
                        resolve_model_runtime(
                            "deepseek-v4-flash"
                        ).effective_config
                    ),
                )
            )

            self.assertIs(suites[1], hidden)
            self.assertEqual(
                codes["hidden-001"],
                hidden_code,
            )
            self.assertEqual(
                replacement.source.source_kind,
                TestSourceKind.DERIVED,
            )
            self.assertEqual(
                replacement.source.prompt_sha256,
                "b" * 64,
            )
            self.assertEqual(
                replacement.source.round_index,
                1,
            )
            self.assertEqual(
                replacement.source
                .expected_content_sha256,
                sha256(public_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                codes["public-001"],
                public_path.read_text(encoding="utf-8"),
            )

    def test_source_bootstrap_wires_public_repair_before_candidate_request(self):
        source = inspect.getsource(
            module.SourceBootstrapPhase.__call__
        )
        self.assertLess(
            source.index("_prepare_public_testbench("),
            source.index(
                "CandidateRepairOrchestrationRequest("
            ),
        )
        self.assertIn(
            "hidden_testbench_exposed_to_model",
            source,
        )
        self.assertIn(
            "public_testbench_repair_attempts",
            source,
        )


if __name__ == "__main__":
    unittest.main()
