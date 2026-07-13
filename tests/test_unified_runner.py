import tempfile
import unittest
from pathlib import Path

from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.runtime import (
    BudgetLimits,
    PhaseResult,
    PhaseStatus,
    RunPhase,
    RunStatus,
    UnifiedRunner,
)


class UnifiedRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        )

    def make_task(self, mode: RunMode) -> TaskSpec:
        return TaskSpec(
            task_id=f"dfs-{mode.value}",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top",
            target=self.target,
            mode=mode,
        )

    def test_refactor_mode_runs_only_refactor(self) -> None:
        called: list[str] = []

        def refactor(context):
            called.append("refactor")
            context.budget.consume(tool_calls=1)
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=PhaseStatus.SUCCEEDED,
            )

        runner = UnifiedRunner({RunPhase.REFACTOR: refactor})
        result = runner.run(
            self.make_task(RunMode.REFACTOR),
            run_id="run-refactor",
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(called, ["refactor"])
        self.assertEqual(result.budget_usage.tool_calls, 1)

    def test_full_mode_runs_refactor_then_optimize(self) -> None:
        called: list[str] = []

        def refactor(context):
            called.append("refactor")
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=PhaseStatus.SUCCEEDED,
            )

        def optimize(context):
            called.append("optimize")
            return PhaseResult(
                phase=RunPhase.OPTIMIZE,
                status=PhaseStatus.SUCCEEDED,
            )

        runner = UnifiedRunner(
            {
                RunPhase.REFACTOR: refactor,
                RunPhase.OPTIMIZE: optimize,
            }
        )
        result = runner.run(
            self.make_task(RunMode.FULL),
            run_id="run-full",
        )

        self.assertEqual(called, ["refactor", "optimize"])
        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(len(result.phases), 2)

    def test_full_mode_stops_after_refactor_failure(self) -> None:
        called: list[str] = []

        def refactor(context):
            called.append("refactor")
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=PhaseStatus.FAILED,
                summary="validation failed",
            )

        def optimize(context):
            called.append("optimize")
            return PhaseResult(
                phase=RunPhase.OPTIMIZE,
                status=PhaseStatus.SUCCEEDED,
            )

        runner = UnifiedRunner(
            {
                RunPhase.REFACTOR: refactor,
                RunPhase.OPTIMIZE: optimize,
            }
        )
        result = runner.run(
            self.make_task(RunMode.FULL),
            run_id="run-failed",
        )

        self.assertEqual(called, ["refactor"])
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(len(result.phases), 1)

    def test_missing_handler_returns_error(self) -> None:
        runner = UnifiedRunner({})

        result = runner.run(
            self.make_task(RunMode.OPTIMIZE),
            run_id="run-missing",
        )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertIn("No handler registered", result.phases[0].summary)

    def test_writes_structured_trace(self) -> None:
        def refactor(context):
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=PhaseStatus.SUCCEEDED,
                metadata={"candidate": 1},
            )

        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            runner = UnifiedRunner(
                {RunPhase.REFACTOR: refactor},
                budget_limits=BudgetLimits(max_tool_calls=5),
            )

            result = runner.run(
                self.make_task(RunMode.REFACTOR),
                run_id="run-trace",
                trace_path=trace_path,
            )

            lines = trace_path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(result.succeeded)
            self.assertEqual(len(lines), 4)
            self.assertIn('"event": "run.started"', lines[0])
            self.assertIn('"event": "run.finished"', lines[-1])


if __name__ == "__main__":
    unittest.main()
