import inspect
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.compat import LegacyRefactorAdapter
from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    PhaseStatus,
    RunContext,
    TraceRecorder,
)
from flow.new import hls_refactor_with_rag
from flow.tools import general


def make_tool_context(
    *,
    hetero_code: str = "",
) -> ContextVariables:
    return ContextVariables(
        data={
            "orig_code": 'extern "C" void top() {}\n',
            "curr_code": 'extern "C" void top_hls() {}\n',
            "code_for_hetero": hetero_code,
            "new_kernel_name": "top_hls",
            "testbench": "int main() { return 0; }\n",
            "target_profile": {
                "toolchain_version": "2023.2",
            },
        }
    )


class LegacyCsynthBudgetPlumbingTests(unittest.TestCase):
    def test_adapter_forwards_exact_run_context_budget(
        self,
    ) -> None:
        target = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        )
        task = TaskSpec(
            task_id="budget-plumbing",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top",
            target=target,
            mode=RunMode.REFACTOR,
        )
        budget = BudgetManager(
            BudgetLimits(max_csynth_calls=1)
        )
        context = RunContext(
            run_id="budget-plumbing",
            task=task,
            budget=budget,
            trace=TraceRecorder("budget-plumbing"),
        )
        captured = {}

        def backend(**kwargs):
            captured.update(kwargs)
            return True, None

        result = LegacyRefactorAdapter(
            backend=backend,
        )(context)

        self.assertEqual(result.status, PhaseStatus.SUCCEEDED)
        self.assertIs(captured["budget"], budget)

    def test_flow_new_exposes_optional_budget(self) -> None:
        signature = inspect.signature(hls_refactor_with_rag)

        self.assertIn("budget", signature.parameters)
        self.assertIsNone(
            signature.parameters["budget"].default
        )

    def test_flow_new_rejects_non_manager_budget(self) -> None:
        with self.assertRaises(TypeError):
            hls_refactor_with_rag(
                "/path/not/read.cpp",
                "top",
                budget=object(),
            )

    def test_bounded_remote_tool_budget_is_rejected_early(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(max_tool_calls=1)
        )

        with self.assertRaises(ValueError) as caught:
            hls_refactor_with_rag(
                "/path/not/read.cpp",
                "top",
                remote=True,
                budget=budget,
            )

        self.assertIn(
            "require local execution",
            str(caught.exception),
        )

    def test_main_csynth_receives_same_budget_instance(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_csynth_calls=2,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general,
                "run_testbench_validation_gate",
                return_value=SimpleNamespace(succeeded=True),
            ):
                with patch.object(
                    general.tools.csynth,
                    "run_csynth",
                    return_value=("succeeded", ""),
                ) as run_csynth:
                    with patch.object(
                        general.tools.csim,
                        "run_csim",
                        return_value=("succeeded", ""),
                    ):
                        result = general.csynth_and_csim(
                            directory,
                            make_tool_context(),
                            first_time=False,
                            budget=budget,
                        )

        self.assertFalse(result[0])
        self.assertEqual(run_csynth.call_count, 1)
        self.assertIs(
            run_csynth.call_args.kwargs["budget"],
            budget,
        )

    def test_heterogeneous_csynth_receives_same_budget_instance(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=2,
                max_csynth_calls=2,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general.tools.csynth,
                "run_csynth",
                return_value=("succeeded", ""),
            ) as run_csynth:
                with patch.object(
                    general.tools.csim,
                    "run_csim",
                    return_value=("succeeded", ""),
                ):
                    result = general.csynth_and_csim(
                        directory,
                        make_tool_context(
                            hetero_code=(
                                'extern "C" void top_hls() {}\n'
                            )
                        ),
                        first_time=True,
                        budget=budget,
                    )

        self.assertTrue(result[0])
        self.assertEqual(run_csynth.call_count, 1)
        self.assertIs(
            run_csynth.call_args.kwargs["budget"],
            budget,
        )


if __name__ == "__main__":
    unittest.main()
