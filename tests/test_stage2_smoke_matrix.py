import json
import re
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TargetProfile,
)
from agrefactor.models import CandidateResponseContract
from agrefactor.smoke import (
    STAGE2_SMOKE_CASES,
    Stage2SmokeBudgetExpectation,
    Stage2SmokeCase,
    Stage2SmokeExpectedRoute,
    Stage2SmokeExpectedTerminalState,
    Stage2SmokeGroundTruth,
    Stage2SmokeGroundTruthOwner,
    Stage2SmokeGroundTruthStage,
    Stage2SmokeHiddenVisibility,
    Stage2SmokeKernelType,
    Stage2SmokeScenarioKind,
    get_stage2_smoke_case,
    load_stage2_smoke_cases,
)


def _truth(
    *,
    case_id="case",
    kernel_type=Stage2SmokeKernelType.ARRAY_MAP,
):
    return Stage2SmokeGroundTruth(
        case_id=case_id,
        kernel_type=kernel_type,
        scenario_kind=Stage2SmokeScenarioKind.BASELINE,
        injected_fault="none_baseline",
        ground_truth_owner=Stage2SmokeGroundTruthOwner.NONE,
        ground_truth_stage=Stage2SmokeGroundTruthStage.NONE,
        expected_route=Stage2SmokeExpectedRoute.ADVANCE,
        expected_terminal_state=(
            Stage2SmokeExpectedTerminalState.ACCEPTED
        ),
        hidden_visibility_expectation=(
            Stage2SmokeHiddenVisibility.OPERATOR_ONLY_NEVER_AGENT
        ),
    )


def _case(**overrides):
    values = {
        "case_id": "case",
        "kernel_type": Stage2SmokeKernelType.ARRAY_MAP,
        "task_id": "task",
        "kernel_name": "candidate_top",
        "original_code": (
            'extern "C" int original_top(int x) { return x; }\n'
        ),
        "candidate_code": (
            'extern "C" int candidate_top(int x) { return x; }\n'
        ),
        "preflight_testbench_code": (
            'extern "C" int original_top(int);\n'
            'extern "C" int candidate_top(int);\n'
            "int main() { return original_top(1) == "
            "candidate_top(1) ? 0 : 1; }\n"
        ),
        "public_testbench_code": (
            'extern "C" int original_top(int);\n'
            'extern "C" int candidate_top(int);\n'
            "int main() { return original_top(2) == "
            "candidate_top(2) ? 0 : 1; }\n"
        ),
        "hidden_testbench_code": (
            'extern "C" int original_top(int);\n'
            'extern "C" int candidate_top(int);\n'
            'const char *secret = "SECRET";\n'
            "int main() { return original_top(3) == "
            "candidate_top(3) ? 0 : 1; }\n"
        ),
        "hidden_secret_marker": "SECRET",
        "ground_truth": _truth(),
        "expected_budget": Stage2SmokeBudgetExpectation(
            tool_calls=5,
            compile_calls=2,
            csynth_calls=1,
            csim_calls=2,
        ),
    }
    values.update(overrides)
    return Stage2SmokeCase(**values)


