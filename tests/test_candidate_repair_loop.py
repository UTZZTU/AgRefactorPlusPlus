import inspect
import json
import unittest
from dataclasses import replace

from agrefactor.config import EvaluationSplit, TargetProfile, TaskSpec, TestSuiteSpec
from agrefactor.evaluation import FeedbackRouteAction, FeedbackRouteDecision, ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    ModelFamilyProfile,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.prompts import CandidateRepairPromptInputs, build_candidate_csynth_repair_prompt
from agrefactor.repair import (
    BoundedCandidateRepairLoop,
    CandidateRepairAttemptStatus,
    CandidateRepairLoopRequest,
    CandidateRepairStopReason,
    CandidateValidationRequest,
    CandidateValidationResult,
)
from agrefactor.runtime import BudgetLimits, BudgetManager
from agrefactor.recovery import RecoveryLedger
import agrefactor.repair.candidate_loop as candidate_loop_module


ORIGINAL = 'extern "C" int original_top(int x) { return x + 1; }\n'
INITIAL = 'extern "C" int candidate_top(int x) { return x; }\n'
PROPOSAL_1 = 'extern "C" int candidate_top(int x) { return x + 1; }\n'
PROPOSAL_2 = 'extern "C" int candidate_top(int x) { return x + 2; }\n'
PROPOSAL_3 = 'extern "C" int candidate_top(int x) { return x + 3; }\n'
PUBLIC_TB = (
    'extern "C" int original_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    'int main() { return original_top(2) == candidate_top(2) ? 0 : 1; }\n'
)

TASK = TaskSpec(
    task_id="bounded-candidate-repair",
    kernel_path="/private/candidate.cpp",
    kernel_name="candidate_top",
    target=TargetProfile(
        name="test-target",
        toolchain="vitis_hls",
        toolchain_version="2023.2",
        device="xcu200-fsgd2104-2-e",
        clock_period_ns=4.0,
    ),
    test_suites=(
        TestSuiteSpec(
            suite_id="public-suite",
            split=EvaluationSplit.PUBLIC,
            testbench_path="/private/public.cpp",
        ),
        TestSuiteSpec(
            suite_id="hidden-suite",
            split=EvaluationSplit.HIDDEN,
            testbench_path="/private/hidden.cpp",
        ),
    ),
)


def stage_for_state(state):
    return {
        ValidationState.PREFLIGHT: FeedbackStage.COMPILE,
        ValidationState.CSYNTH: FeedbackStage.CSYNTH,
        ValidationState.PUBLIC_EVALUATION: FeedbackStage.CSIM,
        ValidationState.PUBLIC_COSIM: FeedbackStage.COSIM,
        ValidationState.HIDDEN_EVALUATION: FeedbackStage.CSIM,
    }[state]


def make_feedback(
    state=ValidationState.CSYNTH,
    *,
    owner=FeedbackOwner.CANDIDATE,
    view="agent_safe",
    blocking=True,
    include_split=True,
    visible=True,
    report_id="candidate-report",
    summary="candidate failure",
    detail="operator detail must not drive routing",
):
    metadata = {"evidence_view": view}
    if owner is FeedbackOwner.CANDIDATE:
        metadata.update({
            "owner_authority": "deterministic_proven",
            "evidence_complete": True,
            "tool_launched": True,
            "physical_tool_launched": True,
        })
    if state in {ValidationState.PUBLIC_EVALUATION, ValidationState.PUBLIC_COSIM, ValidationState.HIDDEN_EVALUATION}:
        if include_split:
            metadata["evaluation_split"] = (
                EvaluationSplit.HIDDEN.value
                if state is ValidationState.HIDDEN_EVALUATION
                else EvaluationSplit.PUBLIC.value
            )
        if visible is not None:
            metadata["feedback_visible_to_agent"] = visible
    return FeedbackReport(
        report_id=report_id,
        source="deterministic-validator",
        items=(
            FeedbackItem(
                feedback_id=f"{report_id}.item",
                stage=stage_for_state(state),
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=(
                    FeedbackSeverity.ERROR if blocking else FeedbackSeverity.WARNING
                ),
                owner=owner,
                summary=summary,
                detail=detail,
                source="test",
                metadata=metadata,
            ),
        ),
        metadata=metadata,
    )


