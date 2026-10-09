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

    def test_cpp_structure_is_deferred_to_compiler(self):
        self.assertIn(
            "void process_top_hls();",
            tb_optimizer._extract_one_cpp_block(
                "```cpp\nvoid process_top_hls();\n```",
                artifact_kind="testbench",
                required_symbol="process_top_hls",
            ),
        )
        self.assertIn(
            "void process_top_hls();",
            tb_optimizer._extract_one_cpp_block(
                "```cpp\nvoid process_top_hls();\n```",
                artifact_kind="stub",
                required_symbol="process_top_hls",
            ),
        )



class FrozenLinkageTests(unittest.TestCase):
    def test_candidate_declaration_keeps_extern_c(self):
        declaration = tb_optimizer.extract_hls_decl_from_testbench(
            'extern "C" int process_top_hls(int value);',
            "process_top_hls",
        )
        self.assertEqual(
            declaration,
            'extern "C" int process_top_hls(int value);',
        )

    def test_original_declaration_linkage_mismatch_is_explicit(self):
        with self.assertRaisesRegex(tb_optimizer.ModelArtifactError, "language linkage"):
            tb_optimizer._validate_original_language_linkage(
                "int process_top(int value);",
                'extern "C" int process_top(int value) { return value; }',
                "process_top",
            )


