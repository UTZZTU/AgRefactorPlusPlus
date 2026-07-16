import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TestSuiteSpec,
)
from agrefactor.evidence import (
    TestEvaluationEvidence,
    TestEvaluationStatus,
)


class TestEvaluationEvidenceTests(unittest.TestCase):
    def make_public_suite(self) -> TestSuiteSpec:
        return TestSuiteSpec(
            suite_id="generic-public",
            suite_version="1",
            split=EvaluationSplit.PUBLIC,
            case_count=4,
            testbench_path="tests/public.cpp",
        )

    def make_hidden_suite(self) -> TestSuiteSpec:
        return TestSuiteSpec(
            suite_id="generic-hidden",
            suite_version="1",
            split=EvaluationSplit.HIDDEN,
            case_count=5,
            testbench_path="secure/hidden.cpp",
        )

    def test_public_agent_view_keeps_details(self) -> None:
        evidence = TestEvaluationEvidence(
            suite=self.make_public_suite(),
            status=TestEvaluationStatus.FAILED,
            passed_cases=3,
            failed_cases=1,
            return_code=1,
            summary="Public case 4 mismatched",
            details={
                "case_id": "case-4",
                "input": [1, 2],
                "expected": [3],
                "actual": [4],
            },
            artifacts=("runs/public.log",),
        )

        agent = evidence.to_agent_dict()

        self.assertFalse(agent["redacted"])
        self.assertEqual(agent["details"]["case_id"], "case-4")
        self.assertEqual(
            agent["suite"]["testbench_path"],
            "tests/public.cpp",
        )
        self.assertEqual(agent["artifacts"], ["runs/public.log"])

    def test_hidden_agent_view_redacts_sensitive_fields(self) -> None:
        evidence = TestEvaluationEvidence(
            suite=self.make_hidden_suite(),
            status=TestEvaluationStatus.FAILED,
            passed_cases=4,
            failed_cases=1,
            return_code=1,
            summary="Secret case failed with expected token",
            details={
                "case_id": "secret-case-5",
                "input": "SECRET_INPUT_927",
                "expected": "SECRET_EXPECTED_441",
                "actual": "SECRET_ACTUAL_118",
                "testbench_source": "SECRET_TESTBENCH_SOURCE",
            },
            artifacts=("secure/hidden-case-5.log",),
        )

        agent = evidence.to_agent_dict()
        encoded = json.dumps(agent, ensure_ascii=False)

        self.assertTrue(agent["redacted"])
        self.assertEqual(agent["details"], {})
        self.assertEqual(agent["artifacts"], [])
        self.assertNotIn("testbench_path", agent["suite"])
        self.assertEqual(agent["passed_cases"], 4)
        self.assertEqual(agent["failed_cases"], 1)
        self.assertEqual(
            agent["summary"],
            "Hidden evaluation failed.",
        )
        for secret in (
            "SECRET_INPUT_927",
            "SECRET_EXPECTED_441",
            "SECRET_ACTUAL_118",
            "SECRET_TESTBENCH_SOURCE",
            "secret-case-5",
            "hidden-case-5.log",
            "Secret case failed",
        ):
            self.assertNotIn(secret, encoded)

    def test_hidden_full_view_remains_available_to_operator(self) -> None:
        evidence = TestEvaluationEvidence(
            suite=self.make_hidden_suite(),
            status="failed",
            passed_cases=4,
            failed_cases=1,
            summary="Operator-only diagnosis",
            details={"expected": "operator-secret"},
            artifacts=("secure/operator.log",),
        )

        full = evidence.to_dict()

        self.assertFalse(full["redacted"])
        self.assertEqual(
            full["suite"]["testbench_path"],
            "secure/hidden.cpp",
        )
        self.assertEqual(
            full["details"]["expected"],
            "operator-secret",
        )
        self.assertEqual(
            full["artifacts"],
            ["secure/operator.log"],
        )

    def test_round_trip_full_evidence(self) -> None:
        original = TestEvaluationEvidence(
            suite=self.make_public_suite(),
            status=TestEvaluationStatus.PASSED,
            passed_cases=4,
            failed_cases=0,
            summary="All public cases passed",
            details={"coverage_percent": 92.5},
            artifacts=("runs/result.json",),
        )

        restored = TestEvaluationEvidence.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_reject_redacted_round_trip(self) -> None:
        evidence = TestEvaluationEvidence(
            suite=self.make_hidden_suite(),
            status=TestEvaluationStatus.FAILED,
            passed_cases=4,
            failed_cases=1,
            summary="Hidden failure",
        )

        with self.assertRaises(ValueError):
            TestEvaluationEvidence.from_dict(
                evidence.to_agent_dict()
            )

    def test_evaluated_cases_is_derived(self) -> None:
        evidence = TestEvaluationEvidence(
            suite=self.make_public_suite(),
            status=TestEvaluationStatus.FAILED,
            passed_cases=2,
            failed_cases=1,
            summary="One case failed",
        )

        self.assertEqual(evidence.evaluated_cases, 3)
        self.assertEqual(evidence.to_dict()["evaluated_cases"], 3)

    def test_reject_evaluated_count_above_suite_count(self) -> None:
        with self.assertRaises(ValueError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.FAILED,
                passed_cases=4,
                failed_cases=1,
                summary="Too many results",
            )

    def test_reject_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.ERROR,
                passed_cases=-1,
                summary="Invalid count",
            )

    def test_reject_boolean_counts(self) -> None:
        with self.assertRaises(TypeError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.ERROR,
                passed_cases=True,
                summary="Invalid count",
            )

    def test_reject_non_serializable_details(self) -> None:
        with self.assertRaises(ValueError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.ERROR,
                summary="Invalid details",
                details={"bad": object()},
            )

    def test_reject_non_finite_details(self) -> None:
        with self.assertRaises(ValueError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.ERROR,
                summary="Invalid details",
                details={"metric": float("nan")},
            )

    def test_reject_empty_summary(self) -> None:
        with self.assertRaises(ValueError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.ERROR,
                summary="  ",
            )

    def test_reject_invalid_artifact_entries(self) -> None:
        with self.assertRaises(ValueError):
            TestEvaluationEvidence(
                suite=self.make_public_suite(),
                status=TestEvaluationStatus.ERROR,
                summary="Invalid artifact",
                artifacts=("  ",),
            )

    def test_reject_derived_count_conflict(self) -> None:
        payload = TestEvaluationEvidence(
            suite=self.make_public_suite(),
            status=TestEvaluationStatus.FAILED,
            passed_cases=2,
            failed_cases=1,
            summary="One case failed",
        ).to_dict()
        payload["evaluated_cases"] = 4

        with self.assertRaises(ValueError):
            TestEvaluationEvidence.from_dict(payload)

    def test_schema_is_kernel_agnostic(self) -> None:
        for family in (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        ):
            with self.subTest(family=family):
                evidence = TestEvaluationEvidence(
                    suite=TestSuiteSpec(
                        suite_id=f"{family}-public",
                    ),
                    status=TestEvaluationStatus.PASSED,
                    summary="Evaluation passed",
                )
                self.assertEqual(
                    evidence.status,
                    TestEvaluationStatus.PASSED,
                )


if __name__ == "__main__":
    unittest.main()
