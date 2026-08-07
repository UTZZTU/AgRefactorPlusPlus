"""Test-suite roles and metadata shared by evaluation flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .test_source import TestSourceSpec


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
        "source",
        "runtime_contract",
    }
)

_RUNTIME_CONTRACT_KIND = "public_differential_self_check_v1"


def _normalize_runtime_contract(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("runtime_contract must be a mapping or null")
    required = {
        "schema_version",
        "kind",
        "candidate_mismatch_returncodes",
    }
    if set(value) != required:
        raise ValueError("runtime_contract has unexpected fields")
    if value.get("schema_version") != 1:
        raise ValueError("runtime_contract.schema_version must be 1")
    if value.get("kind") != _RUNTIME_CONTRACT_KIND:
        raise ValueError(
            "runtime_contract.kind must be public_differential_self_check_v1"
        )
    raw_codes = value.get("candidate_mismatch_returncodes")
    if (
        not isinstance(raw_codes, Sequence)
        or isinstance(raw_codes, (str, bytes))
        or not raw_codes
    ):
        raise ValueError(
            "runtime_contract.candidate_mismatch_returncodes must be a non-empty sequence"
        )
    codes: list[int] = []
    for raw in raw_codes:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0 or raw > 255:
            raise ValueError(
                "candidate mismatch return codes must be integers in [1, 255]"
            )
        if raw in codes:
            raise ValueError("candidate mismatch return codes must be unique")
        codes.append(raw)
    return {
        "schema_version": 1,
        "kind": _RUNTIME_CONTRACT_KIND,
        "candidate_mismatch_returncodes": tuple(codes),
    }


@dataclass(frozen=True, slots=True)
class TestSuiteSpec:
    """Describe one evaluation suite without defining its executor."""

    suite_id: str
    split: EvaluationSplit = EvaluationSplit.PUBLIC
    suite_version: str | None = None
    case_count: int | None = None
    testbench_path: str | None = None
    source: TestSourceSpec | None = None
    runtime_contract: Mapping[str, Any] | None = None

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

        source = self.source
        if source is not None and not isinstance(
            source,
            TestSourceSpec,
        ):
            if isinstance(source, Mapping):
                source = TestSourceSpec.from_dict(source)
            else:
                raise TypeError(
                    "TestSuiteSpec.source must be a "
                    "TestSourceSpec, mapping or null"
                )
        if source is not None and testbench_path is None:
            raise ValueError(
                "TestSuiteSpec.source requires testbench_path"
            )
        runtime_contract = _normalize_runtime_contract(self.runtime_contract)
        if runtime_contract is not None and split is not EvaluationSplit.PUBLIC:
            raise ValueError("runtime_contract is Public-only")

        object.__setattr__(self, "suite_id", suite_id)
        object.__setattr__(self, "split", split)
        object.__setattr__(self, "suite_version", suite_version)
        object.__setattr__(self, "case_count", case_count)
        object.__setattr__(self, "testbench_path", testbench_path)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "runtime_contract", runtime_contract)

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

        payload = {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "split": self.split.value,
            "case_count": self.case_count,
            "testbench_path": self.testbench_path,
            "feedback_visible_to_agent": (
                self.feedback_visible_to_agent
            ),
        }
        if self.source is not None:
            payload["source"] = self.source.to_dict()
        if self.runtime_contract is not None:
            payload["runtime_contract"] = {
                "schema_version": 1,
                "kind": self.runtime_contract["kind"],
                "candidate_mismatch_returncodes": list(
                    self.runtime_contract["candidate_mismatch_returncodes"]
                ),
            }
        return payload

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
            source=data.get("source"),
            runtime_contract=data.get("runtime_contract"),
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
