import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.runtime import BudgetLimits, BudgetManager
from flow.new import hls_refactor_with_rag
from flow.tools import general


def make_context(
    *,
    hetero_code: str = "",
) -> ContextVariables:
    return ContextVariables(
        data={
            "orig_code": "int original() { return 0; }\n",
            "curr_code": "int candidate() { return 0; }\n",
            "code_for_hetero": hetero_code,
            "new_kernel_name": "candidate",
            "testbench": "int main() { return 0; }\n",
            "target_profile": {
                "toolchain_version": "2023.2",
            },
        }
    )


class LegacyCsimBudgetPlumbingTests(unittest.TestCase):
    def test_main_csim_receives_same_budget(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=4,
                max_compile_calls=2,
                max_csim_calls=2,
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
                ):
                    with patch.object(
                        general.tools.csim,
                        "run_csim",
                        return_value=("succeeded", ""),
                    ) as run_csim:
                        general.csynth_and_csim(
                            directory,
                            make_context(),
                            first_time=False,
                            budget=budget,
                        )

        self.assertEqual(run_csim.call_count, 1)
        self.assertIs(
            run_csim.call_args.kwargs["budget"],
            budget,
        )

    def test_heterogeneous_csim_receives_same_budget(
        self,
    ) -> None:
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=4,
                max_compile_calls=2,
                max_csim_calls=2,
                max_csynth_calls=2,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general.tools.csim,
                "run_csim",
                return_value=("succeeded", ""),
            ) as run_csim:
                result = general.csynth_and_csim(
                    directory,
                    make_context(
                        hetero_code=(
                            "int candidate() { return 0; }\n"
                        )
                    ),
                    first_time=False,
                    budget=budget,
                )

        self.assertTrue(result[0])
        self.assertEqual(run_csim.call_count, 1)
        self.assertIs(
            run_csim.call_args.kwargs["budget"],
            budget,
        )

    def test_remote_compile_or_csim_budget_is_rejected(
        self,
    ) -> None:
        limits = (
            BudgetLimits(max_compile_calls=1),
            BudgetLimits(max_csim_calls=1),
        )

        for budget_limits in limits:
            with self.subTest(limits=budget_limits):
                with self.assertRaises(ValueError) as caught:
                    hls_refactor_with_rag(
                        "/path/not/read.cpp",
                        "top",
                        remote=True,
                        budget=BudgetManager(
                            budget_limits
                        ),
                    )

                self.assertIn(
                    "require local execution",
                    str(caught.exception),
                )


if __name__ == "__main__":
    unittest.main()
