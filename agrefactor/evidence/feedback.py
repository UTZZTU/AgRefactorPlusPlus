"""Generic feedback vocabulary shared by evaluation adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any


class FeedbackStage(str, Enum):
    """Identify where an observation was produced."""

    INPUT = "input"
    CONFIGURATION = "configuration"
    STATIC_CHECK = "static_check"
    COMPILE = "compile"
    LINK = "link"
    TEST = "test"
    CSIM = "csim"
    CSYNTH = "csynth"
    COSIM = "cosim"
    TOOLCHAIN = "toolchain"


class FeedbackCategory(str, Enum):
    """Classify an observation independently of its producing stage."""

    INVALID_INPUT = "invalid_input"
    INVALID_CONFIGURATION = "invalid_configuration"
    FORBIDDEN_DEPENDENCY = "forbidden_dependency"
    UNDECLARED_TYPE = "undeclared_type"
    UNDECLARED_SYMBOL = "undeclared_symbol"
    SYNTAX_ERROR = "syntax_error"
    LINK_ERROR = "link_error"
    LINKAGE_MISMATCH = "linkage_mismatch"
    FUNCTIONAL_MISMATCH = "functional_mismatch"
    RUNTIME_CRASH = "runtime_crash"
    TIMEOUT = "timeout"
    UNSUPPORTED_CONSTRUCT = "unsupported_construct"
    UNKNOWN_BOUND = "unknown_bound"
    PIPELINE_DEPENDENCY = "pipeline_dependency"
    MEMORY_PORT_CONTENTION = "memory_port_contention"
    TIMING_VIOLATION = "timing_violation"
    RESOURCE_LIMIT = "resource_limit"
    TOOLCHAIN_FAILURE = "toolchain_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNKNOWN = "unknown"


class FeedbackSeverity(str, Enum):
    """Describe how strongly an observation should block progress."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"

    @property
    def rank(self) -> int:
        """Return a stable ordering used for report summaries."""

        return {
            FeedbackSeverity.INFO: 0,
            FeedbackSeverity.WARNING: 1,
            FeedbackSeverity.ERROR: 2,
            FeedbackSeverity.FATAL: 3,
        }[self]

    @property
    def blocking(self) -> bool:
        """Return whether this severity blocks normal progression."""

        return self in {
            FeedbackSeverity.ERROR,
            FeedbackSeverity.FATAL,
        }


class FeedbackOwner(str, Enum):
    """Identify the component most likely responsible."""

    NONE = "none"
    TASK_INPUT = "task_input"
    CONFIGURATION = "configuration"
    TESTBENCH = "testbench"
    ORIGINAL = "original"
    CANDIDATE = "candidate"
    TOOLCHAIN = "toolchain"
    EVALUATOR = "evaluator"
    UNKNOWN = "unknown"


_ALLOWED_ITEM_FIELDS = frozenset(
    {
        "feedback_id",
        "stage",
        "category",
        "severity",
        "owner",
        "summary",
        "detail",
        "source",
        "evidence_ref",
        "metadata",
        "blocking",
    }
)

_ALLOWED_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "source",
        "items",
        "source_evidence",
        "metadata",
        "blocking",
        "highest_severity",
    }
)


def _clean_required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _clean_optional(
    value: str | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or null")
    cleaned = value.strip()
    return cleaned or None


def _copy_json_mapping(
    value: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be finite JSON-serializable data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{field_name} must normalize to an object")
    return copied


def _coerce_enum(
    value: Any,
    enum_type: type[Enum],
    field_name: str,
) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        choices = ", ".join(
            str(item.value) for item in enum_type
        )
        raise ValueError(
            f"Unsupported {field_name} {value!r}; "
            f"expected one of: {choices}"
        ) from exc


@dataclass(frozen=True, slots=True)
class FeedbackItem:
    """Describe one normalized observation from an evaluation source."""

    feedback_id: str
    stage: FeedbackStage
    category: FeedbackCategory
    severity: FeedbackSeverity
    summary: str
    owner: FeedbackOwner = FeedbackOwner.UNKNOWN
    detail: str | None = None
    source: str | None = None
    evidence_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        feedback_id = _clean_required(
            self.feedback_id,
            "FeedbackItem.feedback_id",
        )
        summary = _clean_required(
            self.summary,
            "FeedbackItem.summary",
        )
        detail = _clean_optional(
            self.detail,
            "FeedbackItem.detail",
        )
        source = _clean_optional(
            self.source,
            "FeedbackItem.source",
        )
        evidence_ref = _clean_optional(
            self.evidence_ref,
            "FeedbackItem.evidence_ref",
        )
        metadata = _copy_json_mapping(
            self.metadata,
            "FeedbackItem.metadata",
        )

        stage = _coerce_enum(
            self.stage,
            FeedbackStage,
            "feedback stage",
        )
        category = _coerce_enum(
            self.category,
            FeedbackCategory,
            "feedback category",
        )
        severity = _coerce_enum(
            self.severity,
            FeedbackSeverity,
            "feedback severity",
        )
        owner = _coerce_enum(
            self.owner,
            FeedbackOwner,
            "feedback owner",
        )

        object.__setattr__(self, "feedback_id", feedback_id)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "detail", detail)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "evidence_ref", evidence_ref)
        object.__setattr__(self, "metadata", metadata)

    @property
    def blocking(self) -> bool:
        """Return whether this item blocks normal progression."""

        return self.severity.blocking

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "feedback_id": self.feedback_id,
            "stage": self.stage.value,
            "category": self.category.value,
            "severity": self.severity.value,
            "owner": self.owner.value,
            "summary": self.summary,
            "detail": self.detail,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
            "metadata": dict(self.metadata),
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "FeedbackItem":
        """Build an item while validating derived fields."""

        if not isinstance(data, Mapping):
            raise TypeError("feedback item must be a mapping")

        unknown_fields = set(data) - _ALLOWED_ITEM_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(
                f"Unknown feedback item fields: {names}"
            )

        item = cls(
            feedback_id=data["feedback_id"],
            stage=data["stage"],
            category=data["category"],
            severity=data["severity"],
            owner=data.get(
                "owner",
                FeedbackOwner.UNKNOWN.value,
            ),
            summary=data["summary"],
            detail=data.get("detail"),
            source=data.get("source"),
            evidence_ref=data.get("evidence_ref"),
            metadata=data.get("metadata", {}),
        )

        if "blocking" in data:
            declared = data["blocking"]
            if not isinstance(declared, bool):
                raise TypeError("blocking must be a boolean")
            if declared is not item.blocking:
                raise ValueError(
                    "blocking conflicts with feedback severity"
                )

        return item


