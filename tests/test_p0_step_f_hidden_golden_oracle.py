from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from flow.tools import tb_optimizer


ORIGINAL = "process_top"
CANDIDATE = "process_top_hls"
PINNED = 'extern "C" void process_top_hls(int n);'


def cov(*, hit: int, total: int = 8):
    return {
        "status": "ok",
        "cov_pct": 100.0 * hit / total,
        "lines_total": total,
        "lines_hit": hit,
        "uncovered_lines": [],
        "run_returncode": 0,
        "compile_stderr": "",
        "run_stderr": "",
        "failure_owner": "none",
        "next_action": "continue_validation",
        "failure_evidence_source": "g++/runtime/gcov",
    }


class HiddenGoldenOraclePromptTests(unittest.TestCase):
    def test_pinned_prompt_requires_actual_original_oracle(self):
        message = tb_optimizer._initial_user_message(
            "void process_top(int n){}\n",
            ORIGINAL,
            pinned_public_hls_decl=PINNED,
        )
        self.assertIn("HELD-OUT GOLDEN ORACLE CONTRACT", message)
        self.assertIn("at least one actual call", message)
        self.assertIn("Expected outputs must come from the actual Original", message)
        self.assertIn("Do not implement or use a Testbench-owned semantic", message)

    def test_unpinned_public_prompt_does_not_add_held_out_block(self):
        message = tb_optimizer._initial_user_message(
            "void process_top(int n){}\n",
            ORIGINAL,
        )
        self.assertNotIn("HELD-OUT GOLDEN ORACLE CONTRACT", message)

    def test_coverage_agent_contract_forbids_local_oracle(self):
        text = Path("flow/agents/testbench_coverage.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("expected outputs must come from an actual call", text)
        self.assertIn("Never substitute a Testbench-owned semantic", text)

    def test_held_out_feedback_repeats_oracle_contract(self):
        message = tb_optimizer._feedback_message(
            2,
            0.0,
            [1],
            "void process_top(){}",
            "qualification_failed",
            prev_compile_stderr="zero Original coverage",
            failure_owner="testbench",
            next_action="repair_testbench",
            frozen_hls_decl=PINNED,
        )
        self.assertIn("HELD-OUT GOLDEN ORACLE CONTRACT", message)
        self.assertIn("Expected outputs must come from the actual Original", message)


class DeduplicatorPromptHardeningTests(unittest.TestCase):
    def setUp(self):
        self.text = Path("flow/agents/identifying.yaml").read_text(
            encoding="utf-8"
        )

    def test_deduplicator_defaults_to_conservative_retention(self):
        self.assertIn("CONSERVATIVE duplicate removal", self.text)
        self.assertIn("If identity is uncertain, KEEP both items", self.text)
        self.assertIn("Default to `index_to_remove: []`", self.text)

    def test_deduplicator_does_not_collapse_related_categories(self):
        self.assertIn(
            "Do NOT treat related, causal, overlapping",
            self.text,
        )
        self.assertIn(
            "Recursion, dynamic allocation, recursive data structures",
            self.text,
        )
        self.assertIn(
            "must not replace distinct discoveries",
            self.text,
        )


class HiddenGoldenOracleStructuralTests(unittest.TestCase):
    def test_held_out_contract_rejects_missing_original_call(self):
        code = (
            "void process_top();\n"
            "void process_top_hls();\n"
            "int main(){process_top_hls();return 0;}\n"
        )
        with self.assertRaisesRegex(
            tb_optimizer.ModelArtifactError,
            "does not call Original top",
        ):
            tb_optimizer.validate_testbench_top_contract(
                code,
                ORIGINAL,
                CANDIDATE,
                require_original_call=True,
            )

    def test_public_compatibility_does_not_force_new_requirement(self):
        code = (
            "void process_top();\n"
            "void process_top_hls();\n"
            "int main(){process_top_hls();return 0;}\n"
        )
        tb_optimizer.validate_testbench_top_contract(
            code,
            ORIGINAL,
            CANDIDATE,
        )

    def test_held_out_contract_accepts_both_real_top_calls(self):
        code = (
            "void process_top();\n"
            "void process_top_hls();\n"
            "int main(){process_top();process_top_hls();return 0;}\n"
        )
        tb_optimizer.validate_testbench_top_contract(
            code,
            ORIGINAL,
            CANDIDATE,
            require_original_call=True,
        )


class HiddenGoldenOracleCoverageTests(unittest.TestCase):
    def test_zero_original_lines_becomes_testbench_failure(self):
        with patch.object(
            tb_optimizer,
            "measure_coverage",
            return_value=cov(hit=0),
        ):
            result = tb_optimizer._measure_qualified_coverage(
                "void process_top(){}",
                "void process_top(); void process_top_hls(); "
                "int main(){process_top();process_top_hls();}",
                "void process_top_hls(){}",
                ORIGINAL,
                require_original_execution=True,
            )
        self.assertEqual(result["status"], "qualification_failed")
        self.assertEqual(result["failure_owner"], "testbench")
        self.assertEqual(result["next_action"], "repair_testbench")
        self.assertIn("zero executed lines", result["compile_stderr"])
        self.assertTrue(result["qualification_errors"])

    def test_positive_original_lines_remains_qualified(self):
        with patch.object(
            tb_optimizer,
            "measure_coverage",
            return_value=cov(hit=3),
        ):
            result = tb_optimizer._measure_qualified_coverage(
                "void process_top(){}",
                "void process_top(); void process_top_hls(); "
                "int main(){process_top();process_top_hls();}",
                "void process_top_hls(){}",
                ORIGINAL,
                require_original_execution=True,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["lines_hit"], 3)

    def test_public_measurement_preserves_existing_zero_coverage_behavior(self):
        with patch.object(
            tb_optimizer,
            "measure_coverage",
            return_value=cov(hit=0),
        ):
            result = tb_optimizer._measure_qualified_coverage(
                "void process_top(){}",
                "void process_top(); void process_top_hls(); "
                "int main(){process_top_hls();}",
                "void process_top_hls(){}",
                ORIGINAL,
                require_original_execution=False,
            )
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
