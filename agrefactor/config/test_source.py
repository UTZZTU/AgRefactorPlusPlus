"""Declared and resolved identities for evaluation test sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SPEC_FIELDS = frozenset(
    {
        "source_id",
        "source_revision",
        "source_kind",
        "expected_content_sha256",
    }
)
_ALLOWED_PROVENANCE_FIELDS = frozenset(
    {
        "source_id",
        "source_revision",
        "source_kind",
        "content_sha256",
        "size_bytes",
        "resolved_path",
        "redacted",
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
    cleaned = value.strip()
    return cleaned or None


def _sha256(value: object, field_name: str) -> str:
    cleaned = _required(value, field_name).lower()
    if _SHA256_RE.fullmatch(cleaned) is None:
        raise ValueError(
            f"{field_name} must be a 64-character SHA-256 digest"
        )
    return cleaned


class TestSourceKind(str, Enum):
    """How a test source is supplied to an evaluator."""

    FILESYSTEM = "filesystem"
    GENERATED = "generated"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class TestSourceSpec:
    """Declare stable source identity and an optional expected digest."""

    source_id: str
    source_revision: str | None = None
    source_kind: TestSourceKind = TestSourceKind.FILESYSTEM
    expected_content_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            _required(self.source_id, "TestSourceSpec.source_id"),
        )
        object.__setattr__(
            self,
            "source_revision",
            _optional(
                self.source_revision,
                "TestSourceSpec.source_revision",
            ),
        )

        source_kind = self.source_kind
        if not isinstance(source_kind, TestSourceKind):
            try:
                source_kind = TestSourceKind(str(source_kind))
            except ValueError as exc:
                choices = ", ".join(item.value for item in TestSourceKind)
                raise ValueError(
                    "Unsupported test source kind "
                    f"{self.source_kind!r}; expected one of: {choices}"
                ) from exc
        object.__setattr__(self, "source_kind", source_kind)

        digest = self.expected_content_sha256
        object.__setattr__(
            self,
            "expected_content_sha256",
            (
                None
                if digest is None
                else _sha256(
                    digest,
                    "TestSourceSpec.expected_content_sha256",
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_kind": self.source_kind.value,
            "expected_content_sha256": self.expected_content_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TestSourceSpec":
        if not isinstance(data, Mapping):
            raise TypeError("test source spec must be a mapping")
        unknown = set(data) - _ALLOWED_SPEC_FIELDS
        if unknown:
            raise ValueError(
                "Unknown test source fields: "
                + ", ".join(sorted(unknown))
            )
        return cls(
            source_id=data["source_id"],
            source_revision=data.get("source_revision"),
            source_kind=data.get(
                "source_kind",
                TestSourceKind.FILESYSTEM.value,
            ),
            expected_content_sha256=data.get(
                "expected_content_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class TestSourceProvenance:
    """Resolved content identity for the exact source used by evaluation."""

    source_id: str
    source_revision: str | None
    source_kind: TestSourceKind
    content_sha256: str
    size_bytes: int
    resolved_path: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            _required(
                self.source_id,
                "TestSourceProvenance.source_id",
            ),
        )
        object.__setattr__(
            self,
            "source_revision",
            _optional(
                self.source_revision,
                "TestSourceProvenance.source_revision",
            ),
        )

        source_kind = self.source_kind
        if not isinstance(source_kind, TestSourceKind):
            try:
                source_kind = TestSourceKind(str(source_kind))
            except ValueError as exc:
                raise ValueError(
                    "Unsupported resolved test source kind: "
                    f"{self.source_kind!r}"
                ) from exc
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(
            self,
            "content_sha256",
            _sha256(
                self.content_sha256,
                "TestSourceProvenance.content_sha256",
            ),
        )

        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError(
                "TestSourceProvenance.size_bytes must be "
                "a non-negative integer"
            )
        object.__setattr__(
            self,
            "resolved_path",
            _required(
                self.resolved_path,
                "TestSourceProvenance.resolved_path",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_kind": self.source_kind.value,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "resolved_path": self.resolved_path,
            "redacted": False,
        }

    def to_hidden_agent_dict(self) -> dict[str, Any]:
        """Return identity without content/path details for hidden suites."""

        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_kind": self.source_kind.value,
            "redacted": True,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "TestSourceProvenance":
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
                "redacted test source provenance cannot restore "
                "operator-full provenance"
            )
        return cls(
            source_id=data["source_id"],
            source_revision=data.get("source_revision"),
            source_kind=data["source_kind"],
            content_sha256=data["content_sha256"],
            size_bytes=data["size_bytes"],
            resolved_path=data["resolved_path"],
        )


def resolve_test_source(
    source: TestSourceSpec,
    path: str | Path,
    *,
    execution_content: str | None = None,
) -> TestSourceProvenance:
    """Resolve and validate one filesystem source before evaluator launch."""

    if not isinstance(source, TestSourceSpec):
        raise TypeError("source must be a TestSourceSpec")
    if source.source_kind is not TestSourceKind.FILESYSTEM:
        raise ValueError(
            "Only filesystem test sources can be resolved by "
            "the local CSIM evaluator"
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
            "test source digest mismatch for "
            f"{source.source_id!r}: observed {digest}, "
            f"expected {expected}"
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
    )
