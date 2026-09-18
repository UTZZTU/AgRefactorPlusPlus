"""Internal R5 research profile selection for the existing product path.

The profile is intentionally not a CLI mode. Ordinary product execution has
no R5 arm selected; an experiment may select A0-A6 through a typed internal
request or the private environment seam used by the campaign runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Any


class R5ProfileError(ValueError):
    """Raised when an internal R5 profile crosses a product boundary."""


class R5Arm(str, Enum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"


@dataclass(frozen=True, slots=True)
class R5Profile:
    arm: R5Arm | None = None
    memory_mode: str = "none"
    advisor_mode: str = "off"
    authorization_mode: str | None = None
    candidate_only: bool = True

    def __post_init__(self) -> None:
        arm = self.arm
        if arm is not None and not isinstance(arm, R5Arm):
            arm = R5Arm(str(arm))
            object.__setattr__(self, "arm", arm)
        if arm is None:
            if self.memory_mode != "none" or self.advisor_mode != "off":
                raise R5ProfileError("unselected R5 profile must remain off")
            if self.authorization_mode is not None:
                raise R5ProfileError("unselected R5 profile cannot authorize a lane")
            return
        expected = {
            R5Arm.A0: ("none", "off", None),
            R5Arm.A1: ("none", "shadow", None),
            R5Arm.A2: ("none", "on", "advisor_only"),
            R5Arm.A3: ("similarity_only", "on", "similarity_only"),
            R5Arm.A4: ("positive_only_gated", "on", "gated_memory"),
            R5Arm.A5: ("positive_negative_gated", "on", "gated_memory"),
            R5Arm.A6: ("full_lifecycle_gated", "on", "gated_memory"),
        }[arm]
        if (self.memory_mode, self.advisor_mode, self.authorization_mode) != expected:
            raise R5ProfileError("R5 arm semantics are frozen and discriminated")
        if self.candidate_only is not True:
            raise R5ProfileError("R5 mutation scope must remain Candidate-only")

    @property
    def selected(self) -> bool:
        return self.arm is not None

    @property
    def shadow_enabled(self) -> bool:
        return self.advisor_mode in {"shadow", "on"}

    @property
    def mutation_enabled(self) -> bool:
        return self.arm in {R5Arm.A2, R5Arm.A3, R5Arm.A4, R5Arm.A5, R5Arm.A6}

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": None if self.arm is None else self.arm.value,
            "memory_mode": self.memory_mode,
            "advisor_mode": self.advisor_mode,
            "authorization_mode": self.authorization_mode,
            "candidate_only": self.candidate_only,
            "selected": self.selected,
        }


def resolve_r5_profile(value: str | None = None) -> R5Profile:
    """Resolve an internal profile; absent/empty values preserve defaults."""

    raw = value if value is not None else os.environ.get("AGREFACTOR_R5_ARM")
    if raw is None or not raw.strip() or raw.strip().casefold() in {"off", "none"}:
        return R5Profile()
    try:
        arm = R5Arm(raw.strip().upper())
        semantics = {
            R5Arm.A0: ("none", "off", None),
            R5Arm.A1: ("none", "shadow", None),
            R5Arm.A2: ("none", "on", "advisor_only"),
            R5Arm.A3: ("similarity_only", "on", "similarity_only"),
            R5Arm.A4: ("positive_only_gated", "on", "gated_memory"),
            R5Arm.A5: ("positive_negative_gated", "on", "gated_memory"),
            R5Arm.A6: ("full_lifecycle_gated", "on", "gated_memory"),
        }[arm]
        return R5Profile(
            arm=arm,
            memory_mode=semantics[0],
            advisor_mode=semantics[1],
            authorization_mode=semantics[2],
        )
    except ValueError as exc:
        raise R5ProfileError("AGREFACTOR_R5_ARM must be off or A0-A6") from exc


__all__ = ["R5Arm", "R5Profile", "R5ProfileError", "resolve_r5_profile"]
