from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.evaluation import (
    FeedbackRouteAction,
    ValidationState,
    ValidationStateMachine,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
)
from agrefactor.evaluation.csim_suite import (
    CsimSuiteEvaluator,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CsimStageInputs,
    CsimValidationStageHandler,
    LocalCandidateValidationHandlerFactory,
    RunContext,
    TraceRecorder,
)
from agrefactor.runtime.candidate_repair_integration import (
    CandidateValidationPlanRequest,
)
from flow.tools.vitis_csim import (
    make_native_vitis_csim_script,
    make_native_vitis_csim_tcl,
    run_vitis_csim,
)


REFERENCE = (
    'extern "C" int reference_top(int x) '
    "{ return x + 1; }\n"
)
CANDIDATE = (
    'extern "C" int candidate_top(int x) '
    "{ return x + 1; }\n"
)
TESTBENCH = (
    'extern "C" int reference_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    "int main() { return "
    "reference_top(4) == candidate_top(4) ? 0 : 1; }\n"
)


def _resolution(_profile):
    return {
        "command": "fake-vitis-run --input_file vitis.tcl",
        "command_source": "fixture",
        "resolved_executable": "/fixture/vitis-run",
        "resolved_settings_path": None,
    }


def _verification(_resolution, requested):
    return {
        "status": "matched",
        "requested": requested,
        "actual": requested,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }


def _variables():
    profile = resolve_target_profile(None)
    return ContextVariables(
        data={
            "orig_code": REFERENCE,
            "curr_code": CANDIDATE,
            "testbench": TESTBENCH,
            "candidate_top_function": (
                "candidate_top"
            ),
            "target_profile": profile,
        }
    )


def _task(*, public=True, hidden=True):
    suites = []
    if public:
        suites.append(
            TestSuiteSpec(
                suite_id="public-main",
                split=EvaluationSplit.PUBLIC,
            )
        )
    if hidden:
        suites.append(
            TestSuiteSpec(
                suite_id="hidden-final",
                split=EvaluationSplit.HIDDEN,
            )
        )
    return TaskSpec(
        task_id="p4-0c-test",
        kernel_path="candidate.cpp",
        kernel_name="candidate_top",
        target=resolve_target_profile(None),
        test_suites=tuple(suites),
    )


