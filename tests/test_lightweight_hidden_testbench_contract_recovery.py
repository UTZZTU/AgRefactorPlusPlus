from __future__ import annotations

from unittest.mock import Mock, patch
import unittest

from flow.tools import tb_optimizer


class LightweightHiddenTestbenchContractRecoveryTests(unittest.TestCase):
    def test_k1_repairs_initial_hidden_abi_drift_once(self) -> None:
        frozen_decl = "void process_top_hls(int *input);"
        qualified = {
            "status": "ok",
            "cov_pct": 100.0,
            "lines_total": 8,
            "lines_hit": 8,
            "uncovered_lines": [],
            "run_returncode": 0,
            "compile_stderr": "",
            "run_stderr": "",
            "qualification_errors": [],
            "failure_owner": "none",
            "next_action": "continue_validation",
            "failure_evidence_source": "g++/runtime/gcov",
        }
        agent = Mock()
        loader = Mock()
        loader.load_agent.return_value = agent

        def finish(_agent, rounds, *_args, **_kwargs):
            return {"rounds": rounds}

        with (
            patch.object(tb_optimizer, "HLSAgentLoader", return_value=loader),
            patch.object(
                tb_optimizer,
                "_request_cpp_artifact",
                side_effect=[
                    "int main(){return 0;}\n",
                    "int main(){return 0;}\n",
                    "void process_top_hls(int *input){}\n",
                ],
            ) as request_artifact,
            patch.object(
                tb_optimizer,
                "extract_hls_decl_from_testbench",
                return_value=frozen_decl,
            ),
            patch.object(
                tb_optimizer,
                "_ensure_original_forward_declaration",
                side_effect=lambda code, *_args: code,
            ),
            patch.object(tb_optimizer, "validate_testbench_top_contract"),
            patch.object(tb_optimizer, "validate_stub_contract"),
            patch.object(
                tb_optimizer,
                "_validate_frozen_candidate_abi",
                side_effect=[
                    tb_optimizer.ModelArtifactError(
                        "Testbench changed the externally frozen "
                        "Public-derived ABI"
                    ),
                    None,
                    None,
                ],
            ) as validate_abi,
            patch.object(
                tb_optimizer,
                "_measure_qualified_coverage",
                return_value=qualified,
            ) as measure,
            patch.object(
                tb_optimizer,
                "_freeze_public_contract",
                return_value=(frozen_decl, ()),
            ),
            patch.object(
                tb_optimizer,
                "_finalize_trajectory",
                side_effect=finish,
            ),
        ):
            result = tb_optimizer.run_trajectory(
                "void process_top(int *input){}\n",
                "process_top",
                K=1,
                target_pct=90.0,
                llm_config={},
                want_sig_spec=False,
                pinned_hls_decl=frozen_decl,
                emit_final_text=False,
            )

        self.assertEqual(request_artifact.call_count, 3)
        self.assertEqual(validate_abi.call_count, 3)
        self.assertEqual(measure.call_count, 1)
        self.assertEqual(len(result["rounds"]), 1)
        record = result["rounds"][0]
        self.assertEqual(record["status"], "ok")
        self.assertTrue(record["initial_testbench_contract_repaired"])
        self.assertIn(
            "externally frozen Public-derived ABI",
            record["initial_testbench_contract_error"],
        )
        repair_prompt = request_artifact.call_args_list[1].args[1]
        self.assertIn(frozen_decl, repair_prompt)
        self.assertIn("actual Original", repair_prompt)
        self.assertNotIn("coverage-only refinement", repair_prompt)


if __name__ == "__main__":
    unittest.main()
