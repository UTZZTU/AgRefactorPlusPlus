from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from agrefactor.recovery.gated_candidate_repair import R4CanaryManifest, R4KillSwitchState
from agrefactor.recovery.memory_gate import GateDecision, GateResult, DiagnosticEpisode, EpisodeOutcome, PatternLifecycle, RepairPatternRevision
from agrefactor.recovery.r4_budget import build_r4_reserve_plan
from agrefactor.recovery.r3_memory_snapshot import MemorySnapshot
from agrefactor.runtime.budget import BudgetLimits
from agrefactor.runtime.r4_integration import ExistingOrchestratorR4Integration, R4IntegrationConfig


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def helpers():
    path = Path(__file__).with_name("test_candidate_repair_integration.py")
    spec = importlib.util.spec_from_file_location("_r4_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StaticAdvisor:
    def __init__(self): self.calls = 0
    def diagnose(self, request):
        from agrefactor.recovery import AdvisoryConfidence, AdvisoryOwner, AdvisoryRepairScope, DiagnosticAdvisory
        self.calls += 1
        return DiagnosticAdvisory(suspected_owner=AdvisoryOwner.CANDIDATE, suspected_failure_class="unknown_candidate_failure", evidence_refs=(request.evidence_ids[0],), repair_scope=AdvisoryRepairScope.CANDIDATE_ONLY, confidence=AdvisoryConfidence.HIGH)


class Mutation:
    def __init__(self, replacement): self.replacement, self.calls = replacement, 0
    def mutate(self, **kwargs): self.calls += 1; return self.replacement


class HookProbe:
    def __init__(self): self.calls = 0
    def run_from_existing_orchestrator(self, **kwargs):
        self.calls += 1
        return {"schema_version": 1, "status": "abstained", "reason": "probe", "main_result_unchanged": True, "accepted_by_integration": False}


class R4ExistingOrchestratorIntegrationTests(unittest.TestCase):
    def _scenario(self, m):
        def scenario(request, state):
            if request.attempt == 0 and state is m.ValidationState.CSYNTH:
                return m.report_for(state, item=m.feedback_item("unknown.failure", state=state, owner=m.FeedbackOwner.UNKNOWN, category=m.FeedbackCategory.UNKNOWN), report_id="unknown-report")
            return m.pass_scenario(request, state)
        return scenario

    def _revision(self, event, advisory):
        return RepairPatternRevision(revision_id="revision-1", parent_revision_id=None, supported_when={"stage": "csynth", "owner": "candidate"}, avoid_when={}, exclusions={}, required_evidence=(), positive_episode_refs=("positive-1",), negative_episode_refs=(), calibration_refs=("calibration-1",), lifecycle=PatternLifecycle.TRUSTED, threshold_source="frozen-calibration")

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

    def _integration(self, m, request, mutation, root, event, *, calibrated=True):
        target = event["target_identity"]["fingerprint"]
        toolchain = event["toolchain_identity"]["fingerprint"]
        identity = {"run_id": event["run_id"], "case_id": "case-1", "stage": "csynth", "identity_complete": True, "hidden_input_count": 0, "secret_present": False, "private_reasoning_present": False, "source_sha256": h(request.original_code), "target_identity": target, "toolchain_identity": toolchain, "parser_identity": "parser-1", "model_identity": "model-1", "prompt_sha256": h("prompt")}
        canary = R4CanaryManifest(manifest_id="canary-1", manifest_sha256=h("canary"), enabled=True, operator_enabled=True, case_ids=("case-1",), source_sha256=h(request.original_code), target_identity=target, toolchain_identity=toolchain, parser_identity="parser-1", model_identity="model-1", prompt_sha256=h("prompt"), allowed_stage="csynth", expires_at="2099-01-01T00:00:00Z")
        plan = build_r4_reserve_plan(public_csim=True, csynth=True, public_cosim=True, hidden_evaluation=True, tool_calls=3, compile_calls=2, wall_time_s=100)
        revision = self._revision(event, {})
        snapshot = self._snapshot(revision, calibrated=calibrated)
        config = R4IntegrationConfig(canary=canary, execution_identity=identity, memory_snapshot=snapshot, reserve_plan=plan, episode_root=root, mutation_adapter=mutation, kill_switch=R4KillSwitchState())
        return ExistingOrchestratorR4Integration(config)

    def test_default_orchestrator_does_not_invoke_r4(self):
        m = helpers()
        result, _, _, shadow = self._run_main(m, shadow=False, advisory_mode="off")
        self.assertNotIn("r4_integration", result.metadata)
        self.assertIsNone(shadow)

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


if __name__ == "__main__": unittest.main()
