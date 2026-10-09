import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cpp_interface import extract_top_type_contract
from agrefactor.evaluation import TestbenchPreflight
from agrefactor.evaluation.feedback_routing import FeedbackRouter, FeedbackRouteAction
from agrefactor.evaluation.preflight_feedback import TestbenchPreflightFeedbackAdapter
from agrefactor.evidence import TestbenchFailureOwner, TestbenchPreflightStatus


class CandidateTypeContractPreflightTests(unittest.TestCase):
    public = (
        "struct Data { int a; int b; };\n"
        "void reference(Data input); void candidate(Data input);\n"
        "int main() { Data input = {}; reference(input); candidate(input); return 0; }\n"
    )
    original = "struct Data { int a; int b; }; void reference(Data input) {}"
    candidate = "struct Data { int a; int b; }; void candidate(Data input) {}"

    def run_preflight(self, directory, candidate=None, public=None, original=None):
        return TestbenchPreflight().compile_and_link(
            work_dir=directory, testbench_code=public or self.public,
            original_code=original or self.original,
            candidate_code=candidate or self.candidate,
            original_top_function="reference", candidate_top_function="candidate",
        )

    def test_same_size_field_order_drift_is_candidate_repair(self):
        changed = self.candidate.replace("int a; int b;", "int b; int a;")
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(directory, changed)
            self.assertEqual(result.status, TestbenchPreflightStatus.FAILED)
            self.assertEqual(result.failure_owner, TestbenchFailureOwner.CANDIDATE)
            evidence = json.loads((Path(directory) / "candidate_type_contract.json").read_text())
            invocation = json.loads((Path(directory) / "testbench_preflight_invocation.json").read_text())
            self.assertTrue(evidence["public_compile_succeeded"])
            self.assertTrue(evidence["candidate_compile_succeeded"])
            self.assertEqual(evidence["status"], "mismatch")
            self.assertEqual(invocation["ownership_evidence"]["authority"], "public_candidate_type_difference")
            self.assertIn(str(Path(directory) / "candidate_type_contract.json"), result.artifacts)
            report = TestbenchPreflightFeedbackAdapter().to_operator_report(result, report_id="types")
            route = FeedbackRouter().route(report, decision_id="route")
            self.assertEqual(route.action, FeedbackRouteAction.REPAIR_CANDIDATE)

    def test_parameter_names_and_typedef_spelling_do_not_change_layout(self):
        candidate = (
            "struct Data { int a; int b; }; typedef Data Alias;\n"
            "void candidate(Alias other_name) {}"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(directory, candidate)
            self.assertEqual(result.status, TestbenchPreflightStatus.PASSED, result.stderr)

    def test_scalar_interface_keeps_existing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(
                directory, "void candidate(const int *different_name) {}",
                "void reference(const int *input); void candidate(const int *input); int main(){int value=0;reference(&value);candidate(&value);}",
                "void reference(const int *input) {}",
            )
            self.assertEqual(result.status, TestbenchPreflightStatus.PASSED, result.stderr)

    def test_incomplete_required_types_preserve_diagnostics_and_owner_unknown(self):
        real_extract = extract_top_type_contract

        def extract(source, function, **context):
            facts = real_extract(source, function, **context)
            if context.get("require_definition"):
                facts.update(
                    status="unknown", unresolved=["Data fields not resolved"],
                    diagnostics=[{"severity": 3, "file": "/headers/types.hpp", "message": "incomplete AST"}],
                )
            return facts

        with tempfile.TemporaryDirectory() as directory, patch(
            "agrefactor.evaluation.staged_preflight.extract_top_type_contract", side_effect=extract,
        ):
            result = self.run_preflight(directory)
            self.assertEqual(result.failure_owner, TestbenchFailureOwner.UNKNOWN)
            evidence = json.loads((Path(directory) / "candidate_type_contract.json").read_text())
            self.assertEqual(evidence["status"], "incomplete")
            self.assertEqual(evidence["candidate"]["diagnostics"][0]["file"], "/headers/types.hpp")
            report = TestbenchPreflightFeedbackAdapter().to_operator_report(result, report_id="types")
            route = FeedbackRouter().route(report, decision_id="route")
            self.assertEqual(route.action, FeedbackRouteAction.REVIEW_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
