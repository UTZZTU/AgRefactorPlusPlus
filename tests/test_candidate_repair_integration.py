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
from agrefactor.models import (
    CandidateModelAdapter,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CandidateRepairOrchestrationRequest,
    CandidateRepairOrchestrationStatus,
    CandidateRepairValidationOrchestrator,
    CandidateValidationPlanRequest,
    LocalCandidateValidationHandlerFactory,
    RunContext,
    TraceRecorder,
    ValidationExecutionOutcome,
    ValidationOrchestrator,
)


BASE = 'extern "C" int top(int x) { return x; }\n'
P1 = 'extern "C" int top(int x) { return x + 1; }'
P2 = 'extern "C" int top(int x) { return x + 2; }'
PUBLIC_TB = (
    'extern "C" int top(int);\n'
    "int main() { return top(1) >= 0 ? 0 : 1; }\n"
)
HIDDEN_TB = (
    'extern "C" int top(int);\n'
    "int main() { return top(2) >= 0 ? 0 : 1; }\n"
)
SECRET = "HIDDEN_INTEGRATION_SECRET"


def make_task(*, public=True, hidden=True):
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
        task_id="candidate-repair-integration",
        kernel_path="candidate.cpp",
        kernel_name="top",
        test_suites=tuple(suites),
    )


def make_context(task=None, *, limits=None, trace_path=None):
    task = task or make_task()
    return RunContext(
        run_id="run",
        task=task,
        budget=BudgetManager(limits or BudgetLimits()),
        trace=TraceRecorder(
            "run",
            task_id=task.task_id,
            output_path=trace_path,
        ),
    )


def make_request(*, max_attempts=2):
    return CandidateRepairOrchestrationRequest(
        initial_candidate=BASE,
        original_code=BASE,
        preflight_testbench_code=PUBLIC_TB,
        suite_testbench_codes={
            "public-main": PUBLIC_TB,
            "hidden-final": HIDDEN_TB,
        },
        prompt_public_testbench_code=PUBLIC_TB,
        max_attempts=max_attempts,
    )


def feedback_item(
    feedback_id,
    *,
    state,
    owner=FeedbackOwner.CANDIDATE,
    category=None,
):
    if state is ValidationState.PREFLIGHT:
        stage = FeedbackStage.COMPILE
        category = category or FeedbackCategory.SYNTAX_ERROR
    elif state is ValidationState.CSYNTH:
        stage = FeedbackStage.CSYNTH
        category = category or FeedbackCategory.SYNTAX_ERROR
    else:
        stage = FeedbackStage.TEST
        category = category or FeedbackCategory.FUNCTIONAL_MISMATCH
    return FeedbackItem(
        feedback_id=feedback_id,
        stage=stage,
        category=category,
        severity=FeedbackSeverity.ERROR,
        owner=owner,
        summary="normalized candidate failure",
        detail=(
            SECRET
            if state is ValidationState.HIDDEN_EVALUATION
            else "safe detail"
        ),
        source="synthetic",
    )


def report_for(state, *, item=None, report_id=None):
    hidden = state is ValidationState.HIDDEN_EVALUATION
    metadata = {
        "evidence_view": (
            "operator_full" if hidden else "agent_safe"
        )
    }
    if state is ValidationState.PUBLIC_EVALUATION:
        metadata.update(
            {
                "evaluation_split": "public",
                "feedback_visible_to_agent": True,
            }
        )
    return FeedbackReport(
        report_id=report_id or f"{state.value}.report",
        source=(
            "test_evaluation"
            if state
            in {
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.HIDDEN_EVALUATION,
            }
            else state.value
        ),
        items=() if item is None else (item,),
        source_evidence=(
            {"secret": SECRET}
            if hidden
            else {"redacted": True}
        ),
        metadata=metadata,
    )


class FakeProvider(ModelProvider):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        if not self.responses:
            raise RuntimeError("no response")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return ModelResponse(
            text=f"```cpp\n{value}\n```",
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=0.01,
            ),
            finish_reason="stop",
        )


