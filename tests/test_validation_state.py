import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
    ValidationState,
    ValidationStateMachine,
    ValidationTransition,
    ValidationTransitionKind,
)


def make_task(*, public=False, hidden=False):
    suites = []
    if public:
        suites.append(
            TestSuiteSpec(
                suite_id="public-main",
                split=EvaluationSplit.PUBLIC,
                testbench_path="/private/public.cpp",
            )
        )
    if hidden:
        suites.append(
            TestSuiteSpec(
                suite_id="hidden-final",
                split=EvaluationSplit.HIDDEN,
                testbench_path="/private/hidden.cpp",
            )
        )
    return TaskSpec(
        task_id="task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        test_suites=tuple(suites),
    )


def make_decision(
    action,
    *,
    view="agent_safe",
    selected=("safe.item.1",),
    source_report_id="report",
):
    ids = (
        ()
        if action
        is FeedbackRouteAction.CONTINUE_VALIDATION
        else selected
    )
    return FeedbackRouteDecision(
        decision_id=f"decision-{action.value}",
        action=action,
        reason=action.value,
        source_report_id=source_report_id,
        blocking_feedback_ids=ids,
        selected_feedback_ids=ids,
        metadata={"evidence_view": view},
    )


class ValidationStateMachineTests(unittest.TestCase):
    def go(self, machine, state, action, **kwargs):
        return machine.transition(
            state,
            make_decision(action, **kwargs),
            transition_id="transition",
        )

    def test_initial_state(self):
        self.assertEqual(
            ValidationStateMachine(
                make_task()
            ).initial_state,
            ValidationState.PREFLIGHT,
        )

    def test_preflight_to_csynth(self):
        result = self.go(
            ValidationStateMachine(make_task()),
            ValidationState.PREFLIGHT,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.CSYNTH,
        )

    def test_no_suite_csynth_accepts(self):
        result = self.go(
            ValidationStateMachine(make_task()),
            ValidationState.CSYNTH,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.ACCEPTED,
        )
        self.assertEqual(
            result.kind,
            ValidationTransitionKind.ACCEPT,
        )

    def test_public_then_hidden_order(self):
        machine = ValidationStateMachine(
            make_task(public=True, hidden=True)
        )
        first = self.go(
            machine,
            ValidationState.CSYNTH,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        second = self.go(
            machine,
            first.next_state,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            first.next_state,
            ValidationState.PUBLIC_EVALUATION,
        )
        self.assertEqual(
            second.next_state,
            ValidationState.HIDDEN_EVALUATION,
        )

    def test_hidden_only_after_csynth(self):
        machine = ValidationStateMachine(
            make_task(hidden=True)
        )
        result = self.go(
            machine,
            ValidationState.CSYNTH,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.HIDDEN_EVALUATION,
        )

    def test_public_failure_can_repair(self):
        result = self.go(
            ValidationStateMachine(
                make_task(public=True)
            ),
            ValidationState.PUBLIC_EVALUATION,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.REPAIR_PENDING,
        )
        self.assertTrue(result.repair_allowed)
        self.assertTrue(
            result.agent_feedback_allowed
        )
        self.assertEqual(
            result.resume_state,
            ValidationState.PUBLIC_EVALUATION,
        )

    def test_operator_full_cannot_enter_repair(self):
        with self.assertRaises(ValueError):
            self.go(
                ValidationStateMachine(make_task()),
                ValidationState.CSYNTH,
                FeedbackRouteAction.REPAIR_CANDIDATE,
                view="operator_full",
            )

    def test_budget_blocks(self):
        result = self.go(
            ValidationStateMachine(make_task()),
            ValidationState.CSYNTH,
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.BLOCKED,
        )

    def test_toolchain_blocks(self):
        result = self.go(
            ValidationStateMachine(make_task()),
            ValidationState.CSYNTH,
            FeedbackRouteAction.FIX_TOOLCHAIN,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.BLOCKED,
        )

    def test_unknown_requires_review(self):
        result = self.go(
            ValidationStateMachine(make_task()),
            ValidationState.CSYNTH,
            FeedbackRouteAction.REVIEW_UNKNOWN,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.REVIEW_REQUIRED,
        )

    def test_hidden_success_accepts(self):
        result = self.go(
            ValidationStateMachine(
                make_task(hidden=True)
            ),
            ValidationState.HIDDEN_EVALUATION,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.ACCEPTED,
        )

    def test_hidden_candidate_failure_is_terminal(self):
        result = self.go(
            ValidationStateMachine(
                make_task(hidden=True)
            ),
            ValidationState.HIDDEN_EVALUATION,
            FeedbackRouteAction.REPAIR_CANDIDATE,
            view="operator_full",
            source_report_id=(
                "HIDDEN_STATE_REPORT_ID_SECRET"
            ),
        )
        payload = json.dumps(
            result.to_dict(),
            sort_keys=True,
        )
        self.assertEqual(
            result.next_state,
            ValidationState.REJECTED,
        )
        self.assertFalse(result.repair_allowed)
        self.assertFalse(
            result.agent_feedback_allowed
        )
        self.assertEqual(
            result.selected_feedback_ids,
            (),
        )
        self.assertNotIn("safe.item.1", payload)
        self.assertNotIn(
            "HIDDEN_STATE_REPORT_ID_SECRET",
            payload,
        )
        self.assertNotIn(
            "source_report_id",
            result.metadata,
        )

    def test_hidden_unknown_requires_review(self):
        result = self.go(
            ValidationStateMachine(
                make_task(hidden=True)
            ),
            ValidationState.HIDDEN_EVALUATION,
            FeedbackRouteAction.REVIEW_UNKNOWN,
            view="operator_full",
        )
        self.assertEqual(
            result.next_state,
            ValidationState.REVIEW_REQUIRED,
        )
        self.assertFalse(
            result.agent_feedback_allowed
        )

    def test_hidden_budget_blocks(self):
        result = self.go(
            ValidationStateMachine(
                make_task(hidden=True)
            ),
            ValidationState.HIDDEN_EVALUATION,
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
            view="operator_full",
        )
        self.assertEqual(
            result.next_state,
            ValidationState.BLOCKED,
        )

    def test_suite_paths_are_not_persisted(self):
        machine = ValidationStateMachine(
            make_task(public=True, hidden=True)
        )
        result = self.go(
            machine,
            ValidationState.CSYNTH,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        payload = json.dumps(
            result.to_dict(),
            sort_keys=True,
        )
        self.assertNotIn(
            "/private/public.cpp",
            payload,
        )
        self.assertNotIn(
            "/private/hidden.cpp",
            payload,
        )
        self.assertIn("public-main", payload)
        self.assertIn("hidden-final", payload)

    def test_round_trip(self):
        original = self.go(
            ValidationStateMachine(
                make_task(public=True)
            ),
            ValidationState.PUBLIC_EVALUATION,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )
        restored = ValidationTransition.from_dict(
            original.to_dict()
        )
        self.assertEqual(restored, original)

    def test_inactive_state_rejected(self):
        with self.assertRaises(ValueError):
            self.go(
                ValidationStateMachine(make_task()),
                ValidationState.ACCEPTED,
                FeedbackRouteAction.CONTINUE_VALIDATION,
            )

    def test_rejects_non_task(self):
        with self.assertRaises(TypeError):
            ValidationStateMachine({})

    def test_state_properties(self):
        self.assertTrue(
            ValidationState.CSYNTH.active
        )
        self.assertTrue(
            ValidationState.ACCEPTED.terminal
        )
        self.assertFalse(
            ValidationState.REPAIR_PENDING.terminal
        )


if __name__ == "__main__":
    unittest.main()
