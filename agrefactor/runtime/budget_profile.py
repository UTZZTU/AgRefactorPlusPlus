
"""Resolve product hard budgets separately from observed-only soft budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from .budget import BudgetLimits


HARD_BUDGET_FIELDS = (
    "max_llm_calls",
    "max_tool_calls",
    "max_compile_calls",
    "max_csim_calls",
    "max_csynth_calls",
    "max_cosim_calls",
    "max_wall_time_s",
)


def _limits_to_dict(limits: BudgetLimits) -> dict[str, int | float | None]:
    return {
        name: getattr(limits, name)
        for name in HARD_BUDGET_FIELDS
    }


def _clean_decimal(name: str, value: object | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative decimal")
    try:
        converted = (
            value if isinstance(value, Decimal) else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        ) from exc
    if not converted.is_finite() or converted < 0:
        raise ValueError(f"{name} must be a finite non-negative decimal")
    return converted


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _clean_currency(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("currency must be a string or None")
    cleaned = value.strip().upper()
    if len(cleaned) != 3 or not cleaned.isalpha():
        raise ValueError("currency must be a three-letter alphabetic code")
    return cleaned


@dataclass(frozen=True, slots=True)
class EffectiveRunBudget:
    """Resolved run-level budget contract used by every execution component."""

    system_defaults: Mapping[str, int | float]
    system_safety_ceilings: Mapping[str, int | float]
    user_requested: Mapping[str, int | float | None]
    effective_hard_limits: Mapping[str, int | float]
    budget_source_per_field: Mapping[str, str]
    token_budget: int | None = None
    cost_budget: Decimal | None = None
    cost_budget_currency: str | None = None
    profile_name: str = "source-run-default"
    phase_reserves: Mapping[str, Mapping[str, int | float]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        expected = set(HARD_BUDGET_FIELDS)
        mappings = {
            "system_defaults": self.system_defaults,
            "system_safety_ceilings": self.system_safety_ceilings,
            "user_requested": self.user_requested,
            "effective_hard_limits": self.effective_hard_limits,
            "budget_source_per_field": self.budget_source_per_field,
        }
        for name, value in mappings.items():
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            if set(value) != expected:
                raise ValueError(
                    f"{name} must contain exactly: "
                    + ", ".join(HARD_BUDGET_FIELDS)
                )

        defaults: dict[str, int | float] = {}
        ceilings: dict[str, int | float] = {}
        requested: dict[str, int | float | None] = {}
        effective: dict[str, int | float] = {}
        sources: dict[str, str] = {}

        for field_name in HARD_BUDGET_FIELDS:
            default = self.system_defaults[field_name]
            ceiling = self.system_safety_ceilings[field_name]
            resolved = self.effective_hard_limits[field_name]
            request = self.user_requested[field_name]
            source = self.budget_source_per_field[field_name]

            self._validate_hard_value(field_name, default)
            self._validate_hard_value(field_name, ceiling)
            self._validate_hard_value(field_name, resolved)
            if request is not None:
                self._validate_hard_value(field_name, request)
            if default > ceiling:
                raise ValueError(
                    f"system default exceeds safety ceiling: {field_name}"
                )
            if resolved > ceiling:
                raise ValueError(
                    f"effective limit exceeds safety ceiling: {field_name}"
                )
            if source not in {"system_default", "user_requested"}:
                raise ValueError(
                    f"unsupported budget source for {field_name}: {source}"
                )
            if source == "system_default":
                if request is not None or resolved != default:
                    raise ValueError(
                        f"inconsistent system-default resolution: {field_name}"
                    )
            else:
                if request is None or resolved != request:
                    raise ValueError(
                        f"inconsistent user resolution: {field_name}"
                    )

            defaults[field_name] = default
            ceilings[field_name] = ceiling
            requested[field_name] = request
            effective[field_name] = resolved
            sources[field_name] = source

        token_budget = self.token_budget
        if token_budget is not None:
            if (
                isinstance(token_budget, bool)
                or not isinstance(token_budget, int)
                or token_budget < 0
            ):
                raise ValueError(
                    "token_budget must be a non-negative integer or None"
                )
        cost_budget = _clean_decimal("cost_budget", self.cost_budget)
        currency = _clean_currency(self.cost_budget_currency)
        if cost_budget is not None and currency is None:
            raise ValueError(
                "cost_budget_currency is required when cost_budget is set"
            )
        if cost_budget is None and currency is not None:
            raise ValueError(
                "cost_budget_currency requires cost_budget"
            )

        object.__setattr__(self, "system_defaults", defaults)
        object.__setattr__(self, "system_safety_ceilings", ceilings)
        object.__setattr__(self, "user_requested", requested)
        object.__setattr__(self, "effective_hard_limits", effective)
        object.__setattr__(self, "budget_source_per_field", sources)
        profile_name = self.profile_name.strip()
        if not profile_name:
            raise ValueError("profile_name must not be empty")
        normalized_reserves: dict[str, dict[str, int | float]] = {}
        for phase, reserve in self.phase_reserves.items():
            if phase not in {"refactor", "optimize"}:
                raise ValueError(f"unsupported reserve phase: {phase}")
            if not isinstance(reserve, Mapping):
                raise TypeError("phase reserve must be a mapping")
            if set(reserve) != expected:
                raise ValueError(
                    "phase reserve must contain exactly: "
                    + ", ".join(HARD_BUDGET_FIELDS)
                )
            copied: dict[str, int | float] = {}
            for field_name in HARD_BUDGET_FIELDS:
                value = reserve[field_name]
                self._validate_hard_value(field_name, value)
                if value > effective[field_name]:
                    raise ValueError(
                        f"phase reserve exceeds effective limit: {phase}.{field_name}"
                    )
                copied[field_name] = value
            normalized_reserves[phase] = copied

        object.__setattr__(self, "cost_budget", cost_budget)
        object.__setattr__(self, "cost_budget_currency", currency)
        object.__setattr__(self, "profile_name", profile_name)
        object.__setattr__(self, "phase_reserves", normalized_reserves)

    @staticmethod
    def _validate_hard_value(name: str, value: object) -> None:
        if name == "max_wall_time_s":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError(
                    "max_wall_time_s must be finite and non-negative"
                )
            return
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer")

    def to_budget_limits(self) -> BudgetLimits:
        """Return hard runtime limits; token/cost remain observed-only."""

        values = dict(self.effective_hard_limits)
        return BudgetLimits(
            max_llm_calls=int(values["max_llm_calls"]),
            max_tool_calls=int(values["max_tool_calls"]),
            max_compile_calls=int(values["max_compile_calls"]),
            max_csim_calls=int(values["max_csim_calls"]),
            max_csynth_calls=int(values["max_csynth_calls"]),
            max_cosim_calls=int(values["max_cosim_calls"]),
            max_tokens=None,
            max_cost_usd=None,
            max_wall_time_s=float(values["max_wall_time_s"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "profile_name": self.profile_name,
            "phase_reserves": {
                phase: dict(reserve)
                for phase, reserve in self.phase_reserves.items()
            },
            "system_defaults": dict(self.system_defaults),
            "system_safety_ceilings": dict(
                self.system_safety_ceilings
            ),
            "user_requested": dict(self.user_requested),
            "effective_hard_limits": dict(
                self.effective_hard_limits
            ),
            "budget_source_per_field": dict(
                self.budget_source_per_field
            ),
            "soft_usage_budgets": {
                "token_budget": self.token_budget,
                "cost_budget": _decimal_text(self.cost_budget),
                "currency": self.cost_budget_currency,
                "enforcement": "observed_only",
                "blocking": False,
            },
        }


@dataclass(frozen=True, slots=True)
class RunBudgetProfile:
    """System defaults and non-overridable safety ceilings."""

    name: str
    system_defaults: BudgetLimits
    system_safety_ceilings: BudgetLimits
    phase_reserves: Mapping[str, BudgetLimits] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("RunBudgetProfile.name must not be empty")
        if not isinstance(self.system_defaults, BudgetLimits):
            raise TypeError("system_defaults must be BudgetLimits")
        if not isinstance(self.system_safety_ceilings, BudgetLimits):
            raise TypeError("system_safety_ceilings must be BudgetLimits")
        for limits, label in (
            (self.system_defaults, "system_defaults"),
            (self.system_safety_ceilings, "system_safety_ceilings"),
        ):
            if limits.max_tokens is not None:
                raise ValueError(
                    f"{label}.max_tokens must remain None; token budgets "
                    "are observed-only"
                )
            if limits.max_cost_usd is not None:
                raise ValueError(
                    f"{label}.max_cost_usd must remain None; cost budgets "
                    "are observed-only"
                )
            values = _limits_to_dict(limits)
            missing = [
                name for name, value in values.items()
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"{label} must define all hard fields: "
                    + ", ".join(missing)
                )

        defaults = _limits_to_dict(self.system_defaults)
        ceilings = _limits_to_dict(self.system_safety_ceilings)
        for field_name in HARD_BUDGET_FIELDS:
            assert defaults[field_name] is not None
            assert ceilings[field_name] is not None
            if defaults[field_name] > ceilings[field_name]:
                raise ValueError(
                    f"default exceeds safety ceiling: {field_name}"
                )
        normalized_reserves: dict[str, BudgetLimits] = {}
        for phase, reserve in self.phase_reserves.items():
            if phase not in {"refactor", "optimize"}:
                raise ValueError(f"unsupported reserve phase: {phase}")
            if not isinstance(reserve, BudgetLimits):
                raise TypeError("phase reserve must be BudgetLimits")
            values = _limits_to_dict(reserve)
            missing = [name for name, value in values.items() if value is None]
            if missing:
                raise ValueError(
                    "phase reserve must define all hard fields: "
                    + ", ".join(missing)
                )
            normalized_reserves[phase] = reserve
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "phase_reserves", normalized_reserves)

    def resolve(
        self,
        *,
        user_requested: Mapping[str, int | float | None] | None = None,
        token_budget: int | None = None,
        cost_budget: object | None = None,
        cost_budget_currency: str | None = None,
    ) -> EffectiveRunBudget:
        requested_input = dict(user_requested or {})
        unknown = set(requested_input) - set(HARD_BUDGET_FIELDS)
        if unknown:
            raise ValueError(
                "unknown hard budget fields: "
                + ", ".join(sorted(unknown))
            )

        defaults = _limits_to_dict(self.system_defaults)
        ceilings = _limits_to_dict(self.system_safety_ceilings)
        requested: dict[str, int | float | None] = {}
        effective: dict[str, int | float] = {}
        sources: dict[str, str] = {}

        for field_name in HARD_BUDGET_FIELDS:
            default = defaults[field_name]
            ceiling = ceilings[field_name]
            assert default is not None
            assert ceiling is not None
            raw = requested_input.get(field_name)
            if raw is None:
                requested[field_name] = None
                effective[field_name] = default
                sources[field_name] = "system_default"
                continue

            EffectiveRunBudget._validate_hard_value(
                field_name,
                raw,
            )
            if raw > ceiling:
                raise ValueError(
                    f"{field_name}={raw} exceeds system safety "
                    f"ceiling {ceiling}"
                )
            requested[field_name] = raw
            effective[field_name] = raw
            sources[field_name] = "user_requested"

        return EffectiveRunBudget(
            system_defaults={
                key: value for key, value in defaults.items()
                if value is not None
            },
            system_safety_ceilings={
                key: value for key, value in ceilings.items()
                if value is not None
            },
            user_requested=requested,
            effective_hard_limits=effective,
            budget_source_per_field=sources,
            token_budget=token_budget,
            cost_budget=_clean_decimal("cost_budget", cost_budget),
            cost_budget_currency=cost_budget_currency,
            profile_name=self.name,
            phase_reserves={
                phase: {
                    key: value
                    for key, value in _limits_to_dict(reserve).items()
                    if value is not None
                }
                for phase, reserve in self.phase_reserves.items()
            },
        )


# P4_0F_FROZEN_PROFILES:BEGIN
SOURCE_RUN_SAFETY_CEILINGS = BudgetLimits(
    max_llm_calls=256, max_tool_calls=512, max_compile_calls=192,
    max_csim_calls=128, max_csynth_calls=64, max_cosim_calls=64,
    max_tokens=None, max_cost_usd=None, max_wall_time_s=14400.0,
)

REFACTOR_RUN_BUDGET_PROFILE = RunBudgetProfile(
    name="refactor-default",
    system_defaults=BudgetLimits(
        max_llm_calls=96, max_tool_calls=192, max_compile_calls=96,
        max_csim_calls=64, max_csynth_calls=32, max_cosim_calls=32,
        max_tokens=None, max_cost_usd=None, max_wall_time_s=10800.0,
    ),
    system_safety_ceilings=SOURCE_RUN_SAFETY_CEILINGS,
)

OPTIMIZE_RUN_BUDGET_PROFILE = RunBudgetProfile(
    name="optimize-default",
    system_defaults=BudgetLimits(
        max_llm_calls=24, max_tool_calls=160, max_compile_calls=72,
        max_csim_calls=24, max_csynth_calls=12, max_cosim_calls=12,
        max_tokens=None, max_cost_usd=None, max_wall_time_s=1800.0,
    ),
    system_safety_ceilings=SOURCE_RUN_SAFETY_CEILINGS,
)

FULL_RUN_BUDGET_PROFILE = RunBudgetProfile(
    name="full-default",
    system_defaults=BudgetLimits(
        max_llm_calls=120, max_tool_calls=352, max_compile_calls=168,
        max_csim_calls=88, max_csynth_calls=44, max_cosim_calls=44,
        max_tokens=None, max_cost_usd=None, max_wall_time_s=12600.0,
    ),
    system_safety_ceilings=SOURCE_RUN_SAFETY_CEILINGS,
    phase_reserves={
        "refactor": OPTIMIZE_RUN_BUDGET_PROFILE.system_defaults,
    },
)
# P4_0F_FROZEN_PROFILES:END

_MODE_PROFILES = {
    "refactor": REFACTOR_RUN_BUDGET_PROFILE,
    "optimize": OPTIMIZE_RUN_BUDGET_PROFILE,
    "full": FULL_RUN_BUDGET_PROFILE,
}

def run_budget_profile_for_mode(mode: object) -> RunBudgetProfile:
    value = getattr(mode, "value", mode)
    key = str(value).strip().casefold()
    try:
        return _MODE_PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unsupported source run budget mode: {value!r}") from exc

# Historical compatibility profile remains stable for callers that have not
# selected a normal product mode. Normal refactor/optimize/full never use it.
DEFAULT_SOURCE_RUN_BUDGET_PROFILE = RunBudgetProfile(
    name="source-run-default",
    system_defaults=BudgetLimits(
        max_llm_calls=64, max_tool_calls=128, max_compile_calls=48,
        max_csim_calls=32, max_csynth_calls=16, max_cosim_calls=16,
        max_tokens=None, max_cost_usd=None, max_wall_time_s=7200.0,
    ),
    system_safety_ceilings=SOURCE_RUN_SAFETY_CEILINGS,
)
