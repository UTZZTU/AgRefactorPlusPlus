"""Opt-in R4 extension for the existing Candidate validation orchestrator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from agrefactor.config import EvaluationSplit
from agrefactor.evidence import audit_product_evidence
from agrefactor.models import CandidateModelAdapter, CandidateModelRequest
from agrefactor.recovery.gated_candidate_repair import (
    R4CanaryManifest,
    R4CandidateRepairAuthorization,
    R4CandidateRepairController,
    R4ExecutionInput,
    R4KillSwitchState,
    R4Outcome,
)
from agrefactor.recovery.memory_gate import ApplicabilityGate, RepairPatternRevision
from agrefactor.recovery.policy import RecoveryAction, RecoveryAuthority, RecoveryPolicy, RecoveryRequest, RecoveryRole, RecoveryStage
from agrefactor.recovery.r4_budget import R4ReservePlan
from agrefactor.recovery.r4_episode import R4RepairEpisode
from agrefactor.recovery.r4_provenance import validate_r4_provenance
from agrefactor.recovery.r3_memory_snapshot import MemorySnapshot, SnapshotRevisionSource


def _canonical(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _candidate_sha(value: str) -> str:
    return hashlib.sha256((value.rstrip() + "\n").encode("utf-8")).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class R4PromptFactory(Protocol):
    def build(self, *, event: Mapping[str, Any], advisory: Mapping[str, Any], task: Any, candidate: str) -> Any: ...


class R4MutationAdapter(Protocol):
    def mutate(self, *, candidate: str, event: Mapping[str, Any], advisory: Mapping[str, Any], task: Any) -> str: ...


class CandidateModelR4MutationAdapter:
    """Use the existing CandidateModelAdapter and shared BudgetManager."""

    def __init__(self, *, model_adapter: CandidateModelAdapter, prompt_factory: R4PromptFactory, budget: Any) -> None:
        if not isinstance(model_adapter, CandidateModelAdapter):
            raise TypeError("model_adapter must be CandidateModelAdapter")
        if not callable(getattr(prompt_factory, "build", None)):
            raise TypeError("prompt_factory must provide build")
        if not callable(getattr(budget, "ensure_available", None)) or not callable(getattr(budget, "consume", None)):
            raise TypeError("budget must be a BudgetManager-like object")
        self._model_adapter = model_adapter
        self._prompt_factory = prompt_factory
        self._budget = budget

    def mutate(self, *, candidate: str, event: Mapping[str, Any], advisory: Mapping[str, Any], task: Any) -> str:
        self._budget.ensure_available(llm_calls=1)
        prompt = self._prompt_factory.build(event=event, advisory=advisory, task=task, candidate=candidate)
        result = self._model_adapter.generate(
            CandidateModelRequest(prompt=prompt, task=task, current_candidate=candidate),
            before_provider_call=lambda: self._budget.consume(llm_calls=1),
            after_provider_response=lambda response: self._budget.record_model_usage(response.usage),
        )
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

    def __init__(self, *, context: Any, request: Any, handler_factory: Any, authorization_id: str, testbench_hashes: Mapping[str, str]) -> None:
        self._context = context
        self._request = request
        self._handler_factory = handler_factory
        self._authorization_id = authorization_id
        self._testbench_hashes = dict(testbench_hashes)
        self.last_result: dict[str, Any] | None = None

    def validate(self, candidate: str) -> Mapping[str, Any]:
        from agrefactor.runtime.candidate_repair_integration import CandidateValidationPlanRequest
        from agrefactor.runtime.runner import RunContext
        from agrefactor.runtime.validation_orchestrator import ValidationOrchestrator

        validation_id = f"{self._request.initial_candidate and self._authorization_id}.r4"
        plan = CandidateValidationPlanRequest(
            task=self._context.task,
            candidate_code=candidate,
            original_code=self._request.original_code,
            preflight_testbench_code=self._request.preflight_testbench_code,
            suite_testbench_codes=self._request.suite_testbench_codes,
            attempt=1,
            validation_id=validation_id,
            reference_top_function=self._request.reference_top_function,
            candidate_top_function=self._request.candidate_top_function or self._context.task.kernel_name,
        )
        child = RunContext(run_id=f"{self._context.run_id}.r4", task=self._context.task, budget=self._context.budget, trace=self._context.trace)
        outcome = ValidationOrchestrator(self._handler_factory.build(plan)).run_detailed(child, validation_id=validation_id)
        states = tuple(step.state.value for step in outcome.result.steps)
        expected = ["preflight"]
        if any(suite.split is EvaluationSplit.PUBLIC for suite in self._context.task.test_suites):
            expected.append("public_evaluation")
        expected.append("csynth")
        if any(suite.split is EvaluationSplit.PUBLIC for suite in self._context.task.test_suites):
            expected.append("public_cosim")
        if any(suite.split is EvaluationSplit.HIDDEN for suite in self._context.task.test_suites):
            expected.append("hidden_evaluation")
        passed = outcome.result.accepted
        value = {
            "passed": passed,
            "full_prefix": passed and states == tuple(expected),
            "prefix_executed_to_terminal": bool(states) and states == tuple(expected[:len(states)]),
            "fresh_validation": True,
            "validation_id": validation_id,
            "testbench_hashes_after": dict(self._testbench_hashes),
            "validation_evidence_refs": [step.step_id for step in outcome.result.steps],
            "vitis_phase_count": sum(state in {"public_evaluation", "csynth", "public_cosim"} for state in states),
            "evidence_complete": bool(outcome.result.steps),
            "failure_attributable": False,
            "environment_excluded": False,
            "audit_summary": {"status": "accepted" if passed else "rejected", "failed_stage": None if passed else states[-1] if states else None, "validation": {state: "passed" for state in states}},
            "audit_identity": {
                "execution_id": self._authorization_id,
                "target_identity": self._context.task.target.to_dict(),
                "validation_records": outcome.result.to_dict(),
            },
            "audit_full_result": {"status": "accepted" if passed else "rejected", "succeeded": passed, "validation": outcome.result.to_dict()},
            "validation_result": outcome.result.to_dict(),
        }
        value["audit_summary"]["execution_identity"] = {"execution_id": self._authorization_id}
        self.last_result = value
        return value


class ExistingFormalR4Auditor:
    """Require the existing validator output and then run the evidence auditor."""

    def audit(self, validation: Mapping[str, Any]) -> dict[str, Any]:
        if validation.get("fresh_validation") is not True or (validation.get("full_prefix") is not True and validation.get("prefix_executed_to_terminal") is not True):
            return {"status": "contradiction", "has_errors": True, "failure_attributable": False, "environment_excluded": False, "reason": "fresh_full_prefix_missing"}
        try:
            return ProductEvidenceR4Auditor().audit(validation)
        except Exception as exc:
            return {"status": "contradiction", "has_errors": True, "failure_attributable": False, "environment_excluded": False, "reason": "evidence_auditor_error:" + type(exc).__name__}


@dataclass(frozen=True, slots=True)
class R4IntegrationConfig:
    canary: R4CanaryManifest
    execution_identity: Mapping[str, Any]
    memory_snapshot: MemorySnapshot
    reserve_plan: R4ReservePlan
    episode_root: str
    mutation_adapter: R4MutationAdapter
    kill_switch: R4KillSwitchState = R4KillSwitchState()

    def __post_init__(self) -> None:
        if not isinstance(self.canary, R4CanaryManifest) or not isinstance(self.reserve_plan, R4ReservePlan) or not isinstance(self.kill_switch, R4KillSwitchState) or not isinstance(self.memory_snapshot, MemorySnapshot):
            raise TypeError("invalid R4 integration contracts")
        if not callable(getattr(self.mutation_adapter, "mutate", None)):
            raise TypeError("R4 integration providers are invalid")
        if not isinstance(self.episode_root, str) or not self.episode_root.strip():
            raise ValueError("episode_root must be non-empty")


class ExistingOrchestratorR4Integration:
    """Consume R2 shadow evidence, run R3 Gate, and execute an R4 side lane."""

    def __init__(self, config: R4IntegrationConfig, *, gate: ApplicabilityGate | None = None, policy: RecoveryPolicy | None = None, auditor: Any | None = None) -> None:
        self._config = config
        self._gate = gate or ApplicabilityGate()
        self._policy = policy or RecoveryPolicy()
        self._auditor = auditor or ExistingFormalR4Auditor()

    def run_from_existing_orchestrator(self, *, context: Any, request: Any, main_result: Any, handler_factory: Any) -> Mapping[str, Any]:
        """Post-validation hook consumed by CandidateRepairValidationOrchestrator."""
        return self.run(context=context, request=request, main_result=main_result, handler_factory=handler_factory)

    def run(self, *, context: Any, request: Any, main_result: Any, handler_factory: Any) -> Mapping[str, Any]:
        if request.llm_advisory_mode != "candidate-only":
            raise ValueError("R4 extension requires candidate-only mode")
        events = list(main_result.metadata.get("diagnostic_events", ()))
        shadows = list(main_result.metadata.get("r2_shadow_diagnostics", ()))
        pairs = []
        for event in events:
            matches = [item for item in shadows if item.get("event_id") == event.get("event_id")]
            if len(matches) == 1:
                advisory = dict(matches[0].get("advisory", {}))
                if matches[0].get("input_status") == "eligible" and matches[0].get("critical_safety_violation") is False and matches[0].get("equivalence", {}).get("equivalent") is True and advisory.get("suspected_owner") == "candidate" and advisory.get("repair_scope") == "candidate_only" and advisory.get("confidence") == "high" and advisory.get("abstain_reason") is None:
                    pairs.append((event, matches[0], advisory))
        if len(pairs) != 1:
            return {"schema_version": 1, "status": "abstained", "reason": "eligible_r2_event_not_unique", "main_result_unchanged": True, "accepted_by_integration": False}
        event, shadow, advisory = pairs[0]
        advisory["advisory_id"] = _canonical({"event_id": event["event_id"], "request_sha256": shadow.get("request_sha256"), "advisory": advisory, "provider_identity": shadow.get("provider_identity", {})})
        revision = SnapshotRevisionSource(self._config.memory_snapshot).resolve(event=event, advisory=advisory)
        if not isinstance(revision, RepairPatternRevision):
            raise TypeError("snapshot revision source returned an invalid revision")
        gate_result = self._gate.evaluate(context=self._config.memory_snapshot.context_for(event=event, advisory=advisory), revision=revision, evidence_refs=tuple(advisory.get("evidence_refs", ())))
        testbench_hashes = {key: _text_sha(value) for key, value in request.suite_testbench_codes.items()}
        public_before = {suite.suite_id: testbench_hashes[suite.suite_id] for suite in context.task.test_suites if suite.split is EvaluationSplit.PUBLIC}
        hidden_before = {suite.suite_id: testbench_hashes[suite.suite_id] for suite in context.task.test_suites if suite.split is EvaluationSplit.HIDDEN}
        if gate_result.decision.value != "accept":
            episode = self._episode(event=event, advisory=advisory, gate=gate_result.to_dict(), revision=revision, authorization={"status": "not_created"}, request=request, public_before=public_before, hidden_before=hidden_before, outcome="abstained", reason="gate_" + gate_result.decision.value, result=None, validation=None, auditor={"status": "not_run"}, budget_delta={})
            path = episode.append_only_write(self._config.episode_root)
            return {"schema_version": 1, "status": "abstained", "reason": "gate_" + gate_result.decision.value, "episode_path": str(path), "episode_hash": episode.episode_hash, "main_result_unchanged": True, "accepted_by_integration": False}
        stage = RecoveryStage(event["stage"])
        policy_request = RecoveryRequest(action=RecoveryAction.REPAIR, role=RecoveryRole.CANDIDATE, stage=stage, evidence_view="agent_safe", owner_authority=RecoveryAuthority.LLM_ADVISORY, lineage_id=event["run_id"], physical_tool_launched=True, evidence_complete=True, advisory_mode="candidate-only")
        policy_decision = self._policy.decide(policy_request)
        if not policy_decision.allowed:
            return {"schema_version": 1, "status": "abstained", "reason": policy_decision.reason_code, "main_result_unchanged": True, "accepted_by_integration": False}
        self._config.reserve_plan.ensure_available(context.budget)
        canary_map = {**self._config.canary.to_dict(), "manifest_sha256": self._config.canary.manifest_sha256, "enabled": self._config.canary.enabled, "operator_enabled": self._config.canary.operator_enabled}
        auth = R4CandidateRepairAuthorization(run_id=event["run_id"], event_ref=event["event_id"], advisory_id=advisory["advisory_id"], gate_decision=gate_result.decision.value, pattern_lifecycle=revision.lifecycle.value, gate_contract_hash=gate_result.contract_hash, revision_sha256=revision.revision_hash, canary_manifest_sha256=self._config.canary.manifest_sha256, before_candidate_sha256=_text_sha(main_result.final_candidate), policy_decision_id=_canonical(policy_decision.to_dict()), budget_reservation_id=_canonical(self._config.reserve_plan.to_dict()), deterministic_terminal_ref=_canonical({"validation_id": main_result.validation_id, "state": main_result.last_validation_state.value, "status": main_result.status.value}))
        provenance = validate_r4_provenance(event=event, execution_identity=self._config.execution_identity, advisory=advisory, gate=gate_result.to_dict(), revision=revision.to_dict(), authorization=auth.to_dict(), canary=canary_map, candidate=main_result.final_candidate, original=request.original_code, testbench_hashes=testbench_hashes)
        if not provenance.valid:
            return {"schema_version": 1, "status": "invalid_evidence", "reasons": list(provenance.reasons), "main_result_unchanged": True, "accepted_by_integration": False}
        execution = R4ExecutionInput(authorization=auth, canary=self._config.canary, kill_switch=self._config.kill_switch, execution_identity=self._config.execution_identity, advisory=advisory, candidate=main_result.final_candidate, original=request.original_code, testbench_hashes=testbench_hashes, route_fingerprint=auth.deterministic_terminal_ref)
        validator = ExistingR4ValidationAdapter(context=context, request=request, handler_factory=handler_factory, authorization_id=auth.authorization_id, testbench_hashes=testbench_hashes)
        audit_result: dict[str, Any] = {"status": "not_run"}
        def audit(payload: Mapping[str, Any]) -> bool:
            nonlocal audit_result
            audit_result = self._auditor.audit(payload["validation"])
            return audit_result.get("status") == "clean" and audit_result.get("has_errors") is not True
        before_budget = context.budget.snapshot().to_dict()
        controller = R4CandidateRepairController(policy=self._policy, budget=context.budget, reserve_plan=self._config.reserve_plan)
        result = controller.run(execution, mutate_candidate=lambda candidate: self._config.mutation_adapter.mutate(candidate=candidate, event=event, advisory=advisory, task=context.task), validate_candidate=validator.validate, audit=audit)
        after_budget = context.budget.snapshot().to_dict()
        budget_delta = {key: after_budget[key] - before_budget[key] for key in ("llm_calls", "tool_calls", "compile_calls", "csim_calls", "csynth_calls", "cosim_calls", "tokens", "cost_usd", "elapsed_s")}
        validation = validator.last_result
        episode = self._episode(event=event, advisory=advisory, gate=gate_result.to_dict(), revision=revision, authorization=auth.to_dict(), request=request, public_before=public_before, hidden_before=hidden_before, outcome=result.outcome.value, reason=";".join(result.reasons), result=result, validation=validation, auditor=audit_result, budget_delta=budget_delta)
        path = episode.append_only_write(self._config.episode_root)
        return {"schema_version": 1, "status": result.outcome.value, "r4_result": result.to_dict(), "provenance": {"valid": provenance.valid, "checked_refs": list(provenance.checked_refs)}, "gate": gate_result.to_dict(), "episode_path": str(path), "episode_hash": episode.episode_hash, "main_result_unchanged": True, "accepted_by_integration": False}

    def _episode(self, *, event: Mapping[str, Any], advisory: Mapping[str, Any], gate: Mapping[str, Any], revision: RepairPatternRevision, authorization: Mapping[str, Any], request: Any, public_before: Mapping[str, str], hidden_before: Mapping[str, str], outcome: str, reason: str, result: Any, validation: Mapping[str, Any] | None, auditor: Mapping[str, Any], budget_delta: Mapping[str, Any]) -> R4RepairEpisode:
        after_hash = None if result is None else result.after_candidate_sha256
        return R4RepairEpisode(episode_id="r4-" + _canonical({"event": event["event_id"], "authorization": authorization})[:32], created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), event_ref=event["event_id"], execution_identity=self._config.execution_identity, request_identity={"validation_id": event["validation_id"], "run_id": event["run_id"]}, advisory_identity=advisory, gate_identity={**dict(gate), "revision_hash": revision.revision_hash}, authorization_identity=authorization, candidate_before_sha256=_text_sha(request.initial_candidate), candidate_after_sha256=after_hash, public_testbench_before=public_before, public_testbench_after=public_before, hidden_testbench_before=hidden_before, hidden_testbench_after=hidden_before, formal_validation_id=None if result is None else result.formal_validation_id, validation_evidence_refs=tuple(() if validation is None else validation.get("validation_evidence_refs", ())), auditor_result=auditor, budget_delta=budget_delta, provider_call_count=0 if result is None else result.provider_call_count, vitis_phase_count=0 if validation is None else int(validation.get("vitis_phase_count", 0)), outcome=outcome, outcome_reason=reason)


__all__ = ["CandidateModelR4MutationAdapter", "ExistingFormalR4Auditor", "ExistingOrchestratorR4Integration", "ExistingR4ValidationAdapter", "ProductEvidenceR4Auditor", "R4IntegrationConfig", "R4MutationAdapter", "R4PromptFactory"]
