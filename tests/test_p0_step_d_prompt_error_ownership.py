from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agrefactor.testing import model_testbench_repairer
from flow.tools import tb_coverage, tb_optimizer, testbench


ORIGINAL_NAME = "process_top"
CANDIDATE_NAME = "process_top_hls"
FROZEN_DECL = "void process_top_hls();"
TB_ONE = (
    "#define CAPACITY 4\n"
    "void process_top();\n"
    "void process_top_hls();\n"
    "int main(){process_top();process_top_hls();return 0;}\n"
)
TB_TWO = (
    "#define CAPACITY 4\n"
    "void process_top();\n"
    "void process_top_hls();\n"
    "int main(){process_top();process_top_hls();"
    "process_top();process_top_hls();return 0;}\n"
)
STUB = "void process_top_hls(){}\n"


def coverage(
    pct: float | None,
    *,
    status: str = "ok",
    owner: str = "none",
    action: str = "continue_validation",
):
    return {
        "status": status,
        "cov_pct": pct,
        "lines_total": 2 if pct is not None else None,
        "lines_hit": (
            round(2 * pct / 100.0)
            if pct is not None
            else None
        ),
        "uncovered_lines": (
            [2] if pct is not None and pct < 100.0 else []
        ),
        "run_returncode": 0 if status == "ok" else None,
        "compile_stderr": "stub compile error" if status != "ok" else "",
        "run_stderr": "",
        "qualification_errors": [],
        "failure_owner": owner,
        "next_action": action,
        "failure_evidence_source": "test",
    }


class StepDPromptContractTests(unittest.TestCase):
    def test_lightweight_prompt_uses_black_box_surface(self):
        message = testbench._build_testbench_request(
            "void process_top(){}\n",
            ORIGINAL_NAME,
        )
        self.assertIn("only external function forward declarations", message)
        self.assertIn("never define, stub, wrap", message)
        self.assertIn("implementation-private globals", message)
        self.assertIn("Correctness", message)
        self.assertIn("priority over testcase count or coverage", message)

    def test_lightweight_instruction_is_public_only(self):
        message = testbench._build_instruction_request(
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )
        self.assertIn("exact `process_top_hls` declaration", message)
        self.assertIn("Public Testbench", message)
        self.assertIn("Do not mention or require implementation-private", message)

    def test_coverage_prompt_forbids_top_definitions(self):
        message = tb_optimizer._initial_user_message(
            "void process_top(){}\n",
            ORIGINAL_NAME,
        )
        self.assertIn("only external function forward", message)
        self.assertIn("Never define, stub, wrap", message)
        self.assertIn("Correctness", message)
        self.assertIn("priority over testcase count or coverage", message)

    def test_stub_prompt_declares_unique_candidate_implementation(self):
        message = tb_optimizer._stub_request_message(
            ORIGINAL_NAME,
            FROZEN_DECL,
        )
        self.assertIn("only temporary implementation", message)
        self.assertIn("Define that Candidate top exactly once", message)
        self.assertIn("Do not include `main`", message)
        self.assertIn("Repair only the Stub", tb_optimizer._stub_request_message(
            ORIGINAL_NAME,
            FROZEN_DECL,
            failure_excerpt="bad stub",
        ))

    def test_empty_stub_preserves_frozen_linkage(self):
        message = tb_optimizer._empty_stub_request_message(
            CANDIDATE_NAME,
            'extern "C" void process_top_hls();',
        )
        self.assertIn("Preserve the exact C/C++ linkage", message)
        self.assertIn('extern "C" void process_top_hls();', message)
        self.assertNotIn("no `extern", message)

    def test_csynth_rewrite_is_coordinated_abi_correction(self):
        message = tb_optimizer._hls_friendly_rewrite_message(
            CANDIDATE_NAME,
            "unsupported interface",
        )
        self.assertIn("coordinated ABI correction", message)
        self.assertIn("matching Stub", message)
        self.assertIn("re-freeze", message)
        self.assertIn("Correctness takes priority over coverage", message)

    def test_repair_prompt_contract_prioritizes_correctness(self):
        combined = "\n".join(
            model_testbench_repairer._TESTBENCH_FORBIDDEN_ACTIONS
            + model_testbench_repairer._TESTBENCH_OUTPUT_REQUIREMENTS
        )
        self.assertIn("Only forward-declare", combined)
        self.assertIn("implementation-private globals", combined)
        self.assertIn("Correctness takes priority", combined)
        self.assertNotIn("Do not reduce test count", combined)