class NativeVitisCsimToolTests(unittest.TestCase):
    def test_tcl_uses_design_and_tb_roles(self):
        tcl = make_native_vitis_csim_tcl(
            top_kernel="candidate_top",
            design_source="candidate.cpp",
            reference_source="reference.cpp",
            testbench_source="testbench.cpp",
            target_profile=resolve_target_profile(None),
        )
        self.assertIn(
            'add_files "candidate.cpp"',
            tcl,
        )
        self.assertIn(
            'add_files -tb "reference.cpp"',
            tcl,
        )
        self.assertIn(
            'add_files -tb "testbench.cpp"',
            tcl,
        )

    def test_tcl_runs_csim_not_csynth(self):
        tcl = make_native_vitis_csim_tcl(
            top_kernel="candidate_top",
            design_source="candidate.cpp",
            reference_source="reference.cpp",
            testbench_source="testbench.cpp",
            target_profile=resolve_target_profile(None),
        )
        self.assertIn("csim_design -clean", tcl)
        self.assertNotIn("csynth_design", tcl)

    def test_tcl_rejects_newline_in_top(self):
        with self.assertRaises(ValueError):
            make_native_vitis_csim_tcl(
                top_kernel="bad\nname",
                design_source="candidate.cpp",
                reference_source="reference.cpp",
                testbench_source="testbench.cpp",
                target_profile=resolve_target_profile(None),
            )

    def test_script_writes_three_sources_and_tcl(self):
        with tempfile.TemporaryDirectory() as directory:
            result = make_native_vitis_csim_script(
                work_dir=directory,
                original_code=REFERENCE,
                candidate_code=CANDIDATE,
                testbench_code=TESTBENCH,
                top_kernel="candidate_top",
                target_profile=resolve_target_profile(None),
            )
            self.assertEqual(
                {item["role"] for item in result["source_files"]},
                {
                    "design",
                    "testbench_reference",
                    "testbench_driver",
                },
            )
            for name in (
                "candidate.cpp",
                "reference.cpp",
                "testbench.cpp",
                "vitis.tcl",
            ):
                self.assertTrue(
                    (Path(directory) / name).is_file()
                )

    def test_success_consumes_one_tool_and_one_csim(self):
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_csim_calls=1,
                max_compile_calls=0,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.vitis_csim."
                    "probe_csynth_version",
                    _verification,
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: {
                        "returncode": 0,
                        "timeout": False,
                        "stdout": "",
                        "stderr": "",
                    },
                ),
            ):
                status, _ = run_vitis_csim(
                    directory,
                    _variables(),
                    budget=budget,
                )
        self.assertEqual(status, "succeeded")
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.csim_calls, 1)
        self.assertEqual(usage.compile_calls, 0)

    def test_budget_blocks_before_tool_launch(self):
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=0,
                max_csim_calls=0,
            )
        )
        launched = []
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: launched.append(True),
                ),
            ):
                with self.assertRaises(Exception):
                    run_vitis_csim(
                        directory,
                        _variables(),
                        budget=budget,
                    )
            invocation = json.loads(
                (
                    Path(directory)
                    / "csim_invocation.json"
                ).read_text(encoding="utf-8")
            )
        self.assertEqual(launched, [])
        self.assertEqual(
            invocation["budget"]["status"],
            "blocked",
        )

    def test_version_mismatch_blocks_launch(self):
        launched = []
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.vitis_csim."
                    "probe_csynth_version",
                    lambda _r, requested: {
                        "status": "mismatch",
                        "requested": requested,
                        "actual": "9999.9",
                    },
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: launched.append(True),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    run_vitis_csim(
                        directory,
                        _variables(),
                    )
        self.assertEqual(launched, [])

    def test_compile_diagnostic_maps_to_tb_compile_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.vitis_csim."
                    "probe_csynth_version",
                    _verification,
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: {
                        "returncode": 1,
                        "timeout": False,
                        "stdout": "",
                        "stderr": (
                            "testbench.cpp:4: error: "
                            "unknown symbol"
                        ),
                    },
                ),
            ):
                status, diagnostic = run_vitis_csim(
                    directory,
                    _variables(),
                )
        self.assertEqual(
            status,
            "tb_compile_failed",
        )
        self.assertIn("testbench.cpp", diagnostic)

    def test_functional_failure_maps_to_csim_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.vitis_csim."
                    "probe_csynth_version",
                    _verification,
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: {
                        "returncode": 1,
                        "timeout": False,
                        "stdout": "mismatch index=2",
                        "stderr": "",
                    },
                ),
            ):
                status, diagnostic = run_vitis_csim(
                    directory,
                    _variables(),
                )
        self.assertEqual(status, "csim_failed")
        self.assertIn("mismatch", diagnostic)

    def test_timeout_maps_to_csim_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.vitis_csim."
                    "probe_csynth_version",
                    _verification,
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: {
                        "returncode": -9,
                        "timeout": True,
                        "stdout": "",
                        "stderr": "",
                    },
                ),
            ):
                status, diagnostic = run_vitis_csim(
                    directory,
                    _variables(),
                )
        self.assertEqual(status, "csim_failed")
        self.assertIn("timed out", diagnostic)


