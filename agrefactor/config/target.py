"""Target and toolchain configuration shared by all AgRefactor++ flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any


DEFAULT_TARGET_PROFILE_NAME = "vitis-2023.2-default"

_ALLOWED_TARGET_OVERRIDE_FIELDS = frozenset(
    {
        "profile",
        "name",
        "toolchain",
        "toolchain_version",
        "device",
        "clock_period_ns",
        "clock_frequency_mhz",
        "compile_flags",
        "append_compile_flags",
    }
)


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Describe the hardware target and HLS toolchain for one run."""

    name: str
    toolchain: str
    toolchain_version: str | None = None
    device: str | None = None
    clock_period_ns: float = 5.0
    compile_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        name = self.name.strip()
        toolchain = self.toolchain.strip()

        if not name:
            raise ValueError("TargetProfile.name must not be empty")
        if not toolchain:
            raise ValueError("TargetProfile.toolchain must not be empty")
        if not isfinite(self.clock_period_ns) or self.clock_period_ns <= 0:
            raise ValueError(
                "clock_period_ns must be a finite positive number"
            )

        toolchain_version = self._clean_optional(self.toolchain_version)
        device = self._clean_optional(self.device)

        if isinstance(self.compile_flags, (str, bytes)):
            raise TypeError(
                "compile_flags must be a sequence of flag strings"
            )
        compile_flags = tuple(
            self._clean_required(flag, "compile flag")
            for flag in self.compile_flags
        )

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "toolchain", toolchain)
        object.__setattr__(
            self,
            "toolchain_version",
            toolchain_version,
        )
        object.__setattr__(self, "device", device)
        object.__setattr__(self, "compile_flags", compile_flags)

    @staticmethod
    def _clean_required(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must not be empty")
        return cleaned

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("optional profile text fields must be strings")
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
        """Build a complete profile from a mapping."""

        return cls(
            name=data["name"],
            toolchain=data["toolchain"],
            toolchain_version=data.get("toolchain_version"),
            device=data.get("device"),
            clock_period_ns=float(
                data.get("clock_period_ns", 5.0)
            ),
            compile_flags=tuple(data.get("compile_flags", ())),
        )


def default_target_profile() -> TargetProfile:
    """Return the built-in profile preserving the legacy HLS defaults."""

    return TargetProfile(
        name=DEFAULT_TARGET_PROFILE_NAME,
        toolchain="vitis_hls",
        toolchain_version="2023.2",
        device="xcu200-fsgd2104-2-e",
        clock_period_ns=5.0,
        compile_flags=("-D XILINX",),
    )


def resolve_target_profile(
    value: TargetProfile | Mapping[str, Any] | str | None,
) -> TargetProfile:
    """Resolve a target from a default, named profile, or partial overrides.

    Resolution rules:

    * ``None`` selects the built-in default profile.
    * ``"default"`` or the default profile name selects that profile.
    * A mapping starts from the selected/default profile and overrides only
      the fields present in the mapping.
    * ``clock_frequency_mhz`` is converted to ``clock_period_ns``.
    * ``compile_flags`` replaces the profile flags.
    * ``append_compile_flags`` appends flags without removing defaults.
    """

    if isinstance(value, TargetProfile):
        return value
    if value is None:
        return default_target_profile()
    if isinstance(value, str):
        return _resolve_named_profile(value)
    if not isinstance(value, Mapping):
        raise TypeError(
            "target must be a TargetProfile, profile name, mapping, or null"
        )

    unknown_fields = set(value) - _ALLOWED_TARGET_OVERRIDE_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"Unknown target profile fields: {names}")

    profile_name = value.get("profile", DEFAULT_TARGET_PROFILE_NAME)
    if not isinstance(profile_name, str):
        raise TypeError("target.profile must be a string")
    base = _resolve_named_profile(profile_name)

    period = _resolve_clock_period(value, base.clock_period_ns)

    if "compile_flags" in value:
        compile_flags = _normalize_flags(
            value["compile_flags"],
            "compile_flags",
        )
    else:
        compile_flags = base.compile_flags

    if "append_compile_flags" in value:
        compile_flags += _normalize_flags(
            value["append_compile_flags"],
            "append_compile_flags",
        )

    return TargetProfile(
        name=_required_text(value.get("name", base.name), "name"),
        toolchain=_required_text(
            value.get("toolchain", base.toolchain),
            "toolchain",
        ),
        toolchain_version=_optional_text(
            value.get(
                "toolchain_version",
                base.toolchain_version,
            ),
            "toolchain_version",
        ),
        device=_optional_text(
            value.get("device", base.device),
            "device",
        ),
        clock_period_ns=period,
        compile_flags=compile_flags,
    )


def _resolve_named_profile(name: str) -> TargetProfile:
    cleaned = name.strip()
    aliases = {
        "default": DEFAULT_TARGET_PROFILE_NAME,
        DEFAULT_TARGET_PROFILE_NAME: DEFAULT_TARGET_PROFILE_NAME,
    }
    canonical = aliases.get(cleaned)
    if canonical is None:
        available = ", ".join(sorted(aliases))
        raise ValueError(
            f"Unknown target profile {name!r}; available: {available}"
        )
    return default_target_profile()


def _resolve_clock_period(
    data: Mapping[str, Any],
    default_period_ns: float,
) -> float:
    has_period = "clock_period_ns" in data
    has_frequency = "clock_frequency_mhz" in data

    period = (
        _positive_number(data["clock_period_ns"], "clock_period_ns")
        if has_period
        else default_period_ns
    )

    if not has_frequency:
        return period

    frequency = _positive_number(
        data["clock_frequency_mhz"],
        "clock_frequency_mhz",
    )
    converted_period = 1000.0 / frequency

    if has_period and not isclose(
        period,
        converted_period,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise ValueError(
            "clock_period_ns conflicts with clock_frequency_mhz"
        )

    return converted_period


def _positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be a number") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(
            f"{field_name} must be a finite positive number"
        )
    return number


def _normalize_flags(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")

    flags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(
                f"{field_name} must contain only strings"
            )
        cleaned = item.strip()
        if not cleaned:
            raise ValueError(
                f"{field_name} must not contain empty values"
            )
        flags.append(cleaned)
    return tuple(flags)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"target.{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"target.{field_name} must not be empty")
    return cleaned


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"target.{field_name} must be a string or null"
        )
    cleaned = value.strip()
    return cleaned or None
