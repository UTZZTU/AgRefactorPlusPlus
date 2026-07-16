"""Task configuration shared by refactoring and optimization flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .test_suite import TestSuiteSpec
from .target import (
    TargetProfile,
    default_target_profile,
    resolve_target_profile,
)


class RunMode(str, Enum):
    """Supported AgRefactor++ execution modes."""

    REFACTOR = "refactor"
    OPTIMIZE = "optimize"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Describe one HLS transformation task independently of its runner."""

    task_id: str
    kernel_path: str
    kernel_name: str
    target: TargetProfile = field(
        default_factory=default_target_profile
    )
    mode: RunMode = RunMode.REFACTOR
    testbench_path: str | None = None
    test_suites: tuple[TestSuiteSpec, ...] = ()

    def __post_init__(self) -> None:
        task_id = self.task_id.strip()
        kernel_path = self.kernel_path.strip()
        kernel_name = self.kernel_name.strip()
        testbench_path = self._clean_optional(self.testbench_path)
        test_suites = self._normalize_test_suites(self.test_suites)

        if not task_id:
            raise ValueError("TaskSpec.task_id must not be empty")
        if not kernel_path:
            raise ValueError("TaskSpec.kernel_path must not be empty")
        if not kernel_name:
            raise ValueError("TaskSpec.kernel_name must not be empty")
        if not isinstance(self.target, TargetProfile):
            raise TypeError("TaskSpec.target must be a TargetProfile")

        mode = self.mode
        if not isinstance(mode, RunMode):
            try:
                mode = RunMode(str(mode))
            except ValueError as exc:
                choices = ", ".join(item.value for item in RunMode)
                raise ValueError(
                    f"Unsupported mode {self.mode!r}; expected one of: "
                    f"{choices}"
                ) from exc

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "kernel_path", kernel_path)
        object.__setattr__(self, "kernel_name", kernel_name)
        object.__setattr__(self, "target", self.target)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "testbench_path", testbench_path)
        object.__setattr__(self, "test_suites", test_suites)

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(
                "TaskSpec.testbench_path must be a string or null"
            )
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _normalize_test_suites(
        value: Sequence[TestSuiteSpec] | None,
    ) -> tuple[TestSuiteSpec, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes, Mapping)):
            raise TypeError(
                "TaskSpec.test_suites must be a sequence of "
                "TestSuiteSpec values"
            )
        if not isinstance(value, Sequence):
            raise TypeError(
                "TaskSpec.test_suites must be a sequence or null"
            )

        suites = tuple(value)
        for suite in suites:
            if not isinstance(suite, TestSuiteSpec):
                raise TypeError(
                    "TaskSpec.test_suites entries must be "
                    "TestSuiteSpec values"
                )

        suite_ids = [suite.suite_id for suite in suites]
        if len(set(suite_ids)) != len(suite_ids):
            raise ValueError(
                "TaskSpec.test_suites must use unique suite_id values"
            )

        return suites

    @staticmethod
    def _parse_test_suites(
        value: Any,
    ) -> tuple[TestSuiteSpec, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes, Mapping)):
            raise TypeError("task.test_suites must be an array or null")
        if not isinstance(value, Sequence):
            raise TypeError("task.test_suites must be an array or null")

        suites: list[TestSuiteSpec] = []
        for item in value:
            if isinstance(item, TestSuiteSpec):
                suites.append(item)
                continue
            if not isinstance(item, Mapping):
                raise TypeError(
                    "task.test_suites entries must be mappings"
                )
            suites.append(TestSuiteSpec.from_dict(item))
        return tuple(suites)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable, fully resolved representation."""

        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "kernel_path": self.kernel_path,
            "kernel_name": self.kernel_name,
            "target": self.target.to_dict(),
            "mode": self.mode.value,
            "testbench_path": self.testbench_path,
        }
        if self.test_suites:
            payload["test_suites"] = [
                suite.to_dict() for suite in self.test_suites
            ]
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskSpec":
        """Build a task while resolving optional target defaults."""

        return cls(
            task_id=data["task_id"],
            kernel_path=data["kernel_path"],
            kernel_name=data["kernel_name"],
            target=resolve_target_profile(data.get("target")),
            mode=RunMode(
                data.get("mode", RunMode.REFACTOR.value)
            ),
            testbench_path=data.get("testbench_path"),
            test_suites=cls._parse_test_suites(
                data.get("test_suites")
            ),
        )
