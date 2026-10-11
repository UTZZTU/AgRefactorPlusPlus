import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import EvaluationSplit, TestSuiteSpec, resolve_target_profile
from agrefactor.evaluation import ValidationState, ValidationFeedbackCoordinator
from agrefactor.evaluation.csim_suite import CsimSuiteEvaluator
from agrefactor.evidence import FeedbackOwner, FeedbackStage
from agrefactor.runtime import CsimStageInputs, CsimValidationStageHandler
from tests.test_csim_stage import make_context
from tests.test_p4_0c_native_vitis_csim import (
    REFERENCE, CANDIDATE, TESTBENCH, _variables, _resolution, _verification,
)
from flow.tools.vitis_csim import run_vitis_csim


def compiler_failure(work_dir, _command, _limit, *, source="testbench.cpp", manifest=True):
    root = Path(work_dir)
    build = root / "native_csim/solution/csim/build"
    build.mkdir(parents=True, exist_ok=True)
    if manifest:
        (build / "csim.mk").write_text(
            "HLS_SOURCES = ../../../../testbench.cpp ../../../../reference.cpp ../../../../candidate.cpp\n"
        )
    report = root / "native_csim/solution/csim/report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "candidate_top_csim.log").write_text(
        "../../../../" + source + ":3:7: error: cannot convert an argument\n"
        "../../../../agrefactor_case_evidence.hpp:1:1: note: declared here\n"
    )
    return {"returncode": 1, "timeout": False, "stdout": "", "stderr": ""}


class NativeCompileOwnershipTests(unittest.TestCase):
    def tool(self, directory, *, source="testbench.cpp", manifest=True):
        with patch("flow.tools.vitis_csim.resolve_csynth_command", _resolution), \
             patch("flow.tools.vitis_csim.probe_csynth_version", _verification), \
             patch("flow.tools.general.run_cmd", lambda *args: compiler_failure(*args, source=source, manifest=manifest)):
            status, diagnostic = run_vitis_csim(directory, _variables())
        return status, diagnostic, json.loads((Path(directory)/"csim_invocation.json").read_text())

    def test_actual_build_manifest_and_primary_error_prove_testbench(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _, invocation = self.tool(directory)
            failure = invocation["compiler_failure"]
            self.assertEqual(status, "csim_execution_failed")
            self.assertEqual(failure["failure_owner"], "testbench")
            self.assertTrue(failure["evidence_complete"])
            self.assertEqual(failure["compiler_returncode"], None)
            self.assertEqual(failure["outer_returncode"], 1)
            self.assertEqual(invocation["simulation_execution"]["status"], "not_observed")

    def test_candidate_primary_error_remains_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, invocation = self.tool(directory, source="candidate.cpp")
            self.assertEqual(invocation["compiler_failure"]["failure_owner"], "candidate")

    def test_support_header_failure_does_not_become_testbench(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, invocation = self.tool(directory, source="agrefactor_case_evidence.hpp")
            self.assertEqual(invocation["compiler_failure"]["failure_owner"], "unknown")

    def test_no_build_manifest_cannot_prove_compile_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, invocation = self.tool(directory, manifest=False)
            self.assertFalse(invocation["compiler_failure"]["evidence_complete"])
            self.assertEqual(invocation["runtime_classification"]["failure_owner"], "unknown")
            self.assertEqual(invocation["simulation_execution"]["status"], "not_observed")

    def test_full_compiler_log_keeps_mixed_source_failures_unknown(self):
        def mixed_errors(*args):
            result = compiler_failure(*args)
            log = Path(args[0]) / "native_csim/solution/csim/report/candidate_top_csim.log"
            log.write_text("../../../../candidate.cpp:3:7: error: candidate compile failure\n"
                           + "template context " * 1500 + "\n" + log.read_text())
            return result
        with tempfile.TemporaryDirectory() as directory:
            with patch("flow.tools.vitis_csim.resolve_csynth_command", _resolution), \
                 patch("flow.tools.vitis_csim.probe_csynth_version", _verification), \
                 patch("flow.tools.general.run_cmd", mixed_errors):
                run_vitis_csim(directory, _variables())
            failure = json.loads((Path(directory) / "csim_invocation.json").read_text())["compiler_failure"]
            self.assertEqual(failure["failure_owner"], "unknown")
            self.assertFalse(failure["evidence_complete"])
            self.assertEqual(len(failure["diagnostics"]), 2)

    def test_missing_manifest_compile_failure_cannot_authorize_runtime_trial(self):
        suite = TestSuiteSpec(suite_id="native-compile", split=EvaluationSplit.PUBLIC)
        with tempfile.TemporaryDirectory() as directory:
            context = make_context((suite,))
            inputs = CsimStageInputs(
                work_dir=directory, original_code=REFERENCE, candidate_code=CANDIDATE,
                suite_testbench_codes={suite.suite_id: TESTBENCH},
                execution_backend="native_vitis", candidate_top_function="candidate_top",
                reference_top_function="reference_top", target_profile=resolve_target_profile(None),
            )
            with patch("flow.tools.vitis_csim.resolve_csynth_command", _resolution), \
                 patch("flow.tools.vitis_csim.probe_csynth_version", _verification), \
                 patch("flow.tools.general.run_cmd", lambda *args: compiler_failure(*args, manifest=False)), \
                 patch("flow.tools.vitis_csim._original_isolation_evidence") as isolation:
                report = CsimValidationStageHandler(
                    inputs, split=EvaluationSplit.PUBLIC,
                    evaluator=CsimSuiteEvaluator(executor=run_vitis_csim),
                )(context)
            isolation.assert_not_called()
            self.assertEqual(report.items[0].stage, FeedbackStage.COMPILE)
            self.assertEqual(report.items[0].owner, FeedbackOwner.UNKNOWN)
            self.assertFalse(report.items[0].metadata["repair_eligible"])

    def test_stage_routes_testbench_without_reference_candidate_trial(self):
        suite = TestSuiteSpec(suite_id="native-compile", split=EvaluationSplit.PUBLIC)
        with tempfile.TemporaryDirectory() as directory:
            context = make_context((suite,))
            inputs = CsimStageInputs(
                work_dir=directory, original_code=REFERENCE, candidate_code=CANDIDATE,
                suite_testbench_codes={suite.suite_id: TESTBENCH},
                execution_backend="native_vitis", candidate_top_function="candidate_top",
                reference_top_function="reference_top",
                target_profile=resolve_target_profile(None),
            )
            with patch("flow.tools.vitis_csim.resolve_csynth_command", _resolution), \
                 patch("flow.tools.vitis_csim.probe_csynth_version", _verification), \
                 patch("flow.tools.general.run_cmd", compiler_failure), \
                 patch("flow.tools.vitis_csim._original_isolation_evidence") as isolation:
                report = CsimValidationStageHandler(
                    inputs, split=EvaluationSplit.PUBLIC,
                    evaluator=CsimSuiteEvaluator(executor=run_vitis_csim),
                )(context)
            isolation.assert_not_called()
            self.assertEqual(report.items[0].owner, FeedbackOwner.TESTBENCH)
            self.assertEqual(report.items[0].stage, FeedbackStage.COMPILE)
            decision = ValidationFeedbackCoordinator(context.task).coordinate(
                report, ValidationState.PUBLIC_EVALUATION, coordination_id="compile-owner",
            )
            self.assertEqual(decision.route_action.value, "repair_testbench")


if __name__ == "__main__":
    unittest.main()
