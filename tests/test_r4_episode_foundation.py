from __future__ import annotations

from dataclasses import replace
import hashlib
import tempfile
import unittest

from agrefactor.recovery.r4_budget import (
    R4ReservePlan,
    build_r4_budget_actual,
    build_r4_reserve_plan,
    build_r4_reserve_plan_from_handlers,
    record_r4_budget_reservation,
)
from agrefactor.recovery.r4_episode import R4RepairEpisode, R4RepairEpisodeReader
from agrefactor.recovery.r4_provenance import (
    canonical_artifact_sha256,
    validate_r4_provenance,
)
from agrefactor.runtime.budget import BudgetExceededError, BudgetLimits, BudgetManager


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def plan():
    return build_r4_reserve_plan(
        public_csim=True,
        csynth=True,
        public_cosim=True,
        hidden_evaluation=True,
        tool_calls=7,
        compile_calls=2,
        wall_time_s=3600,
    )


def producer_artifacts():
    selected = plan()
    budget = BudgetManager()
    before = budget.snapshot().to_dict()
    selected.ensure_available(budget)
    reservation = record_r4_budget_reservation(
        selected,
        budget_before=before,
        budget_after_admission=budget.snapshot().to_dict(),
    ).to_dict()
    policy = {
        "schema_version": 1,
        "status": "allowed",
        "reason_code": "candidate_repair_allowed",
        "action": "repair",
        "role": "candidate",
        "stage": "csynth",
        "evidence_view": "agent_safe",
        "owner_authority": "llm_advisory",
        "lineage_id": "run-1",
    }
    ledger = {**policy, "sequence": 1, "count_key": "candidate", "accepted": True}
    actual = build_r4_budget_actual(
        selected,
        budget_before=before,
        budget_after=budget.snapshot().to_dict(),
        provider_calls=1,
        mutation_calls=1,
        auditor_reads=1,
    )
    return selected, policy, ledger, reservation, actual