def make_route(report, action=FeedbackRouteAction.REPAIR_CANDIDATE, *, source_id=None):
    selected = (
        tuple(item.feedback_id for item in report.items if item.blocking)
        if action is FeedbackRouteAction.REPAIR_CANDIDATE
        else ()
    )
    return FeedbackRouteDecision(
        decision_id=f"{report.report_id}.route",
        action=action,
        reason="deterministic route",
        source_report_id=source_id or report.report_id,
        blocking_feedback_ids=tuple(item.feedback_id for item in report.items if item.blocking),
        selected_feedback_ids=selected,
        metadata={
            "evidence_view": report.metadata.get("evidence_view"),
            "owner_authority": report.items[0].metadata.get("owner_authority", "unknown"),
            "evidence_complete": report.items[0].metadata.get("evidence_complete", False),
            "tool_launched": report.items[0].metadata.get("tool_launched", False),
            "physical_tool_launched": report.items[0].metadata.get("physical_tool_launched", False),
        },
    )


def make_request(
    *,
    state=ValidationState.CSYNTH,
    feedback=None,
    route=None,
    max_attempts=2,
    public_testbench_code=PUBLIC_TB,
):
    feedback = feedback or make_feedback(state)
    route = route or make_route(feedback)
    return CandidateRepairLoopRequest(
        task=TASK,
        initial_candidate=INITIAL,
        original_code=ORIGINAL,
        public_testbench_code=public_testbench_code,
        feedback=feedback,
        route_decision=route,
        failure_state=state,
        max_attempts=max_attempts,
        family_instruction="Emit only the final replacement.",
    )


def fenced(code):
    return f"```cpp\n{code}\n```"


class SequenceProvider(ModelProvider):
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, ModelResponse):
            return value
        return ModelResponse(
            text=value,
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=0.01,
            ),
            finish_reason="stop",
        )


def make_adapter(provider):
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_family_profile(
        ModelFamilyProfile(name="reasoning")
    )
    registry.register_model(
        ModelSpec(
            name="candidate-model",
            provider="fake",
            model="fake-candidate-1",
            family="reasoning",
            default_parameters={"temperature": 0.2},
        )
    )
    return CandidateModelAdapter(
        registry=registry,
        model_name="candidate-model",
        parameters={"temperature": 0},
    )


class RecordingValidator:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def validate(self, request):
        self.calls.append(request)
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def passed_result(
    stages=(
        ValidationState.PREFLIGHT,
        ValidationState.PUBLIC_EVALUATION,
        ValidationState.CSYNTH,
    )
):
    return CandidateValidationResult(
        passed=True,
        completed_stages=stages,
        summary="validation passed",
    )


def failed_result(
    state=ValidationState.CSYNTH,
    *,
    owner=FeedbackOwner.CANDIDATE,
    view="agent_safe",
    action=FeedbackRouteAction.REPAIR_CANDIDATE,
    summary="validation still fails",
    detail="operator detail must not drive routing",
):
    report = make_feedback(
        state,
        owner=owner,
        view=view,
        report_id=f"next-{state.value}",
        summary=summary,
        detail=detail,
        visible=(False if state is ValidationState.HIDDEN_EVALUATION else True),
    )
    route = make_route(report, action=action)
    stages = {
        ValidationState.PREFLIGHT: (ValidationState.PREFLIGHT,),
        ValidationState.PUBLIC_EVALUATION: (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
        ),
        ValidationState.CSYNTH: (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
        ),
        ValidationState.PUBLIC_COSIM: (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
            ValidationState.PUBLIC_COSIM,
        ),
        ValidationState.HIDDEN_EVALUATION: (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
            ValidationState.PUBLIC_COSIM,
            ValidationState.HIDDEN_EVALUATION,
        ),
    }[state]
    return CandidateValidationResult(
        passed=False,
        completed_stages=stages,
        summary=summary,
        feedback=report,
        route_decision=route,
        failure_state=state,
        metadata={"safe": True, "secret": "hidden-value"},
    )


