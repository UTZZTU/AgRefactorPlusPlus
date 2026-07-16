import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agrefactor.config import (
    EvaluationSplit,
    TestSuiteSpec,
)
from agrefactor.evidence import (
    TestEvaluationEvidence,
    TestEvaluationStatus,
)
from agrefactor.runtime import (
    TraceEvidenceView,
    TraceRecorder,
)


class FixedClock:
    def __call__(self) -> datetime:
        return datetime(
            2026,
            7,
            16,
            9,
            0,
            0,
            tzinfo=timezone.utc,
        )


class TraceTestEvaluationTests(unittest.TestCase):
    def make_hidden_evidence(self) -> TestEvaluationEvidence:
        return TestEvaluationEvidence(
            suite=TestSuiteSpec(
                suite_id="generic-hidden",
                split=EvaluationSplit.HIDDEN,
                case_count=3,
                testbench_path="secure/hidden.cpp",
            ),
            status=TestEvaluationStatus.FAILED,
            passed_cases=2,
            failed_cases=1,
            return_code=1,
            summary="SECRET_HIDDEN_SUMMARY",
            details={
                "case_id": "SECRET_CASE_ID",
                "input": "SECRET_INPUT",
                "expected": "SECRET_EXPECTED",
                "actual": "SECRET_ACTUAL",
            },
            artifacts=("secure/secret-hidden.log",),
        )

    def make_public_evidence(self) -> TestEvaluationEvidence:
        return TestEvaluationEvidence(
            suite=TestSuiteSpec(
                suite_id="generic-public",
                split=EvaluationSplit.PUBLIC,
                case_count=2,
                testbench_path="tests/public.cpp",
            ),
            status=TestEvaluationStatus.FAILED,
            passed_cases=1,
            failed_cases=1,
            summary="Public case failed",
            details={
                "case_id": "case-2",
                "expected": [1, 2],
                "actual": [2, 1],
            },
            artifacts=("runs/public.log",),
        )

    def test_hidden_default_trace_is_agent_safe(self) -> None:
        trace = TraceRecorder(
            "hidden-safe",
            task_id="generic-task",
            clock=FixedClock(),
        )

        event = trace.record_test_evaluation(
            self.make_hidden_evidence()
        )

        payload = event.metadata["test_evaluation"]
        encoded = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
        )

        self.assertEqual(
            event.metadata["evidence_view"],
            "agent_safe",
        )
        self.assertTrue(payload["redacted"])
        self.assertEqual(payload["details"], {})
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            event.message,
            "Hidden evaluation failed.",
        )
        for secret in (
            "SECRET_HIDDEN_SUMMARY",
            "SECRET_CASE_ID",
            "SECRET_INPUT",
            "SECRET_EXPECTED",
            "SECRET_ACTUAL",
            "hidden.cpp",
            "secret-hidden.log",
        ):
            self.assertNotIn(secret, encoded)

    def test_hidden_jsonl_default_does_not_persist_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = TraceRecorder(
                "hidden-jsonl",
                output_path=path,
                clock=FixedClock(),
            )

            trace.record_test_evaluation(
                self.make_hidden_evidence()
            )

            persisted = path.read_text(encoding="utf-8")

        self.assertIn('"redacted": true', persisted)
        self.assertIn('"split": "hidden"', persisted)
        for secret in (
            "SECRET_HIDDEN_SUMMARY",
            "SECRET_CASE_ID",
            "SECRET_INPUT",
            "SECRET_EXPECTED",
            "SECRET_ACTUAL",
            "hidden.cpp",
            "secret-hidden.log",
        ):
            self.assertNotIn(secret, persisted)

    def test_hidden_snapshot_default_does_not_persist_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            trace = TraceRecorder(
                "hidden-snapshot",
                clock=FixedClock(),
            )
            trace.record_test_evaluation(
                self.make_hidden_evidence()
            )
            trace.write_json(path)
            persisted = path.read_text(encoding="utf-8")

        self.assertIn('"redacted": true', persisted)
        self.assertNotIn("SECRET_INPUT", persisted)
        self.assertNotIn("hidden.cpp", persisted)

    def test_operator_full_requires_explicit_view(self) -> None:
        trace = TraceRecorder(
            "hidden-operator",
            clock=FixedClock(),
        )

        event = trace.record_test_evaluation(
            self.make_hidden_evidence(),
            view=TraceEvidenceView.OPERATOR_FULL,
        )

        payload = event.metadata["test_evaluation"]
        encoded = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
        )

        self.assertEqual(
            event.metadata["evidence_view"],
            "operator_full",
        )
        self.assertFalse(payload["redacted"])
        self.assertEqual(
            event.message,
            "SECRET_HIDDEN_SUMMARY",
        )
        self.assertIn("SECRET_INPUT", encoded)
        self.assertIn("secure/hidden.cpp", encoded)
        self.assertIn("secret-hidden.log", encoded)

    def test_public_agent_safe_view_keeps_public_details(self) -> None:
        trace = TraceRecorder(
            "public-safe",
            clock=FixedClock(),
        )

        event = trace.record_test_evaluation(
            self.make_public_evidence()
        )

        payload = event.metadata["test_evaluation"]

        self.assertFalse(payload["redacted"])
        self.assertEqual(payload["details"]["case_id"], "case-2")
        self.assertEqual(
            payload["suite"]["testbench_path"],
            "tests/public.cpp",
        )
        self.assertEqual(payload["artifacts"], ["runs/public.log"])

    def test_accepts_view_string(self) -> None:
        trace = TraceRecorder(
            "view-string",
            clock=FixedClock(),
        )

        event = trace.record_test_evaluation(
            self.make_hidden_evidence(),
            view="operator_full",
        )

        self.assertEqual(
            event.metadata["evidence_view"],
            "operator_full",
        )

    def test_rejects_unknown_view(self) -> None:
        trace = TraceRecorder(
            "bad-view",
            clock=FixedClock(),
        )

        with self.assertRaises(ValueError):
            trace.record_test_evaluation(
                self.make_hidden_evidence(),
                view="prompt_full",
            )

    def test_rejects_non_evidence_value(self) -> None:
        trace = TraceRecorder(
            "bad-evidence",
            clock=FixedClock(),
        )

        with self.assertRaises(TypeError):
            trace.record_test_evaluation(
                {"status": "failed"},
            )

    def test_records_suite_identity_and_counts(self) -> None:
        trace = TraceRecorder(
            "identity",
            clock=FixedClock(),
        )

        event = trace.record_test_evaluation(
            self.make_hidden_evidence()
        )
        payload = event.metadata["test_evaluation"]

        self.assertEqual(
            event.event,
            "test_evaluation.finished",
        )
        self.assertEqual(payload["suite"]["suite_id"], "generic-hidden")
        self.assertEqual(payload["passed_cases"], 2)
        self.assertEqual(payload["failed_cases"], 1)
        self.assertEqual(payload["evaluated_cases"], 3)


if __name__ == "__main__":
    unittest.main()
