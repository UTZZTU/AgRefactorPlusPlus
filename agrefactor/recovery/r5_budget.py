"""Global R5 Provider/Vitis budget reconciliation and reservation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


class R5BudgetError(RuntimeError):
    """Raised when a reservation would cross a hard ceiling."""


@dataclass(frozen=True, slots=True)
class R5BudgetLedger:
    provider_cap: int = 500
    vitis_cap: int = 500
    provider_used: int = 0
    vitis_used: int = 0
    ledger_id: str = "r5-global-budget-v1"

    def __post_init__(self) -> None:
        for name in ("provider_cap", "vitis_cap", "provider_used", "vitis_used"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise R5BudgetError(f"{name} must be a non-negative integer")
        if self.provider_used > self.provider_cap or self.vitis_used > self.vitis_cap:
            raise R5BudgetError("budget ledger already exceeds hard cap")

    @property
    def provider_remaining(self) -> int:
        return self.provider_cap - self.provider_used

    @property
    def vitis_remaining(self) -> int:
        return self.vitis_cap - self.vitis_used

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "ledger_id": self.ledger_id,
            "provider_cap": self.provider_cap,
            "vitis_cap": self.vitis_cap,
            "provider_used": self.provider_used,
            "vitis_used": self.vitis_used,
            "provider_remaining": self.provider_remaining,
            "vitis_remaining": self.vitis_remaining,
        }
        value["ledger_sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return value

    @property
    def ledger_sha256(self) -> str:
        return self.to_dict()["ledger_sha256"]

    def reserve(self, *, provider_calls: int = 0, vitis_launches: int = 0) -> "R5BudgetLedger":
        if provider_calls < 0 or vitis_launches < 0:
            raise R5BudgetError("reservation cannot be negative")
        if provider_calls > self.provider_remaining or vitis_launches > self.vitis_remaining:
            raise R5BudgetError("reservation exceeds remaining R5 budget")
        return R5BudgetLedger(self.provider_cap, self.vitis_cap, self.provider_used + provider_calls, self.vitis_used + vitis_launches, self.ledger_id)

    def reserve_with_recovery(self, *, provider_upper_bound: int, vitis_upper_bound: int, reserve_fraction: float = 0.1) -> "R5BudgetReservation":
        if not 0 <= reserve_fraction < 1:
            raise R5BudgetError("reserve_fraction must be in [0,1)")
        provider_reserve = int(self.provider_remaining * reserve_fraction)
        vitis_reserve = int(self.vitis_remaining * reserve_fraction)
        if provider_upper_bound + provider_reserve > self.provider_remaining or vitis_upper_bound + vitis_reserve > self.vitis_remaining:
            raise R5BudgetError("campaign upper bound cannot retain recovery reserve")
        return R5BudgetReservation(self, provider_upper_bound, vitis_upper_bound, provider_reserve, vitis_reserve)


@dataclass(frozen=True, slots=True)
class R5BudgetReservation:
    ledger: R5BudgetLedger
    provider_upper_bound: int
    vitis_upper_bound: int
    provider_recovery_reserve: int
    vitis_recovery_reserve: int

    @property
    def reservation_sha256(self) -> str:
        payload = {
            "ledger": self.ledger.to_dict(),
            "provider_upper_bound": self.provider_upper_bound,
            "vitis_upper_bound": self.vitis_upper_bound,
            "provider_recovery_reserve": self.provider_recovery_reserve,
            "vitis_recovery_reserve": self.vitis_recovery_reserve,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_budget_ledger(path: str | Path) -> R5BudgetLedger:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return R5BudgetLedger(
        provider_cap=int(value.get("provider_cap", 500)),
        vitis_cap=int(value.get("vitis_cap", 500)),
        provider_used=int(value.get("provider_used", 0)),
        vitis_used=int(value.get("vitis_used", 0)),
        ledger_id=str(value.get("ledger_id", "r5-global-budget-v1")),
    )


def write_budget_ledger(path: str | Path, ledger: R5BudgetLedger) -> None:
    Path(path).write_text(json.dumps(ledger.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = ["R5BudgetError", "R5BudgetLedger", "R5BudgetReservation", "load_budget_ledger", "write_budget_ledger"]
