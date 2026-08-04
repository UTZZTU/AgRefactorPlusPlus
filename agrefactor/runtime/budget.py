# Run-budget accounting shared by all AgRefactor++ execution modes.

from __future__ import annotations

import time
from threading import RLock
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import isfinite
import re
from types import MappingProxyType

from agrefactor.models import CostEstimate, TokenUsage


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COST_TOLERANCE = Decimal("1e-12")


def _clean_cost_decimal(name: str, value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        )
    try:
        converted = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        ) from exc
    if not converted.is_finite() or converted < 0:
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        )
    return converted


def _clean_currency(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("currency must be a string")
    normalized = value.strip().upper()
    if _CURRENCY_RE.fullmatch(normalized) is None:
        raise ValueError(
            "currency must be a three-letter alphabetic code"
        )
    return normalized


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _normalize_costs_by_currency(
    value: Mapping[str, object],
) -> dict[str, Decimal]:
    if not isinstance(value, Mapping):
        raise TypeError(
            "costs_by_currency must be a mapping"
        )
    normalized: dict[str, Decimal] = {}
    for raw_currency, raw_amount in value.items():
        currency = _clean_currency(raw_currency)
        amount = _clean_cost_decimal(
            f"costs_by_currency[{currency}]",
            raw_amount,
        )
        if currency in normalized:
            raise ValueError(
                "costs_by_currency contains duplicate currency"
            )
        normalized[currency] = amount
    return dict(sorted(normalized.items()))


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
    max_compile_calls: int | None = None
    max_csim_calls: int | None = None
    max_csynth_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_time_s: float | None = None
    max_cosim_calls: int | None = None

    def __post_init__(self) -> None:
        self._validate_integer_limit("max_llm_calls", self.max_llm_calls)
        self._validate_integer_limit("max_tool_calls", self.max_tool_calls)
        self._validate_integer_limit(
            "max_compile_calls",
            self.max_compile_calls,
        )
        self._validate_integer_limit(
            "max_csim_calls",
            self.max_csim_calls,
        )
        self._validate_integer_limit(
            "max_csynth_calls",
            self.max_csynth_calls,
        )
        self._validate_integer_limit("max_tokens", self.max_tokens)
        self._validate_float_limit("max_cost_usd", self.max_cost_usd)
        self._validate_float_limit("max_wall_time_s", self.max_wall_time_s)
        self._validate_integer_limit("max_cosim_calls", self.max_cosim_calls)

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
    compile_calls: int
    csim_calls: int
    csynth_calls: int
    tokens: int
    cost_usd: float
    elapsed_s: float
    costs_by_currency: Mapping[str, Decimal] = field(
        default_factory=dict
    )
    cosim_calls: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.cost_usd, bool)
            or not isinstance(self.cost_usd, (int, float))
            or not isfinite(float(self.cost_usd))
            or self.cost_usd < 0
        ):
            raise ValueError(
                "cost_usd must be a finite non-negative number"
            )

        normalized = _normalize_costs_by_currency(
            self.costs_by_currency
        )
        legacy_usd = _clean_cost_decimal(
            "cost_usd",
            self.cost_usd,
        )
        mapped_usd = normalized.get("USD")

        if mapped_usd is None:
            if legacy_usd != 0:
                normalized["USD"] = legacy_usd
            usd_total = legacy_usd
        else:
            if (
                legacy_usd != 0
                and abs(mapped_usd - legacy_usd)
                > _COST_TOLERANCE
            ):
                raise ValueError(
                    "cost_usd conflicts with "
                    "costs_by_currency['USD']"
                )
            usd_total = mapped_usd

        object.__setattr__(
            self,
            "cost_usd",
            float(usd_total),
        )
        object.__setattr__(
            self,
            "costs_by_currency",
            MappingProxyType(
                dict(sorted(normalized.items()))
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "compile_calls": self.compile_calls,
            "csim_calls": self.csim_calls,
            "csynth_calls": self.csynth_calls,
            "cosim_calls": self.cosim_calls,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "elapsed_s": self.elapsed_s,
            "costs_by_currency": {
                currency: _decimal_text(amount)
                for currency, amount
                in self.costs_by_currency.items()
            },
        }


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
        self._lock = RLock()
        self._started_at = clock()
        self._llm_calls = 0
        self._tool_calls = 0
        self._compile_calls = 0
        self._csim_calls = 0
        self._csynth_calls = 0
        self._cosim_calls = 0
        self._tokens = 0
        self._costs_by_currency: dict[str, Decimal] = {}

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    def snapshot(self) -> BudgetUsage:
        "Return current usage and check the wall-clock limit."

        with self._lock:
            elapsed_s = self._elapsed_s()
            self._check_limit(
                "wall_time_s",
                elapsed_s,
                self._limits.max_wall_time_s,
            )
            return BudgetUsage(
                llm_calls=self._llm_calls,
                tool_calls=self._tool_calls,
                compile_calls=self._compile_calls,
                csim_calls=self._csim_calls,
                csynth_calls=self._csynth_calls,
                cosim_calls=self._cosim_calls,
                tokens=self._tokens,
                cost_usd=self._cost_usd_total(),
                elapsed_s=elapsed_s,
                costs_by_currency=dict(
                    self._costs_by_currency
                ),
            )

    def ensure_available(
        self,
        *,
        llm_calls: int = 0,
        tool_calls: int = 0,
        compile_calls: int = 0,
        csim_calls: int = 0,
        csynth_calls: int = 0,
        cosim_calls: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        "Check a prospective usage increment without mutating state."

        self._validate_increment("llm_calls", llm_calls)
        self._validate_increment("tool_calls", tool_calls)
        self._validate_increment("compile_calls", compile_calls)
        self._validate_increment("csim_calls", csim_calls)
        self._validate_increment("csynth_calls", csynth_calls)
        self._validate_increment("cosim_calls", cosim_calls)
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
            "compile_calls",
            self._compile_calls + compile_calls,
            self._limits.max_compile_calls,
        )
        self._check_limit(
            "csim_calls",
            self._csim_calls + csim_calls,
            self._limits.max_csim_calls,
        )
        self._check_limit(
            "csynth_calls",
            self._csynth_calls + csynth_calls,
            self._limits.max_csynth_calls,
        )
        self._check_limit(
            "cosim_calls",
            self._cosim_calls + cosim_calls,
            self._limits.max_cosim_calls,
        )
        self._check_limit(
            "tokens",
            self._tokens + tokens,
            self._limits.max_tokens,
        )
        self._check_limit(
            "cost_usd",
            self._cost_usd_total() + cost_usd,
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
        compile_calls: int = 0,
        csim_calls: int = 0,
        csynth_calls: int = 0,
        cosim_calls: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> BudgetUsage:
        "Atomically record usage after checking all configured limits."

        with self._lock:
            self.ensure_available(
                llm_calls=llm_calls,
                tool_calls=tool_calls,
                compile_calls=compile_calls,
                csim_calls=csim_calls,
                csynth_calls=csynth_calls,
                cosim_calls=cosim_calls,
                tokens=tokens,
                cost_usd=cost_usd,
            )

            self._llm_calls += llm_calls
            self._tool_calls += tool_calls
            self._compile_calls += compile_calls
            self._csim_calls += csim_calls
            self._csynth_calls += csynth_calls
            self._cosim_calls += cosim_calls
            cost_increment = self._cost_increment(
                cost_usd=cost_usd,
                estimated_cost=None,
            )
            self._tokens += tokens
            self._apply_cost_increment(cost_increment)
            return self.snapshot()

    def record_observed(
        self,
        *,
        tokens: int = 0,
        cost_usd: float = 0.0,
        estimated_cost: CostEstimate | None = None,
    ) -> BudgetUsage:
        """Record token and cost values known after a call completed.

        Observed token and estimated-cost totals are soft budgets: they are
        recorded after provider execution and may stop later work, but they do
        not pretend to block the completed call.
        """

        with self._lock:
            self._validate_increment("tokens", tokens)
            cost_increment = self._cost_increment(
                cost_usd=cost_usd,
                estimated_cost=estimated_cost,
            )
            self._tokens += tokens
            self._apply_cost_increment(cost_increment)
            return BudgetUsage(
                llm_calls=self._llm_calls,
                tool_calls=self._tool_calls,
                compile_calls=self._compile_calls,
                csim_calls=self._csim_calls,
                csynth_calls=self._csynth_calls,
                cosim_calls=self._cosim_calls,
                tokens=self._tokens,
                cost_usd=self._cost_usd_total(),
                elapsed_s=self._elapsed_s(),
                costs_by_currency=dict(
                    self._costs_by_currency
                ),
            )

    def record_model_usage(
        self,
        usage: TokenUsage,
    ) -> BudgetUsage:
        if not isinstance(usage, TokenUsage):
            raise TypeError(
                "usage must be a TokenUsage"
            )
        return self.record_observed(
            tokens=usage.total_tokens,
            cost_usd=(
                0.0
                if usage.cost_usd is None
                else float(usage.cost_usd)
            ),
            estimated_cost=usage.estimated_cost,
        )

    def _cost_increment(
        self,
        *,
        cost_usd: float,
        estimated_cost: CostEstimate | None,
    ) -> dict[str, Decimal]:
        self._validate_cost_increment(cost_usd)
        legacy_usd = _clean_cost_decimal(
            "cost_usd increment",
            cost_usd,
        )

        if estimated_cost is None:
            return (
                {}
                if legacy_usd == 0
                else {"USD": legacy_usd}
            )
        if not isinstance(estimated_cost, CostEstimate):
            raise TypeError(
                "estimated_cost must be a CostEstimate or None"
            )

        amount = estimated_cost.amount
        currency = estimated_cost.currency
        if amount is None:
            return (
                {}
                if legacy_usd == 0
                else {"USD": legacy_usd}
            )
        if currency is None:
            raise ValueError(
                "priced estimated_cost requires currency"
            )

        normalized_currency = _clean_currency(currency)
        if normalized_currency == "USD":
            if (
                legacy_usd != 0
                and abs(legacy_usd - amount)
                > _COST_TOLERANCE
            ):
                raise ValueError(
                    "conflicting USD cost_usd and "
                    "estimated_cost amounts"
                )
            return {"USD": amount}

        if legacy_usd != 0:
            raise ValueError(
                "non-USD estimated_cost must not be "
                "combined with cost_usd"
            )
        return {normalized_currency: amount}

    def _apply_cost_increment(
        self,
        increment: Mapping[str, Decimal],
    ) -> None:
        for currency, amount in increment.items():
            if amount == 0:
                continue
            self._costs_by_currency[currency] = (
                self._costs_by_currency.get(
                    currency,
                    Decimal("0"),
                )
                + amount
            )

    def _cost_usd_total(self) -> float:
        return float(
            self._costs_by_currency.get(
                "USD",
                Decimal("0"),
            )
        )

    def exhausted(self) -> bool:
        "Return whether current usage reached any configured limit."

        usage = self.snapshot()
        checks = (
            (usage.llm_calls, self._limits.max_llm_calls),
            (usage.tool_calls, self._limits.max_tool_calls),
            (usage.compile_calls, self._limits.max_compile_calls),
            (usage.csim_calls, self._limits.max_csim_calls),
            (usage.csynth_calls, self._limits.max_csynth_calls),
            (usage.cosim_calls, self._limits.max_cosim_calls),
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
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or value < 0
        ):
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
