from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from agrefactor.testing import model_testbench_repairer
from flow.tools import tb_coverage, tb_optimizer, testbench
from agrefactor.product.source_bootstrap import _public_candidate_parameter_names


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
    def test_lightweight_generation_uses_bounded_cpp_artifact_request(self):
        agent = Mock()
        response = Mock()
        response.messages = [{"content": "instruction"}]
        agent.run.return_value = response
        loader = Mock()
        loader.load_agent.return_value = agent
        cv = {
            "kernel_name": ORIGINAL_NAME,
            "curr_code": "void process_top(){}\n",
        }
        with patch.object(
            testbench,
            "HLSAgentLoader",
            return_value=loader,
        ), patch.object(
            tb_optimizer,
            "_request_cpp_artifact",
            return_value=TB_ONE,
        ) as request_artifact, patch.object(
            tb_coverage,
            "check_original_execution",
            return_value={"status": "ok"},
        ):
            generated, instruction, name = testbench.gen_tb_prior(cv)

        request_artifact.assert_called_once()
        self.assertEqual(generated, TB_ONE)
        self.assertEqual(instruction, "instruction")
        self.assertEqual(name, CANDIDATE_NAME)

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
        self.assertIn(
            "one fixed capacity across every Candidate call",
            message,
        )
        self.assertIn("full declared interface depth", message)
        self.assertIn("complete every planned Original call", message)

    def test_lightweight_public_repairs_original_execution_failure(self):
        agent = Mock()
        response = Mock()
        response.messages = [{"content": "instruction"}]
        agent.run.return_value = response
        loader = Mock()
        loader.load_agent.return_value = agent
        cv = {
            "kernel_name": ORIGINAL_NAME,
            "curr_code": "void process_top(){}\n",
        }
        failed = {
            "status": "original_run_failed",
            "compile_stderr": "",
            "run_stderr": "AddressSanitizer: heap-buffer-overflow at orig_code.cpp:12",
            "run_stdout": "",
        }
        with patch.object(
            testbench,
            "HLSAgentLoader",
            return_value=loader,
        ), patch.object(
            tb_optimizer,
            "_request_cpp_artifact",
            side_effect=[TB_ONE, TB_TWO],
        ) as request_artifact, patch.object(
            tb_coverage,
            "check_original_execution",
            side_effect=[failed, {"status": "ok"}],
        ) as original_check:
            generated, instruction, name = testbench.gen_tb_prior(cv)

        self.assertEqual(generated, TB_TWO)
        self.assertEqual(name, CANDIDATE_NAME)
        self.assertEqual(original_check.call_count, 2)
        self.assertEqual(request_artifact.call_count, 2)
        repair_prompt = request_artifact.call_args_list[1].args[1]
        self.assertIn(TB_ONE.strip(), repair_prompt)
        self.assertIn("heap-buffer-overflow", repair_prompt)
        self.assertIn("must not be modified", repair_prompt)

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
    def test_source_bootstrap_interface_parser_handles_template_parameters(self):
        declaration = (
            'template <typename T> struct stream {}; '
            'void process_top_hls('
            'stream<stream<int> > &input, int *out);'
        )
        self.assertEqual(
            _public_candidate_parameter_names(declaration, CANDIDATE_NAME),
            ("input", "out"),
        )
    def test_repair_contract_defers_external_helper_to_preflight(self):
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
        self.assertEqual(issues, ())

    def test_testbench_contract_defers_candidate_definition_to_linker(self):
        tb_optimizer.validate_testbench_top_contract(
            "void process_top();\n"
            "void process_top_hls(){}\n"
            "int main(){process_top_hls();return 0;}\n",
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )

    def test_testbench_contract_defers_original_definition_to_linker(self):
        tb_optimizer.validate_testbench_top_contract(
            "void process_top(){}\n"
            "void process_top_hls();\n"
            "int main(){process_top_hls();return 0;}\n",
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )

    def test_testbench_contract_defers_external_helper_to_preflight(self):
        tb_optimizer.validate_testbench_top_contract(
            "void process_top();\n"
            "void process_top_hls();\n"
            "void internal_reset();\n"
            "int main(){process_top_hls();return 0;}\n",
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )

    def test_testbench_contract_allows_local_constructor_syntax(self):
        tb_optimizer.validate_testbench_top_contract(
            "#include <string>\n"
            "void process_top();\n"
            "void process_top_hls();\n"
            "int main(){std::string qry(255, 'a');process_top_hls();return 0;}\n",
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )

    def test_testbench_contract_defers_weak_main_to_compiler(self):
        code = (
            "void process_top();\n"
            "void process_top_hls();\n"
            "__attribute__((weak)) int main(){"
            "process_top();process_top_hls();return 0;}\n"
        )
        tb_optimizer.validate_testbench_top_contract(
            code,
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )
        repair_contract = model_testbench_repairer.TestbenchRepairContract(
            required_top_function_names=(ORIGINAL_NAME, CANDIDATE_NAME)
        )
        self.assertEqual(repair_contract.validate(code), ())

    def test_testbench_contract_ignores_comments_and_disabled_code(self):
        code = (
            "void process_top();\n"
            "void process_top_hls();\n"
            "/* void commented_helper(); */\n"
            "#if 0\nvoid disabled_helper();\n#endif\n"
            "int main(){process_top();process_top_hls();return 0;}\n"
        )
        tb_optimizer.validate_testbench_top_contract(
            code, ORIGINAL_NAME, CANDIDATE_NAME,
        )

    def test_testbench_contract_defers_process_control_to_real_tools(self):
        tb_optimizer.validate_testbench_top_contract(
            "void process_top();\nvoid process_top_hls();\n"
            "int main(){process_top();if(fork()==0)process_top_hls();return 0;}\n",
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )

    def test_testbench_contract_ignores_process_words_in_text(self):
        tb_optimizer.validate_testbench_top_contract(
            "void process_top();\nvoid process_top_hls();\n"
            "// fork();\n"
            "int main(){const char *message=\"system()\";"
            "process_top();process_top_hls();return message[0]==0;}\n",
            ORIGINAL_NAME,
            CANDIDATE_NAME,
        )

    def test_repair_contract_allows_local_constructor_and_ignores_comments(self):
        contract = model_testbench_repairer.TestbenchRepairContract(
            required_top_function_names=(ORIGINAL_NAME, CANDIDATE_NAME),
        )
        code = (
            "#include <string>\n"
            "void process_top();\nvoid process_top_hls();\n"
            "/* void commented_helper(); */\n"
            "int main(){std::string qry(255, 'a');"
            "process_top();process_top_hls();return 0;}\n"
        )
        self.assertEqual(contract.validate(code), ())

    def test_repair_contract_defers_process_control_to_real_tools(self):
        contract = model_testbench_repairer.TestbenchRepairContract(
            required_top_function_names=(ORIGINAL_NAME, CANDIDATE_NAME),
        )
        code = (
            "void process_top();\nvoid process_top_hls();\n"
            "int main(){process_top();if(fork()==0)process_top_hls();return 0;}\n"
        )
        self.assertEqual(contract.validate(code), ())

    def test_stub_contract_defers_main_to_linker(self):
        tb_optimizer.validate_stub_contract(
            STUB + "int main(){return 0;}\n",
            original_name=ORIGINAL_NAME,
            candidate_name=CANDIDATE_NAME,
            frozen_hls_decl=FROZEN_DECL,
        )

    def test_stub_contract_defers_abi_drift_to_linker(self):
        tb_optimizer.validate_stub_contract(
            "int process_top_hls(int x){return x;}\n",
            original_name=ORIGINAL_NAME,
            candidate_name=CANDIDATE_NAME,
            frozen_hls_decl=FROZEN_DECL,
        )


