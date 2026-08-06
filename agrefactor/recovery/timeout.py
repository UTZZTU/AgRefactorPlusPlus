"""Typed, physical-evidence-first Public CSIM/COSIM timeout classification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TimeoutOwner(str, Enum):
    CANDIDATE = "candidate"
    TESTBENCH = "testbench"
    TOOLCHAIN = "toolchain"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class TimeoutClass(str, Enum):
    CANDIDATE_DEADLOCK = "candidate_deadlock"
    CANDIDATE_STREAM_MISMATCH = "candidate_stream_mismatch"
    PUBLIC_TESTBENCH_PROTOCOL_WAIT = "public_testbench_protocol_wait"
    TOOLCHAIN_STALL = "toolchain_stall"
    INFRASTRUCTURE_LAUNCH_TIMEOUT = "infrastructure_launch_timeout"
    OWNERSHIP_UNKNOWN = "ownership_unknown"


@dataclass(frozen=True, slots=True)
class TimeoutClassification:
    timed_out: bool
    timeout_class: TimeoutClass
    owner: TimeoutOwner
    owner_authority: str
    repair_eligible: bool
    advisory_eligible: bool
    reason_code: str
    physical_tool_launched: bool
    evidence_complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "timed_out": self.timed_out,
            "timeout_class": self.timeout_class.value,
            "owner": self.owner.value,
            "owner_authority": self.owner_authority,
            "repair_eligible": self.repair_eligible,
            "advisory_eligible": self.advisory_eligible,
            "reason_code": self.reason_code,
            "physical_tool_launched": self.physical_tool_launched,
            "evidence_complete": self.evidence_complete,
        }


def classify_public_timeout(
    evidence: Mapping[str, Any] | None,
    *,
    stage: str,
) -> TimeoutClassification:
    payload = dict(evidence or {}) if isinstance(evidence, Mapping) else {}
    normalized_stage = str(stage).strip().casefold()
    if normalized_stage not in {"public_csim", "public_cosim"}:
        raise ValueError("stage must be public_csim or public_cosim")

    timed_out = payload.get("timed_out") is True or payload.get("timeout") is True
    launched = any(
        payload.get(key) is True
        for key in (
            "tool_launched",
            "simulation_launched",
            "csim_launched",
            "cosim_launched",
        )
    )
    complete = payload.get("evidence_complete") is True or (
        launched and payload.get("returncode_present") is True
    )

    if not timed_out:
        return TimeoutClassification(
            False,
            TimeoutClass.OWNERSHIP_UNKNOWN,
            TimeoutOwner.UNKNOWN,
            "not_applicable",
            False,
            False,
            "not_a_timeout",
            launched,
            complete,
        )

    if not launched:
        return TimeoutClassification(
            True,
            TimeoutClass.INFRASTRUCTURE_LAUNCH_TIMEOUT,
            TimeoutOwner.INFRASTRUCTURE,
            "deterministic_proven",
            False,
            False,
            "timeout_before_physical_tool_launch",
            False,
            complete,
        )

    if payload.get("toolchain_stall") is True:
        return TimeoutClassification(
            True,
            TimeoutClass.TOOLCHAIN_STALL,
            TimeoutOwner.TOOLCHAIN,
            "deterministic_proven",
            False,
            False,
            "timeout_toolchain_stall_proven",
            True,
            complete,
        )

    if payload.get("candidate_deadlock") is True:
        return TimeoutClassification(
            True,
            TimeoutClass.CANDIDATE_DEADLOCK,
            TimeoutOwner.CANDIDATE,
            "deterministic_proven",
            True,
            False,
            "timeout_candidate_deadlock_proven",
            True,
            complete,
        )

    if payload.get("candidate_stream_mismatch") is True:
        return TimeoutClassification(
            True,
            TimeoutClass.CANDIDATE_STREAM_MISMATCH,
            TimeoutOwner.CANDIDATE,
            "deterministic_proven",
            True,
            False,
            "timeout_candidate_stream_mismatch_proven",
            True,
            complete,
        )

    if payload.get("public_testbench_protocol_wait") is True:
        return TimeoutClassification(
            True,
            TimeoutClass.PUBLIC_TESTBENCH_PROTOCOL_WAIT,
            TimeoutOwner.TESTBENCH,
            "deterministic_proven",
            True,
            False,
            "timeout_public_testbench_protocol_wait_proven",
            True,
            complete,
        )

    return TimeoutClassification(
        True,
        TimeoutClass.OWNERSHIP_UNKNOWN,
        TimeoutOwner.UNKNOWN,
        "unknown",
        False,
        complete,
        "timeout_ownership_unknown",
        True,
        complete,
    )
