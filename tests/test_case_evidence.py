import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from flow.tools.case_evidence import (
    EVIDENCE_FILENAME,
    build_header,
    make_identity,
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


if __name__ == "__main__":
    unittest.main()