class PublicTypeContractTests(unittest.TestCase):
    public = (
        "struct Payload { int values[3]; unsigned tag; };\n"
        "void process_top_hls(Payload payload);\n"
    )

    def freeze(self):
        return tb_optimizer.freeze_public_type_contract(self.public, "process_top_hls")

    def test_independent_public_facts_reject_same_named_changed_layout(self):
        frozen = self.freeze()
        changed = self.public.replace("int values[3]", "int *values")
        with self.assertRaisesRegex(tb_optimizer.ModelArtifactError, "type layout"):
            tb_optimizer._validate_frozen_candidate_abi(
                changed, "process_top_hls", "void process_top_hls(Payload payload);",
                frozen_type_contract=frozen,
            )

    def test_identical_sizes_do_not_hide_field_order_changes(self):
        frozen = self.freeze()
        changed = self.public.replace(
            "int values[3]; unsigned tag;", "unsigned tag; int values[3];",
        )
        with self.assertRaisesRegex(tb_optimizer.ModelArtifactError, "type layout"):
            tb_optimizer.validate_frozen_type_contract(changed, "process_top_hls", frozen)

    def test_format_and_unrelated_types_do_not_change_contract(self):
        frozen = self.freeze()
        equivalent = (
            "struct Unused { double private_values[29]; };\n"
            "struct Payload{int values[3];unsigned tag;};\n"
            "void process_top_hls( Payload payload );\n"
            "int main() { int inputs[19] = {}; return inputs[0]; }\n"
        )
        tb_optimizer._validate_frozen_candidate_abi(
            equivalent, "process_top_hls", "void process_top_hls(Payload payload);",
            frozen_type_contract=frozen,
        )
        self.assertNotIn("Unused", frozen["declaration_context"])
        self.assertNotIn("inputs", frozen["declaration_context"])

    def test_stub_and_probe_must_keep_public_type_facts(self):
        frozen = self.freeze()
        valid = self.public.replace("payload);", "payload) {}")
        tb_optimizer.validate_stub_contract(
            valid, original_name="process_top", candidate_name="process_top_hls",
            frozen_hls_decl="void process_top_hls(Payload payload);",
            frozen_type_contract=frozen,
        )
        for fragment in ("int values[5]", "int *values"):
            with self.subTest(fragment=fragment):
                with self.assertRaisesRegex(tb_optimizer.ModelArtifactError, "type layout"):
                    tb_optimizer.validate_stub_contract(
                        valid.replace("int values[3]", fragment),
                        original_name="process_top", candidate_name="process_top_hls",
                        frozen_hls_decl="void process_top_hls(Payload payload);",
                        frozen_type_contract=frozen,
                    )

    def test_probe_drift_repairs_probe_before_running_synthesis(self):
        frozen = self.freeze()
        valid = self.public.replace("payload);", "payload) {}")
        bad = valid.replace("int values[3]", "int *values")
        record = {
            "round": 1, "status": "ok", "cov_pct": 100.0,
            "tb_code": self.public, "stub_code": valid,
            "frozen_public_hls_decl": "void process_top_hls(Payload payload);",
            "frozen_public_type_contract": frozen,
        }
        with patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=[bad, valid]) as request, patch.object(
            tb_optimizer, "_synth_check", return_value=(True, ""),
        ) as synth:
            result = tb_optimizer._finalize_trajectory(
                Mock(), [record], False, 0, expected_hls_name="process_top_hls",
                orig_code="void process_top() {}", emit_final_text=False,
                synth_retry_budget=1,
            )
        self.assertTrue(result["qualified"])
        self.assertEqual(synth.call_count, 1)
        self.assertEqual([call.kwargs["artifact_kind"] for call in request.call_args_list], ["empty_stub", "empty_stub"])
        self.assertIn("FROZEN PUBLIC CANDIDATE TYPE CONTRACT", request.call_args_list[0].args[1])

    def test_cache_requires_matching_public_type_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            tb_optimizer._write_golden_cache(directory, "test", {"orig_sha256": "orig"})
            self.assertIsNone(tb_optimizer._load_golden_cache(
                directory, "test", "orig", expected_type_contract_sha="types",
            ))
            tb_optimizer._write_golden_cache(directory, "test", {
                "orig_sha256": "orig", "public_type_contract_sha256": "types",
            })
            self.assertIsNotNone(tb_optimizer._load_golden_cache(
                directory, "test", "orig", expected_type_contract_sha="types",
            ))

    def test_hidden_request_receives_public_types_and_repairs_layout_drift(self):
        frozen = self.freeze()
        valid_tb = self.public + "void process_top(Payload payload); int main(){return 0;}"
        bad_tb = valid_tb.replace("int values[3]", "int *values")
        stub = self.public.replace("payload);", "payload) {}")
        success = {"status": "ok", "cov_pct": 100.0, "lines_total": 1, "lines_hit": 1}
        responses = [Mock(messages=[{"content": "```cpp\n" + code + "\n```"}])
                     for code in [bad_tb, valid_tb, stub, stub]]
        agent = Mock()
        agent.run.side_effect = responses
        with patch.object(tb_optimizer, "HLSAgentLoader") as loader, patch.object(
            tb_optimizer, "_measure_qualified_coverage", return_value=success,
        ), patch.object(tb_optimizer, "_synth_check", return_value=(True, "")):
            loader.return_value.load_agent.return_value = agent
            result = tb_optimizer.run_trajectory(
                "void process_top() {}", "process_top", 1, 100.0, None, False,
                pinned_hls_decl="void process_top_hls(Payload payload);",
                pinned_type_contract=frozen, hidden_generation=True, emit_final_text=False,
            )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["artifact_response_attempts"], 2)
        self.assertEqual(result["artifact_contract_checks"], 2)
        self.assertEqual(result["artifact_response_retries"], 1)
        self.assertEqual(result["qualification_checks_used"], 2)
        self.assertEqual(result["repairs_used"], 1)
        self.assertIn(frozen["declaration_context"], agent.run.call_args_list[0].kwargs["message"])
        self.assertIn("type layout", agent.run.call_args_list[1].kwargs["message"])

    def test_header_parse_failure_is_preserved_without_repairing_environment(self):
        frozen = self.freeze()
        invalid = {
            "status": "unknown", "types": [], "unresolved": ["Payload"],
            "parse_context": {"source_path": "testbench.cpp"},
            "diagnostics": [{"severity": 3, "file": "/vendor/header.hpp", "message": "unknown declaration"}],
        }
        valid_tb = self.public + "void process_top(Payload payload); int main(){return 0;}"
        with tempfile.TemporaryDirectory() as directory, patch.object(
            tb_optimizer, "HLSAgentLoader",
        ) as loader, patch.object(
            tb_optimizer, "_request_cpp_artifact", return_value=valid_tb,
        ) as request, patch.object(
            tb_optimizer, "extract_top_type_contract", return_value=invalid,
        ):
            loader.return_value.load_agent.return_value = Mock()
            with self.assertRaisesRegex(tb_optimizer.ModelArtifactError, "type contract is incomplete"):
                tb_optimizer.run_trajectory(
                    "void process_top() {}", "process_top", 1, 100.0, None, False,
                    pinned_hls_decl="void process_top_hls(Payload payload);",
                    pinned_type_contract=frozen, hidden_generation=True,
                    emit_final_text=False, artifact_root=directory,
                )
            self.assertEqual(request.call_count, 0)
            saved = json.loads((Path(directory) / "trajectory_000/type_contract_unverified.json").read_text())
            self.assertEqual(saved["diagnostics"][0]["file"], "/vendor/header.hpp")

    def test_exhaustion_uses_actual_mapping_checks_and_requests(self):
        failure = tb_optimizer.TestbenchGenerationExhausted(
            split="public", stage="public_mapping_qualification",
            trajectories=[{
                "trajectory_status": "contract_failed", "rounds": [{"status": "contract_failed"}],
                "qualification_checks_used": 4, "repairs_used": 3,
                "mapping_repair_requests_used": 3,
            }],
        ).to_dict()
        self.assertEqual(failure["attempt_count"], 4)
        self.assertEqual(failure["repair_attempt_count"], 3)
        self.assertEqual(failure["counter_source"], "explicit")
        self.assertEqual(failure["trajectory_summaries"][0]["mapping_repair_requests_used"], 3)

