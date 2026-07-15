import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from autogen.agentchat.group import ContextVariables

from agrefactor.evidence import (
    TestbenchDiagnostic,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)
from agrefactor.runtime import BudgetLimits, BudgetManager
from agrefactor.testing import TestbenchRepairLoop
from flow.tools import general


def make_context() -> ContextVariables:
    return ContextVariables(
        data={
            "orig_code": "void original() {}\n",
            "curr_code": "void candidate() {}\n",
            "code_for_hetero": "",
            "new_kernel_name": "candidate",
            "testbench": "int main() { return 0; }\n",
            "target_profile": {
                "toolchain_version": "2023.2",
            },
        }
    )


def passed_result() -> TestbenchPreflightResult:
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.PASSED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=TestbenchFailureKind.NONE,
        failure_owner=TestbenchFailureOwner.NONE,
        return_code=0,
        command=("g++",),
    )


def repairable_result() -> TestbenchPreflightResult:
    diagnostic = TestbenchDiagnostic(
        kind=TestbenchFailureKind.SYNTAX_ERROR,
        message="synthetic testbench failure",
        file="testbench.cpp",
    )
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.FAILED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=TestbenchFailureKind.SYNTAX_ERROR,
        failure_owner=TestbenchFailureOwner.TESTBENCH,
        return_code=1,
        command=("g++",),
        diagnostics=(diagnostic,),
        stderr="synthetic testbench failure",
    )


class RecordingPreflight:
    def __init__(self) -> None:
        self.budgets = []
        self.results = [
            repairable_result(),
            passed_result(),
        ]

    def compile_and_link(self, **kwargs):
        self.budgets.append(kwargs.get("budget"))
        return self.results.pop(0)


class Repairer:
    def repair(self, _request):
        return "int main() { return 0; } // repaired"


class LegacyPreflightBudgetPlumbingTests(
    unittest.TestCase
):
    def test_direct_preflight_receives_same_budget(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(max_compile_calls=1)
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general.TestbenchPreflight,
                "compile_and_link",
                return_value=passed_result(),
            ) as compile_and_link:
                general.run_testbench_preflight(
                    directory,
                    make_context(),
                    budget=budget,
                )

        self.assertIs(
            compile_and_link.call_args.kwargs["budget"],
            budget,
        )

    def test_no_repair_gate_forwards_budget(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(max_compile_calls=1)
        )

        with patch.object(
            general,
            "run_testbench_preflight",
            return_value=passed_result(),
        ) as direct:
            general.run_testbench_validation_gate(
                "/tmp/synthetic",
                make_context(),
                budget=budget,
            )

        self.assertIs(
            direct.call_args.kwargs["budget"],
            budget,
        )

    def test_repair_loop_reuses_budget_for_all_preflights(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(max_compile_calls=2)
        )
        preflight = RecordingPreflight()

        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchRepairLoop(
                preflight=preflight,
                repairer=Repairer(),
                max_repair_attempts=1,
            ).run(
                work_dir=directory,
                testbench_code="int main() { broken }",
                original_code="void original() {}",
                candidate_code="void candidate() {}",
                budget=budget,
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            preflight.budgets,
            [budget, budget],
        )

    def test_csynth_and_csim_forwards_budget_to_gate(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(max_compile_calls=1)
        )
        failed = SimpleNamespace(
            succeeded=False,
            stderr="synthetic gate failure",
            diagnostics=(),
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general,
                "run_testbench_validation_gate",
                return_value=failed,
            ) as gate:
                result = general.csynth_and_csim(
                    directory,
                    make_context(),
                    first_time=False,
                    budget=budget,
                )

        self.assertTrue(result[0])
        self.assertEqual(
            result[1],
            "testbench_preflight",
        )
        self.assertIs(
            gate.call_args.kwargs["budget"],
            budget,
        )


if __name__ == "__main__":
    unittest.main()
