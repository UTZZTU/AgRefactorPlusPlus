import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from autogen.agentchat.group import ContextVariables
from flow.tools import result_mapping as mapping, tb_coverage, testbench as generation, tb_optimizer


ORIGINAL = """struct State { int value; };
void task(int input, State* state) { if (input == 0) return; state->value = input; }
"""


def public_source(*, corrected=False, capture_suffix="", comparator=None, main_suffix="",
                  case_completion=True):
    declaration = "void task_hls(int input, int* result);" if corrected else "void task_hls(int input);"
    candidate_body = ("Observation result{5}; task_hls(in.value, &result.value);" if corrected
                      else "Observation result{}; task_hls(in.value); result.value = in.value;")
    compare = comparator or "return original.value == candidate.value;"
    shared = """// AGREFACTOR_SHARED_RESULT_MAPPING_BEGIN
struct Input { int value; };
struct State { int value; };
struct Observation { int value; };
void task(int input, State* state);
""" + declaration + """
Observation agrefactor_observe_original(const Input& in) {
 State state{5}; task(in.value, &state);
 Observation result{}; result.value = state.value; return result;
}
Observation agrefactor_observe_candidate(const Input& in) {
 """ + candidate_body + capture_suffix + """ return result;
}
bool agrefactor_compare_observations(const Observation& original, const Observation& candidate) {
 """ + compare + """
}
// AGREFACTOR_SHARED_RESULT_MAPPING_END
"""
    manifest = {"observables": [{"id": "state", "kind": "post_call_state", "original": "state after call",
                                "candidate": "result after call", "source_evidence": ["state->value = input;"]}]}
    return "#include <initializer_list>\n#include <string>\n" + shared + mapping.MANIFEST_BEGIN + "\n" + json.dumps(manifest) + "\n*/\n" + """
int main() { int failures=0; for (int value: {0, 4}) {
 Input in{value}; auto original = agrefactor_observe_original(in);
 auto candidate = agrefactor_observe_candidate(in);
 """ + main_suffix + """
 bool matched = agrefactor_compare_observations(original, candidate);
 failures += !matched;
#if defined(AGREFACTOR_CASE_EVIDENCE)
 agrefactor_case_evidence::case_result(std::to_string(value), matched);
#endif
 }
""" + ("""
#if defined(AGREFACTOR_CASE_EVIDENCE)
 agrefactor_case_evidence::complete();
#endif
""" if case_completion else "") + "return failures ? 1 : 0; }\n"


