from __future__ import annotations

import hashlib
import unittest

from agrefactor.campaign import (
    R5Arm,
    R5CampaignManifest,
    R5CampaignRunner,
    R5CaseSpec,
)
from agrefactor.runtime.r5_profile import R5ProfileError, resolve_r5_profile
from agrefactor.runtime.r5_binding import R5RuntimeBinding, R5RuntimeBindingError
from agrefactor.recovery import (
    R5AuthorizationMode,
    R5ResearchAuthorization,
    R5MemoryPayload,
    R5PatternRevision,
    Lifecycle,
)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _manifest() -> R5CampaignManifest:
    return R5CampaignManifest(
        campaign_id="r5-test",
        repository_commit=_sha("repo"),
        source_inventory_sha256=_sha("inventory"),
        history_snapshot_sha256=_sha("snapshot"),
        arm_order=tuple(R5Arm),
        case_ids=("history-01", "future-01"),
        prompt_identity_sha256=_sha("prompt"),
        model_identity_sha256=_sha("model"),
        target_identity_sha256=_sha("target"),
        toolchain_identity_sha256=_sha("toolchain"),
    )


class _Executor:
    def prepare_common_baseline(self, case, repeat):
        return {
            "baseline_id": f"{case.case_id}-{repeat}",
            "source_sha256": case.source_sha256,
            "context_signature": case.context_signature,
        }

    def run_arm(self, *, case, repeat, arm, baseline, arm_index):
        return {
            "baseline_id": baseline["baseline_id"],
            "source_sha256": case.source_sha256,
            "context_signature": case.context_signature,
            "status": "abstained" if arm in {R5Arm.A0, R5Arm.A1} else "inconclusive",
            "provider_calls": 0,
            "vitis_launches": 0,
            "artifact_sha256": _sha(f"{case.case_id}-{repeat}-{arm.value}"),
            "hidden_input_count": 0,
            "cross_arm_cache_used": False,
        }


class R5RuntimeIntegrationTests(unittest.TestCase):
    def test_profile_is_off_by_default_and_discriminated(self):
        self.assertFalse(resolve_r5_profile().selected)
        self.assertEqual(resolve_r5_profile("A3").authorization_mode, "similarity_only")
        with self.assertRaises(R5ProfileError):
            resolve_r5_profile("A7")

    def test_paired_runner_uses_one_baseline_per_case_repeat(self):
        cases = (
            R5CaseSpec("history-01", _sha("source-history"), _sha("context-history"), "history"),
            R5CaseSpec("future-01", _sha("source-future"), _sha("context-future"), "future"),
        )
        result = R5CampaignRunner(
            manifest=_manifest(),
            cases=cases,
            executor=_Executor(),
        ).run()
        self.assertEqual(len(result.observations), 42)
        self.assertEqual(result.reduction.pair_count, 6)
        self.assertEqual(result.budget.provider_used, 0)
        self.assertEqual(result.budget.vitis_used, 0)

    def test_source_holdout_crossing_is_rejected(self):
        source = _sha("same")
        cases = (
            R5CaseSpec("history-01", source, _sha("history"), "history"),
            R5CaseSpec("future-01", source, _sha("future"), "future"),
        )
        with self.assertRaisesRegex(Exception, "crosses"):
            R5CampaignRunner(manifest=_manifest(), cases=cases, executor=_Executor())

    def test_a0_a1_are_observation_only_and_a2_requires_existing_r4(self):
        with self.assertRaises(R5RuntimeBindingError):
            R5RuntimeBinding(resolve_r5_profile("A0"), r4_integration_factory=lambda _: None)
        common = dict(
            authorization_id="a2",
            arm_id="A2",
            mode=R5AuthorizationMode.ADVISOR_ONLY,
            calibration_certificate_sha256=_sha("cal"),
            advisory_sha256=_sha("adv"),
            policy_sha256=_sha("pol"),
            ledger_sha256=_sha("ledger"),
            budget_reservation_sha256=_sha("budget"),
            r4_controller_contract_sha256=_sha("r4"),
            memory_mode="none",
        )
        authorization = R5ResearchAuthorization(**common)
        with self.assertRaises(R5RuntimeBindingError):
            R5RuntimeBinding(resolve_r5_profile("A2"), authorization=authorization)
        binding = R5RuntimeBinding(
            resolve_r5_profile("A2"),
            authorization=authorization,
            r4_integration_factory=lambda _: object(),
        )
        self.assertEqual(binding.approved_memory_snippets, ())
        self.assertTrue(binding.to_dict()["r4_integration_bound"])

    def test_a4_payload_manifest_and_snapshot_are_bound_to_prompt(self):
        revision = R5PatternRevision(
            revision_id="trusted",
            parent_revision_id=None,
            failure_family="f",
            stage="csynth",
            owner="candidate",
            supported_when={},
            avoid_when={},
            exact_exclusions={},
            required_evidence=(),
            positive_episode_refs=("p",),
            negative_episode_refs=(),
            calibration_refs=("cal",),
            memory_payload_manifest_sha256=_sha("placeholder"),
            lifecycle=Lifecycle.TRUSTED,
            transition_reason="trusted",
            threshold_source="r5-lifecycle-v1",
            created_at="2026-09-18T00:00:00Z",
        )
        payload = R5MemoryPayload.from_revision(
            revision,
            snapshot_sha256=_sha("snapshot"),
            repair_intent_or_recipe="bounded candidate-only rewrite",
            source_episode_hashes=(_sha("episode"),),
            evidence_refs=("episode",),
        )
        from agrefactor.runtime.r5_binding import _payload_manifest_sha256
        authorization = R5ResearchAuthorization(
            authorization_id="a4",
            arm_id="A4",
            mode=R5AuthorizationMode.GATED_MEMORY,
            calibration_certificate_sha256=_sha("cal"),
            advisory_sha256=_sha("adv"),
            policy_sha256=_sha("pol"),
            ledger_sha256=_sha("ledger"),
            budget_reservation_sha256=_sha("budget"),
            r4_controller_contract_sha256=_sha("r4"),
            memory_mode="positive_only_gated",
            gate_contract_sha256=_sha("gate"),
            revision_sha256=revision.revision_sha256,
            snapshot_sha256=payload.snapshot_sha256,
            payload_manifest_sha256=_payload_manifest_sha256((payload,)),
        )
        binding = R5RuntimeBinding(
            resolve_r5_profile("A4"),
            authorization=authorization,
            memory_payloads=(payload,),
            r4_integration_factory=lambda _: object(),
        )
        self.assertEqual(len(binding.approved_memory_snippets), 1)
        self.assertEqual(
            binding.payload_manifest_sha256,
            authorization.payload_manifest_sha256,
        )


if __name__ == "__main__":
    unittest.main()
