import json
import unittest

from agrefactor.config import TaskSpec
from agrefactor.evaluation import (
    FeedbackRouteAction,
    FeedbackRouter,
    TestbenchPreflightFeedbackAdapter,
    TestbenchPreflightFeedbackViewAdapter,
    ValidationState,
    ValidationStateMachine,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    TestbenchDiagnostic,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)


SECRET_ROOT = "/home/operator/private/project"
SECRET_TOKEN = "OPERATOR_SECRET_TOKEN"


def make_result(
    *,
    status=TestbenchPreflightStatus.FAILED,
    kind=TestbenchFailureKind.SYNTAX_ERROR,
    owner=TestbenchFailureOwner.TESTBENCH,
    diagnostics=(),
    stdout="",
    stderr="",
):
    return TestbenchPreflightResult(
        status=status,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=kind,
        failure_owner=owner,
        return_code=0 if status is TestbenchPreflightStatus.PASSED else 1,
        command=("/usr/bin/g++", f"{SECRET_ROOT}/testbench.cpp"),
        diagnostics=tuple(diagnostics),
        stdout=stdout,
        stderr=stderr,
        artifacts=(f"{SECRET_ROOT}/preflight_binary",),
        duration_s=0.25,
    )


def make_diagnostic(
    *,
    kind=TestbenchFailureKind.SYNTAX_ERROR,
    message="expected ')' after expression",
):
    return TestbenchDiagnostic(
        kind=kind,
        message=message,
        file=f"{SECRET_ROOT}/testbench.cpp",
        line=42,
        column=7,
        raw=(
            f"{SECRET_ROOT}/testbench.cpp:42:7: "
            f"error: {message}"
        ),
    )


