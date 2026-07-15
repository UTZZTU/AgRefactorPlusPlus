import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
)
from flow.tools import csim, csynth


def make_context() -> ContextVariables:
    return ContextVariables(
        data={
            "orig_code": "int original() { return 0; }\n",
            "curr_code": "int candidate() { return 0; }\n",
            "testbench": "int main() { return 0; }\n",
        }
    )


def command_result(
    returncode: int | None,
    *,
    stdout: str = "",
    stderr: str = "",
    timeout: bool = False,
) -> dict:
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timeout": timeout,
    }


class CsimBudgetTests(unittest.TestCase):
    def read_invocation(self, directory: str) -> dict:
        return json.loads(
            (
                Path(directory) / "csim_invocation.json"
            ).read_text(encoding="utf-8")
        )

    def test_zero_csim_limit_blocks_before_compile(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_compile_calls=10,
                max_csim_calls=0,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
            ) as launch:
                with self.assertRaises(
                    BudgetExceededError
                ) as caught:
                    csim.run_csim(
                        directory,
                        make_context(),
                        budget=budget,
                    )
            invocation = self.read_invocation(directory)

        self.assertEqual(
            caught.exception.resource,
            "csim_calls",
        )
        launch.assert_not_called()
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.compile_calls, 0)
        self.assertEqual(usage.csim_calls, 0)
        self.assertEqual(
            invocation["budget"]["checkpoint"],
            "before_csim_plan",
        )
        self.assertEqual(
            invocation["compile_execution"]["status"],
            "blocked_by_budget",
        )

    def test_total_tool_limit_blocks_full_plan(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
            ) as launch:
                with self.assertRaises(
                    BudgetExceededError
                ) as caught:
                    csim.run_csim(
                        directory,
                        make_context(),
                        budget=budget,
                    )

        self.assertEqual(
            caught.exception.resource,
            "tool_calls",
        )
        launch.assert_not_called()

    def test_success_consumes_compile_and_csim_once(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
                side_effect=[
                    command_result(0),
                    command_result(0),
                ],
            ) as launch:
                result = csim.run_csim(
                    directory,
                    make_context(),
                    budget=budget,
                )
            invocation = self.read_invocation(directory)

        self.assertEqual(result, ("succeeded", ""))
        self.assertEqual(launch.call_count, 2)
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 2)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 1)
        self.assertEqual(
            invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            invocation["compile_execution"]["status"],
            "completed",
        )
        self.assertEqual(
            invocation["simulation_execution"]["status"],
            "completed",
        )

    def test_compile_failure_consumes_only_compile(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
                return_value=command_result(
                    1,
                    stderr="synthetic compile failure",
                ),
            ) as launch:
                result = csim.run_csim(
                    directory,
                    make_context(),
                    budget=budget,
                )
            invocation = self.read_invocation(directory)

        self.assertEqual(
            result,
            (
                "tb_compile_failed",
                "synthetic compile failure",
            ),
        )
        self.assertEqual(launch.call_count, 1)
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 0)
        self.assertEqual(
            invocation["budget"]["status"],
            "partially_consumed",
        )
        self.assertEqual(
            invocation["simulation_execution"]["status"],
            "skipped_after_compile_failure",
        )

    def test_compile_timeout_consumes_only_compile(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
                return_value=command_result(
                    None,
                    stderr="compile timed out",
                    timeout=True,
                ),
            ):
                result = csim.run_csim(
                    directory,
                    make_context(),
                    budget=budget,
                )
            invocation = self.read_invocation(directory)

        self.assertEqual(result[0], "tb_compile_failed")
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 0)
        self.assertTrue(
            invocation["compile_execution"]["timeout"]
        )

    def test_compile_launch_exception_counts_compile(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
                side_effect=OSError(
                    "synthetic compile launch error"
                ),
            ):
                with self.assertRaises(OSError):
                    csim.run_csim(
                        directory,
                        make_context(),
                        budget=budget,
                    )
            invocation = self.read_invocation(directory)

        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 0)
        self.assertEqual(
            invocation["compile_execution"]["status"],
            "launch_error",
        )

    def test_simulation_failure_consumes_both(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
                side_effect=[
                    command_result(0),
                    command_result(
                        1,
                        stdout="mismatch",
                        stderr="simulation failed",
                    ),
                ],
            ):
                result = csim.run_csim(
                    directory,
                    make_context(),
                    budget=budget,
                )

        self.assertEqual(result[0], "csim_failed")
        self.assertIn("mismatch", result[1])
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 2)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 1)

    def test_simulation_launch_exception_counts_both(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csim.tools.general,
                "run_cmd",
                side_effect=[
                    command_result(0),
                    OSError("synthetic csim launch error"),
                ],
            ):
                with self.assertRaises(OSError):
                    csim.run_csim(
                        directory,
                        make_context(),
                        budget=budget,
                    )
            invocation = self.read_invocation(directory)

        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 2)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 1)
        self.assertEqual(
            invocation["simulation_execution"]["status"],
            "launch_error",
        )

    def test_csynth_usage_evidence_has_new_fields(
        self,
    ) -> None:
        usage = BudgetManager().snapshot()
        payload = csynth._budget_usage_to_dict(usage)

        self.assertIn("compile_calls", payload)
        self.assertIn("csim_calls", payload)


if __name__ == "__main__":
    unittest.main()
