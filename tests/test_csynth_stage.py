import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import TaskSpec
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackReport,
)
from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    CsynthStageInputs,
    CsynthValidationStageHandler,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
    read_csynth_invocation_summary,
)


CANDIDATE = (
    'extern "C" int candidate_top(int x) {\n'
    '    return x + 1;\n'
    '}\n'
)


def make_context(*, limits=None, trace_path=None):
    task = TaskSpec(
        task_id="csynth-stage-task",
        kernel_path="candidate_top.cpp",
        kernel_name="candidate_top",
    )
    return RunContext(
        run_id="csynth-stage-run",
        task=task,
        budget=BudgetManager(
            limits or BudgetLimits()
        ),
        trace=TraceRecorder(
            "csynth-stage-run",
            task_id=task.task_id,
            output_path=trace_path,
        ),
    )


def safe_preflight():
    return FeedbackReport(
        report_id="preflight-safe",
        source="testbench_preflight",
        metadata={"evidence_view": "agent_safe"},
    )


def write_invocation(
    directory,
    *,
    budget_status="consumed",
    execution_status="completed",
    returncode=0,
    timeout=False,
    verification_status="matched",
    budget_resource=None,
):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "phase": "csynth",
        "work_dir": str(path.resolve()),
        "top_kernel": "candidate_top",
        "source_files": ["candidate_top.cpp"],
        "target_profile": {
            "name": "default",
            "device": "xcu200-fsgd2104-2-e",
        },
        "requested_toolchain_version": "2023.2",
        "toolchain_version_verification": {
            "status": verification_status,
            "requested": "2023.2",
            "actual": (
                "2023.2"
                if verification_status == "matched"
                else "2024.1"
            ),
            "stdout": "private banner",
            "stderr": "",
        },
        "budget": {
            "status": budget_status,
            "checkpoint": (
                "before_csynth_launch"
                if budget_status == "consumed"
                else "before_version_probe"
            ),
            "resource": budget_resource,
        },
        "execution": {
            "status": execution_status,
            "returncode": returncode,
            "timeout": timeout,
        },
        "command": "/private/bin/vitis-run",
        "resolved_executable": "/private/bin/vitis-run",
    }
    (
        path / "csynth_invocation.json"
    ).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