class PreflightFeedbackViewTests(unittest.TestCase):
    def setUp(self):
        self.operator_adapter = TestbenchPreflightFeedbackAdapter()
        self.view_adapter = TestbenchPreflightFeedbackViewAdapter()

    def safe(self, source):
        operator = self.operator_adapter.to_operator_report(
            source,
            report_id="operator",
        )
        return self.view_adapter.to_agent_report(
            operator,
            report_id="agent",
        )

    def test_preserves_structured_semantics(self):
        safe = self.safe(
            make_result(diagnostics=(make_diagnostic(),))
        )
        item = safe.items[0]
        self.assertEqual(item.category, FeedbackCategory.SYNTAX_ERROR)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)
        self.assertEqual(item.owner, FeedbackOwner.TESTBENCH)
        self.assertEqual(item.metadata["file"], "testbench.cpp")
        self.assertEqual(item.metadata["line"], 42)
        self.assertTrue(safe.blocking)

    def test_removes_operator_evidence_and_paths(self):
        safe = self.safe(
            make_result(
                diagnostics=(make_diagnostic(),),
                stdout=f"{SECRET_TOKEN} {SECRET_ROOT}",
                stderr=f"{SECRET_TOKEN} {SECRET_ROOT}",
            )
        )
        payload = json.dumps(safe.to_dict(), sort_keys=True)
        for forbidden in (
            SECRET_ROOT,
            SECRET_TOKEN,
            "/usr/bin/g++",
            '"command"',
            '"stdout"',
            '"stderr"',
            '"artifacts"',
        ):
            self.assertNotIn(forbidden, payload)
        self.assertIsNone(safe.items[0].evidence_ref)

    def test_sanitizes_detail_and_keeps_basename(self):
        safe = self.safe(
            make_result(diagnostics=(make_diagnostic(),))
        )
        self.assertIn("<PATH>", safe.items[0].detail)
        self.assertNotIn(SECRET_ROOT, safe.items[0].detail)

    def test_fallback_drops_raw_streams(self):
        safe = self.safe(
            make_result(
                kind=TestbenchFailureKind.UNKNOWN,
                owner=TestbenchFailureOwner.UNKNOWN,
                stdout=f"{SECRET_TOKEN} {SECRET_ROOT}",
                stderr=f"{SECRET_TOKEN} {SECRET_ROOT}",
            )
        )
        payload = json.dumps(safe.to_dict(), sort_keys=True)
        self.assertTrue(safe.items[0].metadata["detail_redacted"])
        self.assertNotIn(SECRET_TOKEN, payload)
        self.assertNotIn(SECRET_ROOT, payload)

    def test_unknown_remains_blocking_unknown(self):
        safe = self.safe(
            make_result(
                kind=TestbenchFailureKind.UNKNOWN,
                owner=TestbenchFailureOwner.UNKNOWN,
            )
        )
        self.assertEqual(safe.items[0].category, FeedbackCategory.UNKNOWN)
        self.assertEqual(safe.items[0].owner, FeedbackOwner.UNKNOWN)
        self.assertTrue(safe.items[0].blocking)

    def test_passed_report_is_empty(self):
        safe = self.safe(
            make_result(
                status=TestbenchPreflightStatus.PASSED,
                kind=TestbenchFailureKind.NONE,
                owner=TestbenchFailureOwner.NONE,
            )
        )
        self.assertEqual(safe.items, ())
        self.assertEqual(safe.metadata["evidence_view"], "agent_safe")

    def test_toolchain_semantics_preserved(self):
        safe = self.safe(
            make_result(
                status=TestbenchPreflightStatus.ERROR,
                kind=TestbenchFailureKind.COMPILER_NOT_FOUND,
                owner=TestbenchFailureOwner.TOOLCHAIN,
            )
        )
        item = safe.items[0]
        self.assertEqual(item.category, FeedbackCategory.TOOLCHAIN_FAILURE)
        self.assertEqual(item.owner, FeedbackOwner.TOOLCHAIN)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)

    def test_round_trip(self):
        safe = self.safe(
            make_result(diagnostics=(make_diagnostic(),))
        )
        self.assertEqual(
            FeedbackReport.from_dict(safe.to_dict()),
            safe,
        )

    def test_rejects_wrong_view_and_source(self):
        with self.assertRaises(ValueError):
            self.view_adapter.to_agent_report(
                FeedbackReport(
                    report_id="wrong",
                    source="csynth",
                    metadata={"evidence_view": "operator_full"},
                ),
                report_id="agent",
            )
        with self.assertRaises(ValueError):
            self.view_adapter.to_agent_report(
                FeedbackReport(
                    report_id="wrong",
                    source="testbench_preflight",
                    metadata={"evidence_view": "agent_safe"},
                ),
                report_id="agent",
            )

    def test_safe_testbench_failure_enters_repair_state(self):
        safe = self.safe(
            make_result(diagnostics=(make_diagnostic(),))
        )
        decision = FeedbackRouter().route(
            safe,
            decision_id="route",
        )
        transition = ValidationStateMachine(
            TaskSpec(
                task_id="task",
                kernel_path="kernel.cpp",
                kernel_name="top",
            )
        ).transition(
            ValidationState.PREFLIGHT,
            decision,
            transition_id="transition",
        )
        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_TESTBENCH,
        )
        self.assertEqual(
            transition.next_state,
            ValidationState.REPAIR_PENDING,
        )
        self.assertTrue(transition.agent_feedback_allowed)

    def test_candidate_owner_routes_candidate(self):
        safe = self.safe(
            make_result(
                kind=TestbenchFailureKind.UNDECLARED_SYMBOL,
                owner=TestbenchFailureOwner.CANDIDATE,
                diagnostics=(
                    make_diagnostic(
                        kind=TestbenchFailureKind.UNDECLARED_SYMBOL,
                        message="use of undeclared identifier 'N'",
                    ),
                ),
            )
        )
        decision = FeedbackRouter().route(
            safe,
            decision_id="route",
        )
        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )


if __name__ == "__main__":
    unittest.main()