class CandidateRepairEntryContractTests(unittest.TestCase):
    def test_accepts_compile_candidate_route(self):
        self.assertIs(make_request(state=ValidationState.PREFLIGHT).failure_state, ValidationState.PREFLIGHT)

    def test_accepts_csynth_candidate_route(self):
        self.assertIs(make_request().failure_state, ValidationState.CSYNTH)

    def test_accepts_public_csynth_bounded_trial_route(self):
        report = make_feedback(ValidationState.CSYNTH, owner=FeedbackOwner.UNKNOWN)
        item = replace(
            report.items[0],
            metadata={
                "recovery_authority": "public_csynth_bounded_trial",
                "execution_evidence_complete": True,
                "evidence_complete": False,
                "repair_eligible": True,
                "tool_launched": True,
                "physical_tool_launched": True,
            },
        )
        report = replace(
            report,
            items=(item,),
            metadata={
                **report.metadata,
                "evaluation_split": EvaluationSplit.PUBLIC.value,
                "feedback_visible_to_agent": True,
            },
        )
        route = FeedbackRouteDecision(
            decision_id="bounded.route",
            action=FeedbackRouteAction.REPAIR_CANDIDATE,
            reason="bounded trial",
            source_report_id=report.report_id,
            blocking_feedback_ids=(item.feedback_id,),
            selected_feedback_ids=(item.feedback_id,),
            metadata={
                "evidence_view": "agent_safe",
                "recovery_authority": "public_csynth_bounded_trial",
                "execution_evidence_complete": True,
            },
        )
        request = make_request(feedback=report, route=route)
        self.assertIs(request.failure_state, ValidationState.CSYNTH)

    def test_accepts_public_candidate_route(self):
        self.assertIs(make_request(state=ValidationState.PUBLIC_EVALUATION).failure_state, ValidationState.PUBLIC_EVALUATION)

    def test_rejects_public_cosim_without_split_and_hidden_entry(self):
        cases = (
            (ValidationState.PUBLIC_COSIM, "agent_safe", True),
            (ValidationState.HIDDEN_EVALUATION, "operator_full", False),
        )
        for state, view, visible in cases:
            with self.subTest(state=state.value):
                report = make_feedback(state, view=view, visible=visible, include_split=state is not ValidationState.PUBLIC_COSIM)
                with self.assertRaises(ValueError):
                    make_request(
                        state=state,
                        feedback=report,
                        route=make_route(report),
                    )

    def test_rejects_operator_full_entry(self):
        report = make_feedback(view="operator_full")
        with self.assertRaises(ValueError):
            make_request(feedback=report, route=make_route(report))

    def test_rejects_nonblocking_entry(self):
        report = make_feedback(blocking=False)
        with self.assertRaises(ValueError):
            make_request(feedback=report, route=make_route(report))

    def test_rejects_wrong_route(self):
        report = make_feedback()
        with self.assertRaises(ValueError):
            make_request(feedback=report, route=make_route(report, FeedbackRouteAction.REVIEW_UNKNOWN))

    def test_rejects_non_candidate_owner(self):
        report = make_feedback(owner=FeedbackOwner.TOOLCHAIN)
        with self.assertRaises(ValueError):
            make_request(feedback=report, route=make_route(report))

    def test_rejects_wrong_feedback_stage(self):
        report = make_feedback(ValidationState.PREFLIGHT)
        with self.assertRaises(ValueError):
            make_request(state=ValidationState.CSYNTH, feedback=report, route=make_route(report))

    def test_public_requires_explicit_public_split(self):
        report = make_feedback(ValidationState.PUBLIC_EVALUATION, include_split=False)
        with self.assertRaises(ValueError):
            make_request(state=ValidationState.PUBLIC_EVALUATION, feedback=report, route=make_route(report))

    def test_public_requires_visible_feedback(self):
        report = make_feedback(ValidationState.PUBLIC_EVALUATION, visible=False)
        with self.assertRaises(ValueError):
            make_request(state=ValidationState.PUBLIC_EVALUATION, feedback=report, route=make_route(report))

    def test_public_requires_testbench_and_matching_report(self):
        report = make_feedback(ValidationState.PUBLIC_EVALUATION)
        with self.assertRaises(ValueError):
            make_request(state=ValidationState.PUBLIC_EVALUATION, feedback=report, route=make_route(report), public_testbench_code=None)
        with self.assertRaises(ValueError):
            make_request(feedback=make_feedback(), route=make_route(make_feedback(), source_id="other"))


