"""History-only R5 memory snapshot builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .episode_ledger import R5EpisodeEnvelope, canonical_sha256
from .pattern_lifecycle import LifecycleReduction, R5LifecycleReducer


class SnapshotBoundaryError(ValueError):
    """Raised when a snapshot would include future or cross-source evidence."""


@dataclass(frozen=True, slots=True)
class R5MemorySnapshot:
    snapshot_id: str
    frozen_at: str
    latest_allowed_timestamp: str
    selected_revision_hashes: tuple[str, ...]
    rejected_revision_hashes: tuple[str, ...]
    lifecycle_policy_sha256: str
    evidence_inventory_sha256: str
    exact_exclusions: Mapping[str, Any]
    conflict_sparsity_ood_facts: Mapping[str, Any]
    history_episode_ids: tuple[str, ...]
    snapshot_sha256: str = ""
    schema_version: str = "r5-memory-snapshot-v1"

    def __post_init__(self) -> None:
        cutoff = datetime.fromisoformat(self.latest_allowed_timestamp.replace("Z", "+00:00"))
        frozen = datetime.fromisoformat(self.frozen_at.replace("Z", "+00:00"))
        if frozen < cutoff:
            raise SnapshotBoundaryError("frozen_at cannot precede latest_allowed_timestamp")
        for name in ("lifecycle_policy_sha256", "evidence_inventory_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise SnapshotBoundaryError(f"{name} must be SHA-256")
        for name in ("selected_revision_hashes", "rejected_revision_hashes", "history_episode_ids"):
            values = tuple(getattr(self, name))
            if len(values) != len(set(values)):
                raise SnapshotBoundaryError(f"{name} must be unique")
            object.__setattr__(self, name, values)
        if not isinstance(self.exact_exclusions, Mapping) or not isinstance(self.conflict_sparsity_ood_facts, Mapping):
            raise TypeError("snapshot facts must be mappings")
        expected = canonical_sha256(self.to_dict(include_hash=False))
        if self.snapshot_sha256 and self.snapshot_sha256 != expected:
            raise SnapshotBoundaryError("snapshot_sha256 mismatch")
        object.__setattr__(self, "snapshot_sha256", expected)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "frozen_at": self.frozen_at,
            "latest_allowed_timestamp": self.latest_allowed_timestamp,
            "selected_revision_hashes": list(self.selected_revision_hashes),
            "rejected_revision_hashes": list(self.rejected_revision_hashes),
            "lifecycle_policy_sha256": self.lifecycle_policy_sha256,
            "evidence_inventory_sha256": self.evidence_inventory_sha256,
            "exact_exclusions": dict(self.exact_exclusions),
            "conflict_sparsity_ood_facts": dict(self.conflict_sparsity_ood_facts),
            "history_episode_ids": list(self.history_episode_ids),
        }
        if include_hash:
            value["snapshot_sha256"] = self.snapshot_sha256
        return value


class R5SnapshotBuilder:
    """Build a snapshot from one history partition and never future data."""

    def __init__(self, reducer: R5LifecycleReducer | None = None):
        self.reducer = reducer or R5LifecycleReducer()

    def build(
        self,
        episodes: Sequence[R5EpisodeEnvelope],
        reductions: Sequence[LifecycleReduction],
        *,
        latest_allowed_timestamp: str,
        frozen_at: str,
        evidence_inventory_sha256: str,
        exact_exclusions: Mapping[str, Any],
        conflict_sparsity_ood_facts: Mapping[str, Any],
    ) -> R5MemorySnapshot:
        cutoff = datetime.fromisoformat(latest_allowed_timestamp.replace("Z", "+00:00"))
        history = []
        history_by_id = {}
        for episode in episodes:
            observed = datetime.fromisoformat(episode.observed_at.replace("Z", "+00:00"))
            if observed > cutoff:
                raise SnapshotBoundaryError("future episode supplied to history snapshot")
            previous = history_by_id.get(episode.episode_id)
            if previous is not None and previous.envelope_sha256 != episode.envelope_sha256:
                raise SnapshotBoundaryError("history contains duplicate episode id with changed payload")
            history_by_id[episode.episode_id] = episode
            history.append(episode)
        history_ids = set(history_by_id)
        for reduction in reductions:
            outside_history = set(reduction.eligible_episode_ids) - history_ids
            if outside_history:
                raise SnapshotBoundaryError(
                    "lifecycle reduction references future or external episode"
                )
        selected = [item.revision for item in reductions if item.revision.lifecycle.value in {"Provisional", "Trusted"}]
        rejected = [item.revision for item in reductions if item.revision.lifecycle.value not in {"Provisional", "Trusted"}]
        policy_hash = self.reducer.policy.policy_sha256
        return R5MemorySnapshot(
            snapshot_id="r5-snapshot-" + canonical_sha256({
                "latest_allowed_timestamp": latest_allowed_timestamp,
                "frozen_at": frozen_at,
                "selected": [item.revision_sha256 for item in selected],
                "rejected": [item.revision_sha256 for item in rejected],
                "policy": policy_hash,
                "inventory": evidence_inventory_sha256,
                "episodes": sorted(history_ids),
            })[:32],
            frozen_at=frozen_at,
            latest_allowed_timestamp=latest_allowed_timestamp,
            selected_revision_hashes=tuple(item.revision_sha256 for item in selected),
            rejected_revision_hashes=tuple(item.revision_sha256 for item in rejected),
            lifecycle_policy_sha256=policy_hash,
            evidence_inventory_sha256=evidence_inventory_sha256,
            exact_exclusions=dict(exact_exclusions),
            conflict_sparsity_ood_facts=dict(conflict_sparsity_ood_facts),
            history_episode_ids=tuple(sorted(history_ids)),
        )


__all__ = ["R5MemorySnapshot", "R5SnapshotBuilder", "SnapshotBoundaryError"]
