from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agrefactor.config import RunMode, TaskSpec, resolve_target_profile
from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    PhaseResult,
    PhaseStatus,
    RunPhase,
    UnifiedRunner,
)
from agrefactor.runtime.budget_profile import run_budget_profile_for_mode


HARD_FIELDS = (
    "max_llm_calls",
    "max_tool_calls",
    "max_compile_calls",
    "max_csim_calls",
    "max_csynth_calls",
    "max_cosim_calls",
    "max_wall_time_s",
)


def _direct_optimize_args(extra=()):
    from agrefactor.cli import build_parser

    return build_parser().parse_args(
        [
            "optimize",
            "candidate.cpp",
            "--top",
            "candidate_top",
            "--reference-source",
            "reference.cpp",
            "--public-test",
            "public_tb.cpp",
            "--hidden-test",
            "hidden_tb.cpp",
            *extra,
        ]
    )


class P40FFinalTests(unittest.TestCase):
    def test_mode_profiles_are_distinct_and_within_ceilings(self):
        names = []
        for mode in ("refactor", "optimize", "full"):
            profile = run_budget_profile_for_mode(mode)
            names.append(profile.name)
            for field in HARD_FIELDS:
                self.assertLessEqual(
                    getattr(profile.system_defaults, field),
                    getattr(profile.system_safety_ceilings, field),
                )
        self.assertEqual(
            names,
            ["refactor-default", "optimize-default", "full-default"],
        )

    def test_full_reserve_equals_optimize_defaults(self):
        full = run_budget_profile_for_mode("full")
        optimize = run_budget_profile_for_mode("optimize")
        self.assertEqual(
            full.phase_reserves["refactor"],
            optimize.system_defaults,
        )

    def test_full_capacity_can_hold_refactor_plus_optimize_defaults(self):
        refactor = run_budget_profile_for_mode("refactor").system_defaults
        optimize = run_budget_profile_for_mode("optimize").system_defaults
        full = run_budget_profile_for_mode("full").system_defaults
        for field in HARD_FIELDS:
            self.assertGreaterEqual(
                getattr(full, field),
                getattr(refactor, field) + getattr(optimize, field),
            )

    def test_active_reserve_blocks_refactor_from_spending_optimize_capacity(self):
        budget = BudgetManager(BudgetLimits(max_llm_calls=10))
        budget.set_active_reserve(BudgetLimits(max_llm_calls=4))
        budget.consume(llm_calls=6)
        with self.assertRaises(BudgetExceededError):
            budget.consume(llm_calls=1)
        budget.set_active_reserve(None)
        budget.consume(llm_calls=1)
        self.assertEqual(budget.snapshot().llm_calls, 7)

    def test_runner_releases_refactor_reserve_before_optimize(self):
        target = resolve_target_profile("vitis-2023.2-default")
        with tempfile.TemporaryDirectory(prefix="p4f_unittest_") as temp:
            source = Path(temp) / "x.cpp"
            source.write_text("void top(){}\n", encoding="utf-8")
            task = TaskSpec(
                task_id="p4f",
                kernel_path=str(source),
                kernel_name="top",
                target=target,
                mode=RunMode.FULL,
            )

            def refactor(ctx):
                ctx.budget.consume(llm_calls=6)
                return PhaseResult(
                    RunPhase.REFACTOR,
                    PhaseStatus.SUCCEEDED,
                )

            def optimize(ctx):
                ctx.budget.consume(llm_calls=4)
                return PhaseResult(
                    RunPhase.OPTIMIZE,
                    PhaseStatus.SUCCEEDED,
                )

            runner = UnifiedRunner(
                {
                    RunPhase.REFACTOR: refactor,
                    RunPhase.OPTIMIZE: optimize,
                },
                budget_limits=BudgetLimits(max_llm_calls=10),
                phase_reserves={
                    RunPhase.REFACTOR: BudgetLimits(max_llm_calls=4)
                },
            )
            result = runner.run(task)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.budget_usage.llm_calls, 10)

    def test_budget_contract_persists_profile_and_reserve(self):
        payload = run_budget_profile_for_mode("full").resolve().to_dict()
        optimize = run_budget_profile_for_mode("optimize").system_defaults
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["profile_name"], "full-default")
        self.assertEqual(
            payload["phase_reserves"]["refactor"],
            {field: getattr(optimize, field) for field in HARD_FIELDS},
        )

    def test_direct_optimize_default_surface_is_accepted(self):
        from agrefactor.product.source_bootstrap import (
            _validate_mode_specific_cli,
        )

        _validate_mode_specific_cli(_direct_optimize_args())

    def test_direct_optimize_explicit_generation_only_control_is_rejected(self):
        from agrefactor.product.source_bootstrap import (
            _validate_mode_specific_cli,
        )

        options = (
            ("--test-generation-profile", "lightweight"),
            ("--public-coverage-rounds", "3"),
            ("--hidden-coverage-rounds", "6"),
            ("--public-generation-trajectories", "3"),
            ("--hidden-generation-trajectories", "3"),
            ("--max-testbench-repairs", "2"),
            ("--max-candidate-repairs", "2"),
        )
        for option in options:
            with self.subTest(option=option):
                with self.assertRaisesRegex(
                    ValueError,
                    "does not consume",
                ):
                    _validate_mode_specific_cli(
                        _direct_optimize_args(option)
                    )


if __name__ == "__main__":
    unittest.main()