@dataclass(frozen=True, slots=True)
class FeedbackReport:
    """Aggregate normalized feedback while preserving source evidence."""

    report_id: str
    source: str
    items: tuple[FeedbackItem, ...] = ()
    source_evidence: Mapping[str, Any] = field(
        default_factory=dict
    )
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        report_id = _clean_required(
            self.report_id,
            "FeedbackReport.report_id",
        )
        source = _clean_required(
            self.source,
            "FeedbackReport.source",
        )

        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
        ):
            raise TypeError(
                "FeedbackReport.schema_version must be an integer"
            )
        if self.schema_version <= 0:
            raise ValueError(
                "FeedbackReport.schema_version must be positive"
            )

        items = self._normalize_items(self.items)
        feedback_ids = [
            item.feedback_id for item in items
        ]
        if len(set(feedback_ids)) != len(feedback_ids):
            raise ValueError(
                "FeedbackReport.items must use unique feedback_id values"
            )

        source_evidence = _copy_json_mapping(
            self.source_evidence,
            "FeedbackReport.source_evidence",
        )
        metadata = _copy_json_mapping(
            self.metadata,
            "FeedbackReport.metadata",
        )

        object.__setattr__(self, "report_id", report_id)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "items", items)
        object.__setattr__(
            self,
            "source_evidence",
            source_evidence,
        )
        object.__setattr__(self, "metadata", metadata)

    @staticmethod
    def _normalize_items(
        value: Sequence[FeedbackItem],
    ) -> tuple[FeedbackItem, ...]:
        if isinstance(value, (str, bytes, Mapping)):
            raise TypeError(
                "FeedbackReport.items must be a sequence "
                "of FeedbackItem values"
            )
        if not isinstance(value, Sequence):
            raise TypeError(
                "FeedbackReport.items must be a sequence"
            )

        items = tuple(value)
        for item in items:
            if not isinstance(item, FeedbackItem):
                raise TypeError(
                    "FeedbackReport.items entries must be "
                    "FeedbackItem values"
                )
        return items

    @staticmethod
    def _parse_items(value: Any) -> tuple[FeedbackItem, ...]:
        if isinstance(value, (str, bytes, Mapping)):
            raise TypeError(
                "feedback report items must be an array"
            )
        if not isinstance(value, Sequence):
            raise TypeError(
                "feedback report items must be an array"
            )

        items: list[FeedbackItem] = []
        for item in value:
            if isinstance(item, FeedbackItem):
                items.append(item)
                continue
            if not isinstance(item, Mapping):
                raise TypeError(
                    "feedback report item entries must be mappings"
                )
            items.append(FeedbackItem.from_dict(item))
        return tuple(items)

    @property
    def blocking(self) -> bool:
        """Return whether any item blocks normal progression."""

        return any(item.blocking for item in self.items)

    @property
    def highest_severity(self) -> FeedbackSeverity | None:
        """Return the highest item severity, if the report has items."""

        if not self.items:
            return None
        return max(
            (item.severity for item in self.items),
            key=lambda severity: severity.rank,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return complete operator-oriented report data.

        ``source_evidence`` intentionally preserves its supplied payload.
        Adapters that produce agent-facing reports must supply an
        agent-safe source payload.
        """

        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "source": self.source,
            "items": [item.to_dict() for item in self.items],
            "source_evidence": dict(self.source_evidence),
            "metadata": dict(self.metadata),
            "blocking": self.blocking,
            "highest_severity": (
                self.highest_severity.value
                if self.highest_severity is not None
                else None
            ),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "FeedbackReport":
        """Build a report while validating derived summary fields."""

        if not isinstance(data, Mapping):
            raise TypeError("feedback report must be a mapping")

        unknown_fields = set(data) - _ALLOWED_REPORT_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(
                f"Unknown feedback report fields: {names}"
            )

        report = cls(
            report_id=data["report_id"],
            source=data["source"],
            items=cls._parse_items(data.get("items", ())),
            source_evidence=data.get("source_evidence", {}),
            metadata=data.get("metadata", {}),
            schema_version=data.get("schema_version", 1),
        )

        if "blocking" in data:
            declared_blocking = data["blocking"]
            if not isinstance(declared_blocking, bool):
                raise TypeError("blocking must be a boolean")
            if declared_blocking is not report.blocking:
                raise ValueError(
                    "blocking conflicts with feedback items"
                )

        if "highest_severity" in data:
            declared_severity = data["highest_severity"]
            actual = (
                report.highest_severity.value
                if report.highest_severity is not None
                else None
            )
            if declared_severity != actual:
                raise ValueError(
                    "highest_severity conflicts with feedback items"
                )

        return report