class FrozenAbiAndRepairLimitTests(unittest.TestCase):
    declaration = "int process_top_hls(int value);"
    testbench = (
        "int process_top(int value); int process_top_hls(int value); "
        "int main(){return process_top(1) != process_top_hls(1);}"
    )
    stub = "int process_top(int value); int process_top_hls(int value){return process_top(value);}"
    probe = "int process_top_hls(int value){return value;}"
    original = "int process_top(int value){return value;}"
    passed = {"status": "ok", "cov_pct": 100.0, "lines_total": 1, "lines_hit": 1}
    failed = {
        "status": "original_run_failed", "failure_owner": "testbench",
        "next_action": "repair_testbench", "run_stderr": "qualification runtime evidence",
    }

    def run_artifacts(self, artifacts, coverage, **kwargs):
        agent = Mock()
        agent.run.side_effect = [Mock(messages=[{"content": "```cpp\n" + code + "\n```"}])
                                 for code in artifacts]
        with patch.object(tb_optimizer, "HLSAgentLoader") as loader, patch.object(
            tb_optimizer, "_measure_qualified_coverage", side_effect=coverage,
        ) as measure, patch.object(tb_optimizer, "_synth_check", return_value=(True, "")):
            loader.return_value.load_agent.return_value = agent
            result = tb_optimizer.run_trajectory(
                self.original, "process_top", kwargs.pop("K", 1), 100.0, None, False,
                pinned_hls_decl=self.declaration, emit_final_text=False, **kwargs,
            )
        return result, agent, measure

    def test_scalar_signature_macro_context_and_equivalent_declarations_use_compiler(self):
        public = "#define WIDTH 5\nvoid process_top_hls(int values[WIDTH]);"
        frozen = tb_optimizer.freeze_public_type_contract(public, "process_top_hls")
        self.assertEqual(frozen["types"], [])
        self.assertIn("#define WIDTH 5", frozen["declaration_context"])
        self.assertIn("#define WIDTH 5", tb_optimizer.frozen_type_instruction(frozen))
        tb_optimizer._validate_frozen_candidate_abi(
            public + "\nvoid process_top_hls(int values[WIDTH]);",
            "process_top_hls", "void process_top_hls(int values[WIDTH]);",
            frozen_type_contract=frozen,
        )

    def test_invalid_frozen_contract_cannot_borrow_generated_typedef_or_be_repaired(self):
        with self.assertRaises(tb_optimizer.ModelArtifactError) as caught:
            tb_optimizer._validate_frozen_candidate_abi(
                "typedef int Missing; int process_top_hls(Missing value);",
                "process_top_hls", "int process_top_hls(Missing value);",
            )
        self.assertFalse(caught.exception.repair_eligible)
        self.assertTrue(caught.exception.type_contract["diagnostics"])
        self.assertIn("unknown type name", str(caught.exception))

    def test_invalid_frozen_abi_stops_before_model_generation(self):
        with patch.object(tb_optimizer, "HLSAgentLoader") as loader, patch.object(
            tb_optimizer, "_request_cpp_artifact",
        ) as request, self.assertRaises(tb_optimizer.ModelArtifactError) as caught:
            loader.return_value.load_agent.return_value = Mock()
            tb_optimizer.run_trajectory(
                self.original, "process_top", 1, 100.0, None, False,
                pinned_hls_decl="int process_top_hls(Missing value);",
                hidden_generation=True, emit_final_text=False,
            )
        request.assert_not_called()
        self.assertFalse(caught.exception.repair_eligible)
        self.assertEqual(caught.exception.artifact_counts["repairs_used"], 0)

    def test_generated_missing_and_ambiguous_entry_have_repairable_compiler_facts(self):
        for source, status in (("int other();", "missing"), (
                "int process_top_hls(int value); int process_top_hls(float value);", "ambiguous")):
            with self.subTest(status=status), self.assertRaises(tb_optimizer.ModelArtifactError) as caught:
                tb_optimizer._validate_frozen_candidate_abi(source, "process_top_hls", self.declaration)
            self.assertTrue(caught.exception.repair_eligible)
            self.assertEqual(caught.exception.type_contract["status"], status)
            self.assertTrue(caught.exception.type_contract["diagnostics"])

    def test_abi_drift_repairs_in_existing_contract_loop(self):
        wrong = self.testbench.replace("process_top_hls(int value)", "process_top_hls(float value)")
        result, agent, measure = self.run_artifacts(
            [wrong, self.testbench, self.stub, self.probe], [self.passed], hidden_generation=True,
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 1)
        self.assertEqual(result["artifact_response_attempts"], 2)
        self.assertEqual(measure.call_count, 1)
        self.assertIn("changed the externally frozen", agent.run.call_args_list[1].kwargs["message"])

    def test_contract_and_coverage_repairs_share_actual_testbench_request_limit(self):
        wrong = self.testbench.replace("process_top_hls(int value)", "process_top_hls(float value)")
        result, agent, measure = self.run_artifacts(
            [wrong, self.testbench, self.stub, wrong, self.testbench, self.stub],
            [self.failed, self.failed], hidden_generation=True, max_repairs=3,
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(result["artifact_event_counts"]["testbench"]["requests"], 4)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 3)
        self.assertEqual(measure.call_count, 2)
        self.assertEqual(agent.run.call_count, 6)
        self.assertIn("qualification runtime evidence", agent.run.call_args_list[3].kwargs["message"])

    def test_all_inner_contract_failures_survive_later_coverage_repairs(self):
        wrong = [self.testbench.replace("process_top_hls(int value)",
                                       f"process_top_hls({kind} value)")
                 for kind in ("float", "double", "long")]
        errors = []
        for source in wrong:
            with self.assertRaises(tb_optimizer.ModelArtifactError) as caught:
                tb_optimizer._validate_frozen_candidate_abi(
                    source, "process_top_hls", self.declaration,
                )
            errors.append(str(caught.exception))
        first = {**self.failed, "run_stderr": "first coverage execution failure"}
        second = {**self.failed, "run_stderr": "second coverage execution failure"}
        with tempfile.TemporaryDirectory() as directory:
            result, agent, measure = self.run_artifacts(
                [wrong[0], wrong[1], self.testbench, self.stub,
                 wrong[2], self.testbench, self.stub,
                 self.testbench, self.stub, self.probe],
                [first, second, self.passed], hidden_generation=True,
                max_repairs=5, artifact_root=directory,
            )
            histories = [record["testbench_contract_errors"] for record in result["rounds"]]
            expected = [f"Testbench request {index}: {error}"
                        for index, error in zip((1, 2, 4), errors)]
            self.assertEqual(histories, [expected[:2], expected[2:], []])
            saved = json.loads((Path(directory) / "trajectory_000/round_001/coverage.json").read_text())
            self.assertEqual(saved["testbench_contract_errors"], expected[:2])
        self.assertTrue(result["qualified"])
        self.assertEqual(measure.call_count, 3)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 5)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["requests"], 6)
        messages = [call.kwargs["message"] for call in agent.run.call_args_list]
        for message in (messages[4], messages[5], messages[7]):
            for error in expected[:2]:
                self.assertIn(error, message)
            self.assertIn("first coverage execution failure", message)
        self.assertIn(errors[2], messages[5])
        self.assertIn(expected[2], messages[7])
        self.assertIn("second coverage execution failure", messages[7])
        final_history = tb_optimizer._failure_history(result["rounds"])
        for error in expected:
            self.assertEqual(final_history.count(error), 1)

    def test_successful_round_keeps_contract_evidence_without_stub_only_duplication(self):
        wrong = self.testbench.replace("process_top_hls(int value)", "process_top_hls(float value)")
        stub_failure = {**self.failed, "failure_owner": "stub", "next_action": "regenerate_stub"}
        result, _agent, measure = self.run_artifacts(
            [wrong, self.testbench, self.stub, self.stub, self.probe],
            [stub_failure, self.passed], hidden_generation=True, max_repairs=1,
        )
        self.assertEqual(measure.call_count, 2)
        self.assertEqual(len(result["rounds"][0]["testbench_contract_errors"]), 1)
        self.assertEqual(result["rounds"][1]["testbench_contract_errors"], [])
        self.assertEqual(tb_optimizer._failure_history(result["rounds"]).count(
            "Testbench request 1:"), 1)
        passed, _agent, _measure = self.run_artifacts(
            [wrong, self.testbench, self.stub, self.probe], [self.passed],
            hidden_generation=True, max_repairs=1,
        )
        self.assertEqual(passed["rounds"][0]["status"], "ok")
        self.assertIn("changed the externally frozen", tb_optimizer._failure_history(passed["rounds"]))

    def test_exhausted_testbench_limit_does_not_spend_independent_stub_recovery(self):
        wrong = self.testbench.replace("process_top_hls(int value)", "process_top_hls(float value)")
        stub_failure = {
            **self.failed, "failure_owner": "stub", "next_action": "regenerate_stub",
        }
        result, agent, measure = self.run_artifacts(
            [wrong, self.testbench, self.stub, self.stub, self.probe],
            [stub_failure, self.passed], hidden_generation=True, max_repairs=1,
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 1)
        self.assertEqual(result["artifact_event_counts"]["stub"]["repair_requests"], 1)
        self.assertEqual(measure.call_count, 2)

    def test_zero_semantic_limit_stops_before_requesting_abi_replacement(self):
        wrong = self.testbench.replace("process_top_hls(int value)", "process_top_hls(float value)")
        agent = Mock()
        agent.run.return_value = Mock(messages=[{"content": "```cpp\n" + wrong + "\n```"}])
        with patch.object(tb_optimizer, "HLSAgentLoader") as loader, self.assertRaises(
            tb_optimizer.ModelArtifactError,
        ) as caught:
            loader.return_value.load_agent.return_value = agent
            tb_optimizer.run_trajectory(
                self.original, "process_top", 1, 100.0, None, False,
                pinned_hls_decl=self.declaration, hidden_generation=True,
                emit_final_text=False, max_repairs=0,
            )
        self.assertEqual(agent.run.call_count, 1)
        self.assertEqual(caught.exception.artifact_counts["repairs_used"], 0)

    def test_normal_input_expansion_is_not_capped_as_semantic_repair(self):
        coverage = [{**self.passed, "cov_pct": value} for value in (10.0, 40.0, 100.0)]
        with patch.object(tb_optimizer, "freeze_result_mapping", return_value=None):
            result, agent, measure = self.run_artifacts(
                [self.testbench, self.stub, self.testbench, self.testbench, self.probe],
                coverage, K=3, max_repairs=0,
            )
        self.assertTrue(result["qualified"])
        self.assertEqual(measure.call_count, 3)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["requests"], 3)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 0)

    def test_corrected_contract_history_survives_refinement_and_probe_requests(self):
        wrong = self.testbench.replace("process_top_hls(int value)", "process_top_hls(float value)")
        coverage = [{**self.passed, "cov_pct": value} for value in (10.0, 40.0, 100.0)]
        with patch.object(tb_optimizer, "freeze_result_mapping", return_value=None):
            result, agent, measure = self.run_artifacts(
                [wrong, self.testbench, self.stub, self.testbench, self.testbench, self.probe],
                coverage, K=3, max_repairs=1,
            )
        history = result["rounds"][0]["testbench_contract_errors"][0]
        self.assertEqual(measure.call_count, 3)
        self.assertEqual(result["artifact_event_counts"]["testbench"]["repair_requests"], 1)
        for index in (3, 4, 5):
            self.assertIn(history, agent.run.call_args_list[index].kwargs["message"])

    def test_abi_rewrite_preserves_each_bounded_history_record(self):
        rounds = [
            {"round": 1, "status": "ok", "testbench_contract_errors": ["earliest ABI failure"]},
            {"round": 2, "status": "compile_failed", "compile_stderr": "first compiler evidence " + "a" * 1200},
            {"round": 3, "status": "run_failed", "run_stderr": "later runtime evidence " + "b" * 1200},
        ]
        history = tb_optimizer._failure_history(rounds)
        self.assertGreater(len(history), 1500)
        message = tb_optimizer._hls_friendly_rewrite_message(
            "process_top_hls", "raw diagnostic prefix " + "x" * 1600,
            failure_history=history,
        )
        self.assertIn(history, message)
        self.assertIn("earliest ABI failure", message)
        self.assertNotIn("raw diagnostic prefix", message)


