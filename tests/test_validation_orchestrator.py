import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    RunContext,
    TraceRecorder,
    ValidationOrchestrationResult,
    ValidationOrchestrator,
)


SECRET = "HIDDEN_ORCHESTRATOR_SECRET"
SECRET_PATH = "/private/hidden/result.log"


def make_task(*, public=False, hidden=False):
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
        task_id="orchestrator-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        test_suites=tuple(suites),
    )


def make_context(task, *, output_path=None):
    return RunContext(
        run_id="run",
        task=task,
        budget=BudgetManager(BudgetLimits()),
        trace=TraceRecorder(
            "run",
            task_id=task.task_id,
            output_path=output_path,
        ),
    )


def safe_report(report_id, source, *, item=None):
    return FeedbackReport(
        report_id=report_id,
        source=source,
        items=() if item is None else (item,),
        source_evidence={"redacted": True},
        metadata={"evidence_view": "agent_safe"},
    )


def hidden_report(*, item=None, report_id=SECRET):
    return FeedbackReport(
        report_id=report_id,
        source="test_evaluation",
        items=() if item is None else (item,),
        source_evidence={
            "secret": SECRET,
            "path": SECRET_PATH,
        },
        metadata={"evidence_view": "operator_full"},
    )


def item(
    feedback_id,
    *,
    owner,
    category,
    stage=FeedbackStage.TEST,
    detail="safe diagnostic",
):
    return FeedbackItem(
        feedback_id=feedback_id,
        stage=stage,
        category=category,
        severity=FeedbackSeverity.ERROR,
        owner=owner,
        summary="normalized feedback",
        detail=detail,
        source="test",
    )


