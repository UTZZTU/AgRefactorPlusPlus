from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.models import ChatMessage
from agrefactor.prompts import LayeredPrompt
from agrefactor.recovery.gated_candidate_repair import R4CanaryManifest, R4KillSwitchState, R4MutationFailure
from agrefactor.recovery.memory_gate import GateDecision, GateResult, DiagnosticEpisode, EpisodeOutcome, PatternLifecycle, RepairPatternRevision
from agrefactor.recovery.r3_memory_snapshot import MemorySnapshot
from agrefactor.recovery.r4_episode import R4RepairEpisodeReader
from agrefactor.recovery.shadow_advisor import CalibrationCertificate
from agrefactor.runtime.budget import BudgetLimits
from agrefactor.runtime.r4_integration import CandidateModelR4MutationAdapter, ExistingOrchestratorR4Integration, R4IntegrationConfig


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_h(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


PROVIDER_IDENTITY = {
    "provider": "test-provider",
    "model_name": "test-model",
    "model": "test-model-id",
}


def calibration_certificate() -> CalibrationCertificate:
    return CalibrationCertificate(
        split_id="r2-calibration-test",
        split_sha256=h("split"),
        report_sha256=h("report"),
        policy_sha256=h("policy"),
        provider_identity_sha256=canonical_h(PROVIDER_IDENTITY),
        prompt_contract_version="r2-shadow-output-v2",
        strict_parser="r2-v1",
        input_contract_version="r2-agent-safe-diagnostic-evidence-v2",
        eligible_confidence_labels=("high",),
        accepted=True,
        reasons=(),
    )


def helpers():
    path = Path(__file__).with_name("test_candidate_repair_integration.py")
    spec = importlib.util.spec_from_file_location("_r4_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StaticAdvisor:
    def __init__(self): self.calls = 0
    @property
    def identity(self): return dict(PROVIDER_IDENTITY)
    def diagnose(self, request):
        from agrefactor.recovery import AdvisoryConfidence, AdvisoryOwner, AdvisoryRepairScope, DiagnosticAdvisory
        self.calls += 1
        return DiagnosticAdvisory(suspected_owner=AdvisoryOwner.CANDIDATE, suspected_failure_class="unknown_candidate_failure", evidence_refs=(request.evidence_ids[0],), repair_scope=AdvisoryRepairScope.CANDIDATE_ONLY, confidence=AdvisoryConfidence.HIGH, metadata={"strict_parser": "r2-v1", "prompt_contract_version": "r2-shadow-output-v2"})


class Mutation:
    def __init__(self, replacement): self.replacement, self.calls = replacement, 0
    def mutate(self, **kwargs): self.calls += 1; return self.replacement


class FailingMutation:
    def __init__(self): self.calls = 0
    def mutate(self, **kwargs):
        self.calls += 1
        raise R4MutationFailure(
            "pre_provider_mutation_contract_failure",
            provider_call_observed=False,
        )


class InvalidPromptFactory:
    def build(self, **kwargs):
        return LayeredPrompt(
            messages=(
                ChatMessage(role="system", content="system"),
                ChatMessage(role="user", content="user"),
            ),
            manifest={"editable_artifacts": ["candidate"]},
        )


class HookProbe:
    def __init__(self): self.calls = 0
    def run_from_existing_orchestrator(self, **kwargs):
        self.calls += 1
        return {"schema_version": 1, "status": "abstained", "reason": "probe", "main_result_unchanged": True, "accepted_by_integration": False}


class ContradictingAuditor:
    def audit(self, validation):
        return {"status": "contradiction", "has_errors": True, "failure_attributable": False, "environment_excluded": False}


class AttributingAuditor:
    def audit(self, validation):
        return {"status": "clean", "has_errors": False, "failure_attributable": True, "environment_excluded": True}


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 0, 0, 0, tzinfo=tz or timezone.utc)


class R4ExistingOrchestratorIntegrationTests(unittest.TestCase):
    def _scenario(self, m):
        def scenario(request, state):
            if request.attempt == 0 and state is m.ValidationState.CSYNTH:
                return m.report_for(state, item=m.feedback_item("unknown.failure", state=state, owner=m.FeedbackOwner.UNKNOWN, category=m.FeedbackCategory.UNKNOWN), report_id="unknown-report")
            return m.pass_scenario(request, state)
        return scenario

    def _revision(self, event, advisory, *, certificate_id):
        return RepairPatternRevision(revision_id="revision-1", parent_revision_id=None, supported_when={"stage": "csynth", "owner": "candidate"}, avoid_when={}, exclusions={}, required_evidence=(), positive_episode_refs=("positive-1",), negative_episode_refs=(), calibration_refs=(certificate_id,), lifecycle=PatternLifecycle.TRUSTED, threshold_source="frozen-calibration")

    def _snapshot(self, revision, *, calibrated=True):
        episode = DiagnosticEpisode(
            episode_id="episode-1", created_at="2026-09-12T00:00:00Z", parent_episode_id=None,
            lineage=("episode-1",), event_ref="event-1", execution_identity={"run_id": "run-1"},
            request={"stage": "csynth"}, context_signature=h("context"),
            deterministic_diagnosis={"owner": "candidate"}, advisory={"accepted": False},
            retrieved_revision_ids=("revision-1",),
            gate=GateResult(GateDecision.ABSTAIN, ("shadow_only",), ("event-evidence",), ("identity_hard_reject",), h("gate")),
            repair_authorization="not_requested", before_hash=h("before"), after_hash=None,
            full_revalidation_ref=None, budget_delta={"provider_calls": 0}, outcome=EpisodeOutcome.ABSTAINED,
            outcome_refs=("event-1",),
        )
        return MemorySnapshot.freeze(
            episodes=(episode,), revisions=(revision,),
            gate_context={"identity_complete": True, "hidden_input_count": 0, "secret_present": False, "calibrated_risk_ok": calibrated},
            frozen_at="2026-09-12T00:00:00Z",
        )

    def _run_main(self, m, *, shadow=True, advisory_mode="candidate-only"):
        adapter, _ = m.make_adapter([m.P1])
        request = m.make_request(max_attempts=1)
        request = __import__("dataclasses").replace(request, llm_advisory_mode=advisory_mode)
        factory = m.ScenarioFactory(self._scenario(m))
        selected = StaticAdvisor() if shadow else None
        result = m.CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=factory, shadow_advisor=selected).run(m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request, validation_id="r4-main")
        return result, request, factory, selected

    def _integration(self, m, request, mutation, root, event, *, calibrated=True, kill=False, auditor=None, snapshot=None, include_certificate=True):
        target = event["target_identity"]["fingerprint"]
        toolchain = event["toolchain_identity"]["fingerprint"]
        identity = {"run_id": event["run_id"], "case_id": "case-1", "stage": "csynth", "identity_complete": True, "hidden_input_count": 0, "secret_present": False, "private_reasoning_present": False, "source_sha256": h(request.original_code), "target_identity": target, "toolchain_identity": toolchain, "parser_identity": "parser-1", "model_identity": "model-1", "prompt_sha256": h("prompt")}
        canary = R4CanaryManifest(manifest_id="canary-1", manifest_sha256=h("canary"), enabled=True, operator_enabled=True, case_ids=("case-1",), source_sha256=h(request.original_code), target_identity=target, toolchain_identity=toolchain, parser_identity="parser-1", model_identity="model-1", prompt_sha256=h("prompt"), allowed_stage="csynth", expires_at="2099-01-01T00:00:00Z")
        certificate = calibration_certificate()
        revision = self._revision(
            event, {}, certificate_id=certificate.certificate_id
        )
        selected_snapshot = snapshot or self._snapshot(revision, calibrated=calibrated)
        config = R4IntegrationConfig(canary=canary, execution_identity=identity, memory_snapshot=selected_snapshot, episode_root=root, mutation_adapter=mutation, calibration_certificate=certificate if include_certificate else None, validation_wall_time_s=100, kill_switch=R4KillSwitchState(active=kill, trigger="test" if kill else None))
        return ExistingOrchestratorR4Integration(config, auditor=auditor)

    def test_default_orchestrator_does_not_invoke_r4(self):
        m = helpers()
        result, _, _, shadow = self._run_main(m, shadow=False, advisory_mode="off")
        self.assertNotIn("r4_integration", result.metadata)
        self.assertIsNone(shadow)

    def test_real_candidate_adapter_rejects_bad_prompt_before_provider(self):
        m = helpers()
        adapter, provider = m.make_adapter([m.P1])
        context = m.make_context(
            limits=BudgetLimits(
                max_llm_calls=4,
                max_tool_calls=20,
                max_compile_calls=20,
                max_csim_calls=10,
                max_csynth_calls=10,
                max_cosim_calls=10,
                max_wall_time_s=5000,
            )
        )
        mutation = CandidateModelR4MutationAdapter(
            model_adapter=adapter,
            prompt_factory=InvalidPromptFactory(),
            budget=context.budget,
        )
        with self.assertRaises(R4MutationFailure) as raised:
            mutation.mutate(
                candidate=m.P1,
                event={},
                advisory={},
                task=context.task,
            )
        self.assertEqual(
            raised.exception.reason,
            "pre_provider_mutation_contract_failure",
        )
        self.assertFalse(raised.exception.provider_call_observed)
        self.assertEqual(provider.calls, [])
        self.assertEqual(context.budget.snapshot().llm_calls, 0)

    def test_feature_off_full_serialized_result_is_equivalent(self):
        m = helpers()
        request = replace(m.make_request(max_attempts=1), llm_advisory_mode="off")
        adapter1, provider1 = m.make_adapter([m.P1])
        adapter2, provider2 = m.make_adapter([m.P1])
        factory1 = m.ScenarioFactory(self._scenario(m))
        factory2 = m.ScenarioFactory(self._scenario(m))
        task = m.make_task()
        context1 = m.RunContext(run_id="feature-off", task=task, budget=m.BudgetManager(m.BudgetLimits(), clock=lambda: 0.0), trace=m.TraceRecorder("feature-off", task_id=task.task_id))
        context2 = m.RunContext(run_id="feature-off", task=task, budget=m.BudgetManager(m.BudgetLimits(), clock=lambda: 0.0), trace=m.TraceRecorder("feature-off", task_id=task.task_id))
        with patch("agrefactor.evidence.diagnostic_event.datetime", FixedDateTime):
            implicit = m.CandidateRepairValidationOrchestrator(model_adapter=adapter1, handler_factory=factory1).run(context1, request, validation_id="feature-off")
            explicit = m.CandidateRepairValidationOrchestrator(model_adapter=adapter2, handler_factory=factory2, r4_integration=None).run(context2, request, validation_id="feature-off")
        self.assertEqual(implicit.to_dict(), explicit.to_dict())
        self.assertEqual(provider1.calls, provider2.calls)
        self.assertEqual(context1.budget.snapshot().to_dict(), context2.budget.snapshot().to_dict())

    def test_existing_orchestrator_invokes_explicit_hook_only_in_candidate_mode(self):
        m = helpers()
        adapter, _ = m.make_adapter([m.P1])
        probe = HookProbe()
        request = __import__("dataclasses").replace(m.make_request(max_attempts=1), llm_advisory_mode="candidate-only")
        result = m.CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=m.ScenarioFactory(self._scenario(m)), shadow_advisor=StaticAdvisor(), r4_integration=probe).run(m.make_context(), request, validation_id="r4-hook")
        self.assertEqual(probe.calls, 1)
        self.assertTrue(result.metadata["r4_integration_enabled"])
        self.assertEqual(result.metadata["r4_integration"]["status"], "abstained")
        adapter2, _ = m.make_adapter([m.P1])
        probe2 = HookProbe()
        result2 = m.CandidateRepairValidationOrchestrator(model_adapter=adapter2, handler_factory=m.ScenarioFactory(self._scenario(m)), r4_integration=probe2).run(m.make_context(), m.make_request(max_attempts=1), validation_id="r4-hook-off")
        self.assertEqual(probe2.calls, 0)
        self.assertNotIn("r4_integration", result2.metadata)

    def test_r2_gate_r4_existing_validator_end_to_end(self):
        m = helpers()
        result, request, factory, shadow = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event)
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            self.assertEqual(outcome["status"], "verified_positive")
            self.assertTrue(Path(outcome["episode_path"]).is_file())
            self.assertEqual(mutation.calls, 1)
            self.assertEqual(shadow.calls, 1)
            self.assertTrue(outcome["main_result_unchanged"])

    def test_missing_calibration_certificate_blocks_before_mutation(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(
                m,
                request,
                mutation,
                root,
                event,
                include_certificate=False,
            )
            outcome = integration.run_from_existing_orchestrator(
                context=m.make_context(),
                request=request,
                main_result=result,
                handler_factory=m.ScenarioFactory(m.pass_scenario),
            )
            self.assertEqual(outcome["status"], "abstained")
            self.assertEqual(outcome["reason"], "r2_calibration_unverified")
            self.assertEqual(
                outcome["calibration"]["reasons"], ["certificate_missing"]
            )
            self.assertEqual(mutation.calls, 0)

    def test_revision_must_reference_active_calibration_certificate(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        revision = RepairPatternRevision(
            revision_id="revision-wrong-calibration",
            parent_revision_id=None,
            supported_when={"stage": "csynth", "owner": "candidate"},
            avoid_when={},
            exclusions={},
            required_evidence=(),
            positive_episode_refs=("positive-1",),
            negative_episode_refs=(),
            calibration_refs=("different-calibration-certificate",),
            lifecycle=PatternLifecycle.TRUSTED,
            threshold_source="frozen-calibration",
        )
        snapshot = self._snapshot(revision)
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(
                m, request, mutation, root, event, snapshot=snapshot
            )
            outcome = integration.run_from_existing_orchestrator(
                context=m.make_context(),
                request=request,
                main_result=result,
                handler_factory=m.ScenarioFactory(m.pass_scenario),
            )
            self.assertEqual(outcome["status"], "invalid_evidence")
            self.assertEqual(
                outcome["reason"],
                "revision_calibration_certificate_mismatch",
            )
            self.assertEqual(mutation.calls, 0)

    def test_provenance_mismatch_blocks_before_mutation(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event)
            object.__setattr__(integration._config, "execution_identity", {**integration._config.execution_identity, "source_sha256": h("wrong")})
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            self.assertEqual(outcome["status"], "invalid_evidence")
            self.assertEqual(mutation.calls, 0)

    def test_pre_provider_mutation_failure_is_not_counted_as_provider_call(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = FailingMutation()
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event)
            outcome = integration.run_from_existing_orchestrator(
                context=m.make_context(
                    limits=BudgetLimits(
                        max_llm_calls=4,
                        max_tool_calls=20,
                        max_compile_calls=20,
                        max_csim_calls=10,
                        max_csynth_calls=10,
                        max_cosim_calls=10,
                        max_wall_time_s=5000,
                    )
                ),
                request=request,
                main_result=result,
                handler_factory=m.ScenarioFactory(m.pass_scenario),
            )
            self.assertEqual(outcome["status"], "inconclusive")
            episode = R4RepairEpisodeReader.read(outcome["episode_path"])
            self.assertEqual(episode.outcome_reason, "pre_provider_mutation_contract_failure")
            self.assertEqual(episode.provider_call_count, 0)
            self.assertEqual(episode.budget_actual["provider_calls"], 0)
            self.assertEqual(episode.budget_delta["llm_calls"], 0)
            self.assertEqual(mutation.calls, 1)

    def test_gate_reject_writes_abstention_without_mutation(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event, calibrated=False)
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            self.assertEqual(outcome["status"], "abstained")
            self.assertEqual(mutation.calls, 0)

    def test_budget_admission_failure_is_recorded_before_provider(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event)
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=0, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            self.assertEqual(outcome["status"], "inconclusive")
            self.assertEqual(outcome["reason"], "policy_ledger_or_budget_denied")
            self.assertEqual(mutation.calls, 0)
            self.assertEqual(outcome["budget_actual"]["provider_calls"], 0)
            self.assertEqual(outcome["budget_actual"]["mutation_calls"], 0)
            self.assertTrue(Path(outcome["episode_path"]).is_file())

    def test_kill_switch_blocks_integration_before_provider(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event, kill=True)
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            self.assertEqual(outcome["status"], "abstained")
            self.assertEqual(mutation.calls, 0)

    def test_auditor_contradiction_is_inconclusive_episode(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event, auditor=ContradictingAuditor())
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            self.assertEqual(outcome["status"], "inconclusive")
            episode = R4RepairEpisodeReader.read(outcome["episode_path"])
            self.assertEqual(episode.outcome, "inconclusive")

    def test_attributed_candidate_failure_writes_verified_negative(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        def failing_scenario(plan, state):
            if state is m.ValidationState.CSYNTH:
                return m.report_for(state, item=m.feedback_item("candidate.failure", state=state, owner=m.FeedbackOwner.CANDIDATE, category=m.FeedbackCategory.TIMING_VIOLATION), report_id="candidate-failure")
            return m.pass_scenario(plan, state)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event, auditor=AttributingAuditor())
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=result, handler_factory=m.ScenarioFactory(failing_scenario))
            self.assertEqual(outcome["status"], "verified_negative")
            episode = R4RepairEpisodeReader.read(outcome["episode_path"])
            self.assertEqual(episode.outcome, "verified_negative")
            self.assertTrue(episode.auditor_result["failure_attributable"])

    def test_missing_snapshot_revision_fails_closed_at_hook_boundary(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        empty_snapshot = MemorySnapshot.freeze(episodes=(), revisions=(), gate_context={"identity_complete": True, "hidden_input_count": 0, "secret_present": False, "calibrated_risk_ok": True}, frozen_at="2026-09-12T00:00:00Z")
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event, snapshot=empty_snapshot)
            adapter, _ = m.make_adapter([m.P1])
            hooked = m.CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=m.ScenarioFactory(self._scenario(m)), shadow_advisor=StaticAdvisor(), r4_integration=integration).run(m.make_context(), replace(request, llm_advisory_mode="candidate-only"), validation_id="snapshot-missing")
            self.assertEqual(hooked.metadata["r4_integration"]["status"], "inconclusive")
            self.assertTrue(hooked.metadata["r4_integration"]["main_result_unchanged"])
            self.assertEqual(mutation.calls, 0)

    def test_episode_write_failure_is_contained_by_existing_orchestrator(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        event = result.metadata["diagnostic_events"][0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            blocked_root = Path(root) / "not-a-directory"
            blocked_root.write_text("occupied", encoding="utf-8")
            integration = self._integration(m, request, mutation, str(blocked_root), event)
            adapter, _ = m.make_adapter([m.P1])
            hooked = m.CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=m.ScenarioFactory(self._scenario(m)), shadow_advisor=StaticAdvisor(), r4_integration=integration).run(m.make_context(), replace(request, llm_advisory_mode="candidate-only"), validation_id="episode-write-failure")
            self.assertEqual(hooked.metadata["r4_integration"]["status"], "inconclusive")
            self.assertTrue(hooked.metadata["r4_integration"]["main_result_unchanged"])

    def test_episode_before_hash_uses_actual_main_candidate(self):
        m = helpers()
        result, request, _, _ = self._run_main(m)
        final_candidate = m.P2
        metadata = dict(result.metadata)
        events = [dict(item) for item in metadata["diagnostic_events"]]
        events[0]["candidate_sha256"] = h(final_candidate)
        metadata["diagnostic_events"] = events
        changed_result = replace(result, final_candidate=final_candidate, metadata=metadata)
        event = events[0]
        mutation = Mutation(m.P1)
        with tempfile.TemporaryDirectory() as root:
            integration = self._integration(m, request, mutation, root, event)
            outcome = integration.run_from_existing_orchestrator(context=m.make_context(limits=BudgetLimits(max_llm_calls=4, max_tool_calls=20, max_compile_calls=20, max_csim_calls=10, max_csynth_calls=10, max_cosim_calls=10, max_wall_time_s=5000)), request=request, main_result=changed_result, handler_factory=m.ScenarioFactory(m.pass_scenario))
            episode = R4RepairEpisodeReader.read(outcome["episode_path"])
            self.assertEqual(episode.candidate_before_sha256, h(final_candidate))
            self.assertNotEqual(episode.candidate_before_sha256, h(request.initial_candidate))


if __name__ == "__main__": unittest.main()
