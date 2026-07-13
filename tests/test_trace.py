import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agrefactor.runtime import TraceRecorder


class FixedClock:
    def __call__(self) -> datetime:
        return datetime(2026, 7, 13, 10, 0, 0, tzinfo=timezone.utc)


class TraceRecorderTests(unittest.TestCase):
    def test_records_ordered_events(self) -> None:
        trace = TraceRecorder("run-1", task_id="dfs", clock=FixedClock())

        first = trace.record("run.started", phase="refactor")
        second = trace.record(
            "evaluation.finished",
            phase="validation",
            status="success",
            metadata={"latency": 42},
        )

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.metadata, {"latency": 42})
        self.assertEqual(len(trace.events), 2)

    def test_persists_jsonl_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = TraceRecorder(
                "run-2",
                task_id="dfs",
                output_path=path,
                clock=FixedClock(),
            )

            trace.record("run.started", status="running")

            payload = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["run_id"], "run-2")
            self.assertEqual(payload["task_id"], "dfs")
            self.assertEqual(payload["event"], "run.started")
            self.assertEqual(payload["sequence"], 1)

    def test_writes_complete_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            trace = TraceRecorder("run-3", clock=FixedClock())
            trace.record("run.started")

            trace.write_json(path)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "run-3")
            self.assertEqual(len(payload["events"]), 1)

    def test_rejects_non_serializable_metadata(self) -> None:
        trace = TraceRecorder("run-4", clock=FixedClock())

        with self.assertRaises(TypeError):
            trace.record("bad.metadata", metadata={"value": object()})

    def test_rejects_existing_non_empty_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("existing\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                TraceRecorder("run-5", output_path=path)


if __name__ == "__main__":
    unittest.main()
