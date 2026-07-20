from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from flow import new as flow_new
from flow.tools import tb_optimizer


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
            record = {
                "round": 1,
                "tb_code": "int main(){return 0;}\n",
                "stub_code": "void process_top_hls(){}\n",
                "status": "compile_failed",
                "compile_stderr": "error",
                "run_stderr": "",
            }
            with patch.dict(
                os.environ,
                {"AGREFACTOR_TB_DEBUG_DIR": tmp},
                clear=False,
            ):
                tb_optimizer._persist_round_artifacts(2, record)
            root = Path(tmp) / "trajectory_002" / "round_001"
            self.assertTrue((root / "testbench.cpp").is_file())
            self.assertTrue((root / "stub.cpp").is_file())
            self.assertTrue((root / "coverage.json").is_file())

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
