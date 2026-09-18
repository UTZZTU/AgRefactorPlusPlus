from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.campaign import (
    R5Arm,
    R5ArmObservation,
    R5CampaignManifest,
    estimate_upper_bound,
    reduce_observations,
)
from agrefactor.recovery import (
    AppendOnlyEpisodeLedger,
    EpisodeLedgerError,
    Lifecycle,
    MemoryPayloadError,
    R5AuthorizationError,
    R5AuthorizationMode,
    R5BudgetError,
    R5BudgetLedger,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
    R5LifecycleReducer,
    R5MemoryPayload,
    R5PatternRevision,
    R5ResearchAuthorization,
    R5SnapshotBuilder,
    SnapshotBoundaryError,
    canonical_sha256,
)


def digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def envelope(*, episode_id: str, outcome: R5EpisodeOutcome, observed_at: str = "2026-09-18T00:00:00Z", source: str = "source-a", context: str = "context-a", family: str = "unsupported_construct") -> R5EpisodeEnvelope:
    return R5EpisodeEnvelope(
        episode_kind="r4_repair",
        episode_id=episode_id,
        payload_schema_version="r4-repair-v1",
        payload={"failure_family": family, "stage": "csynth", "owner": "candidate"},
        execution_identity_sha256=digest("identity"),
        source_sha256=digest(source),
        context_signature=digest(context),
        created_at=observed_at,
        observed_at=observed_at,
        lineage=(episode_id,),
        agent_safe_summary={"failure_family": family, "stage": "csynth", "owner": "candidate"},
        outcome=outcome,
        manifest_sha256=digest("manifest"),
    )


