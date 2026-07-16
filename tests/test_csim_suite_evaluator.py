import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agrefactor.config import (
    EvaluationSplit,
    TestSuiteSpec,
)
from agrefactor.evaluation import CsimSuiteEvaluator
from agrefactor.evidence import TestEvaluationStatus
from agrefactor.runtime import (
    BudgetManager,
    TraceRecorder,
)


class FixedClock:
    def __call__(self) -> datetime:
        return datetime(
            2026,
            7,
            16,
            10,
            0,
            0,
            tzinfo=timezone.utc,
        )


class RecordingExecutor:
    def __init__(
        self,
        result: tuple[str, str],
        *,
        invocation: dict | None = None,
    ) -> None:
        self.result = result
        self.invocation = invocation
        self.calls = []

    def __call__(
        self,
        work_dir,
        context_variables,
        timelimit,
        *,
        budget=None,
    ):
        self.calls.append(
            {
                "work_dir": work_dir,
                "context_variables": context_variables,
                "timelimit": timelimit,
                "budget": budget,
            }
        )
        if self.invocation is not None:
            path = Path(work_dir) / "csim_invocation.json"
            path.write_text(
                json.dumps(self.invocation),
                encoding="utf-8",
            )
        return self.result


class CsimSuiteEvaluatorTests(unittest.TestCase):
    def test_success_reuses_same_budget_and_legacy_tuple(self) -> None:
        budget = BudgetManager()
        executor = RecordingExecutor(("succeeded", ""))
        evaluator = CsimSuiteEvaluator(executor=executor)
        suite = TestSuiteSpec(
            suite_id="array-map-public",
            split=EvaluationSplit.PUBLIC,
            case_count=4,
        )

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={"candidate": "code"},
                suite=suite,
                timelimit=19,
                budget=budget,
            )

        self.assertEqual(result.to_legacy_result(), ("succeeded", ""))
        self.assertTrue(result.succeeded)
        self.assertEqual(
            result.evidence.status,
            TestEvaluationStatus.PASSED,
        )
        self.assertEqual(result.evidence.passed_cases, 4)
        self.assertEqual(result.evidence.failed_cases, 0)
        self.assertTrue(
            result.evidence.details["case_counts_complete"]
        )
        self.assertIs(executor.calls[0]["budget"], budget)
        self.assertEqual(executor.calls[0]["timelimit"], 19)

    def test_success_without_declared_count_does_not_invent_count(
        self,
    ) -> None:
        evaluator = CsimSuiteEvaluator(
            executor=RecordingExecutor(("succeeded", ""))
        )
        suite = TestSuiteSpec(suite_id="generic-public")

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=suite,
            )

        self.assertEqual(result.evidence.passed_cases, 0)
        self.assertFalse(
            result.evidence.details["case_counts_complete"]
        )

    def test_public_failure_preserves_diagnostic(self) -> None:
        evaluator = CsimSuiteEvaluator(
            executor=RecordingExecutor(
                ("csim_failed", "PUBLIC_MISMATCH_DETAIL")
            )
        )
        suite = TestSuiteSpec(
            suite_id="reduction-public",
            split=EvaluationSplit.PUBLIC,
        )

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=suite,
            )

        self.assertEqual(
            result.evidence.status,
            TestEvaluationStatus.FAILED,
        )
        self.assertEqual(
            result.evidence.details["diagnostic"],
            "PUBLIC_MISMATCH_DETAIL",
        )
        self.assertFalse(result.succeeded)

    def test_hidden_failure_trace_redacts_diagnostic(self) -> None:
        executor = RecordingExecutor(
            ("csim_failed", "SECRET_HIDDEN_DIAGNOSTIC"),
            invocation={
                "compile_execution": {
                    "status": "completed",
                    "returncode": 0,
                    "timeout": False,
                },
                "simulation_execution": {
                    "status": "completed",
                    "returncode": 1,
                    "timeout": False,
                },
            },
        )
        evaluator = CsimSuiteEvaluator(executor=executor)
        suite = TestSuiteSpec(
            suite_id="stencil-hidden",
            split=EvaluationSplit.HIDDEN,
            case_count=8,
            testbench_path="secure/hidden.cpp",
        )

        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            trace = TraceRecorder(
                "hidden-csim",
                output_path=trace_path,
                clock=FixedClock(),
            )
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=suite,
                trace=trace,
            )
            persisted = trace_path.read_text(encoding="utf-8")

        self.assertEqual(
            result.evidence.details["diagnostic"],
            "SECRET_HIDDEN_DIAGNOSTIC",
        )
        self.assertEqual(result.evidence.return_code, 1)
        self.assertIn('"redacted": true', persisted)
        self.assertNotIn("SECRET_HIDDEN_DIAGNOSTIC", persisted)
        self.assertNotIn("hidden.cpp", persisted)
        self.assertNotIn("csim_invocation.json", persisted)

    def test_compile_failure_maps_to_error(self) -> None:
        executor = RecordingExecutor(
            ("tb_compile_failed", "compile error"),
            invocation={
                "compile_execution": {
                    "status": "completed",
                    "returncode": 1,
                    "timeout": False,
                },
                "simulation_execution": {
                    "status": "skipped_after_compile_failure",
                    "returncode": None,
                    "timeout": False,
                },
            },
        )
        evaluator = CsimSuiteEvaluator(executor=executor)
        suite = TestSuiteSpec(suite_id="stream-public")

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=suite,
            )

        self.assertEqual(
            result.evidence.status,
            TestEvaluationStatus.ERROR,
        )
        self.assertEqual(result.evidence.return_code, 1)
        self.assertEqual(
            result.evidence.summary,
            "CSIM testbench compilation failed",
        )

    def test_timeout_is_read_from_invocation(self) -> None:
        executor = RecordingExecutor(
            ("csim_failed", "timeout"),
            invocation={
                "compile_execution": {
                    "status": "completed",
                    "returncode": 0,
                    "timeout": False,
                },
                "simulation_execution": {
                    "status": "completed",
                    "returncode": None,
                    "timeout": True,
                },
            },
        )
        evaluator = CsimSuiteEvaluator(executor=executor)

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=TestSuiteSpec(
                    suite_id="stateful-public"
                ),
            )

        self.assertTrue(result.evidence.timed_out)
        self.assertIsNone(result.evidence.return_code)

    def test_invocation_is_an_operator_artifact(self) -> None:
        executor = RecordingExecutor(
            ("succeeded", ""),
            invocation={
                "compile_execution": {
                    "status": "completed",
                    "returncode": 0,
                    "timeout": False,
                },
                "simulation_execution": {
                    "status": "completed",
                    "returncode": 0,
                    "timeout": False,
                },
            },
        )
        evaluator = CsimSuiteEvaluator(executor=executor)

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=TestSuiteSpec(
                    suite_id="multi-output-public",
                    case_count=2,
                ),
            )
            artifact = result.evidence.artifacts[0]

        self.assertTrue(artifact.endswith("csim_invocation.json"))

    def test_unknown_status_maps_to_error_without_guessing(self) -> None:
        evaluator = CsimSuiteEvaluator(
            executor=RecordingExecutor(
                ("unexpected_status", "unknown")
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            result = evaluator.evaluate(
                work_dir=directory,
                context_variables={},
                suite=TestSuiteSpec(suite_id="generic-public"),
            )

        self.assertEqual(
            result.evidence.status,
            TestEvaluationStatus.ERROR,
        )
        self.assertEqual(
            result.evidence.summary,
            "CSIM evaluation returned an unknown status",
        )

    def test_rejects_invalid_executor_result(self) -> None:
        evaluator = CsimSuiteEvaluator(
            executor=lambda *args, **kwargs: "succeeded"
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                evaluator.evaluate(
                    work_dir=directory,
                    context_variables={},
                    suite=TestSuiteSpec(
                        suite_id="generic-public"
                    ),
                )

    def test_rejects_invalid_suite(self) -> None:
        evaluator = CsimSuiteEvaluator(
            executor=RecordingExecutor(("succeeded", ""))
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                evaluator.evaluate(
                    work_dir=directory,
                    context_variables={},
                    suite={"suite_id": "invalid"},
                )

    def test_schema_is_kernel_agnostic(self) -> None:
        families = (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        )
        for family in families:
            with self.subTest(family=family):
                evaluator = CsimSuiteEvaluator(
                    executor=RecordingExecutor(
                        ("succeeded", "")
                    )
                )
                with tempfile.TemporaryDirectory() as directory:
                    result = evaluator.evaluate(
                        work_dir=directory,
                        context_variables={},
                        suite=TestSuiteSpec(
                            suite_id=f"{family}-public",
                        ),
                    )
                self.assertTrue(result.succeeded)


if __name__ == "__main__":
    unittest.main()
