from __future__ import annotations

import unittest

from agrefactor.recovery.memory_gate import (
    ApplicabilityGate,
    DiagnosticEpisode,
    EpisodeOutcome,
    EpisodeStore,
    GateDecision,
    GateResult,
    MemoryContractError,
    PatternLifecycle,
    RepairPatternRevision,
    classify_outcome,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def gate(decision: str = "abstain") -> GateResult:
    return GateResult(GateDecision(decision), ("sample",), ("e1",), ApplicabilityGate.ORDER, SHA_A)


def episode(**overrides) -> DiagnosticEpisode:
    values = {
        "episode_id": "ep-1", "created_at": "2026-08-31T00:00:00Z", "parent_episode_id": None,
        "lineage": ("ep-1",), "event_ref": "event-1", "execution_identity": {"target": "zcu", "toolchain": "vitis-2023.2"},
        "request": {"stage": "public_csim"}, "context_signature": SHA_A,
        "deterministic_diagnosis": {"owner": "unknown", "failure_class": "runtime_mismatch"},
        "advisory": {"suspected_owner": "unknown", "accepted": False}, "retrieved_revision_ids": (), "gate": gate(),
        "repair_authorization": "not_requested", "before_hash": SHA_B, "after_hash": None, "full_revalidation_ref": None,
        "budget_delta": {"provider_calls": 0}, "outcome": EpisodeOutcome.ABSTAINED, "outcome_refs": ("event-1",),
    }
    values.update(overrides)
    return DiagnosticEpisode(**values)


class R3MemoryGateTests(unittest.TestCase):
    def test_episode_is_hashed_and_store_is_append_only(self):
        item = episode()
        self.assertEqual(len(item.episode_hash), 64)
        store = EpisodeStore(); store.append(item)
        with self.assertRaises(MemoryContractError): store.append(item)
        with self.assertRaises(MemoryContractError): episode(after_hash=SHA_B)

    def test_firewall_and_lineage(self):
        with self.assertRaises(MemoryContractError): episode(request={"hidden_content": "x"})
        with self.assertRaises(MemoryContractError): episode(lineage=("ep-parent", "ep-parent"))

    def test_outcome_attribution_is_conservative(self):
        self.assertEqual(classify_outcome(authorized=False, full_revalidation=False, semantic_preserved=False, identity_complete=True, auditor_clean=False, attributable=False, environment_excluded=False), EpisodeOutcome.ABSTAINED)
        self.assertEqual(classify_outcome(authorized=True, full_revalidation=True, semantic_preserved=True, identity_complete=True, auditor_clean=True, attributable=False, environment_excluded=True), EpisodeOutcome.VERIFIED_POSITIVE)
        self.assertEqual(classify_outcome(authorized=True, full_revalidation=False, semantic_preserved=False, identity_complete=True, auditor_clean=False, attributable=True, environment_excluded=True), EpisodeOutcome.VERIFIED_NEGATIVE)
        self.assertEqual(classify_outcome(authorized=True, full_revalidation=False, semantic_preserved=False, identity_complete=True, auditor_clean=False, attributable=False, environment_excluded=False), EpisodeOutcome.INCONCLUSIVE)

    def test_gate_order_and_fail_closed_decisions(self):
        rev = RepairPatternRevision("r1", None, {"stage": "public_csim"}, {}, {}, (), (), (), ())
        g = ApplicabilityGate()
        self.assertEqual(g.evaluate(context={"identity_complete": False}, revision=rev, evidence_refs=("e1",)).decision, GateDecision.REJECT)
        self.assertEqual(g.evaluate(context={"identity_complete": True}, revision=rev, evidence_refs=("e1",)).decision, GateDecision.ABSTAIN)
        self.assertEqual(g.evaluate(context={"identity_complete": True, "evidence_predicates": (), "sparse": True}, revision=rev, evidence_refs=("e1",)).decision, GateDecision.ABSTAIN)
        self.assertEqual(g.evaluate(context={"identity_complete": True, "calibrated_risk_ok": True}, revision=rev, evidence_refs=("e1",)).decision, GateDecision.ABSTAIN)
        self.assertEqual(g.evaluate(context={"identity_complete": True, "secret_present": True}, revision=rev, evidence_refs=("e1",)).decision, GateDecision.REJECT)

    def test_lifecycle_and_trusted_threshold_source(self):
        self.assertEqual(RepairPatternRevision("r1", None, {}, {}, {}, (), (), (), ()).lifecycle, PatternLifecycle.QUARANTINED)
        with self.assertRaises(MemoryContractError): RepairPatternRevision("r2", None, {}, {}, {}, (), (), (), (), lifecycle=PatternLifecycle.TRUSTED)


if __name__ == "__main__":
    unittest.main()
