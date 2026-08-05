"""Minimal unified runner shared by all AgRefactor++ execution modes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import os
import tempfile
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
from .execution_identity import (
    execution_identity_summary,
    finalize_execution_identity_bundle,
    validate_execution_identity_bundle,
    write_execution_identity_bundle,
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
    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    schema_version = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            _copy_json_mapping(
                "metadata",
                self.metadata,
            ),
        )

    @property
    def succeeded(self) -> bool:
        return self.status is RunStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        usage = self.budget_usage
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "mode": self.mode.value,
            "status": self.status.value,
            "succeeded": self.succeeded,
            "phases": [
                {
                    "phase": phase.phase.value,
                    "status": phase.status.value,
                    "summary": phase.summary,
                    "metadata": dict(
                        phase.metadata
                    ),
                }
                for phase in self.phases
            ],
            "budget_usage": (
                None
                if usage is None
                else usage.to_dict()
            ),
            "metadata": dict(self.metadata),
        }

    def write_artifacts(
        self,
        root: str | os.PathLike[str],
    ) -> "RunArtifactWriteResult":
        return RunArtifactWriter(root).write(self)


@dataclass(frozen=True, slots=True)
class RunArtifactFile:
    relative_path: str
    sha256: str
    size_bytes: int
    record_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_type": self.record_type,
        }


@dataclass(frozen=True, slots=True)
class RunArtifactWriteResult:
    root: str
    run_result_path: str
    artifact_manifest_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "run_result_path": (
                self.run_result_path
            ),
            "artifact_manifest_path": (
                self.artifact_manifest_path
            ),
        }


class RunArtifactWriter:
    "Write the complete safe run bundle with the manifest last."

    schema_version = 1

    def __init__(
        self,
        root: str | os.PathLike[str],
    ) -> None:
        self._root = _clean_artifact_path(
            root,
            "root",
        )

    @property
    def root(self) -> Path:
        return self._root

    def write(
        self,
        result: RunResult,
    ) -> RunArtifactWriteResult:
        if not isinstance(result, RunResult):
            raise TypeError(
                "result must be a RunResult"
            )
        self._root.mkdir(
            parents=True,
            exist_ok=True,
        )
        result_path = (
            self._root / "run_result.json"
        )
        manifest_path = (
            self._root
            / "run_artifact_manifest.json"
        )
        for path in (
            result_path,
            manifest_path,
        ):
            if path.exists():
                raise FileExistsError(
                    "run artifact already exists: "
                    f"{path}"
                )

        _atomic_json_write(
            result_path,
            result.to_dict(),
        )

        files: list[RunArtifactFile] = []
        for path in sorted(
            self._root.rglob("*")
        ):
            if not path.is_file():
                continue
            if path == manifest_path:
                continue
            if path.is_symlink():
                raise ValueError(
                    "run artifacts must not "
                    "contain symbolic links"
                )
            data = path.read_bytes()
            relative = path.relative_to(
                self._root
            ).as_posix()
            files.append(
                RunArtifactFile(
                    relative_path=relative,
                    sha256=sha256(
                        data
                    ).hexdigest(),
                    size_bytes=len(data),
                    record_type=(
                        _run_record_type(
                            relative
                        )
                    ),
                )
            )

        execution_mode = result.metadata.get(
            "execution_mode"
        )
        legacy_mode = bool(
            result.metadata.get(
                "legacy_mode",
                False,
            )
        )
        _atomic_json_write(
            manifest_path,
            {
                "schema_version": (
                    self.schema_version
                ),
                "run_id": result.run_id,
                "task_id": result.task_id,
                "status": result.status.value,
                "execution_mode": (
                    execution_mode
                ),
                "legacy_mode": legacy_mode,
                "evidence_view": "agent_safe",
                **(
                    {
                        "execution_identity": result.metadata[
                            "execution_identity"
                        ]
                    }
                    if "execution_identity" in result.metadata
                    else {}
                ),
                "files": [
                    item.to_dict()
                    for item in files
                ],
            },
        )
        if tuple(
            self._root.rglob("*.tmp")
        ):
            raise RuntimeError(
                "temporary run artifact "
                "files remain"
            )

        return RunArtifactWriteResult(
            root=str(self._root),
            run_result_path=str(result_path),
            artifact_manifest_path=str(
                manifest_path
            ),
        )


PhaseHandler = Callable[[RunContext], PhaseResult]


def _finalize_execution_identity_artifact(
    artifact_root: Path | None,
    *,
    budget_usage: BudgetUsage | None,
    execution_status: str,
    hard_budget_exhaustion: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if artifact_root is None:
        return None
    path = artifact_root / "execution_identity.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TypeError("execution_identity.json must contain an object")
    finalized = finalize_execution_identity_bundle(
        raw,
        budget_usage=(
            None if budget_usage is None else budget_usage.to_dict()
        ),
        execution_status=execution_status,
        hard_budget_exhaustion=hard_budget_exhaustion,
    )
    validate_execution_identity_bundle(finalized)
    write_execution_identity_bundle(path, finalized)
    return execution_identity_summary(finalized)


class UnifiedRunner:
    """Dispatch refactor, optimize, and full runs through shared services."""

    def __init__(
        self,
        handlers: Mapping[RunPhase | str, PhaseHandler],
        *,
        budget_limits: BudgetLimits | None = None,
        phase_reserves: Mapping[RunPhase | str, BudgetLimits] | None = None,
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

        normalized_reserves: dict[RunPhase, BudgetLimits] = {}
        for raw_phase, reserve in (phase_reserves or {}).items():
            phase = (
                raw_phase
                if isinstance(raw_phase, RunPhase)
                else RunPhase(raw_phase)
            )
            if not isinstance(reserve, BudgetLimits):
                raise TypeError("phase reserve must be BudgetLimits")
            normalized_reserves[phase] = reserve

        self._handlers = normalized
        self._budget_limits = budget_limits or BudgetLimits()
        self._phase_reserves = normalized_reserves

    def run(
        self,
        task: TaskSpec,
        *,
        run_id: str | None = None,
        trace_path: str | Path | None = None,
        artifact_root: (
            str | os.PathLike[str] | None
        ) = None,
        run_metadata: (
            Mapping[str, Any] | None
        ) = None,
    ) -> RunResult:
        """Execute the phases selected by ``task.mode`` in fail-stop order."""

        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec")

        resolved_run_id = (
            _clean_required("run_id", run_id)
            if run_id is not None
            else uuid4().hex
        )
        metadata = _copy_json_mapping(
            "run_metadata",
            run_metadata or {},
        )
        resolved_artifact_root = (
            None
            if artifact_root is None
            else _prepare_artifact_root(
                artifact_root
            )
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
            metadata={
                "mode": task.mode.value,
                "run_metadata": metadata,
            },
        )

        phase_results: list[PhaseResult] = []
        run_status = RunStatus.SUCCEEDED

        reserve_active = False
        for phase in self._phases_for_mode(task.mode):
            reserve = self._phase_reserves.get(phase)
            if reserve is not None:
                if reserve_active:
                    budget.set_active_reserve(None)
                    trace.record(
                        "budget.phase_reserve.released",
                        phase=phase.value,
                        status="recorded",
                        metadata={
                            "reserve": budget.active_reserve_dict(),
                        },
                    )
                    reserve_active = False
                try:
                    budget.set_active_reserve(reserve)
                except BudgetExceededError as exc:
                    result = PhaseResult(
                        phase=phase,
                        status=PhaseStatus.ERROR,
                        summary=str(exc),
                        metadata={
                            "resource": exc.resource,
                            "phase_reserve_activation": True,
                        },
                    )
                    phase_results.append(result)
                    run_status = RunStatus.ERROR
                    trace.record(
                        "budget.phase_reserve.rejected",
                        phase=phase.value,
                        status="error",
                        message=str(exc),
                        metadata={"resource": exc.resource},
                    )
                    break
                reserve_active = True
                trace.record(
                    "budget.phase_reserve.activated",
                    phase=phase.value,
                    status="running",
                    metadata={
                        "reserve": budget.active_reserve_dict(),
                    },
                )
            elif reserve_active:
                budget.set_active_reserve(None)
                trace.record(
                    "budget.phase_reserve.released",
                    phase=phase.value,
                    status="recorded",
                    metadata={
                        "reserve": budget.active_reserve_dict(),
                    },
                )
                reserve_active = False
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

        if reserve_active:
            budget.set_active_reserve(None)
            trace.record(
                "budget.phase_reserve.released",
                status="recorded",
                metadata={"reserve": budget.active_reserve_dict()},
            )

        hard_budget_exhaustion: dict[str, Any] | None = None
        if phase_results:
            last_metadata = phase_results[-1].metadata
            resource = last_metadata.get("resource")
            if resource is not None:
                hard_budget_exhaustion = {
                    "resource": resource,
                    "stage": phase_results[-1].phase.value,
                }

        budget_usage: BudgetUsage | None
        try:
            budget_usage = budget.snapshot()
        except BudgetExceededError as exc:
            budget_usage = None
            run_status = RunStatus.ERROR
            hard_budget_exhaustion = {
                "resource": exc.resource,
                "stage": "run_finalize",
                "limit": exc.limit,
                "attempted": exc.attempted,
            }
            trace.record(
                "budget.exceeded",
                status="error",
                message=str(exc),
                metadata={"resource": exc.resource},
            )

        identity_summary = _finalize_execution_identity_artifact(
            resolved_artifact_root,
            budget_usage=budget_usage,
            execution_status=run_status.value,
            hard_budget_exhaustion=hard_budget_exhaustion,
        )
        if identity_summary is not None:
            metadata["execution_identity"] = identity_summary

        trace.record(
            "run.finished",
            status=run_status.value,
            metadata={
                "completed_phases": [
                    result.phase.value for result in phase_results
                ]
            },
        )

        result = RunResult(
            run_id=resolved_run_id,
            task_id=task.task_id,
            mode=task.mode,
            status=run_status,
            phases=tuple(phase_results),
            budget_usage=budget_usage,
            metadata=metadata,
        )
        if resolved_artifact_root is not None:
            trace.record(
                "run.artifacts.preparing",
                status="running",
                metadata={
                    "artifact_manifest": (
                        "run_artifact_manifest.json"
                    )
                },
            )
            result.write_artifacts(
                resolved_artifact_root
            )
        return result

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


def _prepare_artifact_root(
    value: str | os.PathLike[str],
) -> Path:
    root = _clean_artifact_path(
        value,
        "artifact_root",
    )
    if root.exists():
        if not root.is_dir():
            raise FileExistsError(
                "artifact_root is not "
                f"a directory: {root}"
            )
        if any(root.iterdir()):
            raise FileExistsError(
                "artifact_root is not "
                f"empty: {root}"
            )
    root.mkdir(
        parents=True,
        exist_ok=True,
    )
    return root


def _clean_artifact_path(
    value: str | os.PathLike[str],
    name: str,
) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise TypeError(
            f"{name} must be path-like"
        ) from exc
    if not isinstance(raw, str):
        raise TypeError(
            f"{name} must resolve to a string"
        )
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError(
            f"{name} must not be empty"
        )
    return Path(cleaned).expanduser()


def _atomic_json_write(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    copied = _copy_json_mapping(
        "artifact payload",
        payload,
    )
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                copied,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(
                missing_ok=True
            )
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _run_record_type(
    relative: str,
) -> str:
    if relative == "run_result.json":
        return "run_result"
    if relative == "execution_identity.json":
        return "execution_identity_operator_full"
    if relative == "trace.jsonl":
        return "trace"
    if relative.endswith(
        "artifact_manifest.json"
    ):
        return "nested_artifact_manifest"
    if relative.endswith(".json"):
        return "supporting_json"
    return "supporting_artifact"


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