class Stage2SmokeSchemaTests(unittest.TestCase):
    def test_budget_rejects_negative_count(self):
        with self.assertRaises(ValueError):
            Stage2SmokeBudgetExpectation(
                tool_calls=-1,
                compile_calls=0,
                csynth_calls=0,
                csim_calls=0,
            )

    def test_ground_truth_rejects_blank_fault(self):
        with self.assertRaises(ValueError):
            Stage2SmokeGroundTruth(
                case_id="case",
                kernel_type=Stage2SmokeKernelType.ARRAY_MAP,
                scenario_kind=Stage2SmokeScenarioKind.BASELINE,
                injected_fault=" ",
                ground_truth_owner=Stage2SmokeGroundTruthOwner.NONE,
                ground_truth_stage=Stage2SmokeGroundTruthStage.NONE,
                expected_route=Stage2SmokeExpectedRoute.ADVANCE,
                expected_terminal_state=(
                    Stage2SmokeExpectedTerminalState.ACCEPTED
                ),
                hidden_visibility_expectation=(
                    Stage2SmokeHiddenVisibility
                    .OPERATOR_ONLY_NEVER_AGENT
                ),
            )

    def test_case_rejects_ground_truth_case_mismatch(self):
        with self.assertRaises(ValueError):
            _case(ground_truth=_truth(case_id="other"))

    def test_case_rejects_kernel_type_mismatch(self):
        with self.assertRaises(ValueError):
            _case(
                ground_truth=_truth(
                    kernel_type=Stage2SmokeKernelType.REDUCTION
                )
            )

    def test_case_rejects_duplicate_suite_ids(self):
        with self.assertRaises(ValueError):
            _case(
                public_suite_id="same",
                hidden_suite_id="same",
            )

    def test_task_builder_uses_supplied_target(self):
        target = TargetProfile(
            name="custom",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
            device="xcu200-fsgd2104-2-e",
            clock_period_ns=4.0,
        )
        task = _case().build_task(target=target)
        self.assertIs(task.target, target)

    def test_operator_manifest_has_labels_without_raw_source(self):
        manifest = _case().operator_manifest()
        self.assertIn("ground_truth", manifest)
        self.assertIn("source_sha256", manifest)
        encoded = json.dumps(manifest, sort_keys=True)
        self.assertNotIn(_case().candidate_code, encoded)

    def test_agent_manifest_omits_labels_and_hidden_identity(self):
        manifest = _case().agent_safe_manifest()
        encoded = json.dumps(manifest)
        self.assertNotIn("ground_truth", encoded)
        self.assertNotIn("hidden_suite_id", encoded)
        self.assertNotIn("hidden_testbench", encoded)
        self.assertNotIn("SECRET", encoded)


