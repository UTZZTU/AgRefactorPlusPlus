import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from autogen.agentchat.group import ContextVariables

from agrefactor.config import TestSuiteSpec
from agrefactor.evaluation.csim_suite import CsimSuiteEvaluator
from agrefactor.evidence import TestEvaluationStatus
from flow.tools import csim, csynth, tb_hidden_eval, testbench


MATCHED_VERIFICATION = {
    "status": "matched", "requested": "2023.2", "actual": "2023.2",
    "returncode": 0, "stdout": "vitis-run v2023.2", "stderr": "",
}


def process_result(returncode=0, *, timeout=False):
    return {"returncode": returncode, "timeout": timeout, "stdout": "", "stderr": ""}


def context():
    return ContextVariables(data={
        "curr_code": "void top_hls() {}\n", "new_kernel_name": "top_hls",
        "orig_code": "void top() {}\n", "testbench": "int main() { return 0; }\n",
    })


class CsynthCompletionTruthTests(unittest.TestCase):
    def run_case(
        self, result, *, report="new", marker=True, old_log=False,
        source_roles=None, support_source=False, expected_exception=None,
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            report_path = root / "csynth/solution/syn/report/top_hls_csynth.rpt"
            log_path = root / "csynth/solution/solution.log"
            cv = context()
            if source_roles is not None:
                cv["csynth_source_roles"] = source_roles
            if support_source:
                (root / "support.cpp").write_text("int shared_value = 3;\n", encoding="utf-8")
                cv["csynth_extra_sources"] = ["support.cpp"]
            if old_log:
                log_path.parent.mkdir(parents=True)
                log_path.write_text("Finished Checking Synthesizability:\n", encoding="utf-8")
            if report in {"stale", "updated", "identical"}:
                report_path.parent.mkdir(parents=True)
                report_path.write_text("previous report", encoding="utf-8")

            def launch(work_dir, command, timelimit):
                if isinstance(result, Exception):
                    raise result
                if report in {"new", "updated", "empty", "identical"}:
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    contents = (
                        "" if report == "empty" else
                        "previous report" if report == "identical" else
                        "current report"
                    )
                    report_path.write_text(contents, encoding="utf-8")
                if marker:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.write_text("Finished Checking Synthesizability:\n", encoding="utf-8")
                return result

            with patch.object(csynth.shutil, "which", return_value="/mock/vitis-run"), \
                 patch.object(csynth, "probe_csynth_version", return_value=MATCHED_VERIFICATION), \
                 patch.object(csynth.tools.general, "run_cmd", side_effect=launch):
                if expected_exception is None:
                    outcome = csynth.run_csynth(raw, cv)
                else:
                    with self.assertRaises(expected_exception):
                        csynth.run_csynth(raw, cv)
                    outcome = None
            invocation = json.loads((root / "csynth_invocation.json").read_text(encoding="utf-8"))
            archived = invocation.get("expected_report", {}).get("archived_previous_report")
            if archived is not None:
                self.assertEqual(Path(archived).read_text(encoding="utf-8"), "previous report")
            archived_log = invocation.get("diagnostic_log", {}).get("archived_previous_log")
            if archived_log is not None:
                self.assertEqual(Path(archived_log).read_text(encoding="utf-8"), "Finished Checking Synthesizability:\n")
            return outcome, invocation

    def test_timeout_cannot_be_overridden_by_report_or_marker(self):
        outcome, invocation = self.run_case(process_result(None, timeout=True))
        self.assertEqual(outcome[0], "timeout")
        self.assertTrue(invocation["execution"]["timeout"])

    def test_nonzero_exit_cannot_be_overridden_by_marker(self):
        outcome, _ = self.run_case(process_result(7))
        self.assertEqual(outcome[0], "csynth_failed")

    def test_marker_without_report_is_not_success(self):
        outcome, _ = self.run_case(process_result(), report="missing")
        self.assertEqual(outcome[0], "csynth_failed")

    def test_old_unmodified_report_is_not_current_success_evidence(self):
        outcome, invocation = self.run_case(process_result(), report="stale", marker=False)
        self.assertEqual(outcome[0], "csynth_failed")
        self.assertFalse(invocation["expected_report"]["produced_by_current_invocation"])

    def test_completed_process_with_new_report_passes(self):
        outcome, invocation = self.run_case(process_result())
        self.assertEqual(outcome[0], "succeeded")
        self.assertTrue(invocation["expected_report"]["produced_by_current_invocation"])
        self.assertTrue(invocation["synthesizability_check_finished"])

    def test_regenerated_report_in_reused_work_dir_passes(self):
        outcome, invocation = self.run_case(process_result(), report="updated")
        self.assertEqual(outcome[0], "succeeded")
        self.assertTrue(invocation["expected_report"]["produced_by_current_invocation"])

    def test_empty_report_does_not_prove_synthesis(self):
        outcome, _ = self.run_case(process_result(), report="empty")
        self.assertEqual(outcome[0], "csynth_failed")

    def test_identical_regenerated_report_is_current_evidence(self):
        outcome, invocation = self.run_case(process_result(), report="identical")
        self.assertEqual(outcome[0], "succeeded")
        evidence = invocation["expected_report"]
        self.assertEqual(evidence["sha256"], evidence["sha256_before"])
        self.assertTrue(evidence["produced_by_current_invocation"])
        self.assertTrue(evidence["isolated_before_launch"])

    def test_timeout_remains_failure_with_a_stale_report(self):
        outcome, invocation = self.run_case(process_result(0, timeout=True), report="stale")
        self.assertEqual(outcome[0], "timeout")
        self.assertFalse(invocation["expected_report"]["produced_by_current_invocation"])

    def test_prior_log_is_archived_and_not_returned_as_current_diagnostics(self):
        outcome, invocation = self.run_case(process_result(), marker=False, old_log=True)
        self.assertEqual(outcome, ("succeeded", ""))
        self.assertFalse(invocation["diagnostic_log"]["produced_by_current_invocation"])
        self.assertFalse(invocation["synthesizability_check_finished"])

    def test_identical_regenerated_log_is_current_evidence(self):
        _, invocation = self.run_case(process_result(), old_log=True)
        evidence = invocation["diagnostic_log"]
        self.assertTrue(evidence["produced_by_current_invocation"])
        self.assertEqual(evidence["sha256"], evidence["sha256_before"])

    def test_source_roles_require_explicit_mapping(self):
        _, invocation = self.run_case(process_result(), support_source=True)
        self.assertEqual(invocation["compile_units"], ["top_hls.cpp", "support.cpp"])
        for evidence in invocation["source_provenance"].values():
            self.assertEqual(evidence["role"], "unknown")

    def test_source_provenance_covers_only_executed_files_and_actual_hashes(self):
        _, invocation = self.run_case(
            process_result(), support_source=True,
            source_roles={"top_hls.cpp": "candidate", "support.cpp": "original", "unused.cpp": "testbench"},
        )
        evidence = invocation["source_provenance"]
        self.assertEqual(set(evidence), set(invocation["compile_units"]))
        self.assertEqual(evidence["top_hls.cpp"]["role"], "candidate")
        self.assertEqual(evidence["support.cpp"]["role"], "original")
        self.assertEqual(evidence["top_hls.cpp"]["sha256"], hashlib.sha256(b"void top_hls() {}\n").hexdigest())
        self.assertEqual(evidence["support.cpp"]["sha256"], hashlib.sha256(b"int shared_value = 3;\n").hexdigest())
        self.assertTrue(Path(evidence["top_hls.cpp"]["path"]).is_absolute())

    def test_source_missing_text_does_not_prove_tool_launch_failure(self):
        result = process_result(1)
        result["stderr"] = "include.hpp: file not found; No such file or directory"
        outcome, invocation = self.run_case(result)
        self.assertEqual(outcome[0], "csynth_failed")
        self.assertEqual(invocation["execution"]["status"], "completed")

    def test_shell_command_missing_records_launch_error(self):
        _, invocation = self.run_case(process_result(127), old_log=True, expected_exception=RuntimeError)
        self.assertEqual(invocation["execution"]["status"], "launch_error")
        self.assertTrue(invocation["execution"]["executable_missing"])
        self.assertFalse(invocation["diagnostic_log"]["produced_by_current_invocation"])

    def test_actual_missing_executable_records_launch_error(self):
        _, invocation = self.run_case(FileNotFoundError("executable unavailable"), old_log=True, expected_exception=FileNotFoundError)
        self.assertEqual(invocation["execution"]["status"], "launch_error")
        self.assertFalse(invocation["diagnostic_log"]["produced_by_current_invocation"])

    def test_runner_exception_does_not_prove_toolchain_failure(self):
        _, invocation = self.run_case(RuntimeError("runner failed"), expected_exception=RuntimeError)
        self.assertEqual(invocation["execution"]["status"], "execution_error")
        self.assertFalse(invocation["execution"]["executable_missing"])


class HiddenExecutionTruthTests(unittest.TestCase):
    def evaluate(self, **coverage):
        with patch.object(tb_hidden_eval, "measure_coverage", return_value=coverage):
            return tb_hidden_eval.eval_against_hidden_tb("original", "candidate", "hidden")

    def test_missing_coverage_never_passes_or_invents_mismatch(self):
        for status in ("no_gcda", "gcov_failed", "missing_orig_gcov"):
            for returncode in (0, None, 2):
                with self.subTest(status=status, returncode=returncode):
                    result = self.evaluate(status=status, run_returncode=returncode)
                    self.assertFalse(result["passed"])
                    self.assertEqual(result["failure_kind"], "coverage_evidence_missing")
                    self.assertTrue(result["review_required"])
                    self.assertEqual(result["run_returncode"], returncode)

    def test_unknown_status_is_inconclusive(self):
        result = self.evaluate(status="new_status", run_returncode=0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure_kind"], "inconclusive")

    def test_nonzero_execution_is_not_proven_candidate_mismatch(self):
        result = self.evaluate(status="run_failed", run_returncode=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure_kind"], "execution_failed")

    def test_original_must_have_executed_before_hidden_can_pass(self):
        for total, hit in ((8, 0), (0, 0), (None, None), (True, True), (2, 3)):
            with self.subTest(total=total, hit=hit):
                result = self.evaluate(status="ok", run_returncode=0, lines_total=total, lines_hit=hit)
                self.assertFalse(result["passed"])
                self.assertEqual(result["failure_kind"], "original_execution_unproven")

    def test_completed_program_with_original_coverage_passes(self):
        result = self.evaluate(status="ok", run_returncode=0, lines_total=8, lines_hit=3)
        self.assertTrue(result["passed"])
        self.assertEqual(result["failure_kind"], "pass")
        self.assertFalse(result["review_required"])


class EmptyStubFailureTruthTests(unittest.TestCase):
    def test_synthesis_failure_does_not_invent_toolchain_ownership(self):
        declaration = "void origin_hls();"
        driver = "void origin(); void origin_hls(); int main(){origin(); origin_hls();}"
        stub = "void origin_hls() {}"
        for external in (False, True):
            with self.subTest(external=external), ExitStack() as stack:
                loader = Mock()
                loader.load_agent.return_value = Mock()
                stack.enter_context(patch.object(testbench, "HLSAgentLoader", return_value=loader))
                stack.enter_context(patch.object(testbench, "isolate_reference_program_entry", return_value="void origin() {}"))
                optimizer = testbench.tools.tb_optimizer
                for name in ("validate_testbench_top_contract", "validate_testbench_input_domain", "validate_stub_contract"):
                    stack.enter_context(patch.object(optimizer, name))
                stack.enter_context(patch.object(optimizer, "extract_hls_decl_from_testbench", return_value=declaration))
                stack.enter_context(patch.object(optimizer, "_request_cpp_artifact", side_effect=[stub] if external else [driver, stub]))
                stack.enter_context(patch.object(optimizer, "_synth_check", return_value=(False, "unresolved synthesis failure")))
                stack.enter_context(patch.object(testbench.tools.tb_coverage, "check_original_execution", return_value={"status": "ok"}))
                cv = {
                    "kernel_name": "origin", "curr_code": "void origin() {}",
                    "target_profile": {"compile_flags": ()},
                    "max_testbench_repair_attempts": 0,
                }
                with self.assertRaises(optimizer.TestbenchGenerationExhausted) as caught:
                    if external:
                        testbench.qualify_external_public_testbench(cv, driver)
                    else:
                        testbench.gen_tb_prior(cv)
                payload = caught.exception.to_dict()
                record = payload["trajectory_summaries"][0]
                self.assertEqual(record["failure_owner"], "unknown")
                self.assertEqual(record["next_action"], "review_unknown")
                self.assertIn("unresolved synthesis failure", record["diagnostic_excerpt"])
                self.assertEqual(payload["failure_owner"], "unknown")
                self.assertEqual(
                    payload["next_action"],
                    "review_required_provided" if external else "review_unknown",
                )


class CsimTimeoutTruthTests(unittest.TestCase):
    def test_compile_timeout_has_priority_even_with_zero_returncode(self):
        with tempfile.TemporaryDirectory() as raw, \
             patch.object(csim.tools.general, "run_cmd", return_value=process_result(0, timeout=True)) as launch:
            outcome = csim.run_csim(raw, context())
            invocation = json.loads((Path(raw) / "csim_invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(outcome[0], "tb_compile_failed")
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(invocation["compile_execution"]["status"], "timeout")
        self.assertEqual(invocation["simulation_execution"]["status"], "skipped_after_compile_failure")

    def test_simulation_timeout_remains_authoritative_to_suite_evaluator(self):
        with tempfile.TemporaryDirectory() as raw, \
             patch.object(csim.tools.general, "run_cmd", side_effect=[process_result(), process_result(0, timeout=True)]):
            result = CsimSuiteEvaluator().evaluate(work_dir=raw, context_variables=context(), suite=TestSuiteSpec(suite_id="public"))
            invocation = json.loads((Path(raw) / "csim_invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(result.legacy_status, "csim_failed")
        self.assertEqual(result.evidence.status, TestEvaluationStatus.ERROR)
        self.assertTrue(result.evidence.timed_out)
        self.assertEqual(invocation["simulation_execution"]["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