class CandidateValidationContractTests(unittest.TestCase):
    def test_validation_request_keeps_same_budget_and_csynth_prefix(self):
        budget = BudgetManager()
        request = CandidateValidationRequest(
            task=TASK,
            candidate_code=PROPOSAL_1,
            original_code=ORIGINAL,
            public_testbench_code=PUBLIC_TB,
            attempt=1,
            source_failure_state=ValidationState.CSYNTH,
            required_prefix=(
                ValidationState.PREFLIGHT,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.CSYNTH,
            ),
            budget=budget,
        )
        self.assertIs(request.budget, budget)

    def test_validation_request_rejects_wrong_prefix(self):
        with self.assertRaises(ValueError):
            CandidateValidationRequest(
                task=TASK,
                candidate_code=PROPOSAL_1,
                original_code=ORIGINAL,
                public_testbench_code=PUBLIC_TB,
                attempt=1,
                source_failure_state=ValidationState.PUBLIC_EVALUATION,
                required_prefix=(ValidationState.PUBLIC_EVALUATION,),
                budget=BudgetManager(),
            )

    def test_validation_result_requires_preflight_prefix(self):
        with self.assertRaises(ValueError):
            CandidateValidationResult(
                passed=True,
                completed_stages=(ValidationState.CSYNTH,),
                summary="bad plan",
            )

    def test_failed_validation_requires_feedback_and_route(self):
        with self.assertRaises(TypeError):
            CandidateValidationResult(
                passed=False,
                completed_stages=(ValidationState.PREFLIGHT,),
                summary="failed",
                failure_state=ValidationState.PREFLIGHT,
            )

    def test_failure_state_must_be_last_completed_stage(self):
        report = make_feedback(ValidationState.PREFLIGHT)
        with self.assertRaises(ValueError):
            CandidateValidationResult(
                passed=False,
                completed_stages=(
                    ValidationState.PREFLIGHT,
                    ValidationState.PUBLIC_EVALUATION,
                    ValidationState.CSYNTH,
                ),
                summary="failed",
                feedback=report,
                route_decision=make_route(report),
                failure_state=ValidationState.PREFLIGHT,
            )

    def test_operator_full_safe_dict_redacts_report_and_metadata(self):
        result = failed_result(
            ValidationState.HIDDEN_EVALUATION,
            view="operator_full",
            summary="operator secret summary",
        )
        encoded = json.dumps(result.to_safe_dict(), sort_keys=True)
        self.assertIn("operator-only-redacted", encoded)
        self.assertNotIn("hidden-value", encoded)
        self.assertNotIn("operator secret summary", encoded)
        self.assertNotIn("next-hidden_evaluation", encoded)


