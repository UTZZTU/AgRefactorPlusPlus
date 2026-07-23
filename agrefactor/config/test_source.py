"""Declared and resolved identities for evaluation test sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SPEC_FIELDS = frozenset(
    {
        "source_id", "source_revision", "source_kind",
        "expected_content_sha256", "operator_artifact_path",
        "generation_model", "generation_profile", "prompt_sha256",
        "trajectory_id", "round_index",
    }
)
_ALLOWED_PROVENANCE_FIELDS = frozenset(
    {
        "source_id", "source_revision", "source_kind",
        "content_sha256", "size_bytes", "resolved_path",
        "suite_id", "suite_version", "split",
        "operator_artifact_path", "generation_model",
        "generation_profile", "prompt_sha256", "trajectory_id",
        "round_index", "coverage", "qualification_status",
        "feedback_visibility", "redacted",
    }
)


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _optional(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or null")
    return value.strip() or None


def _sha256(value: object, field_name: str) -> str:
    cleaned = _required(value, field_name).lower()
    if _SHA256_RE.fullmatch(cleaned) is None:
        raise ValueError(
            f"{field_name} must be a 64-character SHA-256 digest"
        )
    return cleaned


def _json_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    try:
        result = json.loads(
            json.dumps(
                dict(value), ensure_ascii=False, allow_nan=False,
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain finite JSON data") from exc
    if not isinstance(result, dict):
        raise TypeError(f"{field_name} must normalize to an object")
    return result


class TestSourceKind(str, Enum):
    PROVIDED = "provided"
    GENERATED = "generated"
    DERIVED = "derived"
    CACHED = "cached"
    # Historical compatibility values; new product paths use the four above.
    FILESYSTEM = "filesystem"
    EXTERNAL = "external"

    @property
    def locally_resolvable(self) -> bool:
        return self is not TestSourceKind.EXTERNAL


class TestQualificationStatus(str, Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    ERROR = "error"


class TestFeedbackVisibility(str, Enum):
    PUBLIC = "public"
    AGENT_SAFE_SUMMARY = "agent_safe_summary"
    OPERATOR_ONLY = "operator_only"


@dataclass(frozen=True, slots=True)
class TestSourceSpec:
    source_id: str
    source_revision: str | None = None
    source_kind: TestSourceKind = TestSourceKind.FILESYSTEM
    expected_content_sha256: str | None = None
    operator_artifact_path: str | None = None
    generation_model: str | None = None
    generation_profile: str | None = None
    prompt_sha256: str | None = None
    trajectory_id: str | None = None
    round_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id", _required(self.source_id, "TestSourceSpec.source_id")
        )
        for name in (
            "source_revision", "operator_artifact_path", "generation_model",
            "generation_profile", "trajectory_id",
        ):
            object.__setattr__(
                self, name, _optional(getattr(self, name), f"TestSourceSpec.{name}")
            )
        kind = self.source_kind
        if not isinstance(kind, TestSourceKind):
            try:
                kind = TestSourceKind(str(kind))
            except ValueError as exc:
                choices = ", ".join(item.value for item in TestSourceKind)
                raise ValueError(
                    f"Unsupported test source kind {self.source_kind!r}; "
                    f"expected one of: {choices}"
                ) from exc
        object.__setattr__(self, "source_kind", kind)
        if self.expected_content_sha256 is not None:
            object.__setattr__(
                self, "expected_content_sha256",
                _sha256(
                    self.expected_content_sha256,
                    "TestSourceSpec.expected_content_sha256",
                ),
            )
        if self.prompt_sha256 is not None:
            object.__setattr__(
                self, "prompt_sha256",
                _sha256(self.prompt_sha256, "TestSourceSpec.prompt_sha256"),
            )
        if self.round_index is not None and (
            isinstance(self.round_index, bool)
            or not isinstance(self.round_index, int)
            or self.round_index < 0
        ):
            raise ValueError(
                "TestSourceSpec.round_index must be a non-negative integer or null"
            )
        if kind is TestSourceKind.GENERATED:
            required = {
                "operator_artifact_path": self.operator_artifact_path,
                "generation_model": self.generation_model,
                "generation_profile": self.generation_profile,
                "prompt_sha256": self.prompt_sha256,
                "trajectory_id": self.trajectory_id,
                "round_index": self.round_index,
            }
            missing = sorted(k for k, v in required.items() if v is None)
            if missing:
                raise ValueError(
                    "generated test source requires provenance fields: "
                    + ", ".join(missing)
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_kind": self.source_kind.value,
            "expected_content_sha256": self.expected_content_sha256,
            "operator_artifact_path": self.operator_artifact_path,
            "generation_model": self.generation_model,
            "generation_profile": self.generation_profile,
            "prompt_sha256": self.prompt_sha256,
            "trajectory_id": self.trajectory_id,
            "round_index": self.round_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TestSourceSpec":
        if not isinstance(data, Mapping):
            raise TypeError("test source spec must be a mapping")
        unknown = set(data) - _ALLOWED_SPEC_FIELDS
        if unknown:
            raise ValueError(
                "Unknown test source fields: " + ", ".join(sorted(unknown))
            )
        return cls(
            source_id=data["source_id"],
            source_revision=data.get("source_revision"),
            source_kind=data.get("source_kind", TestSourceKind.FILESYSTEM.value),
            expected_content_sha256=data.get("expected_content_sha256"),
            operator_artifact_path=data.get("operator_artifact_path"),
            generation_model=data.get("generation_model"),
            generation_profile=data.get("generation_profile"),
            prompt_sha256=data.get("prompt_sha256"),
            trajectory_id=data.get("trajectory_id"),
            round_index=data.get("round_index"),
        )


@dataclass(frozen=True, slots=True)
class TestSourceProvenance:
    source_id: str
    source_revision: str | None
    source_kind: TestSourceKind
    content_sha256: str
    size_bytes: int
    resolved_path: str
    suite_id: str | None = None
    suite_version: str | None = None
    split: str | None = None
    operator_artifact_path: str | None = None
    generation_model: str | None = None
    generation_profile: str | None = None
    prompt_sha256: str | None = None
    trajectory_id: str | None = None
    round_index: int | None = None
    coverage: Mapping[str, Any] = field(default_factory=dict)
    qualification_status: TestQualificationStatus = TestQualificationStatus.PENDING
    feedback_visibility: TestFeedbackVisibility = TestFeedbackVisibility.PUBLIC

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id",
            _required(self.source_id, "TestSourceProvenance.source_id"),
        )
        for name in (
            "source_revision", "suite_id", "suite_version", "split",
            "operator_artifact_path", "generation_model",
            "generation_profile", "trajectory_id",
        ):
            object.__setattr__(
                self, name,
                _optional(getattr(self, name), f"TestSourceProvenance.{name}"),
            )
        kind = self.source_kind
        if not isinstance(kind, TestSourceKind):
            kind = TestSourceKind(str(kind))
        object.__setattr__(self, "source_kind", kind)
        object.__setattr__(
            self, "content_sha256",
            _sha256(self.content_sha256, "TestSourceProvenance.content_sha256"),
        )
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative integer")
        resolved = _required(
            self.resolved_path, "TestSourceProvenance.resolved_path"
        )
        object.__setattr__(self, "resolved_path", resolved)
        if self.operator_artifact_path is None:
            object.__setattr__(self, "operator_artifact_path", resolved)
        if self.prompt_sha256 is not None:
            object.__setattr__(
                self, "prompt_sha256",
                _sha256(
                    self.prompt_sha256,
                    "TestSourceProvenance.prompt_sha256",
                ),
            )
        if self.round_index is not None and (
            isinstance(self.round_index, bool)
            or not isinstance(self.round_index, int)
            or self.round_index < 0
        ):
            raise ValueError("round_index must be non-negative or null")
        object.__setattr__(
            self, "coverage",
            _json_mapping(self.coverage, "TestSourceProvenance.coverage"),
        )
        status = self.qualification_status
        if not isinstance(status, TestQualificationStatus):
            status = TestQualificationStatus(str(status))
        visibility = self.feedback_visibility
        if not isinstance(visibility, TestFeedbackVisibility):
            visibility = TestFeedbackVisibility(str(visibility))
        object.__setattr__(self, "qualification_status", status)
        object.__setattr__(self, "feedback_visibility", visibility)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_kind": self.source_kind.value,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "resolved_path": self.resolved_path,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "split": self.split,
            "operator_artifact_path": self.operator_artifact_path,
            "generation_model": self.generation_model,
            "generation_profile": self.generation_profile,
            "prompt_sha256": self.prompt_sha256,
            "trajectory_id": self.trajectory_id,
            "round_index": self.round_index,
            "coverage": dict(self.coverage),
            "qualification_status": self.qualification_status.value,
            "feedback_visibility": self.feedback_visibility.value,
            "redacted": False,
        }

    def to_hidden_agent_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_kind": self.source_kind.value,
            "feedback_visibility": TestFeedbackVisibility.AGENT_SAFE_SUMMARY.value,
            "redacted": True,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TestSourceProvenance":
        if not isinstance(data, Mapping):
            raise TypeError("test source provenance must be a mapping")
        unknown = set(data) - _ALLOWED_PROVENANCE_FIELDS
        if unknown:
            raise ValueError(
                "Unknown test source provenance fields: "
                + ", ".join(sorted(unknown))
            )
        if data.get("redacted", False):
            raise ValueError(
                "redacted test source provenance cannot restore full provenance"
            )
        return cls(
            source_id=data["source_id"],
            source_revision=data.get("source_revision"),
            source_kind=data["source_kind"],
            content_sha256=data["content_sha256"],
            size_bytes=data["size_bytes"],
            resolved_path=data["resolved_path"],
            suite_id=data.get("suite_id"),
            suite_version=data.get("suite_version"),
            split=data.get("split"),
            operator_artifact_path=data.get("operator_artifact_path"),
            generation_model=data.get("generation_model"),
            generation_profile=data.get("generation_profile"),
            prompt_sha256=data.get("prompt_sha256"),
            trajectory_id=data.get("trajectory_id"),
            round_index=data.get("round_index"),
            coverage=data.get("coverage", {}),
            qualification_status=data.get(
                "qualification_status", TestQualificationStatus.PENDING.value
            ),
            feedback_visibility=data.get(
                "feedback_visibility", TestFeedbackVisibility.PUBLIC.value
            ),
        )


def resolve_test_source(
    source: TestSourceSpec,
    path: str | Path,
    *,
    execution_content: str | None = None,
    suite_id: str | None = None,
    suite_version: str | None = None,
    split: str | None = None,
    coverage: Mapping[str, Any] | None = None,
    qualification_status: TestQualificationStatus | str = (
        TestQualificationStatus.PENDING
    ),
    feedback_visibility: TestFeedbackVisibility | str = (
        TestFeedbackVisibility.PUBLIC
    ),
) -> TestSourceProvenance:
    if not isinstance(source, TestSourceSpec):
        raise TypeError("source must be a TestSourceSpec")
    if not source.source_kind.locally_resolvable:
        raise ValueError(
            "Only filesystem or materialized "
            "provided/generated/derived/cached test sources can be "
            "resolved by the local evaluator"
        )
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"test source does not exist: {candidate}"
        ) from exc
    if not resolved.is_file():
        raise ValueError(
            f"test source must resolve to a regular file: {resolved}"
        )
    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = source.expected_content_sha256
    if expected is not None and digest != expected:
        raise ValueError(
            f"test source digest mismatch for {source.source_id!r}: "
            f"observed {digest}, expected {expected}"
        )
    if execution_content is not None:
        if not isinstance(execution_content, str):
            raise TypeError("execution_content must be a string or null")
        execution_digest = hashlib.sha256(
            execution_content.encode("utf-8")
        ).hexdigest()
        if execution_digest != digest:
            raise ValueError(
                "context_variables['testbench'] does not match "
                f"the resolved source {source.source_id!r}"
            )
    return TestSourceProvenance(
        source_id=source.source_id,
        source_revision=source.source_revision,
        source_kind=source.source_kind,
        content_sha256=digest,
        size_bytes=len(raw),
        resolved_path=str(resolved),
        suite_id=suite_id,
        suite_version=suite_version,
        split=split,
        operator_artifact_path=(
            source.operator_artifact_path or str(resolved)
        ),
        generation_model=source.generation_model,
        generation_profile=source.generation_profile,
        prompt_sha256=source.prompt_sha256,
        trajectory_id=source.trajectory_id,
        round_index=source.round_index,
        coverage={} if coverage is None else coverage,
        qualification_status=qualification_status,
        feedback_visibility=feedback_visibility,
    )
