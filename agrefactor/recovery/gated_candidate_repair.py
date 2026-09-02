"""Fail-closed R4 canary controller for one authorized Candidate repair.

The controller is intentionally adjacent to the existing repair loop. It is
opt-in and provider/validator neutral: callers inject the already configured
Candidate mutation and formal validation functions. No model output, Gate
decision, or controller result is a correctness authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any

from .memory_gate import GateDecision, PatternLifecycle
from .policy import (
    RecoveryAction,
    RecoveryAuthority,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
)
from agrefactor.runtime.budget import BudgetLimits, BudgetManager

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STAGES = frozenset({"preflight", "public_evaluation", "csynth", "public_cosim"})
_OUTCOMES = frozenset({"verified_positive", "verified_negative", "abstained", "inconclusive", "invalid_evidence"})


class R4ContractError(ValueError):
    """Raised when an R4 authorization or execution contract is invalid."""


class R4Outcome(str, Enum):
    VERIFIED_POSITIVE = "verified_positive"
    VERIFIED_NEGATIVE = "verified_negative"
    ABSTAINED = "abstained"
    INCONCLUSIVE = "inconclusive"
    INVALID_EVIDENCE = "invalid_evidence"


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R4ContractError(name + " must be non-empty text")
    return value.strip()


def _hash(value: Any, name: str) -> str:
    value = _text(value, name).lower()
    if not _SHA256.fullmatch(value):
        raise R4ContractError(name + " must be a SHA-256 digest")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _copy(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise R4ContractError(name + " must be a mapping")
    try:
        result = json.loads(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise R4ContractError(name + " must contain finite JSON data") from exc
    if not isinstance(result, dict):
        raise R4ContractError(name + " must be an object")
    return result


@dataclass(frozen=True, slots=True)
class R4CanaryManifest:
    manifest_id: str
    manifest_sha256: str
    enabled: bool = False
    case_ids: tuple[str, ...] = ()
    source_sha256: str = ""
    target_identity: str = ""
    toolchain_identity: str = ""
    parser_identity: str = ""
    model_identity: str = ""
    prompt_sha256: str = ""
    allowed_stage: str = ""
    max_repairs_per_run: int = 1
    expires_at: str = ""
    operator_enabled: bool = False

    def __post_init__(self) -> None:
        _text(self.manifest_id, "manifest_id")
        _hash(self.manifest_sha256, "manifest_sha256")
        if not isinstance(self.enabled, bool) or not isinstance(self.operator_enabled, bool):
            raise TypeError("canary enabled flags must be boolean")
        if self.enabled and not self.operator_enabled:
            raise R4ContractError("canary requires explicit operator enablement")
        if isinstance(self.case_ids, (str, bytes)) or not self.case_ids or any(not _text(item, "case_id") for item in self.case_ids):
            raise R4ContractError("canary case_ids must be a non-empty sequence")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise R4ContractError("canary case_ids must be unique")
        _hash(self.source_sha256, "source_sha256")
        for field_name in ("target_identity", "toolchain_identity", "parser_identity", "model_identity"):
            _text(getattr(self, field_name), field_name)
        _hash(self.prompt_sha256, "prompt_sha256")
        if self.allowed_stage not in _STAGES:
            raise R4ContractError("allowed_stage is not an eligible Candidate stage")
        if self.max_repairs_per_run != 1:
            raise R4ContractError("R4 canary permits exactly one repair")
        _text(self.expires_at, "expires_at")
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise R4ContractError("expires_at must be ISO-8601") from exc
        if expiry.tzinfo is None:
            raise R4ContractError("expires_at must include timezone")

    def matches(self, identity: Mapping[str, Any]) -> bool:
        value = _copy(identity, "execution_identity")
        return (
            value.get("case_id") in self.case_ids
            and value.get("source_sha256") == self.source_sha256
            and value.get("target_identity") == self.target_identity
            and value.get("toolchain_identity") == self.toolchain_identity
            and value.get("parser_identity") == self.parser_identity
            and value.get("model_identity") == self.model_identity
            and value.get("prompt_sha256") == self.prompt_sha256
            and value.get("stage") == self.allowed_stage
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "case_ids": list(self.case_ids),
            "source_sha256": self.source_sha256,
            "target_identity": self.target_identity,
            "toolchain_identity": self.toolchain_identity,
            "parser_identity": self.parser_identity,
            "model_identity": self.model_identity,
            "prompt_sha256": self.prompt_sha256,
            "allowed_stage": self.allowed_stage,
            "max_repairs_per_run": self.max_repairs_per_run,
            "expires_at": self.expires_at,
        }

    @property
    def expired(self) -> bool:
        expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= expiry


@dataclass(frozen=True, slots=True)
class R4KillSwitchState:
    active: bool = False
    scope: str = "global"
    sequence: int = 0
    trigger: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.active, bool) or isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise TypeError("invalid kill-switch state")
        _text(self.scope, "kill_switch.scope")
        if self.active:
            _text(self.trigger, "kill_switch.trigger")


@dataclass(frozen=True, slots=True)
class R4RevisionSafetyRecord:
    revision_sha256: str
    authorization_id: str
    trigger: str
    created_at: str
    quarantined: bool = True

    def __post_init__(self) -> None:
        _hash(self.revision_sha256, "revision_sha256")
        _text(self.authorization_id, "authorization_id")
        _text(self.trigger, "trigger")
        _text(self.created_at, "created_at")
        if not self.quarantined:
            raise R4ContractError("R4 safety records are append-only quarantine records")


@dataclass(frozen=True, slots=True)
class R4CandidateRepairAuthorization:
    run_id: str
    event_ref: str
    advisory_id: str
    gate_decision: str
    pattern_lifecycle: str
    gate_contract_hash: str
    revision_sha256: str
    canary_manifest_sha256: str
    before_candidate_sha256: str
    policy_decision_id: str
    budget_reservation_id: str
    deterministic_terminal_ref: str
    authorization_id: str = ""

    def __post_init__(self) -> None:
        for field_name in ("run_id", "event_ref", "advisory_id", "policy_decision_id", "budget_reservation_id", "deterministic_terminal_ref"):
            _text(getattr(self, field_name), field_name)
        if self.gate_decision != GateDecision.ACCEPT.value:
            raise R4ContractError("R4 authorization requires Gate accept")
        if self.pattern_lifecycle != PatternLifecycle.TRUSTED.value:
            raise R4ContractError("R4 authorization requires Trusted revision")
        for field_name in ("gate_contract_hash", "revision_sha256", "canary_manifest_sha256", "before_candidate_sha256"):
            _hash(getattr(self, field_name), field_name)
        expected = hashlib.sha256(_canonical(self.to_dict(include_id=False))).hexdigest()
        if self.authorization_id and self.authorization_id != expected:
            raise R4ContractError("authorization_id mismatch")
        object.__setattr__(self, "authorization_id", expected)

    def to_dict(self, *, include_id: bool = True) -> dict[str, str]:
        value = {name: getattr(self, name) for name in ("run_id", "event_ref", "advisory_id", "gate_decision", "pattern_lifecycle", "gate_contract_hash", "revision_sha256", "canary_manifest_sha256", "before_candidate_sha256", "policy_decision_id", "budget_reservation_id", "deterministic_terminal_ref")}
        if include_id:
            value["authorization_id"] = self.authorization_id
        return value


@dataclass(frozen=True, slots=True)
class R4ExecutionInput:
    authorization: R4CandidateRepairAuthorization
    canary: R4CanaryManifest
    kill_switch: R4KillSwitchState
    execution_identity: Mapping[str, Any]
    advisory: Mapping[str, Any]
    candidate: str
    original: str
    testbench_hashes: Mapping[str, str]
    route_fingerprint: str
    agent_safe: bool = True
    physical_tool_launched: bool = True
    evidence_complete: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, R4CandidateRepairAuthorization) or not isinstance(self.canary, R4CanaryManifest) or not isinstance(self.kill_switch, R4KillSwitchState):
            raise TypeError("R4 execution contracts have invalid types")
        _copy(self.execution_identity, "execution_identity")
        advisory = _copy(self.advisory, "advisory")
        if advisory.get("accepted") is not False or advisory.get("owner_authority") != "llm_advisory":
            raise R4ContractError("advisory is not non-authoritative")
        _text(self.candidate, "candidate")
        _text(self.original, "original")
        identity = _copy(self.execution_identity, "execution_identity")
        if identity.get("identity_complete") is not True or identity.get("hidden_input_count", 0) != 0 or identity.get("secret_present") is True:
            raise R4ContractError("execution identity is incomplete or not agent-safe")
        if identity.get("source_sha256") != hashlib.sha256(self.original.encode("utf-8")).hexdigest():
            raise R4ContractError("original source identity mismatch")
        _hash(self.authorization.before_candidate_sha256, "before_candidate_sha256")
        if hashlib.sha256(self.candidate.encode("utf-8")).hexdigest() != self.authorization.before_candidate_sha256:
            raise R4ContractError("before Candidate hash mismatch")
        if not isinstance(self.testbench_hashes, Mapping) or not self.testbench_hashes:
            raise R4ContractError("testbench hashes are required")
        for name, value in self.testbench_hashes.items():
            _text(name, "testbench identity")
            _hash(value, "testbench hash")
        _hash(self.route_fingerprint, "route_fingerprint")
        if not all(isinstance(value, bool) for value in (self.agent_safe, self.physical_tool_launched, self.evidence_complete)):
            raise TypeError("R4 execution flags must be boolean")


@dataclass(frozen=True, slots=True)
class R4RunResult:
    outcome: R4Outcome
    authorization_id: str
    before_candidate_sha256: str
    after_candidate_sha256: str | None
    formal_validation_id: str | None
    provider_call_count: int
    mutation_count: int
    reasons: tuple[str, ...] = ()
    quarantine: R4RevisionSafetyRecord | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", R4Outcome(self.outcome))
        _text(self.authorization_id, "authorization_id")
        _hash(self.before_candidate_sha256, "before_candidate_sha256")
        if self.after_candidate_sha256 is not None:
            _hash(self.after_candidate_sha256, "after_candidate_sha256")
        if self.formal_validation_id is not None:
            _text(self.formal_validation_id, "formal_validation_id")
        if isinstance(self.provider_call_count, bool) or self.provider_call_count < 0 or isinstance(self.mutation_count, bool) or self.mutation_count < 0:
            raise TypeError("execution counts must be non-negative integers")
        if self.mutation_count > 1 or self.provider_call_count > 1:
            raise R4ContractError("R4 execution exceeded one-attempt cap")

    @property
    def accepted(self) -> bool:
        return self.outcome is R4Outcome.VERIFIED_POSITIVE

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "outcome": self.outcome.value, "accepted": self.accepted, "authorization_id": self.authorization_id, "before_candidate_sha256": self.before_candidate_sha256, "after_candidate_sha256": self.after_candidate_sha256, "formal_validation_id": self.formal_validation_id, "provider_call_count": self.provider_call_count, "mutation_count": self.mutation_count, "reasons": list(self.reasons), "quarantine": None if self.quarantine is None else {"revision_sha256": self.quarantine.revision_sha256, "authorization_id": self.quarantine.authorization_id, "trigger": self.quarantine.trigger, "created_at": self.quarantine.created_at}}


class R4CandidateRepairController:
    """Authorize and execute exactly one opt-in Candidate mutation."""

    def __init__(self, *, policy: RecoveryPolicy | None = None, ledger: RecoveryLedger | None = None, budget: BudgetManager | None = None) -> None:
        self._policy = policy or RecoveryPolicy()
        self._ledger = ledger or RecoveryLedger(self._policy)
        if budget is not None and not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")
        self._budget = budget
        self._mutation_count = 0
        self._provider_call_count = 0

    @property
    def ledger(self) -> RecoveryLedger:
        return self._ledger

    def run(self, request: R4ExecutionInput, *, mutate_candidate: Callable[[str], str], validate_candidate: Callable[[str], Mapping[str, Any]], audit: Callable[[Mapping[str, Any]], bool], kill_switch_reader: Callable[[], R4KillSwitchState] | None = None) -> R4RunResult:
        if not isinstance(request, R4ExecutionInput) or not callable(mutate_candidate) or not callable(validate_candidate) or not callable(audit):
            raise TypeError("invalid R4 execution request or callback")
        if not request.canary.enabled or request.canary.expired or not request.canary.matches(request.execution_identity):
            return self._result(request, R4Outcome.ABSTAINED, "canary_disabled_or_identity_mismatch")
        if request.authorization.canary_manifest_sha256 != request.canary.manifest_sha256:
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "canary_manifest_hash_mismatch")
        if request.kill_switch.active or (kill_switch_reader is not None and kill_switch_reader().active):
            return self._result(request, R4Outcome.ABSTAINED, "kill_switch_active")
        if not request.agent_safe or not request.physical_tool_launched or not request.evidence_complete:
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "evidence_firewall")
        if self._provider_call_count or self._mutation_count:
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "one_attempt_cap")
        try:
            stage = RecoveryStage(request.canary.allowed_stage)
            if self._budget is not None:
                reserve = BudgetLimits(max_llm_calls=1, max_tool_calls=1, max_compile_calls=1, max_csim_calls=1, max_csynth_calls=1, max_cosim_calls=1)
                self._budget.set_active_reserve(reserve)
            self._ledger.reserve(RecoveryRequest(action=RecoveryAction.REPAIR, role=RecoveryRole.CANDIDATE, stage=stage, evidence_view="agent_safe", owner_authority=RecoveryAuthority.LLM_ADVISORY, lineage_id=request.authorization.run_id, physical_tool_launched=True, evidence_complete=True, advisory_mode="candidate-only"))
        except Exception:
            return self._result(request, R4Outcome.INCONCLUSIVE, "policy_ledger_or_budget_denied")
        if kill_switch_reader is not None and kill_switch_reader().active:
            return self._result(request, R4Outcome.ABSTAINED, "kill_switch_active_before_provider")
        self._provider_call_count += 1
        try:
            proposed = mutate_candidate(request.candidate)
        except Exception:
            return self._result(request, R4Outcome.INCONCLUSIVE, "provider_or_mutation_failure")
        if not isinstance(proposed, str) or not proposed.strip() or proposed == request.candidate:
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "invalid_or_unchanged_candidate")
        self._mutation_count += 1
        after_hash = hashlib.sha256(proposed.encode("utf-8")).hexdigest()
        if kill_switch_reader is not None and kill_switch_reader().active:
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "kill_switch_active_after_provider", after_hash=after_hash, quarantine=True)
        try:
            validation = _copy(validate_candidate(proposed), "validation_result")
        except Exception:
            return self._result(request, R4Outcome.INCONCLUSIVE, "formal_validation_failure", after_hash=after_hash)
        if not all(isinstance(value, str) for value in validation.keys()):
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "invalid_validation_artifact", after_hash=after_hash)
        if validation.get("testbench_hashes_after") is None:
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "testbench_identity_after_missing", after_hash=after_hash)
        if validation.get("testbench_hashes_after") != dict(request.testbench_hashes):
            return self._result(request, R4Outcome.INVALID_EVIDENCE, "testbench_identity_changed", after_hash=after_hash, quarantine=True)
        try:
            auditor_clean = bool(audit({"authorization_id": request.authorization.authorization_id, "before_candidate_sha256": request.authorization.before_candidate_sha256, "after_candidate_sha256": after_hash, "validation": validation, "route_fingerprint": request.route_fingerprint}))
        except Exception:
            auditor_clean = False
        if not auditor_clean or validation.get("passed") is not True or validation.get("full_prefix") is not True:
            return self._result(request, R4Outcome.VERIFIED_NEGATIVE, "validation_or_audit_not_positive", after_hash=after_hash)
        return self._result(request, R4Outcome.VERIFIED_POSITIVE, "fresh_full_prefix_and_audit_passed", after_hash=after_hash, formal_validation_id=str(validation.get("validation_id", "validation")))

    def _result(self, request: R4ExecutionInput, outcome: R4Outcome, reason: str, *, after_hash: str | None = None, formal_validation_id: str | None = None, quarantine: bool = False) -> R4RunResult:
        record = None
        if quarantine:
            record = R4RevisionSafetyRecord(request.authorization.revision_sha256, request.authorization.authorization_id, reason, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        return R4RunResult(outcome, request.authorization.authorization_id, request.authorization.before_candidate_sha256, after_hash, formal_validation_id, self._provider_call_count, self._mutation_count, (reason,), record)


__all__ = ["R4CanaryManifest", "R4CandidateRepairAuthorization", "R4CandidateRepairController", "R4ContractError", "R4ExecutionInput", "R4KillSwitchState", "R4Outcome", "R4RevisionSafetyRecord", "R4RunResult"]