class CsimStageBackendContractTests(unittest.TestCase):
    def test_host_backend_remains_default(self):
        inputs = CsimStageInputs(
            work_dir="work",
            original_code=REFERENCE,
            candidate_code=CANDIDATE,
            suite_testbench_codes={
                "public-main": TESTBENCH
            },
        )
        self.assertEqual(
            inputs.execution_backend,
            "host_differential",
        )

    def test_native_backend_requires_top(self):
        with self.assertRaises(ValueError):
            CsimStageInputs(
                work_dir="work",
                original_code=REFERENCE,
                candidate_code=CANDIDATE,
                suite_testbench_codes={
                    "public-main": TESTBENCH
                },
                execution_backend="native_vitis",
                target_profile=resolve_target_profile(None),
            )

    def test_native_backend_requires_target(self):
        with self.assertRaises(ValueError):
            CsimStageInputs(
                work_dir="work",
                original_code=REFERENCE,
                candidate_code=CANDIDATE,
                suite_testbench_codes={
                    "public-main": TESTBENCH
                },
                execution_backend="native_vitis",
                candidate_top_function=(
                    "candidate_top"
                ),
            )

    def test_hidden_cannot_use_native_backend(self):
        inputs = CsimStageInputs(
            work_dir="work",
            original_code=REFERENCE,
            candidate_code=CANDIDATE,
            suite_testbench_codes={
                "hidden-final": TESTBENCH
            },
            execution_backend="native_vitis",
            candidate_top_function="candidate_top",
            target_profile=resolve_target_profile(None),
        )
        with self.assertRaises(ValueError):
            CsimValidationStageHandler(
                inputs,
                split=EvaluationSplit.HIDDEN,
            )

    def test_native_handler_selects_native_executor(self):
        inputs = CsimStageInputs(
            work_dir="work",
            original_code=REFERENCE,
            candidate_code=CANDIDATE,
            suite_testbench_codes={
                "public-main": TESTBENCH
            },
            execution_backend="native_vitis",
            candidate_top_function="candidate_top",
            target_profile=resolve_target_profile(None),
        )
        handler = CsimValidationStageHandler(
            inputs,
            split=EvaluationSplit.PUBLIC,
        )
        self.assertEqual(
            handler.inputs.execution_backend,
            "native_vitis",
        )
        self.assertEqual(
            handler._evaluator.executor_identity,
            "flow.tools.vitis_csim.run_vitis_csim",
        )

    def test_suite_evaluator_records_configured_identity(self):
        evaluator = CsimSuiteEvaluator(
            executor=lambda *_args, **_kwargs: (
                "succeeded",
                "",
            ),
            executor_identity="fixture.native",
        )
        self.assertEqual(
            evaluator.executor_identity,
            "fixture.native",
        )

    def test_native_toolchain_failure_is_typed(self):
        task = _task(public=True, hidden=False)
        inputs = CsimStageInputs(
            work_dir=tempfile.mkdtemp(
                prefix="p4_0c_toolchain_"
            ),
            original_code=REFERENCE,
            candidate_code=CANDIDATE,
            suite_testbench_codes={
                "public-main": TESTBENCH
            },
            execution_backend="native_vitis",
            candidate_top_function="candidate_top",
            target_profile=task.target,
        )
        handler = CsimValidationStageHandler(
            inputs,
            split=EvaluationSplit.PUBLIC,
        )
        context = RunContext(
            run_id="p4-0c-toolchain",
            task=task,
            budget=BudgetManager(BudgetLimits()),
            trace=TraceRecorder(
                "p4-0c-toolchain",
                task_id=task.task_id,
            ),
        )
        with (
            patch(
                "flow.tools.vitis_csim."
                "resolve_csynth_command",
                _resolution,
            ),
            patch(
                "flow.tools.vitis_csim."
                "probe_csynth_version",
                lambda _r, requested: {
                    "status": "mismatch",
                    "requested": requested,
                    "actual": "9999.9",
                },
            ),
        ):
            report = handler(context)
        self.assertTrue(report.blocking)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.TOOLCHAIN_FAILURE,
        )
        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.TOOLCHAIN,
        )

    def test_native_timeout_is_unknown_safe_not_candidate_mismatch(self):
        task = _task(public=True, hidden=False)
        with tempfile.TemporaryDirectory(
            prefix="p4_0c_timeout_"
        ) as directory:
            inputs = CsimStageInputs(
                work_dir=directory,
                original_code=REFERENCE,
                candidate_code=CANDIDATE,
                suite_testbench_codes={
                    "public-main": TESTBENCH
                },
                execution_backend="native_vitis",
                candidate_top_function=(
                    "candidate_top"
                ),
                target_profile=task.target,
            )
            handler = CsimValidationStageHandler(
                inputs,
                split=EvaluationSplit.PUBLIC,
            )
            context = RunContext(
                run_id="p4-0c-timeout",
                task=task,
                budget=BudgetManager(
                    BudgetLimits()
                ),
                trace=TraceRecorder(
                    "p4-0c-timeout",
                    task_id=task.task_id,
                ),
            )
            with (
                patch(
                    "flow.tools.vitis_csim."
                    "resolve_csynth_command",
                    _resolution,
                ),
                patch(
                    "flow.tools.vitis_csim."
                    "probe_csynth_version",
                    _verification,
                ),
                patch(
                    "flow.tools.general.run_cmd",
                    lambda *_args: {
                        "returncode": -9,
                        "timeout": True,
                        "stdout": "",
                        "stderr": "",
                    },
                ),
            ):
                report = handler(context)
        self.assertTrue(report.blocking)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.TIMEOUT,
        )
        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.UNKNOWN,
        )
        self.assertEqual(
            report.items[0].metadata.get("timeout_class"),
            "ownership_unknown",
        )
        self.assertEqual(
            report.items[0].metadata.get("owner_authority"),
            "unknown",
        )
        self.assertFalse(
            report.items[0].metadata.get("repair_eligible")
        )
        self.assertTrue(
            report.items[0].metadata.get("advisory_eligible")
        )
        self.assertNotEqual(
            report.items[0].category,
            FeedbackCategory.FUNCTIONAL_MISMATCH,
        )