class StepDErrorOwnershipTests(unittest.TestCase):
    def test_compile_diagnostic_owns_testbench_error(self):
        owner = tb_coverage._classify_compile_failure_owner(
            "generated_driver.cc:12:3: error: missing symbol",
            {"generated_driver.cc": "testbench"},
        )
        self.assertEqual(owner, "testbench")

    def test_unstructured_golden_text_keeps_ownership_unknown(self):
        owner, action = tb_coverage._classify_run_failure_owner(
            "[TB] WARNING: the golden reference left the fallback port unwritten.\n"
            "[TB] FAIL fallback[0]: golden=-999 candidate=0\n"
        )
        self.assertEqual(owner, "unknown")
        self.assertEqual(action, "review_unknown")

    def test_compile_diagnostic_owns_stub_error(self):
        owner = tb_coverage._classify_compile_failure_owner(
            "temporary_impl.cc:4:7: error: bad return",
            {"temporary_impl.cc": "stub"},
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
                side_effect=[TB_ONE, STUB, STUB, STUB, STUB, STUB],
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
            ["testbench", "stub", "empty_stub", "empty_stub", "empty_stub", "empty_stub"],
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


class HiddenQualificationExceptionArtifactsTests(unittest.TestCase):
    def test_frozen_abi_probe_can_be_repaired_without_rewriting_testbench(self):
        first_probe = "void process_top_hls(){}"
        second_probe = "void process_top_hls(){volatile int observed=1;}"
        rounds = [{"status": "ok", "cov_pct": 100.0, "round": 1, "tb_code": TB_ONE, "stub_code": STUB, "frozen_public_hls_decl": FROZEN_DECL}]
        with patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=[first_probe, second_probe]) as request, patch.object(tb_optimizer, "_synth_check", side_effect=[(False, "unused port marker"), (True, "")]):
            result = tb_optimizer._finalize_trajectory(
                object(), rounds, False, 0,
                expected_hls_name=CANDIDATE_NAME, orig_code="void process_top(){}",
                emit_final_text=False, allow_abi_correction=False, synth_retry_budget=3,
            )
        self.assertTrue(result["synth_ok"])
        self.assertEqual(result["frozen_public_hls_decl"], FROZEN_DECL)
        self.assertEqual(result["best_tb"], TB_ONE)
        self.assertEqual([call.kwargs["artifact_kind"] for call in request.call_args_list], ["empty_stub", "empty_stub"])
        prompt = request.call_args_list[1].args[1]
        self.assertIn("unused port marker", prompt)
        self.assertIn(first_probe, prompt)

    def test_empty_stub_synthesis_uses_shared_default_timeout(self):
        from agrefactor.config.tool_timeouts import DEFAULT_CSYNTH_TIMEOUT_S
        with tempfile.TemporaryDirectory() as root, patch.object(tb_optimizer.tools.csynth, "run_csynth", return_value=("succeeded", "")) as synth:
            passed, _ = tb_optimizer._synth_check("void trial(){}", "trial", root)
        self.assertTrue(passed)
        self.assertEqual(synth.call_args.kwargs["timelimit"], DEFAULT_CSYNTH_TIMEOUT_S)
        self.assertEqual(DEFAULT_CSYNTH_TIMEOUT_S, 1200)

    def test_hidden_qualification_retains_exception_chain_only_for_operator(self):
        def fail(**kwargs):
            try:
                raise ValueError("held-out diagnostic marker")
            except ValueError as exc:
                raise RuntimeError("generation failed") from exc

        with tempfile.TemporaryDirectory() as root, patch.object(tb_optimizer, "run_trajectory", side_effect=fail):
            with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
                tb_optimizer.make_golden_hidden_tb(
                    "void source_top(){}", "source_top", "void trial_top();",
                    M=1, K=1, artifact_root=root,
                )
            artifact = json.loads((Path(root) / "trajectory_000/exception.json").read_text())
            self.assertEqual(artifact["evidence_view"], "operator_full")
            self.assertEqual(artifact["error_type"], "RuntimeError")
            self.assertIn("held-out diagnostic marker", artifact["traceback"])
            self.assertNotIn("held-out diagnostic marker", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