class StepDStructuralContractTests(unittest.TestCase):
    def test_repair_contract_rejects_external_private_helper(self):
        contract = model_testbench_repairer.TestbenchRepairContract(
            required_top_function_names=(
                ORIGINAL_NAME,
                CANDIDATE_NAME,
            )
        )
        proposed = (
            "void process_top();\n"
            "void process_top_hls();\n"
            "void private_helper();\n"
            "int main(){process_top();process_top_hls();return 0;}\n"
        )
        issues = contract.validate(proposed)
        self.assertTrue(any("external helper declarations" in item for item in issues))

    def test_testbench_contract_rejects_candidate_definition(self):
        with self.assertRaisesRegex(
            tb_optimizer.ModelArtifactError,
            "must not define",
        ):
            tb_optimizer.validate_testbench_top_contract(
                "void process_top();\n"
                "void process_top_hls(){}\n"
                "int main(){process_top_hls();return 0;}\n",
                ORIGINAL_NAME,
                CANDIDATE_NAME,
            )

    def test_testbench_contract_rejects_original_definition(self):
        with self.assertRaisesRegex(
            tb_optimizer.ModelArtifactError,
            "must not define",
        ):
            tb_optimizer.validate_testbench_top_contract(
                "void process_top(){}\n"
                "void process_top_hls();\n"
                "int main(){process_top_hls();return 0;}\n",
                ORIGINAL_NAME,
                CANDIDATE_NAME,
            )

    def test_testbench_contract_rejects_external_helper_declaration(self):
        with self.assertRaisesRegex(
            tb_optimizer.ModelArtifactError,
            "external helper declarations",
        ):
            tb_optimizer.validate_testbench_top_contract(
                "void process_top();\n"
                "void process_top_hls();\n"
                "void internal_reset();\n"
                "int main(){process_top_hls();return 0;}\n",
                ORIGINAL_NAME,
                CANDIDATE_NAME,
            )

    def test_stub_contract_rejects_main(self):
        with self.assertRaisesRegex(
            tb_optimizer.ModelArtifactError,
            "must not define main",
        ):
            tb_optimizer.validate_stub_contract(
                STUB + "int main(){return 0;}\n",
                original_name=ORIGINAL_NAME,
                candidate_name=CANDIDATE_NAME,
                frozen_hls_decl=FROZEN_DECL,
            )

    def test_stub_contract_rejects_abi_drift(self):
        with self.assertRaisesRegex(
            tb_optimizer.ModelArtifactError,
            "frozen ABI",
        ):
            tb_optimizer.validate_stub_contract(
                "int process_top_hls(int x){return x;}\n",
                original_name=ORIGINAL_NAME,
                candidate_name=CANDIDATE_NAME,
                frozen_hls_decl=FROZEN_DECL,
            )


