from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agrefactor.evaluation.testbench_preflight import TestbenchPreflight
from flow.tools import tb_coverage


class CompileOwnerProvenanceTests(unittest.TestCase):
    def test_outside_dependency_with_testbench_basename_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "testbench.cpp"
            dependency.write_text("int dependency(){return undeclared_value;}", encoding="utf-8")
            result = TestbenchPreflight().compile_and_link(
                work_dir=root / "work", testbench_code="int main(){return 0;}",
                original_code="int original(){return 0;}",
                candidate_code="int candidate(){return 0;}",
                extra_sources=(str(dependency),),
            )
        self.assertEqual(result.failure_owner.value, "unknown")
        self.assertEqual(result.next_action, "inspect_compile_failure")

    def test_compile_timeout_does_not_own_testbench(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "agrefactor.evaluation.staged_preflight.subprocess.run",
            side_effect=subprocess.TimeoutExpired("g++", 1),
        ):
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory, testbench_code="int main(){return 0;}",
                original_code="int original(){return 0;}", candidate_code="int candidate(){return 0;}",
            )
        self.assertEqual(result.failure_owner.value, "unknown")
        self.assertEqual(result.failure_kind.value, "compile_timeout")

    def test_generated_template_bridge_error_still_repairs_testbench(self):
        source = "template<int N> void original(int (&values)[N]) {values[0]++;}"
        driver = (
            "void original(int, int *); void candidate(int, int *);"
            "int main(){int values[4]={}; original(4,values); candidate(4,values);return 0;}"
        )
        result = tb_coverage.check_original_execution(
            source, driver, "void candidate(int, int *);", "candidate",
            original_name="original",
        )
        self.assertEqual(result["failure_owner"], "testbench", result)
        self.assertEqual(result["next_action"], "repair_testbench", result)
        self.assertEqual(result["owner_authority"], "source_span", result)

    def test_original_template_body_error_is_not_testbench_owned(self):
        source = "template<int N> void original(int (&values)[N]) {missing_symbol();}"
        driver = (
            "void original(int (&values)[4]); void candidate(int (&values)[4]);"
            "int main(){int values[4]={}; original(values); candidate(values);return 0;}"
        )
        result = tb_coverage.check_original_execution(
            source, driver, "void candidate(int (&values)[4]);", "candidate",
            original_name="original",
        )
        self.assertEqual(result["failure_owner"], "original", result)
        self.assertEqual(result["next_action"], "review_original", result)

    def test_coverage_timeout_and_missing_artifacts_are_unknown(self):
        compile_result = Mock(returncode=0, stderr="", stdout="")
        run_result = Mock(returncode=0, stderr="", stdout="")
        for side_effect, expected in (
            ([subprocess.TimeoutExpired("g++", 1)], "compile_timeout"),
            ([compile_result, subprocess.TimeoutExpired("./csim_cov", 1)], "run_timeout"),
            ([compile_result, run_result], "no_gcda"),
        ):
            with patch.object(tb_coverage.subprocess, "run", side_effect=side_effect):
                result = tb_coverage.measure_coverage(
                    "void original(){}", "int main(){return 0;}", "void candidate(){}",
                )
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["failure_owner"], "unknown")
            self.assertEqual(result["next_action"], "review_unknown")

    def test_real_gcov_launch_failure_preserves_toolchain_owner(self):
        real_run = subprocess.run

        def run(command, **kwargs):
            if command[0] == "gcov":
                raise FileNotFoundError("gcov executable is unavailable")
            return real_run(command, **kwargs)

        with patch.object(tb_coverage.subprocess, "run", side_effect=run):
            result = tb_coverage.measure_coverage(
                "int original(){return 1;}",
                "int original(); int main(){return original()!=1;}",
                "void candidate(){}",
            )
        self.assertEqual(result["failure_owner"], "toolchain", result)
        self.assertEqual(result["failure_evidence_source"], "gcov launch error", result)


if __name__ == "__main__":
    unittest.main()