class CsynthValidationStageHandlerTests(
    unittest.TestCase
):
    def handler(self, directory, executor):
        return CsynthValidationStageHandler(
            CsynthStageInputs(
                work_dir=directory,
                candidate_code=CANDIDATE,
                timelimit=17,
            ),
            executor=executor,
        )

    def test_success_returns_agent_safe_report(self):
        with tempfile.TemporaryDirectory() as directory:
            observed = {}

            def executor(
                work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                observed["budget"] = budget
                observed["timelimit"] = timelimit
                observed["code"] = variables["curr_code"]
                observed["kernel"] = (
                    variables["new_kernel_name"]
                )
                write_invocation(work_dir)
                return "succeeded", ""

            context = make_context()
            report = self.handler(
                directory,
                executor,
            )(context)

            self.assertFalse(report.blocking)
            self.assertEqual(report.items, ())
            self.assertEqual(
                report.metadata["evidence_view"],
                "agent_safe",
            )
            self.assertIs(
                observed["budget"],
                context.budget,
            )
            self.assertEqual(observed["timelimit"], 17)
            self.assertEqual(
                observed["kernel"],
                "candidate_top",
            )
            self.assertEqual(
                observed["code"],
                CANDIDATE,
            )
            self.assertTrue(
                report.metadata["physical_execution"]
            )
            self.assertTrue(
                report.metadata["tool_attempt_counted"]
            )

    def test_known_diagnostic_routes_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            def executor(
                work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                write_invocation(
                    work_dir,
                    returncode=1,
                )
                log = (
                    Path(work_dir)
                    / "csynth"
                    / "solution"
                    / "solution.log"
                )
                log.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                log.write_text(
                    "candidate_top.cpp:3:2: error: "
                    "use of undeclared identifier 'missing'\n",
                    encoding="utf-8",
                )
                return "csynth_failed", "generic failure"

            context = make_context()
            handler = self.handler(
                directory,
                executor,
            )
            result = ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: safe_preflight()
                    ),
                    ValidationState.CSYNTH: handler,
                }
            ).run(
                context,
                validation_id="validation",
            )

            self.assertEqual(
                result.final_state,
                ValidationState.REPAIR_PENDING,
            )
            selected = (
                result.steps[-1]
                .selected_feedback_items
            )
            self.assertTrue(selected)
            self.assertEqual(
                selected[0].owner,
                FeedbackOwner.CANDIDATE,
            )
            self.assertEqual(
                selected[0].category,
                FeedbackCategory.UNDECLARED_SYMBOL,
            )

    def test_budget_exception_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            def executor(
                work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                write_invocation(
                    work_dir,
                    budget_status="blocked",
                    execution_status="blocked_by_budget",
                    returncode=None,
                    budget_resource="csynth_calls",
                )
                raise BudgetExceededError(
                    "csynth_calls",
                    0,
                    1,
                )

            context = make_context()
            report = self.handler(
                directory,
                executor,
            )(context)

            self.assertTrue(report.blocking)
            self.assertEqual(
                report.items[0].category,
                FeedbackCategory.BUDGET_EXHAUSTED,
            )
            self.assertEqual(
                report.items[0].owner,
                FeedbackOwner.EVALUATOR,
            )
            self.assertFalse(
                report.metadata["physical_execution"]
            )
            self.assertFalse(
                report.metadata["tool_attempt_counted"]
            )
            self.assertEqual(
                report.metadata[
                    "execution_exception_type"
                ],
                "BudgetExceededError",
            )

    def test_version_mismatch_is_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            def executor(
                work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                write_invocation(
                    work_dir,
                    budget_status="available",
                    execution_status=(
                        "blocked_before_csynth"
                    ),
                    returncode=None,
                    verification_status="mismatch",
                )
                raise RuntimeError(
                    "private toolchain mismatch detail"
                )

            report = self.handler(
                directory,
                executor,
            )(make_context())

            self.assertEqual(
                report.items[0].category,
                FeedbackCategory.INVALID_CONFIGURATION,
            )
            self.assertEqual(
                report.items[0].owner,
                FeedbackOwner.CONFIGURATION,
            )
            self.assertFalse(
                report.metadata["physical_execution"]
            )

    def test_pre_artifact_exception_is_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            def executor(
                work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                raise ValueError(
                    "invalid input before artifact"
                )

            with self.assertRaises(ValueError):
                self.handler(
                    directory,
                    executor,
                )(make_context())

    def test_safe_report_and_trace_redact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = (
                Path(directory) / "trace.jsonl"
            )
            work_dir = Path(directory) / "work"

            def executor(
                raw_work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                write_invocation(
                    raw_work_dir,
                    returncode=1,
                )
                return (
                    "csynth_failed",
                    f"{work_dir.resolve()}/candidate_top.cpp "
                    "failed",
                )

            context = make_context(
                trace_path=trace_path
            )
            handler = self.handler(
                work_dir,
                executor,
            )
            ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: safe_preflight()
                    ),
                    ValidationState.CSYNTH: handler,
                }
            ).run(
                context,
                validation_id="validation",
            )

            report = handler(context)
            payload = json.dumps(
                report.to_dict(),
                sort_keys=True,
            )
            trace = trace_path.read_text(
                encoding="utf-8"
            )
            for text in (payload, trace):
                self.assertNotIn(
                    str(work_dir.resolve()),
                    text,
                )
                self.assertNotIn(
                    "/private/bin/vitis-run",
                    text,
                )
                self.assertNotIn(
                    '"command"',
                    text,
                )
                self.assertNotIn(
                    '"resolved_executable"',
                    text,
                )
            self.assertTrue(
                all(
                    item.evidence_ref is None
                    for item in report.items
                )
            )

    def test_invalid_executor_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            def executor(
                work_dir,
                variables,
                timelimit,
                *,
                budget,
            ):
                write_invocation(work_dir)
                return {"status": "succeeded"}

            with self.assertRaises(TypeError):
                self.handler(
                    directory,
                    executor,
                )(make_context())

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            CsynthStageInputs(
                work_dir="",
                candidate_code=CANDIDATE,
            )
        with self.assertRaises(ValueError):
            CsynthStageInputs(
                work_dir="/tmp/work",
                candidate_code="",
            )
        with self.assertRaises(ValueError):
            CsynthStageInputs(
                work_dir="/tmp/work",
                candidate_code=CANDIDATE,
                timelimit=0,
            )

    def test_summary_is_non_sensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            write_invocation(directory)
            summary = read_csynth_invocation_summary(
                directory
            )
            payload = json.dumps(
                summary,
                sort_keys=True,
            )

            self.assertEqual(
                summary["budget_status"],
                "consumed",
            )
            self.assertEqual(
                summary["verification_status"],
                "matched",
            )
            self.assertNotIn(
                str(Path(directory).resolve()),
                payload,
            )
            self.assertNotIn(
                "/private/bin/vitis-run",
                payload,
            )

    def test_missing_summary_is_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(
                read_csynth_invocation_summary(
                    directory
                )
            )


if __name__ == "__main__":
    unittest.main()
