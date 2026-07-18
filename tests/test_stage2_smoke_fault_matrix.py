from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agrefactor.evaluation import FeedbackRouteAction, ValidationState
from agrefactor.smoke import (
    STAGE2_SMOKE_FAULT_SCENARIOS,
    Stage2SmokeExpectedTerminalState,
    Stage2SmokeFaultExecutionKind,
    Stage2SmokeFaultMatrixRunner,
    Stage2SmokeGroundTruthOwner,
    Stage2SmokeGroundTruthStage,
    Stage2SmokeScenarioKind,
    expected_stage2_smoke_fault_budget,
    get_stage2_smoke_case,
    get_stage2_smoke_fault_scenario,
)


class Stage2SmokeFaultCorpusTests(unittest.TestCase):
    def test_exact_scenario_count_and_order(self):
        self.assertEqual(
            [item.scenario_id for item in STAGE2_SMOKE_FAULT_SCENARIOS],
            [
                "candidate-compile",
                "testbench-compile",
                "original-compile",
                "public-candidate-mismatch",
                "hidden-candidate-mismatch",
                "toolchain-block",
                "unknown-review",
                "mixed-public-review",
                "hidden-unknown-review",
            ],
        )

    def test_execution_split_is_five_real_four_deterministic(self):
        kinds = [item.execution_kind for item in STAGE2_SMOKE_FAULT_SCENARIOS]
        self.assertEqual(
            kinds.count(Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN),
            5,
        )
        self.assertEqual(
            kinds.count(Stage2SmokeFaultExecutionKind.DETERMINISTIC_REPORTS),
            4,
        )

    def test_owner_coverage(self):
        owners = {
            item.ground_truth.ground_truth_owner
            for item in STAGE2_SMOKE_FAULT_SCENARIOS
        }
        self.assertTrue(
            {
                Stage2SmokeGroundTruthOwner.CANDIDATE,
                Stage2SmokeGroundTruthOwner.TESTBENCH,
                Stage2SmokeGroundTruthOwner.ORIGINAL,
                Stage2SmokeGroundTruthOwner.TOOLCHAIN,
                Stage2SmokeGroundTruthOwner.UNKNOWN,
                Stage2SmokeGroundTruthOwner.MIXED,
            }.issubset(owners)
        )

    def test_stage_coverage(self):
        stages = {
            item.ground_truth.ground_truth_stage
            for item in STAGE2_SMOKE_FAULT_SCENARIOS
        }
        self.assertTrue(
            {
                Stage2SmokeGroundTruthStage.COMPILE,
                Stage2SmokeGroundTruthStage.CSYNTH,
                Stage2SmokeGroundTruthStage.PUBLIC_EVALUATION,
                Stage2SmokeGroundTruthStage.HIDDEN_EVALUATION,
            }.issubset(stages)
        )

    def test_terminal_coverage(self):
        terminals = {
            item.ground_truth.expected_terminal_state
            for item in STAGE2_SMOKE_FAULT_SCENARIOS
        }
        self.assertEqual(
            terminals,
            {
                Stage2SmokeExpectedTerminalState.REPAIR_PENDING,
                Stage2SmokeExpectedTerminalState.REJECTED,
                Stage2SmokeExpectedTerminalState.BLOCKED,
                Stage2SmokeExpectedTerminalState.REVIEW_REQUIRED,
            },
        )

    def test_all_labels_are_injected_faults(self):
        for item in STAGE2_SMOKE_FAULT_SCENARIOS:
            self.assertIs(
                item.ground_truth.scenario_kind,
                Stage2SmokeScenarioKind.INJECTED_FAULT,
            )

    def test_real_sources_differ_from_passing_baselines(self):
        for item in STAGE2_SMOKE_FAULT_SCENARIOS[:5]:
            base = get_stage2_smoke_case(item.base_case_id)
            self.assertTrue(
                item.original_code != base.original_code
                or item.candidate_code != base.candidate_code
                or item.preflight_testbench_code
                != base.preflight_testbench_code
            )

    def test_deterministic_scenarios_have_feedback(self):
        for item in STAGE2_SMOKE_FAULT_SCENARIOS[5:]:
            self.assertTrue(item.deterministic_feedback_items)

    def test_agent_safe_manifests_omit_ground_truth_and_hidden(self):
        encoded = json.dumps(
            [item.agent_safe_manifest() for item in STAGE2_SMOKE_FAULT_SCENARIOS],
            sort_keys=True,
        )
        self.assertNotIn("ground_truth", encoded)
        self.assertNotIn("hidden_testbench", encoded)
        self.assertNotIn("HIDDEN_FAULT_UNKNOWN", encoded)

    def test_operator_manifests_are_serializable(self):
        encoded = json.dumps(
            [item.operator_manifest() for item in STAGE2_SMOKE_FAULT_SCENARIOS],
            sort_keys=True,
        )
        self.assertIn("ground_truth", encoded)
        self.assertIn("candidate-compile", encoded)

    def test_lookup_is_exact(self):
        self.assertEqual(
            get_stage2_smoke_fault_scenario(
                "public-candidate-mismatch"
            ).base_case_id,
            "reduction",
        )
        with self.assertRaises(KeyError):
            get_stage2_smoke_fault_scenario("missing")

    def test_expected_budget_is_exact(self):
        self.assertEqual(
            expected_stage2_smoke_fault_budget().to_dict(),
            {
                "tool_calls": 13,
                "compile_calls": 8,
                "csynth_calls": 2,
                "csim_calls": 3,
                "llm_calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
            },
        )