def make_adapter(responses):
    provider = FakeProvider(responses)
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="candidate-model",
            provider="fake",
            model="fake-candidate",
        )
    )
    return (
        CandidateModelAdapter(
            registry=registry,
            model_name="candidate-model",
        ),
        provider,
    )


class ScenarioFactory:
    def __init__(self, scenario):
        self.scenario = scenario
        self.requests = []
        self.context_ids = []

    def build(self, request):
        self.requests.append(request)
        handlers = {}
        states = [
            ValidationState.PREFLIGHT,
        ]
        if any(
            suite.split is EvaluationSplit.PUBLIC
            for suite in request.task.test_suites
        ):
            states.append(
                ValidationState.PUBLIC_EVALUATION
            )
        states.append(ValidationState.CSYNTH)
        if any(
            suite.split is EvaluationSplit.HIDDEN
            for suite in request.task.test_suites
        ):
            states.append(ValidationState.HIDDEN_EVALUATION)

        for state in states:
            def handler(context, state=state, request=request):
                self.context_ids.append(
                    (
                        request.attempt,
                        state,
                        id(context.budget),
                        id(context.trace),
                    )
                )
                return self.scenario(request, state)
            handlers[state] = handler
        return handlers


def pass_scenario(request, state):
    return report_for(
        state,
        report_id=(
            f"{request.validation_id}.{state.value}.pass"
        ),
    )


class ValidationExecutionOutcomeTests(unittest.TestCase):
    def test_existing_run_remains_compatible(self):
        task = make_task(public=False, hidden=False)
        factory = ScenarioFactory(pass_scenario)
        plan = CandidateValidationPlanRequest(
            task=task,
            candidate_code=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes={},
            attempt=0,
            validation_id="validation",
        )
        result = ValidationOrchestrator(
            factory.build(plan)
        ).run(
            make_context(task),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)

    def test_run_detailed_accepts_without_terminal_feedback(self):
        task = make_task(public=False, hidden=False)
        factory = ScenarioFactory(pass_scenario)
        plan = CandidateValidationPlanRequest(
            task=task,
            candidate_code=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes={},
            attempt=0,
            validation_id="validation",
        )
        outcome = ValidationOrchestrator(
            factory.build(plan)
        ).run_detailed(
            make_context(task),
            validation_id="validation",
        )
        self.assertIsInstance(
            outcome,
            ValidationExecutionOutcome,
        )
        self.assertTrue(outcome.result.accepted)
        self.assertIsNone(outcome.terminal_report)
        self.assertIsNone(outcome.terminal_decision)

    def test_run_detailed_retains_agent_safe_repair_handoff(self):
        def scenario(request, state):
            if state is ValidationState.PREFLIGHT:
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.candidate",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            return pass_scenario(request, state)

        task = make_task(public=False, hidden=False)
        factory = ScenarioFactory(scenario)
        plan = CandidateValidationPlanRequest(
            task=task,
            candidate_code=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes={},
            attempt=0,
            validation_id="validation",
        )
        outcome = ValidationOrchestrator(
            factory.build(plan)
        ).run_detailed(
            make_context(task),
            validation_id="validation",
        )
        self.assertEqual(
            outcome.result.final_state,
            ValidationState.REPAIR_PENDING,
        )
        self.assertEqual(
            outcome.terminal_report.report_id,
            "compile-report",
        )
        self.assertEqual(
            outcome.terminal_decision.action.value,
            "repair_candidate",
        )

    def test_hidden_terminal_is_not_serialized(self):
        def scenario(request, state):
            if state is ValidationState.HIDDEN_EVALUATION:
                return report_for(
                    state,
                    item=feedback_item(
                        "hidden.secret",
                        state=state,
                    ),
                    report_id=SECRET,
                )
            return pass_scenario(request, state)

        task = make_task(public=False, hidden=True)
        factory = ScenarioFactory(scenario)
        plan = CandidateValidationPlanRequest(
            task=task,
            candidate_code=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes={
                "hidden-final": HIDDEN_TB,
            },
            attempt=0,
            validation_id="validation",
        )
        outcome = ValidationOrchestrator(
            factory.build(plan)
        ).run_detailed(
            make_context(task),
            validation_id="validation",
        )
        self.assertEqual(
            outcome.result.final_state,
            ValidationState.REJECTED,
        )
        self.assertEqual(
            outcome.terminal_report.metadata[
                "evidence_view"
            ],
            "operator_full",
        )
        self.assertNotIn(
            SECRET,
            json.dumps(outcome.to_dict()),
        )


