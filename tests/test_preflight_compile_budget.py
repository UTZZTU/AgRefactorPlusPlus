import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agrefactor.evaluation import TestbenchPreflight
from agrefactor.evidence import TestbenchFailureKind
from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
)
from agrefactor.evaluation import testbench_preflight


ORIGINAL = 'extern "C" void original() {}\n'
CANDIDATE = 'extern "C" void candidate() {}\n'
VALID_TB = (
    'extern "C" void original();\n'
    'extern "C" void candidate();\n'
    'int main() { original(); candidate(); return 0; }\n'
)
STATIC_ORIGINAL = "int hidden_state = 0;\n" + ORIGINAL
STATIC_TB = (
    "extern int hidden_state;\n"
    + VALID_TB
)


def completed(
    returncode: int,
    *,
    stdout: str = "",
    stderr: str = "",
):
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class PreflightCompileBudgetTests(unittest.TestCase):
    def read_invocation(self, directory: str) -> dict:
        return json.loads(
            (
                Path(directory)
                / "testbench_preflight_invocation.json"
            ).read_text(encoding="utf-8")
        )

    def run_preflight(
        self,
        directory: str,
        budget: BudgetManager,
    ):
        return TestbenchPreflight().compile_and_link(
            work_dir=directory,
            testbench_code=VALID_TB,
            original_code=ORIGINAL,
            candidate_code=CANDIDATE,
            budget=budget,
        )

    def test_advisory_private_dependency_respects_zero_tool_budget(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=0,
                max_compile_calls=0,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
            ) as launch:
                with self.assertRaises(
                    BudgetExceededError
                ) as caught:
                    TestbenchPreflight().compile_and_link(
                        work_dir=directory,
                        testbench_code=STATIC_TB,
                        original_code=STATIC_ORIGINAL,
                        candidate_code=CANDIDATE,
                        budget=budget,
                    )
            invocation = self.read_invocation(directory)

        self.assertEqual(caught.exception.resource, "tool_calls")
        launch.assert_not_called()
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.compile_calls, 0)
        self.assertEqual(
            invocation["budget"]["status"],
            "blocked",
        )
        self.assertEqual(
            invocation["execution"]["status"],
            "blocked_by_budget",
        )

    def test_zero_compile_limit_blocks_before_launch(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_compile_calls=0,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
            ) as launch:
                with self.assertRaises(
                    BudgetExceededError
                ) as caught:
                    self.run_preflight(directory, budget)
            invocation = self.read_invocation(directory)

        self.assertEqual(
            caught.exception.resource,
            "compile_calls",
        )
        launch.assert_not_called()
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.compile_calls, 0)
        self.assertEqual(
            invocation["execution"]["status"],
            "blocked_by_budget",
        )

    def test_zero_total_tool_limit_blocks_before_launch(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=0,
                max_compile_calls=10,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
            ) as launch:
                with self.assertRaises(
                    BudgetExceededError
                ) as caught:
                    self.run_preflight(directory, budget)

        self.assertEqual(
            caught.exception.resource,
            "tool_calls",
        )
        launch.assert_not_called()

    def test_success_consumes_complete_staged_plan(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=4,
                max_compile_calls=4,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed(0),
            ) as launch:
                result = self.run_preflight(
                    directory,
                    budget,
                )
            invocation = self.read_invocation(directory)

        self.assertTrue(result.succeeded)
        self.assertEqual(launch.call_count, 4)
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 4)
        self.assertEqual(usage.compile_calls, 4)
        self.assertEqual(
            invocation["budget"]["requested_total_increment"],
            {"tool_calls": 4, "compile_calls": 4},
        )
        self.assertEqual(
            invocation["budget"]["status"],
            "consumed",
        )
        self.assertEqual(
            invocation["execution"]["status"],
            "completed",
        )

    def test_compile_failure_consumes_once(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=4,
                max_compile_calls=4,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
                return_value=completed(
                    1,
                    stderr=(
                        "refactor_code.cpp:1:1: "
                        "error: synthetic failure"
                    ),
                ),
            ):
                result = self.run_preflight(
                    directory,
                    budget,
                )

        self.assertFalse(result.succeeded)
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)

    def test_timeout_consumes_once(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=4,
                max_compile_calls=4,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["g++"],
                    timeout=60,
                ),
            ):
                result = self.run_preflight(
                    directory,
                    budget,
                )
            invocation = self.read_invocation(directory)

        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.COMPILE_TIMEOUT,
        )
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(
            invocation["execution"]["status"],
            "timeout",
        )

    def test_missing_compiler_consumes_once(self) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=4,
                max_compile_calls=4,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                testbench_preflight.subprocess,
                "run",
                side_effect=FileNotFoundError(
                    "synthetic compiler missing"
                ),
            ):
                result = self.run_preflight(
                    directory,
                    budget,
                )
            invocation = self.read_invocation(directory)

        self.assertEqual(
            result.failure_kind,
            TestbenchFailureKind.COMPILER_NOT_FOUND,
        )
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(
            invocation["execution"]["status"],
            "launch_error",
        )


if __name__ == "__main__":
    unittest.main()
