"""Exercise public CSYNTH trials through prompts and the shared repair ledger."""

from dataclasses import replace
import json
import unittest

from agrefactor.evaluation import ValidationState
from agrefactor.evidence import FeedbackCategory, FeedbackOwner, FeedbackStage
from agrefactor.repair import CandidateRepairStopReason
from agrefactor.runtime import (
    CandidateRepairOrchestrationStatus,
    CandidateRepairValidationOrchestrator,
)
from tests.test_candidate_repair_integration import (
    P1,
    P2,
    SECRET,
    ScenarioFactory,
    feedback_item,
    make_adapter,
    make_context,
    make_request,
    pass_scenario,
    report_for,
)


P3 = 'extern "C" int top(int x) { return x + 3; }'
TRIAL_AUTHORITY = "public_csynth_bounded_trial"


def public_unknown_report(request, state):
    trial = state is ValidationState.CSYNTH
    marker = f"public-evidence-{request.attempt}"
    item = feedback_item(
        marker,
        state=state,
        owner=FeedbackOwner.UNKNOWN,
        category=FeedbackCategory.UNKNOWN,
    )
    item = replace(
        item,
        stage=FeedbackStage.CSYNTH if trial else FeedbackStage.COSIM,
        detail=marker,
        metadata={
            "owner_authority": "unknown" if trial else "public_reference_qualified",
            "execution_evidence_complete": True,
            "evidence_complete": not trial,
            "repair_eligible": True,
            "physical_tool_launched": True,
            "tool_launched": True,
            **({"recovery_authority": TRIAL_AUTHORITY} if trial else {}),
        },
    )
    report = report_for(state, item=item, report_id=f"{marker}.report")
    return replace(
        report,
        metadata={
            **report.metadata,
            "evaluation_split": "public",
            "feedback_visible_to_agent": True,
            "physical_execution": True,
        },
    )


class CsynthBoundedTrialIntegrationTests(unittest.TestCase):
    def run_scenario(self, scenario):
        adapter, provider = make_adapter([P1, P2, P3])
        context = make_context()
        factory = ScenarioFactory(scenario)
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=factory,
        ).run(context, make_request(max_attempts=3), validation_id="validation")
        return result, provider, factory, context

    def repair_events(self, result):
        return [
            event
            for event in result.metadata["recovery_ledger"]["events"]
            if event["action"] == "repair" and event["accepted"]
        ]

    def test_unknown_owner_can_use_three_shared_attempts_and_history(self):
        def scenario(request, state):
            if state is ValidationState.CSYNTH:
                return public_unknown_report(request, state)
            return pass_scenario(request, state)

        result, provider, factory, context = self.run_scenario(scenario)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(result.status, CandidateRepairOrchestrationStatus.REPAIR_EXHAUSTED)
        self.assertEqual(result.repair_result.stop_reason, CandidateRepairStopReason.ATTEMPTS_EXHAUSTED)
        self.assertEqual(len(result.candidate_validations), 3)
        events = self.repair_events(result)
        self.assertEqual(len(events), 3)
        for event in events:
            self.assertEqual(event["owner_authority"], TRIAL_AUTHORITY)
            self.assertTrue(event["execution_evidence_complete"])
            self.assertFalse(event["evidence_complete"])
        counts = result.metadata["recovery_ledger"]["counts"]
        prefix = f"lineage:{context.task.task_id}:candidate"
        self.assertEqual(counts[f"{prefix}:repairs_total"], 3)
        self.assertEqual(counts[f"{prefix}:total"], 3)
        for attempt in range(4):
            states = [state for number, state, _, _ in factory.context_ids if number == attempt]
            self.assertEqual(states, [
                ValidationState.PREFLIGHT,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.CSYNTH,
            ])
        for attempt, (_, request) in enumerate(provider.calls):
            prompt = "\n".join(message.content for message in request.messages)
            for previous in range(attempt + 1):
                self.assertIn(f"public-evidence-{previous}", prompt)

    def test_cosim_and_csynth_share_quota_budget_and_full_revalidation(self):
        def scenario(request, state):
            if request.attempt == 0 and state is ValidationState.PUBLIC_COSIM:
                return public_unknown_report(request, state)
            if request.attempt in {1, 2} and state is ValidationState.CSYNTH:
                return public_unknown_report(request, state)
            return pass_scenario(request, state)

        result, provider, factory, context = self.run_scenario(scenario)
        self.assertTrue(result.accepted)
        self.assertEqual(len(provider.calls), 3)
        events = self.repair_events(result)
        self.assertEqual([event["stage"] for event in events], ["public_cosim", "csynth", "csynth"])
        self.assertEqual([event["owner_authority"] for event in events], [
            "public_reference_qualified", TRIAL_AUTHORITY, TRIAL_AUTHORITY,
        ])
        counts = result.metadata["recovery_ledger"]["counts"]
        prefix = f"lineage:{context.task.task_id}:candidate"
        self.assertEqual(counts[f"{prefix}:repairs_total"], 3)
        self.assertEqual(counts[f"{prefix}:public_cosim"], 1)
        self.assertEqual(counts[f"{prefix}:total"], 2)
        self.assertEqual({budget_id for _, _, budget_id, _ in factory.context_ids}, {id(context.budget)})
        self.assertEqual({trace_id for _, _, _, trace_id in factory.context_ids}, {id(context.trace)})
        final_states = [state for attempt, state, _, _ in factory.context_ids if attempt == 3]
        self.assertEqual(final_states, [
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
            ValidationState.PUBLIC_COSIM,
            ValidationState.HIDDEN_EVALUATION,
        ])

    def test_hidden_failure_after_trial_does_not_reprompt_candidate(self):
        def scenario(request, state):
            if request.attempt == 0 and state is ValidationState.CSYNTH:
                return public_unknown_report(request, state)
            if state is ValidationState.HIDDEN_EVALUATION:
                return report_for(state, item=feedback_item("hidden.failure", state=state), report_id=SECRET)
            return pass_scenario(request, state)

        result, provider, _, _ = self.run_scenario(scenario)
        self.assertEqual(result.status, CandidateRepairOrchestrationStatus.VALIDATION_TERMINAL)
        self.assertEqual(result.last_validation_state, ValidationState.REJECTED)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.repair_events(result)), 1)
        self.assertNotIn(SECRET, json.dumps(result.to_dict()))
        for _, request in provider.calls:
            self.assertNotIn(SECRET, "\n".join(message.content for message in request.messages))


if __name__ == "__main__":
    unittest.main()
