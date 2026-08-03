import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.compat import LegacyRefactorAdapter
from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.evaluation import testbench_preflight
from agrefactor.runtime import (
    BudgetLimits,
    RunPhase,
    RunStatus,
    UnifiedRunner,
)
from flow.tools import csynth, general


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
        task_id="unified-full-tool-budget",
        kernel_path="src/heterorefactor/dfs/kernel.cpp",
        kernel_name="process_top",
        target=TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        ),
        mode=RunMode.REFACTOR,
    )


def make_tool_context() -> ContextVariables:
    return ContextVariables(
        data={
            "orig_code": (
                'extern "C" void process_top(int *out) '
                "{ out[0] = 1; }\n"
            ),
            "curr_code": (
                'extern "C" void process_top_hls(int *out) '
                "{ out[0] = 1; }\n"
            ),
            "code_for_hetero": "",
            "new_kernel_name": "process_top_hls",
            "testbench": (
                'extern "C" void process_top(int *);\n'
                'extern "C" void process_top_hls(int *);\n'
                "int main() {\n"
                "    int original[1] = {};\n"
                "    int candidate[1] = {};\n"
                "    process_top(original);\n"
                "    process_top_hls(candidate);\n"
                "    return original[0] != candidate[0];\n"
                "}\n"
            ),
            "target_profile": {
                "toolchain": "vitis_hls",
                "toolchain_version": "2023.2",
            },
        }
    )


def completed_preflight():
    return SimpleNamespace(
        returncode=0,
        stdout="",
        stderr="",
    )


def success_result() -> dict:
    return {
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "timeout": False,
    }


class SuccessfulToolRunner:
    def __init__(self) -> None:
        self.commands = []

    def __call__(
        self,
        work_dir,
        command,
        _timelimit,
    ) -> dict:
        self.commands.append(command)

        work_path = Path(work_dir)
        if work_path.name.startswith("csynth_"):
            report_dir = (
                work_path
                / "csynth"
                / "solution"
                / "syn"
                / "report"
            )
            report_dir.mkdir(parents=True, exist_ok=True)
            (
                report_dir
                / "process_top_hls_csynth.rpt"
            ).write_text(
                "Synthetic successful csynth report\n",
                encoding="utf-8",
            )

        return success_result()


def make_backend(root: Path):
    def backend(**kwargs):
        cv = make_tool_context()
        result = general.csynth_and_csim(
            str(root),
            cv,
            first_time=False,
            budget=kwargs["budget"],
        )
        (
            kill_other,
            _first_task,
            first_result,
            _second_task,
            second_result,
        ) = result
        succeeded = (
            not kill_other
            and first_result[0] == "succeeded"
            and second_result is not None
            and second_result[0] == "succeeded"
        )
        return succeeded, cv

    return backend


def load_only_invocation(
    root: Path,
    directory_pattern: str,
    filename: str,
) -> dict:
    matches = sorted(root.glob(directory_pattern))
    if len(matches) != 1:
        raise AssertionError(
            f"Expected one {directory_pattern} directory, "
            f"found {matches!r}"
        )
    return json.loads(
        (matches[0] / filename).read_text(encoding="utf-8")
    )


