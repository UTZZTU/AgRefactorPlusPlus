"""Independent Public/Hidden source selection and mode derivation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from .test_suite import EvaluationSplit


class TestSourceSelectionMode(str, Enum):
    AUTO = "auto"
    PROVIDED = "provided"
    NONE = "none"


class OverallTestSourceMode(str, Enum):
    AUTO = "auto"
    PROVIDED = "provided"
    HYBRID = "hybrid"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class TestSourceSelection:
    split: EvaluationSplit
    mode: TestSourceSelectionMode
    provided_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        split = self.split
        if not isinstance(split, EvaluationSplit):
            split = EvaluationSplit(str(split))
        mode = self.mode
        if not isinstance(mode, TestSourceSelectionMode):
            mode = TestSourceSelectionMode(str(mode))
        if isinstance(self.provided_paths, (str, bytes)):
            raise TypeError("provided_paths must be a sequence")
        normalized = []
        for raw in self.provided_paths:
            if not isinstance(raw, str):
                raise TypeError("provided test paths must be strings")
            cleaned = raw.strip()
            if not cleaned:
                raise ValueError("provided test paths must not be empty")
            normalized.append(str(Path(cleaned).expanduser()))
        if mode is TestSourceSelectionMode.PROVIDED and not normalized:
            raise ValueError(
                "provided test selection requires at least one path"
            )
        if mode is not TestSourceSelectionMode.PROVIDED and normalized:
            raise ValueError(
                f"{mode.value} test selection must not contain paths"
            )
        object.__setattr__(self, "split", split)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "provided_paths", tuple(normalized))

    @classmethod
    def auto(cls, split: EvaluationSplit) -> "TestSourceSelection":
        return cls(split=split, mode=TestSourceSelectionMode.AUTO)

    @classmethod
    def provided(
        cls,
        split: EvaluationSplit,
        paths: Sequence[str],
    ) -> "TestSourceSelection":
        return cls(
            split=split,
            mode=TestSourceSelectionMode.PROVIDED,
            provided_paths=tuple(paths),
        )

    @classmethod
    def none(cls, split: EvaluationSplit) -> "TestSourceSelection":
        return cls(split=split, mode=TestSourceSelectionMode.NONE)

    def to_operator_dict(self) -> dict[str, Any]:
        return {
            "split": self.split.value,
            "mode": self.mode.value,
            "provided_paths": list(self.provided_paths),
            "suite_count": len(self.provided_paths),
            "redacted": False,
        }

    def to_agent_dict(self) -> dict[str, Any]:
        if self.split is EvaluationSplit.PUBLIC:
            return self.to_operator_dict()
        return {
            "split": self.split.value,
            "mode": self.mode.value,
            "suite_count": len(self.provided_paths),
            "redacted": True,
        }


@dataclass(frozen=True, slots=True)
class TestSourcePlan:
    public: TestSourceSelection
    hidden: TestSourceSelection

    def __post_init__(self) -> None:
        if not isinstance(self.public, TestSourceSelection):
            raise TypeError("public must be a TestSourceSelection")
        if not isinstance(self.hidden, TestSourceSelection):
            raise TypeError("hidden must be a TestSourceSelection")
        if self.public.split is not EvaluationSplit.PUBLIC:
            raise ValueError("public selection must use public split")
        if self.hidden.split is not EvaluationSplit.HIDDEN:
            raise ValueError("hidden selection must use hidden split")

    @property
    def overall_mode(self) -> OverallTestSourceMode:
        pair = (self.public.mode, self.hidden.mode)
        if pair == (
            TestSourceSelectionMode.PROVIDED,
            TestSourceSelectionMode.PROVIDED,
        ):
            return OverallTestSourceMode.PROVIDED
        if pair == (
            TestSourceSelectionMode.AUTO,
            TestSourceSelectionMode.AUTO,
        ):
            return OverallTestSourceMode.AUTO
        active = {
            item for item in pair
            if item is not TestSourceSelectionMode.NONE
        }
        if not active:
            return OverallTestSourceMode.NONE
        if len(active) == 1:
            only = next(iter(active))
            return OverallTestSourceMode(only.value)
        return OverallTestSourceMode.HYBRID

    def to_operator_dict(self) -> dict[str, Any]:
        return {
            "overall_mode": self.overall_mode.value,
            "public": self.public.to_operator_dict(),
            "hidden": self.hidden.to_operator_dict(),
        }

    def to_agent_dict(self) -> dict[str, Any]:
        return {
            "overall_mode": self.overall_mode.value,
            "public": self.public.to_agent_dict(),
            "hidden": self.hidden.to_agent_dict(),
        }
