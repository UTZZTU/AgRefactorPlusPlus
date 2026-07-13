"""Minimal unified runner shared by all AgRefactor++ execution modes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from agrefactor.config import RunMode, TaskSpec

from .budget import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    BudgetUsage,
)
from .trace import TraceRecorder


class RunPhase(str, Enum):
    """Logical phases orchestrated by the unified runner."""

    REFACTOR = "refactor"
    OPTIMIZE = "optimize"


class PhaseStatus(str, Enum):
    """Normalized outcome of one runner phase."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ERROR = "error"


class RunStatus(str, Enum):
    """Normalized outcome of a complete run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PhaseResult:
    """Result returned by a refactoring or optimization phase handler."""

    phase: RunPhase
    status: PhaseStatus
    summary: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        phase = self.phase
        if not isinstance(phase, RunPhase):
            phase = RunPhase(phase)

        status = self.status
        if not isinstance(status, PhaseStatus):
            status = PhaseStatus(status)

        summary = _clean_optional(self.summary)
        metadata = _copy_json_mapping("metadata", self.metadata)

        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "metadata", metadata)

    @property
    def succeeded(self) -> bool:
        return self.status is PhaseStatus.SUCCEEDED


@dataclass(frozen=True, slots=True)
class RunContext:
    """Shared services and task data supplied to every phase handler."""

    run_id: str
    task: TaskSpec
    budget: BudgetManager
    trace: TraceRecorder


@dataclass(frozen=True, slots=True)
class RunResult:
    """Normalized result returned by the unified runner."""

    run_id: str
    task_id: str
    mode: RunMode
    status: RunStatus
    phases: tuple[PhaseResult, ...]
    budget_usage: BudgetUsage | None

    @property
    def succeeded(self) -> bool:
        return self.status is RunStatus.SUCCEEDED


PhaseHandler = Callable[[RunContext], PhaseResult]


class UnifiedRunner:
    """Dispatch refactor, optimize, and full runs through shared services."""

    def __init__(
        self,
        handlers: Mapping[RunPhase | str, PhaseHandler],
        *,
        budget_limits: BudgetLimits | None = None,
    ) -> None:
        normalized: dict[RunPhase, PhaseHandler] = {}

        for raw_phase, handler in handlers.items():
            phase = (
                raw_phase
                if isinstance(raw_phase, RunPhase)
                else RunPhase(raw_phase)
            )
            if phase in normalized:
                raise ValueError(f"Duplicate phase handler: {phase.value}")
            if not callable(handler):
                raise TypeError(
                    f"Handler for phase {phase.value} must be callable"
                )
            normalized[phase] = handler

        self._handlers = normalized
        self._budget_limits = budget_limits or BudgetLimits()

    def run(
        self,
        task: TaskSpec,
        *,
        run_id: str | None = None,
        trace_path: str | Path | None = None,
    ) -> RunResult:
        """Execute the phases selected by ``task.mode`` in fail-stop order."""

        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec")

        resolved_run_id = (
            _clean_required("run_id", run_id)
            if run_id is not None
            else uuid4().hex
        )

        trace = TraceRecorder(
            resolved_run_id,
            task_id=task.task_id,
            output_path=trace_path,
        )
        budget = BudgetManager(self._budget_limits)
        context = RunContext(
            run_id=resolved_run_id,
            task=task,
            budget=budget,
            trace=trace,
        )

        trace.record(
            "run.started",
            status="running",
            metadata={"mode": task.mode.value},
        )

        phase_results: list[PhaseResult] = []
        run_status = RunStatus.SUCCEEDED

        for phase in self._phases_for_mode(task.mode):
            trace.record(
                "phase.started",
                phase=phase.value,
                status="running",
            )

            result = self._execute_phase(phase, context)
            phase_results.append(result)

            trace.record(
                "phase.finished",
                phase=phase.value,
                status=result.status.value,
                message=result.summary,
                metadata=result.metadata,
            )

            if not result.succeeded:
                run_status = (
                    RunStatus.FAILED
                    if result.status is PhaseStatus.FAILED
                    else RunStatus.ERROR
                )
                break

        budget_usage: BudgetUsage | None
        try:
            budget_usage = budget.snapshot()
        except BudgetExceededError as exc:
            budget_usage = None
            run_status = RunStatus.ERROR
            trace.record(
                "budget.exceeded",
                status="error",
                message=str(exc),
                metadata={"resource": exc.resource},
            )

        trace.record(
            "run.finished",
            status=run_status.value,
            metadata={
                "completed_phases": [
                    result.phase.value for result in phase_results
                ]
            },
        )

        return RunResult(
            run_id=resolved_run_id,
            task_id=task.task_id,
            mode=task.mode,
            status=run_status,
            phases=tuple(phase_results),
            budget_usage=budget_usage,
        )

    def _execute_phase(
        self,
        phase: RunPhase,
        context: RunContext,
    ) -> PhaseResult:
        handler = self._handlers.get(phase)

        if handler is None:
            return PhaseResult(
                phase=phase,
                status=PhaseStatus.ERROR,
                summary=f"No handler registered for phase: {phase.value}",
            )

        try:
            result = handler(context)
        except BudgetExceededError as exc:
            return PhaseResult(
                phase=phase,
                status=PhaseStatus.ERROR,
                summary=str(exc),
                metadata={"resource": exc.resource},
            )
        except Exception as exc:
            return PhaseResult(
                phase=phase,
                status=PhaseStatus.ERROR,
                summary=f"{type(exc).__name__}: {exc}",
            )

        if not isinstance(result, PhaseResult):
            return PhaseResult(
                phase=phase,
                status=PhaseStatus.ERROR,
                summary=(
                    f"Handler for {phase.value} returned "
                    f"{type(result).__name__}, expected PhaseResult"
                ),
            )

        if result.phase is not phase:
            return PhaseResult(
                phase=phase,
                status=PhaseStatus.ERROR,
                summary=(
                    f"Handler for {phase.value} returned result for "
                    f"{result.phase.value}"
                ),
            )

        return result

    @staticmethod
    def _phases_for_mode(mode: RunMode) -> tuple[RunPhase, ...]:
        if mode is RunMode.REFACTOR:
            return (RunPhase.REFACTOR,)
        if mode is RunMode.OPTIMIZE:
            return (RunPhase.OPTIMIZE,)
        if mode is RunMode.FULL:
            return (RunPhase.REFACTOR, RunPhase.OPTIMIZE)
        raise ValueError(f"Unsupported run mode: {mode}")


def _clean_required(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("summary must be a string or None")
    cleaned = value.strip()
    return cleaned or None


def _copy_json_mapping(
    name: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    copied = dict(value)
    try:
        serialized = json.dumps(copied, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be JSON-serializable") from exc
    return json.loads(serialized)