class CandidateValidationPlanContractTests(unittest.TestCase):
    def test_plan_copies_suite_mapping(self):
        mapping = {"public-main": PUBLIC_TB}
        plan = CandidateValidationPlanRequest(
            task=make_task(hidden=False),
            candidate_code=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes=mapping,
            attempt=0,
            validation_id="validation",
        )
        mapping["public-main"] = "changed"
        self.assertEqual(
            plan.suite_testbench_codes["public-main"],
            PUBLIC_TB,
        )

    def test_plan_rejects_negative_attempt(self):
        with self.assertRaises(ValueError):
            CandidateValidationPlanRequest(
                task=make_task(),
                candidate_code=BASE,
                original_code=BASE,
                preflight_testbench_code=PUBLIC_TB,
                suite_testbench_codes={
                    "public-main": PUBLIC_TB,
                    "hidden-final": HIDDEN_TB,
                },
                attempt=-1,
                validation_id="validation",
            )

    def test_orchestration_request_is_immutable(self):
        mapping = {
            "public-main": PUBLIC_TB,
            "hidden-final": HIDDEN_TB,
        }
        request = CandidateRepairOrchestrationRequest(
            initial_candidate=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes=mapping,
            prompt_public_testbench_code=PUBLIC_TB,
            max_attempts=1,
        )
        mapping["public-main"] = "changed"
        self.assertEqual(
            request.suite_testbench_codes["public-main"],
            PUBLIC_TB,
        )

    def test_orchestration_request_rejects_zero_attempts(self):
        with self.assertRaises(ValueError):
            make_request(max_attempts=0)

    def test_local_factory_builds_all_declared_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = LocalCandidateValidationHandlerFactory(
                directory
            )
            handlers = factory.build(
                CandidateValidationPlanRequest(
                    task=make_task(),
                    candidate_code=BASE,
                    original_code=BASE,
                    preflight_testbench_code=PUBLIC_TB,
                    suite_testbench_codes={
                        "public-main": PUBLIC_TB,
                        "hidden-final": HIDDEN_TB,
                    },
                    attempt=0,
                    validation_id="validation",
                )
            )
            self.assertEqual(
                set(handlers),
                {
                    ValidationState.PREFLIGHT,
                    ValidationState.CSYNTH,
                    ValidationState.PUBLIC_EVALUATION,
                    ValidationState.HIDDEN_EVALUATION,
                },
            )

    def test_local_factory_uses_supplied_candidate_everywhere(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = LocalCandidateValidationHandlerFactory(
                directory
            )
            handlers = factory.build(
                CandidateValidationPlanRequest(
                    task=make_task(),
                    candidate_code=P1,
                    original_code=BASE,
                    preflight_testbench_code=PUBLIC_TB,
                    suite_testbench_codes={
                        "public-main": PUBLIC_TB,
                        "hidden-final": HIDDEN_TB,
                    },
                    attempt=2,
                    validation_id="validation",
                )
            )
            self.assertEqual(
                handlers[
                    ValidationState.PREFLIGHT
                ].inputs.candidate_code,
                P1,
            )
            self.assertEqual(
                handlers[
                    ValidationState.CSYNTH
                ].inputs.candidate_code,
                P1,
            )
            self.assertEqual(
                handlers[
                    ValidationState.PUBLIC_EVALUATION
                ].inputs.candidate_code,
                P1,
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

    def test_missing_declared_suite_code_is_rejected(self):
        adapter, _ = make_adapter([])
        orchestrator = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(pass_scenario),
        )
        request = CandidateRepairOrchestrationRequest(
            initial_candidate=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes={
                "public-main": PUBLIC_TB,
            },
            prompt_public_testbench_code=PUBLIC_TB,
            max_attempts=1,
        )
        with self.assertRaises(ValueError):
            orchestrator.run(
                make_context(),
                request,
                validation_id="validation",
            )

    def test_public_task_requires_prompt_testbench(self):
        adapter, _ = make_adapter([])
        orchestrator = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(pass_scenario),
        )
        request = CandidateRepairOrchestrationRequest(
            initial_candidate=BASE,
            original_code=BASE,
            preflight_testbench_code=PUBLIC_TB,
            suite_testbench_codes={
                "public-main": PUBLIC_TB,
                "hidden-final": HIDDEN_TB,
            },
            prompt_public_testbench_code=None,
            max_attempts=1,
        )
        with self.assertRaises(ValueError):
            orchestrator.run(
                make_context(),
                request,
                validation_id="validation",
            )


