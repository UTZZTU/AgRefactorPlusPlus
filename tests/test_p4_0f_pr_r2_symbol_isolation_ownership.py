from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agrefactor.evaluation.feedback_routing import FeedbackRouteAction, FeedbackRouter
from agrefactor.evaluation.preflight_feedback import TestbenchPreflightFeedbackAdapter
from agrefactor.evaluation.preflight_feedback_view import TestbenchPreflightFeedbackViewAdapter
from agrefactor.evaluation.staged_preflight import run_staged_preflight
from agrefactor.evidence import (
    TestbenchFailureOwner,
    TestbenchPreflightReasonCode,
    TestbenchPreflightStatus,
)

ROOT = Path(__file__).resolve().parents[1]


class P40FPrR2SymbolIsolationOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++")
        cls.nm = shutil.which("nm")
        if cls.compiler is None or cls.nm is None:
            raise unittest.SkipTest("g++ and nm are required")

    def _run(self, reference, candidate, testbench,
             reference_top="reference_top", candidate_top="candidate_top"):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        result = run_staged_preflight(
            compiler=self.compiler,
            timeout_s=30.0,
            extra_flags=(),
            include_dirs=(),
            work_dir=root,
            testbench_code=testbench,
            original_code=reference,
            candidate_code=candidate,
            budget=None,
            original_top_function=reference_top,
            candidate_top_function=candidate_top,
        )
        return root, result

    @staticmethod
    def _tb(extra=""):
        return (
            "int reference_top(int);\n"
            "int candidate_top(int);\n"
            + extra
            + "\nint main(){return reference_top(3)==candidate_top(3)?0:1;}\n"
        )

    def _assert_candidate_collision(self, root, result, expected_symbol):
        self.assertEqual(result.status, TestbenchPreflightStatus.FAILED)
        self.assertEqual(result.failure_owner, TestbenchFailureOwner.CANDIDATE)
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.CANDIDATE_EXTERNAL_SYMBOL_COLLISION,
        )
        evidence = root / "candidate_external_symbol_collision.json"
        self.assertTrue(evidence.is_file())
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(payload["owner"], "candidate")
        self.assertEqual(payload["owner_authority"], "deterministic_proven")
        self.assertEqual(
            payload["authority"],
            "nm_defined_strong_external_exact_intersection_v1",
        )
        self.assertIn(
            expected_symbol,
            {item["symbol"] for item in payload["collisions"]},
        )

    def test_helper_function_collision_is_candidate_owned(self):
        root, result = self._run(
            "int helper(){return 1;} int reference_top(int x){return x+helper();}",
            "int helper(){return 2;} int candidate_top(int x){return x+helper();}",
            self._tb(),
        )
        self._assert_candidate_collision(root, result, "helper()")

    def test_global_collision_with_different_type_is_candidate_owned(self):
        root, result = self._run(
            "int shared_global=1; int reference_top(int x){return x+shared_global;}",
            "long shared_global=2; int candidate_top(int x){return x+(int)shared_global;}",
            self._tb(),
        )
        self._assert_candidate_collision(root, result, "shared_global")

    def test_same_signature_different_return_type_collides(self):
        root, result = self._run(
            "int helper(){return 1;} int reference_top(int x){return x+helper();}",
            "long helper(){return 2;} int candidate_top(int x){return x+(int)helper();}",
            self._tb(),
        )
        self._assert_candidate_collision(root, result, "helper()")

    def test_same_name_different_parameter_signature_does_not_collide(self):
        _, result = self._run(
            "int helper(int x){return x;} int reference_top(int x){return helper(x);}",
            "int helper(long x){return (int)x;} int candidate_top(int x){return helper((long)x);}",
            self._tb(),
        )
        self.assertEqual(result.status, TestbenchPreflightStatus.PASSED)

    def test_static_helpers_do_not_collide(self):
        _, result = self._run(
            "static int helper(){return 3;} int reference_top(int x){return x+helper();}",
            "static int helper(){return 3;} int candidate_top(int x){return x+helper();}",
            self._tb(),
        )
        self.assertEqual(result.status, TestbenchPreflightStatus.PASSED)

    def test_anonymous_namespace_helpers_do_not_collide(self):
        _, result = self._run(
            "namespace { int helper(){return 3;} } int reference_top(int x){return x+helper();}",
            "namespace { int helper(){return 3;} } int candidate_top(int x){return x+helper();}",
            self._tb(),
        )
        self.assertEqual(result.status, TestbenchPreflightStatus.PASSED)

    def test_required_candidate_top_gate_is_unchanged(self):
        _, result = self._run(
            "int reference_top(int x){return x;}",
            "int wrong_top(int x){return x;}",
            self._tb(),
        )
        self.assertEqual(result.failure_owner, TestbenchFailureOwner.CANDIDATE)
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.CANDIDATE_TOP_MISSING,
        )

    def test_unrelated_final_link_error_remains_unknown(self):
        tb = self._tb("int missing_external();\n").replace(
            "int main(){return reference_top(3)==candidate_top(3)?0:1;}",
            "int main(){return missing_external()+reference_top(3)-candidate_top(3);}",
        )
        _, result = self._run(
            "int reference_top(int x){return x;}",
            "int candidate_top(int x){return x;}",
            tb,
        )
        self.assertEqual(result.status, TestbenchPreflightStatus.FAILED)
        self.assertEqual(result.failure_owner, TestbenchFailureOwner.UNKNOWN)
        self.assertEqual(
            result.reason_codes,
            (
                TestbenchPreflightReasonCode.LINK_FAILED,
                TestbenchPreflightReasonCode.OWNERSHIP_UNKNOWN,
            ),
        )

    def test_candidate_collision_routes_to_candidate_repair(self):
        _, result = self._run(
            "int helper(){return 1;} int reference_top(int x){return x+helper();}",
            "int helper(){return 2;} int candidate_top(int x){return x+helper();}",
            self._tb(),
        )
        operator = TestbenchPreflightFeedbackAdapter().to_operator_report(
            result, report_id="pr-r2.operator"
        )
        agent = TestbenchPreflightFeedbackViewAdapter().to_agent_report(
            operator, report_id="pr-r2.agent"
        )
        decision = FeedbackRouter().route(agent, decision_id="pr-r2.route")
        self.assertEqual(decision.action, FeedbackRouteAction.REPAIR_CANDIDATE)

    def test_refactoring_prompts_contain_general_isolation_rule_only(self):
        text = (ROOT / "flow/agents/refactoring.yaml").read_text(encoding="utf-8")
        self.assertEqual(text.count("candidate-local helper functions"), 2)
        self.assertIn("frozen reference implementation may be linked", text)
        self.assertNotIn("new_node", text)
        self.assertNotIn("g_fallback", text)


if __name__ == "__main__":
    unittest.main()
