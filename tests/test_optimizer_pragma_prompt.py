import hashlib
import os
import subprocess
import sys
from dataclasses import replace
import unittest

from agrefactor.config import RunMode, TaskSpec
from agrefactor.optimization import (
    BottleneckEvidenceView,
    CandidateRecord,
    CandidateStatus,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
    PragmaConfidence,
    PragmaKind,
    PragmaTargetKind,
)
from agrefactor.prompts.optimization import (
    PRAGMA_ALLOWED_KINDS,
    PRAGMA_ALLOWED_SIGNAL_FIELDS,
    PRAGMA_ALLOWED_TARGET_KINDS,
    PRAGMA_ANALYSIS_PURPOSE,
    PRAGMA_REWRITE_PURPOSE,
    PragmaAnalysisPromptRequest,
    PragmaOptimizationPromptBuilder,
    PragmaRewritePromptRequest,
)


SOURCE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    for (int i = 0; i < n; ++i) a[i] += 1;\n}\n"""
CONTEXT = "7" * 64


def task(mode=RunMode.OPTIMIZE):
    return TaskSpec(
        task_id="s36-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        mode=mode,
    )


def ppa():
    return PpaEvidence(
        evidence_id="ppa-baseline",
        parser_profile="s36-test",
        report_format=PpaReportFormat.XML,
        report_relative_path="reports/top_csynth.xml",
        report_sha256=hashlib.sha256(b"report").hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
        latency_cycles_min=120,
        latency_cycles_max=128,
        initiation_interval_min=4,
        initiation_interval_max=4,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.2,
        resources_used=PpaResourceUsage(
            bram_18k=2, dsp=4, ff=900, lut=700, uram=0
        ),
        resources_available=PpaResourceUsage(
            bram_18k=100, dsp=200, ff=100000, lut=50000, uram=20
        ),
        max_resource_utilization_ratio=0.02,
        objective_feasible=True,
        parser_warnings=("fixture_only",),
    )


def baseline():
    return CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
        ppa=ppa().to_dict(),
    )


def evidence():
    return BottleneckEvidenceView.from_candidate(baseline()).to_dict()


def hypothesis():
    action = {
        "schema_version": 1,
        "action_id": "pragma-baseline-r1-1",
        "parent_candidate_id": "baseline",
        "kind": PragmaKind.PIPELINE.value,
        "target_kind": PragmaTargetKind.LOOP.value,
        "target_ref": "top.loop_i",
        "parameters": {"ii": 1},
        "claim": "Pipeline the selected high-II loop.",
        "confidence": PragmaConfidence.HIGH.value,
        "supporting_evidence_ids": ["ppa-baseline"],
        "signal_fields": ["initiation_interval_max"],
        "model_identity": {"provider": "fixture"},
        "prompt_identity_sha256": "a" * 64,
        "authoritative": False,
        "action_source": "model_proposal",
    }
    return HypothesisRecord(
        hypothesis_id="hyp-pragma-r1-1",
        level=OptimizationLevel.PRAGMA,
        parent_candidate_id="baseline",
        claim="Add one pipeline directive to the selected loop.",
        supporting_evidence_ids=("ppa-baseline",),
        expected_benefit={"metric": "latency", "direction": "decrease"},
        risk=HypothesisRisk.MEDIUM,
        modification_scope=("top.loop_i pipeline directive only",),
        verification_plan=("preflight", "public", "csynth", "hidden"),
        model_identity={"provider": "fixture", "pragma_action": action},
        prompt_identity_sha256="b" * 64,
    )


class PragmaAnalysisPromptTests(unittest.TestCase):
    def test_prompt_module_imports_first_in_fresh_process(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from agrefactor.prompts.optimization import "
                    "PragmaOptimizationPromptBuilder; "
                    "print(PragmaOptimizationPromptBuilder.__name__)"
                ),
            ],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertEqual(completed.stdout.strip(), "PragmaOptimizationPromptBuilder")

    def request(self, **updates):
        values = {
            "task": task(),
            "parent_candidate_id": "baseline",
            "parent_source": SOURCE,
            "round_number": 1,
            "max_actions": 3,
            "max_hypotheses": 3,
            "evidence": evidence(),
            "safe_context": {"policy": "safe-v1", "objective": "latency"},
        }
        values.update(updates)
        return PragmaAnalysisPromptRequest(**values)

    def test_analysis_manifest_is_deterministic(self):
        builder = PragmaOptimizationPromptBuilder()
        first = builder.build_analysis(self.request())
        second = builder.build_analysis(self.request())
        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(first.manifest["purpose"], PRAGMA_ANALYSIS_PURPOSE)
        self.assertFalse(first.manifest["static_pragma_gate"])

    def test_prompt_contains_exact_action_target_and_signal_allowlists(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        for value in PRAGMA_ALLOWED_KINDS:
            self.assertIn(value, text)
        for value in PRAGMA_ALLOWED_TARGET_KINDS:
            self.assertIn(value, text)
        for value in PRAGMA_ALLOWED_SIGNAL_FIELDS:
            self.assertIn(value, text)
        self.assertIn("Generic RESOURCE is not a safe-v1 action", text)

    def test_prompt_forbids_static_pragma_gate_and_tool_claims(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        self.assertIn("Do not scan text", text)
        self.assertIn("not an authoritative statement", text)
        self.assertIn("kind=unknown", text)

    def test_prompt_contains_exact_directive_target_compatibility_matrix(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        for rule in (
            "pipeline -> loop or function",
            "unroll -> loop",
            "array_partition -> array",
            "dataflow -> function or region",
            "inline -> function",
            "bind_op -> operation",
            "unknown -> unknown",
        ):
            self.assertIn(rule, text)

    def test_prompt_disambiguates_nested_metrics_from_signal_field_names(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        self.assertIn("signal_fields MUST omit the metrics. prefix", text)
        self.assertIn("never metrics.initiation_interval_max", text)

    def test_prompt_contains_complete_executable_and_unknown_shapes(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        self.assertIn("Valid executable shape example", text)
        self.assertIn("Valid safe-unknown shape example", text)
        self.assertIn("SUPPLIED_EVIDENCE_ID", text)
        self.assertIn("self-check every action", text)

    def test_prompt_matches_official_inline_argument_shape(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        self.assertIn("inline: {} for ordinary INLINE", text)
        self.assertIn('mode\":\"off|recursive', text)
        self.assertNotIn('mode\":\"on|off|recursive', text)

    def test_evidence_projection_excludes_raw_report_and_hidden(self):
        payload = evidence()
        self.assertFalse(payload["raw_report_included"])
        self.assertFalse(payload["hidden_evidence_included"])
        prompt = PragmaOptimizationPromptBuilder().build_analysis(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        self.assertNotIn("report_relative_path", text)

    def test_raw_report_flag_true_is_rejected(self):
        payload = evidence()
        payload["raw_report_included"] = True
        with self.assertRaises(ValueError):
            self.request(evidence=payload)

    def test_hidden_evidence_flag_true_is_rejected(self):
        payload = evidence()
        payload["hidden_evidence_included"] = True
        with self.assertRaises(ValueError):
            self.request(evidence=payload)

    def test_hidden_safe_context_is_rejected(self):
        with self.assertRaises(ValueError):
            self.request(safe_context={"hidden_report": "x"})

    def test_prompt_manifest_changes_with_evidence(self):
        changed = evidence()
        changed["metrics"]["initiation_interval_max"] = 5
        builder = PragmaOptimizationPromptBuilder()
        self.assertNotEqual(
            builder.build_analysis(self.request()).manifest["prompt_identity_sha256"],
            builder.build_analysis(self.request(evidence=changed)).manifest[
                "prompt_identity_sha256"
            ],
        )

    def test_family_instruction_is_included(self):
        prompt = PragmaOptimizationPromptBuilder().build_analysis(
            self.request(family_instruction="Return compact strict JSON.")
        )
        text = "\n".join(message.content for message in prompt.messages)
        self.assertIn("Return compact strict JSON.", text)
        self.assertTrue(prompt.manifest["family_instruction_present"])

    def test_bounds_are_frozen_to_three(self):
        with self.assertRaises(ValueError):
            self.request(max_actions=4)
        with self.assertRaises(ValueError):
            self.request(max_hypotheses=0)


class PragmaRewritePromptTests(unittest.TestCase):
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
        return PragmaRewritePromptRequest(**values)

    def test_rewrite_manifest_and_contract(self):
        prompt = PragmaOptimizationPromptBuilder().build_rewrite(self.request())
        self.assertEqual(prompt.manifest["purpose"], PRAGMA_REWRITE_PURPOSE)
        self.assertTrue(prompt.manifest["output_contract"]["complete_replacement"])
        self.assertFalse(prompt.manifest["static_pragma_gate"])

    def test_rewrite_prompt_supports_explicit_safe_abstention(self):
        prompt = PragmaOptimizationPromptBuilder().build_rewrite(self.request())
        body = "\n".join(message.content for message in prompt.messages)
        contract = prompt.manifest["output_contract"]
        self.assertTrue(contract["raw_complete_source_allowed"])
        self.assertEqual(contract["explicit_abstention_token"], "AGREFACTOR_ABSTAIN")
        self.assertIn("typed target cannot be located", body)

    def test_rewrite_contains_selected_action_and_complete_source(self):
        prompt = PragmaOptimizationPromptBuilder().build_rewrite(self.request())
        text = "\n".join(message.content for message in prompt.messages)
        self.assertIn("pragma-baseline-r1-1", text)
        self.assertIn("top.loop_i", text)
        self.assertIn(SOURCE.strip(), text)
        self.assertIn("complete replacement translation unit", text)

    def test_rewrite_rejects_non_pragma_hypothesis(self):
        with self.assertRaises(ValueError):
            self.request(hypothesis=replace(hypothesis(), level=OptimizationLevel.BOTTLENECK))

    def test_rewrite_rejects_parent_mismatch(self):
        with self.assertRaises(ValueError):
            self.request(parent_candidate_id="cand-other")

    def test_rewrite_requires_non_authoritative_action(self):
        hyp = hypothesis()
        identity = dict(hyp.model_identity)
        action = dict(identity["pragma_action"])
        action["authoritative"] = True
        identity["pragma_action"] = action
        with self.assertRaises(ValueError):
            self.request(hypothesis=replace(hyp, model_identity=identity))

    def test_rewrite_rejects_missing_action_metadata(self):
        with self.assertRaises(ValueError):
            self.request(hypothesis=replace(hypothesis(), model_identity={"provider": "fixture"}))


if __name__ == "__main__":
    unittest.main()
