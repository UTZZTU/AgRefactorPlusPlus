"""Opt-in R4 extension for the existing Candidate validation orchestrator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Protocol

from agrefactor.config import EvaluationSplit
from agrefactor.evidence import audit_product_evidence
from agrefactor.models import CandidateModelAdapter, CandidateModelRequest
from agrefactor.models.candidate_adapter import candidate_response_reason_codes
from agrefactor.recovery.gated_candidate_repair import (
    R4CanaryManifest,
    R4CandidateRepairAuthorization,
    R4CandidateRepairController,
    R4ExecutionInput,
    R4KillSwitchState,
    R4MutationFailure,
)
from agrefactor.recovery.memory_gate import ApplicabilityGate, RepairPatternRevision
from agrefactor.recovery.policy import (
    RecoveryAction,
    RecoveryAuthority,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
)
from agrefactor.recovery.r3_memory_snapshot import MemorySnapshot, SnapshotRevisionSource
from agrefactor.recovery.r4_budget import (
    R4ReservePlan,
    build_r4_budget_actual,
    build_r4_reserve_plan_from_handlers,
    record_r4_budget_reservation,
)
from agrefactor.recovery.r4_episode import R4RepairEpisode
from agrefactor.recovery.r4_provenance import (
    canonical_artifact_sha256,
    validate_r4_provenance,
)
from agrefactor.recovery.shadow_advisor import (
    CalibrationCertificate,
    verify_calibrated_advisory,
)


def _canonical(value: Mapping[str, Any]) -> str:
    return canonical_artifact_sha256(value)


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True)
    )


def _budget_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: after[key] - before[key]
        for key in (
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
    }


class R4PromptFactory(Protocol):
    def build(
        self,
        *,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        task: Any,
        candidate: str,
    ) -> Any: ...


class R4MutationAdapter(Protocol):
    def mutate(
        self,
        *,
        candidate: str,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        task: Any,
    ) -> str: ...


class CandidateModelR4MutationAdapter:
    """Use the existing CandidateModelAdapter and shared BudgetManager."""

    def __init__(
        self,
        *,
        model_adapter: CandidateModelAdapter,
        prompt_factory: R4PromptFactory,
        budget: Any,
    ) -> None:
        if not isinstance(model_adapter, CandidateModelAdapter):
            raise TypeError("model_adapter must be CandidateModelAdapter")
        if not callable(getattr(prompt_factory, "build", None)):
            raise TypeError("prompt_factory must provide build")
        if not callable(getattr(budget, "ensure_available", None)) or not callable(
            getattr(budget, "consume", None)
        ):
            raise TypeError("budget must be a BudgetManager-like object")
        self._model_adapter = model_adapter
        self._prompt_factory = prompt_factory
        self._budget = budget

    def mutate(
        self,
        *,
        candidate: str,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        task: Any,
    ) -> str:
        provider_call_observed = False

        def before_provider_call() -> None:
            nonlocal provider_call_observed
            self._budget.consume(llm_calls=1)
            provider_call_observed = True

        try:
            self._budget.ensure_available(llm_calls=1)
        except Exception as exc:
            raise R4MutationFailure(
                "pre_provider_budget_failure",
                provider_call_observed=False,
            ) from exc
        try:
            prompt = self._prompt_factory.build(
                event=event,
                advisory=advisory,
                task=task,
                candidate=candidate,
            )
            request = CandidateModelRequest(
                prompt=prompt,
                task=task,
                current_candidate=candidate,
            )
        except Exception as exc:
            raise R4MutationFailure(
                "pre_provider_mutation_contract_failure",
                provider_call_observed=False,
            ) from exc
        try:
            result = self._model_adapter.generate(
                request,
                before_provider_call=before_provider_call,
                after_provider_response=lambda response: self._budget.record_model_usage(
                    response.usage
                ),
            )
        except Exception as exc:
            raise R4MutationFailure(
                (
                    "provider_or_response_contract_failure"
                    if provider_call_observed
                    else "pre_provider_model_adapter_failure"
                ),
                provider_call_observed=provider_call_observed,
                detail_codes=candidate_response_reason_codes(exc),
            ) from exc
        return result.candidate_code


class ProductEvidenceR4Auditor:
    """Invoke the existing independent product evidence auditor."""

    def audit(self, validation: Mapping[str, Any]) -> dict[str, Any]:
        report = audit_product_evidence(
            validation["audit_summary"],
            validation["audit_identity"],
            full_result=validation["audit_full_result"],
        )
        value = report.to_dict()
        value["failure_attributable"] = validation.get("failure_attributable") is True
        value["environment_excluded"] = validation.get("environment_excluded") is True
        return value


class ExistingR4ValidationAdapter:
    """Run a fresh Candidate through the existing ValidationOrchestrator."""

    def __init__(
        self,
        *,
        context: Any,
        request: Any,
        handler_factory: Any,
        authorization_id: str,
        testbench_hashes: Mapping[str, str],
        expected_reserve_plan: R4ReservePlan,
    ) -> None:
        self._context = context
        self._request = request
        self._handler_factory = handler_factory
        self._authorization_id = authorization_id
        self._testbench_hashes = dict(testbench_hashes)
        self._expected_reserve_plan = expected_reserve_plan
        self.last_result: dict[str, Any] | None = None

    def validate(self, candidate: str) -> Mapping[str, Any]:
        from agrefactor.runtime.candidate_repair_integration import (
            CandidateValidationPlanRequest,
        )
        from agrefactor.runtime.runner import RunContext
        from agrefactor.runtime.validation_orchestrator import ValidationOrchestrator

        validation_id = f"{self._authorization_id}.r4"
        request = CandidateValidationPlanRequest(
            task=self._context.task,
            candidate_code=candidate,
            original_code=self._request.original_code,
            preflight_testbench_code=self._request.preflight_testbench_code,
            suite_testbench_codes=self._request.suite_testbench_codes,
            attempt=1,
            validation_id=validation_id,
            reference_top_function=self._request.reference_top_function,
            candidate_top_function=(
                self._request.candidate_top_function or self._context.task.kernel_name
            ),
        )
        handlers = self._handler_factory.build(request)
        build_r4_reserve_plan_from_handlers(
            handlers,
            wall_time_s=self._expected_reserve_plan.wall_time_s,
            expected_plan=self._expected_reserve_plan,
        )
        child = RunContext(
            run_id=f"{self._context.run_id}.r4",
            task=self._context.task,
            budget=self._context.budget,
            trace=self._context.trace,
        )
        outcome = ValidationOrchestrator(handlers).run_detailed(
            child,
            validation_id=validation_id,
        )
        states = tuple(step.state.value for step in outcome.result.steps)
        expected = list(self._expected_reserve_plan.phase_order)
        passed = outcome.result.accepted
        selected = [
            item.to_dict()
            for step in outcome.result.steps
            for item in step.selected_feedback_items
        ]
        failure_owners = {item.get("owner") for item in selected}
        failure_attributable = bool(selected) and failure_owners == {"candidate"}
        environment_excluded = bool(selected) and not failure_owners.intersection(
            {"configuration", "evaluator", "toolchain", "unknown", "testbench"}
        )
        value = {
            "passed": passed,
            "full_prefix": passed and states == tuple(expected),
            "prefix_executed_to_terminal": bool(states)
            and states == tuple(expected[: len(states)]),
            "fresh_validation": True,
            "validation_id": validation_id,
            "testbench_hashes_after": dict(self._testbench_hashes),
            "validation_evidence_refs": [
                step.step_id for step in outcome.result.steps
            ],
            "vitis_phase_count": sum(
                state in {"public_evaluation", "csynth", "public_cosim"}
                for state in states
            ),
            "evidence_complete": bool(outcome.result.steps),
            "failure_attributable": failure_attributable,
            "environment_excluded": environment_excluded,
            "audit_summary": {
                "status": "accepted" if passed else "rejected",
                "failed_stage": None if passed else states[-1] if states else None,
                "validation": {state: "passed" for state in states},
            },
            "audit_identity": {
                "execution_id": self._authorization_id,
                "target_identity": self._context.task.target.to_dict(),
                "validation_records": outcome.result.to_dict(),
            },
            "audit_full_result": {
                "status": "accepted" if passed else "rejected",
                "succeeded": passed,
                "validation": outcome.result.to_dict(),
            },
            "validation_result": outcome.result.to_dict(),
        }
        value["audit_summary"]["execution_identity"] = {
            "execution_id": self._authorization_id
        }
        self.last_result = value
        return value


class ExistingFormalR4Auditor:
    """Require the existing validator output and product evidence auditor."""

    def audit(self, validation: Mapping[str, Any]) -> dict[str, Any]:
        if validation.get("fresh_validation") is not True or (
            validation.get("full_prefix") is not True
            and validation.get("prefix_executed_to_terminal") is not True
        ):
            return {
                "status": "contradiction",
                "has_errors": True,
                "failure_attributable": False,
                "environment_excluded": False,
                "reason": "fresh_full_prefix_missing",
            }
        try:
            return ProductEvidenceR4Auditor().audit(validation)
        except Exception as exc:
            return {
                "status": "contradiction",
                "has_errors": True,
                "failure_attributable": False,
                "environment_excluded": False,
                "reason": "evidence_auditor_error:" + type(exc).__name__,
            }


@dataclass(frozen=True, slots=True)
class R4IntegrationConfig:
    canary: R4CanaryManifest
    execution_identity: Mapping[str, Any]
    memory_snapshot: MemorySnapshot
    episode_root: str
    mutation_adapter: R4MutationAdapter
    calibration_certificate: CalibrationCertificate | None = None
    reserve_plan: R4ReservePlan | None = None
    validation_wall_time_s: float = 1200.0
    kill_switch: R4KillSwitchState = R4KillSwitchState()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.canary, R4CanaryManifest)
            or not isinstance(self.kill_switch, R4KillSwitchState)
            or not isinstance(self.memory_snapshot, MemorySnapshot)
        ):
            raise TypeError("invalid R4 integration contracts")
        if self.reserve_plan is not None and not isinstance(
            self.reserve_plan, R4ReservePlan
        ):
            raise TypeError("reserve_plan must be R4ReservePlan or None")
        if self.calibration_certificate is not None and not isinstance(
            self.calibration_certificate, CalibrationCertificate
        ):
            raise TypeError(
                "calibration_certificate must be CalibrationCertificate or None"
            )
        if not callable(getattr(self.mutation_adapter, "mutate", None)):
            raise TypeError("R4 integration providers are invalid")
        if not isinstance(self.episode_root, str) or not self.episode_root.strip():
            raise ValueError("episode_root must be non-empty")
        if (
            isinstance(self.validation_wall_time_s, bool)
            or not isinstance(self.validation_wall_time_s, (int, float))
            or self.validation_wall_time_s <= 0
        ):
            raise ValueError("validation_wall_time_s must be positive")


class ExistingOrchestratorR4Integration:
    """Consume R2 shadow evidence, run R3 Gate, and execute an R4 side lane."""

    def __init__(
        self,
        config: R4IntegrationConfig,
        *,
        gate: ApplicabilityGate | None = None,
        policy: RecoveryPolicy | None = None,
        auditor: Any | None = None,
    ) -> None:
        self._config = config
        self._gate = gate or ApplicabilityGate()
        self._policy = policy or RecoveryPolicy()
        self._auditor = auditor or ExistingFormalR4Auditor()

    def run_from_existing_orchestrator(
        self,
        *,
        context: Any,
        request: Any,
        main_result: Any,
        handler_factory: Any,
    ) -> Mapping[str, Any]:
        return self.run(
            context=context,
            request=request,
            main_result=main_result,
            handler_factory=handler_factory,
        )

    def run(
        self,
        *,
        context: Any,
        request: Any,
        main_result: Any,
        handler_factory: Any,
    ) -> Mapping[str, Any]:
        if request.llm_advisory_mode != "candidate-only":
            raise ValueError("R4 extension requires candidate-only mode")
        events = list(main_result.metadata.get("diagnostic_events", ()))
        shadows = list(main_result.metadata.get("r2_shadow_diagnostics", ()))
        pairs = []
        for event in events:
            matches = [
                item for item in shadows if item.get("event_id") == event.get("event_id")
            ]
            if len(matches) != 1:
                continue
            shadow = matches[0]
            advisory = dict(shadow.get("advisory", {}))
            if (
                shadow.get("input_status") == "eligible"
                and shadow.get("critical_safety_violation") is False
                and shadow.get("equivalence", {}).get("equivalent") is True
                and advisory.get("suspected_owner") == "candidate"
                and advisory.get("repair_scope") == "candidate_only"
                and advisory.get("abstain_reason") is None
            ):
                pairs.append((event, shadow, advisory))
        if not pairs:
            return self._terminal("abstained", "eligible_r2_event_missing")
        if len(pairs) > 1:
            return self._terminal("abstained", "eligible_r2_event_ambiguous")

        event, shadow, advisory = pairs[0]
        calibration = verify_calibrated_advisory(
            self._config.calibration_certificate,
            shadow=shadow,
        )
        if not calibration.verified:
            return self._terminal(
                "abstained",
                "r2_calibration_unverified",
                calibration=calibration.to_dict(),
            )
        advisory["advisory_id"] = _canonical(
            {
                "event_id": event["event_id"],
                "request_sha256": shadow.get("request_sha256"),
                "advisory": advisory,
                "provider_identity": shadow.get("provider_identity", {}),
                "calibration_certificate_id": calibration.certificate_id,
            }
        )
        advisory["calibration_certificate_id"] = calibration.certificate_id
        advisory["calibration_verified"] = True
        revision = SnapshotRevisionSource(self._config.memory_snapshot).resolve(
            event=event,
            advisory=advisory,
        )
        if not isinstance(revision, RepairPatternRevision):
            raise TypeError("snapshot revision source returned an invalid revision")
        if calibration.certificate_id not in revision.calibration_refs:
            return self._terminal(
                "invalid_evidence",
                "revision_calibration_certificate_mismatch",
                calibration=calibration.to_dict(),
                revision_id=revision.revision_id,
            )
        gate_context = self._config.memory_snapshot.context_for(
            event=event,
            advisory=advisory,
        )
        gate_context["calibrated_risk_ok"] = (
            calibration.verified
            and gate_context.get("calibrated_risk_ok") is True
        )
        gate_result = self._gate.evaluate(
            context=gate_context,
            revision=revision,
            evidence_refs=tuple(advisory.get("evidence_refs", ())),
        )
        testbench_hashes = {
            "preflight": _text_sha(request.preflight_testbench_code),
            **{
                key: _text_sha(value)
                for key, value in request.suite_testbench_codes.items()
            },
        }
        public_before = {
            suite.suite_id: testbench_hashes[suite.suite_id]
            for suite in context.task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        }
        hidden_before = {
            suite.suite_id: testbench_hashes[suite.suite_id]
            for suite in context.task.test_suites
            if suite.split is EvaluationSplit.HIDDEN
        }
        lineage = tuple(
            dict.fromkeys(
                (
                    *tuple(getattr(self._config.memory_snapshot, "episode_ids", ())),
                    event["event_id"],
                )
            )
        )
        if gate_result.decision.value != "accept":
            episode = self._episode(
                event=event,
                lineage=lineage,
                advisory=advisory,
                gate=gate_result.to_dict(),
                revision=revision,
                authorization={"status": "not_created"},
                policy_decision={},
                ledger_event={},
                budget_requested={},
                budget_effective={},
                budget_actual={},
                candidate_before_sha256=_text_sha(main_result.final_candidate),
                public_before=public_before,
                hidden_before=hidden_before,
                outcome="abstained",
                reason="gate_" + gate_result.decision.value,
                result=None,
                validation=None,
                auditor={"status": "not_run"},
                budget_delta={},
            )
            path = episode.append_only_write(self._config.episode_root)
            return self._terminal(
                "abstained",
                "gate_" + gate_result.decision.value,
                episode_path=str(path),
                episode_hash=episode.episode_hash,
            )

        deterministic_terminal = {
            "validation_id": main_result.validation_id,
            "state": main_result.last_validation_state.value,
            "status": main_result.status.value,
            "candidate_sha256": _text_sha(main_result.final_candidate),
        }
        preauthorization_id = _canonical(
            {
                "event": event["event_id"],
                "terminal": deterministic_terminal,
                "revision": revision.revision_hash,
            }
        )
        preflight_plan, handlers = self._validation_plan(
            context=context,
            request=request,
            handler_factory=handler_factory,
            candidate=main_result.final_candidate,
            validation_id=f"{preauthorization_id}.r4",
        )
        reserve_plan = build_r4_reserve_plan_from_handlers(
            handlers,
            wall_time_s=self._config.validation_wall_time_s,
            expected_plan=self._config.reserve_plan,
        )
        budget_before = context.budget.snapshot().to_dict()
        policy_request = RecoveryRequest(
            action=RecoveryAction.REPAIR,
            role=RecoveryRole.CANDIDATE,
            stage=RecoveryStage(event["stage"]),
            evidence_view="agent_safe",
            owner_authority=RecoveryAuthority.LLM_ADVISORY,
            lineage_id=event["run_id"],
            physical_tool_launched=True,
            evidence_complete=True,
            advisory_mode="candidate-only",
        )
        ledger = RecoveryLedger(self._policy)
        try:
            policy_decision = ledger.reserve(
                policy_request,
                budget=context.budget,
                restart_reserve=reserve_plan.to_budget_request(),
            )
            budget_after_admission = context.budget.snapshot().to_dict()
        except Exception as exc:
            budget_after = context.budget.snapshot().to_dict()
            budget_actual = build_r4_budget_actual(
                reserve_plan,
                budget_before=budget_before,
                budget_after=budget_after,
                provider_calls=0,
                mutation_calls=0,
                auditor_reads=0,
            )
            ledger_event = ledger.events[-1].to_dict() if ledger.events else {}
            episode = self._episode(
                event=event,
                lineage=lineage,
                advisory=advisory,
                gate=gate_result.to_dict(),
                revision=revision,
                authorization={"status": "not_created"},
                policy_decision={},
                ledger_event=ledger_event,
                budget_requested=reserve_plan.to_dict(),
                budget_effective={
                    "requested": reserve_plan.to_dict(),
                    "effective": reserve_plan.to_dict(),
                    "admitted": False,
                    "reason": type(exc).__name__,
                },
                budget_actual=budget_actual,
                candidate_before_sha256=_text_sha(main_result.final_candidate),
                public_before=public_before,
                hidden_before=hidden_before,
                outcome="inconclusive",
                reason="policy_ledger_or_budget_denied",
                result=None,
                validation=None,
                auditor={"status": "not_run"},
                budget_delta=budget_actual["budget_delta"],
            )
            path = episode.append_only_write(self._config.episode_root)
            return self._terminal(
                "inconclusive",
                "policy_ledger_or_budget_denied",
                episode_path=str(path),
                episode_hash=episode.episode_hash,
                budget_requested=reserve_plan.to_dict(),
                budget_actual=budget_actual,
            )

        ledger_event = ledger.events[-1].to_dict()
        reservation = record_r4_budget_reservation(
            reserve_plan,
            budget_before=budget_before,
            budget_after_admission=budget_after_admission,
        )
        policy_artifact = policy_decision.to_dict()
        terminal_ref = _canonical(deterministic_terminal)
        canary_map = {
            **self._config.canary.to_dict(),
            "manifest_sha256": self._config.canary.manifest_sha256,
            "enabled": self._config.canary.enabled,
            "operator_enabled": self._config.canary.operator_enabled,
        }
        authorization = R4CandidateRepairAuthorization(
            run_id=event["run_id"],
            event_ref=event["event_id"],
            advisory_id=advisory["advisory_id"],
            gate_decision=gate_result.decision.value,
            pattern_lifecycle=revision.lifecycle.value,
            gate_contract_hash=gate_result.contract_hash,
            revision_sha256=revision.revision_hash,
            canary_manifest_sha256=self._config.canary.manifest_sha256,
            before_candidate_sha256=_text_sha(main_result.final_candidate),
            policy_decision_id=_canonical(policy_artifact),
            budget_reservation_id=reservation.reservation_id,
            ledger_reservation_id=_canonical(ledger_event),
            deterministic_terminal_ref=terminal_ref,
        )
        provenance = validate_r4_provenance(
            event=event,
            execution_identity=self._config.execution_identity,
            advisory=advisory,
            gate=gate_result.to_dict(),
            revision=revision.to_dict(),
            authorization=authorization.to_dict(),
            canary=canary_map,
            candidate=main_result.final_candidate,
            original=request.original_code,
            testbench_hashes=testbench_hashes,
            policy_decision=policy_artifact,
            ledger_event=ledger_event,
            budget_reservation=reservation.to_dict(),
            deterministic_terminal=deterministic_terminal,
        )
        if not provenance.valid:
            return self._terminal(
                "invalid_evidence",
                "provenance_validation_failed",
                reasons=list(provenance.reasons),
            )

        execution = R4ExecutionInput(
            authorization=authorization,
            canary=self._config.canary,
            kill_switch=self._config.kill_switch,
            execution_identity=self._config.execution_identity,
            advisory=advisory,
            candidate=main_result.final_candidate,
            original=request.original_code,
            testbench_hashes=testbench_hashes,
            route_fingerprint=authorization.deterministic_terminal_ref,
            policy_decision=policy_artifact,
            ledger_event=ledger_event,
            budget_reservation=reservation.to_dict(),
        )
        validator = ExistingR4ValidationAdapter(
            context=context,
            request=request,
            handler_factory=handler_factory,
            authorization_id=authorization.authorization_id,
            testbench_hashes=testbench_hashes,
            expected_reserve_plan=reserve_plan,
        )
        audit_result: dict[str, Any] = {"status": "not_run"}
        auditor_reads = 0

        def audit(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            nonlocal audit_result, auditor_reads
            auditor_reads += 1
            audit_result = _plain(self._auditor.audit(payload["validation"]))
            return audit_result

        controller = R4CandidateRepairController()
        result = controller.run(
            execution,
            mutate_candidate=lambda candidate: self._config.mutation_adapter.mutate(
                candidate=candidate,
                event=event,
                advisory=advisory,
                task=context.task,
            ),
            validate_candidate=validator.validate,
            audit=audit,
        )
        after_budget = context.budget.snapshot().to_dict()
        actual_provider_calls = after_budget["llm_calls"] - budget_before["llm_calls"]
        if actual_provider_calls < 0 or actual_provider_calls > 1:
            raise RuntimeError("r4_provider_call_accounting_out_of_range")
        if (
            isinstance(self._config.mutation_adapter, CandidateModelR4MutationAdapter)
            and result.provider_call_count != actual_provider_calls
        ):
            raise RuntimeError("r4_provider_call_accounting_mismatch")
        budget_actual = build_r4_budget_actual(
            reserve_plan,
            budget_before=budget_before,
            budget_after=after_budget,
            provider_calls=result.provider_call_count,
            mutation_calls=result.mutation_count,
            auditor_reads=auditor_reads,
        )
        delta = _budget_delta(budget_before, after_budget)
        validation = validator.last_result
        episode = self._episode(
            event=event,
            lineage=lineage,
            advisory=advisory,
            gate=gate_result.to_dict(),
            revision=revision,
            authorization=authorization.to_dict(),
            policy_decision=policy_artifact,
            ledger_event=ledger_event,
            budget_requested=reserve_plan.to_dict(),
            budget_effective=reservation.to_dict(),
            budget_actual=budget_actual,
            candidate_before_sha256=authorization.before_candidate_sha256,
            public_before=public_before,
            hidden_before=hidden_before,
            outcome=result.outcome.value,
            reason=";".join(result.reasons),
            result=result,
            validation=validation,
            auditor=audit_result,
            budget_delta=delta,
        )
        path = episode.append_only_write(self._config.episode_root)
        return {
            "schema_version": 2,
            "status": result.outcome.value,
            "r4_result": result.to_dict(),
            "provenance": {
                "valid": provenance.valid,
                "checked_refs": list(provenance.checked_refs),
            },
            "gate": gate_result.to_dict(),
            "episode_path": str(path),
            "episode_hash": episode.episode_hash,
            "budget": {
                "requested": reserve_plan.to_dict(),
                "effective": reservation.to_dict(),
                "actual": budget_actual,
            },
            "main_result_unchanged": True,
            "accepted_by_integration": False,
        }

    def _validation_plan(
        self,
        *,
        context: Any,
        request: Any,
        handler_factory: Any,
        candidate: str,
        validation_id: str,
    ) -> tuple[Any, Mapping[Any, Any]]:
        from agrefactor.runtime.candidate_repair_integration import (
            CandidateValidationPlanRequest,
        )

        plan = CandidateValidationPlanRequest(
            task=context.task,
            candidate_code=candidate,
            original_code=request.original_code,
            preflight_testbench_code=request.preflight_testbench_code,
            suite_testbench_codes=request.suite_testbench_codes,
            attempt=1,
            validation_id=validation_id,
            reference_top_function=request.reference_top_function,
            candidate_top_function=(
                request.candidate_top_function or context.task.kernel_name
            ),
        )
        handlers = handler_factory.build(plan)
        if not isinstance(handlers, Mapping):
            raise TypeError("handler_factory.build must return a mapping")
        return plan, handlers

    def _episode(
        self,
        *,
        event: Mapping[str, Any],
        lineage: tuple[str, ...],
        advisory: Mapping[str, Any],
        gate: Mapping[str, Any],
        revision: RepairPatternRevision,
        authorization: Mapping[str, Any],
        policy_decision: Mapping[str, Any],
        ledger_event: Mapping[str, Any],
        budget_requested: Mapping[str, Any],
        budget_effective: Mapping[str, Any],
        budget_actual: Mapping[str, Any],
        candidate_before_sha256: str,
        public_before: Mapping[str, str],
        hidden_before: Mapping[str, str],
        outcome: str,
        reason: str,
        result: Any,
        validation: Mapping[str, Any] | None,
        auditor: Mapping[str, Any],
        budget_delta: Mapping[str, Any],
    ) -> R4RepairEpisode:
        from datetime import datetime, timezone

        after_hash = None if result is None else result.after_candidate_sha256
        return R4RepairEpisode(
            episode_id="r4-"
            + _canonical({"event": event["event_id"], "authorization": authorization})[:32],
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            event_ref=event["event_id"],
            lineage=lineage,
            execution_identity=self._config.execution_identity,
            request_identity={
                "validation_id": event["validation_id"],
                "run_id": event["run_id"],
            },
            advisory_identity=advisory,
            gate_identity={**dict(gate), "revision_hash": revision.revision_hash},
            authorization_identity=authorization,
            policy_decision=policy_decision,
            ledger_event=ledger_event,
            budget_requested=budget_requested,
            budget_effective=budget_effective,
            budget_actual=budget_actual,
            candidate_before_sha256=candidate_before_sha256,
            candidate_after_sha256=after_hash,
            public_testbench_before=public_before,
            public_testbench_after=public_before,
            hidden_testbench_before=hidden_before,
            hidden_testbench_after=hidden_before,
            formal_validation_id=None if result is None else result.formal_validation_id,
            validation_evidence_refs=tuple(
                ()
                if validation is None
                else validation.get("validation_evidence_refs", ())
            ),
            auditor_result=auditor,
            budget_delta=budget_delta,
            provider_call_count=0 if result is None else result.provider_call_count,
            vitis_phase_count=(
                0 if validation is None else int(validation.get("vitis_phase_count", 0))
            ),
            outcome=outcome,
            outcome_reason=reason,
        )

    @staticmethod
    def _terminal(status: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "status": status,
            "reason": reason,
            "main_result_unchanged": True,
            "accepted_by_integration": False,
            **extra,
        }


__all__ = [
    "CandidateModelR4MutationAdapter",
    "ExistingFormalR4Auditor",
    "ExistingOrchestratorR4Integration",
    "ExistingR4ValidationAdapter",
    "ProductEvidenceR4Auditor",
    "R4IntegrationConfig",
    "R4MutationAdapter",
    "R4PromptFactory",
]
