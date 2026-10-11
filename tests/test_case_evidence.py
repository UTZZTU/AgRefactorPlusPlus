import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from flow.tools.case_evidence import (
    EVIDENCE_FILENAME,
    build_header,
    make_identity,
    instrument_testbench,
    prepare_compile_support,
    read_case_counts,
)


class CaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.identity = make_identity(
            execution_id="a" * 32,
            phase="host_differential_csim",
            suite_id="suite",
            candidate_code="candidate",
            testbench_code="testbench",
        )

    def _line(self, event, **extra):
        return json.dumps({
            "schema_version": 1,
            **self.identity,
            "event": event,
            **extra,
        })

    def _write(self, lines):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / EVIDENCE_FILENAME
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory, path

    def test_complete_mixed_counts_are_verified(self):
        directory, path = self._write([
            self._line("case", case_id="one", status="passed"),
            self._line("case", case_id="two", status="failed"),
            self._line("complete", evaluated_cases=2, passed_cases=1, failed_cases=1),
        ])
        self.addCleanup(directory.cleanup)
        value = read_case_counts(path, expected_identity=self.identity, declared_case_count=2)
        self.assertEqual(value["evaluated_cases"], 2)
        self.assertEqual(value["failed_cases"], 1)
        self.assertTrue(value["identity_verified"])

    def test_free_text_or_missing_completion_is_unverified(self):
        directory, path = self._write(["FAIL: 2 mismatch(es) over 2 case(s)"])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity, declared_case_count=2))

    def test_duplicate_case_is_unverified(self):
        directory, path = self._write([
            self._line("case", case_id="one", status="passed"),
            self._line("case", case_id="one", status="passed"),
            self._line("complete", evaluated_cases=2, passed_cases=2, failed_cases=0),
        ])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity, declared_case_count=2))

    def test_count_mismatch_and_identity_mismatch_are_unverified(self):
        directory, path = self._write([
            self._line("case", case_id="one", status="passed"),
            self._line("complete", evaluated_cases=2, passed_cases=2, failed_cases=0),
        ])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity, declared_case_count=2))
        altered = dict(self.identity, candidate_sha256="b" * 64)
        self.assertIsNone(read_case_counts(path, expected_identity=altered))

    def test_complete_must_be_last_event(self):
        directory, path = self._write([
            self._line("case", case_id="one", status="passed"),
            self._line("complete", evaluated_cases=1, passed_cases=1, failed_cases=0),
            self._line("case", case_id="two", status="passed"),
        ])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity))

    def test_bool_or_float_counts_are_unverified(self):
        for complete in (
            {"evaluated_cases": True, "passed_cases": 1, "failed_cases": 0},
            {"evaluated_cases": 1.0, "passed_cases": 1, "failed_cases": 0},
        ):
            directory, path = self._write([
                self._line("case", case_id="one", status="passed"),
                self._line("complete", **complete),
            ])
            self.addCleanup(directory.cleanup)
            self.assertIsNone(read_case_counts(path, expected_identity=self.identity))

    def test_bool_or_float_schema_is_unverified(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / EVIDENCE_FILENAME
        value = json.loads(self._line("case", case_id="one", status="passed"))
        value["schema_version"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity))
        value["schema_version"] = 1.0
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity))

    def test_structured_status_and_declared_count_are_strict(self):
        directory, path = self._write([
            self._line("case", case_id="one", status=[]),
            self._line("complete", evaluated_cases=1, passed_cases=1, failed_cases=0),
        ])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity))
        directory, path = self._write([
            self._line("case", case_id="one", status="passed"),
            self._line("complete", evaluated_cases=1, passed_cases=1, failed_cases=0),
        ])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity, declared_case_count=True))

    def test_host_crash_after_completion_does_not_verify_counts(self):
        from flow.tools.csim import run_csim

        cv = {
            "orig_code": "int original(){ return 0; }",
            "curr_code": "int candidate(){ return 0; }",
            "testbench": "int main(){ return 0; }",
            "csim_suite_id": "host-suite",
            "csim_case_count": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            def execute(work_dir, command, timeout):
                if command.startswith("g++"):
                    return {"returncode": 0, "timeout": False, "stdout": "", "stderr": ""}
                invocation = json.loads((Path(work_dir) / "csim_invocation.json").read_text())
                identity = invocation["case_evidence"]["identity"]
                records = [
                    {"schema_version": 1, **identity, "event": "case", "case_id": "one", "status": "passed"},
                    {"schema_version": 1, **identity, "event": "complete", "evaluated_cases": 1, "passed_cases": 1, "failed_cases": 0},
                ]
                (Path(work_dir) / EVIDENCE_FILENAME).write_text("\n".join(json.dumps(item) for item in records))
                return {"returncode": 134, "timeout": False, "stdout": "", "stderr": "aborted"}

            with patch("flow.tools.csim.tools.general.run_cmd", side_effect=execute):
                status, _ = run_csim(directory, cv)
            invocation = json.loads((Path(directory) / "csim_invocation.json").read_text())
            self.assertEqual(status, "csim_failed")
            self.assertNotIn("case_counts", invocation)
            self.assertIn("observed_unverified_counts", invocation)

    def test_missing_structured_evidence_has_explicit_reason(self):
        from flow.tools.csim import run_csim

        cv = {
            "orig_code": "int original(){ return 0; }",
            "curr_code": "int candidate(){ return 0; }",
            "testbench": "int main(){ return 0; }",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch("flow.tools.csim.tools.general.run_cmd", return_value={
                "returncode": 0, "timeout": False, "stdout": "8 case(s)", "stderr": ""
            }):
                status, _ = run_csim(directory, cv)
            invocation = json.loads((Path(directory) / "csim_invocation.json").read_text())
            self.assertEqual(status, "succeeded")
            self.assertNotIn("case_counts", invocation)
            self.assertEqual(invocation["observed_unverified_counts"]["reason"], "structured_case_evidence_missing")

    def test_zero_cases_require_declared_suite_count(self):
        directory, path = self._write([
            self._line("complete", evaluated_cases=0, passed_cases=0, failed_cases=0),
        ])
        self.addCleanup(directory.cleanup)
        self.assertIsNone(read_case_counts(path, expected_identity=self.identity))
        self.assertEqual(
            read_case_counts(path, expected_identity=self.identity, declared_case_count=0)["evaluated_cases"],
            0,
        )

    def test_header_is_cxx11_compatible_and_identity_bound(self):
        header = build_header(identity=self.identity)
        self.assertIn(self.identity["candidate_sha256"], header)
        self.assertIn("case_result", header)
        self.assertIn("complete", header)
        self.assertIn("write_failed", header)


    @unittest.skipUnless(shutil.which("g++"), "g++ is required")
    def test_reporting_api_compiles_cxx11_string_ids_and_keeps_failure_exit(self):
        code = """#include <string>
int main() {
    const std::string id = "variable";
    agrefactor_case_evidence::case_result("literal", true);
    agrefactor_case_evidence::case_result(id, false);
    agrefactor_case_evidence::case_result("dynamic_" + std::to_string(2), true);
    agrefactor_case_evidence::complete();
    return 1;
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            support = prepare_compile_support(
                work_dir=root, candidate_code="candidate", testbench_code=code,
                phase="helper_contract_test",
            )
            (root / "testbench.cpp").write_text(instrument_testbench(code))
            compiled = subprocess.run(
                ["g++", "-std=c++11", "testbench.cpp", "-o", "driver"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            run = subprocess.run([str(root / "driver")], cwd=root)
            self.assertEqual(run.returncode, 1)
            counts = read_case_counts(root / EVIDENCE_FILENAME,
                                      expected_identity=support["identity"], declared_case_count=3)
            self.assertEqual(counts["evaluated_cases"], 3)
            self.assertEqual(counts["passed_cases"], 2)
            self.assertEqual(counts["failed_cases"], 1)


@unittest.skipUnless(shutil.which("g++"), "g++ is required")
class CaseEvidenceCompilationContextTests(unittest.TestCase):
    original = "int original(int x){return x+1;}"
    candidate = """#ifdef AGREFACTOR_CASE_EVIDENCE
#error reporting support leaked into Candidate
#endif
int candidate(int x){return x+1;}
"""
    driver = """#include <string>
int original(int);
int candidate(int);
int main(){
    const bool matched = original(2) == candidate(2);
#ifdef AGREFACTOR_CASE_EVIDENCE
    agrefactor_case_evidence::case_result(std::string("case_0"), matched);
    agrefactor_case_evidence::complete();
#endif
    return matched ? 0 : 1;
}
"""

    def test_preflight_compiles_reporting_and_does_not_inject_candidate(self):
        from agrefactor.evaluation.testbench_preflight import TestbenchPreflight

        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory, original_code=self.original,
                candidate_code=self.candidate, testbench_code=self.driver,
                original_top_function="original", candidate_top_function="candidate",
            )
            self.assertTrue(result.succeeded, result.to_dict())
            invocation = json.loads((Path(directory) / "testbench_preflight_invocation.json").read_text())
            self.assertIn("case_evidence_support", invocation)
            self.assertEqual(invocation["source_roles"]["agrefactor_case_evidence.hpp"], "unknown")

    def test_preflight_preserves_raw_testbench_identity_for_native_authority(self):
        from agrefactor.evaluation.testbench_preflight import TestbenchPreflight
        from agrefactor.runtime.csim_stage import CsimStageInputs, _native_csim_preflight_authority

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = TestbenchPreflight().compile_and_link(
                work_dir=root / "preflight", original_code=self.original,
                candidate_code=self.candidate, testbench_code=self.driver,
                original_top_function="original", candidate_top_function="candidate",
            )
            self.assertTrue(result.succeeded, result.to_dict())
            self.assertEqual((root / "preflight" / "testbench.cpp").read_text(), self.driver)
            authority = _native_csim_preflight_authority(CsimStageInputs(
                work_dir=root / "csim", original_code=self.original, candidate_code=self.candidate,
                suite_testbench_codes={"public": self.driver}), suite_id="public", testbench_code=self.driver)
            self.assertEqual(authority["status"], "passed")

    def test_broken_executor_header_does_not_become_testbench_owned(self):
        from agrefactor.evaluation.testbench_preflight import TestbenchPreflight

        with tempfile.TemporaryDirectory() as directory:
            with patch("flow.tools.case_evidence.build_header", return_value="#error broken executor support\n"):
                result = TestbenchPreflight().compile_and_link(
                    work_dir=directory, original_code=self.original,
                    candidate_code=self.candidate, testbench_code="int main(){return 0;}",
                )
            self.assertFalse(result.succeeded)
            self.assertEqual(result.failure_owner.value, "unknown")
            self.assertIn("broken executor support", result.stderr)

    def test_original_qualification_checks_reporting_compile_branch(self):
        from flow.tools.tb_coverage import check_original_execution

        broken = self.driver.replace('std::string("case_0")', '17')
        with tempfile.TemporaryDirectory() as directory:
            result = check_original_execution(self.original, broken, "int candidate(int);", "candidate",
                original_name="original", required_original_entry="original", keep_dir=directory)
            self.assertEqual(result["status"], "compile_failed", result)
            self.assertEqual(result["failure_owner"], "testbench")
            self.assertEqual(result["next_action"], "repair_testbench")

    def test_preflight_reports_error_in_reporting_branch_as_testbench(self):
        from agrefactor.evaluation.testbench_preflight import TestbenchPreflight

        broken = self.driver.replace('std::string("case_0")', '17')
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory, original_code=self.original,
                candidate_code=self.candidate, testbench_code=broken,
                original_top_function="original", candidate_top_function="candidate",
            )
            self.assertFalse(result.succeeded)
            self.assertEqual(result.failure_owner.value, "testbench")
            self.assertEqual(result.next_action, "repair_testbench")
            self.assertIn("case_result", result.stderr)

    def test_original_qualification_preserves_noop_mismatch_and_reports_cases(self):
        from flow.tools.tb_coverage import check_original_execution

        with tempfile.TemporaryDirectory() as directory:
            result = check_original_execution(
                self.original, self.driver, "int candidate(int);", "candidate",
                original_name="original", required_original_entry="original", keep_dir=directory,
            )
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["run_returncode"], 1)
            counts = read_case_counts(Path(directory) / EVIDENCE_FILENAME,
                expected_identity=result["case_evidence_support"]["identity"], declared_case_count=1)
            self.assertEqual(counts["failed_cases"], 1)

    def test_coverage_reports_without_changing_comparison_or_stub_context(self):
        from flow.tools.tb_coverage import measure_coverage

        with tempfile.TemporaryDirectory() as directory:
            result = measure_coverage(self.original, self.driver, self.candidate,
                original_name="original", keep_dir=directory)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["run_returncode"], 0)
            counts = read_case_counts(Path(directory) / EVIDENCE_FILENAME,
                expected_identity=result["case_evidence_support"]["identity"], declared_case_count=1)
            self.assertEqual(counts["passed_cases"], 1)


if __name__ == "__main__":
    unittest.main()
