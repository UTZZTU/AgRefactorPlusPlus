"""Generic evaluation interfaces shared by AgRefactor++ flows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any

from agrefactor.config import TargetProfile


class EvaluationStatus(str, Enum):
    """Normalized outcome of an evaluator invocation."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """Describe one tool-backed evaluation operation."""

    task_id: str
    kernel_path: str
    kernel_name: str
    target: TargetProfile
    work_dir: str
    testbench_path: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = self._clean_required("task_id", self.task_id)
        kernel_path = self._clean_required("kernel_path", self.kernel_path)
        kernel_name = self._clean_required("kernel_name", self.kernel_name)
        work_dir = self._clean_required("work_dir", self.work_dir)
        testbench_path = self._clean_optional(self.testbench_path)

        if not isinstance(self.target, TargetProfile):
            raise TypeError("EvaluationRequest.target must be a TargetProfile")

        options = self._copy_json_mapping("options", self.options)

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "kernel_path", kernel_path)
        object.__setattr__(self, "kernel_name", kernel_name)
        object.__setattr__(self, "work_dir", work_dir)
        object.__setattr__(self, "testbench_path", testbench_path)
        object.__setattr__(self, "options", options)

    @staticmethod
    def _clean_required(name: str, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{name} must not be empty")
        return cleaned

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("testbench_path must be a string or None")
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _copy_json_mapping(
        name: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        import json

        copied = dict(value)
        try:
            serialized = json.dumps(copied, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be JSON-serializable") from exc
        return json.loads(serialized)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Normalized result returned by any evaluator adapter."""

    evaluator: str
    status: EvaluationStatus
    summary: str | None = None
    return_code: int | None = None
    artifacts: tuple[str, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        evaluator = EvaluationRequest._clean_required("evaluator", self.evaluator)
        summary = EvaluationRequest._clean_optional(self.summary)

        status = self.status
        if not isinstance(status, EvaluationStatus):
            try:
                status = EvaluationStatus(str(status))
            except ValueError as exc:
                choices = ", ".join(item.value for item in EvaluationStatus)
                raise ValueError(
                    f"Unsupported evaluation status {self.status!r}; "
                    f"expected one of: {choices}"
                ) from exc

        if self.return_code is not None:
            if isinstance(self.return_code, bool) or not isinstance(
                self.return_code, int
            ):
                raise TypeError("return_code must be an integer or None")

        artifacts = tuple(
            EvaluationRequest._clean_required("artifact", path)
            for path in self.artifacts
        )
        diagnostics = tuple(
            EvaluationRequest._clean_required("diagnostic", item)
            for item in self.diagnostics
        )

        metrics = dict(self.metrics)
        for name, value in metrics.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("metric names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("metric values must be numeric")
            if not isfinite(float(value)):
                raise ValueError("metric values must be finite")
        metrics = {name.strip(): float(value) for name, value in metrics.items()}

        metadata = EvaluationRequest._copy_json_mapping(
            "metadata",
            self.metadata,
        )

        object.__setattr__(self, "evaluator", evaluator)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "metadata", metadata)

    @property
    def succeeded(self) -> bool:
        """Return whether the evaluator reported a passing result."""

        return self.status is EvaluationStatus.PASSED


class Evaluator(ABC):
    """Abstract interface implemented by concrete toolchain adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a stable evaluator identifier."""

    @abstractmethod
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """Evaluate one request and return a normalized result."""
