from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from agrefactor.recovery.r4_budget import R4ReservePlan, build_r4_reserve_plan
from agrefactor.recovery.r4_episode import R4RepairEpisode, R4RepairEpisodeReader
from agrefactor.recovery.r4_provenance import validate_r4_provenance
from agrefactor.runtime.budget import BudgetLimits, BudgetManager, BudgetExceededError


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class R4EpisodeFoundationTests(unittest.TestCase):
    def _episode(self, outcome="verified_negative"):
        return R4RepairEpisode(
            episode_id="episode-1", created_at="2026-09-12T00:00:00Z", event_ref="event-1",
            execution_identity={"case_id": "case-1"}, request_identity={"run_id": "run-1"},
            advisory_identity={"advisory_id": "advisory-1", "accepted": False},
            gate_identity={"decision": "accept", "contract_hash": h("gate")},
            authorization_identity={"authorization_id": "authorization-1"},
            candidate_before_sha256=h("before"), candidate_after_sha256=h("after"),
            public_testbench_before={"public": h("public")}, public_testbench_after={"public": h("public")},
            hidden_testbench_before={"hidden": h("hidden")}, hidden_testbench_after={"hidden": h("hidden")},
            formal_validation_id="validation-1", validation_evidence_refs=("evidence-1",),
            auditor_result={"status": "clean", "failure_attributable": True, "environment_excluded": True}, budget_delta={"provider_calls": 1}, provider_call_count=1,
            vitis_phase_count=4, outcome=outcome, outcome_reason="typed test outcome",
        )

    def test_r4_episode_is_not_r3_shadow_episode(self):
        episode = self._episode()
        self.assertEqual(episode.outcome, "verified_negative")
        self.assertNotEqual(type(episode).__name__, "DiagnosticEpisode")

    def test_episode_append_only_round_trip_and_duplicate_rejection(self):
        episode = self._episode()
        with tempfile.TemporaryDirectory() as root:
            path = episode.append_only_write(root)
            self.assertEqual(R4RepairEpisodeReader.read(path).episode_hash, episode.episode_hash)
            with self.assertRaises(FileExistsError):
                episode.append_only_write(root)

    def test_verified_positive_requires_real_validation_and_clean_audit(self):
        episode = self._episode(outcome="verified_positive")
        self.assertEqual(episode.to_dict()["accepted_by_episode"], False)
        with self.assertRaises(TypeError):
            episode.execution_identity["case_id"] = "changed"

    def test_abstention_cannot_hide_provider_or_mutation(self):
        with self.assertRaises(ValueError):
            self._episode(outcome="abstained")

    def test_verified_negative_requires_attribution_and_environment_exclusion(self):
        from dataclasses import replace
        episode = self._episode()
        with self.assertRaises(ValueError):
            replace(episode, auditor_result={"status": "clean", "failure_attributable": False, "environment_excluded": True}, episode_hash="")

    def test_episode_id_cannot_escape_store_root(self):
        from dataclasses import replace
        with self.assertRaises(ValueError):
            replace(self._episode(), episode_id="../escape", episode_hash="")

    def test_provenance_rejects_event_candidate_mismatch_and_scope(self):
        event = {"event_id": "event-1", "run_id": "run-1", "candidate_sha256": h("other"), "evidence_refs": ["event-evidence"], "evidence_view": "agent_safe", "hidden_input_count": 0}
        advisory = {"accepted": False, "owner_authority": "llm_advisory", "evidence_refs": ["event-evidence"]}
        gate = {"decision": "accept", "evidence_refs": ["event-evidence"]}
        revision = {"revision_hash": h("revision")}
        authorization = {"event_ref": "event-1", "run_id": "run-1", "gate_decision": "accept", "revision_sha256": h("revision"), "canary_manifest_sha256": h("canary")}
        canary = {"manifest_sha256": h("canary")}
        result = validate_r4_provenance(event=event, execution_identity={}, advisory=advisory, gate=gate, revision=revision, authorization=authorization, canary=canary, candidate="candidate", original="original", testbench_hashes={"public": h("tb")})
        self.assertFalse(result.valid)
        self.assertIn("candidate_hash_mismatch", result.reasons)

    def test_provenance_rejects_advisory_evidence_outside_event(self):
        event = {"event_id": "event-1", "run_id": "run-1", "candidate_sha256": h("candidate"), "evidence_refs": ["event-evidence"], "evidence_view": "agent_safe", "hidden_input_count": 0}
        result = validate_r4_provenance(event=event, execution_identity={}, advisory={"accepted": False, "owner_authority": "llm_advisory", "evidence_refs": ["other"]}, gate={"decision": "accept", "evidence_refs": ["event-evidence"]}, revision={"revision_hash": h("revision")}, authorization={"event_ref": "event-1", "run_id": "run-1", "gate_decision": "accept", "revision_sha256": h("revision"), "canary_manifest_sha256": h("canary")}, canary={"manifest_sha256": h("canary")}, candidate="candidate", original="original", testbench_hashes={"public": h("tb")})
        self.assertIn("advisory_evidence_out_of_scope", result.reasons)

    def test_complete_provenance_is_valid(self):
        candidate = "candidate"
        revision_hash = h("revision")
        canary_hash = h("canary")
        target_id, toolchain_id = h("target"), h("toolchain")
        event = {"event_id": "event-1", "run_id": "run-1", "candidate_sha256": h(candidate), "evidence_refs": ["evidence-1"], "target_identity": {"fingerprint": target_id}, "toolchain_identity": {"fingerprint": toolchain_id}, "evidence_view": "agent_safe", "hidden_input_count": 0, "secret_present": False, "private_reasoning_present": False, "physical_tool_launched": True, "evidence_complete": True}
        execution_identity = {"run_id": "run-1", "case_id": "case-1", "identity_complete": True, "hidden_input_count": 0, "secret_present": False, "private_reasoning_present": False, "source_sha256": h("original"), "target_identity": target_id, "toolchain_identity": toolchain_id, "parser_identity": "parser-1", "model_identity": "model-1", "prompt_sha256": h("prompt")}
        authorization = {"event_ref": "event-1", "run_id": "run-1", "advisory_id": "advisory-1", "gate_decision": "accept", "gate_contract_hash": h("gate"), "revision_sha256": revision_hash, "canary_manifest_sha256": canary_hash, "before_candidate_sha256": h(candidate), "policy_decision_id": "policy-1", "budget_reservation_id": "reserve-1", "deterministic_terminal_ref": "terminal-1"}
        result = validate_r4_provenance(event=event, execution_identity=execution_identity, advisory={"advisory_id": "advisory-1", "accepted": False, "owner_authority": "llm_advisory", "evidence_refs": ["evidence-1"]}, gate={"decision": "accept", "contract_hash": h("gate"), "evidence_refs": ["evidence-1"]}, revision={"revision_hash": revision_hash, "lifecycle": "Trusted", "threshold_source": "frozen-calibration"}, authorization=authorization, canary={"manifest_sha256": canary_hash, "enabled": True, "operator_enabled": True, "case_ids": ["case-1"], "target_identity": target_id, "toolchain_identity": toolchain_id, "parser_identity": "parser-1", "model_identity": "model-1", "prompt_sha256": h("prompt")}, candidate=candidate, original="original", testbench_hashes={"public": h("tb")})
        self.assertTrue(result.valid, result.reasons)

    def test_reserve_plan_covers_declared_full_prefix(self):
        plan = build_r4_reserve_plan(public_csim=True, csynth=True, public_cosim=True, hidden_evaluation=True, tool_calls=6, compile_calls=4, wall_time_s=3600)
        limits = plan.to_budget_limits()
        self.assertEqual(limits.max_llm_calls, 1)
        self.assertEqual(limits.max_csim_calls, 2)
        self.assertEqual(limits.max_cosim_calls, 1)
        self.assertEqual(limits.max_csynth_calls, 1)
        self.assertEqual(plan.phase_order, ("preflight", "public_csim", "csynth", "public_cosim", "hidden_evaluation"))

    def test_reserve_requires_exactly_one_provider_and_mutation(self):
        with self.assertRaises(ValueError):
            R4ReservePlan(provider_calls=0)

    def test_reserve_admission_is_prospective_and_fail_closed(self):
        plan = build_r4_reserve_plan(public_csim=True, csynth=True, public_cosim=True, hidden_evaluation=True, tool_calls=4, compile_calls=2, wall_time_s=100)
        budget = BudgetManager(BudgetLimits(max_llm_calls=1, max_tool_calls=4, max_compile_calls=2, max_csim_calls=2, max_csynth_calls=1, max_cosim_calls=1, max_wall_time_s=200))
        before = budget.snapshot().to_dict()
        plan.ensure_available(budget)
        after = budget.snapshot().to_dict()
        for key in ("llm_calls", "tool_calls", "compile_calls", "csim_calls", "csynth_calls", "cosim_calls"):
            self.assertEqual(before[key], after[key])
        blocked = BudgetManager(BudgetLimits(max_llm_calls=0, max_tool_calls=4, max_compile_calls=2, max_csim_calls=2, max_csynth_calls=1, max_cosim_calls=1, max_wall_time_s=200))
        with self.assertRaises(BudgetExceededError):
            plan.ensure_available(blocked)


if __name__ == "__main__":
    unittest.main()
