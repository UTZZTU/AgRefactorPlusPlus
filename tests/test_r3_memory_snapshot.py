from __future__ import annotations

import hashlib
import unittest

from agrefactor.recovery.memory_gate import DiagnosticEpisode, EpisodeOutcome, GateDecision, GateResult, PatternLifecycle, RepairPatternRevision
from agrefactor.recovery.r3_memory_snapshot import MemorySnapshot, SnapshotRevisionSource


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class MemorySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.revision = RepairPatternRevision(
            revision_id="revision-1", parent_revision_id=None,
            supported_when={"stage": "csynth", "owner": "candidate"},
            avoid_when={}, exclusions={}, required_evidence=(),
            positive_episode_refs=("positive-1",), negative_episode_refs=(),
            calibration_refs=("calibration-1",), lifecycle=PatternLifecycle.TRUSTED,
            threshold_source="frozen-calibration",
        )
        self.episode = DiagnosticEpisode(
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

    def test_freeze_is_self_identifying_and_resolves_only_snapshot_revision(self):
        snapshot = MemorySnapshot.freeze(
            episodes=(self.episode,), revisions=(self.revision,),
            gate_context={"identity_complete": True, "calibrated_risk_ok": True},
            frozen_at="2026-09-12T00:00:00Z",
        )
        source = SnapshotRevisionSource(snapshot)
        resolved = source.resolve(event={"stage": "csynth"}, advisory={"suspected_owner": "candidate"})
        self.assertEqual(resolved.revision_hash, self.revision.revision_hash)
        context = snapshot.context_for(
            event={"evidence_refs": ["e1"]},
            advisory={
                "advisory_id": "a1",
                "suspected_failure_class": "unsupported_construct",
            },
        )
        self.assertEqual(context["snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(context["failure_family"], "unsupported_construct")
        with self.assertRaises(TypeError):
            snapshot.gate_context["identity_complete"] = False

    def test_snapshot_rejects_ambiguous_revision_resolution(self):
        second = RepairPatternRevision(
            revision_id="revision-2", parent_revision_id="revision-1",
            supported_when={"stage": "csynth", "owner": "candidate"},
            avoid_when={}, exclusions={}, required_evidence=(),
            positive_episode_refs=("positive-2",), negative_episode_refs=(),
            calibration_refs=("calibration-2",), lifecycle=PatternLifecycle.TRUSTED,
            threshold_source="frozen-calibration",
        )
        snapshot = MemorySnapshot.freeze(episodes=(self.episode,), revisions=(self.revision, second), gate_context={}, frozen_at="2026-09-12T00:00:00Z")
        with self.assertRaises(LookupError):
            SnapshotRevisionSource(snapshot).resolve(event={"stage": "csynth"}, advisory={"suspected_owner": "candidate"})


if __name__ == "__main__":
    unittest.main()
