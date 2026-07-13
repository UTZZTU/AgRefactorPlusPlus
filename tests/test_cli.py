import io
import json
import tempfile
import unittest
from pathlib import Path

from agrefactor.cli import main


class CliTests(unittest.TestCase):
    def test_validate_task_prints_normalized_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            task_path.write_text(
                json.dumps(
                    {
                        "task_id": "dfs-refactor",
                        "kernel_path": "src/heterorefactor/dfs/kernel.cpp",
                        "kernel_name": "process_top",
                        "mode": "refactor",
                        "target": {
                            "name": "vitis-2023.2-default",
                            "toolchain": "vitis_hls",
                            "toolchain_version": "2023.2",
                        },
                    }
                ),
                encoding="utf-8",
            )
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
            task_path.write_text(
                json.dumps(
                    {
                        "task_id": "dfs-full",
                        "kernel_path": "kernel.cpp",
                        "kernel_name": "process_top",
                        "target": {
                            "name": "default",
                            "toolchain": "vitis_hls",
                        },
                    }
                ),
                encoding="utf-8",
            )
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
            self.assertIn("Task validation failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
