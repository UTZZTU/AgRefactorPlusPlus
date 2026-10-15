"""Unified conservative recovery policy and exact action ledger.

This module is deliberately independent from validation/evidence packages so
policy objects can be consumed without creating import cycles.  Callers pass
normalized string values and retain authority for typed source evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from math import isfinite
import re
from threading import RLock
from typing import Any


class RecoveryAction(str, Enum):
    RETRY = "retry"
    REGENERATION = "regeneration"
    ADVISORY = "advisory"
    REPAIR = "repair"
    VALIDATION_RESTART = "validation_restart"
    NEW_OPTIMIZATION_ROUND = "new_optimization_round"


class RecoveryRole(str, Enum):
    CANDIDATE = "candidate"
    TESTBENCH = "testbench"
    TOOLCHAIN = "toolchain"
    CONFIGURATION = "configuration"
    ORIGINAL = "original"
    UNKNOWN = "unknown"


class RecoveryStage(str, Enum):
    PREFLIGHT = "preflight"
    PUBLIC_CSIM = "public_evaluation"
    CSYNTH = "csynth"
    PUBLIC_COSIM = "public_cosim"
    HIDDEN = "hidden_evaluation"
    PROVIDER = "provider"
    GENERATION = "generation"


class RecoveryAuthority(str, Enum):
    DETERMINISTIC_PROVEN = "deterministic_proven"
    PUBLIC_REFERENCE_QUALIFIED = "public_reference_qualified"
    PUBLIC_CSYNTH_BOUNDED_TRIAL = "public_csynth_bounded_trial"
    LLM_ADVISORY = "llm_advisory"
    UNKNOWN = "unknown"


# Evidence producers use descriptive authority labels. Keep the conversion
# explicit so arbitrary metadata cannot silently acquire repair authority.
_RECOVERY_AUTHORITY_ALIASES = {
    "deterministic_proven": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "preflight_component_completed": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "source_compile_unit": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "source_span": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "source_entry_contract": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "differential_isolation_proven": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "evaluated_case_failure": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "original_entry_not_executed": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "original_only_completed": RecoveryAuthority.DETERMINISTIC_PROVEN,
    "public_reference_qualified": RecoveryAuthority.PUBLIC_REFERENCE_QUALIFIED,
    "public_csynth_bounded_trial": RecoveryAuthority.PUBLIC_CSYNTH_BOUNDED_TRIAL,
    "llm_advisory": RecoveryAuthority.LLM_ADVISORY,
    "unknown": RecoveryAuthority.UNKNOWN,
}


def normalize_recovery_authority(value: object) -> RecoveryAuthority:
    """Normalize evidence authority without trusting arbitrary strings."""

    if isinstance(value, RecoveryAuthority):
        return value
    if not isinstance(value, str):
        return RecoveryAuthority.UNKNOWN
    return _RECOVERY_AUTHORITY_ALIASES.get(
        value.strip().casefold(),
        RecoveryAuthority.UNKNOWN,
    )


class RecoveryDecisionStatus(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    REVIEW_REQUIRED = "review_required"
    BUDGET_BLOCKED = "budget_blocked"


class RecoveryPolicyError(RuntimeError):
    """Base class for a policy/ledger rejection."""


class RecoveryDeniedError(RecoveryPolicyError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = _code(reason_code)
        super().__init__(self.reason_code)


class RecoveryBudgetBlockedError(RecoveryPolicyError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = _code(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class RecoveryLimits:
    provider_retries: int = 1
    response_regenerations: int = 3
    llm_advisories: int = 1
    tool_retries_per_stage: int = 1
    testbench_preflight_repairs: int = 3
    refactor_candidate_repairs_total: int = 3
    candidate_public_csim_repairs: int = 3
    candidate_public_cosim_repairs: int = 3
    testbench_public_csim_repairs: int = 3
    testbench_public_cosim_repairs: int = 3
    optimize_recoveries_per_root: int = 1
    hidden_repairs: int = 0
    total_recovery_actions: int = 5
    validation_restarts: int = 4

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.hidden_repairs != 0:
            raise ValueError("Hidden repair limit must remain zero")

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    action: RecoveryAction
    role: RecoveryRole
    stage: RecoveryStage
    evidence_view: str
    owner_authority: RecoveryAuthority
    lineage_id: str
    physical_tool_launched: bool = False
    evidence_complete: bool = False
    execution_evidence_complete: bool = False
    advisory_mode: str = "off"
    timeout_class: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _enum(self.action, RecoveryAction))
        object.__setattr__(self, "role", _enum(self.role, RecoveryRole))
        object.__setattr__(self, "stage", _enum(self.stage, RecoveryStage))
        object.__setattr__(
            self,
            "owner_authority",
            _enum(self.owner_authority, RecoveryAuthority),
        )
        if self.evidence_view not in {"agent_safe", "operator_full"}:
            raise ValueError("evidence_view must be agent_safe or operator_full")
        if not isinstance(self.lineage_id, str) or not self.lineage_id.strip():
            raise ValueError("lineage_id must not be empty")
        if not isinstance(self.physical_tool_launched, bool):
            raise TypeError("physical_tool_launched must be boolean")
        if not isinstance(self.evidence_complete, bool):
            raise TypeError("evidence_complete must be boolean")
        if not isinstance(self.execution_evidence_complete, bool):
            raise TypeError("execution_evidence_complete must be boolean")
        cleaned_mode = str(self.advisory_mode).strip().casefold()
        if cleaned_mode not in {"off", "candidate-only"}:
            raise ValueError("advisory_mode must be off or candidate-only")
        object.__setattr__(self, "advisory_mode", cleaned_mode)
        if self.timeout_class is not None:
            object.__setattr__(self, "timeout_class", _code(self.timeout_class))
        object.__setattr__(self, "lineage_id", self.lineage_id.strip())


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    status: RecoveryDecisionStatus
    reason_code: str
    request: RecoveryRequest
    limit_key: str | None = None
    limit: int | None = None
    observed: int | None = None
    restart_required: bool = False
    accepted_directly: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", _enum(self.status, RecoveryDecisionStatus)
        )
        object.__setattr__(self, "reason_code", _code(self.reason_code))
        if not isinstance(self.request, RecoveryRequest):
            raise TypeError("request must be RecoveryRequest")
        if self.accepted_directly:
            raise ValueError("Recovery policy must never directly accept a candidate")

    @property
    def allowed(self) -> bool:
        return self.status is RecoveryDecisionStatus.ALLOWED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "action": self.request.action.value,
            "role": self.request.role.value,
            "stage": self.request.stage.value,
            "evidence_view": self.request.evidence_view,
            "owner_authority": self.request.owner_authority.value,
            "lineage_id": self.request.lineage_id,
            "physical_tool_launched": self.request.physical_tool_launched,
            "evidence_complete": self.request.evidence_complete,
            "execution_evidence_complete": self.request.execution_evidence_complete,
            "advisory_mode": self.request.advisory_mode,
            "timeout_class": self.request.timeout_class,
            "limit_key": self.limit_key,
            "limit": self.limit,
            "observed": self.observed,
            "restart_required": self.restart_required,
            "accepted_directly": False,
        }


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    profile_name: str = "conservative-v1"
    limits: RecoveryLimits = field(default_factory=RecoveryLimits)

    def __post_init__(self) -> None:
        if self.profile_name != "conservative-v1":
            raise ValueError("Only conservative-v1 is implemented")
        if not isinstance(self.limits, RecoveryLimits):
            raise TypeError("limits must be RecoveryLimits")

    def decide(self, request: RecoveryRequest) -> RecoveryDecision:
        if not isinstance(request, RecoveryRequest):
            raise TypeError("request must be RecoveryRequest")

        if request.stage is RecoveryStage.HIDDEN:
            return self._deny(request, "hidden_recovery_permanently_forbidden")
        if request.evidence_view != "agent_safe" and request.action in {
            RecoveryAction.REPAIR,
            RecoveryAction.ADVISORY,
        }:
            return self._deny(request, "recovery_requires_agent_safe_evidence")

        if request.action is RecoveryAction.REPAIR:
            if request.owner_authority is RecoveryAuthority.PUBLIC_CSYNTH_BOUNDED_TRIAL:
                if (
                    request.role is not RecoveryRole.CANDIDATE
                    or request.stage is not RecoveryStage.CSYNTH
                    or request.evidence_view != "agent_safe"
                    or not request.physical_tool_launched
                    or not request.execution_evidence_complete
                    or request.evidence_complete
                    or request.timeout_class is not None
                ):
                    return self._review(
                        request,
                        "public_csynth_bounded_trial_requires_execution_evidence",
                    )
            elif request.owner_authority is RecoveryAuthority.PUBLIC_REFERENCE_QUALIFIED:
                if (request.role is not RecoveryRole.CANDIDATE
                        or request.stage not in {RecoveryStage.PREFLIGHT, RecoveryStage.PUBLIC_CSIM, RecoveryStage.PUBLIC_COSIM}
                        or not request.physical_tool_launched
                        or not request.evidence_complete):
                    return self._review(request, "public_unknown_requires_qualified_reference")
            elif request.owner_authority is RecoveryAuthority.LLM_ADVISORY:
                if request.advisory_mode != "candidate-only":
                    return self._review(request, "llm_advisory_repair_gate_disabled")
                if request.role is not RecoveryRole.CANDIDATE:
                    return self._review(request, "llm_advisory_testbench_repair_forbidden")
            elif request.owner_authority is not RecoveryAuthority.DETERMINISTIC_PROVEN:
                return self._review(request, "repair_owner_not_deterministically_proven")

            if request.role is RecoveryRole.CANDIDATE:
                if request.stage not in {
                    RecoveryStage.PREFLIGHT,
                    RecoveryStage.PUBLIC_CSIM,
                    RecoveryStage.CSYNTH,
                    RecoveryStage.PUBLIC_COSIM,
                }:
                    return self._deny(request, "candidate_repair_stage_not_eligible")
                if (request.owner_authority is not RecoveryAuthority.PUBLIC_REFERENCE_QUALIFIED
                        and request.timeout_class is not None and request.timeout_class not in {
                    "candidate_deadlock",
                    "candidate_stream_mismatch",
                }):
                    return self._review(request, "candidate_timeout_not_proven")
                return self._allow(request, "candidate_repair_eligible", restart=True)

            if request.role is RecoveryRole.TESTBENCH:
                if request.stage not in {
                    RecoveryStage.PREFLIGHT,
                    RecoveryStage.PUBLIC_CSIM,
                    RecoveryStage.PUBLIC_COSIM,
                }:
                    return self._deny(request, "testbench_repair_stage_not_eligible")
                if request.timeout_class is not None and request.timeout_class != (
                    "public_testbench_protocol_wait"
                ):
                    return self._review(request, "testbench_timeout_not_proven")
                return self._allow(request, "testbench_repair_eligible", restart=True)

            return self._deny(request, "repair_role_not_eligible")

        if request.action is RecoveryAction.ADVISORY:
            if request.advisory_mode == "off":
                return self._deny(request, "llm_advisory_disabled")
            if request.role is not RecoveryRole.UNKNOWN:
                return self._deny(request, "advisory_requires_unknown_owner")
            if request.stage not in {
                RecoveryStage.PUBLIC_CSIM,
                RecoveryStage.CSYNTH,
                RecoveryStage.PUBLIC_COSIM,
            }:
                return self._deny(request, "advisory_stage_not_eligible")
            if not request.physical_tool_launched:
                return self._deny(request, "advisory_requires_physical_tool_launch")
            if not request.evidence_complete:
                return self._deny(request, "advisory_requires_complete_evidence")
            return self._allow(request, "llm_advisory_eligible", restart=False)

        if request.action is RecoveryAction.RETRY:
            if request.role is not RecoveryRole.TOOLCHAIN:
                return self._deny(request, "retry_requires_proven_transient_toolchain_owner")
            if not request.physical_tool_launched:
                return self._deny(request, "retry_requires_physical_launch")
            return self._allow(request, "transient_tool_retry_eligible", restart=False)

        if request.action is RecoveryAction.VALIDATION_RESTART:
            return self._allow(request, "validation_restart_required", restart=False)

        if request.action is RecoveryAction.REGENERATION:
            if request.stage is not RecoveryStage.GENERATION:
                return self._deny(request, "regeneration_only_before_qualification")
            return self._allow(request, "generation_contract_regeneration_eligible")

        return self._deny(request, "recovery_action_not_eligible")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "profile_name": self.profile_name,
            "limits": self.limits.to_dict(),
            "hidden_repair_permanently_disabled": True,
            "llm_advisory_direct_acceptance": False,
        }

    @staticmethod
    def _allow(
        request: RecoveryRequest,
        reason: str,
        *,
        restart: bool = False,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            RecoveryDecisionStatus.ALLOWED,
            reason,
            request,
            restart_required=restart,
        )

    @staticmethod
    def _deny(request: RecoveryRequest, reason: str) -> RecoveryDecision:
        return RecoveryDecision(RecoveryDecisionStatus.DENIED, reason, request)

    @staticmethod
    def _review(request: RecoveryRequest, reason: str) -> RecoveryDecision:
        return RecoveryDecision(
            RecoveryDecisionStatus.REVIEW_REQUIRED, reason, request
        )


@dataclass(frozen=True, slots=True)
class RecoveryLedgerEvent:
    sequence: int
    decision: RecoveryDecision
    count_key: str | None
    accepted: bool

    def to_dict(self) -> dict[str, Any]:
        payload = self.decision.to_dict()
        payload.update(
            {
                "sequence": self.sequence,
                "count_key": self.count_key,
                "accepted": self.accepted,
            }
        )
        return payload


class RepairComponentBucket(str, Enum):
    CANDIDATE = "candidate"
    TESTBENCH_PUBLIC = "testbench_public"
    TESTBENCH_HIDDEN_GENERATION = "testbench_hidden_generation"
    STUB_PUBLIC = "stub_public"
    STUB_HIDDEN_GENERATION = "stub_hidden_generation"


@dataclass(frozen=True, slots=True)
class RepairComponentLimits:
    candidate: int = 3
    testbench_public: int = 3
    testbench_hidden_generation: int = 3
    stub_public: int = 3
    stub_hidden_generation: int = 3
    total_actions: int = 15

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    def limit_for(self, bucket: RepairComponentBucket | str) -> int:
        normalized = _enum(bucket, RepairComponentBucket)
        return getattr(self, normalized.value)

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class RepairActionCharge:
    scope_id: str
    logical_action_id: str
    components: tuple[RepairComponentBucket | str, ...]
    authorization_ref: str

    def __post_init__(self) -> None:
        scope_id = _ledger_identifier(self.scope_id, "scope_id")
        action_id = _ledger_identifier(
            self.logical_action_id,
            "logical_action_id",
        )
        authorization_ref = _ledger_identifier(
            self.authorization_ref,
            "authorization_ref",
        )
        if isinstance(self.components, (str, bytes)):
            raise TypeError("components must be a non-empty sequence")
        raw_components = tuple(self.components)
        components = tuple(
            sorted(
                {_enum(item, RepairComponentBucket) for item in raw_components},
                key=lambda item: item.value,
            )
        )
        if not components:
            raise ValueError("components must not be empty")
        if len(components) != len(raw_components):
            raise ValueError("components must be unique")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "logical_action_id", action_id)
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "authorization_ref", authorization_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "logical_action_id": self.logical_action_id,
            "components": [item.value for item in self.components],
            "authorization_ref": self.authorization_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairActionCharge":
        if not isinstance(value, Mapping):
            raise TypeError("charge must be a mapping")
        return cls(
            scope_id=value.get("scope_id"),
            logical_action_id=value.get("logical_action_id"),
            components=tuple(value.get("components", ())),
            authorization_ref=value.get("authorization_ref"),
        )


@dataclass(frozen=True, slots=True)
class RepairActionReservation:
    charge: RepairActionCharge

    def __post_init__(self) -> None:
        if not isinstance(self.charge, RepairActionCharge):
            raise TypeError("charge must be RepairActionCharge")


class RepairActionStatus(str, Enum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class RepairActionLedgerEvent:
    sequence: int
    event: str
    charge: RepairActionCharge
    accepted: bool
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "sequence": self.sequence,
            "event": self.event,
            "charge": self.charge.to_dict(),
            "accepted": self.accepted,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class _RepairActionState:
    charge: RepairActionCharge
    status: RepairActionStatus


class RecoveryLedger:
    """Exact run-local action ledger with stage/lineage/run-wide limits."""

    def __init__(
        self,
        policy: RecoveryPolicy | None = None,
        *,
        component_limits: RepairComponentLimits | None = None,
    ) -> None:
        self._policy = policy or RecoveryPolicy()
        if not isinstance(self._policy, RecoveryPolicy):
            raise TypeError("policy must be RecoveryPolicy")
        self._component_limits = component_limits or RepairComponentLimits()
        if not isinstance(self._component_limits, RepairComponentLimits):
            raise TypeError("component_limits must be RepairComponentLimits")
        self._counts: dict[str, int] = {}
        self._events: list[RecoveryLedgerEvent] = []
        self._component_actions: dict[
            tuple[str, str], _RepairActionState
        ] = {}
        self._component_action_events: list[RepairActionLedgerEvent] = []
        self._component_action_lock = RLock()

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    @property
    def events(self) -> tuple[RecoveryLedgerEvent, ...]:
        return tuple(self._events)

    @property
    def component_limits(self) -> RepairComponentLimits:
        return self._component_limits

    @property
    def component_action_events(self) -> tuple[RepairActionLedgerEvent, ...]:
        with self._component_action_lock:
            return tuple(self._component_action_events)

    def reserve_component_action(
        self,
        charge: RepairActionCharge,
    ) -> RepairActionReservation:
        """Reserve one action and all its component buckets atomically.

        The authorization reference is audit metadata. This method does not
        evaluate or grant repair authority; callers must pass their local gate
        before reserving.
        """

        if not isinstance(charge, RepairActionCharge):
            raise TypeError("charge must be RepairActionCharge")
        key = (charge.scope_id, charge.logical_action_id)
        with self._component_action_lock:
            existing = self._component_actions.get(key)
            if existing is not None:
                if existing.charge != charge:
                    raise ValueError(
                        "logical_action_id was reused with a different charge"
                    )
                reason = (
                    "logical_action_already_committed"
                    if existing.status is RepairActionStatus.COMMITTED
                    else "logical_action_already_reserved"
                )
                self._append_component_action_event(
                    "rejected",
                    charge,
                    accepted=False,
                    reason_code=reason,
                )
                raise RecoveryDeniedError(reason)

            if self._count_component_actions(charge.scope_id) >= (
                self._component_limits.total_actions
            ):
                return self._reject_component_action(
                    charge,
                    "component_global_action_limit_exhausted",
                )
            for bucket in charge.components:
                observed = self._count_component_actions(
                    charge.scope_id,
                    bucket,
                )
                if observed >= self._component_limits.limit_for(bucket):
                    return self._reject_component_action(
                        charge,
                        "component_action_limit_exhausted",
                    )

            self._component_actions[key] = _RepairActionState(
                charge,
                RepairActionStatus.RESERVED,
            )
            self._append_component_action_event(
                "reserved",
                charge,
                accepted=True,
            )
            return RepairActionReservation(charge)

    def commit_dispatched_action(
        self,
        reservation: RepairActionReservation,
    ) -> None:
        if not isinstance(reservation, RepairActionReservation):
            raise TypeError("reservation must be RepairActionReservation")
        charge = reservation.charge
        key = (charge.scope_id, charge.logical_action_id)
        with self._component_action_lock:
            state = self._component_actions.get(key)
            if state is None or state.charge != charge:
                raise ValueError("reservation is not present in this ledger")
            if state.status is RepairActionStatus.COMMITTED:
                return
            if state.status is RepairActionStatus.RELEASED:
                raise ValueError("released action cannot be committed")
            self._component_actions[key] = _RepairActionState(
                charge,
                RepairActionStatus.COMMITTED,
            )
            self._append_component_action_event(
                "committed",
                charge,
                accepted=True,
            )

    def release_component_action(
        self,
        reservation: RepairActionReservation,
    ) -> None:
        if not isinstance(reservation, RepairActionReservation):
            raise TypeError("reservation must be RepairActionReservation")
        charge = reservation.charge
        key = (charge.scope_id, charge.logical_action_id)
        with self._component_action_lock:
            state = self._component_actions.get(key)
            if state is None or state.charge != charge:
                raise ValueError("reservation is not present in this ledger")
            if state.status is RepairActionStatus.RELEASED:
                return
            if state.status is RepairActionStatus.COMMITTED:
                raise ValueError("dispatched action cannot be released")
            self._component_actions[key] = _RepairActionState(
                charge,
                RepairActionStatus.RELEASED,
            )
            self._append_component_action_event(
                "released",
                charge,
                accepted=True,
            )

    def committed_component_action_charges(
        self,
        scope_id: str | None = None,
    ) -> tuple[RepairActionCharge, ...]:
        normalized_scope = (
            None
            if scope_id is None
            else _ledger_identifier(scope_id, "scope_id")
        )
        with self._component_action_lock:
            charges = [
                state.charge
                for state in self._component_actions.values()
                if state.status is RepairActionStatus.COMMITTED
                and (
                    normalized_scope is None
                    or state.charge.scope_id == normalized_scope
                )
            ]
            return tuple(
                sorted(
                    charges,
                    key=lambda item: (item.scope_id, item.logical_action_id),
                )
            )

    def restore_committed_component_actions(
        self,
        charges: Iterable[RepairActionCharge],
    ) -> None:
        """Restore committed charges from trusted local case/action records."""

        normalized = tuple(charges)
        if not all(isinstance(item, RepairActionCharge) for item in normalized):
            raise TypeError("charges must contain RepairActionCharge")
        keys = [(item.scope_id, item.logical_action_id) for item in normalized]
        if len(keys) != len(set(keys)):
            raise ValueError("restored logical action IDs must be unique")
        with self._component_action_lock:
            additions: list[RepairActionCharge] = []
            for charge in normalized:
                key = (charge.scope_id, charge.logical_action_id)
                existing = self._component_actions.get(key)
                if existing is not None:
                    if (
                        existing.charge == charge
                        and existing.status is RepairActionStatus.COMMITTED
                    ):
                        continue
                    raise ValueError("restored action conflicts with ledger state")
                additions.append(charge)

            usage: dict[tuple[str, RepairComponentBucket | None], int] = {}
            for state in self._component_actions.values():
                if state.status is RepairActionStatus.RELEASED:
                    continue
                scope = state.charge.scope_id
                total_key = (scope, None)
                usage[total_key] = usage.get(total_key, 0) + 1
                for bucket in state.charge.components:
                    bucket_key = (scope, bucket)
                    usage[bucket_key] = usage.get(bucket_key, 0) + 1
            for charge in additions:
                total_key = (charge.scope_id, None)
                if usage.get(total_key, 0) >= self._component_limits.total_actions:
                    raise RecoveryBudgetBlockedError(
                        "component_global_action_limit_exhausted"
                    )
                usage[total_key] = usage.get(total_key, 0) + 1
                for bucket in charge.components:
                    bucket_key = (charge.scope_id, bucket)
                    if usage.get(bucket_key, 0) >= self._component_limits.limit_for(
                        bucket
                    ):
                        raise RecoveryBudgetBlockedError(
                            "component_action_limit_exhausted"
                        )
                    usage[bucket_key] = usage.get(bucket_key, 0) + 1
            for charge in additions:
                key = (charge.scope_id, charge.logical_action_id)
                self._component_actions[key] = _RepairActionState(
                    charge,
                    RepairActionStatus.COMMITTED,
                )
                self._append_component_action_event(
                    "restored",
                    charge,
                    accepted=True,
                )

    def component_action_snapshot(
        self,
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = (
            None
            if scope_id is None
            else _ledger_identifier(scope_id, "scope_id")
        )
        with self._component_action_lock:
            states = [
                state
                for state in self._component_actions.values()
                if normalized_scope is None
                or state.charge.scope_id == normalized_scope
            ]
            states.sort(
                key=lambda item: (
                    item.charge.scope_id,
                    item.charge.logical_action_id,
                )
            )
            events = [
                event.to_dict()
                for event in self._component_action_events
                if normalized_scope is None
                or event.charge.scope_id == normalized_scope
            ]
            return {
                "schema_version": 1,
                "limits": self._component_limits.to_dict(),
                "actions": [
                    {
                        "charge": state.charge.to_dict(),
                        "status": state.status.value,
                    }
                    for state in states
                ],
                "events": events,
            }

    def _count_component_actions(
        self,
        scope_id: str,
        bucket: RepairComponentBucket | None = None,
    ) -> int:
        return sum(
            1
            for state in self._component_actions.values()
            if state.charge.scope_id == scope_id
            and state.status is not RepairActionStatus.RELEASED
            and (bucket is None or bucket in state.charge.components)
        )

    def _reject_component_action(
        self,
        charge: RepairActionCharge,
        reason_code: str,
    ):
        self._append_component_action_event(
            "rejected",
            charge,
            accepted=False,
            reason_code=reason_code,
        )
        raise RecoveryBudgetBlockedError(reason_code)

    def _append_component_action_event(
        self,
        event: str,
        charge: RepairActionCharge,
        *,
        accepted: bool,
        reason_code: str | None = None,
    ) -> None:
        self._component_action_events.append(
            RepairActionLedgerEvent(
                sequence=len(self._component_action_events) + 1,
                event=event,
                charge=charge,
                accepted=accepted,
                reason_code=reason_code,
            )
        )


    def reserve(
        self,
        request: RecoveryRequest,
        *,
        budget: Any | None = None,
        restart_reserve: Mapping[str, int] | None = None,
    ) -> RecoveryDecision:
        decision = self._policy.decide(request)
        if not decision.allowed:
            self._append(decision, None, False)
            if decision.status is RecoveryDecisionStatus.REVIEW_REQUIRED:
                raise RecoveryDeniedError(decision.reason_code)
            raise RecoveryDeniedError(decision.reason_code)

        key, limit = self._limit_key(request)
        additional_limits = self._additional_limit_keys(request, key)
        for checked_key, checked_limit in ((key, limit), *additional_limits):
            observed = self._counts.get(checked_key, 0)
            if observed >= checked_limit:
                blocked = RecoveryDecision(
                    RecoveryDecisionStatus.DENIED,
                    "recovery_limit_exhausted",
                    request,
                    limit_key=checked_key,
                    limit=checked_limit,
                    observed=observed,
                    restart_required=decision.restart_required,
                )
                self._append(blocked, checked_key, False)
                raise RecoveryDeniedError(blocked.reason_code)

        total = self._counts.get("run:total_recovery_actions", 0)
        if request.action not in {RecoveryAction.VALIDATION_RESTART}:
            if total >= self._policy.limits.total_recovery_actions:
                blocked = RecoveryDecision(
                    RecoveryDecisionStatus.DENIED,
                    "run_total_recovery_actions_exhausted",
                    request,
                    limit_key="run:total_recovery_actions",
                    limit=self._policy.limits.total_recovery_actions,
                    observed=total,
                    restart_required=decision.restart_required,
                )
                self._append(blocked, key, False)
                raise RecoveryDeniedError(blocked.reason_code)

        if budget is not None:
            increments = dict(restart_reserve or {})
            wall_time_s = increments.pop("wall_time_s", 0.0)
            try:
                if (
                    isinstance(wall_time_s, bool)
                    or not isinstance(wall_time_s, (int, float))
                    or not isfinite(float(wall_time_s))
                    or wall_time_s < 0
                ):
                    raise ValueError(
                        "restart wall_time_s must be finite and non-negative"
                    )
                if wall_time_s:
                    limits = getattr(budget, "limits", None)
                    configured = getattr(limits, "max_wall_time_s", None)
                    active = getattr(budget, "active_reserve", None)
                    reserved = getattr(active, "max_wall_time_s", None)
                    if configured is not None:
                        effective = float(configured) - float(reserved or 0.0)
                        elapsed = float(budget.snapshot().elapsed_s)
                        if elapsed + float(wall_time_s) > effective:
                            raise RuntimeError(
                                "insufficient wall time for full validation restart"
                            )
                budget.ensure_available(**increments)
            except Exception as exc:  # budget type remains owned by runtime.
                blocked = RecoveryDecision(
                    RecoveryDecisionStatus.BUDGET_BLOCKED,
                    "recovery_restart_budget_unavailable",
                    request,
                    limit_key=key,
                    limit=limit,
                    observed=observed,
                    restart_required=decision.restart_required,
                )
                self._append(blocked, key, False)
                raise RecoveryBudgetBlockedError(blocked.reason_code) from exc

        self._counts[key] = self._counts.get(key, 0) + 1
        for additional_key, _ in additional_limits:
            self._counts[additional_key] = (
                self._counts.get(additional_key, 0) + 1
            )
        if request.action not in {RecoveryAction.VALIDATION_RESTART}:
            self._counts["run:total_recovery_actions"] = total + 1
        self._append(decision, key, True)
        return decision

    def record_validation_restart(
        self,
        *,
        lineage_id: str,
        stage: RecoveryStage,
        budget: Any | None = None,
        restart_reserve: Mapping[str, int] | None = None,
    ) -> RecoveryDecision:
        return self.reserve(
            RecoveryRequest(
                action=RecoveryAction.VALIDATION_RESTART,
                role=RecoveryRole.CANDIDATE,
                stage=stage,
                evidence_view="agent_safe",
                owner_authority=RecoveryAuthority.DETERMINISTIC_PROVEN,
                lineage_id=lineage_id,
                physical_tool_launched=True,
                evidence_complete=True,
            ),
            budget=budget,
            restart_reserve=restart_reserve,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy": self._policy.to_dict(),
            "counts": dict(sorted(self._counts.items())),
            "events": [item.to_dict() for item in self._events],
            "accepted_directly": False,
        }

    def json_text(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def _limit_key(self, request: RecoveryRequest) -> tuple[str, int]:
        limits = self._policy.limits
        prefix = f"lineage:{request.lineage_id}"
        if request.action is RecoveryAction.VALIDATION_RESTART:
            return f"{prefix}:validation_restart", limits.validation_restarts
        if request.action is RecoveryAction.ADVISORY:
            return f"{prefix}:advisory", limits.llm_advisories
        if request.action is RecoveryAction.RETRY:
            return (
                f"{prefix}:retry:{request.stage.value}",
                limits.tool_retries_per_stage,
            )
        if request.action is RecoveryAction.REGENERATION:
            return f"{prefix}:regeneration", limits.response_regenerations
        if request.action is RecoveryAction.REPAIR:
            if request.role is RecoveryRole.CANDIDATE:
                if request.stage is RecoveryStage.PUBLIC_CSIM:
                    return (
                        f"{prefix}:candidate:public_csim",
                        limits.candidate_public_csim_repairs,
                    )
                if request.stage is RecoveryStage.PUBLIC_COSIM:
                    return (
                        f"{prefix}:candidate:public_cosim",
                        limits.candidate_public_cosim_repairs,
                    )
                return (
                    f"{prefix}:candidate:total",
                    limits.refactor_candidate_repairs_total,
                )
            if request.role is RecoveryRole.TESTBENCH:
                if request.stage is RecoveryStage.PUBLIC_CSIM:
                    return (
                        f"{prefix}:testbench:public_csim",
                        limits.testbench_public_csim_repairs,
                    )
                if request.stage is RecoveryStage.PUBLIC_COSIM:
                    return (
                        f"{prefix}:testbench:public_cosim",
                        limits.testbench_public_cosim_repairs,
                    )
                return (
                    f"{prefix}:testbench:preflight",
                    limits.testbench_preflight_repairs,
                )
        return f"{prefix}:{request.action.value}", 0

    def _additional_limit_keys(
        self,
        request: RecoveryRequest,
        primary_key: str,
    ) -> tuple[tuple[str, int], ...]:
        """Return aggregate limits that supplement the stage lane."""

        if request.action is not RecoveryAction.REPAIR:
            return ()
        if request.role is not RecoveryRole.CANDIDATE:
            return ()
        aggregate_key = (
            f"lineage:{request.lineage_id}:candidate:repairs_total"
        )
        if aggregate_key == primary_key:
            return ()
        return (
            (
                aggregate_key,
                self._policy.limits.refactor_candidate_repairs_total,
            ),
        )

    def _append(
        self,
        decision: RecoveryDecision,
        key: str | None,
        accepted: bool,
    ) -> None:
        self._events.append(
            RecoveryLedgerEvent(
                sequence=len(self._events) + 1,
                decision=decision,
                count_key=key,
                accepted=accepted,
            )
        )


def conservative_v1_policy() -> RecoveryPolicy:
    return RecoveryPolicy()


def default_restart_reserve(stage: RecoveryStage | str) -> dict[str, int]:
    normalized = _enum(stage, RecoveryStage)
    reserve = {
        "llm_calls": 1,
        "tool_calls": 1,
        "compile_calls": 2,
        "csim_calls": 0,
        "csynth_calls": 1,
        "cosim_calls": 0,
        "wall_time_s": 1200.0,
    }
    if normalized in {RecoveryStage.PUBLIC_CSIM, RecoveryStage.PUBLIC_COSIM}:
        reserve["csim_calls"] = 1
        reserve["cosim_calls"] = 1
        reserve["tool_calls"] = 3
    return reserve


def _enum(value: Any, enum_type):
    if isinstance(value, enum_type):
        return value
    return enum_type(str(value))


def _code(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("reason code must be a string")
    cleaned = value.strip().casefold()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_-."
    if not cleaned or any(character not in allowed for character in cleaned):
        raise ValueError(f"unsafe reason code: {value!r}")
    return cleaned


def _ledger_identifier(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > 256
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", cleaned) is None
    ):
        raise ValueError(f"{name} must be a bounded stable identifier")
    return cleaned
