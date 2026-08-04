"""Exact, secret-free validation cache identity and immutable evidence store."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from agrefactor.runtime.execution_identity import canonical_json_sha256


CACHE_SCHEMA_VERSION = 1
VALIDATION_PIPELINE_VERSION = (
    "prestage4-public-rtl-cosim-v1"
)
VALIDATION_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)
_UNSAFE_KEY_RE = re.compile(
    r"(?:hidden_(?:testbench|diagnostic|plaintext|source)|"
    r"operator_full_payload|raw_diagnostic|raw_testbench)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SuiteIdentity:
    suite_id: str
    split: str
    content_sha256: str
    suite_version: str | None = None
    source_identity_sha256: str | None = None

    schema_version = CACHE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        suite_id = _required_id(self.suite_id, "suite_id")
        split = _required_id(self.split, "split")
        if split not in {"public", "hidden"}:
            raise ValueError("split must be public or hidden")
        content_sha = _sha256(self.content_sha256, "content_sha256")
        version = _optional_text(self.suite_version, "suite_version")
        source_sha = (
            None
            if self.source_identity_sha256 is None
            else _sha256(
                self.source_identity_sha256,
                "source_identity_sha256",
            )
        )
        object.__setattr__(self, "suite_id", suite_id)
        object.__setattr__(self, "split", split)
        object.__setattr__(self, "content_sha256", content_sha)
        object.__setattr__(self, "suite_version", version)
        object.__setattr__(self, "source_identity_sha256", source_sha)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "split": self.split,
            "content_sha256": self.content_sha256,
            "suite_version": self.suite_version,
            "source_identity_sha256": self.source_identity_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SuiteIdentity":
        payload = _strict_payload(
            value,
            {
                "schema_version",
                "suite_id",
                "split",
                "content_sha256",
                "suite_version",
                "source_identity_sha256",
            },
            "suite identity",
        )
        return cls(
            suite_id=payload["suite_id"],
            split=payload["split"],
            content_sha256=payload["content_sha256"],
            suite_version=payload["suite_version"],
            source_identity_sha256=payload["source_identity_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ValidationCacheIdentity:
    """All frozen Stage 3 fields required for exact validation reuse."""

    source_sha256: str
    target_identity_sha256: str
    toolchain_fingerprint_sha256: str
    public_suites: tuple[SuiteIdentity, ...]
    hidden_suites: tuple[SuiteIdentity, ...]
    validation_pipeline_version: str
    validation_schema_version: int
    compile_flags: tuple[str, ...]
    clock_period_ns: float
    device: str
    parser_profile: str
    comparison_context_identity_sha256: str
    cache_key_sha256: str

    schema_version = CACHE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        source_sha = _sha256(self.source_sha256, "source_sha256")
        target_sha = _sha256(
            self.target_identity_sha256,
            "target_identity_sha256",
        )
        toolchain_sha = _sha256(
            self.toolchain_fingerprint_sha256,
            "toolchain_fingerprint_sha256",
        )
        public = _suite_tuple(self.public_suites, "public_suites", "public")
        hidden = _suite_tuple(self.hidden_suites, "hidden_suites", "hidden")
        pipeline = _required_id(
            self.validation_pipeline_version,
            "validation_pipeline_version",
        )
        schema_version = _positive_int(
            self.validation_schema_version,
            "validation_schema_version",
        )
        flags = _text_tuple(self.compile_flags, "compile_flags")
        clock = _positive_float(self.clock_period_ns, "clock_period_ns")
        device = _required_text(self.device, "device")
        parser = _required_id(self.parser_profile, "parser_profile")
        comparison_sha = _sha256(
            self.comparison_context_identity_sha256,
            "comparison_context_identity_sha256",
        )
        cache_sha = _sha256(self.cache_key_sha256, "cache_key_sha256")

        object.__setattr__(self, "source_sha256", source_sha)
        object.__setattr__(self, "target_identity_sha256", target_sha)
        object.__setattr__(
            self,
            "toolchain_fingerprint_sha256",
            toolchain_sha,
        )
        object.__setattr__(self, "public_suites", public)
        object.__setattr__(self, "hidden_suites", hidden)
        object.__setattr__(self, "validation_pipeline_version", pipeline)
        object.__setattr__(self, "validation_schema_version", schema_version)
        object.__setattr__(self, "compile_flags", flags)
        object.__setattr__(self, "clock_period_ns", clock)
        object.__setattr__(self, "device", device)
        object.__setattr__(self, "parser_profile", parser)
        object.__setattr__(
            self,
            "comparison_context_identity_sha256",
            comparison_sha,
        )
        object.__setattr__(self, "cache_key_sha256", cache_sha)

        expected_context = canonical_json_sha256(
            self._comparison_material()
        )
        if expected_context != comparison_sha:
            raise ValueError(
                "comparison_context_identity_sha256 does not match fields"
            )
        expected_key = canonical_json_sha256(self._key_material())
        if expected_key != cache_sha:
            raise ValueError("cache_key_sha256 does not match identity fields")

    @classmethod
    def build(
        cls,
        *,
        source_sha256: str,
        effective_target: Mapping[str, Any],
        toolchain_fingerprint_sha256: str,
        suites: Sequence[SuiteIdentity | Mapping[str, Any]],
        compile_flags: Sequence[str],
        clock_period_ns: float,
        device: str,
        parser_profile: str,
        validation_pipeline_version: str = VALIDATION_PIPELINE_VERSION,
        validation_schema_version: int = VALIDATION_SCHEMA_VERSION,
    ) -> "ValidationCacheIdentity":
        normalized_suites = tuple(
            item
            if isinstance(item, SuiteIdentity)
            else SuiteIdentity.from_dict(item)
            for item in suites
        )
        public = tuple(
            sorted(
                (item for item in normalized_suites if item.split == "public"),
                key=lambda item: (item.suite_id, item.suite_version or ""),
            )
        )
        hidden = tuple(
            sorted(
                (item for item in normalized_suites if item.split == "hidden"),
                key=lambda item: (item.suite_id, item.suite_version or ""),
            )
        )
        target_value = _finite_json_mapping(
            effective_target,
            "effective_target",
            reject_secrets=True,
        )
        target_sha = canonical_json_sha256(target_value)
        base = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "target_identity_sha256": target_sha,
            "toolchain_fingerprint_sha256": _sha256(
                toolchain_fingerprint_sha256,
                "toolchain_fingerprint_sha256",
            ),
            "public_suites": [item.to_dict() for item in public],
            "hidden_suites": [item.to_dict() for item in hidden],
            "compile_flags": list(
                _text_tuple(compile_flags, "compile_flags")
            ),
            "clock_period_ns": _positive_float(
                clock_period_ns,
                "clock_period_ns",
            ),
            "device": _required_text(device, "device"),
            "parser_profile": _required_id(
                parser_profile,
                "parser_profile",
            ),
            "validation_pipeline_version": _required_id(
                validation_pipeline_version,
                "validation_pipeline_version",
            ),
            "validation_schema_version": _positive_int(
                validation_schema_version,
                "validation_schema_version",
            ),
        }
        context_material = dict(base)
        context_material.pop("schema_version")
        comparison_sha = canonical_json_sha256(context_material)
        key_material = {
            **base,
            "source_sha256": _sha256(source_sha256, "source_sha256"),
            "comparison_context_identity_sha256": comparison_sha,
        }
        cache_sha = canonical_json_sha256(key_material)
        return cls(
            source_sha256=key_material["source_sha256"],
            target_identity_sha256=target_sha,
            toolchain_fingerprint_sha256=base[
                "toolchain_fingerprint_sha256"
            ],
            public_suites=public,
            hidden_suites=hidden,
            validation_pipeline_version=base[
                "validation_pipeline_version"
            ],
            validation_schema_version=base[
                "validation_schema_version"
            ],
            compile_flags=tuple(base["compile_flags"]),
            clock_period_ns=base["clock_period_ns"],
            device=base["device"],
            parser_profile=base["parser_profile"],
            comparison_context_identity_sha256=comparison_sha,
            cache_key_sha256=cache_sha,
        )

    def _comparison_material(self) -> dict[str, Any]:
        return {
            "target_identity_sha256": self.target_identity_sha256,
            "toolchain_fingerprint_sha256": (
                self.toolchain_fingerprint_sha256
            ),
            "public_suites": [item.to_dict() for item in self.public_suites],
            "hidden_suites": [item.to_dict() for item in self.hidden_suites],
            "compile_flags": list(self.compile_flags),
            "clock_period_ns": self.clock_period_ns,
            "device": self.device,
            "parser_profile": self.parser_profile,
            "validation_pipeline_version": self.validation_pipeline_version,
            "validation_schema_version": self.validation_schema_version,
        }

    def _key_material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            **self._comparison_material(),
            "source_sha256": self.source_sha256,
            "comparison_context_identity_sha256": (
                self.comparison_context_identity_sha256
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._key_material(),
            "cache_key_sha256": self.cache_key_sha256,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ValidationCacheIdentity":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "source_sha256",
                "target_identity_sha256",
                "toolchain_fingerprint_sha256",
                "public_suites",
                "hidden_suites",
                "validation_pipeline_version",
                "validation_schema_version",
                "compile_flags",
                "clock_period_ns",
                "device",
                "parser_profile",
                "comparison_context_identity_sha256",
                "cache_key_sha256",
            },
            "validation cache identity",
        )
        return cls(
            source_sha256=value["source_sha256"],
            target_identity_sha256=value["target_identity_sha256"],
            toolchain_fingerprint_sha256=value[
                "toolchain_fingerprint_sha256"
            ],
            public_suites=tuple(
                SuiteIdentity.from_dict(item)
                for item in value["public_suites"]
            ),
            hidden_suites=tuple(
                SuiteIdentity.from_dict(item)
                for item in value["hidden_suites"]
            ),
            validation_pipeline_version=value[
                "validation_pipeline_version"
            ],
            validation_schema_version=value[
                "validation_schema_version"
            ],
            compile_flags=tuple(value["compile_flags"]),
            clock_period_ns=value["clock_period_ns"],
            device=value["device"],
            parser_profile=value["parser_profile"],
            comparison_context_identity_sha256=value[
                "comparison_context_identity_sha256"
            ],
            cache_key_sha256=value["cache_key_sha256"],
        )


class QualificationEvidenceCache:
    """Immutable atomic cache for safe qualification/tool evidence only."""

    schema_version = CACHE_SCHEMA_VERSION

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = _prepare_root(root)

    @property
    def root(self) -> Path:
        return self._root

    def load(
        self,
        identity: ValidationCacheIdentity,
    ) -> dict[str, Any] | None:
        if not isinstance(identity, ValidationCacheIdentity):
            raise TypeError("identity must be ValidationCacheIdentity")
        path = self._entry_path(identity.cache_key_sha256)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ValueError("cache entry must be a regular file")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("cache entry is not valid JSON") from exc
        if not isinstance(raw, Mapping):
            raise TypeError("cache entry must contain a JSON object")
        allowed = {
            "schema_version",
            "cache_key_sha256",
            "identity",
            "evidence_sha256",
            "evidence",
        }
        if set(raw) != allowed:
            raise ValueError("cache entry fields do not match schema")
        if raw["schema_version"] != self.schema_version:
            raise ValueError("unsupported cache entry schema_version")
        if raw["cache_key_sha256"] != identity.cache_key_sha256:
            raise ValueError("cache entry key does not match requested identity")
        stored_identity = ValidationCacheIdentity.from_dict(raw["identity"])
        if stored_identity != identity:
            raise ValueError("cache entry identity does not match request")
        evidence = _finite_json_mapping(
            raw["evidence"],
            "cached evidence",
            reject_secrets=True,
            reject_hidden_payload=True,
        )
        if canonical_json_sha256(evidence) != raw["evidence_sha256"]:
            raise ValueError("cache evidence hash does not match")
        return evidence

    def store(
        self,
        identity: ValidationCacheIdentity,
        evidence: Mapping[str, Any],
    ) -> Path:
        if not isinstance(identity, ValidationCacheIdentity):
            raise TypeError("identity must be ValidationCacheIdentity")
        copied = _finite_json_mapping(
            evidence,
            "qualification evidence",
            reject_secrets=True,
            reject_hidden_payload=True,
        )
        payload = {
            "schema_version": self.schema_version,
            "cache_key_sha256": identity.cache_key_sha256,
            "identity": identity.to_dict(),
            "evidence_sha256": canonical_json_sha256(copied),
            "evidence": copied,
        }
        data = _json_bytes(payload)
        path = self._entry_path(identity.cache_key_sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("cache entry path is not a regular file")
            if path.read_bytes() != data:
                raise FileExistsError(
                    "cache entry already exists with different evidence"
                )
            return path
        _atomic_write(path, data)
        return path

    def _entry_path(self, key: str) -> Path:
        normalized = _sha256(key, "cache key")
        path = self._root / "entries" / normalized[:2] / f"{normalized}.json"
        parent = path.parent.resolve(strict=False)
        if not parent.is_relative_to(self._root):
            raise ValueError("cache path escaped cache root")
        return path


def suite_identity_from_file(
    *,
    suite_id: str,
    split: str,
    path: str | os.PathLike[str],
    suite_version: str | None = None,
    source_identity: Mapping[str, Any] | None = None,
) -> SuiteIdentity:
    file_path = Path(path).expanduser()
    if file_path.is_symlink() or not file_path.is_file():
        raise FileNotFoundError(f"suite file not found or unsafe: {file_path}")
    data = file_path.read_bytes()
    source_sha = (
        None
        if source_identity is None
        else canonical_json_sha256(
            _finite_json_mapping(
                source_identity,
                "suite source identity",
                reject_secrets=True,
            )
        )
    )
    return SuiteIdentity(
        suite_id=suite_id,
        split=split,
        content_sha256=sha256(data).hexdigest(),
        suite_version=suite_version,
        source_identity_sha256=source_sha,
    )


def build_toolchain_fingerprint(
    value: Mapping[str, Any],
) -> str:
    """Hash an observed, secret-free toolchain manifest."""

    manifest = _finite_json_mapping(
        value,
        "toolchain manifest",
        reject_secrets=True,
    )
    return canonical_json_sha256(manifest)


def _prepare_root(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("cache root must be a non-empty path")
    root = Path(raw).expanduser()
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValueError("cache root must be a real directory")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _atomic_write(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _suite_tuple(
    value: Sequence[SuiteIdentity],
    name: str,
    split: str,
) -> tuple[SuiteIdentity, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(value)
    if not all(isinstance(item, SuiteIdentity) for item in result):
        raise TypeError(f"{name} must contain SuiteIdentity values")
    if any(item.split != split for item in result):
        raise ValueError(f"{name} contains an incorrect split")
    sorted_result = tuple(
        sorted(result, key=lambda item: (item.suite_id, item.suite_version or ""))
    )
    ids = tuple(item.suite_id for item in sorted_result)
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name} suite IDs must be unique")
    return sorted_result


def _finite_json_mapping(
    value: Mapping[str, Any],
    name: str,
    *,
    reject_secrets: bool = False,
    reject_hidden_payload: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite JSON") from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    if reject_secrets:
        _reject_secret_keys(copied, name)
    if reject_hidden_payload:
        _reject_hidden_payload_keys(copied, name)
    return copied


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SECRET_KEYS:
                raise ValueError(f"secret key is forbidden in {path}: {key}")
            _reject_secret_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


def _reject_hidden_payload_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _UNSAFE_KEY_RE.search(str(key)):
                raise ValueError(
                    f"raw Hidden/operator payload key is forbidden in {path}: {key}"
                )
            _reject_hidden_payload_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_hidden_payload_keys(child, f"{path}[{index}]")


def _strict_payload(
    value: Mapping[str, Any],
    allowed: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} payload must be a mapping")
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown or missing:
        raise ValueError(
            f"{name} fields mismatch: unknown={sorted(unknown)} "
            f"missing={sorted(missing)}"
        )
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ValueError(f"unsupported {name} schema_version")
    return _finite_json_mapping(value, name)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _required_id(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} contains unsafe characters")
    return cleaned


def _sha256(value: Any, name: str) -> str:
    cleaned = _required_text(value, name).lower()
    if _SHA256_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return cleaned


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _text_tuple(value: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(_required_text(item, name) for item in value)
    return result
