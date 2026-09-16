"""Full-prefix reserve contract for future R4 integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agrefactor.runtime.budget import BudgetLimits, BudgetManager


@dataclass(frozen=True, slots=True)
class R4ReservePlan:
    provider_calls: int = 1
    mutation_calls: int = 1
    tool_calls: int = 0
    compile_calls: int = 0
    csim_calls: int = 0
    csynth_calls: int = 0
    cosim_calls: int = 0
    wall_time_s: float = 0.0
    phase_order: tuple[str, ...] = ("preflight", "csynth")

    def __post_init__(self) -> None:
        for name in ("provider_calls", "mutation_calls", "tool_calls", "compile_calls", "csim_calls", "csynth_calls", "cosim_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.provider_calls != 1 or self.mutation_calls != 1:
            raise ValueError("R4 requires exactly one provider call and one mutation")
        if isinstance(self.wall_time_s, bool) or self.wall_time_s < 0:
            raise ValueError("wall_time_s must be non-negative")
        if not self.phase_order or self.phase_order[0] != "preflight" or "csynth" not in self.phase_order:
            raise ValueError("R4 reserve must cover preflight and csynth")

    def to_budget_limits(self) -> BudgetLimits:
        """Return an artifact snapshot; do not pass this to set_active_reserve."""
        return BudgetLimits(
            max_llm_calls=self.provider_calls,
            max_tool_calls=self.tool_calls,
            max_compile_calls=self.compile_calls,
            max_csim_calls=self.csim_calls,
            max_csynth_calls=self.csynth_calls,
            max_cosim_calls=self.cosim_calls,
            max_wall_time_s=self.wall_time_s,
        )

    def ensure_available(self, budget: BudgetManager) -> None:
        """Prospectively check the entire R4 plan without consuming capacity."""
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")
        budget.ensure_available(
            llm_calls=self.provider_calls,
            tool_calls=self.tool_calls,
            compile_calls=self.compile_calls,
            csim_calls=self.csim_calls,
            csynth_calls=self.csynth_calls,
            cosim_calls=self.cosim_calls,
        )
        limit = budget.limits.max_wall_time_s
        if limit is not None and budget.snapshot().elapsed_s + self.wall_time_s > limit:
            raise RuntimeError("insufficient wall-time capacity for R4 full prefix")

    def to_dict(self) -> dict[str, int | float]:
        return {"provider_calls": self.provider_calls, "mutation_calls": self.mutation_calls, "tool_calls": self.tool_calls, "compile_calls": self.compile_calls, "csim_calls": self.csim_calls, "csynth_calls": self.csynth_calls, "cosim_calls": self.cosim_calls, "wall_time_s": self.wall_time_s, "phase_order": list(self.phase_order), "admission_semantics": "prospective_no_consumption", "must_not_use_as_active_reserve": True}


def build_r4_reserve_plan(*, public_csim: bool, csynth: bool, public_cosim: bool, hidden_evaluation: bool, tool_calls: int, compile_calls: int, wall_time_s: float) -> R4ReservePlan:
    if not all(isinstance(value, bool) for value in (public_csim, csynth, public_cosim, hidden_evaluation)):
        raise TypeError("phase selectors must be boolean")
    if not csynth:
        raise ValueError("R4 full-prefix reserve requires csynth")
    logical_tool_phases = int(public_csim) + 1 + int(public_cosim)
    if tool_calls < logical_tool_phases or compile_calls < 1 or wall_time_s <= 0:
        raise ValueError("reserve counts/time must be non-negative")
    phases = ["preflight"]
    if public_csim:
        phases.append("public_csim")
    phases.append("csynth")
    if public_cosim:
        phases.append("public_cosim")
    if hidden_evaluation:
        phases.append("hidden_evaluation")
    return R4ReservePlan(
        tool_calls=tool_calls,
        compile_calls=compile_calls,
        csim_calls=int(public_csim) + int(hidden_evaluation),
        csynth_calls=int(csynth),
        cosim_calls=int(public_cosim),
        wall_time_s=wall_time_s,
        phase_order=tuple(phases),
    )


__all__ = ["R4ReservePlan", "build_r4_reserve_plan"]
