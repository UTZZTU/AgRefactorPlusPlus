"""Discriminated R5 research authorization adjacent to accepted R4."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from .episode_ledger import canonical_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class R5AuthorizationError(ValueError):
    """Raised when an arm tries to cross a frozen authorization boundary."""


class R5AuthorizationMode(str, Enum):
    ADVISOR_ONLY = "advisor_only"
    SIMILARITY_ONLY = "similarity_only"
    GATED_MEMORY = "gated_memory"


@dataclass(frozen=True, slots=True)
class R5ResearchAuthorization:
    authorization_id: str
    arm_id: str
    mode: R5AuthorizationMode
    calibration_certificate_sha256: str
    advisory_sha256: str
    policy_sha256: str
    ledger_sha256: str
    budget_reservation_sha256: str
    r4_controller_contract_sha256: str
    memory_mode: str
    retrieval_manifest_sha256: str | None = None
    gate_contract_sha256: str | None = None
    revision_sha256: str | None = None
    snapshot_sha256: str | None = None
    payload_manifest_sha256: str | None = None
    maximum_mutations: int = 1
    candidate_only: bool = True
    authorization_sha256: str = ""
    schema_version: str = "r5-research-authorization-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, R5AuthorizationMode):
            object.__setattr__(self, "mode", R5AuthorizationMode(self.mode))
        if self.arm_id not in {"A2", "A3", "A4", "A5", "A6"}:
            raise R5AuthorizationError("only mutation arms may receive R5 authorization")
        expected_modes = {
            "A2": (R5AuthorizationMode.ADVISOR_ONLY, "none"),
            "A3": (R5AuthorizationMode.SIMILARITY_ONLY, "similarity_only"),
            "A4": (R5AuthorizationMode.GATED_MEMORY, "positive_only_gated"),
            "A5": (R5AuthorizationMode.GATED_MEMORY, "positive_negative_gated"),
            "A6": (R5AuthorizationMode.GATED_MEMORY, "full_lifecycle_gated"),
        }
        mode, memory_mode = expected_modes[self.arm_id]
        if self.mode is not mode or self.memory_mode != memory_mode:
            raise R5AuthorizationError("arm, authorization mode, and memory mode conflict")
        if self.maximum_mutations != 1 or self.candidate_only is not True:
            raise R5AuthorizationError("R5 authorization permits exactly one Candidate-only mutation")
        required = (
            "calibration_certificate_sha256", "advisory_sha256", "policy_sha256",
            "ledger_sha256", "budget_reservation_sha256", "r4_controller_contract_sha256",
        )
        for name in required:
            self._validate_sha(getattr(self, name), name)
        gated = ("gate_contract_sha256", "revision_sha256", "snapshot_sha256", "payload_manifest_sha256")
        if self.mode is R5AuthorizationMode.ADVISOR_ONLY:
            if self.retrieval_manifest_sha256 is not None or any(getattr(self, name) is not None for name in gated):
                raise R5AuthorizationError("advisor_only must not bind memory")
        elif self.mode is R5AuthorizationMode.SIMILARITY_ONLY:
            self._validate_sha(self.retrieval_manifest_sha256, "retrieval_manifest_sha256")
            if any(getattr(self, name) is not None for name in gated):
                raise R5AuthorizationError("similarity_only must not claim Gate acceptance")
        else:
            if self.retrieval_manifest_sha256 is not None:
                raise R5AuthorizationError("gated_memory must bind the snapshot, not a similarity manifest")
            for name in gated:
                self._validate_sha(getattr(self, name), name)
        expected = canonical_sha256(self.to_dict(include_hash=False))
        if self.authorization_sha256 and self.authorization_sha256 != expected:
            raise R5AuthorizationError("authorization_sha256 mismatch")
        object.__setattr__(self, "authorization_sha256", expected)

    @staticmethod
    def _validate_sha(value: str | None, name: str) -> None:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise R5AuthorizationError(f"{name} must be SHA-256")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "authorization_id": self.authorization_id,
            "arm_id": self.arm_id,
            "mode": self.mode.value,
            "calibration_certificate_sha256": self.calibration_certificate_sha256,
            "advisory_sha256": self.advisory_sha256,
            "policy_sha256": self.policy_sha256,
            "ledger_sha256": self.ledger_sha256,
            "budget_reservation_sha256": self.budget_reservation_sha256,
            "r4_controller_contract_sha256": self.r4_controller_contract_sha256,
            "memory_mode": self.memory_mode,
            "retrieval_manifest_sha256": self.retrieval_manifest_sha256,
            "gate_contract_sha256": self.gate_contract_sha256,
            "revision_sha256": self.revision_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "payload_manifest_sha256": self.payload_manifest_sha256,
            "maximum_mutations": self.maximum_mutations,
            "candidate_only": self.candidate_only,
        }
        if include_hash:
            value["authorization_sha256"] = self.authorization_sha256
        return value


__all__ = ["R5AuthorizationError", "R5AuthorizationMode", "R5ResearchAuthorization"]