class PublicObservationQualificationTests(unittest.TestCase):
    def freeze(self, source, original=ORIGINAL):
        return mapping.freeze_result_mapping(source, original, "task", "task_hls")

    def probe(self, source):
        return mapping.observation_qualification_probe(source, ORIGINAL, self.freeze(source), "task", "task_hls")

    def qualify(self, source, directory, original=ORIGINAL):
        frozen = self.freeze(source, original)
        declaration = tb_optimizer.extract_hls_decl_from_testbench(source, "task_hls")
        return tb_coverage.check_original_execution(
            original, source, declaration, "task_hls", original_name="task",
            result_mapping=frozen, keep_dir=directory,
        )

    def test_actual_qualified_input_proxy_counterexample_is_testbench(self):
        source = public_source()
        with tempfile.TemporaryDirectory() as directory:
            result = self.qualify(source, directory)
            self.assertEqual(result["status"], "contract_failed", result)
            self.assertEqual(result["failure_owner"], "testbench")
            evidence = result["public_observation_qualification"]
            self.assertEqual(evidence["status"], "contradiction_proven")
            self.assertEqual(evidence["comparison_count"], 2)
            self.assertEqual(evidence["counterexamples"][0]["original_value"], "5")
            self.assertEqual(evidence["counterexamples"][0]["input_proxy_value"], "0")
            compiled = Path(directory, "testbench.cpp").read_text()
            self.assertEqual(evidence["compiled_testbench_sha256"], mapping._sha(compiled))
            self.assertEqual(result["case_evidence_support"]["compiled_testbench_sha256"], mapping._sha(compiled))
            self.assertIsNotNone(mapping.validated_observation_contradiction(evidence, testbench_code=source, original_code=ORIGINAL))
            self.assertIsNone(mapping.validated_observation_contradiction(evidence, testbench_code=source + "\n", original_code=ORIGINAL))
            Path(evidence["recording_path"]).write_text("stale")
            self.assertIsNone(mapping.validated_observation_contradiction(evidence, testbench_code=source, original_code=ORIGINAL))

    def test_input_proxy_that_really_is_invariant_remains_qualified(self):
        original = ORIGINAL.replace("if (input == 0) return; ", "")
        with tempfile.TemporaryDirectory() as directory:
            result = self.qualify(public_source(), directory, original)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["public_observation_qualification"]["status"], "no_contradiction_observed")

    def test_real_output_and_noop_output_mismatch_do_not_prove_public_error(self):
        source = public_source(corrected=True)
        with tempfile.TemporaryDirectory() as directory:
            result = self.qualify(source, directory)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["public_observation_qualification"]["status"], "unproven")

    def test_unused_equality_or_non_equality_comparator_is_not_a_counterexample(self):
        for comparator in ["bool unused = original.value == candidate.value; return true;",
                           "return original.value <= candidate.value;",
                           "return original.value == candidate.value || true;",
                           "if (false) { if (original.value != candidate.value) return false; } return true;",
                           "if (original.value == 5) return true; if (original.value != candidate.value) return false; return true;",
                           "return static_cast<unsigned char>(original.value) == static_cast<unsigned char>(candidate.value);",
                           "bool match=true; if(original.value != candidate.value) match=false; bool& alias=match; alias=true; return match;"]:
            with self.subTest(comparator=comparator):
                _, probe = self.probe(public_source(comparator=comparator))
                self.assertEqual(probe["status"], "unproven")

    def test_conditional_original_top_is_not_claimed_executed_by_capture_order(self):
        for replacement in ["State state{5}; if (in.value != 0) task(in.value, &state);",
                            "State state{5}; (false ? task(in.value, &state) : (void)0);",
                            "State state{5}; false && (task(in.value, &state), true);"]:
            source = public_source().replace("State state{5}; task(in.value, &state);", replacement)
            _, probe = self.probe(source)
            self.assertEqual(probe["status"], "unproven")

    def test_missing_case_completion_preserves_legacy_qualification_without_new_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.qualify(public_source(case_completion=False), directory)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["public_observation_qualification"]["status"], "unproven")

    def test_pointer_member_capture_keeps_legacy_suite_without_instrumentation(self):
        source = public_source().replace("struct Observation { int value; };",
                                        "struct Observation { int* value; };")
        source = source.replace("result.value = state.value;", "result.value = &state.value;")
        source = source.replace("result.value = in.value;", "result.value = const_cast<int*>(&in.value);")
        source = source.replace("original.value == candidate.value", "*original.value == *candidate.value")
        compiled, probe = self.probe(source)
        self.assertEqual(compiled, source)
        self.assertEqual(probe["status"], "unproven")

    def test_scoped_enum_capture_keeps_valid_suite_without_new_instrumentation(self):
        source = public_source().replace("struct Observation { int value; };",
            "enum class Kind : int { zero }; struct Observation { Kind value; };")
        source = source.replace("result.value = state.value;", "result.value = static_cast<Kind>(state.value);")
        source = source.replace("result.value = in.value;", "result.value = static_cast<Kind>(in.value);")
        compiled, probe = self.probe(source)
        self.assertEqual(compiled, source)
        self.assertEqual(probe["status"], "unproven")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.qualify(source, directory)["status"], "ok")

    def test_later_capture_write_alias_or_main_mutation_stays_unproven(self):
        for kwargs in [dict(capture_suffix=" if (in.value == 0) result.value = 5;"),
                       dict(capture_suffix=" int* alias = &result.value; *alias = 5;"),
                       dict(main_suffix="candidate.value = original.value;")]:
            with self.subTest(kwargs=kwargs):
                _, probe = self.probe(public_source(**kwargs))
                self.assertEqual(probe["status"], "unproven", probe)

    def test_monotone_mismatch_flag_and_macro_predicate_are_bound(self):
        comparator = """bool match = true;
#define CHECK(a,b) do { if ((a) != (b)) { match = false; } } while (0)
CHECK(original.value, candidate.value);
#undef CHECK
return match;"""
        _, probe = self.probe(public_source(comparator=comparator))
        self.assertEqual(probe["status"], "recording_prepared", probe)

    def test_incomplete_or_duplicate_recording_never_grants_owner(self):
        _, probe = self.probe(public_source())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, probe["recording_filename"])
            channel = probe["channels"][0]["channel_id"]
            execution = probe["recording_execution_id"]
            row = f"{execution}\t1\t{channel}\t5\t0\n"
            for contents, complete in [(row, False), (row + row, True), (row.replace(execution, "stale"), True)]:
                path.write_text(contents)
                evidence = mapping.complete_observation_qualification(
                    probe, work_dir=directory, execution_complete=complete, original_executed=True,
                    case_counts={"complete": True, "identity_verified": True, "evaluated_cases": 1},
                )
                self.assertEqual(evidence["status"], "unproven")

    def test_late_public_receipt_blocks_candidate_but_stale_and_hidden_preserve_authority(self):
        from agrefactor.config import EvaluationSplit, TestSuiteSpec
        from agrefactor.evaluation.csim_suite import CsimSuiteEvaluationResult
        from agrefactor.evidence import FeedbackOwner, TestEvaluationEvidence, TestEvaluationStatus
        from agrefactor.runtime import CsimStageInputs, CsimValidationStageHandler

        source = public_source()
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.qualify(source, directory)["public_observation_qualification"]
            for split, current, expected in [
                (EvaluationSplit.PUBLIC, source, FeedbackOwner.TESTBENCH),
                (EvaluationSplit.PUBLIC, source + "\n", FeedbackOwner.CANDIDATE),
                (EvaluationSplit.HIDDEN, source, FeedbackOwner.CANDIDATE),
            ]:
                with self.subTest(split=split, stale=current != source):
                    suite = TestSuiteSpec(suite_id="suite", split=split)
                    evidence = TestEvaluationEvidence(
                        suite=suite, status=TestEvaluationStatus.FAILED, failed_cases=1, return_code=1,
                        details={"compile_execution": {"status": "completed", "returncode": 0, "timeout": False},
                                 "simulation_execution": {"status": "completed", "returncode": 1, "timeout": False}},
                    )
                    handler = CsimValidationStageHandler(CsimStageInputs(
                        work_dir=directory, original_code=ORIGINAL, candidate_code="void task_hls(int) {}",
                        suite_testbench_codes={"suite": current}, public_observation_qualification=receipt,
                    ), split=split)
                    result = CsimSuiteEvaluationResult(legacy_status="csim_failed", diagnostic="mismatch", evidence=evidence)
                    report = handler._result_report(result, report_id="observation")
                    self.assertEqual(report.items[0].owner, expected)
                    if expected is FeedbackOwner.TESTBENCH:
                        self.assertFalse(report.items[0].metadata["repair_eligible"])
                        self.assertEqual(report.items[0].metadata["failure_reason"],
                                         "frozen_public_observation_contract_requires_regeneration")

    def test_last_shared_replacement_is_requalified_and_generates_no_candidate(self):
        flawed, corrected = public_source(), public_source(corrected=True)
        agent, loader = Mock(), Mock()
        loader.load_agent.return_value = agent
        agent.run.return_value = Mock(messages=[{"content": "Use final ABI."}])
        cv = ContextVariables(data={"kernel_name": "task", "curr_code": ORIGINAL, "orig_code": ORIGINAL,
                                   "max_testbench_repair_attempts": 1})
        messages = []
        def request(_agent, message, **kwargs):
            messages.append((kwargs["artifact_kind"], message, kwargs.get("max_repairs")))
            return flawed if len(messages) == 1 else corrected
        with tempfile.TemporaryDirectory() as directory:
            cv["public_tb_artifact_dir"] = directory
            with patch.object(generation, "HLSAgentLoader", return_value=loader), patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=request):
                final, _, _ = generation.gen_tb_prior(cv)
            self.assertEqual(final, corrected)
            self.assertEqual([message[0] for message in messages], ["testbench", "testbench_repair"])
            self.assertEqual(messages[1][2], 0)
            self.assertIn("coordinated ABI correction", messages[1][1])
            self.assertEqual(cv["public_result_mapping_qualification"]["mapping_repair_requests_used"], 1)
            self.assertEqual(len(list(Path(directory, "original_qualification").glob("round_*"))), 2)
            self.assertEqual(cv["public_observation_qualification"]["status"], "unproven")

    def test_zero_shared_replacements_does_not_call_model_again(self):
        loader = Mock(); loader.load_agent.return_value = Mock()
        cv = ContextVariables(data={"kernel_name": "task", "curr_code": ORIGINAL, "orig_code": ORIGINAL,
                                   "max_testbench_repair_attempts": 0})
        with patch.object(generation, "HLSAgentLoader", return_value=loader), patch.object(tb_optimizer, "_request_cpp_artifact", return_value=public_source()) as request:
            with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted):
                generation.gen_tb_prior(cv)
        self.assertEqual(request.call_count, 1)

    def test_abi_correction_reuses_observation_qualification_before_freeze(self):
        corrected, flawed = public_source(corrected=True), public_source()
        agent, loader = Mock(), Mock()
        loader.load_agent.return_value = agent
        agent.run.return_value = Mock(messages=[{"content": "Use final ABI."}])
        cv = ContextVariables(data={"kernel_name": "task", "curr_code": ORIGINAL, "orig_code": ORIGINAL,
                                   "max_testbench_repair_attempts": 1, "target_profile": {"part": "example"}})
        returned = iter([corrected, "void task_hls(int, int*) {}", flawed, corrected,
                         "void task_hls(int, int*) {}"])
        with tempfile.TemporaryDirectory() as directory:
            cv["public_tb_artifact_dir"] = directory
            with patch.object(generation, "HLSAgentLoader", return_value=loader), \
                 patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=lambda *a, **k: next(returned)), \
                 patch.object(tb_optimizer, "validate_stub_contract"), \
                 patch.object(tb_optimizer, "_synth_check", side_effect=[(False, "ABI probe fails"), (True, "")]):
                final, _, _ = generation.gen_tb_prior(cv)
            self.assertEqual(final, corrected)
            self.assertEqual(cv["public_result_mapping_qualification"]["mapping_repair_requests_used"], 1)
            self.assertEqual(len(list(Path(directory, "original_qualification").glob("round_*"))), 3)
            self.assertEqual(cv["public_observation_qualification"]["status"], "unproven")


if __name__ == "__main__":
    unittest.main()
