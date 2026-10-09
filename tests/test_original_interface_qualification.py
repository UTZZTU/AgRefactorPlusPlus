from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agrefactor.cpp_interface import extract_top_interface, interfaces_equivalent
from flow.tools import tb_coverage, tb_optimizer


class SemanticOriginalInterfaceTests(unittest.TestCase):
    def interface(self, source, name="transform", **context):
        value = extract_top_interface(
            source, name, require_definition=False, **context
        )
        self.assertIsNotNone(value)
        self.assertTrue(value.canonical_function_type)
        return value

    def equivalent(self, expected, observed):
        return interfaces_equivalent(
            self.interface(expected), self.interface(observed),
            require_parameter_names=False, semantic_types=True,
        )

    def test_typedef_return_alias_and_parameter_rename_are_equivalent(self):
        left = self.interface(
            "typedef long result_t; typedef int row_t[13]; "
            "result_t transform(row_t *values);"
        )
        right = self.interface("long transform(int (*other)[13]);")
        self.assertTrue(interfaces_equivalent(
            left, right, require_parameter_names=False, semantic_types=True,
        ))
        self.assertFalse(interfaces_equivalent(left, right))

    def test_parameter_names_remain_strict_by_default(self):
        left = self.interface("void transform(int *input);")
        right = self.interface("void transform(int *renamed);")
        self.assertFalse(interfaces_equivalent(left, right))
        self.assertTrue(interfaces_equivalent(
            left, right, require_parameter_names=False, semantic_types=True,
        ))

    def test_legal_array_parameter_decay_is_equivalent(self):
        for left, right in (
            ("int input[17]", "int *renamed"),
            ("int input[3][7]", "int (*renamed)[7]"),
            ("int input[3][7]", "int renamed[29][7]"),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(self.equivalent(
                    f"void transform({left});", f"void transform({right});"
                ))

    def test_pointer_to_array_mismatch_is_dimension_independent(self):
        for dimensions in ((2, 7), (3, 5, 9), (6, 11)):
            suffix = "".join(f"[{value}]" for value in dimensions)
            with self.subTest(dimensions=dimensions):
                self.assertFalse(self.equivalent(
                    f"typedef unsigned char tile_t{suffix}; "
                    "void transform(tile_t *values);",
                    f"void transform(unsigned char values{suffix});",
                ))

    def test_reference_typedef_matches_but_pointer_does_not(self):
        definition = (
            "typedef short block_t[5][9]; void transform(block_t &values);"
        )
        self.assertTrue(self.equivalent(
            definition, "void transform(short (&renamed)[5][9]);"
        ))
        self.assertFalse(self.equivalent(
            definition, "void transform(short (*renamed)[9]);"
        ))

    def test_language_linkage_is_part_of_semantic_contract(self):
        self.assertFalse(self.equivalent(
            'extern "C" void transform(int *values);',
            "void transform(int *values);",
        ))
        self.assertTrue(self.equivalent(
            'extern "C" void transform(int *values);',
            'extern "C" { void transform(int *renamed); }',
        ))

    def test_variadic_and_fixed_function_types_are_different(self):
        self.assertFalse(self.equivalent(
            "int transform(int count, ...);", "int transform(int count);"
        ))

    def test_definition_inherits_compiler_c_linkage_from_prior_declaration(self):
        left = extract_top_interface(
            'extern "C" void transform(int *values); '
            "void transform(int *values) { *values = 0; }",
            "transform",
        )
        right = self.interface('extern "C" void transform(int *values);')
        self.assertIsNotNone(left)
        self.assertTrue(interfaces_equivalent(
            left, right, require_parameter_names=False, semantic_types=True,
        ))
        self.assertEqual(left.linker_symbol, right.linker_symbol)

    def test_equal_function_types_in_different_namespaces_have_different_symbols(self):
        self.assertFalse(self.equivalent(
            "namespace implementation { void transform(int *values); }",
            "void transform(int *values);",
        ))


class OriginalInterfaceQualificationTests(unittest.TestCase):
    def qualify(self, original, testbench, **context):
        return tb_coverage.check_original_execution(
            original, testbench, "void candidate();", "candidate",
            original_name="operate", **context,
        )

    def assert_testbench_mismatch(self, result, status):
        self.assertEqual(result["status"], status)
        self.assertEqual(result["failure_owner"], "testbench")
        self.assertEqual(result["owner_authority"], "original_interface_differential")
        self.assertEqual(result["next_action"], "repair_testbench")
        self.assertTrue(result["evidence_complete"])
        evidence = result["original_interface_evidence"]
        self.assertEqual(evidence["status"], "mismatch")
        self.assertEqual(evidence["expected"]["source_role"], "original")
        self.assertEqual(evidence["observed"]["source_role"], "testbench")
        self.assertTrue(evidence["expected"]["declaration"])
        self.assertTrue(evidence["observed"]["declaration"])
        self.assertTrue(evidence["differences"])
        self.assertTrue(result["executions"])
        self.assertTrue(all(
            item["component"] == "compile" for item in result["executions"]
        ))

    def test_real_link_failure_with_wrong_array_pointer_repairs_testbench(self):
        result = self.qualify(
            "typedef double block_t[3][7]; "
            "void operate(block_t *values) { (*values)[0][0] = 1; }",
            "void operate(double values[3][7]); void candidate(); "
            "int main() { double values[3][7] = {}; operate(values); return 0; }",
        )
        self.assert_testbench_mismatch(result, "compile_failed")
        self.assertIn("undefined reference", result["compile_stderr"])
        self.assertTrue(all(
            item["returncode"] != 0 for item in result["executions"]
        ))

    def test_successful_c_link_cannot_hide_parameter_type_mismatch(self):
        result = self.qualify(
            'extern "C" void operate(int *values) { *values = 1; }',
            'extern "C" void operate(double *values); void candidate(); '
            "int main() { double values = 0; operate(&values); return 0; }",
        )
        self.assert_testbench_mismatch(result, "contract_failed")
        self.assertEqual(result["executions"][-1]["returncode"], 0)

    def test_successful_cpp_link_cannot_hide_return_type_mismatch(self):
        result = self.qualify(
            "short operate(int value) { return value; }",
            "double operate(int value); void candidate(); "
            "int main() { return operate(0) != 0; }",
        )
        self.assert_testbench_mismatch(result, "contract_failed")
        self.assertEqual(result["executions"][-1]["returncode"], 0)

    def test_equivalent_aliases_and_renamed_parameters_complete_runtime(self):
        result = self.qualify(
            "typedef long result_t; typedef int block_t[13]; "
            "result_t operate(block_t *values) { return (*values)[0]; }",
            "long operate(int (*renamed)[13]); void candidate(); "
            "int main() { int values[13] = {}; return operate(&values); }",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["failure_owner"], "none")
        self.assertEqual(result["original_interface_evidence"]["status"], "equivalent")
        self.assertIn("runtime", [item["component"] for item in result["executions"]])

    def test_source_root_headers_and_compile_flags_apply_to_both_interfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "types.hpp").write_text(
                "typedef int block_t[EXTENT];\n", encoding="utf-8"
            )
            result = self.qualify(
                '#include "types.hpp"\n'
                "void operate(block_t *values) { (*values)[0] = 0; }",
                '#include "types.hpp"\n'
                "void operate(int (*renamed)[EXTENT]); void candidate(); "
                "int main() { block_t values = {}; operate(&values); return 0; }",
                source_root=directory, compile_flags=("-DEXTENT=19",),
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["original_interface_evidence"]["status"], "equivalent")

    def test_original_definition_with_inherited_c_linkage_qualifies(self):
        result = self.qualify(
            'extern "C" void operate(int *values); '
            "void operate(int *values) { *values = 0; }",
            'extern "C" void operate(int *renamed); void candidate(); '
            "int main() { int value = 0; operate(&value); return 0; }",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["original_interface_evidence"]["status"], "equivalent")

    def test_c_linkage_inherited_from_source_root_header_qualifies(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "entry.hpp").write_text(
                'extern "C" void operate(int *values);\n', encoding="utf-8"
            )
            result = self.qualify(
                '#include "entry.hpp"\n'
                "void operate(int *values) { *values = 0; }",
                '#include "entry.hpp"\n'
                "void operate(int *renamed); void candidate(); "
                "int main() { int value = 0; operate(&value); return 0; }",
                source_root=directory,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["original_interface_evidence"]["status"], "equivalent")

    def test_global_declaration_for_namespace_original_is_linker_mismatch(self):
        result = self.qualify(
            "namespace implementation { void operate(int *values) { *values = 0; } }",
            "void operate(int *values); void candidate(); "
            "int main() { int value = 0; operate(&value); return 0; }",
        )
        self.assert_testbench_mismatch(result, "compile_failed")
        self.assertIn("linker_symbol", result["original_interface_evidence"]["differences"])

    def test_unrelated_link_failure_is_not_testbench_interface_ownership(self):
        result = self.qualify(
            "void operate(int *values) { *values = 0; }",
            "void operate(int *values); void candidate(); void unavailable(); "
            "int main() { int value = 0; operate(&value); unavailable(); return 0; }",
        )
        self.assertEqual(result["status"], "compile_failed")
        self.assertEqual(result["original_interface_evidence"]["status"], "equivalent")
        self.assertEqual(result["failure_owner"], "unknown")
        self.assertEqual(result["next_action"], "review_unknown")

    def test_proven_interface_defect_does_not_erase_independent_link_failure(self):
        result = self.qualify(
            "int unavailable(); int operate(int value) { return unavailable() + value; }",
            "double operate(int value); void candidate(); "
            "int main() { return operate(0) != 0; }",
        )
        self.assert_testbench_mismatch(result, "compile_failed")
        self.assertIn("unavailable", result["compile_stderr"])
        self.assertIn("undefined reference", result["compile_stderr"])
        self.assertIn("requalify", result["ownership_evidence"]["reason"])

    def test_compiler_signal_cannot_grant_interface_repair(self):
        real_run = subprocess.run

        def run(command, **kwargs):
            if "-o" in command and "original_only" in command:
                return subprocess.CompletedProcess(
                    command, -11, stdout="", stderr="undefined reference to unavailable()"
                )
            return real_run(command, **kwargs)

        with patch.object(tb_coverage.subprocess, "run", side_effect=run):
            result = self.qualify(
                "int operate(int value) { return value; }",
                "double operate(int value); void candidate(); "
                "int main() { return operate(0) != 0; }",
            )
        self.assertEqual(result["original_interface_evidence"]["status"], "mismatch")
        self.assertEqual(result["status"], "compile_failed")
        self.assertEqual(result["failure_owner"], "unknown")
        self.assertEqual(result["next_action"], "review_unknown")
        self.assertFalse(result["evidence_complete"])
        self.assertTrue(all(item["returncode"] == -11 for item in result["executions"]))

    def test_unavailable_ast_does_not_create_static_rejection_gate(self):
        def extract(source, name, **context):
            return None if name == "operate" else extract_top_interface(source, name, **context)

        with patch.object(tb_coverage, "extract_top_interface", side_effect=extract):
            result = self.qualify(
                "void operate(int *values) { *values = 0; }",
                "void operate(double *values); void candidate(); "
                "int main() { double value = 0; operate(&value); return 0; }",
            )
        self.assertEqual(result["status"], "compile_failed")
        self.assertEqual(result["original_interface_evidence"]["status"], "unknown")
        self.assertEqual(result["failure_owner"], "unknown")
        self.assertEqual(result["next_action"], "review_unknown")
        self.assertTrue(result["executions"])

    def test_ambiguous_original_overloads_defer_to_real_compile_and_runtime(self):
        result = self.qualify(
            "void operate(int *values) { *values = 0; } "
            "void operate(double *values) { *values = 0; }",
            "void operate(int *values); void candidate(); "
            "int main() { int value = 0; operate(&value); return 0; }",
        )
        self.assertEqual(result["original_interface_evidence"]["status"], "unknown")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["failure_owner"], "none")
        self.assertIn("runtime", [item["component"] for item in result["executions"]])

    def test_unresolved_return_type_keeps_real_original_compile_ownership(self):
        original = "unresolved_result operate(int *values) { return {}; }"
        self.assertIsNone(extract_top_interface(original, "operate"))
        result = self.qualify(
            original,
            "int operate(int *values); void candidate(); "
            "int main() { int value = 0; return operate(&value); }",
        )
        self.assertEqual(result["original_interface_evidence"]["status"], "unknown")
        self.assertEqual(result["status"], "compile_failed")
        self.assertEqual(result["failure_owner"], "original")
        self.assertEqual(result["next_action"], "review_original")

    def test_nonlink_compile_provenance_is_not_overridden_by_interface_mismatch(self):
        result = self.qualify(
            "void operate(int *values) { nonexistent_original_symbol(); }",
            "void operate(double *values); void candidate(); "
            "int main() { double value = 0; operate(&value); return 0; }",
        )
        self.assertEqual(result["status"], "compile_failed")
        self.assertEqual(result["failure_owner"], "original")
        self.assertNotEqual(result["owner_authority"], "original_interface_differential")
        self.assertEqual(result["next_action"], "review_original")


