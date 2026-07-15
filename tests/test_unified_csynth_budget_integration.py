import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.compat import LegacyRefactorAdapter
from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.runtime import (
    BudgetLimits,
    RunPhase,
    RunStatus,
    UnifiedRunner,
)
from flow.tools import csynth


MATCHED_VERIFICATION = {
    "status": "matched",
    "requested": "2023.2",
    "actual": "2023.2",
    "probe_command": "/mock/bin/vitis-run --version",
    "probe_source": "resolved_executable",
    "returncode": 0,
    "stdout": "****** vitis-run v2023.2 (64-bit)\n",
    "stderr": "",
}


def make_task() -> TaskSpec:
    return TaskSpec(
        task_id="unified-csynth-budget",
        kernel_path="src/heterorefactor/dfs/kernel.cpp",
        kernel_name="process_top",
        target=TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        ),
        mode=RunMode.REFACTOR,
    )


def make_csynth_context() -> ContextVariables:
    return ContextVariables(
        data={
            "curr_code": 'extern "C" void process_top_hls() {}\n',
            "new_kernel_name": "process_top_hls",
            "target_profile": {
                "toolchain": "vitis_hls",
                "toolchain_version": "2023.2",
            },
        }
    )


def failed_command_result(*_args, **_kwargs) -> dict:
    return {
        "returncode": 1,
        "stdout": "",
        "stderr": "synthetic csynth failure",
        "timeout": False,
    }


def make_csynth_backend(
    root: Path,
    *,
    attempts: int,
):
    def backend(**kwargs):
        budget = kwargs["budget"]
        cv = make_csynth_context()

        for index in range(attempts):
            work_dir = root / f"attempt_{index + 1}"
            work_dir.mkdir(parents=True, exist_ok=False)
            csynth.run_csynth(
                str(work_dir),
                cv,
                budget=budget,
            )

        return True, cv

    return backend


class UnifiedCsynthBudgetIntegrationTests(unittest.TestCase):
    def test_zero_limit_blocks_before_probe_across_full_chain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "trace.jsonl"
            adapter = LegacyRefactorAdapter(
                backend=make_csynth_backend(
                    root,
                    attempts=1,
                )
            )
            runner = UnifiedRunner(
                {RunPhase.REFACTOR: adapter},
                budget_limits=BudgetLimits(
                    max_tool_calls=10,
                    max_csynth_calls=0,
                ),
            )

            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                ) as probe:
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                    ) as launch:
                        result = runner.run(
                            make_task(),
                            run_id="unified-zero-limit",
                            trace_path=trace_path,
                        )

            invocation = json.loads(
                (
                    root
                    / "attempt_1"
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )
            trace_text = trace_path.read_text(
                encoding="utf-8"
            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(len(result.phases), 1)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "csynth_calls",
        )
        self.assertIn(
            "Budget exceeded for csynth_calls",
            result.phases[0].summary,
        )
        probe.assert_not_called()
        launch.assert_not_called()
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 0)
        self.assertEqual(result.budget_usage.csynth_calls, 0)
        self.assertEqual(
            invocation["budget"]["checkpoint"],
            "before_version_probe",
        )
        self.assertEqual(
            invocation["execution"]["status"],
            "blocked_by_budget",
        )
        self.assertIn('"resource": "csynth_calls"', trace_text)

    def test_limit_one_allows_one_then_blocks_second(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = LegacyRefactorAdapter(
                backend=make_csynth_backend(
                    root,
                    attempts=2,
                )
            )
            runner = UnifiedRunner(
                {RunPhase.REFACTOR: adapter},
                budget_limits=BudgetLimits(
                    max_tool_calls=1,
                    max_csynth_calls=1,
                ),
            )

            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                    return_value=MATCHED_VERIFICATION,
                ) as probe:
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                        side_effect=failed_command_result,
                    ) as launch:
                        result = runner.run(
                            make_task(),
                            run_id="unified-one-limit",
                        )

            first_invocation = json.loads(
                (
                    root
                    / "attempt_1"
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )
            second_invocation = json.loads(
                (
                    root
                    / "attempt_2"
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "tool_calls",
        )
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(launch.call_count, 1)
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 1)
        self.assertEqual(result.budget_usage.csynth_calls, 1)
        self.assertEqual(
            first_invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            first_invocation["execution"]["status"],
            "completed",
        )
        self.assertEqual(
            second_invocation["budget"]["checkpoint"],
            "before_version_probe",
        )
        self.assertEqual(
            second_invocation["execution"]["status"],
            "blocked_by_budget",
        )


if __name__ == "__main__":
    unittest.main()
