"""Read-only explanation of the effective repair window.

This module never reserves or consumes budget.  RecoveryLedger and
BudgetManager remain the only action and hard-budget authorities.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from .policy import RecoveryLedger


@dataclass(frozen=True, slots=True)
class EffectiveRepairQuotaSummary:
    requested: Mapping[str, int]
    lane_local_limits: Mapping[str, int]
    policy_limits: Mapping[str, int]
    policy_counts: Mapping[str, int]
    hard_budget: Mapping[str, Any]
    accepted_attempts: Mapping[str, int]
    denied_reason_counts: Mapping[str, int]
    effective_configured_ceilings: Mapping[str, int]

    schema_version = 1

    def __post_init__(self) -> None:
        for name in (
            "requested",
            "lane_local_limits",
            "policy_limits",
            "policy_counts",
            "hard_budget",
            "accepted_attempts",
            "denied_reason_counts",
            "effective_configured_ceilings",
        ):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            object.__setattr__(
                self,
                name,
                json.loads(json.dumps(dict(value), sort_keys=True, allow_nan=False)),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "requested": dict(self.requested),
            "lane_local_limits": dict(self.lane_local_limits),
            "policy_limits": dict(self.policy_limits),
            "policy_counts": dict(self.policy_counts),
            "hard_budget": dict(self.hard_budget),
            "accepted_attempts": dict(self.accepted_attempts),
            "denied_reason_counts": dict(self.denied_reason_counts),
            "effective_configured_ceilings": dict(self.effective_configured_ceilings),
            "authority": "explanation_only",
            "creates_budget_authority": False,
            "creates_action_counter": False,
            "recovery_ledger_authoritative": True,
            "budget_manager_authoritative": True,
        }


def build_effective_repair_quota_summary(
    *,
    ledger: RecoveryLedger,
    budget: Any,
    candidate_requested_max: int,
    runtime_testbench_local_max: int = 1,
) -> EffectiveRepairQuotaSummary:
    if not isinstance(ledger, RecoveryLedger):
        raise TypeError("ledger must be a RecoveryLedger")
    for name, value in (
        ("candidate_requested_max", candidate_requested_max),
        ("runtime_testbench_local_max", runtime_testbench_local_max),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    policy = ledger.policy.limits.to_dict()
    ledger_payload = ledger.to_dict()
    counts = dict(ledger_payload["counts"])
    accepted = Counter()
    denied = Counter()
    for event in ledger.events:
        payload = event.to_dict()
        if event.accepted and payload.get("action") == "repair":
            lane = f"{payload.get('role', 'unknown')}:{payload.get('stage', 'unknown')}"
            accepted[lane] += 1
        if not event.accepted:
            denied[str(payload.get("reason_code") or "unknown")] += 1
    hard_budget = _hard_budget_snapshot(budget)
    lane_local = {
        "candidate_formal": candidate_requested_max,
        "testbench_preflight": runtime_testbench_local_max,
        "testbench_public_csim": runtime_testbench_local_max,
        "testbench_public_cosim": runtime_testbench_local_max,
    }
    requested = {
        "candidate_formal": candidate_requested_max,
        "testbench_runtime": runtime_testbench_local_max,
    }
    ceilings = {
        "candidate_formal": min(
            candidate_requested_max,
            policy["refactor_candidate_repairs_total"],
            policy["total_recovery_actions"],
        ),
        "testbench_preflight": min(
            runtime_testbench_local_max,
            policy["testbench_preflight_repairs"],
            policy["total_recovery_actions"],
        ),
        "testbench_public_csim": min(
            runtime_testbench_local_max,
            policy["testbench_public_csim_repairs"],
            policy["total_recovery_actions"],
        ),
        "testbench_public_cosim": min(
            runtime_testbench_local_max,
            policy["testbench_public_cosim_repairs"],
            policy["total_recovery_actions"],
        ),
    }
    return EffectiveRepairQuotaSummary(
        requested=requested,
        lane_local_limits=lane_local,
        policy_limits=policy,
        policy_counts=counts,
        hard_budget=hard_budget,
        accepted_attempts=dict(sorted(accepted.items())),
        denied_reason_counts=dict(sorted(denied.items())),
        effective_configured_ceilings=ceilings,
    )


def _hard_budget_snapshot(budget: Any) -> dict[str, Any]:
    limits = getattr(budget, "limits", None)
    try:
        snapshot = budget.snapshot()
    except Exception as exc:
        if exc.__class__.__name__ != "BudgetExceededError":
            raise
        snapshot = budget.record_observed()
    usage = snapshot.to_dict()
    reserve = (
        budget.active_reserve_dict()
        if callable(getattr(budget, "active_reserve_dict", None))
        else {}
    )
    pairs = {
        "llm_calls": "max_llm_calls",
        "tool_calls": "max_tool_calls",
        "compile_calls": "max_compile_calls",
        "csim_calls": "max_csim_calls",
        "csynth_calls": "max_csynth_calls",
        "cosim_calls": "max_cosim_calls",
        "iterations": "max_iterations",
        "elapsed_s": "max_wall_time_s",
    }
    resources: dict[str, Any] = {}
    for usage_name, limit_name in pairs.items():
        configured = getattr(limits, limit_name, None)
        observed = usage.get(usage_name)
        reserved = reserve.get(limit_name)
        remaining = None
        if configured is not None and isinstance(observed, (int, float)):
            remaining = max(0, configured - observed - (reserved or 0))
        resources[usage_name] = {
            "configured": configured,
            "observed": observed,
            "active_reserve": reserved,
            "remaining_now": remaining,
        }
    return {
        "resources": resources,
        "snapshot": usage,
        "explanation_time": "orchestration_finish",
    }