class UnifiedOrderTests(unittest.TestCase):
    def _continue(self, machine, state):
        from agrefactor.evaluation import (
            FeedbackRouteDecision,
        )
        return machine.transition(
            state,
            FeedbackRouteDecision(
                decision_id="decision",
                action=(
                    FeedbackRouteAction.CONTINUE_VALIDATION
                ),
                reason="passed",
                source_report_id="report",
                blocking_feedback_ids=(),
                selected_feedback_ids=(),
                metadata={
                    "evidence_view": "agent_safe"
                },
            ),
            transition_id="transition",
        ).next_state

    def test_preflight_advances_to_public(self):
        machine = ValidationStateMachine(
            _task(public=True, hidden=True)
        )
        self.assertIs(
            self._continue(
                machine,
                ValidationState.PREFLIGHT,
            ),
            ValidationState.PUBLIC_EVALUATION,
        )

    def test_public_advances_to_csynth(self):
        machine = ValidationStateMachine(
            _task(public=True, hidden=True)
        )
        self.assertIs(
            self._continue(
                machine,
                ValidationState.PUBLIC_EVALUATION,
            ),
            ValidationState.CSYNTH,
        )

    def test_csynth_advances_through_public_cosim_to_hidden(self):
        machine = ValidationStateMachine(
            _task(public=True, hidden=True)
        )
        public_cosim = self._continue(
            machine,
            ValidationState.CSYNTH,
        )
        self.assertIs(
            public_cosim,
            ValidationState.PUBLIC_COSIM,
        )
        self.assertIs(
            self._continue(machine, public_cosim),
            ValidationState.HIDDEN_EVALUATION,
        )

    def test_no_public_keeps_preflight_to_csynth(self):
        machine = ValidationStateMachine(
            _task(public=False, hidden=True)
        )
        self.assertIs(
            self._continue(
                machine,
                ValidationState.PREFLIGHT,
            ),
            ValidationState.CSYNTH,
        )


class ProductFactoryContractTests(unittest.TestCase):
    def test_local_factory_public_native_hidden_host(self):
        with tempfile.TemporaryDirectory() as directory:
            task = _task(public=True, hidden=True)
            plan = CandidateValidationPlanRequest(
                task=task,
                candidate_code=CANDIDATE,
                original_code=REFERENCE,
                preflight_testbench_code=TESTBENCH,
                suite_testbench_codes={
                    "public-main": TESTBENCH,
                    "hidden-final": TESTBENCH,
                },
                attempt=0,
                validation_id="validation",
                reference_top_function=(
                    "reference_top"
                ),
                candidate_top_function=(
                    "candidate_top"
                ),
            )
            handlers = (
                LocalCandidateValidationHandlerFactory(
                    directory
                ).build(plan)
            )
        self.assertEqual(
            handlers[
                ValidationState.PUBLIC_EVALUATION
            ].inputs.execution_backend,
            "native_vitis",
        )
        self.assertEqual(
            handlers[
                ValidationState.HIDDEN_EVALUATION
            ].inputs.execution_backend,
            "host_differential",
        )


if __name__ == "__main__":
    unittest.main()
