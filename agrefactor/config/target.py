"""Target and toolchain configuration shared by all AgRefactor++ flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import isclose, isfinite
from pathlib import Path
import re
from typing import Any

from .target_profiles import (
    DEFAULT_TARGET_PROFILE_NAME,
    available_target_profile_names,
    load_named_target_profile,
)


_RESOURCE_FIELDS = (
    "max_bram_18k",
    "max_dsp",
    "max_ff",
    "max_lut",
    "max_uram",
)
_PROFILE_FIELDS = (
    "name",
    "toolchain",
    "toolchain_version",
    "device",
    "clock_period_ns",
    "compile_flags",
    "executable",
    "settings_path",
    "parser_profile",
)
_PROVENANCE_FIELDS = (
    *_PROFILE_FIELDS,
    *(
        f"resource_limits.{name}"
        for name in _RESOURCE_FIELDS
    ),
)
_ALLOWED_TARGET_OVERRIDE_FIELDS = frozenset(
    {
        "profile",
        *_PROFILE_FIELDS,
        "clock_frequency_mhz",
        "append_compile_flags",
        "resource_limits",
    }
)
_PROFILE_JSON_FIELDS = frozenset(
    {
        "schema_version",
        *_PROFILE_FIELDS,
        "resource_limits",
    }
)
_PARSER_PROFILE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*$"
)


@dataclass(frozen=True, slots=True)
class TargetResourceLimits:
    """Optional device-resource ceilings for later candidate comparison."""

    max_bram_18k: int | None = None
    max_dsp: int | None = None
    max_ff: int | None = None
    max_lut: int | None = None
    max_uram: int | None = None

    def __post_init__(self) -> None:
        for field_name in _RESOURCE_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
            ):
                raise TypeError(
                    f"{field_name} must be an integer or null"
                )
            if value < 0:
                raise ValueError(
                    f"{field_name} must be non-negative"
                )

    def to_dict(self) -> dict[str, int | None]:
        return {
            name: getattr(self, name)
            for name in _RESOURCE_FIELDS
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
    ) -> "TargetResourceLimits":
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise TypeError(
                "resource_limits must be a mapping or null"
            )
        unknown = set(data) - set(_RESOURCE_FIELDS)
        if unknown:
            raise ValueError(
                "Unknown resource limit fields: "
                + ", ".join(sorted(unknown))
            )
        return cls(
            **{
                name: data.get(name)
                for name in _RESOURCE_FIELDS
            }
        )


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Describe one stable target execution contract for a run."""

    name: str
    toolchain: str
    toolchain_version: str | None = None
    device: str | None = None
    clock_period_ns: float = 5.0
    compile_flags: tuple[str, ...] = ()
    executable: str | None = None
    settings_path: str | None = None
    parser_profile: str = "vitis-hls-generic"
    resource_limits: TargetResourceLimits = field(
        default_factory=TargetResourceLimits
    )
    field_provenance: Mapping[str, str] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        name = _required_text(self.name, "name")
        toolchain = _required_text(
            self.toolchain,
            "toolchain",
        )
        if (
            not isfinite(self.clock_period_ns)
            or self.clock_period_ns <= 0
        ):
            raise ValueError(
                "clock_period_ns must be a finite positive number"
            )

        toolchain_version = _optional_text(
            self.toolchain_version,
            "toolchain_version",
        )
        device = _optional_text(
            self.device,
            "device",
        )
        executable = _optional_launcher_text(
            self.executable,
            "executable",
        )
        settings_path = _optional_settings_path(
            self.settings_path
        )
        parser_profile = _required_text(
            self.parser_profile,
            "parser_profile",
        )
        if not _PARSER_PROFILE_RE.fullmatch(
            parser_profile
        ):
            raise ValueError(
                "parser_profile must use lowercase letters, "
                "digits, dot, underscore, or hyphen"
            )

        if isinstance(self.compile_flags, (str, bytes)):
            raise TypeError(
                "compile_flags must be a sequence of flag strings"
            )
        compile_flags = tuple(
            _required_text(flag, "compile flag")
            for flag in self.compile_flags
        )

        resource_limits = self.resource_limits
        if isinstance(resource_limits, Mapping):
            resource_limits = (
                TargetResourceLimits.from_dict(
                    resource_limits
                )
            )
        if not isinstance(
            resource_limits,
            TargetResourceLimits,
        ):
            raise TypeError(
                "resource_limits must be "
                "TargetResourceLimits or a mapping"
            )

        provenance = _normalize_provenance(
            self.field_provenance
        )

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "toolchain", toolchain)
        object.__setattr__(
            self,
            "toolchain_version",
            toolchain_version,
        )
        object.__setattr__(self, "device", device)
        object.__setattr__(
            self,
            "compile_flags",
            compile_flags,
        )
        object.__setattr__(
            self,
            "executable",
            executable,
        )
        object.__setattr__(
            self,
            "settings_path",
            settings_path,
        )
        object.__setattr__(
            self,
            "parser_profile",
            parser_profile,
        )
        object.__setattr__(
            self,
            "resource_limits",
            resource_limits,
        )
        object.__setattr__(
            self,
            "field_provenance",
            provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable effective values."""

        return {
            "name": self.name,
            "toolchain": self.toolchain,
            "toolchain_version": (
                self.toolchain_version
            ),
            "device": self.device,
            "clock_period_ns": self.clock_period_ns,
            "compile_flags": list(
                self.compile_flags
            ),
            "executable": self.executable,
            "settings_path": self.settings_path,
            "parser_profile": self.parser_profile,
            "resource_limits": (
                self.resource_limits.to_dict()
            ),
        }

    def to_effective_dict(self) -> dict[str, Any]:
        """Return effective values with per-field provenance."""

        return {
            "schema_version": 2,
            "profile": self.to_dict(),
            "field_provenance": dict(
                self.field_provenance
            ),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "TargetProfile":
        if not isinstance(data, Mapping):
            raise TypeError(
                "TargetProfile data must be a mapping"
            )
        return cls(
            name=data["name"],
            toolchain=data["toolchain"],
            toolchain_version=data.get(
                "toolchain_version"
            ),
            device=data.get("device"),
            clock_period_ns=float(
                data.get("clock_period_ns", 5.0)
            ),
            compile_flags=tuple(
                data.get("compile_flags", ())
            ),
            executable=data.get("executable"),
            settings_path=data.get("settings_path"),
            parser_profile=data.get(
                "parser_profile",
                "vitis-hls-generic",
            ),
            resource_limits=(
                TargetResourceLimits.from_dict(
                    data.get("resource_limits")
                )
            ),
            field_provenance=data.get(
                "field_provenance",
                {},
            ),
        )


def default_target_profile() -> TargetProfile:
    """Return the committed profile preserving legacy Vitis defaults."""

    return _resolve_named_profile(
        DEFAULT_TARGET_PROFILE_NAME
    )


def resolve_target_profile(
    value: TargetProfile | Mapping[str, Any] | str | None,
) -> TargetProfile:
    """Resolve a committed profile plus explicit task overrides."""

    if isinstance(value, TargetProfile):
        return value
    if value is None:
        return default_target_profile()
    if isinstance(value, str):
        return _resolve_named_profile(value)
    if not isinstance(value, Mapping):
        raise TypeError(
            "target must be a TargetProfile, profile name, "
            "mapping, or null"
        )

    unknown_fields = (
        set(value)
        - _ALLOWED_TARGET_OVERRIDE_FIELDS
    )
    if unknown_fields:
        names = ", ".join(
            sorted(unknown_fields)
        )
        raise ValueError(
            f"Unknown target profile fields: {names}"
        )

    profile_name = value.get(
        "profile",
        DEFAULT_TARGET_PROFILE_NAME,
    )
    if not isinstance(profile_name, str):
        raise TypeError(
            "target.profile must be a string"
        )
    base = _resolve_named_profile(profile_name)
    provenance = dict(base.field_provenance)

    period = _resolve_clock_period(
        value,
        base.clock_period_ns,
    )
    if "clock_frequency_mhz" in value:
        provenance["clock_period_ns"] = (
            "task_override:clock_frequency_mhz"
        )
    elif "clock_period_ns" in value:
        provenance["clock_period_ns"] = (
            "task_override:clock_period_ns"
        )

    if "compile_flags" in value:
        compile_flags = _normalize_flags(
            value["compile_flags"],
            "compile_flags",
        )
        provenance["compile_flags"] = (
            "task_override:compile_flags"
        )
    else:
        compile_flags = base.compile_flags

    if "append_compile_flags" in value:
        compile_flags += _normalize_flags(
            value["append_compile_flags"],
            "append_compile_flags",
        )
        provenance["compile_flags"] = (
            "task_override:append_compile_flags"
        )

    resource_limits = (
        _resolve_resource_limits(
            value.get("resource_limits"),
            base.resource_limits,
            provenance,
        )
        if "resource_limits" in value
        else base.resource_limits
    )

    simple_values = {
        "name": value.get("name", base.name),
        "toolchain": value.get(
            "toolchain",
            base.toolchain,
        ),
        "toolchain_version": value.get(
            "toolchain_version",
            base.toolchain_version,
        ),
        "device": value.get(
            "device",
            base.device,
        ),
        "executable": value.get(
            "executable",
            base.executable,
        ),
        "settings_path": value.get(
            "settings_path",
            base.settings_path,
        ),
        "parser_profile": value.get(
            "parser_profile",
            base.parser_profile,
        ),
    }
    for field_name in simple_values:
        if field_name in value:
            provenance[field_name] = (
                f"task_override:{field_name}"
            )

    return TargetProfile(
        name=simple_values["name"],
        toolchain=simple_values["toolchain"],
        toolchain_version=(
            simple_values["toolchain_version"]
        ),
        device=simple_values["device"],
        clock_period_ns=period,
        compile_flags=compile_flags,
        executable=simple_values["executable"],
        settings_path=simple_values[
            "settings_path"
        ],
        parser_profile=simple_values[
            "parser_profile"
        ],
        resource_limits=resource_limits,
        field_provenance=provenance,
    )


def _resolve_named_profile(
    name: str,
) -> TargetProfile:
    canonical, data, source = (
        load_named_target_profile(name)
    )
    unknown = set(data) - _PROFILE_JSON_FIELDS
    if unknown:
        raise ValueError(
            "Unknown committed target profile fields: "
            + ", ".join(sorted(unknown))
        )

    provenance = {
        field_name: (
            f"named_profile:{canonical}|{source}"
        )
        for field_name in _PROFILE_FIELDS
    }
    for resource_name in _RESOURCE_FIELDS:
        provenance[
            f"resource_limits.{resource_name}"
        ] = (
            f"named_profile:{canonical}|{source}"
        )

    return TargetProfile(
        name=data["name"],
        toolchain=data["toolchain"],
        toolchain_version=data.get(
            "toolchain_version"
        ),
        device=data.get("device"),
        clock_period_ns=float(
            data.get("clock_period_ns", 5.0)
        ),
        compile_flags=tuple(
            data.get("compile_flags", ())
        ),
        executable=data.get("executable"),
        settings_path=data.get(
            "settings_path"
        ),
        parser_profile=data.get(
            "parser_profile",
            "vitis-hls-generic",
        ),
        resource_limits=(
            TargetResourceLimits.from_dict(
                data.get("resource_limits")
            )
        ),
        field_provenance=provenance,
    )


def _resolve_clock_period(
    data: Mapping[str, Any],
    default_period_ns: float,
) -> float:
    has_period = "clock_period_ns" in data
    has_frequency = "clock_frequency_mhz" in data
    period = (
        _positive_number(
            data["clock_period_ns"],
            "clock_period_ns",
        )
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
            "clock_period_ns conflicts with "
            "clock_frequency_mhz"
        )
    return converted_period


def _resolve_resource_limits(
    value: Any,
    base: TargetResourceLimits,
    provenance: dict[str, str],
) -> TargetResourceLimits:
    if value is None:
        for name in _RESOURCE_FIELDS:
            provenance[
                f"resource_limits.{name}"
            ] = "task_override:resource_limits"
        return TargetResourceLimits()
    if not isinstance(value, Mapping):
        raise TypeError(
            "resource_limits must be a mapping or null"
        )
    unknown = set(value) - set(_RESOURCE_FIELDS)
    if unknown:
        raise ValueError(
            "Unknown resource limit fields: "
            + ", ".join(sorted(unknown))
        )
    merged = base.to_dict()
    for name, item in value.items():
        merged[name] = item
        provenance[
            f"resource_limits.{name}"
        ] = (
            f"task_override:resource_limits.{name}"
        )
    return TargetResourceLimits.from_dict(merged)


def _normalize_provenance(
    value: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(
            "field_provenance must be a mapping"
        )
    if not value:
        return {
            name: "direct_constructor"
            for name in _PROVENANCE_FIELDS
        }

    unknown = set(value) - set(
        _PROVENANCE_FIELDS
    )
    if unknown:
        raise ValueError(
            "Unknown provenance fields: "
            + ", ".join(sorted(unknown))
        )
    missing = set(_PROVENANCE_FIELDS) - set(
        value
    )
    if missing:
        raise ValueError(
            "Missing provenance fields: "
            + ", ".join(sorted(missing))
        )

    normalized: dict[str, str] = {}
    for name in _PROVENANCE_FIELDS:
        item = value[name]
        if not isinstance(item, str):
            raise TypeError(
                "provenance values must be strings"
            )
        cleaned = item.strip()
        if not cleaned:
            raise ValueError(
                "provenance values must not be empty"
            )
        normalized[name] = cleaned
    return normalized


def _positive_number(
    value: Any,
    field_name: str,
) -> float:
    if isinstance(value, bool):
        raise TypeError(
            f"{field_name} must be a number"
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"{field_name} must be a number"
        ) from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(
            f"{field_name} must be a finite "
            "positive number"
        )
    return number


def _normalize_flags(
    value: Any,
    field_name: str,
) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
    ):
        raise TypeError(
            f"{field_name} must be a sequence "
            "of strings"
        )
    return tuple(
        _required_text(item, field_name)
        for item in value
    )


def _required_text(
    value: Any,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"target.{field_name} must be a string"
        )
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(
            f"target.{field_name} must not be empty"
        )
    return cleaned


def _optional_text(
    value: Any,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"target.{field_name} must be a "
            "string or null"
        )
    cleaned = value.strip()
    return cleaned or None


def _optional_launcher_text(
    value: Any,
    field_name: str,
) -> str | None:
    cleaned = _optional_text(
        value,
        field_name,
    )
    if cleaned is None:
        return None
    if any(
        character in cleaned
        for character in ("\x00", "\r", "\n")
    ):
        raise ValueError(
            f"target.{field_name} must not contain "
            "NUL or newline characters"
        )
    return cleaned


def _optional_settings_path(
    value: Any,
) -> str | None:
    cleaned = _optional_launcher_text(
        value,
        "settings_path",
    )
    if cleaned is None:
        return None
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise ValueError(
            "target.settings_path must be an "
            "absolute path"
        )
    return str(path)