class Stage2SmokeFaultRunnerTests(unittest.TestCase):
    def _run_deterministic(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "matrix"
        selected = STAGE2_SMOKE_FAULT_SCENARIOS[5:]
        result = Stage2SmokeFaultMatrixRunner(root).run(
            selected,
            matrix_id="deterministic-matrix",
        )
        return root, selected, result

    def test_deterministic_subset_matches(self):
        _, selected, result = self._run_deterministic()
        self.assertTrue(result.matched)
        self.assertEqual(len(result.observations), len(selected))

    def test_observed_routes_match_labels(self):
        _, selected, result = self._run_deterministic()
        self.assertEqual(
            [item.observed_route_action for item in result.observations],
            [item.expected_route_action for item in selected],
        )

    def test_observed_finals_match_labels(self):
        _, selected, result = self._run_deterministic()
        self.assertEqual(
            [item.observed_final_state for item in result.observations],
            [item.expected_final_state for item in selected],
        )

    def test_deterministic_subset_consumes_zero_budget(self):
        _, _, result = self._run_deterministic()
        self.assertEqual(result.total_usage.tool_calls, 0)
        self.assertEqual(result.total_usage.compile_calls, 0)
        self.assertEqual(result.total_usage.csynth_calls, 0)
        self.assertEqual(result.total_usage.csim_calls, 0)

    def test_hidden_secret_is_absent_from_safe_result(self):
        _, selected, result = self._run_deterministic()
        encoded = json.dumps(result.to_dict(), sort_keys=True)
        self.assertNotIn(selected[-1].deterministic_secret_marker, encoded)
        self.assertNotIn("ground_truth", encoded)

    def test_trace_files_are_written(self):
        root, selected, _ = self._run_deterministic()
        self.assertEqual(
            len(tuple((root / "traces").glob("*.jsonl"))),
            len(selected),
        )
        self.assertEqual(
            len(tuple((root / "traces").glob("*.json"))),
            len(selected),
        )

    def test_duplicate_scenarios_are_rejected(self):
        scenario = STAGE2_SMOKE_FAULT_SCENARIOS[5]
        with TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                Stage2SmokeFaultMatrixRunner(
                    Path(temporary) / "matrix"
                ).run((scenario, scenario))

    def test_nonempty_root_is_rejected(self):
        root, selected, _ = self._run_deterministic()
        with self.assertRaises(FileExistsError):
            Stage2SmokeFaultMatrixRunner(root).run(selected)


if __name__ == "__main__":
    unittest.main()
