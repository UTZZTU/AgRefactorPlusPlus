"""Structured test results with split-aware agent redaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any

from agrefactor.config import (
    EvaluationSplit,
    TestSourceProvenance,
    TestSuiteSpec,
)


class TestEvaluationStatus(str, Enum):
    """Normalized outcome of one test-suite evaluation."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


_ALLOWED_EVIDENCE_FIELDS = frozenset(
    {
        "suite",
        "status",
        "passed_cases",
        "failed_cases",
        "evaluated_cases",
        "timed_out",
        "return_code",
        "summary",
        "details",
        "artifacts",
        "redacted",
        "source_provenance",
    }
)


@dataclass(frozen=True, slots=True)
class TestEvaluationEvidence:
    """Store full evaluator evidence and derive an agent-safe view."""

    suite: TestSuiteSpec
    status: TestEvaluationStatus
    passed_cases: int = 0
    failed_cases: int = 0
    timed_out: bool = False
    return_code: int | None = None
    summary: str = "Test evaluation completed"
    details: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    source_provenance: TestSourceProvenance | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.suite, TestSuiteSpec):
            raise TypeError(
                "TestEvaluationEvidence.suite must be a TestSuiteSpec"
            )

        status = self.status
        if not isinstance(status, TestEvaluationStatus):
            try:
                status = TestEvaluationStatus(str(status))
            except ValueError as exc:
                choices = ", ".join(
                    item.value for item in TestEvaluationStatus
                )
                raise ValueError(
                    f"Unsupported test evaluation status "
                    f"{self.status!r}; expected one of: {choices}"
                ) from exc

        passed_cases = self._validate_count(
            self.passed_cases,
            "passed_cases",
        )
        failed_cases = self._validate_count(
            self.failed_cases,
            "failed_cases",
        )
        evaluated_cases = passed_cases + failed_cases

        if (
            self.suite.case_count is not None
            and evaluated_cases > self.suite.case_count
        ):
            raise ValueError(
                "evaluated cases must not exceed suite.case_count"
            )

        if not isinstance(self.timed_out, bool):
            raise TypeError("timed_out must be a boolean")

        return_code = self.return_code
        if (
            return_code is not None
            and (
                isinstance(return_code, bool)
                or not isinstance(return_code, int)
            )
        ):
            raise TypeError("return_code must be an integer or null")

        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string")
        summary = self.summary.strip()
        if not summary:
            raise ValueError("summary must not be empty")

        details = self._normalize_details(self.details)
        artifacts = self._normalize_artifacts(self.artifacts)

        provenance = self.source_provenance
        if provenance is not None and not isinstance(
            provenance,
            TestSourceProvenance,
        ):
            if isinstance(provenance, Mapping):
                provenance = TestSourceProvenance.from_dict(
                    provenance
                )
            else:
                raise TypeError(
                    "source_provenance must be a "
                    "TestSourceProvenance, mapping or null"
                )

        object.__setattr__(self, "status", status)
        object.__setattr__(self, "passed_cases", passed_cases)
        object.__setattr__(self, "failed_cases", failed_cases)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "details", details)
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(
            self,
            "source_provenance",
            provenance,
        )

    @property
    def evaluated_cases(self) -> int:
        """Return the number of cases with pass/fail outcomes."""

        return self.passed_cases + self.failed_cases

    @property
    def feedback_visible_to_agent(self) -> bool:
        """Return whether detailed evidence may enter an agent prompt."""

        return self.suite.feedback_visible_to_agent

    @staticmethod
    def _validate_count(value: int, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} must be an integer")
        if value < 0:
            raise ValueError(f"{field_name} must not be negative")
        return value

    @staticmethod
    def _normalize_details(
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError("details must be a mapping")
        try:
            encoded = json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
            )
            normalized = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "details must be finite JSON-serializable data"
            ) from exc
        if not isinstance(normalized, dict):
            raise TypeError("details must normalize to an object")
        return normalized

    @staticmethod
    def _normalize_artifacts(
        value: Sequence[str],
    ) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(
            value,
            Sequence,
        ):
            raise TypeError("artifacts must be a sequence of strings")
        normalized = []
        for artifact in value:
            if not isinstance(artifact, str):
                raise TypeError(
                    "artifact entries must be strings"
                )
            cleaned = artifact.strip()
            if not cleaned:
                raise ValueError(
                    "artifact entries must not be empty"
                )
            normalized.append(cleaned)
        return tuple(normalized)

    def to_dict(self) -> dict[str, Any]:
        """Return complete operator/evaluator evidence."""

        payload = {
            "suite": self.suite.to_dict(),
            "status": self.status.value,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "evaluated_cases": self.evaluated_cases,
            "timed_out": self.timed_out,
            "return_code": self.return_code,
            "summary": self.summary,
            "details": dict(self.details),
            "artifacts": list(self.artifacts),
            "redacted": False,
        }
        if self.source_provenance is not None:
            payload["source_provenance"] = (
                self.source_provenance.to_dict()
            )
        return payload

    def to_agent_dict(self) -> dict[str, Any]:
        """Return evidence safe to include in an agent prompt."""

        if self.feedback_visible_to_agent:
            return self.to_dict()

        payload = {
            "suite": {
                "suite_id": self.suite.suite_id,
                "suite_version": self.suite.suite_version,
                "split": EvaluationSplit.HIDDEN.value,
                "case_count": self.suite.case_count,
                "feedback_visible_to_agent": False,
            },
            "status": self.status.value,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "evaluated_cases": self.evaluated_cases,
            "timed_out": self.timed_out,
            "return_code": self.return_code,
            "summary": f"Hidden evaluation {self.status.value}.",
            "details": {},
            "artifacts": [],
            "redacted": True,
        }
        if self.source_provenance is not None:
            payload["source_provenance"] = (
                self.source_provenance.to_hidden_agent_dict()
            )
        return payload

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "TestEvaluationEvidence":
        """Restore complete, non-redacted evaluator evidence."""

        if not isinstance(data, Mapping):
            raise TypeError("test evaluation evidence must be a mapping")

        unknown_fields = set(data) - _ALLOWED_EVIDENCE_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(
                f"Unknown test evaluation evidence fields: {names}"
            )

        if data.get("redacted", False):
            raise ValueError(
                "redacted agent evidence cannot restore full evidence"
            )

        suite_data = data["suite"]
        suite = (
            suite_data
            if isinstance(suite_data, TestSuiteSpec)
            else TestSuiteSpec.from_dict(suite_data)
        )

        provenance_data = data.get("source_provenance")
        provenance = (
            None
            if provenance_data is None
            else (
                provenance_data
                if isinstance(
                    provenance_data,
                    TestSourceProvenance,
                )
                else TestSourceProvenance.from_dict(
                    provenance_data
                )
            )
        )

        evidence = cls(
            suite=suite,
            status=data["status"],
            passed_cases=data.get("passed_cases", 0),
            failed_cases=data.get("failed_cases", 0),
            timed_out=data.get("timed_out", False),
            return_code=data.get("return_code"),
            summary=data.get(
                "summary",
                "Test evaluation completed",
            ),
            details=data.get("details", {}),
            artifacts=tuple(data.get("artifacts", ())),
            source_provenance=provenance,
        )

        if "evaluated_cases" in data:
            declared = data["evaluated_cases"]
            if declared != evidence.evaluated_cases:
                raise ValueError(
                    "evaluated_cases conflicts with pass/fail counts"
                )

        return evidence
