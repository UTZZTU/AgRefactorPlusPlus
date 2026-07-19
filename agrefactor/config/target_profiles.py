"""Load the finite set of committed, secret-free target profiles."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Any


DEFAULT_TARGET_PROFILE_NAME = "vitis-2023.2-default"

_NAMED_PROFILE_FILES = {
    DEFAULT_TARGET_PROFILE_NAME: "vitis-2023.2-default.json",
}
_PROFILE_ALIASES = {
    "default": DEFAULT_TARGET_PROFILE_NAME,
    DEFAULT_TARGET_PROFILE_NAME: DEFAULT_TARGET_PROFILE_NAME,
}
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|bearer|credential|"
    r"password|refresh[_-]?token|secret|access[_-]?token)",
    re.IGNORECASE,
)


def target_profile_config_dir() -> Path:
    """Return the committed target-template directory."""

    return (
        Path(__file__).resolve().parents[2]
        / "configs"
        / "targets"
    )


def available_target_profile_names() -> tuple[str, ...]:
    """Return canonical committed profile names."""

    return tuple(sorted(_NAMED_PROFILE_FILES))


def resolve_named_target_profile_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("target profile name must be a string")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("target profile name must not be empty")
    canonical = _PROFILE_ALIASES.get(cleaned)
    if canonical is None:
        available = ", ".join(
            sorted(_PROFILE_ALIASES)
        )
        raise ValueError(
            f"Unknown target profile {name!r}; available: "
            f"{available}"
        )
    return canonical


def load_named_target_profile(
    name: str,
) -> tuple[str, dict[str, Any], str]:
    """Load one canonical JSON profile and return its source identity."""

    canonical = resolve_named_target_profile_name(name)
    filename = _NAMED_PROFILE_FILES[canonical]
    path = target_profile_config_dir() / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Committed target profile is missing: {path}"
        )

    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid target profile JSON: {path}"
        ) from exc

    if not isinstance(value, Mapping):
        raise TypeError(
            "Committed target profile must be a JSON object"
        )
    copied = _copy_json_mapping(value)
    if copied.get("schema_version") != 1:
        raise ValueError(
            "Committed target profile schema_version must be 1"
        )
    if copied.get("name") != canonical:
        raise ValueError(
            "Committed target profile name does not match "
            f"registry key: {canonical}"
        )
    _reject_secret_keys(copied, "target_profile")
    return (
        canonical,
        copied,
        f"committed_json:configs/targets/{filename}",
    )


def _copy_json_mapping(
    value: Mapping[str, Any],
) -> dict[str, Any]:
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
            "Target profile must contain finite JSON data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(
            "Target profile must normalize to an object"
        )
    return copied


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _SECRET_KEY_RE.search(key_text):
                raise ValueError(
                    "Target configuration must not contain "
                    f"credential-like key: {child_path}"
                )
            _reject_secret_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(
                child,
                f"{path}[{index}]",
            )
