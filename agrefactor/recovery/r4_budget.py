"""Plan-derived full-prefix budget admission artifacts for R4."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from typing import Any

from agrefactor.runtime.budget import BudgetLimits, BudgetManager


_PHASE_ORDER = (
    "preflight",
    "public_evaluation",
    "csynth",
    "public_cosim",
    "hidden_evaluation",
)
_PHASE_COSTS = {
    "preflight": {"tool_calls": 1, "compile_calls": 1},
    "public_evaluation": {"tool_calls": 1, "csim_calls": 1},
    "csynth": {"tool_calls": 1, "csynth_calls": 1},
    "public_cosim": {"tool_calls": 2, "cosim_calls": 1},
    "hidden_evaluation": {
        "tool_calls": 2,
        "compile_calls": 1,
        "csim_calls": 1,
    },
}
_COUNTERS = (
    "llm_calls",
    "tool_calls",
    "compile_calls",
    "csim_calls",
    "csynth_calls",
    "cosim_calls",
    "tokens",
    "cost_usd",
    "elapsed_s",
)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plain_mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result = json.loads(
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True)
    )
    if not isinstance(result, dict):
        raise TypeError(f"{name} must be an object")
    return result


def _expected_counts(phases: tuple[str, ...]) -> dict[str, int]:
    counts = {
        "tool_calls": 0,
        "compile_calls": 0,
        "csim_calls": 0,
        "csynth_calls": 0,
        "cosim_calls": 0,
    }
    for phase in phases:
        if phase not in _PHASE_COSTS:
            raise ValueError(f"unsupported R4 validation phase: {phase}")
        for name, increment in _PHASE_COSTS[phase].items():
            counts[name] += increment
    return counts


@dataclass(frozen=True, slots=True)
class R4ReservePlan:
    provider_calls: int = 1
    mutation_calls: int = 1
    auditor_reads: int = 1
    tool_calls: int = 0
    compile_calls: int = 0
    csim_calls: int = 0
    csynth_calls: int = 0
    cosim_calls: int = 0
    wall_time_s: float = 0.0
    phase_order: tuple[str, ...] = ("preflight", "csynth")
    source: str = "validation_handler_plan_v1"

    def __post_init__(self) -> None:
        integer_fields = (
            "provider_calls",
            "mutation_calls",
            "auditor_reads",
            "tool_calls",
            "compile_calls",
            "csim_calls",
            "csynth_calls",
            "cosim_calls",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.provider_calls != 1 or self.mutation_calls != 1:
            raise ValueError("R4 requires exactly one provider call and one mutation")
        if self.auditor_reads != 1:
            raise ValueError("R4 requires exactly one independent auditor read")
        if (
            isinstance(self.wall_time_s, bool)
            or not isinstance(self.wall_time_s, (int, float))
            or not isfinite(float(self.wall_time_s))
            or self.wall_time_s <= 0
        ):
            raise ValueError("wall_time_s must be finite and positive")
        phases = tuple(self.phase_order)
        if phases != tuple(phase for phase in _PHASE_ORDER if phase in phases):
            raise ValueError("R4 phase_order must follow the existing validator order")
        if not phases or phases[0] != "preflight" or "csynth" not in phases:
            raise ValueError("R4 reserve must cover preflight and csynth")
        if len(phases) != len(set(phases)):
            raise ValueError("R4 phase_order must not contain duplicates")
        expected = _expected_counts(phases)
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ValueError(f"{name} does not match the validation handler plan")
        if self.source != "validation_handler_plan_v1":
            raise ValueError("unsupported R4 reserve source")
        object.__setattr__(self, "phase_order", phases)
        object.__setattr__(self, "wall_time_s", float(self.wall_time_s))

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "provider_calls": self.provider_calls,
            "mutation_calls": self.mutation_calls,
            "auditor_reads": self.auditor_reads,
            "tool_calls": self.tool_calls,
            "compile_calls": self.compile_calls,
            "csim_calls": self.csim_calls,
            "csynth_calls": self.csynth_calls,
            "cosim_calls": self.cosim_calls,
            "wall_time_s": self.wall_time_s,
            "phase_order": list(self.phase_order),
            "source": self.source,
            "admission_semantics": "prospective_no_consumption",
        }

    @property
    def plan_sha256(self) -> str:
        return _canonical_sha256(self._payload())

    def to_budget_limits(self) -> BudgetLimits:
        return BudgetLimits(
            max_llm_calls=self.provider_calls,
            max_tool_calls=self.tool_calls,
            max_compile_calls=self.compile_calls,
            max_csim_calls=self.csim_calls,
            max_csynth_calls=self.csynth_calls,
            max_cosim_calls=self.cosim_calls,
            max_wall_time_s=self.wall_time_s,
        )

    def to_budget_request(self) -> dict[str, int | float]:
        return {
            "llm_calls": self.provider_calls,
            "tool_calls": self.tool_calls,
            "compile_calls": self.compile_calls,
            "csim_calls": self.csim_calls,
            "csynth_calls": self.csynth_calls,
            "cosim_calls": self.cosim_calls,
            "wall_time_s": self.wall_time_s,
        }

    def ensure_available(self, budget: BudgetManager) -> None:
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")
        request = self.to_budget_request()
        wall_time_s = float(request.pop("wall_time_s"))
        budget.ensure_available(**request)
        limit = budget.limits.max_wall_time_s
        if limit is not None and budget.snapshot().elapsed_s + wall_time_s > limit:
            raise RuntimeError("insufficient wall-time capacity for R4 full prefix")

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "plan_sha256": self.plan_sha256}


@dataclass(frozen=True, slots=True)
class R4BudgetReservation:
    requested: Mapping[str, Any]
    effective: Mapping[str, Any]
    budget_before: Mapping[str, Any]
    budget_after_admission: Mapping[str, Any]
    admitted: bool = True
    reservation_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "requested",
            "effective",
            "budget_before",
            "budget_after_admission",
        ):
            object.__setattr__(self, name, _plain_mapping(getattr(self, name), name))
        if self.admitted is not True:
            raise ValueError("R4BudgetReservation represents admitted plans only")
        if self.requested.get("plan_sha256") != self.effective.get("plan_sha256"):
            raise ValueError("requested and effective R4 plans must match")
        for counter in _COUNTERS:
            before = self.budget_before.get(counter)
            after = self.budget_after_admission.get(counter)
            if not isinstance(before, (int, float)) or isinstance(before, bool):
                raise ValueError(f"budget_before missing numeric {counter}")
            if not isinstance(after, (int, float)) or isinstance(after, bool):
                raise ValueError(f"budget_after_admission missing numeric {counter}")
            if counter != "elapsed_s" and after != before:
                raise ValueError("prospective R4 admission must not consume budget")
            if counter == "elapsed_s" and after < before:
                raise ValueError("budget elapsed time must be monotonic")
        expected = _canonical_sha256(self._payload())
        if self.reservation_id and self.reservation_id != expected:
            raise ValueError("R4 budget reservation_id mismatch")
        object.__setattr__(self, "reservation_id", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "requested": dict(self.requested),
            "effective": dict(self.effective),
            "budget_before": dict(self.budget_before),
            "budget_after_admission": dict(self.budget_after_admission),
            "admitted": self.admitted,
            "admission_semantics": "prospective_no_consumption",
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "reservation_id": self.reservation_id}


def build_r4_reserve_plan_from_handlers(
    handlers: Mapping[Any, Any],
    *,
    wall_time_s: float,
    expected_plan: R4ReservePlan | None = None,
) -> R4ReservePlan:
    if not isinstance(handlers, Mapping):
        raise TypeError("handlers must be the existing validation handler mapping")
    names: set[str] = set()
    for state, handler in handlers.items():
        name = getattr(state, "value", str(state))
        if name not in _PHASE_COSTS:
            raise ValueError(f"unsupported validation handler state: {name}")
        if not callable(handler):
            raise TypeError(f"validation handler for {name} must be callable")
        names.add(name)
    phases = tuple(phase for phase in _PHASE_ORDER if phase in names)
    if len(phases) != len(handlers):
        raise ValueError("validation handler plan contains duplicate state aliases")
    counts = _expected_counts(phases)
    plan = R4ReservePlan(
        tool_calls=counts["tool_calls"],
        compile_calls=counts["compile_calls"],
        csim_calls=counts["csim_calls"],
        csynth_calls=counts["csynth_calls"],
        cosim_calls=counts["cosim_calls"],
        wall_time_s=wall_time_s,
        phase_order=phases,
    )
    if expected_plan is not None:
        if not isinstance(expected_plan, R4ReservePlan):
            raise TypeError("expected_plan must be R4ReservePlan or None")
        if expected_plan.plan_sha256 != plan.plan_sha256:
            raise ValueError("configured R4 reserve does not match validation handler plan")
    return plan


def build_r4_reserve_plan(
    *,
    public_csim: bool,
    csynth: bool,
    public_cosim: bool,
    hidden_evaluation: bool,
    tool_calls: int,
    compile_calls: int,
    wall_time_s: float,
) -> R4ReservePlan:
    """Compatibility builder that now rejects non-plan-derived counts."""
    if not all(
        isinstance(value, bool)
        for value in (public_csim, csynth, public_cosim, hidden_evaluation)
    ):
        raise TypeError("phase selectors must be boolean")
    phases = ["preflight"]
    if public_csim:
        phases.append("public_evaluation")
    if csynth:
        phases.append("csynth")
    if public_cosim:
        phases.append("public_cosim")
    if hidden_evaluation:
        phases.append("hidden_evaluation")
    counts = _expected_counts(tuple(phases))
    if tool_calls != counts["tool_calls"] or compile_calls != counts["compile_calls"]:
        raise ValueError("manual reserve counts do not match the declared validation plan")
    return R4ReservePlan(
        tool_calls=counts["tool_calls"],
        compile_calls=counts["compile_calls"],
        csim_calls=counts["csim_calls"],
        csynth_calls=counts["csynth_calls"],
        cosim_calls=counts["cosim_calls"],
        wall_time_s=wall_time_s,
        phase_order=tuple(phases),
    )


def record_r4_budget_reservation(
    plan: R4ReservePlan,
    *,
    budget_before: Mapping[str, Any],
    budget_after_admission: Mapping[str, Any],
) -> R4BudgetReservation:
    if not isinstance(plan, R4ReservePlan):
        raise TypeError("plan must be R4ReservePlan")
    artifact = plan.to_dict()
    return R4BudgetReservation(
        requested=artifact,
        effective=artifact,
        budget_before=budget_before,
        budget_after_admission=budget_after_admission,
    )


def build_r4_budget_actual(
    plan: R4ReservePlan,
    *,
    budget_before: Mapping[str, Any],
    budget_after: Mapping[str, Any],
    provider_calls: int,
    mutation_calls: int,
    auditor_reads: int,
) -> dict[str, Any]:
    before = _plain_mapping(budget_before, "budget_before")
    after = _plain_mapping(budget_after, "budget_after")
    delta: dict[str, int | float] = {}
    for counter in _COUNTERS:
        left = before.get(counter)
        right = after.get(counter)
        if not isinstance(left, (int, float)) or isinstance(left, bool):
            raise ValueError(f"budget_before missing numeric {counter}")
        if not isinstance(right, (int, float)) or isinstance(right, bool):
            raise ValueError(f"budget_after missing numeric {counter}")
        value = right - left
        if value < 0:
            raise ValueError(f"budget counter decreased: {counter}")
        delta[counter] = value
    return {
        "schema_version": 1,
        "plan_sha256": plan.plan_sha256,
        "provider_calls": provider_calls,
        "mutation_calls": mutation_calls,
        "auditor_reads": auditor_reads,
        "budget_delta": delta,
    }


__all__ = [
    "R4BudgetReservation",
    "R4ReservePlan",
    "build_r4_budget_actual",
    "build_r4_reserve_plan",
    "build_r4_reserve_plan_from_handlers",
    "record_r4_budget_reservation",
]
