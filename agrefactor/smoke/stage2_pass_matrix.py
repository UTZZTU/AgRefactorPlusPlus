"""Run the committed Stage 2 baseline corpus through the real validation chain."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any

from agrefactor.config import TargetProfile
from agrefactor.evaluation import ValidationState
from agrefactor.runtime.budget import (
    BudgetLimits,
    BudgetManager,
    BudgetUsage,
)
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
    Stage2SmokeCase,
    Stage2SmokeKernelType,
    load_stage2_smoke_cases,
)


_EXPECTED_PASS_STAGES = (
    ValidationState.PREFLIGHT,
    ValidationState.PUBLIC_EVALUATION,
    ValidationState.CSYNTH,
    ValidationState.PUBLIC_COSIM,
    ValidationState.HIDDEN_EVALUATION,
)


class Stage2SmokePassMatrixError(RuntimeError):
    """Raised when a baseline case cannot satisfy the pass-matrix contract."""

    def __init__(
        self,
        case_id: str,
        reason: str,
    ) -> None:
        self.case_id = _required_text(case_id, "case_id")
        self.reason = _required_text(reason, "reason")
        super().__init__(
            f"Stage 2 smoke pass matrix failed for "
            f"{self.case_id}: {self.reason}"
        )


@dataclass(frozen=True, slots=True)
class Stage2SmokePassCaseResult:
    """Safe operator record for one accepted baseline validation."""

    case_id: str
    kernel_type: Stage2SmokeKernelType
    validation_id: str
    validation_result: ValidationOrchestrationResult
    budget_before: BudgetUsage
    budget_after: BudgetUsage
    budget_delta: Stage2SmokeBudgetExpectation
    trace_jsonl_path: str
    trace_snapshot_path: str
    source_sha256: Mapping[str, str] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "case_id",
            _required_text(self.case_id, "case_id"),
        )
        object.__setattr__(
            self,
            "validation_id",
            _required_text(
                self.validation_id,
                "validation_id",
            ),
        )
        kernel_type = (
            self.kernel_type
            if isinstance(
                self.kernel_type,
                Stage2SmokeKernelType,
            )
            else Stage2SmokeKernelType(str(self.kernel_type))
        )
        if not isinstance(
            self.validation_result,
            ValidationOrchestrationResult,
        ):
            raise TypeError(
                "validation_result must be "
                "ValidationOrchestrationResult"
            )
        if not self.validation_result.accepted:
            raise ValueError(
                "Stage2SmokePassCaseResult requires "
                "an accepted validation"
            )
        if not isinstance(self.budget_before, BudgetUsage):
            raise TypeError(
                "budget_before must be BudgetUsage"
            )
        if not isinstance(self.budget_after, BudgetUsage):
            raise TypeError(
                "budget_after must be BudgetUsage"
            )
        if not isinstance(
            self.budget_delta,
            Stage2SmokeBudgetExpectation,
        ):
            raise TypeError(
                "budget_delta must be "
                "Stage2SmokeBudgetExpectation"
            )
        trace_jsonl = _required_text(
            self.trace_jsonl_path,
            "trace_jsonl_path",
        )
        trace_snapshot = _required_text(
            self.trace_snapshot_path,
            "trace_snapshot_path",
        )
        source_sha256 = _digest_mapping(
            self.source_sha256,
            "source_sha256",
        )

        object.__setattr__(
            self,
            "kernel_type",
            kernel_type,
        )
        object.__setattr__(
            self,
            "trace_jsonl_path",
            trace_jsonl,
        )
        object.__setattr__(
            self,
            "trace_snapshot_path",
            trace_snapshot,
        )
        object.__setattr__(
            self,
            "source_sha256",
            source_sha256,
        )

    @property
    def completed_stages(
        self,
    ) -> tuple[ValidationState, ...]:
        return tuple(
            step.state
            for step in self.validation_result.steps
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "kernel_type": self.kernel_type.value,
            "validation_id": self.validation_id,
            "accepted": True,
            "completed_stages": [
                state.value
                for state in self.completed_stages
            ],
            "validation_result": (
                self.validation_result.to_dict()
            ),
            "budget_before": self.budget_before.to_dict(),
            "budget_after": self.budget_after.to_dict(),
            "budget_delta": self.budget_delta.to_dict(),
            "trace_jsonl_path": self.trace_jsonl_path,
            "trace_snapshot_path": (
                self.trace_snapshot_path
            ),
            "source_sha256": dict(self.source_sha256),
        }


@dataclass(frozen=True, slots=True)
class Stage2SmokePassMatrixResult:
    """Serializable result of an entirely accepted baseline matrix."""

    matrix_id: str
    case_results: tuple[Stage2SmokePassCaseResult, ...]
    expected_total_budget: Stage2SmokeBudgetExpectation
    total_usage: BudgetUsage
    work_root: str

    schema_version = 1

    def __post_init__(self) -> None:
        matrix_id = _required_text(
            self.matrix_id,
            "matrix_id",
        )
        cases = tuple(self.case_results)
        if not cases:
            raise ValueError(
                "case_results must not be empty"
            )
        if not all(
            isinstance(
                item,
                Stage2SmokePassCaseResult,
            )
            for item in cases
        ):
            raise TypeError(
                "case_results must contain "
                "Stage2SmokePassCaseResult"
            )
        case_ids = tuple(
            item.case_id for item in cases
        )
        if len(case_ids) != len(set(case_ids)):
            raise ValueError(
                "case_results must use unique case IDs"
            )
        if not isinstance(
            self.expected_total_budget,
            Stage2SmokeBudgetExpectation,
        ):
            raise TypeError(
                "expected_total_budget must be "
                "Stage2SmokeBudgetExpectation"
            )
        if not isinstance(self.total_usage, BudgetUsage):
            raise TypeError(
                "total_usage must be BudgetUsage"
            )
        work_root = _required_text(
            self.work_root,
            "work_root",
        )
        if (
            _usage_expectation(self.total_usage)
            != self.expected_total_budget
        ):
            raise ValueError(
                "total_usage does not match "
                "expected_total_budget"
            )

        object.__setattr__(self, "matrix_id", matrix_id)
        object.__setattr__(
            self,
            "case_results",
            cases,
        )
        object.__setattr__(
            self,
            "work_root",
            work_root,
        )

    @property
    def accepted(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "matrix_id": self.matrix_id,
            "accepted": True,
            "case_count": len(self.case_results),
            "case_results": [
                item.to_dict()
                for item in self.case_results
            ],
            "expected_total_budget": (
                self.expected_total_budget.to_dict()
            ),
            "total_usage": self.total_usage.to_dict(),
            "work_root": self.work_root,
        }

    def write_json(
        self,
        path: str | os.PathLike[str],
    ) -> Path:
        destination = Path(path)
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        destination.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return destination


class Stage2SmokePassMatrixRunner:
    """Validate baseline cases with one exact shared physical budget."""

    def __init__(
        self,
        work_root: str | os.PathLike[str],
        *,
        handler_factory: (
            CandidateValidationHandlerFactory | None
        ) = None,
        csynth_timelimit: int = 600,
        csim_timelimit: int = 90,
    ) -> None:
        try:
            raw_root = os.fspath(work_root)
        except TypeError as exc:
            raise TypeError(
                "work_root must be path-like"
            ) from exc
        if not isinstance(raw_root, str):
            raise TypeError(
                "work_root must resolve to a string"
            )
        if not raw_root.strip():
            raise ValueError(
                "work_root must not be empty"
            )
        for name, value in (
            ("csynth_timelimit", csynth_timelimit),
            ("csim_timelimit", csim_timelimit),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                int,
            ):
                raise TypeError(
                    f"{name} must be an integer"
                )
            if value <= 0:
                raise ValueError(
                    f"{name} must be positive"
                )

        root = Path(raw_root).expanduser()
        factory = (
            handler_factory
            if handler_factory is not None
            else LocalCandidateValidationHandlerFactory(
                root / "validation",
                csynth_timelimit=csynth_timelimit,
                csim_timelimit=csim_timelimit,
            )
        )
        if not callable(getattr(factory, "build", None)):
            raise TypeError(
                "handler_factory must provide build(request)"
            )

        self._work_root = root
        self._handler_factory = factory

    @property
    def work_root(self) -> Path:
        return self._work_root

    def run(
        self,
        cases: Iterable[Stage2SmokeCase] | None = None,
        *,
        matrix_id: str = "stage2-smoke-pass-matrix",
        target: TargetProfile | None = None,
    ) -> Stage2SmokePassMatrixResult:
        normalized_id = _required_text(
            matrix_id,
            "matrix_id",
        )
        normalized_cases = _normalize_cases(cases)
        if target is not None and not isinstance(
            target,
            TargetProfile,
        ):
            raise TypeError(
                "target must be TargetProfile or None"
            )

        if self._work_root.exists() and any(
            self._work_root.iterdir()
        ):
            raise FileExistsError(
                "Stage 2 smoke pass-matrix work root "
                f"is not empty: {self._work_root}"
            )
        self._work_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        expected_total = (
            expected_stage2_smoke_pass_budget(
                normalized_cases
            )
        )
        budget = BudgetManager(
            BudgetLimits(
                max_llm_calls=(
                    expected_total.llm_calls
                ),
                max_tool_calls=(
                    expected_total.tool_calls
                ),
                max_compile_calls=(
                    expected_total.compile_calls
                ),
                max_csim_calls=(
                    expected_total.csim_calls
                ),
                max_csynth_calls=(
                    expected_total.csynth_calls
                ),
                max_cosim_calls=(
                    expected_total.cosim_calls
                ),
                max_tokens=expected_total.tokens,
                max_cost_usd=(
                    expected_total.cost_usd
                ),
            )
        )

        results: list[
            Stage2SmokePassCaseResult
        ] = []

        for case in normalized_cases:
            before = budget.snapshot()
            validation_id = (
                f"{normalized_id}.{case.case_id}"
            )
            run_id = (
                f"{normalized_id}.{case.case_id}"
            )
            trace_jsonl = (
                self._work_root
                / "traces"
                / f"{case.case_id}.jsonl"
            )
            trace_snapshot = (
                self._work_root
                / "traces"
                / f"{case.case_id}.json"
            )
            trace = TraceRecorder(
                run_id,
                task_id=case.task_id,
                output_path=trace_jsonl,
            )
            task = case.build_task(target=target)
            plan = CandidateValidationPlanRequest(
                task=task,
                candidate_code=case.candidate_code,
                original_code=case.original_code,
                preflight_testbench_code=(
                    case.preflight_testbench_code
                ),
                suite_testbench_codes=(
                    case.suite_testbench_codes
                ),
                attempt=0,
                validation_id=validation_id,
            )
            handlers = self._handler_factory.build(plan)
            context = RunContext(
                run_id=run_id,
                task=task,
                budget=budget,
                trace=trace,
            )
            outcome = ValidationOrchestrator(
                handlers
            ).run_detailed(
                context,
                validation_id=validation_id,
            )
            after = budget.snapshot()
            delta = _usage_delta(before, after)

            if not outcome.result.accepted:
                raise Stage2SmokePassMatrixError(
                    case.case_id,
                    "baseline validation did not reach accepted",
                )
            stages = tuple(
                step.state
                for step in outcome.result.steps
            )
            if stages != _EXPECTED_PASS_STAGES:
                raise Stage2SmokePassMatrixError(
                    case.case_id,
                    "unexpected validation stage sequence: "
                    + ", ".join(
                        state.value for state in stages
                    ),
                )
            if delta != case.expected_budget:
                raise Stage2SmokePassMatrixError(
                    case.case_id,
                    "physical budget delta did not match "
                    f"{case.expected_budget.to_dict()}: "
                    f"{delta.to_dict()}",
                )

            trace.write_json(trace_snapshot)
            safe_text = json.dumps(
                {
                    "validation_result": (
                        outcome.result.to_dict()
                    ),
                    "trace": trace.to_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for forbidden in (
                case.hidden_secret_marker,
                case.hidden_testbench_code,
                "ground_truth",
            ):
                if forbidden in safe_text:
                    raise Stage2SmokePassMatrixError(
                        case.case_id,
                        "safe result or trace contains "
                        "operator-only information",
                    )

            source_sha256 = case.agent_safe_manifest()[
                "source_sha256"
            ]
            results.append(
                Stage2SmokePassCaseResult(
                    case_id=case.case_id,
                    kernel_type=case.kernel_type,
                    validation_id=validation_id,
                    validation_result=outcome.result,
                    budget_before=before,
                    budget_after=after,
                    budget_delta=delta,
                    trace_jsonl_path=str(trace_jsonl),
                    trace_snapshot_path=(
                        str(trace_snapshot)
                    ),
                    source_sha256=source_sha256,
                )
            )

        total_usage = budget.snapshot()
        return Stage2SmokePassMatrixResult(
            matrix_id=normalized_id,
            case_results=tuple(results),
            expected_total_budget=expected_total,
            total_usage=total_usage,
            work_root=str(self._work_root),
        )


def expected_stage2_smoke_pass_budget(
    cases: Iterable[Stage2SmokeCase] | None = None,
) -> Stage2SmokeBudgetExpectation:
    """Sum exact expected physical usage for the supplied baseline cases."""

    normalized = _normalize_cases(cases)
    return Stage2SmokeBudgetExpectation(
        tool_calls=sum(
            case.expected_budget.tool_calls
            for case in normalized
        ),
        compile_calls=sum(
            case.expected_budget.compile_calls
            for case in normalized
        ),
        csynth_calls=sum(
            case.expected_budget.csynth_calls
            for case in normalized
        ),
        csim_calls=sum(
            case.expected_budget.csim_calls
            for case in normalized
        ),
        cosim_calls=sum(
            case.expected_budget.cosim_calls
            for case in normalized
        ),
        llm_calls=sum(
            case.expected_budget.llm_calls
            for case in normalized
        ),
        tokens=sum(
            case.expected_budget.tokens
            for case in normalized
        ),
        cost_usd=sum(
            case.expected_budget.cost_usd
            for case in normalized
        ),
    )


def _normalize_cases(
    cases: Iterable[Stage2SmokeCase] | None,
) -> tuple[Stage2SmokeCase, ...]:
    if cases is None:
        normalized = load_stage2_smoke_cases()
    else:
        if isinstance(
            cases,
            (str, bytes, Mapping),
        ):
            raise TypeError(
                "cases must be an iterable of "
                "Stage2SmokeCase values"
            )
        try:
            normalized = tuple(cases)
        except TypeError as exc:
            raise TypeError(
                "cases must be iterable"
            ) from exc
    if not normalized:
        raise ValueError(
            "cases must not be empty"
        )
    if not all(
        isinstance(case, Stage2SmokeCase)
        for case in normalized
    ):
        raise TypeError(
            "cases must contain Stage2SmokeCase values"
        )
    case_ids = tuple(
        case.case_id for case in normalized
    )
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(
            "cases must use unique case IDs"
        )
    return normalized


def _usage_delta(
    before: BudgetUsage,
    after: BudgetUsage,
) -> Stage2SmokeBudgetExpectation:
    if not isinstance(before, BudgetUsage):
        raise TypeError(
            "before must be BudgetUsage"
        )
    if not isinstance(after, BudgetUsage):
        raise TypeError(
            "after must be BudgetUsage"
        )

    values = {
        "tool_calls": (
            after.tool_calls - before.tool_calls
        ),
        "compile_calls": (
            after.compile_calls - before.compile_calls
        ),
        "csynth_calls": (
            after.csynth_calls - before.csynth_calls
        ),
        "csim_calls": (
            after.csim_calls - before.csim_calls
        ),
        "cosim_calls": (
            after.cosim_calls - before.cosim_calls
        ),
        "llm_calls": (
            after.llm_calls - before.llm_calls
        ),
        "tokens": after.tokens - before.tokens,
        "cost_usd": (
            after.cost_usd - before.cost_usd
        ),
    }
    if any(
        value < 0 for value in values.values()
    ):
        raise ValueError(
            "budget usage must be monotonic"
        )
    return Stage2SmokeBudgetExpectation(**values)


def _usage_expectation(
    usage: BudgetUsage,
) -> Stage2SmokeBudgetExpectation:
    if not isinstance(usage, BudgetUsage):
        raise TypeError(
            "usage must be BudgetUsage"
        )
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


def _digest_mapping(
    value: Mapping[str, str],
    field_name: str,
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(
            f"{field_name} must be a mapping"
        )
    result: dict[str, str] = {}
    for raw_key, raw_digest in value.items():
        key = _required_text(
            raw_key,
            field_name,
        )
        digest = _required_text(
            raw_digest,
            field_name,
        )
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            digest,
        ):
            raise ValueError(
                f"{field_name} contains invalid SHA-256"
            )
        result[key] = digest
    return result


def _required_text(
    value: str,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string"
        )
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(
            f"{field_name} must not be empty"
        )
    return cleaned
