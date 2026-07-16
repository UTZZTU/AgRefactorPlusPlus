import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TestSuiteSpec,
)
from agrefactor.evaluation import (
    TestEvaluationFeedbackAdapter,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackSeverity,
    FeedbackStage,
    TestEvaluationEvidence,
    TestEvaluationStatus,
)


class TestEvaluationFeedbackAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = TestEvaluationFeedbackAdapter()

    def make_public(
        self,
        *,
        status=TestEvaluationStatus.FAILED,
        summary="Public evaluation failed",
        timed_out=False,
        return_code=1,
        details=None,
        artifacts=("runs/public.json",),
    ) -> TestEvaluationEvidence:
        return TestEvaluationEvidence(
            suite=TestSuiteSpec(
                suite_id="generic-public",
                split=EvaluationSplit.PUBLIC,
                case_count=4,
                testbench_path="tests/public.cpp",
            ),
            status=status,
            passed_cases=3 if status is not TestEvaluationStatus.PASSED else 4,
            failed_cases=1 if status is not TestEvaluationStatus.PASSED else 0,
            timed_out=timed_out,
            return_code=return_code,
            summary=summary,
            details=details or {},
            artifacts=artifacts,
        )

    def make_hidden(
        self,
        *,
        status=TestEvaluationStatus.FAILED,
        timed_out=False,
        details=None,
    ) -> TestEvaluationEvidence:
        return TestEvaluationEvidence(
            suite=TestSuiteSpec(
                suite_id="generic-hidden",
                split=EvaluationSplit.HIDDEN,
                case_count=2,
                testbench_path="secure/hidden.cpp",
            ),
            status=status,
            passed_cases=1,
            failed_cases=1,
            timed_out=timed_out,
            return_code=None if timed_out else 1,
            summary="SECRET_HIDDEN_SUMMARY",
            details=details or {
                "legacy_status": "csim_failed",
                "diagnostic": "SECRET_HIDDEN_DIAGNOSTIC",
                "input": "SECRET_INPUT",
                "expected": "SECRET_EXPECTED",
                "actual": "SECRET_ACTUAL",
                "simulation_execution": {
                    "status": "completed",
                    "returncode": 1,
                    "timeout": False,
                },
            },
            artifacts=("secure/secret-hidden.json",),
        )

    def test_passed_evidence_creates_empty_report(self) -> None:
        evidence = self.make_public(
            status=TestEvaluationStatus.PASSED,
            summary="All tests passed",
            return_code=0,
        )

        report = self.adapter.to_agent_report(
            evidence,
            report_id="passed",
        )

        self.assertEqual(report.items, ())
        self.assertFalse(report.blocking)
        self.assertIsNone(report.highest_severity)
        self.assertEqual(
            report.metadata["evaluation_status"],
            "passed",
        )

    def test_public_agent_report_preserves_diagnostic(self) -> None:
        evidence = self.make_public(
            details={
                "legacy_status": "csim_failed",
                "diagnostic": "PUBLIC_EXPECTED_4_ACTUAL_5",
                "simulation_execution": {
                    "status": "completed",
                    "returncode": 1,
                    "timeout": False,
                },
            },
        )

        report = self.adapter.to_agent_report(
            evidence,
            report_id="public-failure",
        )
        item = report.items[0]

        self.assertEqual(item.stage, FeedbackStage.CSIM)
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)
        self.assertEqual(
            item.detail,
            "PUBLIC_EXPECTED_4_ACTUAL_5",
        )
        self.assertEqual(
            item.evidence_ref,
            "runs/public.json",
        )
        self.assertFalse(report.source_evidence["redacted"])

    def test_hidden_operator_report_preserves_full_evidence(
        self,
    ) -> None:
        evidence = self.make_hidden()

        report = self.adapter.to_operator_report(
            evidence,
            report_id="hidden-operator",
        )
        encoded = json.dumps(
            report.to_dict(),
            ensure_ascii=False,
        )

        self.assertEqual(
            report.metadata["evidence_view"],
            "operator_full",
        )
        self.assertFalse(report.metadata["source_redacted"])
        self.assertIn("SECRET_HIDDEN_SUMMARY", encoded)
        self.assertIn("SECRET_HIDDEN_DIAGNOSTIC", encoded)
        self.assertIn("SECRET_INPUT", encoded)
        self.assertIn("secure/hidden.cpp", encoded)
        self.assertIn("secret-hidden.json", encoded)

    def test_hidden_agent_report_redacts_every_secret(self) -> None:
        evidence = self.make_hidden()

        report = self.adapter.to_agent_report(
            evidence,
            report_id="hidden-agent",
        )
        item = report.items[0]
        encoded = json.dumps(
            report.to_dict(),
            ensure_ascii=False,
        )

        self.assertEqual(
            report.metadata["evidence_view"],
            "agent_safe",
        )
        self.assertTrue(report.metadata["source_redacted"])
        self.assertTrue(report.source_evidence["redacted"])
        self.assertEqual(
            item.summary,
            "Hidden evaluation failed.",
        )
        self.assertIsNone(item.detail)
        self.assertIsNone(item.evidence_ref)
        self.assertEqual(item.stage, FeedbackStage.TEST)
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)

        for secret in (
            "SECRET_HIDDEN_SUMMARY",
            "SECRET_HIDDEN_DIAGNOSTIC",
            "SECRET_INPUT",
            "SECRET_EXPECTED",
            "SECRET_ACTUAL",
            "hidden.cpp",
            "secret-hidden.json",
            "csim_failed",
        ):
            self.assertNotIn(secret, encoded)

    def test_compile_failure_maps_to_compile_stage(self) -> None:
        evidence = self.make_public(
            status=TestEvaluationStatus.ERROR,
            summary="Compilation failed",
            details={
                "legacy_status": "tb_compile_failed",
                "diagnostic": "compiler output",
                "compile_execution": {
                    "status": "completed",
                    "returncode": 1,
                    "timeout": False,
                },
                "simulation_execution": {
                    "status": "skipped_after_compile_failure",
                },
            },
        )

        item = self.adapter.to_operator_report(
            evidence,
            report_id="compile",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.COMPILE)
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)

    def test_timeout_maps_to_fatal_timeout(self) -> None:
        evidence = self.make_public(
            status=TestEvaluationStatus.FAILED,
            summary="Simulation timed out",
            timed_out=True,
            return_code=None,
            details={
                "legacy_status": "csim_failed",
                "simulation_execution": {
                    "status": "completed",
                    "returncode": None,
                    "timeout": True,
                },
            },
        )

        item = self.adapter.to_operator_report(
            evidence,
            report_id="timeout",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.CSIM)
        self.assertEqual(item.category, FeedbackCategory.TIMEOUT)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)

    def test_launch_error_maps_to_toolchain_failure(self) -> None:
        evidence = self.make_public(
            status=TestEvaluationStatus.ERROR,
            summary="Simulation launch failed",
            details={
                "simulation_execution": {
                    "status": "launch_error",
                    "returncode": None,
                    "timeout": False,
                },
            },
        )

        item = self.adapter.to_operator_report(
            evidence,
            report_id="launch",
        ).items[0]

        self.assertEqual(item.stage, FeedbackStage.CSIM)
        self.assertEqual(
            item.category,
            FeedbackCategory.TOOLCHAIN_FAILURE,
        )
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)

    def test_explicit_safe_category_is_honored(self) -> None:
        evidence = self.make_public(
            details={
                "feedback_category": "functional_mismatch",
                "diagnostic": "known mismatch",
            },
        )

        item = self.adapter.to_operator_report(
            evidence,
            report_id="explicit",
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.FUNCTIONAL_MISMATCH,
        )

    def test_unknown_explicit_category_is_not_guessed(self) -> None:
        evidence = self.make_public(
            details={
                "feedback_category": "invented_category",
            },
        )

        item = self.adapter.to_operator_report(
            evidence,
            report_id="unknown",
        ).items[0]

        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)

    def test_agent_report_round_trip(self) -> None:
        evidence = self.make_public(
            details={"diagnostic": "public failure"},
        )

        original = self.adapter.to_agent_report(
            evidence,
            report_id="round-trip",
        )
        restored = type(original).from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_adapter_does_not_mutate_evidence(self) -> None:
        evidence = self.make_hidden()
        before = evidence.to_dict()

        self.adapter.to_agent_report(
            evidence,
            report_id="immutable",
        )

        self.assertEqual(evidence.to_dict(), before)

    def test_rejects_non_evidence_value(self) -> None:
        with self.assertRaises(TypeError):
            self.adapter.to_agent_report(
                {"status": "failed"},
                report_id="invalid",
            )

    def test_adapter_is_kernel_agnostic(self) -> None:
        families = (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        )

        reports = []
        for family in families:
            evidence = TestEvaluationEvidence(
                suite=TestSuiteSpec(
                    suite_id=f"{family}-public",
                ),
                status=TestEvaluationStatus.PASSED,
                summary="Passed",
            )
            reports.append(
                self.adapter.to_agent_report(
                    evidence,
                    report_id=f"{family}-report",
                )
            )

        self.assertEqual(len(reports), len(families))


if __name__ == "__main__":
    unittest.main()
