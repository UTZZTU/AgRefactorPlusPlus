"""Test-suite roles and metadata shared by evaluation flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvaluationSplit(str, Enum):
    """Control whether evaluation details may be shown to an agent."""

    PUBLIC = "public"
    HIDDEN = "hidden"

    @property
    def feedback_visible_to_agent(self) -> bool:
        """Return whether detailed suite feedback may enter an agent prompt."""

        return self is EvaluationSplit.PUBLIC


_ALLOWED_TEST_SUITE_FIELDS = frozenset(
    {
        "suite_id",
        "suite_version",
        "split",
        "case_count",
        "testbench_path",
        "feedback_visible_to_agent",
    }
)


@dataclass(frozen=True, slots=True)
class TestSuiteSpec:
    """Describe one evaluation suite without defining its executor."""

    suite_id: str
    split: EvaluationSplit = EvaluationSplit.PUBLIC
    suite_version: str | None = None
    case_count: int | None = None
    testbench_path: str | None = None

    def __post_init__(self) -> None:
        suite_id = self._clean_required(
            self.suite_id,
            "TestSuiteSpec.suite_id",
        )
        suite_version = self._clean_optional(
            self.suite_version,
            "TestSuiteSpec.suite_version",
        )
        testbench_path = self._clean_optional(
            self.testbench_path,
            "TestSuiteSpec.testbench_path",
        )

        split = self.split
        if not isinstance(split, EvaluationSplit):
            try:
                split = EvaluationSplit(str(split))
            except ValueError as exc:
                choices = ", ".join(
                    item.value for item in EvaluationSplit
                )
                raise ValueError(
                    f"Unsupported evaluation split {self.split!r}; "
                    f"expected one of: {choices}"
                ) from exc

        case_count = self.case_count
        if case_count is not None:
            if isinstance(case_count, bool) or not isinstance(
                case_count,
                int,
            ):
                raise TypeError(
                    "TestSuiteSpec.case_count must be an integer or null"
                )
            if case_count <= 0:
                raise ValueError(
                    "TestSuiteSpec.case_count must be positive when set"
                )

        object.__setattr__(self, "suite_id", suite_id)
        object.__setattr__(self, "split", split)
        object.__setattr__(self, "suite_version", suite_version)
        object.__setattr__(self, "case_count", case_count)
        object.__setattr__(self, "testbench_path", testbench_path)

    @property
    def feedback_visible_to_agent(self) -> bool:
        """Return the visibility policy derived from the suite split."""

        return self.split.feedback_visible_to_agent

    @staticmethod
    def _clean_required(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must not be empty")
        return cleaned

    @staticmethod
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

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable suite description."""

        return {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "split": self.split.value,
            "case_count": self.case_count,
            "testbench_path": self.testbench_path,
            "feedback_visible_to_agent": (
                self.feedback_visible_to_agent
            ),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "TestSuiteSpec":
        """Build a suite while validating derived visibility metadata."""

        if not isinstance(data, Mapping):
            raise TypeError("test suite must be a mapping")

        unknown_fields = set(data) - _ALLOWED_TEST_SUITE_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(f"Unknown test suite fields: {names}")

        suite = cls(
            suite_id=data["suite_id"],
            suite_version=data.get("suite_version"),
            split=data.get(
                "split",
                EvaluationSplit.PUBLIC.value,
            ),
            case_count=data.get("case_count"),
            testbench_path=data.get("testbench_path"),
        )

        if "feedback_visible_to_agent" in data:
            declared = data["feedback_visible_to_agent"]
            if not isinstance(declared, bool):
                raise TypeError(
                    "feedback_visible_to_agent must be a boolean"
                )
            if declared is not suite.feedback_visible_to_agent:
                raise ValueError(
                    "feedback_visible_to_agent conflicts with split"
                )

        return suite