class StepDErrorOwnershipTests(unittest.TestCase):
    def test_compile_diagnostic_owns_testbench_error(self):
        owner = tb_coverage._classify_compile_failure_owner(
            "testbench.cpp:12:3: error: missing symbol"
        )
        self.assertEqual(owner, "testbench")

    def test_compile_diagnostic_owns_stub_error(self):
        owner = tb_coverage._classify_compile_failure_owner(
            "refactor_code.cpp:4:7: error: bad return"
        )
        self.assertEqual(owner, "stub")

    def test_link_diagnostic_owns_abi_error(self):
        owner = tb_coverage._classify_compile_failure_owner(
            "undefined reference to `process_top_hls(int)'"
        )
        self.assertEqual(owner, "abi")

    def test_round_record_persists_ownership(self):
        rounds = []
        record = tb_optimizer._append_round(
            rounds,
            trajectory_idx=0,
            round_index=1,
            tb_code=TB_ONE,
            stub_code=STUB,
            cov=coverage(
                None,
                status="compile_failed",
                owner="stub",
                action="regenerate_stub",
            ),
        )
        self.assertEqual(record["failure_owner"], "stub")
        self.assertEqual(record["next_action"], "regenerate_stub")

    def test_coverage_shortfall_reuses_frozen_stub(self):
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
                side_effect=[TB_ONE, STUB, TB_TWO, STUB],
            ) as request,
            patch.object(
                tb_optimizer,
                "measure_coverage",
                side_effect=[coverage(50.0), coverage(100.0)],
            ),
            patch.object(
                tb_optimizer,
                "_synth_check",
                return_value=(True, ""),
            ),
            patch.object(
                tb_optimizer,
                "_agent_run_once",
                return_value="short instruction",
            ),
        ):
            result = tb_optimizer.run_trajectory(
                orig_code="void process_top(){}\n",
                kernel_name=ORIGINAL_NAME,
                K=2,
                target_pct=100.0,
                llm_config=None,
                want_sig_spec=False,
            )
        kinds = [
            call.kwargs["artifact_kind"]
            for call in request.call_args_list
        ]
        self.assertEqual(
            kinds,
            ["testbench", "stub", "testbench", "empty_stub"],
        )
        self.assertTrue(result["rounds"][1]["stub_reused"])
        self.assertEqual(
            result["rounds"][1]["frozen_public_hls_decl"],
            FROZEN_DECL,
        )

    def test_externally_pinned_public_abi_is_not_rewritten(self):
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
                side_effect=[TB_ONE, STUB, STUB],
            ) as request,
            patch.object(
                tb_optimizer,
                "measure_coverage",
                return_value=coverage(100.0),
            ),
            patch.object(
                tb_optimizer,
                "_synth_check",
                return_value=(False, "unsupported ABI"),
            ),
        ):
            result = tb_optimizer.run_trajectory(
                orig_code="void process_top(){}\n",
                kernel_name=ORIGINAL_NAME,
                K=1,
                target_pct=100.0,
                llm_config=None,
                want_sig_spec=False,
                pinned_hls_decl=FROZEN_DECL,
                emit_final_text=False,
            )
        kinds = [
            call.kwargs["artifact_kind"]
            for call in request.call_args_list
        ]
        self.assertEqual(
            kinds,
            ["testbench", "stub", "empty_stub"],
        )
        self.assertFalse(result["synth_ok"])
        self.assertEqual(
            result["frozen_public_hls_decl"],
            FROZEN_DECL,
        )

    def test_stub_owned_failure_regenerates_only_stub(self):
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
                side_effect=[TB_ONE, STUB, STUB, STUB],
            ) as request,
            patch.object(
                tb_optimizer,
                "measure_coverage",
                side_effect=[
                    coverage(
                        None,
                        status="compile_failed",
                        owner="stub",
                        action="regenerate_stub",
                    ),
                    coverage(100.0),
                ],
            ),
            patch.object(
                tb_optimizer,
                "_synth_check",
                return_value=(True, ""),
            ),
            patch.object(
                tb_optimizer,
                "_agent_run_once",
                return_value="short instruction",
            ),
        ):
            result = tb_optimizer.run_trajectory(
                orig_code="void process_top(){}\n",
                kernel_name=ORIGINAL_NAME,
                K=2,
                target_pct=100.0,
                llm_config=None,
                want_sig_spec=False,
            )
        kinds = [
            call.kwargs["artifact_kind"]
            for call in request.call_args_list
        ]
        self.assertEqual(
            kinds,
            ["testbench", "stub", "stub", "empty_stub"],
        )
        self.assertTrue(result["rounds"][1]["testbench_reused"])
        self.assertEqual(
            result["rounds"][1]["ownership_action"],
            "regenerate_stub",
        )


if __name__ == "__main__":
    unittest.main()
