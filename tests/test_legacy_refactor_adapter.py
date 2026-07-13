import tempfile
import unittest
from pathlib import Path

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.runtime import (
    BudgetManager,
    PhaseStatus,
    RunContext,
    TraceRecorder,
)


class LegacyRefactorAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        )
        self.task = TaskSpec(
            task_id="dfs-refactor",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top",
            target=self.target,
            mode=RunMode.REFACTOR,
        )

    def make_context(self, task: TaskSpec | None = None) -> RunContext:
        return RunContext(
            run_id="adapter-test",
            task=task or self.task,
            budget=BudgetManager(),
            trace=TraceRecorder("adapter-test"),
        )

    def test_builds_legacy_keyword_arguments(self) -> None:
        settings = LegacyRefactorSettings(
            model="deepseek-v4-flash",
            reasoning_effort="low",
            base_url="https://api.deepseek.com",
            debug=True,
        )

        kwargs = build_legacy_refactor_kwargs(self.task, settings)

        self.assertEqual(kwargs["kernel_name"], "process_top")
        self.assertEqual(kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertEqual(kwargs["debug"], 1)
        self.assertIsNone(kwargs["external_testbench"])

    def test_reads_external_testbench_from_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tb_path = Path(directory) / "tb.cpp"
            tb_path.write_text("int main() { return 0; }\n", encoding="utf-8")
            task = TaskSpec(
                task_id="dfs-external-tb",
                kernel_path=self.task.kernel_path,
                kernel_name=self.task.kernel_name,
                target=self.target,
                mode=RunMode.REFACTOR,
                testbench_path=str(tb_path),
            )

            kwargs = build_legacy_refactor_kwargs(
                task,
                LegacyRefactorSettings(),
            )

            self.assertEqual(
                kwargs["external_testbench"],
                "int main() { return 0; }\n",
            )

    def test_adapter_converts_success_result(self) -> None:
        captured = {}

        def backend(**kwargs):
            captured.update(kwargs)
            return True, {"unused": "payload"}

        adapter = LegacyRefactorAdapter(
            LegacyRefactorSettings(model="deepseek-v4-flash"),
            backend=backend,
        )

        result = adapter(self.make_context())

        self.assertEqual(result.status, PhaseStatus.SUCCEEDED)
        self.assertEqual(captured["kernel_path"], self.task.kernel_path)

    def test_adapter_converts_failure_result(self) -> None:
        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: (False, None)
        )

        result = adapter(self.make_context())

        self.assertEqual(result.status, PhaseStatus.FAILED)

    def test_rejects_invalid_backend_result(self) -> None:
        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: "unexpected"
        )

        with self.assertRaises(TypeError):
            adapter(self.make_context())

    def test_rejects_zero_retry_limit(self) -> None:
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(max_retry_attempts=0)


if __name__ == "__main__":
    unittest.main()