class R4EpisodeFoundationTests(unittest.TestCase):
    def _episode(self, outcome="verified_negative"):
        selected, policy, ledger, reservation, actual = producer_artifacts()
        authorization = {
            "authorization_id": "authorization-1",
            "before_candidate_sha256": h("before"),
            "policy_decision_id": canonical_artifact_sha256(policy),
            "ledger_reservation_id": canonical_artifact_sha256(ledger),
            "budget_reservation_id": reservation["reservation_id"],
        }
        return R4RepairEpisode(
            episode_id="episode-1",
            created_at="2026-09-12T00:00:00Z",
            event_ref="event-1",
            lineage=("r3-episode-1", "event-1"),
            execution_identity={"case_id": "case-1"},
            request_identity={"run_id": "run-1"},
            advisory_identity={"advisory_id": "advisory-1", "accepted": False},
            gate_identity={"decision": "accept", "contract_hash": h("gate")},
            authorization_identity=authorization,
            policy_decision=policy,
            ledger_event=ledger,
            budget_requested=selected.to_dict(),
            budget_effective=reservation,
            budget_actual=actual,
            candidate_before_sha256=h("before"),
            candidate_after_sha256=h("after"),
            public_testbench_before={"public": h("public")},
            public_testbench_after={"public": h("public")},
            hidden_testbench_before={"hidden": h("hidden")},
            hidden_testbench_after={"hidden": h("hidden")},
            formal_validation_id="validation-1",
            validation_evidence_refs=("evidence-1",),
            auditor_result={
                "status": "clean",
                "failure_attributable": True,
                "environment_excluded": True,
            },
            budget_delta=actual["budget_delta"],
            provider_call_count=1,
            vitis_phase_count=4,
            outcome=outcome,
            outcome_reason="typed test outcome",
        )

    def test_r4_episode_is_not_r3_shadow_episode(self):
        episode = self._episode()
        self.assertEqual(episode.outcome, "verified_negative")
        self.assertEqual(episode.to_dict()["schema_version"], 2)

    def test_episode_append_only_round_trip_and_duplicate_rejection(self):
        episode = self._episode()
        with tempfile.TemporaryDirectory() as root:
            path = episode.append_only_write(root)
            self.assertEqual(
                R4RepairEpisodeReader.read(path).episode_hash,
                episode.episode_hash,
            )
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
        episode = self._episode()
        with self.assertRaises(ValueError):
            replace(
                episode,
                auditor_result={
                    "status": "clean",
                    "failure_attributable": False,
                    "environment_excluded": True,
                },
                episode_hash="",
            )

    def test_episode_rejects_before_hash_and_producer_mismatches(self):
        episode = self._episode()
        with self.assertRaises(ValueError):
            replace(episode, candidate_before_sha256=h("wrong"), episode_hash="")
        with self.assertRaises(ValueError):
            replace(
                episode,
                policy_decision={**episode.policy_decision, "status": "denied"},
                episode_hash="",
            )

    def test_episode_id_cannot_escape_store_root(self):
        with self.assertRaises(ValueError):
            replace(self._episode(), episode_id="../escape", episode_hash="")

    def _provenance(self, **overrides):
        candidate = "candidate"
        revision_hash = h("revision")
        canary_hash = h("canary")
        target_id, toolchain_id = h("target"), h("toolchain")
        selected, policy, ledger, reservation, _ = producer_artifacts()
        terminal = {
            "validation_id": "main",
            "state": "csynth",
            "status": "validation_terminal",
            "candidate_sha256": h(candidate),
        }
        event = {
            "event_id": "event-1",
            "run_id": "run-1",
            "candidate_sha256": h(candidate),
            "evidence_refs": ["evidence-1"],
            "target_identity": {"fingerprint": target_id},
            "toolchain_identity": {"fingerprint": toolchain_id},
            "evidence_view": "agent_safe",
            "hidden_input_count": 0,
            "secret_present": False,
            "private_reasoning_present": False,
            "physical_tool_launched": True,
            "evidence_complete": True,
        }
        identity = {
            "run_id": "run-1",
            "case_id": "case-1",
            "identity_complete": True,
            "hidden_input_count": 0,
            "secret_present": False,
            "private_reasoning_present": False,
            "source_sha256": h("original"),
            "target_identity": target_id,
            "toolchain_identity": toolchain_id,
            "parser_identity": "parser-1",
            "model_identity": "model-1",
            "prompt_sha256": h("prompt"),
        }
        authorization = {
            "event_ref": "event-1",
            "run_id": "run-1",
            "advisory_id": "advisory-1",
            "gate_decision": "accept",
            "gate_contract_hash": h("gate"),
            "revision_sha256": revision_hash,
            "canary_manifest_sha256": canary_hash,
            "before_candidate_sha256": h(candidate),
            "policy_decision_id": canonical_artifact_sha256(policy),
            "ledger_reservation_id": canonical_artifact_sha256(ledger),
            "budget_reservation_id": reservation["reservation_id"],
            "deterministic_terminal_ref": canonical_artifact_sha256(terminal),
        }
        values = {
            "event": event,
            "execution_identity": identity,
            "advisory": {
                "advisory_id": "advisory-1",
                "accepted": False,
                "owner_authority": "llm_advisory",
                "evidence_refs": ["evidence-1"],
            },
            "gate": {
                "decision": "accept",
                "contract_hash": h("gate"),
                "evidence_refs": ["evidence-1"],
            },
            "revision": {
                "revision_hash": revision_hash,
                "lifecycle": "Trusted",
                "threshold_source": "frozen-calibration",
            },
            "authorization": authorization,
            "canary": {
                "manifest_sha256": canary_hash,
                "enabled": True,
                "operator_enabled": True,
                "case_ids": ["case-1"],
                "target_identity": target_id,
                "toolchain_identity": toolchain_id,
                "parser_identity": "parser-1",
                "model_identity": "model-1",
                "prompt_sha256": h("prompt"),
            },
            "candidate": candidate,
            "original": "original",
            "testbench_hashes": {"preflight": h("tb")},
            "policy_decision": policy,
            "ledger_event": ledger,
            "budget_reservation": reservation,
            "deterministic_terminal": terminal,
        }
        values.update(overrides)
        return validate_r4_provenance(**values)

    def test_complete_provenance_is_valid(self):
        result = self._provenance()
        self.assertTrue(result.valid, result.reasons)

    def test_provenance_rejects_candidate_and_advisory_scope_mismatches(self):
        base = self._provenance()
        self.assertTrue(base.valid)
        event = {
            "event_id": "event-1",
            "run_id": "run-1",
            "candidate_sha256": h("other"),
            "evidence_refs": ["evidence-1"],
            "target_identity": {"fingerprint": h("target")},
            "toolchain_identity": {"fingerprint": h("toolchain")},
            "evidence_view": "agent_safe",
            "hidden_input_count": 0,
            "secret_present": False,
            "private_reasoning_present": False,
            "physical_tool_launched": True,
            "evidence_complete": True,
        }
        result = self._provenance(
            event=event,
            advisory={
                "advisory_id": "advisory-1",
                "accepted": False,
                "owner_authority": "llm_advisory",
                "evidence_refs": ["outside"],
            },
        )
        self.assertIn("candidate_hash_mismatch", result.reasons)
        self.assertIn("advisory_evidence_out_of_scope", result.reasons)

    def test_provenance_rejects_producer_hash_mismatch(self):
        result = self._provenance(
            policy_decision={
                "schema_version": 1,
                "status": "denied",
                "action": "repair",
            }
        )
        self.assertIn("policy_decision_producer_hash_mismatch", result.reasons)

    def test_reserve_plan_covers_declared_full_prefix(self):
        selected = plan()
        limits = selected.to_budget_limits()
        self.assertEqual(limits.max_llm_calls, 1)
        self.assertEqual(limits.max_tool_calls, 7)
        self.assertEqual(limits.max_compile_calls, 2)
        self.assertEqual(limits.max_csim_calls, 2)
        self.assertEqual(limits.max_cosim_calls, 1)
        self.assertEqual(limits.max_csynth_calls, 1)
        self.assertEqual(
            selected.phase_order,
            (
                "preflight",
                "public_evaluation",
                "csynth",
                "public_cosim",
                "hidden_evaluation",
            ),
        )

    def test_reserve_is_derived_from_existing_handler_plan(self):
        handlers = {
            "preflight": lambda _: None,
            "public_evaluation": lambda _: None,
            "csynth": lambda _: None,
            "public_cosim": lambda _: None,
            "hidden_evaluation": lambda _: None,
        }
        selected = build_r4_reserve_plan_from_handlers(
            handlers,
            wall_time_s=3600,
            expected_plan=plan(),
        )
        self.assertEqual(selected.plan_sha256, plan().plan_sha256)
        with self.assertRaises(ValueError):
            build_r4_reserve_plan_from_handlers(
                {**handlers, "unknown": lambda _: None},
                wall_time_s=3600,
            )

    def test_manual_reserve_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            build_r4_reserve_plan(
                public_csim=True,
                csynth=True,
                public_cosim=True,
                hidden_evaluation=True,
                tool_calls=4,
                compile_calls=2,
                wall_time_s=100,
            )
        with self.assertRaises(ValueError):
            R4ReservePlan(provider_calls=0)

    def test_reserve_admission_is_prospective_and_fail_closed(self):
        selected = plan()
        budget = BudgetManager(
            BudgetLimits(
                max_llm_calls=1,
                max_tool_calls=7,
                max_compile_calls=2,
                max_csim_calls=2,
                max_csynth_calls=1,
                max_cosim_calls=1,
                max_wall_time_s=4000,
            )
        )
        before = budget.snapshot().to_dict()
        selected.ensure_available(budget)
        after = budget.snapshot().to_dict()
        for key in (
            "llm_calls",
            "tool_calls",
            "compile_calls",
            "csim_calls",
            "csynth_calls",
            "cosim_calls",
        ):
            self.assertEqual(before[key], after[key])
        blocked = BudgetManager(BudgetLimits(max_llm_calls=0))
        with self.assertRaises(BudgetExceededError):
            selected.ensure_available(blocked)


if __name__ == "__main__":
    unittest.main()
