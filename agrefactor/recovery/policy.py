"""Unified conservative recovery policy and exact action ledger.

This module is deliberately independent from validation/evidence packages so
policy objects can be consumed without creating import cycles.  Callers pass
normalized string values and retain authority for typed source evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from math import isfinite
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
    LLM_ADVISORY = "llm_advisory"
    UNKNOWN = "unknown"


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
    response_regenerations: int = 1
    llm_advisories: int = 1
    tool_retries_per_stage: int = 1
    testbench_preflight_repairs: int = 3
    refactor_candidate_repairs_total: int = 3
    candidate_public_csim_repairs: int = 1
    candidate_public_cosim_repairs: int = 1
    testbench_public_csim_repairs: int = 1
    testbench_public_cosim_repairs: int = 1
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
            if request.owner_authority is RecoveryAuthority.LLM_ADVISORY:
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
                if request.timeout_class is not None and request.timeout_class not in {
                    "candidate_deadlock",
                    "candidate_stream_mismatch",
                }:
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


class RecoveryLedger:
    """Exact run-local action ledger with stage/lineage/run-wide limits."""

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self._policy = policy or RecoveryPolicy()
        if not isinstance(self._policy, RecoveryPolicy):
            raise TypeError("policy must be RecoveryPolicy")
        self._counts: dict[str, int] = {}
        self._events: list[RecoveryLedgerEvent] = []

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    @property
    def events(self) -> tuple[RecoveryLedgerEvent, ...]:
        return tuple(self._events)

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
        observed = self._counts.get(key, 0)
        if observed >= limit:
            blocked = RecoveryDecision(
                RecoveryDecisionStatus.DENIED,
                "recovery_limit_exhausted",
                request,
                limit_key=key,
                limit=limit,
                observed=observed,
                restart_required=decision.restart_required,
            )
            self._append(blocked, key, False)
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

        self._counts[key] = observed + 1
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
