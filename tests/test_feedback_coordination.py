import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import (
    FeedbackRouteAction,
    ValidationFeedbackCoordinator,
    ValidationFeedbackResult,
    ValidationState,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


SECRET = "HIDDEN_SECRET_DIAGNOSTIC"


def task(*, hidden=False):
    suites = ()
    if hidden:
        suites = (
            TestSuiteSpec(
                suite_id="hidden-final",
                split=EvaluationSplit.HIDDEN,
            ),
        )
    return TaskSpec(
        task_id="coordination-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        test_suites=suites,
    )


def report(
    *,
    source="testbench_preflight",
    view="agent_safe",
    owner=FeedbackOwner.NONE,
    category=FeedbackCategory.UNKNOWN,
    severity=FeedbackSeverity.INFO,
    feedback_id="safe.item.1",
    detail="safe detail",
):
    items = ()
    if owner is not FeedbackOwner.NONE:
        items = (
            FeedbackItem(
                feedback_id=feedback_id,
                stage=FeedbackStage.COMPILE,
                category=category,
                severity=severity,
                owner=owner,
                summary="normalized feedback",
                detail=detail,
                source=source,
                evidence_ref=(
                    None
                    if view == "agent_safe"
                    else "/private/operator/evidence.log"
                ),
            ),
        )
    return FeedbackReport(
        report_id=f"{view}-report",
        source=source,
        items=items,
        source_evidence=(
            {"secret": SECRET}
            if view == "operator_full"
            else {"redacted": True}
        ),
        metadata={"evidence_view": view},
    )


class ValidationFeedbackCoordinatorTests(
    unittest.TestCase
):
    def coordinate(
        self,
        source,
        state=ValidationState.PREFLIGHT,
        *,
        hidden=False,
    ):
        return ValidationFeedbackCoordinator(
            task(hidden=hidden)
        ).coordinate(
            source,
            state,
            coordination_id="coordination",
        )

    def test_preflight_testbench_repair(self):
        result = self.coordinate(
            report(
                owner=FeedbackOwner.TESTBENCH,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
            )
        )

        self.assertEqual(
            result.route_action,
            FeedbackRouteAction.REPAIR_TESTBENCH,
        )
        self.assertEqual(
            result.transition.next_state,
            ValidationState.REPAIR_PENDING,
        )
        self.assertTrue(
            result.agent_feedback_allowed
        )
        self.assertEqual(
            len(result.selected_feedback_items),
            1,
        )

    def test_csynth_candidate_repair(self):
        result = self.coordinate(
            report(
                source="csynth",
                owner=FeedbackOwner.CANDIDATE,
                category=(
                    FeedbackCategory.UNDECLARED_SYMBOL
                ),
                severity=FeedbackSeverity.ERROR,
            ),
            ValidationState.CSYNTH,
        )

        self.assertEqual(
            result.route_action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )
        self.assertEqual(
            result.selected_feedback_items[0].owner,
            FeedbackOwner.CANDIDATE,
        )

    def test_public_pass_advances_to_hidden(self):
        result = self.coordinate(
            report(source="test_evaluation"),
            ValidationState.PUBLIC_EVALUATION,
            hidden=True,
        )

        self.assertEqual(
            result.transition.next_state,
            ValidationState.HIDDEN_EVALUATION,
        )
        self.assertEqual(
            result.selected_feedback_items,
            (),
        )

    def test_hidden_candidate_failure_is_suppressed(self):
        source = report(
            source="test_evaluation",
            view="operator_full",
            owner=FeedbackOwner.CANDIDATE,
            category=FeedbackCategory.FUNCTIONAL_MISMATCH,
            severity=FeedbackSeverity.ERROR,
            feedback_id="hidden.secret.item",
            detail=SECRET,
        )
        result = self.coordinate(
            source,
            ValidationState.HIDDEN_EVALUATION,
            hidden=True,
        )
        payload = json.dumps(
            result.to_dict(),
            sort_keys=True,
        )

        self.assertEqual(
            result.transition.next_state,
            ValidationState.REJECTED,
        )
        self.assertFalse(
            result.agent_feedback_allowed
        )
        self.assertEqual(
            result.selected_feedback_items,
            (),
        )
        self.assertNotIn(SECRET, payload)
        self.assertNotIn(
            "hidden.secret.item",
            payload,
        )
        self.assertNotIn(
            "/private/operator",
            payload,
        )

    def test_hidden_unknown_requires_review(self):
        result = self.coordinate(
            report(
                source="test_evaluation",
                view="operator_full",
                owner=FeedbackOwner.UNKNOWN,
                category=FeedbackCategory.UNKNOWN,
                severity=FeedbackSeverity.ERROR,
                detail=SECRET,
            ),
            ValidationState.HIDDEN_EVALUATION,
            hidden=True,
        )

        self.assertEqual(
            result.transition.next_state,
            ValidationState.REVIEW_REQUIRED,
        )
        self.assertEqual(
            result.selected_feedback_items,
            (),
        )

    def test_hidden_requires_operator_full(self):
        with self.assertRaises(ValueError):
            self.coordinate(
                report(
                    source="test_evaluation",
                    view="agent_safe",
                ),
                ValidationState.HIDDEN_EVALUATION,
                hidden=True,
            )

    def test_non_hidden_requires_agent_safe(self):
        with self.assertRaises(ValueError):
            self.coordinate(
                report(
                    view="operator_full",
                    owner=FeedbackOwner.TESTBENCH,
                    category=FeedbackCategory.SYNTAX_ERROR,
                    severity=FeedbackSeverity.ERROR,
                )
            )

    def test_budget_exhaustion_blocks_without_feedback(self):
        result = self.coordinate(
            report(
                owner=FeedbackOwner.EVALUATOR,
                category=FeedbackCategory.BUDGET_EXHAUSTED,
                severity=FeedbackSeverity.ERROR,
            ),
            ValidationState.CSYNTH,
        )

        self.assertEqual(
            result.route_action,
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            result.transition.next_state,
            ValidationState.BLOCKED,
        )
        self.assertEqual(
            result.selected_feedback_items,
            (),
        )

    def test_warning_continues_without_selected_items(self):
        result = self.coordinate(
            report(
                source="csynth",
                owner=FeedbackOwner.CANDIDATE,
                category=(
                    FeedbackCategory.PIPELINE_DEPENDENCY
                ),
                severity=FeedbackSeverity.WARNING,
            ),
            ValidationState.CSYNTH,
        )

        self.assertEqual(
            result.route_action,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            result.transition.next_state,
            ValidationState.ACCEPTED,
        )
        self.assertEqual(
            result.selected_feedback_items,
            (),
        )

    def test_result_round_trip(self):
        original = self.coordinate(
            report(
                owner=FeedbackOwner.TESTBENCH,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
            )
        )
        restored = ValidationFeedbackResult.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_does_not_mutate_report(self):
        source = report(
            owner=FeedbackOwner.TESTBENCH,
            category=FeedbackCategory.SYNTAX_ERROR,
            severity=FeedbackSeverity.ERROR,
        )
        before = source.to_dict()

        self.coordinate(source)

        self.assertEqual(source.to_dict(), before)

    def test_rejects_non_report(self):
        with self.assertRaises(TypeError):
            ValidationFeedbackCoordinator(
                task()
            ).coordinate(
                {"items": []},
                ValidationState.PREFLIGHT,
                coordination_id="coordination",
            )

    def test_rejects_inactive_state(self):
        with self.assertRaises(ValueError):
            self.coordinate(
                report(),
                ValidationState.ACCEPTED,
            )

    def test_custom_ids_are_preserved(self):
        result = ValidationFeedbackCoordinator(
            task()
        ).coordinate(
            report(),
            ValidationState.PREFLIGHT,
            coordination_id="coord",
            decision_id="route-id",
            transition_id="transition-id",
        )

        self.assertEqual(
            result.coordination_id,
            "coord",
        )
        self.assertEqual(
            result.transition.transition_id,
            "transition-id",
        )
        self.assertEqual(
            result.transition.source_decision_id,
            "route-id",
        )

    def test_source_is_generic(self):
        sources = (
            "testbench_preflight",
            "csynth",
            "test_evaluation",
            "future_evaluator",
        )
        results = [
            self.coordinate(
                report(source=source)
            )
            for source in sources
        ]

        self.assertTrue(
            all(
                item.route_action
                is FeedbackRouteAction.CONTINUE_VALIDATION
                for item in results
            )
        )


if __name__ == "__main__":
    unittest.main()