class BoundedCandidateRepairLoopTests(unittest.TestCase):
    def make_loop(self, provider, validator, budget=None):
        budget = budget or BudgetManager(BudgetLimits(max_llm_calls=5, max_tokens=1000, max_cost_usd=1.0))
        return BoundedCandidateRepairLoop(
            model_adapter=make_adapter(provider),
            validator=validator,
            budget=budget,
        ), budget

    def test_zero_llm_budget_blocks_before_provider(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        loop, budget = self.make_loop(provider, RecordingValidator([passed_result()]), BudgetManager(BudgetLimits(max_llm_calls=0)))
        result = loop.run(make_request(max_attempts=2))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.BUDGET_EXHAUSTED)
        self.assertEqual(provider.calls, [])
        self.assertEqual(budget.snapshot().llm_calls, 0)

    def test_provider_exception_counts_exact_once_without_fake_usage(self):
        provider = SequenceProvider([RuntimeError("provider down")] * 3)
        loop, budget = self.make_loop(provider, RecordingValidator([]))
        result = loop.run(make_request(max_attempts=1))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.PROVIDER_EXHAUSTED)
        self.assertEqual(result.repair_attempt_count, 0)
        self.assertEqual(result.provider_failure_count, 3)
        self.assertEqual(budget.snapshot().llm_calls, 3)
        self.assertEqual(budget.snapshot().tokens, 0)
        self.assertEqual(budget.snapshot().cost_usd, 0.0)

    def test_non_cpp_text_reaches_real_validator_and_keeps_usage(self):
        provider = SequenceProvider(["commentary outside a block"])
        loop, budget = self.make_loop(provider, RecordingValidator([]))
        result = loop.run(make_request(max_attempts=1))
        self.assertIs(result.attempts[0].status, CandidateRepairAttemptStatus.VALIDATOR_ERROR)
        self.assertEqual(budget.snapshot().llm_calls, 1)
        self.assertEqual(budget.snapshot().tokens, 15)
        self.assertAlmostEqual(budget.snapshot().cost_usd, 0.01)

    def test_unchanged_response_reaches_validator_and_counts_usage(self):
        provider = SequenceProvider([fenced(INITIAL)])
        loop, budget = self.make_loop(provider, RecordingValidator([]))
        result = loop.run(make_request(max_attempts=1))
        self.assertEqual(len(result.attempts), 1)
        self.assertIs(result.attempts[0].status, CandidateRepairAttemptStatus.VALIDATOR_ERROR)
        self.assertEqual(budget.snapshot().llm_calls, 1)
        self.assertEqual(budget.snapshot().tokens, 15)

    def test_success_sets_last_validated_candidate(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        validator = RecordingValidator(
            [
                passed_result(
                    (
                        ValidationState.PREFLIGHT,
                        ValidationState.PUBLIC_EVALUATION,
                        ValidationState.CSYNTH,
                    )
                )
            ]
        )
        loop, budget = self.make_loop(provider, validator)
        result = loop.run(make_request(max_attempts=2))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.last_validated_candidate, PROPOSAL_1.strip())
        self.assertEqual(result.current_candidate, PROPOSAL_1.strip())
        self.assertEqual(budget.snapshot().llm_calls, 1)

    def test_provider_errors_have_separate_finite_limit(self):
        provider = SequenceProvider([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
        loop, budget = self.make_loop(provider, RecordingValidator([]))
        result = loop.run(make_request(max_attempts=2))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.PROVIDER_EXHAUSTED)
        self.assertEqual(result.repair_attempt_count, 0)
        self.assertEqual(result.provider_failure_count, 3)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(budget.snapshot().llm_calls, 3)
        record = result.to_repair_run_record("provider-run")
        self.assertEqual(record.metadata["repair_attempt_count"], 0)

    def test_failed_proposal_updates_current_not_last_validated(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        loop, _ = self.make_loop(provider, RecordingValidator([failed_result()]))
        result = loop.run(make_request(max_attempts=1))
        self.assertEqual(result.current_candidate, PROPOSAL_1.strip())
        self.assertEqual(result.last_proposal, PROPOSAL_1.strip())
        self.assertIsNone(result.last_validated_candidate)

    def test_second_attempt_uses_failed_proposal_and_new_feedback(self):
        provider = SequenceProvider([fenced(PROPOSAL_1), fenced(PROPOSAL_2)])
        validator = RecordingValidator([failed_result(), passed_result()])
        loop, _ = self.make_loop(provider, validator)
        result = loop.run(make_request(max_attempts=2))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.attempts[1].input_candidate, PROPOSAL_1.strip())
        self.assertEqual(result.last_validated_candidate, PROPOSAL_2.strip())
        self.assertIn("validation still fails", provider.calls[1][1].messages[0].content + provider.calls[1][1].messages[1].content)

    def test_third_attempt_keeps_compile_and_csynth_failures(self):
        provider = SequenceProvider(
            [fenced(PROPOSAL_1), fenced(PROPOSAL_2), fenced(PROPOSAL_3)]
        )
        validator = RecordingValidator(
            [
                failed_result(
                    ValidationState.PREFLIGHT,
                    summary="first compile failure",
                    detail="compile diagnostic marker",
                ),
                failed_result(
                    ValidationState.CSYNTH,
                    summary="second synthesis failure",
                    detail="synthesis diagnostic marker",
                ),
                passed_result(),
            ]
        )
        loop, _ = self.make_loop(provider, validator)

        result = loop.run(
            make_request(
                state=ValidationState.PREFLIGHT,
                max_attempts=3,
            )
        )

        self.assertTrue(result.succeeded)
        prompt = "\n".join(
            message.content for message in provider.calls[2][1].messages
        )
        self.assertIn("Attempt 1", prompt)
        self.assertIn("stage: preflight", prompt)
        self.assertIn("compile diagnostic marker", prompt)
        self.assertIn("Attempt 2", prompt)
        self.assertIn("stage: csynth", prompt)
        self.assertIn("synthesis diagnostic marker", prompt)
        self.assertEqual(
            validator.calls[2].required_prefix,
            (
                ValidationState.PREFLIGHT,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.CSYNTH,
            ),
        )

    def test_raw_invalid_code_reaches_validation_and_next_attempt(self):
        provider = SequenceProvider(
            [
                "commentary outside a block",
                fenced(PROPOSAL_1),
                fenced(PROPOSAL_2),
            ]
        )
        validator = RecordingValidator(
            [
                failed_result(
                    summary="candidate validation marker",
                    detail="candidate tool marker",
                ),
                passed_result(),
            ]
        )
        loop, _ = self.make_loop(provider, validator)

        result = loop.run(make_request(max_attempts=3))

        self.assertTrue(result.succeeded)
        self.assertEqual(len(provider.calls), 2)
        prompt = "\n".join(
            message.content for message in provider.calls[1][1].messages
        )
        self.assertIn("Attempt 1", prompt)
        self.assertIn("candidate validation marker", prompt)
        self.assertIn("candidate tool marker", prompt)

    def test_provider_error_and_empty_response_keep_candidate_repair_slot(self):
        provider = SequenceProvider(
            [
                RuntimeError("provider unavailable marker"),
                "",
                fenced(PROPOSAL_1),
            ]
        )
        validator = RecordingValidator([passed_result()])
        loop, _ = self.make_loop(provider, validator)
        ledger = RecoveryLedger()

        result = loop.run(replace(make_request(max_attempts=1), recovery_ledger=ledger))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempt_count, 1)
        self.assertEqual(result.provider_failure_count, 2)
        self.assertEqual([item.attempt for item in validator.calls], [1])
        repairs = [event for event in ledger.events if event.accepted and event.to_dict()["action"] == "repair"]
        self.assertEqual(len(repairs), 1)
        self.assertEqual([item.attempt for item in result.attempts], [1, 2, 3])
        self.assertEqual(result.attempts[2].prompt_manifest["attempt"], 1)
        prompt = "\n".join(
            message.content for message in provider.calls[2][1].messages
        )
        self.assertIn("Attempt 1", prompt)
        self.assertIn("status: provider_error", prompt)
        self.assertIn("RuntimeError", prompt)
        self.assertIn("provider unavailable marker", prompt)
        self.assertIn("Attempt 2", prompt)
        self.assertIn("status: provider_error", prompt)
        self.assertIn("ValueError", prompt)
        self.assertIn("text must not be empty", prompt)

    def test_provider_error_after_validation_keeps_all_failure_evidence(self):
        provider = SequenceProvider([fenced(PROPOSAL_1), RuntimeError("network marker"), fenced(PROPOSAL_2)])
        validator = RecordingValidator([failed_result(summary="compile history marker"), passed_result()])
        loop, _ = self.make_loop(provider, validator)
        result = loop.run(make_request(max_attempts=2))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempt_count, 2)
        self.assertEqual(result.provider_failure_count, 1)
        self.assertEqual([item.attempt for item in validator.calls], [1, 2])
        prompt = "\n".join(message.content for message in provider.calls[2][1].messages)
        self.assertIn("compile history marker", prompt)
        self.assertIn("network marker", prompt)
        self.assertIn("Initial validation failure", prompt)

    def test_provider_retries_preserve_exact_llm_budget_and_ledger_usage(self):
        provider = SequenceProvider([RuntimeError("network"), fenced(PROPOSAL_1)])
        validator = RecordingValidator([passed_result()])
        loop, _ = self.make_loop(provider, validator, BudgetManager(BudgetLimits(max_llm_calls=2)))
        result = loop.run(replace(make_request(max_attempts=1), recovery_ledger=RecoveryLedger()))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempt_count, 1)
        self.assertEqual(result.budget_usage.llm_calls, 2)

    def test_post_response_restart_budget_block_retains_model_evidence(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        validator = RecordingValidator([])
        loop, _ = self.make_loop(provider, validator, BudgetManager(BudgetLimits(max_tool_calls=0)))
        result = loop.run(replace(make_request(max_attempts=1), recovery_ledger=RecoveryLedger()))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.BUDGET_EXHAUSTED)
        self.assertEqual(result.repair_attempt_count, 0)
        self.assertEqual(validator.calls, [])
        record = result.to_repair_run_record("blocked-response")
        self.assertIsNotNone(record.attempts[0].model_observation.model_response)
        self.assertTrue(record.attempts[0].model_observation.model_call_observed)

    def test_repaired_cosim_candidate_can_fail_at_preflight_and_be_repaired_again(self):
        provider = SequenceProvider([fenced(PROPOSAL_1), fenced(PROPOSAL_2)])
        validator = RecordingValidator([failed_result(ValidationState.PREFLIGHT, summary="new compile error"), passed_result()])
        loop, _ = self.make_loop(provider, validator)
        result = loop.run(make_request(state=ValidationState.PUBLIC_COSIM, max_attempts=2))
        self.assertTrue(result.succeeded)
        self.assertIs(result.attempts[0].status, CandidateRepairAttemptStatus.VALIDATION_FAILED)
        self.assertEqual(result.repair_attempt_count, 2)

    def test_failed_validation_cannot_skip_public_stage(self):
        result = failed_result(ValidationState.CSYNTH)
        result = replace(result, completed_stages=(ValidationState.PREFLIGHT, ValidationState.CSYNTH))
        loop, _ = self.make_loop(SequenceProvider([fenced(PROPOSAL_1)]), RecordingValidator([result]))
        outcome = loop.run(make_request(state=ValidationState.PUBLIC_COSIM, max_attempts=1))
        self.assertIs(outcome.stop_reason, CandidateRepairStopReason.VALIDATOR_ERROR)

    def test_failed_validation_cannot_skip_public_stage_after_preflight_repair(self):
        result = replace(failed_result(ValidationState.CSYNTH), completed_stages=(ValidationState.PREFLIGHT, ValidationState.CSYNTH))
        loop, _ = self.make_loop(SequenceProvider([fenced(PROPOSAL_1)]), RecordingValidator([result]))
        outcome = loop.run(make_request(state=ValidationState.PREFLIGHT, max_attempts=1))
        self.assertIs(outcome.stop_reason, CandidateRepairStopReason.VALIDATOR_ERROR)

    def test_history_truncates_one_diagnostic_without_dropping_attempt(self):
        marker = "first diagnostic marker "
        result = failed_result(
            summary="first summary marker",
            detail=marker + ("x" * 2500) + " first diagnostic tail",
        )

        summary = candidate_loop_module._candidate_validation_failure_summary(
            1,
            result,
        )

        self.assertIn("Attempt 1", summary)
        self.assertIn(marker.strip(), summary)
        self.assertNotIn("first diagnostic tail", summary)

    def test_terminal_toolchain_feedback_stops_without_second_model(self):
        provider = SequenceProvider([fenced(PROPOSAL_1), fenced(PROPOSAL_2)])
        validator = RecordingValidator([failed_result(owner=FeedbackOwner.TOOLCHAIN, action=FeedbackRouteAction.FIX_TOOLCHAIN)])
        loop, _ = self.make_loop(provider, validator)
        result = loop.run(make_request(max_attempts=2))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.TERMINAL_FEEDBACK)
        self.assertEqual(len(provider.calls), 1)

    def test_hidden_failure_is_terminal_and_safe(self):
        provider = SequenceProvider([fenced(PROPOSAL_1), fenced(PROPOSAL_2)])
        validator = RecordingValidator([failed_result(ValidationState.HIDDEN_EVALUATION, view="operator_full")])
        loop, _ = self.make_loop(provider, validator)
        result = loop.run(make_request(max_attempts=2))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.TERMINAL_FEEDBACK)
        encoded = json.dumps(result.to_dict(), sort_keys=True)
        self.assertNotIn("hidden-value", encoded)
        self.assertEqual(len(provider.calls), 1)

    def test_validator_receives_exact_shared_budget(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        validator = RecordingValidator([passed_result()])
        loop, budget = self.make_loop(provider, validator)
        loop.run(make_request(max_attempts=1))
        self.assertIs(validator.calls[0].budget, budget)

    def test_validator_cannot_skip_required_csynth_prefix(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        validator = RecordingValidator([passed_result((ValidationState.PREFLIGHT,))])
        loop, _ = self.make_loop(provider, validator)
        result = loop.run(make_request(max_attempts=1))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.VALIDATOR_ERROR)

    def test_observed_usage_can_exceed_post_call_limit_and_is_retained(self):
        response = ModelResponse(
            text=fenced(PROPOSAL_1),
            model="fake-candidate-1",
            usage=TokenUsage(prompt_tokens=6, completion_tokens=4, cost_usd=0.02),
        )
        provider = SequenceProvider([response])
        validator = RecordingValidator([failed_result()])
        budget = BudgetManager(BudgetLimits(max_llm_calls=3, max_tokens=5, max_cost_usd=1.0))
        loop, _ = self.make_loop(provider, validator, budget)
        result = loop.run(make_request(max_attempts=2))
        self.assertIs(result.stop_reason, CandidateRepairStopReason.BUDGET_EXHAUSTED)
        self.assertEqual(budget.snapshot().tokens, 10)
        self.assertEqual(budget.snapshot().llm_calls, 1)

    def test_result_is_json_serializable_and_controller_has_no_tool_or_orchestrator_dependency(self):
        provider = SequenceProvider([fenced(PROPOSAL_1)])
        loop, _ = self.make_loop(provider, RecordingValidator([passed_result()]))
        result = loop.run(make_request(max_attempts=1))
        json.dumps(result.to_dict(), allow_nan=False, sort_keys=True)
        source = inspect.getsource(candidate_loop_module)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("socket", source)
        self.assertNotIn("ValidationOrchestrator", source)
        self.assertNotIn("Stage 3", source)


if __name__ == "__main__":
    unittest.main()
