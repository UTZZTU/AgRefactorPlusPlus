"""Fail-closed provenance checks for the R4 integration seam."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_artifact_sha256(value: Mapping[str, Any]) -> str:
    payload = _mapping(value, "artifact")
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result = json.loads(
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True)
    )
    if not isinstance(result, dict):
        raise TypeError(f"{name} must be an object")
    return result


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value.casefold())
    )


@dataclass(frozen=True, slots=True)
class R4ProvenanceResult:
    valid: bool
    reasons: tuple[str, ...]
    checked_refs: tuple[str, ...]

    @property
    def abstain(self) -> bool:
        return not self.valid


def validate_r4_provenance(
    *,
    event: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    advisory: Mapping[str, Any],
    gate: Mapping[str, Any],
    revision: Mapping[str, Any],
    authorization: Mapping[str, Any],
    canary: Mapping[str, Any],
    candidate: str,
    original: str,
    testbench_hashes: Mapping[str, str],
    policy_decision: Mapping[str, Any],
    ledger_event: Mapping[str, Any],
    budget_reservation: Mapping[str, Any],
    deterministic_terminal: Mapping[str, Any],
) -> R4ProvenanceResult:
    reasons: list[str] = []
    event = _mapping(event, "event")
    execution_identity = _mapping(execution_identity, "execution_identity")
    advisory = _mapping(advisory, "advisory")
    gate = _mapping(gate, "gate")
    revision = _mapping(revision, "revision")
    authorization = _mapping(authorization, "authorization")
    canary = _mapping(canary, "canary")
    policy_decision = _mapping(policy_decision, "policy_decision")
    ledger_event = _mapping(ledger_event, "ledger_event")
    budget_reservation = _mapping(budget_reservation, "budget_reservation")
    deterministic_terminal = _mapping(deterministic_terminal, "deterministic_terminal")
    event_refs = set(event.get("evidence_refs", ()))

    if event.get("event_id") != authorization.get("event_ref"):
        reasons.append("event_authorization_ref_mismatch")
    if event.get("run_id") != authorization.get("run_id"):
        reasons.append("run_authorization_ref_mismatch")
    if execution_identity.get("run_id") not in {None, event.get("run_id")}:
        reasons.append("execution_run_mismatch")
    if event.get("candidate_sha256") != _sha(candidate):
        reasons.append("candidate_hash_mismatch")
    if event.get("original_sha256") not in {None, _sha(original)}:
        reasons.append("original_hash_mismatch")
    if (
        execution_identity.get("identity_complete") is not True
        or execution_identity.get("hidden_input_count", 0) != 0
        or execution_identity.get("secret_present", False)
        or execution_identity.get("private_reasoning_present", False)
    ):
        reasons.append("execution_identity_incomplete_or_unsafe")
    if execution_identity.get("source_sha256") != _sha(original):
        reasons.append("execution_source_mismatch")
    if not set(gate.get("evidence_refs", ())).issubset(event_refs):
        reasons.append("gate_evidence_out_of_scope")
    if not set(advisory.get("evidence_refs", ())).issubset(event_refs):
        reasons.append("advisory_evidence_out_of_scope")
    if advisory.get("accepted") is not False or advisory.get("owner_authority") != "llm_advisory":
        reasons.append("advisory_authority_invalid")
    if not isinstance(advisory.get("advisory_id"), str) or authorization.get("advisory_id") != advisory.get("advisory_id"):
        reasons.append("advisory_authorization_mismatch")
    if gate.get("decision") != "accept":
        reasons.append("gate_not_accept")
    if revision.get("lifecycle") != "Trusted" or not revision.get("threshold_source"):
        reasons.append("revision_not_trusted_or_calibrated")
    if authorization.get("gate_decision") != gate.get("decision"):
        reasons.append("authorization_gate_mismatch")
    if authorization.get("gate_contract_hash") != gate.get("contract_hash"):
        reasons.append("authorization_gate_contract_mismatch")
    if authorization.get("revision_sha256") != revision.get("revision_hash"):
        reasons.append("authorization_revision_mismatch")
    if authorization.get("canary_manifest_sha256") != canary.get("manifest_sha256"):
        reasons.append("authorization_canary_mismatch")
    if authorization.get("before_candidate_sha256") != _sha(candidate):
        reasons.append("authorization_candidate_mismatch")

    if authorization.get("policy_decision_id") != canonical_artifact_sha256(policy_decision):
        reasons.append("policy_decision_producer_hash_mismatch")
    if authorization.get("ledger_reservation_id") != canonical_artifact_sha256(ledger_event):
        reasons.append("ledger_reservation_producer_hash_mismatch")
    reservation_id = budget_reservation.get("reservation_id")
    reservation_payload = dict(budget_reservation)
    reservation_payload.pop("reservation_id", None)
    if reservation_id != canonical_artifact_sha256(reservation_payload):
        reasons.append("budget_reservation_self_hash_mismatch")
    if authorization.get("budget_reservation_id") != reservation_id:
        reasons.append("budget_reservation_producer_hash_mismatch")
    if authorization.get("deterministic_terminal_ref") != canonical_artifact_sha256(deterministic_terminal):
        reasons.append("deterministic_terminal_producer_hash_mismatch")

    producer_fields = (
        "action",
        "role",
        "stage",
        "evidence_view",
        "owner_authority",
        "lineage_id",
    )
    if policy_decision.get("status") != "allowed":
        reasons.append("policy_decision_not_allowed")
    if ledger_event.get("accepted") is not True:
        reasons.append("ledger_reservation_not_accepted")
    if any(policy_decision.get(field) != ledger_event.get(field) for field in producer_fields):
        reasons.append("policy_ledger_artifact_mismatch")
    if policy_decision.get("lineage_id") != event.get("run_id"):
        reasons.append("policy_lineage_mismatch")
    if policy_decision.get("action") != "repair" or policy_decision.get("role") != "candidate":
        reasons.append("policy_scope_mismatch")
    if budget_reservation.get("admitted") is not True:
        reasons.append("budget_reservation_not_admitted")
    requested = budget_reservation.get("requested")
    effective = budget_reservation.get("effective")
    if not isinstance(requested, Mapping) or not isinstance(effective, Mapping):
        reasons.append("budget_plan_artifact_missing")
    elif requested.get("plan_sha256") != effective.get("plan_sha256"):
        reasons.append("budget_requested_effective_mismatch")

    if canary.get("enabled") is not True or canary.get("operator_enabled") is not True:
        reasons.append("canary_not_operator_enabled")
    for key in (
        "target_identity",
        "toolchain_identity",
        "parser_identity",
        "model_identity",
        "prompt_sha256",
    ):
        if not execution_identity.get(key) or execution_identity.get(key) != canary.get(key):
            reasons.append(f"canary_identity_mismatch:{key}")
    if execution_identity.get("case_id") not in set(canary.get("case_ids", ())):
        reasons.append("canary_case_mismatch")
    event_target = event.get("target_identity", {})
    event_toolchain = event.get("toolchain_identity", {})
    if isinstance(event_target, Mapping) and event_target.get("fingerprint") != execution_identity.get("target_identity"):
        reasons.append("event_target_mismatch")
    if isinstance(event_toolchain, Mapping) and event_toolchain.get("fingerprint") != execution_identity.get("toolchain_identity"):
        reasons.append("event_toolchain_mismatch")
    if (
        event.get("evidence_view", "agent_safe") != "agent_safe"
        or event.get("hidden_input_count", 0) != 0
        or event.get("secret_present", False)
        or event.get("private_reasoning_present", False)
        or event.get("physical_tool_launched") is not True
        or event.get("evidence_complete") is not True
    ):
        reasons.append("agent_safe_boundary_violated")
    if not isinstance(testbench_hashes, Mapping) or "preflight" not in testbench_hashes:
        reasons.append("testbench_identity_missing")
    elif any(not _valid_digest(value) for value in testbench_hashes.values()):
        reasons.append("testbench_identity_invalid")

    checked = set(event_refs)
    for value in (
        authorization.get("policy_decision_id"),
        authorization.get("ledger_reservation_id"),
        authorization.get("budget_reservation_id"),
        authorization.get("deterministic_terminal_ref"),
    ):
        if isinstance(value, str) and value:
            checked.add(value)
    return R4ProvenanceResult(
        valid=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        checked_refs=tuple(sorted(checked)),
    )


__all__ = [
    "R4ProvenanceResult",
    "canonical_artifact_sha256",
    "validate_r4_provenance",
]
