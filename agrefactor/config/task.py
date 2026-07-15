"""Task configuration shared by refactoring and optimization flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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

    def __post_init__(self) -> None:
        task_id = self.task_id.strip()
        kernel_path = self.kernel_path.strip()
        kernel_name = self.kernel_name.strip()
        testbench_path = self._clean_optional(self.testbench_path)

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

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable, fully resolved representation."""

        return {
            "task_id": self.task_id,
            "kernel_path": self.kernel_path,
            "kernel_name": self.kernel_name,
            "target": self.target.to_dict(),
            "mode": self.mode.value,
            "testbench_path": self.testbench_path,
        }

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
        )
