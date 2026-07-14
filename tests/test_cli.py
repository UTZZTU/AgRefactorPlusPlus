import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agrefactor.cli import main
from agrefactor.runtime import (
    PhaseResult,
    PhaseStatus,
    RunPhase,
)


def write_task(path: Path, *, mode: str = "refactor") -> None:
    path.write_text(
        json.dumps(
            {
                "task_id": f"dfs-{mode}",
                "kernel_path": "src/heterorefactor/dfs/kernel.cpp",
                "kernel_name": "process_top",
                "mode": mode,
                "target": {
                    "name": "vitis-2023.2-default",
                    "toolchain": "vitis_hls",
                    "toolchain_version": "2023.2",
                },
            }
        ),
        encoding="utf-8",
    )


class CliTests(unittest.TestCase):
    def test_validate_task_prints_normalized_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            write_task(task_path)
            stdout = io.StringIO()
            stderr = io.StringIO()

            exit_code = main(
                ["validate-task", str(task_path)],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["mode"], "refactor")
            self.assertEqual(payload["kernel_name"], "process_top")
            self.assertEqual(payload["target"]["clock_period_ns"], 10.0)

    def test_validate_task_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_path = root / "task.json"
            output_path = root / "normalized" / "task.json"
            write_task(task_path, mode="full")
            stdout = io.StringIO()

            exit_code = main(
                [
                    "validate-task",
                    str(task_path),
                    "--output",
                    str(output_path),
                ],
                stdout=stdout,
                stderr=io.StringIO(),
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.is_file())
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "full")

    def test_invalid_task_returns_exit_code_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "bad.json"
            task_path.write_text(
                '{"task_id": "missing-required-fields"}',
                encoding="utf-8",
            )
            stderr = io.StringIO()

            exit_code = main(
                ["validate-task", str(task_path)],
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("Command failed", stderr.getvalue())

    def test_dry_run_refactor_uses_unified_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            write_task(task_path, mode="refactor")
            stdout = io.StringIO()
            stderr = io.StringIO()

            exit_code = main(
                [
                    "run",
                    str(task_path),
                    "--dry-run",
                    "--run-id",
                    "cli-refactor",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["succeeded"])
            self.assertEqual(payload["run_id"], "cli-refactor")
            self.assertEqual(
                [item["phase"] for item in payload["phases"]],
                ["refactor"],
            )

    def test_dry_run_full_writes_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_path = root / "task.json"
            trace_path = root / "trace.jsonl"
            write_task(task_path, mode="full")
            stdout = io.StringIO()

            exit_code = main(
                [
                    "run",
                    str(task_path),
                    "--dry-run",
                    "--run-id",
                    "cli-full",
                    "--trace",
                    str(trace_path),
                ],
                stdout=stdout,
                stderr=io.StringIO(),
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                [item["phase"] for item in payload["phases"]],
                ["refactor", "optimize"],
            )
            events = [
                json.loads(line)["event"]
                for line in trace_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(events[0], "run.started")
            self.assertEqual(events[-1], "run.finished")
            self.assertEqual(events.count("dry_run.checked"), 2)

    def test_run_without_execution_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            write_task(task_path)
            stderr = io.StringIO()

            exit_code = main(
                ["run", str(task_path)],
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "Choose exactly one execution mode",
                stderr.getvalue(),
            )

    def test_legacy_refactor_uses_adapter_without_real_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            write_task(task_path, mode="refactor")
            stdout = io.StringIO()
            stderr = io.StringIO()

            def fake_handler(context):
                return PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.SUCCEEDED,
                    metadata={"fake": True},
                )

            with patch(
                "agrefactor.cli.LegacyRefactorAdapter",
                return_value=fake_handler,
            ) as adapter_class:
                exit_code = main(
                    [
                        "run",
                        str(task_path),
                        "--legacy",
                        "--run-id",
                        "legacy-test",
                        "--model",
                        "deepseek-v4-flash",
                        "--base-url",
                        "https://api.deepseek.com",
                        "--reasoning-effort",
                        "low",
                        "--max-retry-attempts",
                        "3",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["succeeded"])
            self.assertEqual(payload["run_id"], "legacy-test")
            settings = adapter_class.call_args.args[0]
            self.assertEqual(settings.model, "deepseek-v4-flash")
            self.assertEqual(settings.max_retry_attempts, 3)

    def test_legacy_rejects_full_mode_until_optimizer_adapter_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            write_task(task_path, mode="full")
            stderr = io.StringIO()

            exit_code = main(
                ["run", str(task_path), "--legacy"],
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn(
                "currently supports only mode='refactor'",
                stderr.getvalue(),
            )


if __name__ == "__main__":
    unittest.main()
