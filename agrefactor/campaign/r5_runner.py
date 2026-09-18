"""Paired A0-A6 R5 campaign runner on top of the existing execution seam.

The runner owns identity, pairing, source/time partitions, and global budget
accounting.  It does not implement a second Vitis flow: an injected executor
must call the existing refactor path and return agent-safe observations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol

from agrefactor.recovery.r5_budget import R5BudgetLedger

from .r5_protocol import R5Arm, R5CampaignManifest, estimate_upper_bound
from .r5_reducer import R5ArmObservation, R5CampaignReduction, reduce_observations

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class R5CampaignError(RuntimeError):
    """Raised when a paired campaign violates a frozen contract."""


@dataclass(frozen=True, slots=True)
class R5CaseSpec:
    case_id: str
    source_sha256: str
    context_signature: str
    period: str
    synthetic: bool = False

    def __post_init__(self) -> None:
        if (
            not self.case_id.strip()
            or not isinstance(self.source_sha256, str)
            or not _SHA256.fullmatch(self.source_sha256)
            or not isinstance(self.context_signature, str)
            or not _SHA256.fullmatch(self.context_signature)
        ):
            raise R5CampaignError("case identity is incomplete")
        if self.period not in {"history", "future"}:
            raise R5CampaignError("case period must be history or future")
        if self.synthetic:
            raise R5CampaignError("synthetic fixtures cannot enter a real R5 campaign")


class R5CampaignExecutor(Protocol):
    def prepare_common_baseline(self, case: R5CaseSpec, repeat: int) -> Mapping[str, Any]:
        """Return one immutable baseline terminal artifact for the pair."""

    def run_arm(
        self,
        *,
        case: R5CaseSpec,
        repeat: int,
        arm: R5Arm,
        baseline: Mapping[str, Any],
        arm_index: int,
    ) -> Mapping[str, Any]:
        """Execute one arm through the existing product/refactor seam."""


@dataclass(frozen=True, slots=True)
class R5BaselineObservation:
    case_id: str
    repeat: int
    baseline_id: str
    source_sha256: str
    context_signature: str
    provider_calls: int
    vitis_launches: int
    artifact_sha256: str


@dataclass(frozen=True, slots=True)
class R5CampaignRun:
    manifest: Mapping[str, Any]
    baselines: tuple[R5BaselineObservation, ...]
    observations: tuple[R5ArmObservation, ...]
    reduction: R5CampaignReduction
    budget: R5BudgetLedger
    output_root: str | None
    budget_reservation: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "manifest": dict(self.manifest),
            "baselines": [asdict(item) for item in self.baselines],
            "observations": [asdict(item) for item in self.observations],
            "reduction": self.reduction.to_dict(),
            "budget": self.budget.to_dict(),
            "output_root": self.output_root,
            "budget_reservation": (
                None if self.budget_reservation is None else dict(self.budget_reservation)
            ),
        }


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _required_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise R5CampaignError(f"{name} must be SHA-256")
    return value


def _usage_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise R5CampaignError(f"{name} must be a non-negative integer")
    return value


def _validate_arm_usage(
    arm: R5Arm,
    *,
    provider_calls: int,
    vitis_launches: int,
) -> None:
    provider_cap, vitis_cap = {
        R5Arm.A0: (0, 0),
        R5Arm.A1: (1, 0),
        R5Arm.A2: (2, 3),
        R5Arm.A3: (2, 3),
        R5Arm.A4: (2, 3),
        R5Arm.A5: (2, 3),
        R5Arm.A6: (2, 3),
    }[arm]
    if provider_calls > provider_cap or vitis_launches > vitis_cap:
        raise R5CampaignError(
            f"arm {arm.value} exceeded frozen Provider/Vitis upper bound"
        )


class R5CampaignRunner:
    """Run a paired A0-A6 campaign with no cross-arm cache or hidden inputs."""

    def __init__(
        self,
        *,
        manifest: R5CampaignManifest,
        cases: Sequence[R5CaseSpec],
        executor: R5CampaignExecutor,
        budget: R5BudgetLedger | None = None,
        output_root: str | Path | None = None,
    ) -> None:
        if not isinstance(manifest, R5CampaignManifest):
            raise TypeError("manifest must be R5CampaignManifest")
        self.manifest = manifest
        self.cases = tuple(cases)
        if not self.cases or {item.case_id for item in self.cases} != set(manifest.case_ids):
            raise R5CampaignError("manifest and case inventory do not match")
        if not callable(getattr(executor, "prepare_common_baseline", None)) or not callable(getattr(executor, "run_arm", None)):
            raise TypeError("executor must provide prepare_common_baseline and run_arm")
        self.executor = executor
        self.budget = budget or R5BudgetLedger()
        self.output_root = None if output_root is None else Path(output_root)
        self._validate_inventory()

    @property
    def _future_cases(self) -> tuple[R5CaseSpec, ...]:
        """Cases eligible for arm execution after the history snapshot freeze."""

        return tuple(case for case in self.cases if case.period == "future")

    def _validate_inventory(self) -> None:
        source_periods: dict[str, set[str]] = {}
        for case in self.cases:
            _required_sha(case.source_sha256, "source_sha256")
            _required_sha(case.context_signature, "context_signature")
            source_periods.setdefault(case.source_sha256, set()).add(case.period)
        crossing = [source for source, periods in source_periods.items() if len(periods) > 1]
        if crossing:
            raise R5CampaignError("source hash crosses history/future holdout: " + ",".join(crossing))
        if len(source_periods) < 2:
            raise R5CampaignError("R5 campaign requires at least two independent source hashes")
        if not any(case.period == "history" for case in self.cases) or not any(case.period == "future" for case in self.cases):
            raise R5CampaignError("R5 campaign requires both history and future partitions")

    def _arm_schedule(self, case: R5CaseSpec, repeat: int) -> tuple[R5Arm, ...]:
        offset = int(_sha({"case_id": case.case_id, "repeat": repeat})[:8], 16) % len(R5Arm)
        arms = tuple(self.manifest.arm_order)
        return arms[offset:] + arms[:offset]

    def plan(self) -> dict[str, Any]:
        """Return the frozen schedule without invoking the executor."""

        future_cases = self._future_cases
        provider_upper_bound, vitis_upper_bound = estimate_upper_bound(
            case_count=len(future_cases), repeats=self.manifest.repeats
        )
        try:
            reservation = self.budget.reserve_with_recovery(
                provider_upper_bound=provider_upper_bound,
                vitis_upper_bound=vitis_upper_bound,
            )
        except Exception as exc:
            raise R5CampaignError("campaign budget preflight failed") from exc

        return {
            "schema_version": 1,
            "manifest": self.manifest.to_dict(),
            "schedule": [
                {
                    "case_id": case.case_id,
                    "repeat": repeat,
                    "period": case.period,
                    "arms": [arm.value for arm in self._arm_schedule(case, repeat)],
                }
                for case in future_cases
                for repeat in range(1, self.manifest.repeats + 1)
            ],
            "history_case_ids": [
                case.case_id for case in self.cases if case.period == "history"
            ],
            "future_case_ids": [case.case_id for case in future_cases],
            "budget_upper_bound": {
                "provider_calls": provider_upper_bound,
                "vitis_launches": vitis_upper_bound,
            },
            "budget_reservation": {
                "reservation_sha256": reservation.reservation_sha256,
                "provider_recovery_reserve": reservation.provider_recovery_reserve,
                "vitis_recovery_reserve": reservation.vitis_recovery_reserve,
            },
            "provider_calls": 0,
            "vitis_launches": 0,
        }

    def run(self) -> R5CampaignRun:
        future_cases = self._future_cases
        provider_upper_bound, vitis_upper_bound = estimate_upper_bound(
            case_count=len(future_cases), repeats=self.manifest.repeats
        )
        try:
            reservation = self.budget.reserve_with_recovery(
                provider_upper_bound=provider_upper_bound,
                vitis_upper_bound=vitis_upper_bound,
            )
        except Exception as exc:
            raise R5CampaignError("campaign budget preflight failed") from exc
        frozen_plan = self.plan()
        baselines: list[R5BaselineObservation] = []
        observations: list[R5ArmObservation] = []
        # History is identity input to the already-frozen snapshot. Replaying
        # A0-A6 on it would allow memory to affect the evidence that created
        # that same memory, violating the time-ordered R5 contract.
        for case in sorted(future_cases, key=lambda item: item.case_id):
            for repeat in range(1, self.manifest.repeats + 1):
                baseline = dict(self.executor.prepare_common_baseline(case, repeat))
                baseline_id = str(baseline.get("baseline_id", "")).strip()
                if not baseline_id:
                    raise R5CampaignError("common baseline must have an immutable baseline_id")
                if baseline.get("source_sha256") != case.source_sha256 or baseline.get("context_signature") != case.context_signature:
                    raise R5CampaignError("baseline identity does not match case inventory")
                if baseline.get("cross_arm_cache_used") is True or baseline.get("hidden_input_count", 0) != 0:
                    raise R5CampaignError("cross-arm cache or Hidden input reached the common baseline")
                baseline_provider_calls = _usage_count(
                    baseline.get("provider_calls"),
                    "baseline provider_calls",
                )
                baseline_vitis_launches = _usage_count(
                    baseline.get("vitis_launches"),
                    "baseline vitis_launches",
                )
                if baseline_provider_calls > 1 or baseline_vitis_launches > 3:
                    raise R5CampaignError(
                        "common baseline exceeded frozen Provider/Vitis upper bound"
                    )
                baseline_artifact_sha256 = _required_sha(
                    baseline.get("artifact_sha256"),
                    "baseline artifact_sha256",
                )
                self.budget = self.budget.reserve(
                    provider_calls=baseline_provider_calls,
                    vitis_launches=baseline_vitis_launches,
                )
                baselines.append(R5BaselineObservation(
                    case_id=case.case_id,
                    repeat=repeat,
                    baseline_id=baseline_id,
                    source_sha256=case.source_sha256,
                    context_signature=case.context_signature,
                    provider_calls=baseline_provider_calls,
                    vitis_launches=baseline_vitis_launches,
                    artifact_sha256=baseline_artifact_sha256,
                ))
                for arm_index, arm in enumerate(self._arm_schedule(case, repeat)):
                    value = dict(self.executor.run_arm(case=case, repeat=repeat, arm=arm, baseline=baseline, arm_index=arm_index))
                    if value.get("baseline_id") != baseline_id:
                        raise R5CampaignError("arm did not consume the common baseline")
                    if value.get("source_sha256") != case.source_sha256 or value.get("context_signature") != case.context_signature:
                        raise R5CampaignError("arm identity mismatch")
                    if value.get("cross_arm_cache_used") is True or value.get("hidden_input_count", 0) != 0:
                        raise R5CampaignError("cross-arm cache or Hidden input reached an arm")
                    provider_calls = _usage_count(
                        value.get("provider_calls"),
                        f"{arm.value} provider_calls",
                    )
                    vitis_launches = _usage_count(
                        value.get("vitis_launches"),
                        f"{arm.value} vitis_launches",
                    )
                    _validate_arm_usage(
                        arm,
                        provider_calls=provider_calls,
                        vitis_launches=vitis_launches,
                    )
                    self.budget = self.budget.reserve(provider_calls=provider_calls, vitis_launches=vitis_launches)
                    observations.append(R5ArmObservation(
                        case_id=case.case_id,
                        repeat=repeat,
                        arm=arm,
                        baseline_id=baseline_id,
                        status=str(value.get("status", "inconclusive")),
                        verified_positive=value.get("status") == "verified_positive",
                        attributable_negative=value.get("status") == "verified_negative",
                        false_repair=bool(value.get("false_repair", False)),
                        abstained=value.get("status") == "abstained",
                        inconclusive=value.get("status") == "inconclusive",
                        invalid_evidence=value.get("status") == "invalid_evidence",
                        provider_calls=provider_calls,
                        vitis_launches=vitis_launches,
                        source_sha256=case.source_sha256,
                        context_signature=case.context_signature,
                        artifact_sha256=_required_sha(value.get("artifact_sha256"), "artifact_sha256"),
                    ))
        reduction = reduce_observations(observations)
        result = R5CampaignRun(
            self.manifest.to_dict(),
            tuple(baselines),
            tuple(observations),
            reduction,
            self.budget,
            None if self.output_root is None else str(self.output_root),
            {
                "reservation_sha256": reservation.reservation_sha256,
                "provider_upper_bound": reservation.provider_upper_bound,
                "vitis_upper_bound": reservation.vitis_upper_bound,
                "provider_recovery_reserve": reservation.provider_recovery_reserve,
                "vitis_recovery_reserve": reservation.vitis_recovery_reserve,
            },
        )
        if self.output_root is not None:
            self.output_root.mkdir(parents=True, exist_ok=True)
            (self.output_root / "campaign_plan.json").write_text(json.dumps(frozen_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (self.output_root / "baseline_observations.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "baselines": [asdict(item) for item in baselines],
                    },
                    indent=2,
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            (self.output_root / "campaign_result.json").write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return result


__all__ = [
    "R5BaselineObservation",
    "R5CampaignError",
    "R5CaseSpec",
    "R5CampaignExecutor",
    "R5CampaignRun",
    "R5CampaignRunner",
]
