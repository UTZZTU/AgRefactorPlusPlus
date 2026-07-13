"""Target and toolchain configuration shared by all AgRefactor++ flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Describe the hardware target and HLS toolchain for one run."""

    name: str
    toolchain: str
    toolchain_version: str | None = None
    device: str | None = None
    clock_period_ns: float = 10.0
    compile_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        name = self.name.strip()
        toolchain = self.toolchain.strip()

        if not name:
            raise ValueError("TargetProfile.name must not be empty")
        if not toolchain:
            raise ValueError("TargetProfile.toolchain must not be empty")
        if not isfinite(self.clock_period_ns) or self.clock_period_ns <= 0:
            raise ValueError("clock_period_ns must be a finite positive number")

        toolchain_version = self._clean_optional(self.toolchain_version)
        device = self._clean_optional(self.device)
        compile_flags = tuple(flag.strip() for flag in self.compile_flags)

        if any(not flag for flag in compile_flags):
            raise ValueError("compile_flags must not contain empty values")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "toolchain", toolchain)
        object.__setattr__(self, "toolchain_version", toolchain_version)
        object.__setattr__(self, "device", device)
        object.__setattr__(self, "compile_flags", compile_flags)

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "name": self.name,
            "toolchain": self.toolchain,
            "toolchain_version": self.toolchain_version,
            "device": self.device,
            "clock_period_ns": self.clock_period_ns,
            "compile_flags": list(self.compile_flags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TargetProfile":
        """Build a profile from a mapping such as parsed JSON or YAML."""

        return cls(
            name=data["name"],
            toolchain=data["toolchain"],
            toolchain_version=data.get("toolchain_version"),
            device=data.get("device"),
            clock_period_ns=float(data.get("clock_period_ns", 10.0)),
            compile_flags=tuple(data.get("compile_flags", ())),
        )