class UnifiedFullToolBudgetIntegrationTests(
    unittest.TestCase
):
    def run_with_limits(
        self,
        root: Path,
        limits: BudgetLimits,
    ):
        runner = UnifiedRunner(
            {
                RunPhase.REFACTOR: LegacyRefactorAdapter(
                    backend=make_backend(root)
                )
            },
            budget_limits=limits,
        )
        return runner.run(
            make_task(),
            run_id="unified-full-tool-budget",
        )

    def test_zero_compile_limit_blocks_before_all_tools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_runner = SuccessfulToolRunner()

            with patch.object(
                testbench_preflight.subprocess,
                "run",
            ) as preflight_launch:
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
                            general,
                            "run_cmd",
                            side_effect=tool_runner,
                        ) as tool_launch:
                            result = self.run_with_limits(
                                root,
                                BudgetLimits(
                                    max_tool_calls=10,
                                    max_compile_calls=0,
                                    max_csynth_calls=1,
                                    max_csim_calls=1,
                                ),
                            )

            preflight_invocation = load_only_invocation(
                root,
                "testbench_preflight_*",
                "testbench_preflight_invocation.json",
            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "compile_calls",
        )
        preflight_launch.assert_not_called()
        probe.assert_not_called()
        tool_launch.assert_not_called()
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 0)
        self.assertEqual(result.budget_usage.compile_calls, 0)
        self.assertEqual(result.budget_usage.csynth_calls, 0)
        self.assertEqual(result.budget_usage.csim_calls, 0)
        self.assertEqual(
            preflight_invocation["execution"]["status"],
            "blocked_by_budget",
        )

    def test_zero_csynth_limit_blocks_after_preflight(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_runner = SuccessfulToolRunner()

            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed_preflight(),
            ) as preflight_launch:
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
                            general,
                            "run_cmd",
                            side_effect=tool_runner,
                        ) as tool_launch:
                            result = self.run_with_limits(
                                root,
                                BudgetLimits(
                                    max_tool_calls=10,
                                    max_compile_calls=4,
                                    max_csynth_calls=0,
                                    max_csim_calls=1,
                                ),
                            )

            csynth_invocation = load_only_invocation(
                root,
                "csynth_*",
                "csynth_invocation.json",
            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "csynth_calls",
        )
        self.assertEqual(preflight_launch.call_count, 4)
        probe.assert_not_called()
        tool_launch.assert_not_called()
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 4)
        self.assertEqual(result.budget_usage.compile_calls, 4)
        self.assertEqual(result.budget_usage.csynth_calls, 0)
        self.assertEqual(result.budget_usage.csim_calls, 0)
        self.assertEqual(
            csynth_invocation["budget"]["checkpoint"],
            "before_version_probe",
        )
        self.assertEqual(
            csynth_invocation["execution"]["status"],
            "blocked_by_budget",
        )

    def test_total_limit_four_blocks_csynth_after_preflight(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_runner = SuccessfulToolRunner()

            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed_preflight(),
            ) as preflight_launch:
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
                            general,
                            "run_cmd",
                            side_effect=tool_runner,
                        ) as tool_launch:
                            result = self.run_with_limits(
                                root,
                                BudgetLimits(
                                    max_tool_calls=4,
                                    max_compile_calls=4,
                                    max_csynth_calls=1,
                                    max_csim_calls=1,
                                ),
                            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "tool_calls",
        )
        self.assertEqual(preflight_launch.call_count, 4)
        probe.assert_not_called()
        tool_launch.assert_not_called()
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 4)
        self.assertEqual(result.budget_usage.compile_calls, 4)
        self.assertEqual(result.budget_usage.csynth_calls, 0)
        self.assertEqual(result.budget_usage.csim_calls, 0)

    def test_shared_compile_limit_blocks_csim_after_csynth(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_runner = SuccessfulToolRunner()

            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed_preflight(),
            ) as preflight_launch:
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
                            general,
                            "run_cmd",
                            side_effect=tool_runner,
                        ) as tool_launch:
                            result = self.run_with_limits(
                                root,
                                BudgetLimits(
                                    max_tool_calls=7,
                                    max_compile_calls=4,
                                    max_csynth_calls=1,
                                    max_csim_calls=1,
                                ),
                            )

            csynth_invocation = load_only_invocation(
                root,
                "csynth_*",
                "csynth_invocation.json",
            )
            csim_invocation = load_only_invocation(
                root,
                "csim_*",
                "csim_invocation.json",
            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "compile_calls",
        )
        self.assertEqual(preflight_launch.call_count, 4)
        probe.assert_called_once()
        self.assertEqual(tool_launch.call_count, 1)
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 5)
        self.assertEqual(result.budget_usage.compile_calls, 4)
        self.assertEqual(result.budget_usage.csynth_calls, 1)
        self.assertEqual(result.budget_usage.csim_calls, 0)
        self.assertEqual(
            csynth_invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            csim_invocation["budget"]["checkpoint"],
            "before_csim_plan",
        )
        self.assertEqual(
            csim_invocation["compile_execution"]["status"],
            "blocked_by_budget",
        )

    def test_total_limit_six_blocks_full_csim_plan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_runner = SuccessfulToolRunner()

            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed_preflight(),
            ) as preflight_launch:
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
                            general,
                            "run_cmd",
                            side_effect=tool_runner,
                        ) as tool_launch:
                            result = self.run_with_limits(
                                root,
                                BudgetLimits(
                                    max_tool_calls=6,
                                    max_compile_calls=5,
                                    max_csynth_calls=1,
                                    max_csim_calls=1,
                                ),
                            )

            csim_invocation = load_only_invocation(
                root,
                "csim_*",
                "csim_invocation.json",
            )

        self.assertEqual(result.status, RunStatus.ERROR)
        self.assertEqual(
            result.phases[0].metadata["resource"],
            "tool_calls",
        )
        self.assertEqual(preflight_launch.call_count, 4)
        probe.assert_called_once()
        self.assertEqual(tool_launch.call_count, 1)
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 5)
        self.assertEqual(result.budget_usage.compile_calls, 4)
        self.assertEqual(result.budget_usage.csynth_calls, 1)
        self.assertEqual(result.budget_usage.csim_calls, 0)
        self.assertEqual(
            csim_invocation["budget"]["checkpoint"],
            "before_csim_plan",
        )
        self.assertEqual(
            csim_invocation["compile_execution"]["status"],
            "blocked_by_budget",
        )

    def test_exact_budget_allows_complete_toolchain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_runner = SuccessfulToolRunner()

            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed_preflight(),
            ) as preflight_launch:
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
                            general,
                            "run_cmd",
                            side_effect=tool_runner,
                        ) as tool_launch:
                            result = self.run_with_limits(
                                root,
                                BudgetLimits(
                                    max_tool_calls=7,
                                    max_compile_calls=5,
                                    max_csynth_calls=1,
                                    max_csim_calls=1,
                                ),
                            )

            preflight_invocation = load_only_invocation(
                root,
                "testbench_preflight_*",
                "testbench_preflight_invocation.json",
            )
            csynth_invocation = load_only_invocation(
                root,
                "csynth_*",
                "csynth_invocation.json",
            )
            csim_invocation = load_only_invocation(
                root,
                "csim_*",
                "csim_invocation.json",
            )

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(preflight_launch.call_count, 4)
        probe.assert_called_once()
        self.assertEqual(tool_launch.call_count, 3)
        self.assertIsNotNone(result.budget_usage)
        self.assertEqual(result.budget_usage.tool_calls, 7)
        self.assertEqual(result.budget_usage.compile_calls, 5)
        self.assertEqual(result.budget_usage.csynth_calls, 1)
        self.assertEqual(result.budget_usage.csim_calls, 1)
        self.assertEqual(
            preflight_invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            csynth_invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            csynth_invocation["execution"]["status"],
            "completed",
        )
        self.assertEqual(
            csim_invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            csim_invocation["compile_execution"]["status"],
            "completed",
        )
        self.assertEqual(
            csim_invocation["simulation_execution"]["status"],
            "completed",
        )


if __name__ == "__main__":
    unittest.main()