class HiddenOriginalInterfaceRepairLoopTests(unittest.TestCase):
    ORIGINAL = "void operate(int *values) { *values = 0; }"
    PINNED = "void operate_hls(int *values);"
    TB = (
        "void operate(int *values); void operate_hls(int *values); "
        "int main() { int value = 0; operate(&value); operate_hls(&value); return 0; }"
    )
    STUB = "void operate_hls(int *values) { *values = 0; }"
    DOMAIN = {"n": {"minimum": 1, "maximum": 19}}

    @staticmethod
    def failure(index=1):
        return {
            "status": "compile_failed", "failure_owner": "testbench",
            "next_action": "repair_testbench", "evidence_complete": True,
            "owner_authority": "original_interface_differential",
            "compile_stderr": f"interface failure evidence {index}",
            "original_interface_evidence": {
                "status": "mismatch",
                "expected": {"declaration": "void operate(int *values)",
                             "canonical_function_type": "void (int *)"},
                "observed": {"declaration": f"void operate(double *bad_{index})",
                             "canonical_function_type": "void (double *)"},
                "differences": ["canonical_function_type"],
            },
        }

    def run_loop(self, *, max_repairs=3, K=1, initial_error=False, passed_after=None):
        loader = Mock()
        loader.load_agent.return_value = object()
        request_count = 0

        def artifact(_agent, _message, **kwargs):
            nonlocal request_count
            request_count += 1
            if kwargs.get("on_event"):
                kwargs["on_event"]("request")
                kwargs["on_event"]("response")
            return self.TB if kwargs["artifact_kind"] == "testbench" else self.STUB

        measurements = 0

        def qualification(*_args, **_kwargs):
            nonlocal measurements
            measurements += 1
            if passed_after == measurements:
                return {
                    "status": "ok", "cov_pct": 100.0, "lines_total": 1,
                    "lines_hit": 1, "uncovered_lines": [],
                }
            return self.failure(measurements)

        initial_error_raised = False

        def validate_abi(source, *_args, **_kwargs):
            nonlocal initial_error_raised
            # The Public frozen declaration is checked before generation; the
            # defect in this scenario belongs only to the generated artifact.
            if initial_error and source == self.TB and not initial_error_raised:
                initial_error_raised = True
                raise tb_optimizer.ModelArtifactError("initial frozen ABI mismatch")

        with ExitStack() as stack:
            stack.enter_context(patch.object(tb_optimizer, "HLSAgentLoader", return_value=loader))
            requests = stack.enter_context(patch.object(
                tb_optimizer, "_request_cpp_artifact", side_effect=artifact
            ))
            measure = stack.enter_context(patch.object(
                tb_optimizer, "_measure_qualified_coverage", side_effect=qualification
            ))
            stack.enter_context(patch.object(
                tb_optimizer, "_validate_frozen_candidate_abi", side_effect=validate_abi
            ))
            stack.enter_context(patch.object(
                tb_optimizer, "_freeze_public_contract", return_value=(self.PINNED, ())
            ))
            stack.enter_context(patch.object(tb_optimizer, "_synth_check", return_value=(True, "")))
            result = tb_optimizer.run_trajectory(
                self.ORIGINAL, "operate", K=K, target_pct=100.0,
                llm_config=None, want_sig_spec=False, pinned_hls_decl=self.PINNED,
                hidden_generation=True, emit_final_text=False,
                input_domain_contract=self.DOMAIN, max_repairs=max_repairs,
            )
        return result, requests, measure

    def test_three_repairs_share_one_budget_even_for_larger_K(self):
        result, requests, measure = self.run_loop(max_repairs=3, K=6)
        self.assertEqual(measure.call_count, 4)
        self.assertEqual(len(result["rounds"]), 4)
        self.assertFalse(result["qualified"])
        messages = [call.args[1] for call in requests.call_args_list
                    if call.kwargs["artifact_kind"] == "testbench"]
        self.assertEqual(len(messages), 4)
        for message in messages[1:]:
            self.assertIn("COMPILER-RESOLVED ORIGINAL INTERFACE DIFFERENTIAL", message)
            self.assertIn("void operate(int *values)", message)
            self.assertIn(self.PINNED, message)
            self.assertIn("FROZEN LEGAL INPUT-DOMAIN CONTRACT", message)
            self.assertIn("must not be passed to Candidate repair", message)
        self.assertIn("interface failure evidence 1", messages[-1])
        self.assertIn("interface failure evidence 2", messages[-1])
        self.assertIn("interface failure evidence 3", messages[-1])

    def test_initial_contract_correction_consumes_existing_budget(self):
        result, requests, measure = self.run_loop(max_repairs=3, initial_error=True)
        messages = [call for call in requests.call_args_list
                    if call.kwargs["artifact_kind"] == "testbench"]
        self.assertEqual(len(messages), 4)
        self.assertEqual(measure.call_count, 3)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 3)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["format_retries"], 0)
        self.assertIn("initial frozen ABI mismatch", messages[1].args[1])
        self.assertFalse(result["qualified"])
        self.assertIn("initial frozen ABI mismatch", messages[-1].args[1])

    def test_initial_contract_failure_remains_in_history_after_successful_round(self):
        history = tb_optimizer._failure_history([{
            "round": 1, "status": "ok",
            "initial_testbench_contract_error": "initial frozen ABI mismatch",
            "initial_testbench_contract_repaired": True,
        }])
        self.assertIn("initial frozen ABI mismatch", history)

    def test_zero_repair_budget_does_not_retry_initial_contract_failure(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            self.run_loop(max_repairs=0, initial_error=True)

    def test_corrected_generation_requalifies_then_can_pass(self):
        result, _requests, measure = self.run_loop(passed_after=3)
        self.assertEqual(measure.call_count, 3)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["rounds"][1]["failure_owner"], "testbench")
        self.assertEqual(result["rounds"][2]["status"], "ok")
        self.assertTrue(all(call.kwargs["require_original_execution"]
                            for call in measure.call_args_list))

    def test_hidden_interface_details_remain_operator_only_on_exhaustion(self):
        private = "PRIVATE_HELD_OUT_INTERFACE_DETAIL"
        failure = self.failure()
        failure["compile_stderr"] = private
        failure["original_interface_evidence"]["observed"]["declaration"] = private
        error = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden", stage="hidden_generation_qualification",
            trajectories=[{"rounds": [failure], "best_tb": private,
                           "qualified": False, "trajectory_status": "coverage_failed"}],
        )
        payload = error.to_dict()
        self.assertNotIn(private, json.dumps(payload))
        self.assertFalse(payload["hidden_testbench_exposed_to_model"])


if __name__ == "__main__":
    unittest.main()
