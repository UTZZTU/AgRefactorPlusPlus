import unittest

from agrefactor.config import EvaluationSplit, TaskSpec, TestSuiteSpec
from agrefactor.evaluation import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
    ValidationState,
    ValidationStateMachine,
)


def task(hidden=False):
    suites = [TestSuiteSpec(
        suite_id="public", split=EvaluationSplit.PUBLIC, testbench_path="public.cpp"
    )]
    if hidden:
        suites.append(TestSuiteSpec(
            suite_id="hidden", split=EvaluationSplit.HIDDEN, testbench_path="hidden.cpp"
        ))
    return TaskSpec(
        task_id="r5d", kernel_path="kernel.cpp", kernel_name="top",
        test_suites=tuple(suites),
    )


def decision(action, **metadata):
    ids = ("feedback.1",)
    return FeedbackRouteDecision(
        decision_id="decision",
        action=action,
        reason="test",
        source_report_id="report",
        blocking_feedback_ids=ids,
        selected_feedback_ids=ids,
        metadata={"evidence_view": "agent_safe", **metadata},
    )


class ValidationRecoveryTests(unittest.TestCase):
    def test_public_cosim_candidate_enters_repair(self):
        result = ValidationStateMachine(task()).transition(
            ValidationState.PUBLIC_COSIM,
            decision(FeedbackRouteAction.REPAIR_CANDIDATE),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REPAIR_PENDING)

    def test_public_cosim_testbench_enters_repair(self):
        result = ValidationStateMachine(task()).transition(
            ValidationState.PUBLIC_COSIM,
            decision(FeedbackRouteAction.REPAIR_TESTBENCH),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REPAIR_PENDING)

    def test_public_cosim_original_requires_review(self):
        result = ValidationStateMachine(task()).transition(
            ValidationState.PUBLIC_COSIM,
            decision(FeedbackRouteAction.REPAIR_ORIGINAL),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REVIEW_REQUIRED)

    def test_hidden_candidate_stays_terminal(self):
        result = ValidationStateMachine(task(hidden=True)).transition(
            ValidationState.HIDDEN_EVALUATION,
            decision(FeedbackRouteAction.REPAIR_CANDIDATE),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REJECTED)
        self.assertFalse(result.repair_allowed)

    def test_hidden_testbench_stays_terminal(self):
        result = ValidationStateMachine(task(hidden=True)).transition(
            ValidationState.HIDDEN_EVALUATION,
            decision(FeedbackRouteAction.REPAIR_TESTBENCH),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REJECTED)

    def test_operator_full_repair_raises_before_policy_fallback(self):
        with self.assertRaises(ValueError):
            ValidationStateMachine(task()).transition(
                ValidationState.CSYNTH,
                decision(
                    FeedbackRouteAction.REPAIR_CANDIDATE,
                    evidence_view="operator_full",
                ),
                transition_id="t",
            )

    def test_llm_advisory_off_requires_review(self):
        result = ValidationStateMachine(task()).transition(
            ValidationState.PUBLIC_COSIM,
            decision(
                FeedbackRouteAction.REPAIR_CANDIDATE,
                owner_authority="llm_advisory",
                advisory_mode="off",
            ),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REVIEW_REQUIRED)

    def test_llm_advisory_candidate_only_can_request_repair(self):
        result = ValidationStateMachine(task()).transition(
            ValidationState.PUBLIC_COSIM,
            decision(
                FeedbackRouteAction.REPAIR_CANDIDATE,
                owner_authority="llm_advisory",
                advisory_mode="candidate-only",
            ),
            transition_id="t",
        )
        self.assertEqual(result.next_state, ValidationState.REPAIR_PENDING)

    def test_policy_identity_is_persisted(self):
        result = ValidationStateMachine(task()).transition(
            ValidationState.PUBLIC_COSIM,
            decision(FeedbackRouteAction.REPAIR_CANDIDATE),
            transition_id="t",
        )
        self.assertEqual(result.metadata["recovery_policy"], "conservative-v1")


if __name__ == "__main__":
    unittest.main()