class CandidateRepairValidationOrchestratorTests(unittest.TestCase):
    def test_initial_acceptance_skips_model(self):
        adapter, provider = make_adapter([])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(pass_scenario),
        ).run(
            make_context(),
            make_request(),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        self.assertEqual(provider.calls, [])
        self.assertIsNone(result.repair_result)

    def test_testbench_repair_is_not_taken_by_candidate_loop(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "testbench.failure",
                        state=state,
                        owner=FeedbackOwner.TESTBENCH,
                    ),
                    report_id="testbench-report",
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            REPAIR_NOT_APPLICABLE,
        )
        self.assertEqual(provider.calls, [])

    def test_unknown_terminal_skips_model(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.CSYNTH
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "unknown.failure",
                        state=state,
                        owner=FeedbackOwner.UNKNOWN,
                        category=FeedbackCategory.UNKNOWN,
                    ),
                    report_id="unknown-report",
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            VALIDATION_TERMINAL,
        )
        self.assertEqual(provider.calls, [])

    def test_compile_repair_restarts_from_preflight(self):
        calls = []

        def scenario(request, state):
            calls.append((request.attempt, state))
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            return pass_scenario(request, state)

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        self.assertEqual(
            calls[:2],
            [
                (0, ValidationState.PREFLIGHT),
                (1, ValidationState.PREFLIGHT),
            ],
        )

    def test_csynth_repair_restarts_preflight_then_csynth(self):
        calls = []

        def scenario(request, state):
            calls.append((request.attempt, state))
            if (
                request.attempt == 0
                and state is ValidationState.CSYNTH
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "csynth.failure",
                        state=state,
                    ),
                    report_id="csynth-report",
                )
            return pass_scenario(request, state)

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        self.assertIn(
            (1, ValidationState.PREFLIGHT),
            calls,
        )
        self.assertIn(
            (1, ValidationState.CSYNTH),
            calls,
        )
        self.assertLess(
            calls.index((1, ValidationState.PREFLIGHT)),
            calls.index((1, ValidationState.CSYNTH)),
        )

    def test_public_repair_restarts_full_public_prefix(self):
        calls = []

        def scenario(request, state):
            calls.append((request.attempt, state))
            if (
                request.attempt == 0
                and state is ValidationState.PUBLIC_EVALUATION
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "public.failure",
                        state=state,
                    ),
                    report_id="public-report",
                )
            return pass_scenario(request, state)

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        repair_states = [
            state for attempt, state in calls
            if attempt == 1
        ]
        self.assertEqual(
            repair_states[:3],
            [
                ValidationState.PREFLIGHT,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.CSYNTH,
            ],
        )

    def test_repaired_candidate_can_reach_hidden_acceptance(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            return pass_scenario(request, state)

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertEqual(
            result.candidate_validations[-1].steps[-1].state,
            ValidationState.HIDDEN_EVALUATION,
        )
        self.assertEqual(result.final_candidate, P1)

    def test_hidden_failure_is_terminal_and_not_reprompted(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            if (
                request.attempt == 1
                and state is ValidationState.HIDDEN_EVALUATION
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "hidden.failure",
                        state=state,
                    ),
                    report_id=SECRET,
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter([P1, P2])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=2),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            VALIDATION_TERMINAL,
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertNotIn(
            SECRET,
            json.dumps(result.to_dict()),
        )

    def test_public_candidate_failure_can_trigger_second_attempt(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            if (
                request.attempt == 1
                and state is ValidationState.PUBLIC_EVALUATION
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "public.failure",
                        state=state,
                    ),
                    report_id="public-report",
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter([P1, P2])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=2),
            validation_id="validation",
        )
        self.assertTrue(result.accepted)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result.final_candidate, P2)

    def test_toolchain_failure_after_proposal_stops(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            if (
                request.attempt == 1
                and state is ValidationState.CSYNTH
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "toolchain.failure",
                        state=state,
                        owner=FeedbackOwner.TOOLCHAIN,
                        category=(
                            FeedbackCategory.TOOLCHAIN_FAILURE
                        ),
                    ),
                    report_id="toolchain-report",
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter([P1, P2])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=2),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            VALIDATION_TERMINAL,
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result.final_candidate, BASE)

    def test_provider_errors_are_bounded(self):
        def scenario(request, state):
            if state is ValidationState.PREFLIGHT:
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter(
            [RuntimeError("one"), RuntimeError("two")]
        )
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=2),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            REPAIR_EXHAUSTED,
        )
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result.final_candidate, BASE)

    def test_zero_llm_budget_blocks_before_provider(self):
        def scenario(request, state):
            if state is ValidationState.PREFLIGHT:
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            return pass_scenario(request, state)

        adapter, provider = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(
                limits=BudgetLimits(max_llm_calls=0)
            ),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            BUDGET_EXHAUSTED,
        )
        self.assertEqual(provider.calls, [])

    def test_exhausted_repairs_return_initial_not_failed_proposal(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            if state is ValidationState.PUBLIC_EVALUATION:
                return report_for(
                    state,
                    item=feedback_item(
                        f"public.failure.{request.attempt}",
                        state=state,
                    ),
                    report_id=(
                        f"public-report-{request.attempt}"
                    ),
                )
            return pass_scenario(request, state)

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(scenario),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            REPAIR_EXHAUSTED,
        )
        self.assertEqual(result.final_candidate, BASE)
        self.assertEqual(
            result.repair_result.current_candidate,
            P1,
        )

    def test_exact_budget_and_trace_are_shared(self):
        def scenario(request, state):
            if (
                request.attempt == 0
                and state is ValidationState.PREFLIGHT
            ):
                return report_for(
                    state,
                    item=feedback_item(
                        "compile.failure",
                        state=state,
                    ),
                    report_id="compile-report",
                )
            return pass_scenario(request, state)

        factory = ScenarioFactory(scenario)
        adapter, _ = make_adapter([P1])
        context = make_context()
        CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=factory,
        ).run(
            context,
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertEqual(
            {entry[2] for entry in factory.context_ids},
            {id(context.budget)},
        )
        self.assertEqual(
            {entry[3] for entry in factory.context_ids},
            {id(context.trace)},
        )

    def test_non_mapping_factory_result_is_validator_error(self):
        class BadFactory:
            def build(self, request):
                if request.attempt == 0:
                    return ScenarioFactory(
                        lambda request, state: (
                            report_for(
                                state,
                                item=(
                                    feedback_item(
                                        "compile.failure",
                                        state=state,
                                    )
                                    if state
                                    is ValidationState.PREFLIGHT
                                    else None
                                ),
                                report_id=(
                                    "compile-report"
                                    if state
                                    is ValidationState.PREFLIGHT
                                    else None
                                ),
                            )
                        )
                    ).build(request)
                return []

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=BadFactory(),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            VALIDATOR_ERROR,
        )

    def test_missing_proposal_handler_is_validator_error(self):
        class MissingFactory:
            def build(self, request):
                scenario = ScenarioFactory(
                    lambda request, state: (
                        report_for(
                            state,
                            item=(
                                feedback_item(
                                    "compile.failure",
                                    state=state,
                                )
                                if (
                                    request.attempt == 0
                                    and state
                                    is ValidationState.PREFLIGHT
                                )
                                else None
                            ),
                            report_id=(
                                "compile-report"
                                if (
                                    request.attempt == 0
                                    and state
                                    is ValidationState.PREFLIGHT
                                )
                                else None
                            ),
                        )
                    )
                )
                handlers = dict(scenario.build(request))
                if request.attempt > 0:
                    handlers.pop(ValidationState.CSYNTH)
                return handlers

        adapter, _ = make_adapter([P1])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=MissingFactory(),
        ).run(
            make_context(),
            make_request(max_attempts=1),
            validation_id="validation",
        )
        self.assertEqual(
            result.status,
            CandidateRepairOrchestrationStatus.
            VALIDATOR_ERROR,
        )

    def test_result_is_json_serializable(self):
        adapter, _ = make_adapter([])
        result = CandidateRepairValidationOrchestrator(
            model_adapter=adapter,
            handler_factory=ScenarioFactory(pass_scenario),
        ).run(
            make_context(),
            make_request(),
            validation_id="validation",
        )
        encoded = json.dumps(
            result.to_dict(),
            sort_keys=True,
        )
        self.assertIn('"status": "accepted"', encoded)

    def test_hidden_secret_is_absent_from_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"

            def scenario(request, state):
                if (
                    request.attempt == 0
                    and state is ValidationState.PREFLIGHT
                ):
                    return report_for(
                        state,
                        item=feedback_item(
                            "compile.failure",
                            state=state,
                        ),
                        report_id="compile-report",
                    )
                if state is ValidationState.HIDDEN_EVALUATION:
                    return report_for(
                        state,
                        item=feedback_item(
                            "hidden.failure",
                            state=state,
                        ),
                        report_id=SECRET,
                    )
                return pass_scenario(request, state)

            adapter, _ = make_adapter([P1])
            result = CandidateRepairValidationOrchestrator(
                model_adapter=adapter,
                handler_factory=ScenarioFactory(scenario),
            ).run(
                make_context(trace_path=trace_path),
                make_request(max_attempts=1),
                validation_id="validation",
            )
            combined = (
                json.dumps(result.to_dict())
                + trace_path.read_text(encoding="utf-8")
            )
            self.assertNotIn(SECRET, combined)

    def test_runtime_exports_are_available(self):
        from agrefactor import runtime

        for name in (
            "CandidateRepairOrchestrationRequest",
            "CandidateRepairOrchestrationResult",
            "CandidateRepairOrchestrationStatus",
            "CandidateRepairValidationOrchestrator",
            "CandidateValidationHandlerFactory",
            "CandidateValidationPlanRequest",
            "LocalCandidateValidationHandlerFactory",
            "ValidationExecutionOutcome",
        ):
            self.assertTrue(hasattr(runtime, name))

    def test_integration_module_does_not_import_models_in_handlers(self):
        import inspect
        import agrefactor.runtime.csim_stage as csim_stage
        import agrefactor.runtime.csynth_stage as csynth_stage
        import agrefactor.runtime.preflight_stage as preflight_stage

        for module in (
            preflight_stage,
            csynth_stage,
            csim_stage,
        ):
            source = inspect.getsource(module)
            self.assertNotIn(
                "CandidateModelAdapter",
                source,
            )
            self.assertNotIn(
                "BoundedCandidateRepairLoop",
                source,
            )


if __name__ == "__main__":
    unittest.main()