class Stage2SmokeCorpusTests(unittest.TestCase):
    def test_corpus_has_exact_seven_required_types(self):
        self.assertEqual(len(STAGE2_SMOKE_CASES), 7)
        self.assertEqual(
            {case.kernel_type for case in STAGE2_SMOKE_CASES},
            set(Stage2SmokeKernelType),
        )

    def test_case_and_task_ids_are_unique(self):
        case_ids = [case.case_id for case in STAGE2_SMOKE_CASES]
        task_ids = [case.task_id for case in STAGE2_SMOKE_CASES]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertEqual(len(task_ids), len(set(task_ids)))

    def test_case_order_is_stable(self):
        self.assertEqual(
            [case.case_id for case in STAGE2_SMOKE_CASES],
            [
                "array-map",
                "reduction",
                "nested-stencil",
                "multi-output",
                "struct-record",
                "hls-stream",
                "stateful",
            ],
        )

    def test_all_cases_are_passing_baselines(self):
        for case in STAGE2_SMOKE_CASES:
            truth = case.ground_truth
            self.assertIs(
                truth.scenario_kind,
                Stage2SmokeScenarioKind.BASELINE,
            )
            self.assertEqual(truth.injected_fault, "none_baseline")
            self.assertIs(
                truth.ground_truth_owner,
                Stage2SmokeGroundTruthOwner.NONE,
            )
            self.assertIs(
                truth.ground_truth_stage,
                Stage2SmokeGroundTruthStage.NONE,
            )
            self.assertIs(
                truth.expected_route,
                Stage2SmokeExpectedRoute.ADVANCE,
            )
            self.assertIs(
                truth.expected_terminal_state,
                Stage2SmokeExpectedTerminalState.ACCEPTED,
            )

    def test_all_baselines_have_exact_full_chain_budget(self):
        for case in STAGE2_SMOKE_CASES:
            self.assertEqual(
                case.expected_budget.to_dict(),
                {
                    "tool_calls": 7,
                    "compile_calls": 2,
                    "csynth_calls": 1,
                    "csim_calls": 2,
                    "cosim_calls": 1,
                    "llm_calls": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                },
            )

    def test_each_task_declares_public_then_hidden(self):
        for case in STAGE2_SMOKE_CASES:
            task = case.build_task()
            self.assertEqual(len(task.test_suites), 2)
            self.assertIs(
                task.test_suites[0].split,
                EvaluationSplit.PUBLIC,
            )
            self.assertIs(
                task.test_suites[1].split,
                EvaluationSplit.HIDDEN,
            )
            self.assertIsNone(task.test_suites[0].testbench_path)
            self.assertIsNone(task.test_suites[1].testbench_path)

    def test_source_bundle_has_exact_keys(self):
        expected = {
            "original_code",
            "candidate_code",
            "preflight_testbench_code",
            "public_testbench_code",
            "hidden_testbench_code",
        }
        for case in STAGE2_SMOKE_CASES:
            self.assertEqual(set(case.source_bundle), expected)

    def test_source_roles_use_separate_top_symbols(self):
        for case in STAGE2_SMOKE_CASES:
            self.assertIn("original_top", case.original_code)
            self.assertNotIn("candidate_top", case.original_code)
            self.assertIn("candidate_top", case.candidate_code)
            self.assertNotIn("original_top", case.candidate_code)

    def test_hidden_secret_is_isolated_to_hidden_testbench(self):
        for case in STAGE2_SMOKE_CASES:
            marker = case.hidden_secret_marker
            self.assertIn(marker, case.hidden_testbench_code)
            self.assertNotIn(marker, case.original_code)
            self.assertNotIn(marker, case.candidate_code)
            self.assertNotIn(
                marker,
                case.preflight_testbench_code,
            )
            self.assertNotIn(marker, case.public_testbench_code)

    def test_operator_manifests_are_json_serializable(self):
        encoded = json.dumps(
            [
                case.operator_manifest()
                for case in STAGE2_SMOKE_CASES
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertIn("array-map", encoded)
        self.assertIn("hls-stream", encoded)

    def test_agent_manifests_contain_no_hidden_secret(self):
        encoded = json.dumps(
            [
                case.agent_safe_manifest()
                for case in STAGE2_SMOKE_CASES
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        for case in STAGE2_SMOKE_CASES:
            self.assertNotIn(case.hidden_secret_marker, encoded)
        self.assertNotIn("ground_truth", encoded)
        self.assertNotIn("hidden_suite_id", encoded)

    def test_loader_returns_immutable_corpus(self):
        loaded = load_stage2_smoke_cases()
        self.assertIs(loaded, STAGE2_SMOKE_CASES)
        self.assertIsInstance(loaded, tuple)

    def test_case_lookup_is_exact(self):
        self.assertEqual(
            get_stage2_smoke_case("hls-stream").kernel_type,
            Stage2SmokeKernelType.HLS_STREAM,
        )
        with self.assertRaises(KeyError):
            get_stage2_smoke_case("missing")

    def test_candidate_response_contract_parses_all_interfaces(self):
        for case in STAGE2_SMOKE_CASES:
            contract = CandidateResponseContract.from_candidate(
                case.build_task(),
                case.candidate_code,
            )
            self.assertEqual(
                contract.top_function_name,
                case.kernel_name,
            )
            self.assertIn(
                case.kernel_name,
                contract.interface_header,
            )

    def test_testbenches_reference_both_implementations(self):
        for case in STAGE2_SMOKE_CASES:
            for code in (
                case.preflight_testbench_code,
                case.public_testbench_code,
                case.hidden_testbench_code,
            ):
                self.assertIn("original_top", code)
                self.assertIn("candidate_top", code)
                self.assertIn("int main()", code)

    def test_corpus_uses_generic_project_naming(self):
        encoded = "\n".join(
            json.dumps(
                case.operator_manifest(),
                sort_keys=True,
            )
            + "\n"
            + "\n".join(case.source_bundle.values())
            for case in STAGE2_SMOKE_CASES
        ).lower()
        for forbidden in ("fpt26", "competition", "track_a"):
            self.assertNotIn(forbidden, encoded)
        for case in STAGE2_SMOKE_CASES:
            for digest in case.operator_manifest()[
                "source_sha256"
            ].values():
                self.assertRegex(digest, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
