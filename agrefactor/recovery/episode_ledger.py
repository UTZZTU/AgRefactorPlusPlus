"""Append-only, file-backed R5 episode ledger.

The ledger is deliberately independent from the R3/R4 in-memory stores.  It
accepts only agent-safe, identity-bound summaries and makes invalid records
unusable for lifecycle reduction.  It never decides whether a repair
succeeded; callers must provide the deterministic/audited outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEY_PARTS = (
    "hidden", "secret", "oracle", "private_reasoning", "raw_provider",
    "raw_response", "future_outcome", "source_content", "password",
    "api_key", "credential",
)


class EpisodeLedgerError(ValueError):
    """Raised when an episode cannot enter the append-only ledger."""


class R5EpisodeOutcome(str, Enum):
    VERIFIED_POSITIVE = "verified_positive"
    VERIFIED_NEGATIVE = "verified_negative"
    ABSTAINED = "abstained"
    INCONCLUSIVE = "inconclusive"
    INVALID_EVIDENCE = "invalid_evidence"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EpisodeLedgerError(f"{field} must be non-empty text")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    value = _text(value, field).lower()
    if not _SHA256.fullmatch(value):
        raise EpisodeLedgerError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _safe_copy(value: Any, path: str = "root") -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            key_text = _text(key, f"{path}.key")
            lowered = key_text.casefold().replace("-", "_")
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise EpisodeLedgerError(f"forbidden field at {path}.{key_text}")
            result[key_text] = _safe_copy(child, f"{path}.{key_text}")
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_copy(item, f"{path}[]") for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            lowered = value.casefold()
            if any(tag in lowered for tag in ("<think", "<reasoning", "private reasoning")):
                raise EpisodeLedgerError(f"private reasoning marker at {path}")
        return value
    raise EpisodeLedgerError(f"unsupported value at {path}")


def _iso(value: Any, field: str) -> str:
    value = _text(value, field)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EpisodeLedgerError(f"{field} must be ISO-8601") from exc
    return value


@dataclass(frozen=True, slots=True)
class R5EpisodeEnvelope:
    """Immutable, content-addressed episode envelope."""

    episode_kind: str
    episode_id: str
    payload_schema_version: str
    payload: Mapping[str, Any]
    execution_identity_sha256: str
    source_sha256: str
    context_signature: str
    created_at: str
    observed_at: str
    lineage: tuple[str, ...]
    agent_safe_summary: Mapping[str, Any]
    outcome: R5EpisodeOutcome
    manifest_sha256: str
    payload_sha256: str = ""
    schema_version: int = 1
    envelope_sha256: str = ""

    def __post_init__(self) -> None:
        kind = _text(self.episode_kind, "episode_kind")
        if kind not in {"r3_shadow", "r4_repair"}:
            raise EpisodeLedgerError("episode_kind must be r3_shadow or r4_repair")
        object.__setattr__(self, "episode_kind", kind)
        object.__setattr__(self, "episode_id", _text(self.episode_id, "episode_id"))
        object.__setattr__(self, "payload_schema_version", _text(self.payload_schema_version, "payload_schema_version"))
        payload = _safe_copy(self.payload, "payload")
        summary = _safe_copy(self.agent_safe_summary, "agent_safe_summary")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "agent_safe_summary", summary)
        payload_hash = canonical_sha256(payload)
        if self.payload_sha256 and self.payload_sha256 != payload_hash:
            raise EpisodeLedgerError("payload_sha256 mismatch")
        object.__setattr__(self, "payload_sha256", payload_hash)
        object.__setattr__(self, "execution_identity_sha256", _sha(self.execution_identity_sha256, "execution_identity_sha256"))
        object.__setattr__(self, "source_sha256", _sha(self.source_sha256, "source_sha256"))
        object.__setattr__(self, "context_signature", _sha(self.context_signature, "context_signature"))
        object.__setattr__(self, "manifest_sha256", _sha(self.manifest_sha256, "manifest_sha256"))
        object.__setattr__(self, "created_at", _iso(self.created_at, "created_at"))
        object.__setattr__(self, "observed_at", _iso(self.observed_at, "observed_at"))
        lineage = tuple(_text(item, "lineage") for item in self.lineage)
        if len(lineage) != len(set(lineage)):
            raise EpisodeLedgerError("lineage must be unique")
        object.__setattr__(self, "lineage", lineage)
        object.__setattr__(self, "outcome", R5EpisodeOutcome(self.outcome))
        if self.schema_version != 1:
            raise EpisodeLedgerError("unsupported episode schema")
        expected = canonical_sha256(self.to_dict(include_hash=False))
        if self.envelope_sha256 and self.envelope_sha256 != expected:
            raise EpisodeLedgerError("envelope_sha256 mismatch")
        object.__setattr__(self, "envelope_sha256", expected)

    @property
    def eligible_for_reduction(self) -> bool:
        return self.outcome in {
            R5EpisodeOutcome.VERIFIED_POSITIVE,
            R5EpisodeOutcome.VERIFIED_NEGATIVE,
        }

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "episode_kind": self.episode_kind,
            "episode_id": self.episode_id,
            "payload_schema_version": self.payload_schema_version,
            "payload": self.payload,
            "payload_sha256": self.payload_sha256,
            "execution_identity_sha256": self.execution_identity_sha256,
            "source_sha256": self.source_sha256,
            "context_signature": self.context_signature,
            "created_at": self.created_at,
            "observed_at": self.observed_at,
            "lineage": list(self.lineage),
            "agent_safe_summary": self.agent_safe_summary,
            "outcome": self.outcome.value,
            "manifest_sha256": self.manifest_sha256,
        }
        if include_hash:
            value["envelope_sha256"] = self.envelope_sha256
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "R5EpisodeEnvelope":
        data = dict(value)
        data.pop("envelope_sha256", None)
        return cls(envelope_sha256=value.get("envelope_sha256", ""), **data)


class AppendOnlyEpisodeLedger:
    """Append-only ledger backed by one JSON file per episode."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, str] = {}
        for path in sorted(self.root.glob("*.json")):
            if path.name == "ledger_manifest.json":
                continue
            try:
                envelope = R5EpisodeEnvelope.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise EpisodeLedgerError(f"invalid ledger record: {path.name}") from exc
            if envelope.episode_id in self._index and self._index[envelope.episode_id] != envelope.envelope_sha256:
                raise EpisodeLedgerError("duplicate episode id with changed payload")
            self._index[envelope.episode_id] = envelope.envelope_sha256

    def append(self, envelope: R5EpisodeEnvelope) -> Path:
        if not isinstance(envelope, R5EpisodeEnvelope):
            raise TypeError("envelope must be R5EpisodeEnvelope")
        existing = self._index.get(envelope.episode_id)
        if existing is not None:
            if existing != envelope.envelope_sha256:
                raise EpisodeLedgerError("episode id already exists with changed payload")
            raise EpisodeLedgerError("episode id already exists")
        path = self.root / f"{envelope.episode_id}.json"
        if path.exists():
            raise EpisodeLedgerError("episode path already exists")
        path.write_text(json.dumps(envelope.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._index[envelope.episode_id] = envelope.envelope_sha256
        self._write_manifest()
        return path

    def get(self, episode_id: str) -> R5EpisodeEnvelope | None:
        path = self.root / f"{episode_id}.json"
        if not path.exists():
            return None
        return R5EpisodeEnvelope.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def records(self) -> tuple[R5EpisodeEnvelope, ...]:
        return tuple(self.get(episode_id) for episode_id in sorted(self._index))  # type: ignore[misc]

    def eligible_records(self, *, through: str | None = None) -> tuple[R5EpisodeEnvelope, ...]:
        cutoff = None if through is None else datetime.fromisoformat(through.replace("Z", "+00:00"))
        result = []
        for record in self.records():
            if not record.eligible_for_reduction:
                continue
            observed = datetime.fromisoformat(record.observed_at.replace("Z", "+00:00"))
            if cutoff is not None and observed > cutoff:
                raise EpisodeLedgerError("future record crossed snapshot boundary")
            result.append(record)
        return tuple(result)

    def _write_manifest(self) -> None:
        payload = {
            "schema_version": 1,
            "record_ids": sorted(self._index),
            "record_hashes": [self._index[item] for item in sorted(self._index)],
            "append_only": True,
        }
        payload["manifest_sha256"] = canonical_sha256(payload)
        (self.root / "ledger_manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "AppendOnlyEpisodeLedger", "EpisodeLedgerError", "R5EpisodeEnvelope",
    "R5EpisodeOutcome", "canonical_sha256",
]
