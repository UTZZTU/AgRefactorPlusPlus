# Run-budget accounting shared by all AgRefactor++ execution modes.

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite


class BudgetExceededError(RuntimeError):
    "Raised when an operation would exceed a configured run budget."

    def __init__(
        self,
        resource: str,
        limit: int | float,
        attempted: int | float,
    ) -> None:
        self.resource = resource
        self.limit = limit
        self.attempted = attempted
        super().__init__(
            f"Budget exceeded for {resource}: "
            f"attempted {attempted}, limit {limit}"
        )


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    "Optional upper bounds for one AgRefactor++ run."

    max_llm_calls: int | None = None
    max_tool_calls: int | None = None
    max_csynth_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_time_s: float | None = None

    def __post_init__(self) -> None:
        self._validate_integer_limit("max_llm_calls", self.max_llm_calls)
        self._validate_integer_limit("max_tool_calls", self.max_tool_calls)
        self._validate_integer_limit(
            "max_csynth_calls",
            self.max_csynth_calls,
        )
        self._validate_integer_limit("max_tokens", self.max_tokens)
        self._validate_float_limit("max_cost_usd", self.max_cost_usd)
        self._validate_float_limit("max_wall_time_s", self.max_wall_time_s)

    @staticmethod
    def _validate_integer_limit(name: str, value: int | None) -> None:
        if value is not None and (isinstance(value, bool) or value < 0):
            raise ValueError(f"{name} must be a non-negative integer or None")

    @staticmethod
    def _validate_float_limit(name: str, value: float | None) -> None:
        if value is not None and (not isfinite(value) or value < 0):
            raise ValueError(f"{name} must be a finite non-negative number or None")


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    "Immutable snapshot of consumed resources."

    llm_calls: int
    tool_calls: int
    csynth_calls: int
    tokens: int
    cost_usd: float
    elapsed_s: float


class BudgetManager:
    "Track resource usage and reject operations that exceed run limits."

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = limits or BudgetLimits()
        self._clock = clock
        self._started_at = clock()
        self._llm_calls = 0
        self._tool_calls = 0
        self._csynth_calls = 0
        self._tokens = 0
        self._cost_usd = 0.0

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    def snapshot(self) -> BudgetUsage:
        "Return current usage and check the wall-clock limit."

        elapsed_s = self._elapsed_s()
        self._check_limit(
            "wall_time_s",
            elapsed_s,
            self._limits.max_wall_time_s,
        )
        return BudgetUsage(
            llm_calls=self._llm_calls,
            tool_calls=self._tool_calls,
            csynth_calls=self._csynth_calls,
            tokens=self._tokens,
            cost_usd=self._cost_usd,
            elapsed_s=elapsed_s,
        )

    def ensure_available(
        self,
        *,
        llm_calls: int = 0,
        tool_calls: int = 0,
        csynth_calls: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        "Check a prospective usage increment without mutating state."

        self._validate_increment("llm_calls", llm_calls)
        self._validate_increment("tool_calls", tool_calls)
        self._validate_increment("csynth_calls", csynth_calls)
        self._validate_increment("tokens", tokens)
        self._validate_cost_increment(cost_usd)

        self._check_limit(
            "llm_calls",
            self._llm_calls + llm_calls,
            self._limits.max_llm_calls,
        )
        self._check_limit(
            "tool_calls",
            self._tool_calls + tool_calls,
            self._limits.max_tool_calls,
        )
        self._check_limit(
            "csynth_calls",
            self._csynth_calls + csynth_calls,
            self._limits.max_csynth_calls,
        )
        self._check_limit(
            "tokens",
            self._tokens + tokens,
            self._limits.max_tokens,
        )
        self._check_limit(
            "cost_usd",
            self._cost_usd + cost_usd,
            self._limits.max_cost_usd,
        )
        self._check_limit(
            "wall_time_s",
            self._elapsed_s(),
            self._limits.max_wall_time_s,
        )

    def consume(
        self,
        *,
        llm_calls: int = 0,
        tool_calls: int = 0,
        csynth_calls: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> BudgetUsage:
        "Atomically record usage after checking all configured limits."

        self.ensure_available(
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            csynth_calls=csynth_calls,
            tokens=tokens,
            cost_usd=cost_usd,
        )

        self._llm_calls += llm_calls
        self._tool_calls += tool_calls
        self._csynth_calls += csynth_calls
        self._tokens += tokens
        self._cost_usd += cost_usd
        return self.snapshot()

    def exhausted(self) -> bool:
        "Return whether current usage reached any configured limit."

        usage = self.snapshot()
        checks = (
            (usage.llm_calls, self._limits.max_llm_calls),
            (usage.tool_calls, self._limits.max_tool_calls),
            (usage.csynth_calls, self._limits.max_csynth_calls),
            (usage.tokens, self._limits.max_tokens),
            (usage.cost_usd, self._limits.max_cost_usd),
            (usage.elapsed_s, self._limits.max_wall_time_s),
        )
        return any(
            limit is not None and value >= limit
            for value, limit in checks
        )

    def _elapsed_s(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    @staticmethod
    def _validate_increment(name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f"{name} increment must be a non-negative integer"
            )

    @staticmethod
    def _validate_cost_increment(value: float) -> None:
        if not isfinite(value) or value < 0:
            raise ValueError(
                "cost_usd increment must be a finite non-negative number"
            )

    @staticmethod
    def _check_limit(
        resource: str,
        attempted: int | float,
        limit: int | float | None,
    ) -> None:
        if limit is not None and attempted > limit:
            raise BudgetExceededError(resource, limit, attempted)
