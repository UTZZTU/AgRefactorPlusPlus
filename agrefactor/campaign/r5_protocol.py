"""Machine-readable R5 A0-A6 campaign protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


class R5ProtocolError(ValueError):
    """Raised when an R5 campaign manifest is not causally identifiable."""


class R5Arm(str, Enum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"


ARM_SEMANTICS: dict[R5Arm, dict[str, str]] = {
    R5Arm.A0: {"advisor": "off", "memory": "none", "repair": "off"},
    R5Arm.A1: {"advisor": "shadow", "memory": "none", "repair": "off"},
    R5Arm.A2: {"advisor": "on", "memory": "none", "repair": "once"},
    R5Arm.A3: {"advisor": "on", "memory": "similarity_only", "repair": "once"},
    R5Arm.A4: {"advisor": "on", "memory": "positive_only_gated", "repair": "once"},
    R5Arm.A5: {"advisor": "on", "memory": "positive_negative_gated", "repair": "once"},
    R5Arm.A6: {"advisor": "on", "memory": "full_lifecycle_gated", "repair": "once"},
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True, slots=True)
class R5CampaignManifest:
    campaign_id: str
    repository_commit: str
    source_inventory_sha256: str
    history_snapshot_sha256: str
    arm_order: tuple[R5Arm, ...]
    case_ids: tuple[str, ...]
    repeats: int = 3
    seed: int = 23
    provider_cap: int = 500
    vitis_cap: int = 500
    prompt_identity_sha256: str = ""
    model_identity_sha256: str = ""
    target_identity_sha256: str = ""
    toolchain_identity_sha256: str = ""
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        if len(self.arm_order) != len(set(self.arm_order)) or set(self.arm_order) != set(R5Arm):
            raise R5ProtocolError("manifest must contain each A0-A6 arm exactly once")
        if len(self.case_ids) < 1 or len(self.case_ids) != len(set(self.case_ids)):
            raise R5ProtocolError("case_ids must be unique and non-empty")
        if self.repeats != 3:
            raise R5ProtocolError("R5 target repeats are frozen at three")
        if not isinstance(self.repository_commit, str) or not _GIT_COMMIT.fullmatch(self.repository_commit):
            raise R5ProtocolError("repository_commit must be a 40-64 character git SHA")
        for name in ("source_inventory_sha256", "history_snapshot_sha256", "prompt_identity_sha256", "model_identity_sha256", "target_identity_sha256", "toolchain_identity_sha256"):
            if not isinstance(getattr(self, name), str) or not _SHA256.fullmatch(getattr(self, name)):
                raise R5ProtocolError(f"{name} must be SHA-256")
        if self.provider_cap != 500 or self.vitis_cap != 500:
            raise R5ProtocolError("R5 hard caps are 500 Provider and 500 Vitis launches")
        expected = hashlib.sha256(json.dumps(self.to_dict(include_hash=False), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.manifest_sha256 and self.manifest_sha256 != expected:
            raise R5ProtocolError("manifest_sha256 mismatch")
        object.__setattr__(self, "manifest_sha256", expected)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "campaign_id": self.campaign_id,
            "repository_commit": self.repository_commit,
            "source_inventory_sha256": self.source_inventory_sha256,
            "history_snapshot_sha256": self.history_snapshot_sha256,
            "arm_order": [item.value for item in self.arm_order],
            "arm_semantics": {item.value: ARM_SEMANTICS[item] for item in self.arm_order},
            "case_ids": list(self.case_ids),
            "repeats": self.repeats,
            "seed": self.seed,
            "provider_cap": self.provider_cap,
            "vitis_cap": self.vitis_cap,
            "prompt_identity_sha256": self.prompt_identity_sha256,
            "model_identity_sha256": self.model_identity_sha256,
            "target_identity_sha256": self.target_identity_sha256,
            "toolchain_identity_sha256": self.toolchain_identity_sha256,
            "one_common_baseline_per_case_repeat": True,
            "fresh_validation_only_for_mutation_arms": True,
            "cross_arm_mutation_cache_allowed": False,
        }
        if include_hash:
            value["manifest_sha256"] = self.manifest_sha256
        return value


def estimate_upper_bound(*, case_count: int, repeats: int = 3) -> tuple[int, int]:
    """Return conservative Provider/Vitis upper bounds for A0-A6.

    Each case/repeat has one initial generation. Advisor arms A1-A6 call once;
    repair arms A2-A6 may call once more. The common baseline is one formal
    prefix with three Vitis stages; mutation arms may launch one fresh full
    validation (three stages).
    """
    if case_count < 1 or repeats != 3:
        raise R5ProtocolError("invalid case_count or repeats")
    provider = case_count * repeats * (1 + 6 + 5)
    vitis = case_count * repeats * (3 + 5 * 3)
    return provider, vitis


def validate_arm_diff(manifest: R5CampaignManifest, observed: Mapping[str, Mapping[str, Any]]) -> None:
    for arm in R5Arm:
        if arm.value not in observed:
            raise R5ProtocolError(f"missing observed arm: {arm.value}")
        expected = ARM_SEMANTICS[arm]
        for key, value in expected.items():
            if observed[arm.value].get(key) != value:
                raise R5ProtocolError(f"arm {arm.value} changed frozen semantics for {key}")


__all__ = ["ARM_SEMANTICS", "R5Arm", "R5CampaignManifest", "R5ProtocolError", "estimate_upper_bound", "validate_arm_diff"]
