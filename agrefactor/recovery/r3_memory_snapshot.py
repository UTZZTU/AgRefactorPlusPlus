"""Frozen R3 memory consumer for the explicit R4 experiment seam.

The snapshot is immutable and self-identifying.  It is the only source from
which the R4 integration may obtain a pattern revision and Gate context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .memory_gate import DiagnosticEpisode, PatternLifecycle, RepairPatternRevision


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    snapshot_id: str
    frozen_at: str
    episode_ids: tuple[str, ...]
    revisions: tuple[RepairPatternRevision, ...]
    gate_context: Mapping[str, Any]
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty")
        if not isinstance(self.frozen_at, str) or not self.frozen_at.strip():
            raise ValueError("frozen_at must be non-empty")
        ids = tuple(self.episode_ids)
        if len(ids) != len(set(ids)) or any(not isinstance(item, str) or not item.strip() for item in ids):
            raise ValueError("episode_ids must be unique non-empty strings")
        revs = tuple(self.revisions)
        if any(not isinstance(item, RepairPatternRevision) for item in revs):
            raise TypeError("revisions must contain RepairPatternRevision")
        if len({item.revision_id for item in revs}) != len(revs):
            raise ValueError("revision ids must be unique")
        if not isinstance(self.gate_context, Mapping):
            raise TypeError("gate_context must be a mapping")
        expected = _sha({"snapshot_id": self.snapshot_id, "frozen_at": self.frozen_at, "episode_ids": list(ids), "revision_hashes": [item.revision_hash for item in revs], "gate_context": self.gate_context})
        if self.source_sha256 != expected:
            raise ValueError("source_sha256 does not match frozen snapshot")
        object.__setattr__(self, "episode_ids", ids)
        object.__setattr__(self, "revisions", revs)
        object.__setattr__(self, "gate_context", _freeze(json.loads(json.dumps(dict(self.gate_context), sort_keys=True, allow_nan=False))))

    @classmethod
    def freeze(cls, *, episodes: Sequence[DiagnosticEpisode], revisions: Sequence[RepairPatternRevision], gate_context: Mapping[str, Any], frozen_at: str | None = None) -> "MemorySnapshot":
        episode_values = tuple(episodes)
        if any(not isinstance(item, DiagnosticEpisode) for item in episode_values):
            raise TypeError("episodes must contain DiagnosticEpisode")
        revision_values = tuple(revisions)
        timestamp = frozen_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot_id = "r3-snapshot-" + _sha({"episode_ids": [item.episode_id for item in episode_values], "revision_hashes": [item.revision_hash for item in revision_values], "frozen_at": timestamp})[:32]
        payload = {"snapshot_id": snapshot_id, "frozen_at": timestamp, "episode_ids": [item.episode_id for item in episode_values], "revision_hashes": [item.revision_hash for item in revision_values], "gate_context": dict(gate_context)}
        return cls(snapshot_id=snapshot_id, frozen_at=timestamp, episode_ids=tuple(item.episode_id for item in episode_values), revisions=revision_values, gate_context=dict(gate_context), source_sha256=_sha(payload))

    def context_for(self, *, event: Mapping[str, Any], advisory: Mapping[str, Any]) -> dict[str, Any]:
        context = dict(self.gate_context)
        context.update({"snapshot_id": self.snapshot_id, "identity_complete": event.get("identity_complete", context.get("identity_complete")), "hidden_input_count": event.get("hidden_input_count", context.get("hidden_input_count", 0)), "secret_present": event.get("secret_present", context.get("secret_present", False)), "evidence_predicates": tuple(event.get("evidence_refs", ())), "advisory_id": advisory.get("advisory_id"), "stage": event.get("stage"), "owner": advisory.get("suspected_owner"), "failure_family": advisory.get("failure_class")})
        return context


class SnapshotRevisionSource:
    """Resolve only revisions frozen in one MemorySnapshot."""

    def __init__(self, snapshot: MemorySnapshot) -> None:
        if not isinstance(snapshot, MemorySnapshot):
            raise TypeError("snapshot must be MemorySnapshot")
        self.snapshot = snapshot

    def resolve(self, *, event: Mapping[str, Any], advisory: Mapping[str, Any]) -> RepairPatternRevision:
        stage = event.get("stage")
        owner = advisory.get("suspected_owner")
        candidates = []
        for revision in self.snapshot.revisions:
            supported = dict(revision.supported_when)
            if supported.get("stage") not in (None, stage):
                continue
            if supported.get("owner") not in (None, owner):
                continue
            candidates.append(revision)
        if len(candidates) != 1:
            raise LookupError("snapshot_revision_not_unique")
        return candidates[0]


__all__ = ["MemorySnapshot", "SnapshotRevisionSource"]
