"""Typed candidate-only payloads consumed by the existing repair prompt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from .episode_ledger import canonical_sha256
from .pattern_lifecycle import Lifecycle, R5PatternRevision


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN = ("hidden", "original", "testbench", "target", "configuration", "private", "reasoning", "raw_provider", "future_outcome", "success")


class MemoryPayloadError(ValueError):
    """Raised when a memory payload violates the candidate-only boundary."""


R5_MEMORY_PAYLOAD_POLICY_SHA256 = canonical_sha256(
    {
        "schema_version": "r5-memory-payload-policy-v1",
        "candidate_only_scope": True,
        "agent_safe_only": True,
        "snapshot_instance_bound_by_authorization": True,
        "final_payload_manifest_bound_by_authorization": True,
    }
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryPayloadError(f"{field} must be non-empty text")
    return value.strip()


def _digest(value: Any, field: str) -> str:
    value = _text(value, field).lower()
    if not _SHA256.fullmatch(value):
        raise MemoryPayloadError(f"{field} must be SHA-256")
    return value


def _safe(value: Any, path: str = "root") -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            key_text = _text(key, f"{path}.key")
            lowered = key_text.casefold().replace("-", "_")
            if any(token in lowered for token in _FORBIDDEN):
                raise MemoryPayloadError(f"forbidden payload field: {path}.{key_text}")
            result[key_text] = _safe(child, f"{path}.{key_text}")
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, f"{path}[]") for item in value]
    if isinstance(value, str):
        lowered = value.casefold()
        if any(token in lowered for token in ("<think", "<reasoning", "private reasoning")):
            raise MemoryPayloadError(f"private reasoning marker at {path}")
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    raise MemoryPayloadError(f"unsupported payload value at {path}")


@dataclass(frozen=True, slots=True)
class R5MemoryPayload:
    snippet_id: str
    revision_id: str
    revision_sha256: str
    snapshot_sha256: str
    repair_intent_or_recipe: str
    supported_when: Mapping[str, Any]
    avoid_when: Mapping[str, Any]
    candidate_only_scope: bool
    source_episode_hashes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    payload_sha256: str = ""
    schema_version: str = "r5-memory-payload-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "snippet_id", _text(self.snippet_id, "snippet_id"))
        object.__setattr__(self, "revision_id", _text(self.revision_id, "revision_id"))
        object.__setattr__(self, "revision_sha256", _digest(self.revision_sha256, "revision_sha256"))
        object.__setattr__(self, "snapshot_sha256", _digest(self.snapshot_sha256, "snapshot_sha256"))
        object.__setattr__(self, "repair_intent_or_recipe", _text(self.repair_intent_or_recipe, "repair_intent_or_recipe"))
        if self.candidate_only_scope is not True:
            raise MemoryPayloadError("candidate_only_scope must be true")
        for name in ("supported_when", "avoid_when"):
            object.__setattr__(self, name, _safe(getattr(self, name), name))
        for name in ("source_episode_hashes", "evidence_refs"):
            values = tuple(_text(item, name) for item in getattr(self, name))
            if len(values) != len(set(values)):
                raise MemoryPayloadError(f"{name} must be unique")
            object.__setattr__(self, name, values)
        expected = canonical_sha256(self.to_dict(include_hash=False))
        if self.payload_sha256 and self.payload_sha256 != expected:
            raise MemoryPayloadError("payload_sha256 mismatch")
        object.__setattr__(self, "payload_sha256", expected)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "snippet_id": self.snippet_id,
            "revision_id": self.revision_id,
            "revision_sha256": self.revision_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "repair_intent_or_recipe": self.repair_intent_or_recipe,
            "supported_when": self.supported_when,
            "avoid_when": self.avoid_when,
            "candidate_only_scope": self.candidate_only_scope,
            "source_episode_hashes": list(self.source_episode_hashes),
            "evidence_refs": list(self.evidence_refs),
        }
        if include_hash:
            value["payload_sha256"] = self.payload_sha256
        return value

    @classmethod
    def from_revision(
        cls,
        revision: R5PatternRevision,
        *,
        snapshot_sha256: str,
        repair_intent_or_recipe: str,
        source_episode_hashes: Sequence[str],
        evidence_refs: Sequence[str],
    ) -> "R5MemoryPayload":
        if revision.lifecycle not in {Lifecycle.PROVISIONAL, Lifecycle.TRUSTED}:
            raise MemoryPayloadError("only provisional or trusted revisions may produce payload")
        return cls(
            snippet_id=f"{revision.revision_id}:candidate",
            revision_id=revision.revision_id,
            revision_sha256=revision.revision_sha256,
            snapshot_sha256=snapshot_sha256,
            repair_intent_or_recipe=repair_intent_or_recipe,
            supported_when=revision.supported_when,
            avoid_when={**dict(revision.avoid_when), **dict(revision.exact_exclusions)},
            candidate_only_scope=True,
            source_episode_hashes=tuple(source_episode_hashes),
            evidence_refs=tuple(evidence_refs),
        )


def render_candidate_memory_snippets(payloads: Sequence[R5MemoryPayload]) -> tuple[str, ...]:
    """Render bounded, agent-safe text for the existing prompt hook."""
    snippets = []
    for payload in payloads:
        if not isinstance(payload, R5MemoryPayload):
            raise TypeError("payloads must contain R5MemoryPayload")
        snippets.append(json.dumps({
            "snippet_id": payload.snippet_id,
            "revision_id": payload.revision_id,
            "supported_when": payload.supported_when,
            "avoid_when": payload.avoid_when,
            "repair_intent_or_recipe": payload.repair_intent_or_recipe,
            "candidate_only_scope": True,
            "payload_sha256": payload.payload_sha256,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return tuple(snippets)


def memory_payload_manifest_sha256(payloads: Sequence[R5MemoryPayload]) -> str:
    """Return the canonical order-independent manifest for prompt payloads."""

    values = []
    for payload in payloads:
        if not isinstance(payload, R5MemoryPayload):
            raise TypeError("payloads must contain R5MemoryPayload")
        values.append(payload.payload_sha256)
    return canonical_sha256(
        {
            "schema_version": "r5-payload-manifest-v1",
            "payloads": sorted(values),
        }
    )


__all__ = ["MemoryPayloadError", "R5MemoryPayload", "R5_MEMORY_PAYLOAD_POLICY_SHA256", "memory_payload_manifest_sha256", "render_candidate_memory_snippets"]
