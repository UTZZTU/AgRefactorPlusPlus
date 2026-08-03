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
    BudgetLimits,
    BudgetManager,
    PreflightStageInputs,
    PreflightValidationStageHandler,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
    read_preflight_invocation_summary,
)


ORIGINAL = (
    'extern "C" int original_top(int x) {\n'
    '    return x + 1;\n'
    '}\n'
)
CANDIDATE = (
    'extern "C" int candidate_top(int x) {\n'
    '    return x + 1;\n'
    '}\n'
)
TESTBENCH = (
    'extern "C" int original_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    'int main() {\n'
    '    return original_top(4) != candidate_top(4);\n'
    '}\n'
)
BROKEN_TESTBENCH = (
    'extern "C" int original_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    'int main() {\n'
    '    return original_top(4) != candidate_top(4)\n'
    '}\n'
)
BROKEN_CANDIDATE = (
    'extern "C" int candidate_top(int x) {\n'
    '    return x + ;\n'
    '}\n'
)


def make_context(*, limits=None, trace_path=None):
    task = TaskSpec(
        task_id="preflight-stage-task",
        kernel_path="kernel.cpp",
        kernel_name="candidate_top",
    )
    return RunContext(
        run_id="preflight-stage-run",
        task=task,
        budget=BudgetManager(
            limits or BudgetLimits()
        ),
        trace=TraceRecorder(
            "preflight-stage-run",
            task_id=task.task_id,
            output_path=trace_path,
        ),
    )


def passed_csynth_report():
    return FeedbackReport(
        report_id="csynth-safe",
        source="csynth",
        metadata={"evidence_view": "agent_safe"},
    )


class PreflightValidationStageHandlerTests(
    unittest.TestCase
):
    def handler(
        self,
        directory,
        *,
        testbench=TESTBENCH,
        candidate=CANDIDATE,
    ):
        return PreflightValidationStageHandler(
            PreflightStageInputs(
                work_dir=directory,
                testbench_code=testbench,
                original_code=ORIGINAL,
                candidate_code=candidate,
            )
        )

    def test_real_success_consumes_shared_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            context = make_context()
            report = self.handler(directory)(context)
            usage = context.budget.snapshot()
            summary = read_preflight_invocation_summary(
                directory
            )

            self.assertFalse(report.blocking)
            self.assertEqual(report.items, ())
            self.assertEqual(
                report.metadata["evidence_view"],
                "agent_safe",
            )
            self.assertTrue(
                report.metadata["physical_execution"]
            )
            self.assertEqual(usage.tool_calls, 4)
            self.assertEqual(usage.compile_calls, 4)
            self.assertTrue(
                (
                    Path(directory)
                    / "testbench_preflight"
                ).is_file()
            )
            self.assertEqual(
                summary["budget_status"],
                "consumed",
            )
            self.assertEqual(
                summary["execution_status"],
                "completed",
            )
            self.assertEqual(
                summary["execution_returncode"],
                0,
            )

    def test_testbench_failure_routes_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            context = make_context()
            calls = []
            preflight = self.handler(
                directory,
                testbench=BROKEN_TESTBENCH,
            )
            result = ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: (
                            calls.append("preflight")
                            or preflight(ctx)
                        )
                    ),
                    ValidationState.CSYNTH: (
                        lambda ctx: (
                            calls.append("csynth")
                            or passed_csynth_report()
                        )
                    ),
                }
            ).run(
                context,
                validation_id="validation",
            )

            self.assertEqual(
                result.final_state,
                ValidationState.REPAIR_PENDING,
            )
            self.assertEqual(calls, ["preflight"])
            selected = (
                result.steps[0]
                .selected_feedback_items
            )
            self.assertEqual(
                selected[0].owner,
                FeedbackOwner.TESTBENCH,
            )
            self.assertEqual(
                selected[0].category,
                FeedbackCategory.SYNTAX_ERROR,
            )

    def test_candidate_failure_routes_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.handler(
                directory,
                candidate=BROKEN_CANDIDATE,
            )(make_context())

            self.assertTrue(report.blocking)
            self.assertEqual(
                report.items[0].owner,
                FeedbackOwner.CANDIDATE,
            )
            self.assertEqual(
                report.items[0].category,
                FeedbackCategory.SYNTAX_ERROR,
            )

    def test_zero_compile_budget_returns_block(self):
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(
                limits=BudgetLimits(
                    max_compile_calls=0,
                )
            )
            calls = []
            preflight = self.handler(directory)
            result = ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: (
                            calls.append("preflight")
                            or preflight(ctx)
                        )
                    ),
                    ValidationState.CSYNTH: (
                        lambda ctx: (
                            calls.append("csynth")
                            or passed_csynth_report()
                        )
                    ),
                }
            ).run(
                context,
                validation_id="validation",
            )
            usage = context.budget.snapshot()
            summary = read_preflight_invocation_summary(
                directory
            )

            self.assertEqual(
                result.final_state,
                ValidationState.BLOCKED,
            )
            self.assertEqual(calls, ["preflight"])
            self.assertEqual(usage.tool_calls, 0)
            self.assertEqual(usage.compile_calls, 0)
            self.assertEqual(
                result.steps[0].route_action.value,
                "stop_budget_exhausted",
            )
            self.assertEqual(
                summary["budget_status"],
                "blocked",
            )
            self.assertEqual(
                summary["execution_status"],
                "blocked_by_budget",
            )
            self.assertFalse(
                (
                    Path(directory)
                    / "testbench_preflight"
                ).exists()
            )

    def test_safe_report_does_not_leak_operator_data(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            report = self.handler(
                directory,
                candidate=BROKEN_CANDIDATE,
            )(make_context())
            payload = json.dumps(
                report.to_dict(),
                sort_keys=True,
            )

            self.assertNotIn(
                str(Path(directory).resolve()),
                payload,
            )
            for forbidden in (
                '"command"',
                '"stdout"',
                '"stderr"',
                '"artifacts"',
                "testbench_preflight_invocation.json",
            ):
                self.assertNotIn(forbidden, payload)
            self.assertTrue(
                all(
                    item.evidence_ref is None
                    for item in report.items
                )
            )

    def test_budget_report_is_agent_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.handler(directory)(
                make_context(
                    limits=BudgetLimits(
                        max_tool_calls=0,
                    )
                )
            )
            payload = json.dumps(
                report.to_dict(),
                sort_keys=True,
            )

            self.assertEqual(
                report.items[0].category,
                FeedbackCategory.BUDGET_EXHAUSTED,
            )
            self.assertEqual(
                report.items[0].owner,
                FeedbackOwner.EVALUATOR,
            )
            self.assertEqual(
                report.metadata["evidence_view"],
                "agent_safe",
            )
            self.assertNotIn(
                str(Path(directory).resolve()),
                payload,
            )

    def test_orchestrator_trace_is_agent_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            work_dir = Path(directory) / "work"
            context = make_context(
                trace_path=trace_path,
            )
            preflight = self.handler(
                work_dir,
                candidate=BROKEN_CANDIDATE,
            )
            ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: preflight,
                    ValidationState.CSYNTH: (
                        lambda ctx: passed_csynth_report()
                    ),
                }
            ).run(
                context,
                validation_id="validation",
            )

            trace = trace_path.read_text(
                encoding="utf-8"
            )
            self.assertIn(
                '"evidence_view": "agent_safe"',
                trace,
            )
            self.assertNotIn(
                str(work_dir.resolve()),
                trace,
            )
            for forbidden in (
                '"command"',
                '"stdout"',
                '"stderr"',
                '"artifacts"',
            ):
                self.assertNotIn(forbidden, trace)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            PreflightStageInputs(
                work_dir="",
                testbench_code=TESTBENCH,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
        with self.assertRaises(ValueError):
            PreflightStageInputs(
                work_dir="/tmp/work",
                testbench_code="",
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

    def test_rejects_non_context(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                self.handler(directory)(
                    {"budget": "invalid"}
                )

    def test_missing_invocation_summary_is_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(
                read_preflight_invocation_summary(
                    directory
                )
            )


if __name__ == "__main__":
    unittest.main()
