"""Stage 2 injected-fault corpus and ground-truth comparison runner."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from agrefactor.config import TargetProfile
from agrefactor.evaluation import FeedbackRouteAction, ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.runtime.budget import BudgetLimits, BudgetManager, BudgetUsage
from agrefactor.runtime.candidate_repair_integration import (
    CandidateValidationHandlerFactory,
    CandidateValidationPlanRequest,
    LocalCandidateValidationHandlerFactory,
)
from agrefactor.runtime.runner import RunContext
from agrefactor.runtime.trace import TraceRecorder
from agrefactor.runtime.validation_orchestrator import (
    ValidationOrchestrationResult,
    ValidationOrchestrator,
)

from .stage2_matrix import (
    Stage2SmokeBudgetExpectation,
    Stage2SmokeExpectedRoute,
    Stage2SmokeExpectedTerminalState,
    Stage2SmokeGroundTruth,
    Stage2SmokeGroundTruthOwner,
    Stage2SmokeGroundTruthStage,
    Stage2SmokeHiddenVisibility,
    Stage2SmokeScenarioKind,
    get_stage2_smoke_case,
)


class Stage2SmokeFaultExecutionKind(str, Enum):
    REAL_LOCAL_CHAIN = "real_local_chain"
    DETERMINISTIC_REPORTS = "deterministic_reports"


_ROUTE_MAP = {
    FeedbackRouteAction.REPAIR_CANDIDATE: Stage2SmokeExpectedRoute.REPAIR_CANDIDATE,
    FeedbackRouteAction.REPAIR_TESTBENCH: Stage2SmokeExpectedRoute.REPAIR_TESTBENCH,
    FeedbackRouteAction.REPAIR_ORIGINAL: Stage2SmokeExpectedRoute.REPAIR_ORIGINAL,
    FeedbackRouteAction.FIX_TOOLCHAIN: Stage2SmokeExpectedRoute.EXTERNAL_REMEDIATION,
    FeedbackRouteAction.REVIEW_UNKNOWN: Stage2SmokeExpectedRoute.REVIEW_REQUIRED,
    FeedbackRouteAction.REVIEW_MIXED: Stage2SmokeExpectedRoute.REVIEW_REQUIRED,
}
_FINAL_MAP = {
    ValidationState.REPAIR_PENDING: Stage2SmokeExpectedTerminalState.REPAIR_PENDING,
    ValidationState.REJECTED: Stage2SmokeExpectedTerminalState.REJECTED,
    ValidationState.BLOCKED: Stage2SmokeExpectedTerminalState.BLOCKED,
    ValidationState.REVIEW_REQUIRED: Stage2SmokeExpectedTerminalState.REVIEW_REQUIRED,
}


@dataclass(frozen=True, slots=True)
class Stage2SmokeFaultScenario:
    scenario_id: str
    base_case_id: str
    execution_kind: Stage2SmokeFaultExecutionKind
    ground_truth: Stage2SmokeGroundTruth
    expected_route_action: FeedbackRouteAction
    expected_final_state: ValidationState
    expected_failure_state: ValidationState
    expected_budget: Stage2SmokeBudgetExpectation
    original_code: str
    candidate_code: str
    preflight_testbench_code: str
    public_testbench_code: str
    hidden_testbench_code: str
    deterministic_feedback_items: tuple[FeedbackItem, ...] = ()
    deterministic_secret_marker: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "scenario_id",
            "base_case_id",
            "original_code",
            "candidate_code",
            "preflight_testbench_code",
            "public_testbench_code",
            "hidden_testbench_code",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        execution_kind = (
            self.execution_kind
            if isinstance(self.execution_kind, Stage2SmokeFaultExecutionKind)
            else Stage2SmokeFaultExecutionKind(str(self.execution_kind))
        )
        route = (
            self.expected_route_action
            if isinstance(self.expected_route_action, FeedbackRouteAction)
            else FeedbackRouteAction(str(self.expected_route_action))
        )
        final = (
            self.expected_final_state
            if isinstance(self.expected_final_state, ValidationState)
            else ValidationState(str(self.expected_final_state))
        )
        failure = (
            self.expected_failure_state
            if isinstance(self.expected_failure_state, ValidationState)
            else ValidationState(str(self.expected_failure_state))
        )
        if not failure.active:
            raise ValueError("expected_failure_state must be active")
        if not isinstance(self.ground_truth, Stage2SmokeGroundTruth):
            raise TypeError("ground_truth must be Stage2SmokeGroundTruth")
        if not isinstance(self.expected_budget, Stage2SmokeBudgetExpectation):
            raise TypeError("expected_budget must be Stage2SmokeBudgetExpectation")
        if self.ground_truth.case_id != self.scenario_id:
            raise ValueError("ground_truth.case_id must match scenario_id")
        if self.ground_truth.scenario_kind is not Stage2SmokeScenarioKind.INJECTED_FAULT:
            raise ValueError("fault ground truth must be injected_fault")
        base = get_stage2_smoke_case(self.base_case_id)
        if base.kernel_type is not self.ground_truth.kernel_type:
            raise ValueError("ground-truth kernel type must match base case")
        if _ROUTE_MAP[route] is not self.ground_truth.expected_route:
            raise ValueError("route action conflicts with ground truth")
        if _FINAL_MAP[final] is not self.ground_truth.expected_terminal_state:
            raise ValueError("final state conflicts with ground truth")
        _check_stage(failure, self.ground_truth.ground_truth_stage)
        items = tuple(self.deterministic_feedback_items)
        if not all(isinstance(item, FeedbackItem) for item in items):
            raise TypeError("deterministic feedback must contain FeedbackItem values")
        if execution_kind is Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN and items:
            raise ValueError("real scenarios cannot contain deterministic feedback")
        if execution_kind is Stage2SmokeFaultExecutionKind.DETERMINISTIC_REPORTS and not items:
            raise ValueError("deterministic scenarios require feedback")
        marker = self.deterministic_secret_marker
        if marker is not None:
            if not isinstance(marker, str) or not marker.strip():
                raise ValueError("deterministic_secret_marker must not be empty")
            encoded = json.dumps([item.to_dict() for item in items], sort_keys=True)
            if marker not in encoded:
                raise ValueError("secret marker must occur in deterministic feedback")
        object.__setattr__(self, "execution_kind", execution_kind)
        object.__setattr__(self, "expected_route_action", route)
        object.__setattr__(self, "expected_final_state", final)
        object.__setattr__(self, "expected_failure_state", failure)
        object.__setattr__(self, "deterministic_feedback_items", items)

    @property
    def suite_testbench_codes(self) -> dict[str, str]:
        base = get_stage2_smoke_case(self.base_case_id)
        return {
            base.public_suite_id: self.public_testbench_code,
            base.hidden_suite_id: self.hidden_testbench_code,
        }

    def build_task(self, *, target: TargetProfile | None = None):
        return get_stage2_smoke_case(self.base_case_id).build_task(target=target)

    def operator_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scenario_id": self.scenario_id,
            "base_case_id": self.base_case_id,
            "execution_kind": self.execution_kind.value,
            "ground_truth": self.ground_truth.to_dict(),
            "expected_route_action": self.expected_route_action.value,
            "expected_final_state": self.expected_final_state.value,
            "expected_failure_state": self.expected_failure_state.value,
            "expected_budget": self.expected_budget.to_dict(),
            "source_sha256": {
                "original_code": _sha(self.original_code),
                "candidate_code": _sha(self.candidate_code),
                "preflight_testbench_code": _sha(self.preflight_testbench_code),
                "public_testbench_code": _sha(self.public_testbench_code),
                "hidden_testbench_code": _sha(self.hidden_testbench_code),
            },
        }

    def agent_safe_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scenario_id": self.scenario_id,
            "base_case_id": self.base_case_id,
            "execution_kind": self.execution_kind.value,
            "source_sha256": {
                "original_code": _sha(self.original_code),
                "candidate_code": _sha(self.candidate_code),
                "preflight_testbench_code": _sha(self.preflight_testbench_code),
                "public_testbench_code": _sha(self.public_testbench_code),
            },
        }


@dataclass(frozen=True, slots=True)
class Stage2SmokeFaultObservation:
    scenario_id: str
    execution_kind: Stage2SmokeFaultExecutionKind
    validation_result: ValidationOrchestrationResult
    observed_failure_state: ValidationState
    observed_route_action: FeedbackRouteAction
    observed_final_state: ValidationState
    budget_delta: Stage2SmokeBudgetExpectation
    trace_jsonl_path: str
    trace_snapshot_path: str
    source_sha256: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scenario_id": self.scenario_id,
            "execution_kind": self.execution_kind.value,
            "observed_failure_state": self.observed_failure_state.value,
            "observed_route_action": self.observed_route_action.value,
            "observed_final_state": self.observed_final_state.value,
            "budget_delta": self.budget_delta.to_dict(),
            "validation_result": self.validation_result.to_dict(),
            "trace_jsonl_path": self.trace_jsonl_path,
            "trace_snapshot_path": self.trace_snapshot_path,
            "source_sha256": dict(self.source_sha256),
        }


@dataclass(frozen=True, slots=True)
class Stage2SmokeFaultMatrixResult:
    matrix_id: str
    observations: tuple[Stage2SmokeFaultObservation, ...]
    expected_total_budget: Stage2SmokeBudgetExpectation
    total_usage: BudgetUsage
    work_root: str

    @property
    def matched(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "matrix_id": self.matrix_id,
            "matched": True,
            "scenario_count": len(self.observations),
            "observations": [item.to_dict() for item in self.observations],
            "expected_total_budget": self.expected_total_budget.to_dict(),
            "total_usage": self.total_usage.to_dict(),
            "work_root": self.work_root,
        }

    def write_json(self, path: str | os.PathLike[str]) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return destination


class _DeterministicFactory:
    def __init__(self, scenario: Stage2SmokeFaultScenario) -> None:
        self.scenario = scenario

    def build(self, request: CandidateValidationPlanRequest):
        def make_handler(state: ValidationState):
            def handler(context: RunContext) -> FeedbackReport:
                hidden = state is ValidationState.HIDDEN_EVALUATION
                items = (
                    self.scenario.deterministic_feedback_items
                    if state is self.scenario.expected_failure_state
                    else ()
                )
                source_evidence = {}
                if hidden and items and self.scenario.deterministic_secret_marker:
                    source_evidence["operator_secret"] = (
                        self.scenario.deterministic_secret_marker
                    )
                return FeedbackReport(
                    report_id=f"{request.validation_id}.{state.value}.report",
                    source="stage2-smoke-deterministic",
                    items=items,
                    source_evidence=source_evidence,
                    metadata={
                        "evidence_view": "operator_full" if hidden else "agent_safe"
                    },
                )

            return handler

        return {
            state: make_handler(state)
            for state in (
                ValidationState.PREFLIGHT,
                ValidationState.CSYNTH,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.PUBLIC_COSIM,
                ValidationState.HIDDEN_EVALUATION,
            )
        }


class Stage2SmokeFaultMatrixRunner:
    def __init__(
        self,
        work_root: str | os.PathLike[str],
        *,
        real_handler_factory: CandidateValidationHandlerFactory | None = None,
        csynth_timelimit: int = 600,
        csim_timelimit: int = 90,
    ) -> None:
        raw = os.fspath(work_root)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("work_root must not be empty")
        self.work_root = Path(raw)
        self.real_handler_factory = (
            real_handler_factory
            if real_handler_factory is not None
            else LocalCandidateValidationHandlerFactory(
                self.work_root / "validation",
                csynth_timelimit=csynth_timelimit,
                csim_timelimit=csim_timelimit,
            )
        )
        if not callable(getattr(self.real_handler_factory, "build", None)):
            raise TypeError("real_handler_factory must provide build(request)")

    def run(
        self,
        scenarios: Iterable[Stage2SmokeFaultScenario] | None = None,
        *,
        matrix_id: str = "stage2-smoke-fault-matrix",
        target: TargetProfile | None = None,
    ) -> Stage2SmokeFaultMatrixResult:
        selected = _normalize(scenarios)
        if target is not None and not isinstance(target, TargetProfile):
            raise TypeError("target must be TargetProfile or None")
        if self.work_root.exists() and any(self.work_root.iterdir()):
            raise FileExistsError(f"work root is not empty: {self.work_root}")
        self.work_root.mkdir(parents=True, exist_ok=True)
        expected_total = expected_stage2_smoke_fault_budget(selected)
        budget = BudgetManager(
            BudgetLimits(
                max_tool_calls=expected_total.tool_calls,
                max_compile_calls=expected_total.compile_calls,
                max_csynth_calls=expected_total.csynth_calls,
                max_csim_calls=expected_total.csim_calls,
                max_cosim_calls=expected_total.cosim_calls,
                max_llm_calls=0,
                max_tokens=0,
                max_cost_usd=0.0,
            )
        )
        observations: list[Stage2SmokeFaultObservation] = []
        for scenario in selected:
            task = scenario.build_task(target=target)
            validation_id = f"{matrix_id}.{scenario.scenario_id}"
            trace_jsonl = self.work_root / "traces" / f"{scenario.scenario_id}.jsonl"
            trace_snapshot = self.work_root / "traces" / f"{scenario.scenario_id}.json"
            trace = TraceRecorder(
                validation_id,
                task_id=task.task_id,
                output_path=trace_jsonl,
            )
            request = CandidateValidationPlanRequest(
                task=task,
                candidate_code=scenario.candidate_code,
                original_code=scenario.original_code,
                preflight_testbench_code=scenario.preflight_testbench_code,
                suite_testbench_codes=scenario.suite_testbench_codes,
                attempt=0,
                validation_id=validation_id,
            )
            factory = (
                self.real_handler_factory
                if scenario.execution_kind
                is Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN
                else _DeterministicFactory(scenario)
            )
            before = budget.snapshot()
            outcome = ValidationOrchestrator(factory.build(request)).run_detailed(
                RunContext(
                    run_id=validation_id,
                    task=task,
                    budget=budget,
                    trace=trace,
                ),
                validation_id=validation_id,
            )
            after = budget.snapshot()
            delta = _delta(before, after)
            last = outcome.result.steps[-1]
            observed = (
                last.state,
                last.route_action,
                outcome.result.final_state,
                delta,
            )
            expected = (
                scenario.expected_failure_state,
                scenario.expected_route_action,
                scenario.expected_final_state,
                scenario.expected_budget,
            )
            if observed != expected:
                raise RuntimeError(
                    f"{scenario.scenario_id} observed {observed!r}, "
                    f"expected {expected!r}"
                )
            if scenario.expected_final_state is ValidationState.REPAIR_PENDING:
                if not last.selected_feedback_items:
                    raise RuntimeError(
                        f"{scenario.scenario_id} repair handoff has no feedback"
                    )
                if not last.transition.agent_feedback_allowed:
                    raise RuntimeError(
                        f"{scenario.scenario_id} repair handoff is not agent-safe"
                    )
            elif last.selected_feedback_items:
                raise RuntimeError(
                    f"{scenario.scenario_id} exposed selected feedback"
                )
            if scenario.expected_failure_state is ValidationState.HIDDEN_EVALUATION:
                if last.source_report_id is not None:
                    raise RuntimeError(
                        f"{scenario.scenario_id} exposed Hidden report ID"
                    )
                if not last.metadata.get("hidden_source_suppressed"):
                    raise RuntimeError(
                        f"{scenario.scenario_id} did not suppress Hidden source"
                    )
            trace.write_json(trace_snapshot)
            safe_text = json.dumps(
                {
                    "result": outcome.result.to_dict(),
                    "trace": trace.to_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            base = get_stage2_smoke_case(scenario.base_case_id)
            forbidden = [
                base.hidden_secret_marker,
                scenario.hidden_testbench_code,
                "ground_truth",
            ]
            if scenario.deterministic_secret_marker:
                forbidden.append(scenario.deterministic_secret_marker)
            if any(value in safe_text for value in forbidden):
                raise RuntimeError(
                    f"{scenario.scenario_id} leaked operator-only content"
                )
            observations.append(
                Stage2SmokeFaultObservation(
                    scenario_id=scenario.scenario_id,
                    execution_kind=scenario.execution_kind,
                    validation_result=outcome.result,
                    observed_failure_state=last.state,
                    observed_route_action=last.route_action,
                    observed_final_state=outcome.result.final_state,
                    budget_delta=delta,
                    trace_jsonl_path=str(trace_jsonl),
                    trace_snapshot_path=str(trace_snapshot),
                    source_sha256=scenario.agent_safe_manifest()["source_sha256"],
                )
            )
        total = budget.snapshot()
        if _as_expectation(total) != expected_total:
            raise RuntimeError("matrix total budget mismatch")
        return Stage2SmokeFaultMatrixResult(
            matrix_id=matrix_id,
            observations=tuple(observations),
            expected_total_budget=expected_total,
            total_usage=total,
            work_root=str(self.work_root),
        )


def expected_stage2_smoke_fault_budget(
    scenarios: Iterable[Stage2SmokeFaultScenario] | None = None,
) -> Stage2SmokeBudgetExpectation:
    selected = _normalize(scenarios)
    return Stage2SmokeBudgetExpectation(
        tool_calls=sum(item.expected_budget.tool_calls for item in selected),
        compile_calls=sum(item.expected_budget.compile_calls for item in selected),
        csynth_calls=sum(item.expected_budget.csynth_calls for item in selected),
        csim_calls=sum(item.expected_budget.csim_calls for item in selected),
        cosim_calls=sum(item.expected_budget.cosim_calls for item in selected),
        llm_calls=0,
        tokens=0,
        cost_usd=0.0,
    )


def get_stage2_smoke_fault_scenario(scenario_id: str) -> Stage2SmokeFaultScenario:
    for scenario in STAGE2_SMOKE_FAULT_SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(f"unknown fault scenario: {scenario_id}")


def _normalize(
    scenarios: Iterable[Stage2SmokeFaultScenario] | None,
) -> tuple[Stage2SmokeFaultScenario, ...]:
    selected = (
        STAGE2_SMOKE_FAULT_SCENARIOS
        if scenarios is None
        else tuple(scenarios)
    )
    if not selected:
        raise ValueError("scenarios must not be empty")
    if not all(isinstance(item, Stage2SmokeFaultScenario) for item in selected):
        raise TypeError("scenarios must contain Stage2SmokeFaultScenario")
    ids = [item.scenario_id for item in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario IDs must be unique")
    return tuple(selected)


def _truth(
    scenario_id: str,
    base_case_id: str,
    injected_fault: str,
    owner: Stage2SmokeGroundTruthOwner,
    stage: Stage2SmokeGroundTruthStage,
    route: Stage2SmokeExpectedRoute,
    terminal: Stage2SmokeExpectedTerminalState,
) -> Stage2SmokeGroundTruth:
    return Stage2SmokeGroundTruth(
        case_id=scenario_id,
        kernel_type=get_stage2_smoke_case(base_case_id).kernel_type,
        scenario_kind=Stage2SmokeScenarioKind.INJECTED_FAULT,
        injected_fault=injected_fault,
        ground_truth_owner=owner,
        ground_truth_stage=stage,
        expected_route=route,
        expected_terminal_state=terminal,
        hidden_visibility_expectation=(
            Stage2SmokeHiddenVisibility.OPERATOR_ONLY_NEVER_AGENT
        ),
    )


def _make(
    scenario_id: str,
    base_case_id: str,
    execution_kind: Stage2SmokeFaultExecutionKind,
    truth: Stage2SmokeGroundTruth,
    route: FeedbackRouteAction,
    final: ValidationState,
    failure: ValidationState,
    budget: Stage2SmokeBudgetExpectation,
    *,
    original_code: str | None = None,
    candidate_code: str | None = None,
    preflight_testbench_code: str | None = None,
    feedback_items: Sequence[FeedbackItem] = (),
    secret_marker: str | None = None,
) -> Stage2SmokeFaultScenario:
    base = get_stage2_smoke_case(base_case_id)
    return Stage2SmokeFaultScenario(
        scenario_id=scenario_id,
        base_case_id=base_case_id,
        execution_kind=execution_kind,
        ground_truth=truth,
        expected_route_action=route,
        expected_final_state=final,
        expected_failure_state=failure,
        expected_budget=budget,
        original_code=original_code or base.original_code,
        candidate_code=candidate_code or base.candidate_code,
        preflight_testbench_code=(
            preflight_testbench_code or base.preflight_testbench_code
        ),
        public_testbench_code=base.public_testbench_code,
        hidden_testbench_code=base.hidden_testbench_code,
        deterministic_feedback_items=tuple(feedback_items),
        deterministic_secret_marker=secret_marker,
    )


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"source anchor mismatch: {old}")
    return text.replace(old, new, 1)


def _check_stage(
    state: ValidationState,
    stage: Stage2SmokeGroundTruthStage,
) -> None:
    allowed = {
        ValidationState.PREFLIGHT: {
            Stage2SmokeGroundTruthStage.STATIC_CHECK,
            Stage2SmokeGroundTruthStage.COMPILE,
            Stage2SmokeGroundTruthStage.LINK,
        },
        ValidationState.CSYNTH: {
            Stage2SmokeGroundTruthStage.CSYNTH,
            Stage2SmokeGroundTruthStage.CONFIGURATION,
        },
        ValidationState.PUBLIC_EVALUATION: {
            Stage2SmokeGroundTruthStage.PUBLIC_EVALUATION,
        },
        ValidationState.PUBLIC_COSIM: {
            Stage2SmokeGroundTruthStage.PUBLIC_COSIM,
        },
        ValidationState.HIDDEN_EVALUATION: {
            Stage2SmokeGroundTruthStage.HIDDEN_EVALUATION,
        },
    }
    if stage not in allowed[state]:
        raise ValueError("ground-truth stage conflicts with failure state")


def _delta(before: BudgetUsage, after: BudgetUsage) -> Stage2SmokeBudgetExpectation:
    return Stage2SmokeBudgetExpectation(
        tool_calls=after.tool_calls - before.tool_calls,
        compile_calls=after.compile_calls - before.compile_calls,
        csynth_calls=after.csynth_calls - before.csynth_calls,
        csim_calls=after.csim_calls - before.csim_calls,
        cosim_calls=after.cosim_calls - before.cosim_calls,
        llm_calls=after.llm_calls - before.llm_calls,
        tokens=after.tokens - before.tokens,
        cost_usd=after.cost_usd - before.cost_usd,
    )


def _as_expectation(usage: BudgetUsage) -> Stage2SmokeBudgetExpectation:
    return Stage2SmokeBudgetExpectation(
        tool_calls=usage.tool_calls,
        compile_calls=usage.compile_calls,
        csynth_calls=usage.csynth_calls,
        csim_calls=usage.csim_calls,
        cosim_calls=usage.cosim_calls,
        llm_calls=usage.llm_calls,
        tokens=usage.tokens,
        cost_usd=usage.cost_usd,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_array = get_stage2_smoke_case("array-map")
_reduction = get_stage2_smoke_case("reduction")
_stencil = get_stage2_smoke_case("nested-stencil")

_candidate_compile = _array.candidate_code + "\nint stage2_candidate_fault = ;\n"
_testbench_compile = (
    "Stage2MissingType stage2_missing;\n"
    + _reduction.preflight_testbench_code
)
_original_compile = _stencil.original_code + "\nint stage2_original_fault = ;\n"
_public_mismatch = _replace_once(
    _reduction.candidate_code,
    "return sum;",
    "return sum + 1;",
)
_hidden_mismatch = _replace_once(
    _array.candidate_code,
    "output[i] = input[i] * 3 + 1;",
    (
        "int value = input[i] * 3 + 1;\n"
        "        if (input[i] == 31) value += 1;\n"
        "        output[i] = value;"
    ),
)
_HIDDEN_UNKNOWN = "HIDDEN_FAULT_UNKNOWN_4D91"

_toolchain = FeedbackItem(
    feedback_id="toolchain.item",
    stage=FeedbackStage.TOOLCHAIN,
    category=FeedbackCategory.TOOLCHAIN_FAILURE,
    severity=FeedbackSeverity.FATAL,
    owner=FeedbackOwner.TOOLCHAIN,
    summary="Injected toolchain failure",
)
_unknown = FeedbackItem(
    feedback_id="unknown.item",
    stage=FeedbackStage.CSYNTH,
    category=FeedbackCategory.UNKNOWN,
    severity=FeedbackSeverity.ERROR,
    owner=FeedbackOwner.UNKNOWN,
    summary="Injected unknown synthesis failure",
)
_mixed_candidate = FeedbackItem(
    feedback_id="mixed.candidate",
    stage=FeedbackStage.CSIM,
    category=FeedbackCategory.FUNCTIONAL_MISMATCH,
    severity=FeedbackSeverity.ERROR,
    owner=FeedbackOwner.CANDIDATE,
    summary="Injected candidate mismatch",
)
_mixed_testbench = FeedbackItem(
    feedback_id="mixed.testbench",
    stage=FeedbackStage.CSIM,
    category=FeedbackCategory.INVALID_INPUT,
    severity=FeedbackSeverity.ERROR,
    owner=FeedbackOwner.TESTBENCH,
    summary="Injected testbench issue",
)
_hidden_unknown = FeedbackItem(
    feedback_id="hidden.unknown",
    stage=FeedbackStage.CSIM,
    category=FeedbackCategory.UNKNOWN,
    severity=FeedbackSeverity.ERROR,
    owner=FeedbackOwner.UNKNOWN,
    summary="Hidden ambiguous failure",
    detail=f"operator-only {_HIDDEN_UNKNOWN}",
)


STAGE2_SMOKE_FAULT_SCENARIOS = (
    _make(
        "candidate-compile",
        "array-map",
        Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN,
        _truth(
            "candidate-compile",
            "array-map",
            "candidate_global_syntax_error",
            Stage2SmokeGroundTruthOwner.CANDIDATE,
            Stage2SmokeGroundTruthStage.COMPILE,
            Stage2SmokeExpectedRoute.REPAIR_CANDIDATE,
            Stage2SmokeExpectedTerminalState.REPAIR_PENDING,
        ),
        FeedbackRouteAction.REPAIR_CANDIDATE,
        ValidationState.REPAIR_PENDING,
        ValidationState.PREFLIGHT,
        Stage2SmokeBudgetExpectation(1, 1, 0, 0),
        candidate_code=_candidate_compile,
    ),
    _make(
        "testbench-compile",
        "reduction",
        Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN,
        _truth(
            "testbench-compile",
            "reduction",
            "preflight_undeclared_type",
            Stage2SmokeGroundTruthOwner.TESTBENCH,
            Stage2SmokeGroundTruthStage.COMPILE,
            Stage2SmokeExpectedRoute.REPAIR_TESTBENCH,
            Stage2SmokeExpectedTerminalState.REPAIR_PENDING,
        ),
        FeedbackRouteAction.REPAIR_TESTBENCH,
        ValidationState.REPAIR_PENDING,
        ValidationState.PREFLIGHT,
        Stage2SmokeBudgetExpectation(1, 1, 0, 0),
        preflight_testbench_code=_testbench_compile,
    ),
    _make(
        "original-compile",
        "nested-stencil",
        Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN,
        _truth(
            "original-compile",
            "nested-stencil",
            "original_global_syntax_error",
            Stage2SmokeGroundTruthOwner.ORIGINAL,
            Stage2SmokeGroundTruthStage.COMPILE,
            Stage2SmokeExpectedRoute.REPAIR_ORIGINAL,
            Stage2SmokeExpectedTerminalState.REPAIR_PENDING,
        ),
        FeedbackRouteAction.REPAIR_ORIGINAL,
        ValidationState.REPAIR_PENDING,
        ValidationState.PREFLIGHT,
        Stage2SmokeBudgetExpectation(1, 1, 0, 0),
        original_code=_original_compile,
    ),
    _make(
        "public-candidate-mismatch",
        "reduction",
        Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN,
        _truth(
            "public-candidate-mismatch",
            "reduction",
            "candidate_returns_sum_plus_one",
            Stage2SmokeGroundTruthOwner.CANDIDATE,
            Stage2SmokeGroundTruthStage.PUBLIC_EVALUATION,
            Stage2SmokeExpectedRoute.REPAIR_CANDIDATE,
            Stage2SmokeExpectedTerminalState.REPAIR_PENDING,
        ),
        FeedbackRouteAction.REPAIR_CANDIDATE,
        ValidationState.REPAIR_PENDING,
        ValidationState.PUBLIC_EVALUATION,
        Stage2SmokeBudgetExpectation(4, 2, 1, 1),
        candidate_code=_public_mismatch,
    ),
    _make(
        "hidden-candidate-mismatch",
        "array-map",
        Stage2SmokeFaultExecutionKind.REAL_LOCAL_CHAIN,
        _truth(
            "hidden-candidate-mismatch",
            "array-map",
            "candidate_wrong_only_for_hidden_31",
            Stage2SmokeGroundTruthOwner.CANDIDATE,
            Stage2SmokeGroundTruthStage.HIDDEN_EVALUATION,
            Stage2SmokeExpectedRoute.REPAIR_CANDIDATE,
            Stage2SmokeExpectedTerminalState.REJECTED,
        ),
        FeedbackRouteAction.REPAIR_CANDIDATE,
        ValidationState.REJECTED,
        ValidationState.HIDDEN_EVALUATION,
        Stage2SmokeBudgetExpectation(8, 3, 1, 2, 1),
        candidate_code=_hidden_mismatch,
    ),
    _make(
        "toolchain-block",
        "multi-output",
        Stage2SmokeFaultExecutionKind.DETERMINISTIC_REPORTS,
        _truth(
            "toolchain-block",
            "multi-output",
            "normalized_toolchain_failure",
            Stage2SmokeGroundTruthOwner.TOOLCHAIN,
            Stage2SmokeGroundTruthStage.CSYNTH,
            Stage2SmokeExpectedRoute.EXTERNAL_REMEDIATION,
            Stage2SmokeExpectedTerminalState.BLOCKED,
        ),
        FeedbackRouteAction.FIX_TOOLCHAIN,
        ValidationState.BLOCKED,
        ValidationState.CSYNTH,
        Stage2SmokeBudgetExpectation(0, 0, 0, 0),
        feedback_items=(_toolchain,),
    ),
    _make(
        "unknown-review",
        "struct-record",
        Stage2SmokeFaultExecutionKind.DETERMINISTIC_REPORTS,
        _truth(
            "unknown-review",
            "struct-record",
            "normalized_unknown_synthesis_failure",
            Stage2SmokeGroundTruthOwner.UNKNOWN,
            Stage2SmokeGroundTruthStage.CSYNTH,
            Stage2SmokeExpectedRoute.REVIEW_REQUIRED,
            Stage2SmokeExpectedTerminalState.REVIEW_REQUIRED,
        ),
        FeedbackRouteAction.REVIEW_UNKNOWN,
        ValidationState.REVIEW_REQUIRED,
        ValidationState.CSYNTH,
        Stage2SmokeBudgetExpectation(0, 0, 0, 0),
        feedback_items=(_unknown,),
    ),
    _make(
        "mixed-public-review",
        "hls-stream",
        Stage2SmokeFaultExecutionKind.DETERMINISTIC_REPORTS,
        _truth(
            "mixed-public-review",
            "hls-stream",
            "mixed_candidate_testbench_public",
            Stage2SmokeGroundTruthOwner.MIXED,
            Stage2SmokeGroundTruthStage.PUBLIC_EVALUATION,
            Stage2SmokeExpectedRoute.REVIEW_REQUIRED,
            Stage2SmokeExpectedTerminalState.REVIEW_REQUIRED,
        ),
        FeedbackRouteAction.REVIEW_MIXED,
        ValidationState.REVIEW_REQUIRED,
        ValidationState.PUBLIC_EVALUATION,
        Stage2SmokeBudgetExpectation(0, 0, 0, 0),
        feedback_items=(_mixed_candidate, _mixed_testbench),
    ),
    _make(
        "hidden-unknown-review",
        "stateful",
        Stage2SmokeFaultExecutionKind.DETERMINISTIC_REPORTS,
        _truth(
            "hidden-unknown-review",
            "stateful",
            "hidden_ambiguous_operator_only_failure",
            Stage2SmokeGroundTruthOwner.UNKNOWN,
            Stage2SmokeGroundTruthStage.HIDDEN_EVALUATION,
            Stage2SmokeExpectedRoute.REVIEW_REQUIRED,
            Stage2SmokeExpectedTerminalState.REVIEW_REQUIRED,
        ),
        FeedbackRouteAction.REVIEW_UNKNOWN,
        ValidationState.REVIEW_REQUIRED,
        ValidationState.HIDDEN_EVALUATION,
        Stage2SmokeBudgetExpectation(0, 0, 0, 0),
        feedback_items=(_hidden_unknown,),
        secret_marker=_HIDDEN_UNKNOWN,
    ),
)
