"""Paired, auditable reducers for R5 campaign results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .r5_protocol import R5Arm


class R5ReductionError(ValueError):
    """Raised when paired campaign records are not comparable."""


@dataclass(frozen=True, slots=True)
class R5ArmObservation:
    case_id: str
    repeat: int
    arm: R5Arm
    baseline_id: str
    status: str
    verified_positive: bool
    attributable_negative: bool
    false_repair: bool
    abstained: bool
    inconclusive: bool
    invalid_evidence: bool
    provider_calls: int
    vitis_launches: int
    source_sha256: str
    context_signature: str
    artifact_sha256: str
    baseline_accepted: bool = False

    def key(self) -> tuple[str, int, str]:
        return self.case_id, self.repeat, self.arm.value


@dataclass(frozen=True, slots=True)
class R5CampaignReduction:
    observation_count: int
    pair_count: int
    paired_verified_positive_difference: float
    paired_negative_transfer_difference: float
    by_arm: Mapping[str, Mapping[str, Any]]
    leakage_findings: tuple[str, ...]
    reduction_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "observation_count": self.observation_count,
            "pair_count": self.pair_count,
            "paired_verified_positive_difference": self.paired_verified_positive_difference,
            "paired_negative_transfer_difference": self.paired_negative_transfer_difference,
            "by_arm": dict(self.by_arm),
            "leakage_findings": list(self.leakage_findings),
            "reduction_sha256": self.reduction_sha256,
        }


def reduce_observations(observations: Iterable[R5ArmObservation]) -> R5CampaignReduction:
    rows = list(observations)
    keys = [item.key() for item in rows]
    if len(keys) != len(set(keys)):
        raise R5ReductionError("duplicate case/repeat/arm observation")
    by_parent: dict[tuple[str, int], list[R5ArmObservation]] = defaultdict(list)
    for item in rows:
        by_parent[(item.case_id, item.repeat)].append(item)
    leakage: list[str] = []
    for parent, group in by_parent.items():
        baselines = {item.baseline_id for item in group}
        if len(baselines) != 1:
            leakage.append(f"baseline_mismatch:{parent[0]}:{parent[1]}")
        sources = {item.source_sha256 for item in group}
        if len(sources) != 1:
            leakage.append(f"source_mismatch:{parent[0]}:{parent[1]}")
    stats: dict[str, dict[str, Any]] = {}
    for arm in R5Arm:
        group = [item for item in rows if item.arm is arm]
        stats[arm.value] = {
            "count": len(group),
            "verified_positive": sum(item.verified_positive for item in group),
            "baseline_accepted": sum(item.baseline_accepted for item in group),
            "attributable_negative": sum(item.attributable_negative for item in group),
            "false_repair": sum(item.false_repair for item in group),
            "abstained": sum(item.abstained for item in group),
            "inconclusive": sum(item.inconclusive for item in group),
            "invalid_evidence": sum(item.invalid_evidence for item in group),
            "provider_calls": sum(item.provider_calls for item in group),
            "vitis_launches": sum(item.vitis_launches for item in group),
        }
    a0 = { (item.case_id, item.repeat): item for item in rows if item.arm is R5Arm.A0 }
    a6 = { (item.case_id, item.repeat): item for item in rows if item.arm is R5Arm.A6 }
    pairs = sorted(set(a0) & set(a6))
    if not pairs:
        leakage.append("no_a0_a6_pairs")
        repair_diff = 0.0
        negative_diff = 0.0
    else:
        repair_diff = sum(a6[key].verified_positive - a0[key].verified_positive for key in pairs) / len(pairs)
        negative_diff = sum(a6[key].attributable_negative - a0[key].attributable_negative for key in pairs) / len(pairs)
    payload = {
        "observation_count": len(rows),
        "pair_count": len(pairs),
        "by_arm": stats,
        "leakage_findings": leakage,
        "repair_diff": repair_diff,
        "negative_diff": negative_diff,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return R5CampaignReduction(len(rows), len(pairs), repair_diff, negative_diff, stats, tuple(leakage), digest)


__all__ = ["R5ArmObservation", "R5CampaignReduction", "R5ReductionError", "reduce_observations"]
