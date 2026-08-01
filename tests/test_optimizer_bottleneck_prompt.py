import hashlib
from dataclasses import replace
import unittest

from agrefactor.config import RunMode, TaskSpec
from agrefactor.optimization import (
    BottleneckEvidenceView,
    BottleneckKind,
    CandidateRecord,
    CandidateStatus,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
)
from agrefactor.prompts.optimization import (
    BOTTLENECK_ALLOWED_SIGNAL_FIELDS,
    BOTTLENECK_ANALYSIS_PURPOSE,
    BOTTLENECK_REWRITE_PURPOSE,
    BottleneckAnalysisPromptRequest,
    BottleneckOptimizationPromptBuilder,
    BottleneckRewritePromptRequest,
)


SOURCE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    for (int i = 0; i < n; ++i) a[i] += 1;\n}\n"""
CONTEXT = "c" * 64


def task(mode=RunMode.OPTIMIZE):
    return TaskSpec(
        task_id="s35-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        mode=mode,
    )


def ppa():
    return PpaEvidence(
        evidence_id="ppa-baseline",
        parser_profile="s35-test",
        report_format=PpaReportFormat.XML,
        report_relative_path="reports/top_csynth.xml",
        report_sha256=hashlib.sha256(b"report").hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
        latency_cycles_min=96,
        latency_cycles_max=112,
        initiation_interval_min=1,
        initiation_interval_max=2,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.25,
        resources_used=PpaResourceUsage(bram_18k=4, dsp=8, ff=1200, lut=900, uram=0),
        resources_available=PpaResourceUsage(bram_18k=100, dsp=200, ff=100000, lut=50000, uram=20),
        max_resource_utilization_ratio=0.04,
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
    classification = {
        "schema_version": 1,
        "classification_id": "btl-baseline-r1-1",
        "parent_candidate_id": "baseline",
        "kind": BottleneckKind.INITIATION_INTERVAL.value,
        "claim": "The reported initiation interval is above one.",
        "confidence": "high",
        "supporting_evidence_ids": ["ppa-baseline"],
        "signal_fields": ["initiation_interval_max"],
        "model_identity": {"provider": "fixture"},
        "prompt_identity_sha256": "a" * 64,
        "authoritative": False,
        "classification_source": "model_inference",
    }
    return HypothesisRecord(
        hypothesis_id="hyp-bottleneck-r1-1",
        level=OptimizationLevel.BOTTLENECK,
        parent_candidate_id="baseline",
        claim="Reduce the loop initiation interval by removing the carried accumulator.",
        supporting_evidence_ids=("ppa-baseline",),
        expected_benefit={"metric": "latency", "direction": "decrease"},
        risk=HypothesisRisk.MEDIUM,
        modification_scope=("loop-carried accumulator",),
        verification_plan=("preflight", "public", "csynth", "hidden"),
        model_identity={"provider": "fixture", "classification": classification},
        prompt_identity_sha256="b" * 64,
    )


def analysis_request(**updates):
    values = {
        "task": task(),
        "parent_candidate_id": "baseline",
        "parent_source": SOURCE,
        "round_number": 1,
        "max_classifications": 3,
        "max_hypotheses": 3,
        "evidence": evidence(),
        "safe_context": {"policy": "safe-v1", "objective": "latency"},
    }
    values.update(updates)
    return BottleneckAnalysisPromptRequest(**values)


def rewrite_request(**updates):
    values = {
        "task": task(),
        "candidate_id": "cand-1",
        "parent_candidate_id": "baseline",
        "parent_source": SOURCE,
        "hypothesis": hypothesis(),
        "safe_context": {"policy": "safe-v1", "objective": "latency"},
    }
    values.update(updates)
    return BottleneckRewritePromptRequest(**values)


class BottleneckAnalysisPromptTests(unittest.TestCase):
    def test_analysis_prompt_is_deterministic(self):
        builder = BottleneckOptimizationPromptBuilder()
        self.assertEqual(builder.build_analysis(analysis_request()), builder.build_analysis(analysis_request()))

    def test_manifest_has_frozen_purpose_and_level(self):
        manifest = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request()).manifest
        self.assertEqual(manifest["purpose"], BOTTLENECK_ANALYSIS_PURPOSE)
        self.assertEqual(manifest["level"], "bottleneck")
        self.assertEqual(manifest["classification_authority"], "model_inference_not_tool_fact")

    def test_manifest_hashes_evidence_without_raw_report(self):
        manifest = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request()).manifest
        self.assertEqual(manifest["evidence_id"], "ppa-baseline")
        self.assertEqual(len(manifest["evidence_projection_sha256"]), 64)
        self.assertNotIn("top_csynth.xml", str(manifest))
        self.assertFalse(manifest["raw_report_included"])

    def test_user_prompt_contains_typed_evidence_and_source(self):
        prompt = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request())
        user = prompt.messages[1].content
        self.assertIn('"initiation_interval_max": 2', user)
        self.assertIn("void top", user)
        self.assertNotIn("reports/top_csynth.xml", user)

    def test_prompt_requires_unknown_when_insufficient(self):
        system = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request()).messages[0].content
        self.assertIn("kind=unknown", system)
        self.assertIn("no executable hypothesis", system)

    def test_prompt_rejects_static_gate_semantics(self):
        system = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request()).messages[0].content
        self.assertIn("source-string", system)
        self.assertIn("warning-regex", system)
        self.assertIn("not an authoritative tool fact", system)

    def test_prompt_states_strict_json_contract(self):
        system = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request()).messages[0].content
        self.assertIn('"classifications"', system)
        self.assertIn('"hypotheses"', system)
        self.assertIn("strict JSON only", system)

    def test_prompt_exposes_exact_signal_leaf_allowlist(self):
        prompt = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request())
        system = prompt.messages[0].content
        user = prompt.messages[1].content
        for field in BOTTLENECK_ALLOWED_SIGNAL_FIELDS:
            self.assertIn(field, system)
            self.assertIn(field, user)
        self.assertIn(
            "resources_used and resources_available are JSON container names, not valid signal_fields",
            system,
        )
        self.assertIn('"allowed_signal_fields"', user)

    def test_analysis_rejects_refactor_mode(self):
        with self.assertRaises(ValueError):
            analysis_request(task=task(RunMode.REFACTOR))

    def test_analysis_allows_full_mode(self):
        request = analysis_request(task=task(RunMode.FULL))
        self.assertEqual(request.task.mode, RunMode.FULL)

    def test_analysis_rejects_more_than_three(self):
        with self.assertRaises(ValueError):
            analysis_request(max_hypotheses=4)
        with self.assertRaises(ValueError):
            analysis_request(max_classifications=4)

    def test_analysis_rejects_empty_source(self):
        with self.assertRaises(ValueError):
            analysis_request(parent_source=" ")

    def test_analysis_rejects_raw_report_projection(self):
        bad = dict(evidence())
        bad["raw_report_included"] = True
        with self.assertRaises(ValueError):
            analysis_request(evidence=bad)

    def test_analysis_rejects_hidden_projection(self):
        bad = dict(evidence())
        bad["hidden_evidence_included"] = True
        with self.assertRaises(ValueError):
            analysis_request(evidence=bad)

    def test_source_change_changes_identity(self):
        builder = BottleneckOptimizationPromptBuilder()
        first = builder.build_analysis(analysis_request()).manifest["prompt_identity_sha256"]
        second = builder.build_analysis(analysis_request(parent_source=SOURCE + "\n// change\n")).manifest["prompt_identity_sha256"]
        self.assertNotEqual(first, second)

    def test_evidence_change_changes_identity(self):
        builder = BottleneckOptimizationPromptBuilder()
        first = builder.build_analysis(analysis_request()).manifest["prompt_identity_sha256"]
        changed = dict(evidence())
        changed["metrics"] = dict(changed["metrics"])
        changed["metrics"]["initiation_interval_max"] = 3
        second = builder.build_analysis(analysis_request(evidence=changed)).manifest["prompt_identity_sha256"]
        self.assertNotEqual(first, second)

    def test_family_instruction_changes_identity(self):
        builder = BottleneckOptimizationPromptBuilder()
        first = builder.build_analysis(analysis_request()).manifest["prompt_identity_sha256"]
        second = builder.build_analysis(analysis_request(family_instruction="Return concise JSON.")).manifest["prompt_identity_sha256"]
        self.assertNotEqual(first, second)


class BottleneckRewritePromptTests(unittest.TestCase):
    def test_rewrite_prompt_is_deterministic(self):
        builder = BottleneckOptimizationPromptBuilder()
        self.assertEqual(builder.build_rewrite(rewrite_request()), builder.build_rewrite(rewrite_request()))

    def test_rewrite_manifest_has_complete_source_contract(self):
        manifest = BottleneckOptimizationPromptBuilder().build_rewrite(rewrite_request()).manifest
        self.assertEqual(manifest["purpose"], BOTTLENECK_REWRITE_PURPOSE)
        self.assertTrue(manifest["output_contract"]["complete_replacement"])
        self.assertTrue(manifest["output_contract"]["top_function_interface_must_remain_unchanged"])

    def test_rewrite_contains_classification_and_hypothesis(self):
        prompt = BottleneckOptimizationPromptBuilder().build_rewrite(rewrite_request())
        user = prompt.messages[1].content
        self.assertIn("btl-baseline-r1-1", user)
        self.assertIn("hyp-bottleneck-r1-1", user)
        self.assertIn("void top", user)

    def test_rewrite_rejects_parent_mismatch(self):
        with self.assertRaises(ValueError):
            rewrite_request(parent_candidate_id="cand-9")

    def test_rewrite_rejects_structural_hypothesis(self):
        structural = replace(hypothesis(), level=OptimizationLevel.STRUCTURAL, supporting_evidence_ids=())
        with self.assertRaises(ValueError):
            rewrite_request(hypothesis=structural)

    def test_rewrite_rejects_missing_classification(self):
        bad = replace(hypothesis(), model_identity={"provider": "fixture"})
        with self.assertRaises(ValueError):
            rewrite_request(hypothesis=bad)

    def test_rewrite_rejects_authoritative_classification(self):
        identity = dict(hypothesis().model_identity)
        classification = dict(identity["classification"])
        classification["authoritative"] = True
        identity["classification"] = classification
        bad = replace(hypothesis(), model_identity=identity)
        with self.assertRaises(ValueError):
            rewrite_request(hypothesis=bad)

    def test_rewrite_rejects_hidden_context(self):
        with self.assertRaises(ValueError):
            rewrite_request(safe_context={"hidden_report": "x"})

    def test_rewrite_prompt_supports_explicit_safe_abstention(self):
        prompt = BottleneckOptimizationPromptBuilder().build_rewrite(rewrite_request())
        body = "\n".join(message.content for message in prompt.messages)
        contract = prompt.manifest["output_contract"]
        self.assertTrue(contract["raw_complete_source_allowed"])
        self.assertEqual(contract["explicit_abstention_token"], "AGREFACTOR_ABSTAIN")
        self.assertIn("source-only change cannot be implemented", body)

    def test_analysis_omits_non_source_only_hypotheses(self):
        prompt = BottleneckOptimizationPromptBuilder().build_analysis(analysis_request())
        self.assertIn("concrete source-only causal change", prompt.messages[0].content)

    def test_rewrite_explicitly_keeps_qualification_authoritative(self):
        system = BottleneckOptimizationPromptBuilder().build_rewrite(rewrite_request()).messages[0].content
        self.assertIn("requires full qualification", system)
        self.assertIn("qualification and PPA evidence remain authoritative", system)


    def test_rewrite_reserves_pragma_edits_for_pragma_level(self):
        prompt = BottleneckOptimizationPromptBuilder().build_rewrite(rewrite_request())
        body = "\n".join(message.content for message in prompt.messages)
        self.assertIn("Do not add, remove, or modify HLS pragmas/directives", body)
        self.assertIn("Pragma level owns directive edits", body)


if __name__ == "__main__":
    unittest.main()