class R5LedgerTests(unittest.TestCase):
    def test_append_round_trip_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = AppendOnlyEpisodeLedger(root)
            record = envelope(episode_id="e1", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE)
            path = store.append(record)
            self.assertTrue(path.exists())
            self.assertEqual(store.get("e1").envelope_sha256, record.envelope_sha256)
            manifest = json.loads((Path(root) / "ledger_manifest.json").read_text())
            self.assertTrue(manifest["append_only"])

    def test_duplicate_changed_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = AppendOnlyEpisodeLedger(root)
            store.append(envelope(episode_id="e1", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE))
            changed = envelope(episode_id="e1", outcome=R5EpisodeOutcome.VERIFIED_NEGATIVE)
            with self.assertRaises(EpisodeLedgerError):
                store.append(changed)

    def test_future_record_cannot_enter_history(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = AppendOnlyEpisodeLedger(root)
            store.append(envelope(episode_id="future", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE, observed_at="2026-09-19T00:00:00Z"))
            with self.assertRaises(EpisodeLedgerError):
                store.eligible_records(through="2026-09-18T00:00:00Z")

    def test_raw_provider_field_is_rejected(self) -> None:
        with self.assertRaises(EpisodeLedgerError):
            R5EpisodeEnvelope(
                episode_kind="r4_repair", episode_id="bad", payload_schema_version="v1",
                payload={"raw_provider_response": "secret"}, execution_identity_sha256=digest("i"),
                source_sha256=digest("s"), context_signature=digest("c"), created_at="2026-09-18T00:00:00Z",
                observed_at="2026-09-18T00:00:00Z", lineage=("bad",), agent_safe_summary={},
                outcome=R5EpisodeOutcome.INCONCLUSIVE, manifest_sha256=digest("m"),
            )


class R5LifecycleTests(unittest.TestCase):
    def test_two_independent_positive_episodes_become_trusted(self) -> None:
        episodes = [
            envelope(episode_id="p1", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE, source="s1", context="c1"),
            envelope(episode_id="p2", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE, source="s2", context="c2"),
        ]
        result = R5LifecycleReducer().reduce(
            episodes, revision_id="rev-1", failure_family="unsupported_construct", stage="csynth", owner="candidate",
            supported_when={"stage": "csynth"}, avoid_when={}, exact_exclusions={}, required_evidence=("r2",),
            calibration_refs=("calibration",), memory_payload_manifest_sha256=digest("payload"), created_at="2026-09-18T01:00:00Z",
        )
        self.assertEqual(result.revision.lifecycle, Lifecycle.TRUSTED)
        self.assertEqual(result.positive_count, 2)

    def test_negative_transfer_quarantines(self) -> None:
        episodes = [
            envelope(episode_id="p1", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE, source="s1", context="c1"),
            envelope(episode_id="n1", outcome=R5EpisodeOutcome.VERIFIED_NEGATIVE, source="s2", context="c2"),
            envelope(episode_id="n2", outcome=R5EpisodeOutcome.VERIFIED_NEGATIVE, source="s3", context="c3"),
        ]
        result = R5LifecycleReducer().reduce(
            episodes, revision_id="rev-2", failure_family="unsupported_construct", stage="csynth", owner="candidate",
            supported_when={}, avoid_when={}, exact_exclusions={}, required_evidence=(), calibration_refs=("cal",),
            memory_payload_manifest_sha256=digest("payload"), created_at="2026-09-18T01:00:00Z",
        )
        self.assertEqual(result.revision.lifecycle, Lifecycle.QUARANTINED)

    def test_parent_revision_is_not_rewritten(self) -> None:
        parent = R5PatternRevision(
            revision_id="parent", parent_revision_id=None, failure_family="f", stage="csynth", owner="candidate",
            supported_when={}, avoid_when={}, exact_exclusions={}, required_evidence=(), positive_episode_refs=(),
            negative_episode_refs=(), calibration_refs=(), memory_payload_manifest_sha256=digest("p"),
            lifecycle=Lifecycle.PROVISIONAL, transition_reason="seed", threshold_source="r5-lifecycle-v1", created_at="2026-09-18T00:00:00Z",
        )
        child = R5LifecycleReducer().reduce(
            [envelope(episode_id="p", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE)], revision_id="child", failure_family="f", stage="csynth", owner="candidate",
            supported_when={}, avoid_when={"conflict": True}, exact_exclusions={}, required_evidence=(), calibration_refs=(), memory_payload_manifest_sha256=digest("c"), created_at="2026-09-18T01:00:00Z", parent=parent,
        )
        self.assertEqual(child.revision.parent_revision_id, "parent")
        self.assertNotEqual(parent.revision_sha256, child.revision.revision_sha256)

    def test_post_quarantine_negatives_deprecate(self) -> None:
        episodes = []
        for index, source in enumerate(("s1", "s2", "s1"), start=1):
            item = envelope(
                episode_id=f"post-n{index}",
                outcome=R5EpisodeOutcome.VERIFIED_NEGATIVE,
                source=source,
                context=f"c{index}",
            )
            item = R5EpisodeEnvelope.from_dict({
                **item.to_dict(),
                "agent_safe_summary": {
                    **item.agent_safe_summary,
                    "after_quarantine": True,
                },
                "envelope_sha256": "",
            })
            episodes.append(item)
        result = R5LifecycleReducer().reduce(
            episodes,
            revision_id="rev-deprecated",
            failure_family="unsupported_construct",
            stage="csynth",
            owner="candidate",
            supported_when={}, avoid_when={}, exact_exclusions={},
            required_evidence=(), calibration_refs=("cal",),
            memory_payload_manifest_sha256=digest("payload"),
            created_at="2026-09-18T01:00:00Z",
        )
        self.assertEqual(result.revision.lifecycle, Lifecycle.DEPRECATED)


class R5PayloadAndAuthorizationTests(unittest.TestCase):
    def test_candidate_only_payload_renders_safe_snippet(self) -> None:
        revision = R5PatternRevision(
            revision_id="trusted", parent_revision_id=None, failure_family="f", stage="csynth", owner="candidate",
            supported_when={}, avoid_when={}, exact_exclusions={}, required_evidence=(), positive_episode_refs=("p",),
            negative_episode_refs=(), calibration_refs=("cal",), memory_payload_manifest_sha256=digest("p"),
            lifecycle=Lifecycle.TRUSTED, transition_reason="trusted", threshold_source="r5-lifecycle-v1", created_at="2026-09-18T00:00:00Z",
        )
        payload = R5MemoryPayload.from_revision(revision, snapshot_sha256=digest("s"), repair_intent_or_recipe="candidate-only bounded rewrite", source_episode_hashes=(digest("e"),), evidence_refs=("e",))
        self.assertTrue(payload.candidate_only_scope)
        self.assertIn("candidate_only_scope", json.dumps(payload.to_dict()))

    def test_forbidden_payload_is_rejected(self) -> None:
        with self.assertRaises(MemoryPayloadError):
            R5MemoryPayload("s", "r", digest("r"), digest("s"), "x", {"hidden": True}, {}, True, (digest("e"),), ("e",))

    def test_authorization_modes_are_discriminated(self) -> None:
        common = dict(authorization_id="a2", arm_id="A2", mode=R5AuthorizationMode.ADVISOR_ONLY, calibration_certificate_sha256=digest("cal"), advisory_sha256=digest("adv"), policy_sha256=digest("pol"), ledger_sha256=digest("led"), budget_reservation_sha256=digest("bud"), r4_controller_contract_sha256=digest("r4"), memory_mode="none")
        auth = R5ResearchAuthorization(**common)
        self.assertEqual(auth.mode, R5AuthorizationMode.ADVISOR_ONLY)
        with self.assertRaises(R5AuthorizationError):
            R5ResearchAuthorization(**{**common, "retrieval_manifest_sha256": digest("retrieval")})

    def test_gated_authorization_requires_all_memory_hashes(self) -> None:
        common = dict(authorization_id="a4", arm_id="A4", mode=R5AuthorizationMode.GATED_MEMORY, calibration_certificate_sha256=digest("cal"), advisory_sha256=digest("adv"), policy_sha256=digest("pol"), ledger_sha256=digest("led"), budget_reservation_sha256=digest("bud"), r4_controller_contract_sha256=digest("r4"), memory_mode="positive_only_gated")
        with self.assertRaises(R5AuthorizationError):
            R5ResearchAuthorization(**common)


class R5ProtocolTests(unittest.TestCase):
    def test_budget_defaults_to_500_and_reserves(self) -> None:
        ledger = R5BudgetLedger(provider_used=5, vitis_used=20)
        reservation = ledger.reserve_with_recovery(provider_upper_bound=100, vitis_upper_bound=100)
        self.assertEqual(reservation.ledger.provider_cap, 500)
        self.assertEqual(reservation.ledger.vitis_cap, 500)
        with self.assertRaises(R5BudgetError):
            ledger.reserve(provider_calls=600)

    def test_manifest_requires_all_arms_and_three_repeats(self) -> None:
        kwargs = dict(campaign_id="r5", repository_commit="abc", source_inventory_sha256=digest("source"), history_snapshot_sha256=digest("snapshot"), arm_order=tuple(R5Arm), case_ids=("case-1",), prompt_identity_sha256=digest("prompt"), model_identity_sha256=digest("model"), target_identity_sha256=digest("target"), toolchain_identity_sha256=digest("tool"))
        manifest = R5CampaignManifest(**kwargs)
        self.assertEqual(len(manifest.to_dict()["arm_semantics"]), 7)

    def test_upper_bound_and_paired_reducer(self) -> None:
        provider, vitis = estimate_upper_bound(case_count=2)
        self.assertEqual((provider, vitis), (72, 96))
        rows = []
        for arm in R5Arm:
            rows.append(R5ArmObservation("case", 1, arm, "baseline", "ok", arm is R5Arm.A6, False, False, arm is R5Arm.A0, False, False, 1, 1, digest("source"), digest("context"), digest(arm.value)))
        reduction = reduce_observations(rows)
        self.assertEqual(reduction.pair_count, 1)
        self.assertEqual(reduction.paired_verified_positive_difference, 1.0)

    def test_snapshot_rejects_future_episode(self) -> None:
        with self.assertRaises(SnapshotBoundaryError):
            R5SnapshotBuilder().build([envelope(episode_id="future", outcome=R5EpisodeOutcome.VERIFIED_POSITIVE, observed_at="2026-09-19T00:00:00Z")], [], latest_allowed_timestamp="2026-09-18T00:00:00Z", frozen_at="2026-09-19T00:00:00Z", evidence_inventory_sha256=digest("inventory"), exact_exclusions={}, conflict_sparsity_ood_facts={})


if __name__ == "__main__":
    unittest.main()
