"""CWD-local .env loading and secret-free credential evidence."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import dotenv_values
except Exception as exc:  # pragma: no cover - dependency diagnostic.
    dotenv_values = None
    _DOTENV_IMPORT_ERROR = exc
else:
    _DOTENV_IMPORT_ERROR = None


@dataclass(frozen=True, slots=True)
class InvocationEnvironmentEvidence:
    invocation_cwd: str
    dotenv_path: str
    dotenv_present: bool
    dotenv_loaded: bool
    override: bool
    loaded_variable_count: int
    preserved_process_variable_count: int
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "invocation_cwd": self.invocation_cwd,
            "dotenv_path": self.dotenv_path,
            "dotenv_present": self.dotenv_present,
            "dotenv_loaded": self.dotenv_loaded,
            "override": self.override,
            "loaded_variable_count": self.loaded_variable_count,
            "preserved_process_variable_count": (
                self.preserved_process_variable_count
            ),
            "secret_values_persisted": False,
            "dotenv_contents_persisted": False,
        }


def load_invocation_dotenv(
    cwd: str | os.PathLike[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> InvocationEnvironmentEvidence:
    root = Path.cwd() if cwd is None else Path(cwd).expanduser().resolve()
    env = os.environ if environ is None else environ
    path = root / ".env"
    present = path.exists()
    if present and (path.is_symlink() or not path.is_file()):
        raise ValueError("invocation .env must be a regular non-symlink file")

    loaded = 0
    preserved = 0
    parsed = False
    if present:
        if dotenv_values is None:
            raise RuntimeError(
                "python-dotenv is required for local .env loading"
            ) from _DOTENV_IMPORT_ERROR
        values = dotenv_values(path)
        parsed = True
        for raw_name, raw_value in values.items():
            if (
                not isinstance(raw_name, str)
                or not raw_name
                or raw_value is None
            ):
                continue
            if raw_name in env:
                preserved += 1
                continue
            env[raw_name] = str(raw_value)
            loaded += 1

    return InvocationEnvironmentEvidence(
        invocation_cwd=str(root),
        dotenv_path=str(path),
        dotenv_present=present,
        dotenv_loaded=parsed,
        override=False,
        loaded_variable_count=loaded,
        preserved_process_variable_count=preserved,
    )


def credential_presence_evidence(
    api_key_env: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise ValueError("api_key_env must not be empty")
    name = api_key_env.strip()
    env = os.environ if environ is None else environ
    value = env.get(name)
    return {
        "schema_version": 1,
        "api_key_env": name,
        "credential_present": bool(isinstance(value, str) and value),
        "credential_value_persisted": False,
        "credential_length_persisted": False,
    }
