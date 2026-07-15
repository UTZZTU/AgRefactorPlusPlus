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

MISMATCH_VERIFICATION = {
    **MATCHED_VERIFICATION,
    "status": "mismatch",
    "actual": "2024.1",
    "stdout": "****** vitis-run v2024.1 (64-bit)\n",
}


def make_context() -> ContextVariables:
    return ContextVariables(
        data={
            "curr_code": 'extern "C" void top_hls() {}\n',
            "new_kernel_name": "top_hls",
            "target_profile": {
                "toolchain_version": "2023.2",
            },
        }
    )


def failed_result(*_args, **_kwargs) -> dict:
    return {
        "returncode": 1,
        "stdout": "",
        "stderr": "synthetic csynth failure",
        "timeout": False,
    }


class CsynthBudgetTests(unittest.TestCase):
    def test_zero_csynth_limit_blocks_probe_and_launch(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_csynth_calls=0,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
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
                        with self.assertRaises(
                            BudgetExceededError
                        ) as caught:
                            csynth.run_csynth(
                                directory,
                                make_context(),
                                budget=budget,
                            )

            invocation = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(caught.exception.resource, "csynth_calls")
        probe.assert_not_called()
        launch.assert_not_called()
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.csynth_calls, 0)
        self.assertEqual(
            invocation["budget"]["checkpoint"],
            "before_version_probe",
        )
        self.assertEqual(
            invocation["execution"]["status"],
            "blocked_by_budget",
        )

    def test_zero_total_tool_limit_also_blocks_probe(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=0,
                max_csynth_calls=10,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
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
                        with self.assertRaises(
                            BudgetExceededError
                        ) as caught:
                            csynth.run_csynth(
                                directory,
                                make_context(),
                                budget=budget,
                            )

        self.assertEqual(caught.exception.resource, "tool_calls")
        probe.assert_not_called()
        launch.assert_not_called()

    def test_limit_one_allows_first_and_blocks_second(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_csynth_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as first_directory:
            with tempfile.TemporaryDirectory() as second_directory:
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
                            side_effect=failed_result,
                        ) as launch:
                            first_result = csynth.run_csynth(
                                first_directory,
                                make_context(),
                                budget=budget,
                            )
                            with self.assertRaises(
                                BudgetExceededError
                            ):
                                csynth.run_csynth(
                                    second_directory,
                                    make_context(),
                                    budget=budget,
                                )

                second_invocation = json.loads(
                    (
                        Path(second_directory)
                        / "csynth_invocation.json"
                    ).read_text(encoding="utf-8")
                )

        self.assertEqual(first_result[0], "csynth_failed")
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(launch.call_count, 1)
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.csynth_calls, 1)
        self.assertEqual(
            second_invocation["execution"]["status"],
            "blocked_by_budget",
        )

    def test_version_mismatch_does_not_consume_csynth(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_csynth_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                    return_value=MISMATCH_VERIFICATION,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                    ) as launch:
                        with self.assertRaises(RuntimeError):
                            csynth.run_csynth(
                                directory,
                                make_context(),
                                budget=budget,
                            )

            invocation = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        launch.assert_not_called()
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.csynth_calls, 0)
        self.assertEqual(
            invocation["budget"]["status"],
            "available",
        )
        self.assertEqual(
            invocation["execution"]["status"],
            "blocked_before_csynth",
        )

    def test_launch_exception_still_counts_real_attempt(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_csynth_calls=1,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                    return_value=MATCHED_VERIFICATION,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                        side_effect=OSError("synthetic launch error"),
                    ):
                        with self.assertRaises(OSError):
                            csynth.run_csynth(
                                directory,
                                make_context(),
                                budget=budget,
                            )

            invocation = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.csynth_calls, 1)
        self.assertEqual(
            invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            invocation["execution"]["status"],
            "launch_error",
        )

    def test_timeout_still_counts_real_attempt(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_csynth_calls=1,
            )
        )
        timeout_result = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timeout": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                    return_value=MATCHED_VERIFICATION,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                        return_value=timeout_result,
                    ):
                        result = csynth.run_csynth(
                            directory,
                            make_context(),
                            budget=budget,
                        )

            invocation = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result[0], "timeout")
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.csynth_calls, 1)
        self.assertTrue(invocation["execution"]["timeout"])


if __name__ == "__main__":
    unittest.main()
