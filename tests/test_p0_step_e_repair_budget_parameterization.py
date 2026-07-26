from __future__ import annotations

import inspect
import unittest

from agrefactor.cli import build_parser
from agrefactor.compat import LegacyRefactorSettings
from agrefactor.config import (
    DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
    DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
    MIN_REPAIR_ATTEMPTS,
    REPAIR_ATTEMPT_SAFETY_CEILING,
    validate_repair_attempts,
)
from agrefactor.product import source_bootstrap
from agrefactor.repair import (
    BoundedCandidateRepairLoop,
    CandidateRepairLoopRequest,
)
from agrefactor.runtime import CandidateRepairOrchestrationRequest
from agrefactor.testing import (
    TestbenchRepairLoop,
    TestbenchRepairRequest,
)


class RepairBudgetContractTests(unittest.TestCase):
    def test_shared_constants_match_frozen_contract(self):
        self.assertEqual(MIN_REPAIR_ATTEMPTS, 1)
        self.assertEqual(DEFAULT_TESTBENCH_REPAIR_ATTEMPTS, 3)
        self.assertEqual(DEFAULT_CANDIDATE_REPAIR_ATTEMPTS, 3)
        self.assertEqual(REPAIR_ATTEMPT_SAFETY_CEILING, 20)

    def test_shared_validator_accepts_boundaries(self):
        self.assertEqual(
            validate_repair_attempts(1, field_name="repairs"),
            1,
        )
        self.assertEqual(
            validate_repair_attempts(20, field_name="repairs"),
            20,
        )

    def test_shared_validator_rejects_zero_and_above_ceiling(self):
        with self.assertRaises(ValueError):
            validate_repair_attempts(0, field_name="repairs")
        with self.assertRaises(ValueError):
            validate_repair_attempts(21, field_name="repairs")

    def test_normal_source_cli_defaults_to_three_each(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "process_top",
                "--model",
                "model-id",
            ]
        )
        self.assertEqual(args.max_testbench_repairs, 3)
        self.assertEqual(args.max_candidate_repairs, 3)

    def test_normal_source_cli_accepts_upper_boundary(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "process_top",
                "--model",
                "model-id",
                "--max-testbench-repairs",
                "20",
                "--max-candidate-repairs",
                "20",
            ]
        )
        self.assertEqual(args.max_testbench_repairs, 20)
        self.assertEqual(args.max_candidate_repairs, 20)

    def test_normal_source_cli_rejects_zero_before_execution(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "refactor",
                    "kernel.cpp",
                    "--top",
                    "process_top",
                    "--model",
                    "model-id",
                    "--max-testbench-repairs",
                    "0",
                ]
            )

    def test_normal_source_cli_rejects_above_ceiling_before_execution(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "refactor",
                    "kernel.cpp",
                    "--top",
                    "process_top",
                    "--model",
                    "model-id",
                    "--max-candidate-repairs",
                    "21",
                ]
            )

    def test_advanced_entrypoints_share_default_three(self):
        parser = build_parser()
        repair_aware = parser.parse_args(
            ["run", "task.json", "--repair-aware"]
        )
        legacy = parser.parse_args(
            ["run", "task.json", "--legacy"]
        )
        self.assertEqual(
            repair_aware.max_candidate_repair_attempts,
            3,
        )
        self.assertEqual(
            legacy.max_testbench_repair_attempts,
            3,
        )

    def test_legacy_settings_share_default_and_ceiling(self):
        self.assertEqual(
            LegacyRefactorSettings().max_testbench_repair_attempts,
            3,
        )
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(
                max_testbench_repair_attempts=21
            )

    def test_candidate_orchestration_default_is_three(self):
        request = CandidateRepairOrchestrationRequest(
            initial_candidate="void top_hls() {}",
            original_code="void top() {}",
            preflight_testbench_code=(
                "void top(); void top_hls(); int main(){"
                "top(); top_hls(); return 0;}"
            ),
            suite_testbench_codes={
                "public": (
                    "void top(); void top_hls(); int main(){"
                    "top(); top_hls(); return 0;}"
                )
            },
            prompt_public_testbench_code=(
                "void top(); void top_hls(); int main(){"
                "top(); top_hls(); return 0;}"
            ),
        )
        self.assertEqual(request.max_attempts, 3)

    def test_candidate_orchestration_rejects_above_ceiling(self):
        with self.assertRaises(ValueError):
            CandidateRepairOrchestrationRequest(
                initial_candidate="void top_hls() {}",
                original_code="void top() {}",
                preflight_testbench_code=(
                    "void top(); void top_hls(); int main(){"
                    "top(); top_hls(); return 0;}"
                ),
                suite_testbench_codes={
                    "public": (
                        "void top(); void top_hls(); int main(){"
                        "top(); top_hls(); return 0;}"
                    )
                },
                prompt_public_testbench_code=None,
                max_attempts=21,
            )

    def test_candidate_loop_request_default_is_three(self):
        field = CandidateRepairLoopRequest.__dataclass_fields__[
            "max_attempts"
        ]
        self.assertEqual(field.default, 3)

    def test_testbench_loop_default_is_three(self):
        default = inspect.signature(
            TestbenchRepairLoop
        ).parameters["max_repair_attempts"].default
        self.assertEqual(default, 3)

    def test_testbench_loop_rejects_above_ceiling(self):
        with self.assertRaises(ValueError):
            TestbenchRepairLoop(
                preflight=object(),
                repairer=object(),
                max_repair_attempts=21,
            )

    def test_testbench_request_rejects_above_ceiling(self):
        with self.assertRaises(ValueError):
            TestbenchRepairRequest(
                attempt=1,
                max_attempts=21,
                current_testbench="int main(){return 0;}",
                original_code="void top(){}",
                candidate_code="void top_hls(){}",
                preflight=object(),
            )

    def test_source_bootstrap_has_no_fixed_two_attempt_budget(self):
        source = inspect.getsource(
            source_bootstrap.SourceBootstrapPhase.__call__
        )
        self.assertNotIn('"max_repair_attempts": 2', source)
        self.assertNotIn("max_repair_attempts=2", source)
        field = source_bootstrap.SourceBootstrapRequest.__dataclass_fields__[
            "max_testbench_repairs"
        ]
        self.assertEqual(field.default, 3)

    def test_both_loops_forward_all_prior_safe_summaries(self):
        testbench_source = inspect.getsource(
            TestbenchRepairLoop.run
        )
        candidate_source = inspect.getsource(
            BoundedCandidateRepairLoop.run
        )
        self.assertIn(
            "prior_attempt_summaries=tuple(",
            testbench_source,
        )
        self.assertIn(
            "prior_summaries=tuple(prior_summaries)",
            candidate_source,
        )

    def test_no_no_progress_early_stop_is_added(self):
        testbench_source = inspect.getsource(
            TestbenchRepairLoop.run
        ).casefold()
        candidate_source = inspect.getsource(
            BoundedCandidateRepairLoop.run
        ).casefold()
        self.assertNotIn("no_progress", testbench_source)
        self.assertNotIn("no_progress", candidate_source)
        self.assertIn(
            "self._max_repair_attempts + 1",
            testbench_source,
        )
        self.assertIn(
            "request.max_attempts + 1",
            candidate_source,
        )


if __name__ == "__main__":
    unittest.main()
