"""Execute paired R5 arms from one captured existing-refactor baseline."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Protocol

from agrefactor.product.source_bootstrap import R5CommonBaselineCapture
from agrefactor.models import CandidateModelAdapter
from agrefactor.recovery.shadow_advisor import run_shadow_diagnostics
from agrefactor.runtime import BudgetManager, RunContext, TraceRecorder
from agrefactor.runtime.candidate_repair_integration import (
    LocalCandidateValidationHandlerFactory,
)

from .r5_protocol import (
    COMMON_BASELINE_PROVIDER_CAP,
    COMMON_BASELINE_VITIS_CAP,
    R5Arm,
)
from .r5_runner import R5CampaignError, R5CaseSpec


class R5BaselineRunner(Protocol):
    def __call__(
        self,
        case: R5CaseSpec,
        repeat: int,
        artifact_root: Path,
    ) -> R5CommonBaselineCapture: ...


class R5AdvisorFactory(Protocol):
    def __call__(
        self,
        capture: R5CommonBaselineCapture,
        arm: R5Arm,
        context: RunContext,
    ) -> Any: ...


class R5IntegrationFactory(Protocol):
    def __call__(
        self,
        capture: R5CommonBaselineCapture,
        arm: R5Arm,
        context: RunContext,
        episode_root: Path,
        model_adapter: CandidateModelAdapter,
    ) -> Any: ...


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"R5 artifact already exists: {path}")
    payload = json.loads(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
    try:
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_component(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _budget_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[int, int]:
    provider = int(after["llm_calls"]) - int(before["llm_calls"])
    vitis = sum(
        int(after[name]) - int(before[name])
        for name in ("csim_calls", "csynth_calls", "cosim_calls")
    )
    if provider < 0 or vitis < 0:
        raise R5CampaignError("R5 arm budget counters moved backwards")
    return provider, vitis


def _shadow_main_snapshot(capture: R5CommonBaselineCapture) -> dict[str, Any]:
    result = capture.formal_result
    ledger = result.metadata.get("recovery_ledger")
    events = ledger.get("events", ()) if isinstance(ledger, Mapping) else ()
    return {
        "route": result.last_validation_state.value,
        "status": result.status.value,
        "final_candidate_sha256": hashlib.sha256(
            (result.final_candidate.rstrip() + "\n").encode("utf-8")
        ).hexdigest(),
        "recovery_ledger_count": len(events),
        "repair_count": int(result.metadata.get("repair_attempt_count", 0)),
        "best_correct_pointer": None,
    }


class ExistingRefactorR5CampaignExecutor:
    """Fork A0-A6 from one product baseline without a second Vitis flow."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        baseline_runner: R5BaselineRunner,
        advisor_factory: R5AdvisorFactory,
        integration_factory: R5IntegrationFactory,
    ) -> None:
        if not callable(baseline_runner):
            raise TypeError("baseline_runner must be callable")
        if not callable(advisor_factory):
            raise TypeError("advisor_factory must be callable")
        if not callable(integration_factory):
            raise TypeError("integration_factory must be callable")
        self._artifact_root = Path(artifact_root).expanduser().resolve()
        self._baseline_runner = baseline_runner
        self._advisor_factory = advisor_factory
        self._integration_factory = integration_factory
        self._captures: dict[str, R5CommonBaselineCapture] = {}
        self._capture_keys: dict[str, tuple[str, int]] = {}
        self._arm_roots: set[Path] = set()

    def _pair_root(self, case: R5CaseSpec, repeat: int) -> Path:
        return (
            self._artifact_root
            / f"case-{_safe_component(case.case_id)}"
            / f"repeat-{repeat:02d}"
        )

    def prepare_common_baseline(
        self,
        case: R5CaseSpec,
        repeat: int,
    ) -> Mapping[str, Any]:
        pair_root = self._pair_root(case, repeat)
        common_root = pair_root / "common"
        if common_root.exists():
            raise R5CampaignError("common baseline root already exists")
        capture = self._baseline_runner(case, repeat, common_root)
        return self._register_common_baseline(case, repeat, capture)

    def adopt_history_common_baseline(
        self,
        *,
        case_id: str,
        source_sha256: str,
        repeat: int,
        capture: R5CommonBaselineCapture,
    ) -> tuple[R5CaseSpec, Mapping[str, Any]]:
        """Register a real history capture whose context is observed at runtime.

        Future campaign contexts are frozen before execution.  A history
        acquisition context cannot be known until the ordinary ``refactor``
        baseline has produced its deterministic diagnostic event, so this
        method binds the observed context without weakening the source hash or
        common-baseline checks used by the paired executor.
        """

        if not isinstance(capture, R5CommonBaselineCapture):
            raise TypeError("capture must be R5CommonBaselineCapture")
        case = R5CaseSpec(
            case_id=case_id,
            source_sha256=source_sha256,
            context_signature=capture.context_signature,
            period="history",
        )
        return case, self._register_common_baseline(case, repeat, capture)

    def _register_common_baseline(
        self,
        case: R5CaseSpec,
        repeat: int,
        capture: R5CommonBaselineCapture,
    ) -> Mapping[str, Any]:
        pair_root = self._pair_root(case, repeat)
        common_root = pair_root / "common"
        if not isinstance(capture, R5CommonBaselineCapture):
            raise TypeError("baseline_runner must return R5CommonBaselineCapture")
        if (
            capture.source_sha256 != case.source_sha256
            or capture.context_signature != case.context_signature
        ):
            raise R5CampaignError("captured product baseline identity mismatch")
        if capture.baseline_id in self._captures:
            raise R5CampaignError("captured baseline_id is not unique")
        if (
            capture.provider_calls > COMMON_BASELINE_PROVIDER_CAP
            or capture.vitis_launches > COMMON_BASELINE_VITIS_CAP
        ):
            raise R5CampaignError("captured baseline exceeded frozen usage bound")
        event_signatures = {
            item.get("context_signature")
            for item in capture.formal_result.metadata.get(
                "diagnostic_events",
                (),
            )
            if isinstance(item, Mapping)
            and isinstance(item.get("context_signature"), str)
        }
        if len(event_signatures) > 1 or (
            event_signatures
            and event_signatures != {capture.context_signature}
        ):
            raise R5CampaignError(
                "captured diagnostic context does not match the common baseline"
            )
        self._captures[capture.baseline_id] = capture
        self._capture_keys[capture.baseline_id] = (case.case_id, repeat)
        manifest = capture.to_manifest()
        _write_json_exclusive(pair_root / "common_baseline.json", manifest)
        return manifest

    def run_arm(
        self,
        *,
        case: R5CaseSpec,
        repeat: int,
        arm: R5Arm,
        baseline: Mapping[str, Any],
        arm_index: int,
    ) -> Mapping[str, Any]:
        baseline_id = str(baseline.get("baseline_id", ""))
        capture = self._captures.get(baseline_id)
        if capture is None:
            raise R5CampaignError("arm references an unknown common baseline")
        if self._capture_keys[baseline_id] != (case.case_id, repeat):
            raise R5CampaignError("arm crossed a case/repeat baseline boundary")

        arm_root = (
            self._pair_root(case, repeat)
            / "arms"
            / f"{arm_index:02d}-{arm.value}"
        )
        if arm_root in self._arm_roots or arm_root.exists():
            raise R5CampaignError("arm workspace is not isolated")
        self._arm_roots.add(arm_root)
        arm_root.mkdir(parents=True)
        work_root = arm_root / "work"
        handler_factory = LocalCandidateValidationHandlerFactory(
            work_root,
            csynth_timelimit=capture.phase_config.csynth_timelimit,
            csim_timelimit=capture.phase_config.csim_timelimit,
            cosim_timelimit=capture.phase_config.cosim_timelimit,
            cosim_policy=capture.phase_config.cosim_policy,
        )
        budget = BudgetManager(capture.budget_limits)
        context = RunContext(
            run_id=f"{capture.run_id}.r5.{arm.value}",
            task=capture.formal_task,
            budget=budget,
            trace=TraceRecorder(
                f"{capture.run_id}.r5.{arm.value}",
                task_id=capture.formal_task.task_id,
                output_path=arm_root / "trace.jsonl",
            ),
        )
        before = budget.snapshot().to_dict()
        main_result = capture.formal_result
        shadow_artifacts: tuple[dict[str, Any], ...] = ()
        diagnostic_events = tuple(
            item
            for item in main_result.metadata.get("diagnostic_events", ())
            if isinstance(item, Mapping)
        )
        if arm is not R5Arm.A0 and len(diagnostic_events) == 1:
            advisor = self._advisor_factory(capture, arm, context)
            snapshot = _shadow_main_snapshot(capture)
            shadow_artifacts = run_shadow_diagnostics(
                diagnostic_events,
                advisor=advisor,
                main_before=snapshot,
                main_after=snapshot,
            )
            main_result = replace(
                main_result,
                metadata={
                    **dict(main_result.metadata),
                    "r2_shadow_diagnostics": list(shadow_artifacts),
                    "r2_shadow_enabled": True,
                },
            )

        outcome: Mapping[str, Any] | None = None
        if arm in {R5Arm.A2, R5Arm.A3, R5Arm.A4, R5Arm.A5, R5Arm.A6}:
            if len(diagnostic_events) != 1:
                reason = (
                    "accepted_without_diagnostic"
                    if main_result.accepted and not diagnostic_events
                    else "eligible_r2_event_not_unique"
                )
                outcome = {
                    "schema_version": "r5-existing-orchestrator-integration-v1",
                    "status": "abstained",
                    "reason": reason,
                    "main_result_unchanged": True,
                    "accepted_by_integration": False,
                }
            else:
                integration = self._integration_factory(
                    capture,
                    arm,
                    context,
                    arm_root / "episodes",
                    capture.model_adapter.fork(),
                )
                integration_arm = getattr(
                    getattr(integration, "profile", None),
                    "arm",
                    None,
                )
                if getattr(integration_arm, "value", None) != arm.value:
                    raise R5CampaignError("integration factory returned the wrong arm")
                approved_snippets = tuple(
                    getattr(integration, "approved_memory_snippets", ())
                )
                arm_request = replace(
                    capture.formal_request,
                    r5_arm=arm.value,
                    llm_advisory_mode="candidate-only",
                    approved_memory_snippets=approved_snippets,
                )
                outcome = integration.run_from_existing_orchestrator(
                    context=context,
                    request=arm_request,
                    main_result=main_result,
                    handler_factory=handler_factory,
                )
                if not isinstance(outcome, Mapping):
                    raise TypeError("R5 integration must return a mapping")

        after = budget.snapshot().to_dict()
        provider_calls, vitis_launches = _budget_delta(before, after)
        verified_repair = (
            outcome is not None
            and str(outcome.get("status", "")) == "verified_positive"
        )
        status = (
            str(outcome.get("status", "inconclusive"))
            if outcome is not None
            else (
                "baseline_accepted"
                if main_result.accepted
                else "abstained"
            )
        )
        artifact = {
            "schema_version": "r5-product-arm-observation-v1",
            "baseline_id": capture.baseline_id,
            "case_id": case.case_id,
            "repeat": repeat,
            "arm": arm.value,
            "status": status,
            "baseline_accepted": bool(capture.formal_result.accepted),
            "verified_repair": verified_repair,
            "source_sha256": capture.source_sha256,
            "context_signature": capture.context_signature,
            "provider_calls": provider_calls,
            "vitis_launches": vitis_launches,
            "r2_shadow_diagnostics": list(shadow_artifacts),
            "integration": None if outcome is None else dict(outcome),
            "hidden_input_count": 0,
            "cross_arm_cache_used": False,
            "workspace_sha256": _canonical_sha256(
                {
                    "baseline_id": capture.baseline_id,
                    "arm": arm.value,
                    "arm_index": arm_index,
                    "root": str(arm_root),
                }
            ),
        }
        artifact_path = arm_root / "arm_result.json"
        _write_json_exclusive(artifact_path, artifact)
        return {
            **artifact,
            "artifact_sha256": _file_sha256(artifact_path),
        }


__all__ = [
    "ExistingRefactorR5CampaignExecutor",
    "R5AdvisorFactory",
    "R5BaselineRunner",
    "R5IntegrationFactory",
]