class ValidationOrchestratorTests(unittest.TestCase):
    def test_pass_order_and_shared_context(self):
        calls = []
        task = make_task()
        context = make_context(task)

        def handler(name, source):
            def run(ctx):
                calls.append(
                    (name, id(ctx.budget), id(ctx.trace))
                )
                return safe_report(name, source)
            return run

        result = ValidationOrchestrator(
            {
                ValidationState.PREFLIGHT: handler(
                    "preflight",
                    "testbench_preflight",
                ),
                ValidationState.CSYNTH: handler(
                    "csynth",
                    "csynth",
                ),
            }
        ).run(
            context,
            validation_id="validation",
        )

        self.assertTrue(result.accepted)
        self.assertEqual(
            [entry[0] for entry in calls],
            ["preflight", "csynth"],
        )
        self.assertEqual(
            {entry[1] for entry in calls},
            {id(context.budget)},
        )
        self.assertEqual(
            {entry[2] for entry in calls},
            {id(context.trace)},
        )

    def test_public_hidden_order(self):
        calls = []
        task = make_task(public=True, hidden=True)
        context = make_context(task)

        handlers = {
            ValidationState.PREFLIGHT: lambda ctx: (
                calls.append("preflight")
                or safe_report(
                    "preflight",
                    "testbench_preflight",
                )
            ),
            ValidationState.CSYNTH: lambda ctx: (
                calls.append("csynth")
                or safe_report("csynth", "csynth")
            ),
            ValidationState.PUBLIC_EVALUATION: (
                lambda ctx: (
                    calls.append("public")
                    or safe_report(
                        "public",
                        "test_evaluation",
                    )
                )
            ),
            ValidationState.PUBLIC_COSIM: (
                lambda ctx: (
                    calls.append("public_cosim")
                    or safe_report(
                        "public_cosim",
                        "test_evaluation",
                    )
                )
            ),
            ValidationState.HIDDEN_EVALUATION: (
                lambda ctx: (
                    calls.append("hidden")
                    or hidden_report(
                        report_id="hidden-report"
                    )
                )
            ),
        }
        result = ValidationOrchestrator(
            handlers
        ).run(
            context,
            validation_id="validation",
        )

        self.assertEqual(
            calls,
            ["preflight", "public", "csynth", "public_cosim", "hidden"],
        )
        self.assertTrue(result.accepted)

    def test_repair_stops_before_next_handler(self):
        calls = []
        repair = item(
            "preflight.safe.1",
            owner=FeedbackOwner.TESTBENCH,
            category=FeedbackCategory.SYNTAX_ERROR,
            stage=FeedbackStage.COMPILE,
        )
        result = ValidationOrchestrator(
            {
                ValidationState.PREFLIGHT: (
                    lambda ctx: (
                        calls.append("preflight")
                        or safe_report(
                            "preflight",
                            "testbench_preflight",
                            item=repair,
                        )
                    )
                ),
                ValidationState.CSYNTH: (
                    lambda ctx: (
                        calls.append("csynth")
                        or safe_report(
                            "csynth",
                            "csynth",
                        )
                    )
                ),
            }
        ).run(
            make_context(make_task()),
            validation_id="validation",
        )

        self.assertTrue(result.repair_pending)
        self.assertEqual(calls, ["preflight"])
        self.assertEqual(
            result.steps[0].selected_feedback_items,
            (repair,),
        )

    def test_unknown_requires_review(self):
        unknown = item(
            "csynth.safe.1",
            owner=FeedbackOwner.UNKNOWN,
            category=FeedbackCategory.UNKNOWN,
            stage=FeedbackStage.CSYNTH,
        )
        result = ValidationOrchestrator(
            {
                ValidationState.PREFLIGHT: (
                    lambda ctx: safe_report(
                        "preflight",
                        "testbench_preflight",
                    )
                ),
                ValidationState.CSYNTH: (
                    lambda ctx: safe_report(
                        "csynth",
                        "csynth",
                        item=unknown,
                    )
                ),
            }
        ).run(
            make_context(make_task()),
            validation_id="validation",
        )
        self.assertEqual(
            result.final_state,
            ValidationState.REVIEW_REQUIRED,
        )

    def test_budget_blocks_public(self):
        calls = []
        budget_item = item(
            "csynth.budget.1",
            owner=FeedbackOwner.EVALUATOR,
            category=FeedbackCategory.BUDGET_EXHAUSTED,
            stage=FeedbackStage.CONFIGURATION,
        )
        result = ValidationOrchestrator(
            {
                ValidationState.PREFLIGHT: (
                    lambda ctx: (
                        calls.append("preflight")
                        or safe_report(
                            "preflight",
                            "testbench_preflight",
                        )
                    )
                ),
                ValidationState.CSYNTH: (
                    lambda ctx: (
                        calls.append("csynth")
                        or safe_report(
                            "csynth",
                            "csynth",
                            item=budget_item,
                        )
                    )
                ),
                ValidationState.PUBLIC_EVALUATION: (
                    lambda ctx: (
                        calls.append("public")
                        or safe_report(
                            "public",
                            "test_evaluation",
                        )
                    )
                ),
                ValidationState.PUBLIC_COSIM: (
                    lambda ctx: safe_report(
                        "public_cosim",
                        "test_evaluation",
                    )
                ),
            }
        ).run(
            make_context(make_task(public=True)),
            validation_id="validation",
        )

        self.assertEqual(
            result.final_state,
            ValidationState.BLOCKED,
        )
        self.assertEqual(
            calls,
            ["preflight", "public", "csynth"],
        )

    def test_hidden_data_not_in_result_or_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            hidden_item = FeedbackItem(
                feedback_id="hidden.secret.item",
                stage=FeedbackStage.TEST,
                category=(
                    FeedbackCategory.FUNCTIONAL_MISMATCH
                ),
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="hidden mismatch",
                detail=SECRET,
                source="test_evaluation",
                evidence_ref=SECRET_PATH,
            )
            result = ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: safe_report(
                            "preflight",
                            "testbench_preflight",
                        )
                    ),
                    ValidationState.CSYNTH: (
                        lambda ctx: safe_report(
                            "csynth",
                            "csynth",
                        )
                    ),
                    ValidationState.HIDDEN_EVALUATION: (
                        lambda ctx: hidden_report(
                            item=hidden_item
                        )
                    ),
                }
            ).run(
                make_context(
                    make_task(hidden=True),
                    output_path=trace_path,
                ),
                validation_id="validation",
            )

            combined = json.dumps(
                result.to_dict(),
                sort_keys=True,
            ) + trace_path.read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                result.final_state,
                ValidationState.REJECTED,
            )
            for forbidden in (
                SECRET,
                "hidden.secret.item",
                SECRET_PATH,
            ):
                self.assertNotIn(forbidden, combined)
            self.assertIsNone(
                result.steps[-1].source_report_id
            )

            events = [
                json.loads(line)
                for line in trace_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            hidden_feedback = next(
                event
                for event in events
                if (
                    event["event"] == "validation.feedback"
                    and event["phase"] == "hidden_evaluation"
                )
            )
            feedback_summary = hidden_feedback[
                "metadata"
            ]["feedback_report_summary"]
            self.assertEqual(
                feedback_summary["report_id"],
                "validation.step.3.hidden-report",
            )
            self.assertEqual(
                feedback_summary["source"],
                "test_evaluation",
            )
            self.assertEqual(
                feedback_summary["item_count"],
                1,
            )
            self.assertTrue(feedback_summary["blocking"])
            self.assertEqual(
                feedback_summary["items"],
                [
                    {
                        "stage": "test",
                        "category": "functional_mismatch",
                        "severity": "error",
                        "owner": "candidate",
                        "blocking": True,
                    }
                ],
            )
            self.assertEqual(
                set(feedback_summary),
                {
                    "schema_version",
                    "report_id",
                    "source",
                    "item_count",
                    "blocking",
                    "items",
                    "source_report_id_redacted",
                    "item_identifiers_retained",
                    "item_text_retained",
                    "source_evidence_retained",
                },
            )

            hidden_transition = next(
                event
                for event in events
                if (
                    event["event"]
                    == "validation.transition"
                    and event["phase"]
                    == "hidden_evaluation"
                )
            )
            self.assertEqual(
                hidden_transition["metadata"][
                    "evidence_view"
                ],
                "operator_full",
            )
            decision_summary = hidden_transition[
                "metadata"
            ]["route_decision_summary"]
            self.assertEqual(
                decision_summary["decision_id"],
                "validation.step.3.route",
            )
            self.assertEqual(
                decision_summary["source_report_id"],
                feedback_summary["report_id"],
            )
            self.assertEqual(
                decision_summary["action"],
                "repair_candidate",
            )
            self.assertEqual(
                decision_summary["blocking_feedback_count"],
                1,
            )
            self.assertEqual(
                decision_summary["selected_feedback_count"],
                1,
            )
            self.assertEqual(
                decision_summary["advisory_feedback_count"],
                0,
            )
            self.assertEqual(
                decision_summary["candidate_actions"],
                ["repair_candidate"],
            )
            self.assertTrue(
                decision_summary["source_report_redacted"]
            )
            self.assertFalse(
                decision_summary["feedback_ids_retained"]
            )
            self.assertFalse(
                decision_summary["reason_retained"]
            )

    def test_hidden_unknown_trace_retains_redacted_review_chain(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            hidden_item = FeedbackItem(
                feedback_id="hidden.unknown.item",
                stage=FeedbackStage.TEST,
                category=FeedbackCategory.UNKNOWN,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.UNKNOWN,
                summary="hidden unknown",
                detail=SECRET,
                source="test_evaluation",
                evidence_ref=SECRET_PATH,
            )
            result = ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: safe_report(
                            "preflight",
                            "testbench_preflight",
                        )
                    ),
                    ValidationState.CSYNTH: (
                        lambda ctx: safe_report(
                            "csynth",
                            "csynth",
                        )
                    ),
                    ValidationState.HIDDEN_EVALUATION: (
                        lambda ctx: hidden_report(
                            item=hidden_item
                        )
                    ),
                }
            ).run(
                make_context(
                    make_task(hidden=True),
                    output_path=trace_path,
                ),
                validation_id="validation",
            )

            self.assertEqual(
                result.final_state,
                ValidationState.REVIEW_REQUIRED,
            )
            events = [
                json.loads(line)
                for line in trace_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            hidden_feedback = next(
                event
                for event in events
                if (
                    event["event"] == "validation.feedback"
                    and event["phase"] == "hidden_evaluation"
                )
            )
            hidden_transition = next(
                event
                for event in events
                if (
                    event["event"]
                    == "validation.transition"
                    and event["phase"]
                    == "hidden_evaluation"
                )
            )

            report_summary = hidden_feedback[
                "metadata"
            ]["feedback_report_summary"]
            decision_summary = hidden_transition[
                "metadata"
            ]["route_decision_summary"]

            self.assertEqual(
                report_summary["report_id"],
                "validation.step.3.hidden-report",
            )
            self.assertEqual(
                report_summary["items"],
                [
                    {
                        "stage": "test",
                        "category": "unknown",
                        "severity": "error",
                        "owner": "unknown",
                        "blocking": True,
                    }
                ],
            )
            self.assertEqual(
                decision_summary["decision_id"],
                "validation.step.3.route",
            )
            self.assertEqual(
                decision_summary["source_report_id"],
                report_summary["report_id"],
            )
            self.assertEqual(
                decision_summary["action"],
                "review_unknown",
            )
            self.assertEqual(
                decision_summary["candidate_actions"],
                ["review_unknown"],
            )
            self.assertEqual(
                hidden_transition["status"],
                "review_required",
            )
            self.assertFalse(
                hidden_transition["metadata"][
                    "agent_feedback_allowed"
                ]
            )

            combined = trace_path.read_text(
                encoding="utf-8"
            ) + json.dumps(
                result.to_dict(),
                sort_keys=True,
            )
            for forbidden in (
                SECRET,
                "hidden.unknown.item",
                SECRET_PATH,
                "hidden unknown",
            ):
                self.assertNotIn(forbidden, combined)

    def test_missing_handler_fails_before_execution(self):
        calls = []
        context = make_context(
            make_task(public=True)
        )
        with self.assertRaises(ValueError):
            ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: (
                        lambda ctx: (
                            calls.append("preflight")
                            or safe_report(
                                "preflight",
                                "testbench_preflight",
                            )
                        )
                    ),
                    ValidationState.CSYNTH: (
                        lambda ctx: safe_report(
                            "csynth",
                            "csynth",
                        )
                    ),
                }
            ).run(
                context,
                validation_id="validation",
            )
        self.assertEqual(calls, [])
        self.assertEqual(context.trace.events, ())

    def test_handler_error_is_traced(self):
        context = make_context(make_task())

        def fail(ctx):
            raise RuntimeError("synthetic failure")

        with self.assertRaises(RuntimeError):
            ValidationOrchestrator(
                {
                    ValidationState.PREFLIGHT: fail,
                    ValidationState.CSYNTH: (
                        lambda ctx: safe_report(
                            "csynth",
                            "csynth",
                        )
                    ),
                }
            ).run(
                context,
                validation_id="validation",
            )

        self.assertEqual(
            context.trace.events[-1].event,
            "validation.stage.error",
        )

    def test_round_trip(self):
        result = ValidationOrchestrator(
            {
                ValidationState.PREFLIGHT: (
                    lambda ctx: safe_report(
                        "preflight",
                        "testbench_preflight",
                    )
                ),
                ValidationState.CSYNTH: (
                    lambda ctx: safe_report(
                        "csynth",
                        "csynth",
                    )
                ),
            }
        ).run(
            make_context(make_task()),
            validation_id="validation",
        )
        restored = (
            ValidationOrchestrationResult.from_dict(
                result.to_dict()
            )
        )
        self.assertEqual(restored, result)


if __name__ == "__main__":
    unittest.main()