class ArtifactEventAccountingTests(unittest.TestCase):
    @staticmethod
    def response(content):
        return Mock(messages=[{"content": content}])

    def hidden_failure(self, agent, artifact_root=None):
        with patch.object(tb_optimizer, "HLSAgentLoader") as loader:
            loader.return_value.load_agent.return_value = agent
            with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
                tb_optimizer.make_golden_hidden_tb(
                    "void process_top() {}", "process_top", "void process_top_hls();",
                    K=1, M=1, artifact_root=artifact_root,
                )
        return caught.exception.to_dict()

    def test_invalid_envelopes_exhaust_format_retries_without_semantic_fallback(self):
        agent = Mock()
        agent.run.side_effect = [self.response("") for _ in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            failure = self.hidden_failure(agent, directory)
            saved = json.loads((Path(directory) / "trajectory_000/artifact_response_counts.json").read_text())
        self.assertEqual(agent.run.call_count, 4)
        self.assertEqual(failure["attempt_count"], 4)
        self.assertEqual(failure["repair_attempt_count"], 0)
        self.assertEqual(failure["artifact_request_count"], 4)
        self.assertEqual(failure["artifact_response_count"], 4)
        self.assertEqual(failure["artifact_format_retry_count"], 3)
        self.assertEqual(failure["counter_source"], "explicit")
        self.assertEqual(saved["qualification_checks_used"], 4)
        self.assertEqual(failure["trajectory_summaries"][0]["artifact_event_counts"], saved["artifact_event_counts"])
        self.assertIn(tb_optimizer.mapping_generation_instruction(), agent.run.call_args_list[1].kwargs["message"])

    def test_api_timeout_counts_request_without_response(self):
        agent = Mock()
        response = self.response("")
        response.process.side_effect = TimeoutError("transport unavailable")
        agent.run.return_value = response
        failure = self.hidden_failure(agent)
        self.assertEqual(failure["artifact_request_count"], 1)
        self.assertEqual(failure["artifact_response_count"], 0)
        self.assertEqual(failure["attempt_count"], 0)
        self.assertEqual(failure["repair_attempt_count"], 0)

    def test_budget_rejection_is_not_a_request(self):
        agent = Mock()
        agent.run.side_effect = tb_optimizer.BudgetExceededError("llm_calls", 0, 1)
        failure = self.hidden_failure(agent)
        self.assertEqual(failure["artifact_request_count"], 0)
        self.assertEqual(failure["artifact_response_count"], 0)
        self.assertEqual(failure["repair_attempt_count"], 0)

    def test_normal_result_separates_stub_format_retry_from_semantic_repair(self):
        testbench = "void process_top(); void process_top_hls(); int main(){process_top();process_top_hls();return 0;}"
        stub = "void process_top(); void process_top_hls(){process_top();}"
        agent = Mock()
        agent.run.side_effect = [self.response(value) for value in [
            "```cpp\n" + testbench + "\n```", "malformed envelope",
            "```cpp\n" + stub + "\n```", "```cpp\nvoid process_top_hls(){}\n```",
        ]]
        with patch.object(tb_optimizer, "HLSAgentLoader") as loader, patch.object(
            tb_optimizer, "_measure_qualified_coverage",
            return_value={"status": "ok", "cov_pct": 100.0, "lines_total": 1, "lines_hit": 1},
        ), patch.object(tb_optimizer, "_synth_check", return_value=(True, "")):
            loader.return_value.load_agent.return_value = agent
            result = tb_optimizer.run_trajectory(
                "void process_top() {}", "process_top", 1, 100.0, None, False,
                pinned_hls_decl="void process_top_hls();", hidden_generation=True,
                emit_final_text=False,
            )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["qualification_checks_used"], 1)
        self.assertEqual(result["repairs_used"], 0)
        self.assertEqual(result["coverage_checks_used"], 1)
        counts = result["artifact_event_counts"]
        self.assertEqual(counts["stub"]["requests"], 2)
        self.assertEqual(counts["stub"]["responses"], 2)
        self.assertEqual(counts["stub"]["format_retries"], 1)
        self.assertEqual(counts["empty_stub"]["requests"], 1)

    def test_exception_merges_persisted_counts_without_legacy_round_guess(self):
        counts = {"qualification_checks_used": 5, "repairs_used": 2, "coverage_checks_used": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory_000/artifact_response_counts.json"
            path.parent.mkdir()
            path.write_text(json.dumps(counts))
            with patch.object(tb_optimizer, "run_trajectory", side_effect=RuntimeError("qualification stopped")):
                with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
                    tb_optimizer.make_golden_hidden_tb(
                        "void process_top() {}", "process_top", "void process_top_hls();",
                        K=1, M=1, artifact_root=directory,
                    )
        failure = caught.exception.to_dict()
        self.assertEqual(failure["attempt_count"], 5)
        self.assertEqual(failure["repair_attempt_count"], 2)
        self.assertEqual(failure["counter_source"], "explicit")


class PromptStateSafetyTests(unittest.TestCase):
    def test_initial_prompt_requests_normal_complete_state_safe_testbench(self):
        message = tb_optimizer._initial_user_message(
            orig_code="int state; void process_top(){}\n",
            kernel_name="process_top",
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


class CompilerOwnedBoundaryTests(unittest.TestCase):
    def test_type_alias_void_stub_does_not_get_a_return_expression(self):
        declaration = "typedef void Ret; Ret process_top_hls(int *values);"
        stub = tb_coverage._original_only_candidate_stub(declaration, "process_top_hls")
        self.assertIn("process_top_hls(int *values)", stub)
        self.assertNotIn("return {}", stub)

    def test_unknown_declaration_is_not_rejected_by_lexical_shape(self):
        declaration = "not_a_known_type process_top_hls(int *values);"
        stub = tb_coverage._original_only_candidate_stub(declaration, "process_top_hls")
        self.assertIn("process_top_hls(int *values)", stub)

    def test_hls_decl_extraction_uses_compiler_and_ignores_comments(self):
        source = (
            "// void process_top_hls(double wrong);\n"
            "typedef int value_t;\n"
            "void process_top_hls(value_t *values);\n"
            "int main(){return 0;}\n"
        )
        observed = tb_optimizer.extract_hls_decl_from_testbench(source, "process_top_hls")
        self.assertIn("process_top_hls", observed)
        self.assertIn("value_t *values", observed)

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

    def test_capacity_heuristic_does_not_block_real_coverage(self):
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
        with patch.object(
            tb_optimizer,
            "measure_coverage",
            return_value=dict(passed),
        ) as measure:
            result = tb_optimizer._measure_qualified_coverage(
                self.ORIGINAL,
                self._tb("16"),
                "void process_top_hls(){}\n",
                "process_top",
            )
        measure.assert_called_once()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["qualification_errors"], [])

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

    def test_looped_stateful_original_call_reaches_real_coverage(self):
        looped_tb = (
            "int main(){\n"
            " int input[1] = {}, output[1] = {}, fallback = 0;\n"
            " for (int t = 0; t < 3; ++t) {\n"
            "  process_top(1, input, output, &fallback);\n"
            " }\n"
            " return 0;\n"
            "}\n"
        )
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
        with patch.object(
            tb_optimizer,
            "measure_coverage",
            return_value=dict(passed),
        ) as measure:
            result = tb_optimizer._measure_qualified_coverage(
                self.STATEFUL,
                looped_tb,
                "void process_top_hls(int, int*, int*, int*) {}\n",
                "process_top",
            )
        measure.assert_called_once()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["qualification_errors"], [])

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

    def test_failure_fingerprint_is_stable_diagnostic_metadata(self):
        record = {
            "status": "compile_failed",
            "compile_stderr": "same error",
            "run_stderr": "",
        }
        self.assertEqual(
            tb_optimizer._coverage_failure_fingerprint(record),
            tb_optimizer._coverage_failure_fingerprint(dict(record)),
        )

    def test_repeated_failures_do_not_stop_configured_rounds(self):
        testbench = (
            "void process_top_hls();\n"
            "int main(){process_top_hls();return 0;}\n"
        )
        stub = "void process_top_hls(){missing_function();}\n"
        failed = tb_coverage.measure_coverage(
            "void process_top(){}\n", testbench, stub,
        )
        self.assertEqual(failed["failure_owner"], "stub", failed)
        self.assertEqual(failed["next_action"], "regenerate_stub", failed)
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
                    testbench,
                    stub,
                    stub,
                    stub,
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
                side_effect=[dict(failed), dict(failed), dict(failed)],
            ) as measure,
        ):
            result = tb_optimizer.run_trajectory(
                orig_code="void process_top(){}\n",
                kernel_name="process_top",
                K=3,
                target_pct=100.0,
                    llm_config=None,
                want_sig_spec=False,
            )

        self.assertEqual(measure.call_count, 3)
        self.assertEqual(len(result["rounds"]), 3)
        self.assertFalse(result["qualified"])
        self.assertFalse(
            any("early_stop_reason" in record for record in result["rounds"])
        )
        self.assertEqual(
            [call.kwargs["artifact_kind"] for call in request_artifact.call_args_list],
            ["testbench", "stub", "stub", "stub"],
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
        testbench = "void process_top_hls();\nint main(){process_top_hls();return 0;}\n"
        broken_stub = "void process_top_hls(){missing_function();}\n"
        failed = tb_coverage.measure_coverage(
            "void process_top(){}\n", testbench, broken_stub,
        )
        self.assertEqual(failed["failure_owner"], "stub", failed)
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
                    testbench,
                    broken_stub,
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
                    llm_config=None,
                want_sig_spec=False,
            )
        kinds = [
            call.kwargs["artifact_kind"]
            for call in request_artifact.call_args_list
        ]
        self.assertEqual(
            kinds,
            ["testbench", "stub", "stub", "empty_stub"],
        )
        self.assertTrue(result["qualified"])

    def test_hidden_original_runtime_failure_retries_generation_only(self):
        testbench = (
            "void process_top_hls();\n"
            "int main(){process_top();process_top_hls();return 0;}\n"
        )
        failure = {
            "status": "original_run_failed",
            "failure_owner": "unknown",
            "next_action": "review_unknown",
            "failure_evidence_source": "original-only sanitizer/runtime",
            "owner_authority": "runtime_not_isolated",
            "evidence_complete": False,
            "run_stderr": "AddressSanitizer: heap-buffer-overflow",
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
        input_domain = {"n": {"minimum": 1, "maximum": 8}}
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
                    testbench,
                    "void process_top_hls(){}\n",
                    testbench,
                    "void process_top_hls(){}\n",
                    "void process_top_hls(){}\n",
                ],
            ) as request_artifact,
            patch.object(
                tb_optimizer,
                "_measure_qualified_coverage",
                side_effect=[failure, passed],
            ) as measure,
            patch.object(
                tb_optimizer,
                "_freeze_public_contract",
                return_value=("void process_top_hls();", ()),
            ),
            patch.object(
                tb_optimizer,
                "_validate_frozen_candidate_abi",
            ) as validate_abi,
            patch.object(
                tb_optimizer,
                "_synth_check",
                return_value=(True, ""),
            ),
        ):
            result = tb_optimizer.run_trajectory(
                orig_code="void process_top(){}\n",
                kernel_name="process_top",
                K=1,
                target_pct=100.0,
                llm_config=None,
                want_sig_spec=False,
                pinned_hls_decl="void process_top_hls();",
                hidden_generation=True,
                emit_final_text=False,
                input_domain_contract=input_domain,
                max_repairs=1,
            )

        self.assertEqual(measure.call_count, 2)
        self.assertTrue(
            all(
                call.kwargs["require_original_execution"]
                for call in measure.call_args_list
            )
        )
        self.assertEqual(result["rounds"][0]["failure_owner"], "unknown")
        self.assertEqual(
            result["rounds"][0]["next_action"],
            "review_unknown",
        )
        self.assertEqual(
            result["rounds"][1]["ownership_action"],
            "repair_testbench",
        )
        self.assertEqual(
            result["rounds"][1]["lightweight_bounded_recovery_reported_action"],
            "review_unknown",
        )
        self.assertTrue(result["qualified"])
        self.assertGreater(validate_abi.call_count, 0)
        testbench_messages = [
            call.args[1]
            for call in request_artifact.call_args_list
            if call.kwargs.get("artifact_kind") == "testbench"
        ]
        self.assertEqual(len(testbench_messages), 2)
        self.assertIn(
            "AddressSanitizer: heap-buffer-overflow",
            testbench_messages[1],
        )
        self.assertIn("Hidden-test generation", testbench_messages[1])
        self.assertIn(
            "FROZEN LEGAL INPUT-DOMAIN CONTRACT",
            testbench_messages[1],
        )
        self.assertIn(
            "All generated and repaired test cases must remain inside this "
            "finite range.",
            testbench_messages[1],
        )


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
                pinned_public_hls_decl=(
                    "void process_top_hls();"
                ),
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
        ) as run_trajectory:
            result = tb_optimizer.make_golden_hidden_tb(
                orig_code="void process_top(){}\n",
                kernel_name="process_top",
                pinned_public_hls_decl=(
                    "void process_top_hls();"
                ),
                M=1,
                K=1,
            )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["hidden_cov"], 100.0)
        self.assertTrue(
            run_trajectory.call_args.kwargs["hidden_generation"]
        )


if __name__ == "__main__":
    unittest.main()
