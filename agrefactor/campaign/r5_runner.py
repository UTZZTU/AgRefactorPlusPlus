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
from pathlib import Path
from typing import Any, Protocol

from agrefactor.recovery.r5_budget import R5BudgetLedger

from .r5_protocol import R5Arm, R5CampaignManifest
from .r5_reducer import R5ArmObservation, R5CampaignReduction, reduce_observations


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
        if not self.case_id.strip() or len(self.source_sha256) != 64 or len(self.context_signature) != 64:
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
class R5CampaignRun:
    manifest: Mapping[str, Any]
    observations: tuple[R5ArmObservation, ...]
    reduction: R5CampaignReduction
    budget: R5BudgetLedger
    output_root: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "manifest": dict(self.manifest),
            "observations": [asdict(item) for item in self.observations],
            "reduction": self.reduction.to_dict(),
            "budget": self.budget.to_dict(),
            "output_root": self.output_root,
        }


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _required_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise R5CampaignError(f"{name} must be SHA-256")
    return value


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
                for case in self.cases
                for repeat in range(1, self.manifest.repeats + 1)
            ],
            "provider_calls": 0,
            "vitis_launches": 0,
        }

    def run(self) -> R5CampaignRun:
        observations: list[R5ArmObservation] = []
        for case in sorted(self.cases, key=lambda item: item.case_id):
            for repeat in range(1, self.manifest.repeats + 1):
                baseline = dict(self.executor.prepare_common_baseline(case, repeat))
                baseline_id = str(baseline.get("baseline_id", "")).strip()
                if not baseline_id:
                    raise R5CampaignError("common baseline must have an immutable baseline_id")
                if baseline.get("source_sha256") != case.source_sha256 or baseline.get("context_signature") != case.context_signature:
                    raise R5CampaignError("baseline identity does not match case inventory")
                for arm_index, arm in enumerate(self._arm_schedule(case, repeat)):
                    value = dict(self.executor.run_arm(case=case, repeat=repeat, arm=arm, baseline=baseline, arm_index=arm_index))
                    if value.get("baseline_id") != baseline_id:
                        raise R5CampaignError("arm did not consume the common baseline")
                    if value.get("source_sha256") != case.source_sha256 or value.get("context_signature") != case.context_signature:
                        raise R5CampaignError("arm identity mismatch")
                    if value.get("cross_arm_cache_used") is True or value.get("hidden_input_count", 0) != 0:
                        raise R5CampaignError("cross-arm cache or Hidden input reached an arm")
                    provider_calls = int(value.get("provider_calls", 0))
                    vitis_launches = int(value.get("vitis_launches", 0))
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
        result = R5CampaignRun(self.manifest.to_dict(), tuple(observations), reduction, self.budget, None if self.output_root is None else str(self.output_root))
        if self.output_root is not None:
            self.output_root.mkdir(parents=True, exist_ok=True)
            (self.output_root / "campaign_plan.json").write_text(json.dumps(self.plan(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (self.output_root / "campaign_result.json").write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return result


__all__ = ["R5CampaignError", "R5CaseSpec", "R5CampaignExecutor", "R5CampaignRun", "R5CampaignRunner"]
