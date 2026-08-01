import hashlib
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.optimization import (
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
)
from agrefactor.prompts.optimization import (
    STRUCTURAL_HYPOTHESIS_PURPOSE,
    STRUCTURAL_REWRITE_PURPOSE,
    StructuralHypothesisPromptRequest,
    StructuralOptimizationPromptBuilder,
    StructuralRewritePromptRequest,
)


SOURCE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    for (int i = 0; i < n; ++i) a[i] += 1;\n}\n"""


def task(*, mode=RunMode.OPTIMIZE, suites=()):
    return TaskSpec(
        task_id="s34-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        mode=mode,
        test_suites=tuple(suites),
    )


def hypothesis(*, parent="baseline", claim="Use a local tile to reduce repeated memory traffic"):
    return HypothesisRecord(
        hypothesis_id="hyp-structural-r1-1",
        level=OptimizationLevel.STRUCTURAL,
        parent_candidate_id=parent,
        claim=claim,
        supporting_evidence_ids=(),
        expected_benefit={"metric": "latency", "direction": "decrease"},
        risk=HypothesisRisk.LOW,
        modification_scope=("loop organization", "local buffering"),
        verification_plan=("preflight", "public", "csynth", "hidden"),
        model_identity={"provider": "fixture", "network": False},
        prompt_identity_sha256="a" * 64,
    )


class StructuralHypothesisPromptTests(unittest.TestCase):
    def setUp(self):
        self.builder = StructuralOptimizationPromptBuilder()

    def request(self, **updates):
        values = {
            "task": task(),
            "parent_candidate_id": "baseline",
            "parent_source": SOURCE,
            "round_number": 1,
            "max_hypotheses": 3,
            "supporting_evidence_ids": (),
            "safe_context": {"policy": "safe-v1", "objective": "latency"},
        }
        values.update(updates)
        return StructuralHypothesisPromptRequest(**values)

    def test_hypothesis_prompt_is_deterministic(self):
        first = self.builder.build_hypothesis(self.request())
        second = self.builder.build_hypothesis(self.request())
        self.assertEqual(first, second)

    def test_hypothesis_manifest_has_frozen_purpose_and_identity(self):
        prompt = self.builder.build_hypothesis(self.request())
        self.assertEqual(prompt.manifest["purpose"], STRUCTURAL_HYPOTHESIS_PURPOSE)
        self.assertRegex(prompt.manifest["prompt_identity_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(prompt.manifest["level"], "structural")

    def test_hypothesis_manifest_contains_source_hash_not_source(self):
        prompt = self.builder.build_hypothesis(self.request())
        self.assertEqual(
            prompt.manifest["parent_source_sha256"],
            hashlib.sha256(SOURCE.encode()).hexdigest(),
        )
        self.assertNotIn(SOURCE, str(prompt.manifest))

    def test_hypothesis_user_prompt_contains_read_only_source(self):
        prompt = self.builder.build_hypothesis(self.request())
        self.assertIn(SOURCE, prompt.messages[1].content)
        self.assertIn("Parent candidate source (read-only)", prompt.messages[1].content)

    def test_hypothesis_prompt_states_strict_json_contract(self):
        prompt = self.builder.build_hypothesis(self.request())
        system = prompt.messages[0].content
        self.assertIn("Return strict JSON only", system)
        self.assertIn('"schema_version":1', system)
        self.assertIn("at most 3 hypotheses", system)

    def test_hypothesis_prompt_rejects_refactor_mode(self):
        with self.assertRaises(ValueError):
            self.request(task=task(mode=RunMode.REFACTOR))

    def test_hypothesis_prompt_allows_full_mode(self):
        prompt = self.builder.build_hypothesis(
            self.request(task=task(mode=RunMode.FULL))
        )
        self.assertEqual(prompt.manifest["mode"], "full")

    def test_hypothesis_prompt_rejects_more_than_three(self):
        with self.assertRaises(ValueError):
            self.request(max_hypotheses=4)

    def test_hypothesis_prompt_rejects_empty_source(self):
        with self.assertRaises(ValueError):
            self.request(parent_source="   ")

    def test_hypothesis_prompt_rejects_agent_unsafe_context(self):
        with self.assertRaises(ValueError):
            self.request(safe_context={"hidden_report": "x"})

    def test_family_instruction_is_explicit_and_identity_affecting(self):
        plain = self.builder.build_hypothesis(self.request())
        instructed = self.builder.build_hypothesis(
            self.request(family_instruction="Prefer direct, concise JSON.")
        )
        self.assertIn("Prefer direct, concise JSON.", instructed.messages[0].content)
        self.assertNotEqual(
            plain.manifest["prompt_identity_sha256"],
            instructed.manifest["prompt_identity_sha256"],
        )

    def test_source_change_changes_prompt_identity(self):
        first = self.builder.build_hypothesis(self.request())
        second = self.builder.build_hypothesis(
            self.request(parent_source=SOURCE + "\n// changed\n")
        )
        self.assertNotEqual(
            first.manifest["prompt_identity_sha256"],
            second.manifest["prompt_identity_sha256"],
        )

    def test_hidden_testbench_path_is_not_in_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            hidden = Path(directory) / "operator_hidden.cpp"
            hidden.write_text("HIDDEN_SECRET_MARKER", encoding="utf-8")
            suite = TestSuiteSpec(
                suite_id="hidden-suite",
                split=EvaluationSplit.HIDDEN,
                testbench_path=str(hidden),
            )
            prompt = self.builder.build_hypothesis(
                self.request(task=task(suites=(suite,)))
            )
            rendered = "\n".join(item.content for item in prompt.messages)
            self.assertNotIn(str(hidden), rendered)
            self.assertNotIn("HIDDEN_SECRET_MARKER", rendered)


class StructuralRewritePromptTests(unittest.TestCase):
    def setUp(self):
        self.builder = StructuralOptimizationPromptBuilder()

    def request(self, **updates):
        values = {
            "task": task(),
            "candidate_id": "cand-1",
            "parent_candidate_id": "baseline",
            "parent_source": SOURCE,
            "hypothesis": hypothesis(),
            "safe_context": {"policy": "safe-v1", "objective": "latency"},
        }
        values.update(updates)
        return StructuralRewritePromptRequest(**values)

    def test_rewrite_prompt_is_deterministic(self):
        self.assertEqual(
            self.builder.build_rewrite(self.request()),
            self.builder.build_rewrite(self.request()),
        )

    def test_rewrite_manifest_has_complete_source_contract(self):
        prompt = self.builder.build_rewrite(self.request())
        self.assertEqual(prompt.manifest["purpose"], STRUCTURAL_REWRITE_PURPOSE)
        contract = prompt.manifest["output_contract"]
        self.assertTrue(contract["complete_replacement"])
        self.assertTrue(contract["fenced_code_block_preferred"])
        self.assertTrue(contract["raw_complete_source_allowed"])
        self.assertEqual(contract["explicit_abstention_token"], "AGREFACTOR_ABSTAIN")
        self.assertFalse(contract["commentary_allowed"])
        self.assertTrue(contract["top_function_interface_must_remain_unchanged"])

    def test_rewrite_prompt_contains_selected_hypothesis_and_source(self):
        prompt = self.builder.build_rewrite(self.request())
        user = prompt.messages[1].content
        self.assertIn(hypothesis().claim, user)
        self.assertIn(SOURCE, user)
        self.assertIn("cand-1", user)

    def test_rewrite_prompt_rejects_parent_mismatch(self):
        with self.assertRaises(ValueError):
            self.request(parent_candidate_id="cand-9")

    def test_rewrite_prompt_rejects_nonstructural_hypothesis(self):
        invalid = HypothesisRecord(
            hypothesis_id="hyp-pragma-r1-1",
            level=OptimizationLevel.PRAGMA,
            parent_candidate_id="baseline",
            claim="Pipeline one loop",
            supporting_evidence_ids=(),
            expected_benefit={"metric": "latency", "direction": "decrease"},
            risk=HypothesisRisk.LOW,
            modification_scope=("loop",),
            verification_plan=("preflight", "public", "csynth", "hidden"),
            model_identity={"provider": "fixture"},
            prompt_identity_sha256="b" * 64,
        )
        with self.assertRaises(ValueError):
            self.request(hypothesis=invalid)

    def test_rewrite_prompt_supports_explicit_safe_abstention(self):
        prompt = self.builder.build_rewrite(self.request())
        body = "\n".join(message.content for message in prompt.messages)
        self.assertIn("AGREFACTOR_ABSTAIN", body)
        self.assertIn("raw complete translation unit", body)

    def test_hypothesis_prompt_requires_source_only_executability(self):
        prompt = self.builder.build_hypothesis(
            StructuralHypothesisPromptRequest(
                task=task(),
                parent_candidate_id="baseline",
                parent_source=SOURCE,
                round_number=1,
                max_hypotheses=3,
            )
        )
        self.assertIn("implementable as a concrete source-only change", prompt.messages[0].content)

    def test_rewrite_prompt_explicitly_avoids_static_certification(self):
        prompt = self.builder.build_rewrite(self.request())
        self.assertIn(
            "no static string matcher will certify the edit",
            prompt.messages[0].content,
        )

    def test_hypothesis_change_changes_rewrite_identity(self):
        first = self.builder.build_rewrite(self.request())
        changed = hypothesis(claim="Reorganize the loop into a producer consumer pipeline")
        second = self.builder.build_rewrite(self.request(hypothesis=changed))
        self.assertNotEqual(
            first.manifest["prompt_identity_sha256"],
            second.manifest["prompt_identity_sha256"],
        )

    def test_rewrite_safe_context_rejects_operator_full(self):
        with self.assertRaises(ValueError):
            self.request(safe_context={"operator_full": {"detail": "x"}})

    def test_rewrite_prompt_does_not_include_hidden_suite_content(self):
        with tempfile.TemporaryDirectory() as directory:
            hidden = Path(directory) / "hidden.cpp"
            hidden.write_text("NEVER_EXPOSE_THIS", encoding="utf-8")
            suite = TestSuiteSpec(
                suite_id="hidden-suite",
                split=EvaluationSplit.HIDDEN,
                testbench_path=str(hidden),
            )
            prompt = self.builder.build_rewrite(
                self.request(task=task(suites=(suite,)))
            )
            rendered = "\n".join(message.content for message in prompt.messages)
            self.assertNotIn(str(hidden), rendered)
            self.assertNotIn("NEVER_EXPOSE_THIS", rendered)


    def test_rewrite_reserves_pragma_edits_for_pragma_level(self):
        prompt = StructuralOptimizationPromptBuilder().build_rewrite(self.request())
        body = "\n".join(message.content for message in prompt.messages)
        self.assertIn("Do not add, remove, or modify HLS pragmas/directives", body)
        self.assertIn("Pragma level owns directive edits", body)


if __name__ == "__main__":
    unittest.main()
