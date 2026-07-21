from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from flow import new as flow_new
from flow.tools import tb_coverage, tb_optimizer


class ReasoningEffortNormalizationTests(unittest.TestCase):
    def test_none_is_preserved(self):
        self.assertIsNone(flow_new.normalize_reasoning_effort(None))

    def test_max_maps_to_xhigh(self):
        self.assertEqual(
            flow_new.normalize_reasoning_effort("max"),
            "xhigh",
        )

    def test_xhigh_is_preserved(self):
        self.assertEqual(
            flow_new.normalize_reasoning_effort(" XHIGH "),
            "xhigh",
        )

    def test_unknown_value_is_rejected(self):
        with self.assertRaises(ValueError):
            flow_new.normalize_reasoning_effort("ultra")


class StrictCppArtifactTests(unittest.TestCase):
    def test_accepts_one_testbench_block(self):
        value = tb_optimizer._extract_one_cpp_block(
            "```cpp\nvoid process_top_hls();\n"
            "int main(){process_top_hls();return 0;}\n```",
            artifact_kind="testbench",
            required_symbol="process_top_hls",
        )
        self.assertIn("int main()", value)

    def test_rejects_empty_response(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            tb_optimizer._extract_one_cpp_block("")

    def test_rejects_raw_prompt_text(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            tb_optimizer._extract_one_cpp_block(
                "userOriginal kernel source code: ..."
            )

    def test_rejects_multiple_cpp_blocks(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            tb_optimizer._extract_one_cpp_block(
                "```cpp\nint a;\n```\n```cpp\nint b;\n```"
            )

    def test_rejects_commentary_outside_block(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            tb_optimizer._extract_one_cpp_block(
                "Here you go.\n```cpp\nint main(){return 0;}\n```",
                artifact_kind="testbench",
            )

    def test_testbench_requires_main(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            tb_optimizer._extract_one_cpp_block(
                "```cpp\nvoid process_top_hls();\n```",
                artifact_kind="testbench",
                required_symbol="process_top_hls",
            )

    def test_stub_requires_definition(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            tb_optimizer._extract_one_cpp_block(
                "```cpp\nvoid process_top_hls();\n```",
                artifact_kind="stub",
                required_symbol="process_top_hls",
            )


class PromptStateSafetyTests(unittest.TestCase):
    def test_initial_prompt_requests_normal_complete_state_safe_testbench(self):
        message = tb_optimizer._initial_user_message(
            orig_code="int state; void process_top(){}\n",
            kernel_name="process_top",
            sig_spec_constraint=None,
        )
        self.assertIn("complete, normal-strength testbench", message)
        self.assertIn("do not emit a preliminary or simplified", message)
        self.assertIn("equivalent clean logical states", message)
        self.assertIn("separate mutable input/output storage", message)
        self.assertIn("do not call the original repeatedly", message)
        self.assertIn("State safety takes priority", message)
        self.assertIn("preserve its C/C++ language linkage", message)
        self.assertIn("Never add or remove `extern \"C\"`", message)

    def test_stub_prompt_makes_original_delegation_conditional(self):
        message = tb_optimizer._stub_request_message("process_top")
        self.assertIn("CONDITIONAL, not mandatory", message)
        self.assertIn("Never delegate as a second execution", message)
        self.assertIn("independent minimal stub", message)
        self.assertIn("does not call or copy the original", message)
        self.assertNotIn(
            "signature and delegate to the corresponding original function",
            message,
        )

    def test_stub_prompt_pins_exact_hls_linkage(self):
        declaration = (
            'void process_top_hls(int n, int *input, '
            'int *output, int *fallback)'
        )
        message = tb_optimizer._stub_request_message(
            "process_top",
            declaration,
        )
        self.assertIn("EXACT `_hls` DEFINITION HEADER", message)
        self.assertIn(declaration, message)
        self.assertIn(
            'Preserve `extern "C"` presence or absence',
            message,
        )

    def test_runtime_crash_feedback_names_shared_state_delegation(self):
        message = tb_optimizer._feedback_message(
            round_idx=2,
            prev_cov=0.0,
            uncovered_lines=[],
            annotated_source="void process_top(){}\n",
            prev_status="no_gcda",
            prev_run_stderr="malloc(): corrupted top size",
        )
        self.assertIn("invoked repeatedly", message)
        self.assertIn("delegating stub", message)
        self.assertIn("equivalent clean state", message)
        self.assertIn("unsafe delegation", message)

    def test_agent_system_prompt_has_same_state_safety_policy(self):
        import yaml

        data = yaml.safe_load(
            Path(tb_optimizer.AGENT_YAML).read_text(encoding="utf-8")
        )
        message = data["agents"]["tb_engineer"]["system_message"]
        self.assertIn("complete, normal-strength testbench directly", message)
        self.assertIn("Persistent-state safety rules", message)
        self.assertIn("State safety takes priority", message)
        self.assertIn("CONDITIONAL, not mandatory", message)
        self.assertIn("independent minimal behavior", message)
        self.assertNotIn("delegate to the original wherever possible", message)


class LightweightQualificationGateTests(unittest.TestCase):
    ORIGINAL = (
        "void process_top(int n, int *input, int *output, "
        "int *fallback) { *fallback = 0; }\n"
    )

    @staticmethod
    def _tb(size: str) -> str:
        return (
            "#define CAPACITY 8\n"
            "int main(){\n"
            " int input[CAPACITY] = {};\n"
            " int output[CAPACITY] = {};\n"
            " int fallback = 0;\n"
            f" process_top({size}, input, output, &fallback);\n"
            " return 0;\n"
            "}\n"
        )

    def test_capacity_gate_is_conservative(self):
        conflicts = tb_optimizer._obvious_capacity_conflicts
        self.assertEqual(
            len(conflicts(self.ORIGINAL, self._tb("16"), "process_top")),
            2,
        )
        self.assertEqual(
            conflicts(self.ORIGINAL, self._tb("CAPACITY"), "process_top"),
            [],
        )
        self.assertEqual(
            conflicts(
                self.ORIGINAL,
                self._tb("runtime_size + 1"),
                "process_top",
            ),
            [],
        )

    def test_capacity_failure_skips_coverage_process(self):
        with patch.object(tb_optimizer, "measure_coverage") as measure:
            result = tb_optimizer._measure_qualified_coverage(
                self.ORIGINAL,
                self._tb("16"),
                "void process_top_hls(){}\n",
                "process_top",
            )
        measure.assert_not_called()
        self.assertEqual(result["status"], "qualification_failed")
        self.assertEqual(len(result["qualification_errors"]), 2)

    def test_nonzero_testbench_return_is_run_failed(self):
        compile_result = Mock(returncode=0, stderr="", stdout="")
        run_result = Mock(returncode=1, stderr="mismatch", stdout="")
        with patch.object(
            tb_coverage.subprocess,
            "run",
            side_effect=[compile_result, run_result],
        ) as run:
            result = tb_coverage.measure_coverage(
                "void process_top(){}\n",
                "int main(){return 1;}\n",
                "void process_top_hls(){}\n",
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result["status"], "run_failed")
        self.assertEqual(result["run_returncode"], 1)


    STATEFUL = (
        "int *queue;\n"
        "int front = 0, rear = -1;\n"
        "bool fallback = false;\n"
        "void process_top(int n, int *input, int *output, int *flag) {}\n"
    )

    @staticmethod
    def _state_tb(call_count: int) -> str:
        calls = "\n".join(
            " process_top(1, input, output, &fallback);"
            for _ in range(call_count)
        )
        return (
            "int main(){\n"
            " int input[1] = {};\n"
            " int output[1] = {};\n"
            " int fallback = 0;\n"
            f"{calls}\n"
            " return 0;\n"
            "}\n"
        )

    def test_stateful_delegating_stub_is_rejected(self):
        stub = (
            "void process_top_hls(int n, int *input, int *output, "
            "int *fallback){\n"
            " process_top(n, input, output, fallback);\n"
            "}\n"
        )
        with patch.object(tb_optimizer, "measure_coverage") as measure:
            result = tb_optimizer._measure_qualified_coverage(
                self.STATEFUL,
                self._state_tb(1),
                stub,
                "process_top",
            )
        measure.assert_not_called()
        self.assertEqual(result["status"], "qualification_failed")
        self.assertTrue(
            any(
                "stub delegates to stateful original" in error
                for error in result["qualification_errors"]
            )
        )

    def test_repeated_stateful_original_calls_are_rejected(self):
        with patch.object(tb_optimizer, "measure_coverage") as measure:
            result = tb_optimizer._measure_qualified_coverage(
                self.STATEFUL,
                self._state_tb(2),
                "void process_top_hls(int, int*, int*, int*){}\n",
                "process_top",
            )
        measure.assert_not_called()
        self.assertTrue(
            any(
                "without a verified reset" in error
                for error in result["qualification_errors"]
            )
        )

    def test_safe_cases_are_not_blocked_by_state_gate(self):
        passed = {
            "status": "ok",
            "cov_pct": 100.0,
            "lines_total": 1,
            "lines_hit": 1,
            "uncovered_lines": [],
            "run_returncode": 0,
            "compile_stderr": "",
            "run_stderr": "",
        }
        independent_stub = (
            "void process_top_hls(int, int*, int*, int*){}\n"
        )
        stateless = (
            "void process_top(int n, int *input, int *output, "
            "int *fallback){}\n"
        )
        delegating_stub = (
            "void process_top_hls(int n, int *input, int *output, "
            "int *fallback){process_top(n,input,output,fallback);}\n"
        )
        with patch.object(
            tb_optimizer,
            "measure_coverage",
            return_value=dict(passed),
        ) as measure:
            stateful_once = tb_optimizer._measure_qualified_coverage(
                self.STATEFUL,
                self._state_tb(1),
                independent_stub,
                "process_top",
            )
            stateless_delegate = tb_optimizer._measure_qualified_coverage(
                stateless,
                self._state_tb(1),
                delegating_stub,
                "process_top",
            )
        self.assertEqual(measure.call_count, 2)
        self.assertEqual(stateful_once["status"], "ok")
        self.assertEqual(stateless_delegate["status"], "ok")

    def test_const_only_globals_do_not_count_as_mutable_state(self):
        source = (
            "const int LIMIT = 8;\n"
            "static constexpr int WIDTH = 4;\n"
            "void process_top(){}\n"
        )
        self.assertEqual(
            tb_optimizer._obvious_persistent_state_markers(source),
            [],
        )


    def test_original_linkage_mismatch_is_rejected(self):
        errors = tb_optimizer._obvious_linkage_conflicts(
            "void process_top() {}\n",
            (
                'extern "C" void process_top();\n'
                "void process_top_hls();\n"
                "int main(){process_top();process_top_hls();}\n"
            ),
            "void process_top_hls() {}\n",
            "process_top",
        )
        self.assertTrue(
            any(
                "language linkage of original" in error
                for error in errors
            )
        )

    def test_hls_linkage_mismatch_is_rejected(self):
        errors = tb_optimizer._obvious_linkage_conflicts(
            "void process_top() {}\n",
            (
                "void process_top();\n"
                "void process_top_hls();\n"
                "int main(){process_top();process_top_hls();}\n"
            ),
            'extern "C" void process_top_hls() {}\n',
            "process_top",
        )
        self.assertTrue(
            any(
                "language linkage of process_top_hls" in error
                for error in errors
            )
        )

    def test_matching_cpp_linkage_is_allowed(self):
        self.assertEqual(
            tb_optimizer._obvious_linkage_conflicts(
                "void process_top() {}\n",
                (
                    "void process_top();\n"
                    "void process_top_hls();\n"
                    "int main(){process_top();process_top_hls();}\n"
                ),
                "void process_top_hls() {}\n",
                "process_top",
            ),
            [],
        )

    def test_looped_stateful_original_call_is_rejected(self):
        looped_tb = (
            "int main(){\n"
            " int input[1] = {}, output[1] = {}, fallback = 0;\n"
            " for (int t = 0; t < 3; ++t) {\n"
            "  process_top(1, input, output, &fallback);\n"
            " }\n"
            " return 0;\n"
            "}\n"
        )
        with patch.object(tb_optimizer, "measure_coverage") as measure:
            result = tb_optimizer._measure_qualified_coverage(
                self.STATEFUL,
                looped_tb,
                "void process_top_hls(int, int*, int*, int*) {}\n",
                "process_top",
            )
        measure.assert_not_called()
        self.assertTrue(
            any(
                "inside an obvious loop" in error
                for error in result["qualification_errors"]
            )
        )

    def test_failure_feedback_explains_both_gates(self):
        run_failed = tb_optimizer._feedback_message(
            2, 0.0, [], self.ORIGINAL, "run_failed",
            prev_run_stderr="mismatch",
        )
        qualification_failed = tb_optimizer._feedback_message(
            2, 0.0, [], self.ORIGINAL, "qualification_failed",
            prev_run_stderr="fixed capacity 8",
        )
        self.assertIn("coverage alone is not sufficient", run_failed)
        self.assertIn("pre-compile qualification gate", qualification_failed)
        self.assertIn("language-linkage", qualification_failed)
        self.assertIn("persistent-state constraint", qualification_failed)

class CoverageLoopHardeningTests(unittest.TestCase):
    ORIGINAL = (
        "void process_top(int n, int *input, int *output, "
        "int *fallback) {\n    *fallback = 0;\n}\n"
    )

    def test_missing_original_declaration_is_injected(self):
        stub = (
            "void process_top_hls(int n, int *input, int *output, "
            "int *fallback) {\n"
            "    process_top(n, input, output, fallback);\n}\n"
        )
        value = tb_optimizer._ensure_original_forward_declaration(
            stub,
            self.ORIGINAL,
            "process_top",
        )
        self.assertTrue(
            value.startswith(
                "void process_top(int n, int *input, "
                "int *output, int *fallback);"
            )
        )

    def test_existing_declaration_is_not_duplicated(self):
        stub = (
            "void process_top(int n, int *input, int *output, "
            "int *fallback);\n"
            "void process_top_hls(int n, int *input, int *output, "
            "int *fallback) {\n"
            "    process_top(n, input, output, fallback);\n}\n"
        )
        value = tb_optimizer._ensure_original_forward_declaration(
            stub,
            self.ORIGINAL,
            "process_top",
        )
        self.assertEqual(value, stub)

    def test_identical_failures_are_detected(self):
        record = {
            "status": "compile_failed",
            "compile_stderr": "same error",
            "run_stderr": "",
        }
        self.assertTrue(
            tb_optimizer._repeated_failure(
                [dict(record), dict(record)]
            )
        )

    def test_different_failures_are_not_collapsed(self):
        self.assertFalse(
            tb_optimizer._repeated_failure(
                [
                    {
                        "status": "compile_failed",
                        "compile_stderr": "error A",
                        "run_stderr": "",
                    },
                    {
                        "status": "compile_failed",
                        "compile_stderr": "error B",
                        "run_stderr": "",
                    },
                ]
            )
        )

    def test_debug_artifacts_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            rounds = []
            coverage = {
                "status": "ok",
                "cov_pct": 100.0,
                "lines_total": 1,
                "lines_hit": 1,
                "uncovered_lines": [],
                "run_returncode": 0,
                "compile_stderr": "",
                "run_stderr": "",
                "qualification_errors": [],
            }
            with patch.dict(
                os.environ,
                {"AGREFACTOR_TB_DEBUG_DIR": tmp},
                clear=False,
            ):
                record = tb_optimizer._append_round(
                    rounds,
                    trajectory_idx=2,
                    round_index=1,
                    tb_code="int main(){return 0;}\n",
                    stub_code="void process_top_hls(){}\n",
                    cov=coverage,
                )
            root = Path(tmp) / "trajectory_002" / "round_001"
            self.assertTrue((root / "testbench.cpp").is_file())
            self.assertTrue((root / "stub.cpp").is_file())
            coverage_path = root / "coverage.json"
            self.assertTrue(coverage_path.is_file())
            persisted = json.loads(
                coverage_path.read_text(encoding="utf-8")
            )
            self.assertEqual(record["run_returncode"], 0)
            self.assertEqual(rounds[0]["run_returncode"], 0)
            self.assertEqual(persisted["run_returncode"], 0)

    def test_run_trajectory_regenerates_stub(self):
        failed = {
            "status": "compile_failed",
            "cov_pct": None,
            "lines_total": None,
            "lines_hit": None,
            "uncovered_lines": [],
            "compile_stderr": "bad first stub",
            "run_stderr": "",
        }
        passed = {
            "status": "ok",
            "cov_pct": 100.0,
            "lines_total": 1,
            "lines_hit": 1,
            "uncovered_lines": [],
            "compile_stderr": "",
            "run_stderr": "",
        }
        loader = Mock()
        loader.load_agent.return_value = object()
        with (
            patch.object(
                tb_optimizer,
                "HLSAgentLoader",
                return_value=loader,
            ),
            patch.object(
                tb_optimizer,
                "_request_cpp_artifact",
                side_effect=[
                    "void process_top_hls();\n"
                    "int main(){process_top_hls();return 0;}\n",
                    "void process_top_hls(){process_top();}\n",
                    "void process_top_hls();\n"
                    "int main(){process_top_hls();return 0;}\n",
                    "void process_top_hls(){process_top();}\n",
                    "void process_top_hls(){}\n",
                ],
            ) as request_artifact,
            patch.object(
                tb_optimizer,
                "_ensure_original_forward_declaration",
                side_effect=lambda code, *_: code,
            ),
            patch.object(
                tb_optimizer,
                "measure_coverage",
                side_effect=[failed, passed],
            ),
            patch.object(
                tb_optimizer,
                "_synth_check",
                return_value=(True, ""),
            ),
            patch.object(
                tb_optimizer,
                "_agent_run_once",
                return_value="final instruction",
            ),
        ):
            result = tb_optimizer.run_trajectory(
                orig_code="void process_top(){}\n",
                kernel_name="process_top",
                K=2,
                target_pct=100.0,
                sig_spec_constraint=None,
                llm_config=None,
                want_sig_spec=False,
            )
        kinds = [
            call.kwargs["artifact_kind"]
            for call in request_artifact.call_args_list
        ]
        self.assertEqual(
            kinds,
            ["testbench", "stub", "testbench", "stub", "empty_stub"],
        )
        self.assertTrue(result["qualified"])


class HiddenQualificationTests(unittest.TestCase):
    def test_unqualified_hidden_trajectories_are_rejected(self):
        with (
            patch.object(
                tb_optimizer,
                "run_trajectory",
                return_value={
                    "qualified": False,
                    "synth_ok": False,
                    "best_tb": "",
                    "final_text": "",
                    "trajectory_status": "coverage_failed",
                },
            ),
            self.assertRaises(RuntimeError),
        ):
            tb_optimizer.make_golden_hidden_tb(
                orig_code="void process_top(){}\n",
                kernel_name="process_top",
                M=1,
                K=1,
            )

    def test_qualified_hidden_trajectory_is_selected(self):
        trajectory = {
            "trajectory_idx": 0,
            "qualified": True,
            "synth_ok": True,
            "best_tb": "int main(){return 0;}\n",
            "best_stub": "void process_top_hls(){}\n",
            "best_empty_stub": "void process_top_hls(){}\n",
            "final_text": "signature spec",
            "best_cov": 100.0,
            "best_round": 1,
            "rounds": [],
        }
        with patch.object(
            tb_optimizer,
            "run_trajectory",
            return_value=trajectory,
        ):
            result = tb_optimizer.make_golden_hidden_tb(
                orig_code="void process_top(){}\n",
                kernel_name="process_top",
                M=1,
                K=1,
            )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["hidden_cov"], 100.0)


if __name__ == "__main__":
    unittest.main()
