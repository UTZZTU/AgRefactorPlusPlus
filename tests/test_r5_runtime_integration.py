from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agrefactor.campaign import (
    R5Arm,
    R5CampaignManifest,
    R5CampaignRunner,
    R5CaseSpec,
)
from agrefactor.runtime.r5_profile import R5ProfileError, resolve_r5_profile
from agrefactor.runtime.r5_binding import R5RuntimeBinding, R5RuntimeBindingError
from agrefactor.product.source_bootstrap import (
    run_source_command_with_r5_binding,
    run_source_command_with_r5_capture,
)
from agrefactor.recovery import (
    R5AuthorizationMode,
    R5BudgetLedger,
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
    def __init__(self, *, arm_usage=None):
        self.baseline_calls = []
        self.arm_usage = arm_usage or {}

    def prepare_common_baseline(self, case, repeat):
        self.baseline_calls.append((case.case_id, repeat))
        return {
            "baseline_id": f"{case.case_id}-{repeat}",
            "source_sha256": case.source_sha256,
            "context_signature": case.context_signature,
            "provider_calls": 1,
            "vitis_launches": 3,
            "artifact_sha256": _sha(f"baseline-{case.case_id}-{repeat}"),
            "hidden_input_count": 0,
            "cross_arm_cache_used": False,
        }

    def run_arm(self, *, case, repeat, arm, baseline, arm_index):
        provider_calls, vitis_launches = self.arm_usage.get(
            arm,
            (0, 0),
        )
        return {
            "baseline_id": baseline["baseline_id"],
            "source_sha256": case.source_sha256,
            "context_signature": case.context_signature,
            "status": "abstained" if arm in {R5Arm.A0, R5Arm.A1} else "inconclusive",
            "provider_calls": provider_calls,
            "vitis_launches": vitis_launches,
            "artifact_sha256": _sha(f"{case.case_id}-{repeat}-{arm.value}"),
            "hidden_input_count": 0,
            "cross_arm_cache_used": False,
        }


class R5RuntimeIntegrationTests(unittest.TestCase):
    def test_internal_helpers_bind_arm_without_global_environment(self):
        binding = R5RuntimeBinding(resolve_r5_profile("A0"))
        expected = object()
        with patch(
            "agrefactor.product.source_bootstrap.run_source_command",
            return_value=expected,
        ) as run:
            self.assertIs(
                run_source_command_with_r5_binding(
                    SimpleNamespace(),
                    binding,
                ),
                expected,
            )
        internal_args = run.call_args.args[0]
        self.assertEqual(internal_args._r5_arm_override, "A0")
        self.assertIs(internal_args._r5_binding, binding)

        capture = object()
        captured_result = SimpleNamespace(r5_common_baseline=capture)
        with patch(
            "agrefactor.product.source_bootstrap.run_source_command",
            return_value=captured_result,
        ) as run:
            self.assertIs(
                run_source_command_with_r5_capture(SimpleNamespace()),
                captured_result,
            )
        internal_args = run.call_args.args[0]
        self.assertEqual(internal_args._r5_arm_override, "A0")
        self.assertTrue(internal_args._capture_r5_common_baseline)

    def test_profile_is_off_by_default_and_discriminated(self):
        self.assertFalse(resolve_r5_profile().selected)
        self.assertEqual(resolve_r5_profile("A3").authorization_mode, "similarity_only")
        with self.assertRaises(R5ProfileError):
            resolve_r5_profile("A7")

    def test_campaign_manifest_rejects_noncanonical_identity_hashes(self):
        with self.assertRaises(Exception):
            R5CampaignManifest(
                campaign_id="r5-bad",
                repository_commit="z" * 40,
                source_inventory_sha256=_sha("inventory"),
                history_snapshot_sha256=_sha("snapshot"),
                arm_order=tuple(R5Arm),
                case_ids=("case-1",),
                prompt_identity_sha256=_sha("prompt"),
                model_identity_sha256=_sha("model"),
                target_identity_sha256=_sha("target"),
                toolchain_identity_sha256=_sha("toolchain"),
            )

    def test_paired_runner_executes_only_future_cases(self):
        cases = (
            R5CaseSpec("history-01", _sha("source-history"), _sha("context-history"), "history"),
            R5CaseSpec("future-01", _sha("source-future"), _sha("context-future"), "future"),
        )
        executor = _Executor()
        with tempfile.TemporaryDirectory() as root:
            result = R5CampaignRunner(
                manifest=_manifest(),
                cases=cases,
                executor=executor,
                output_root=root,
            ).run()
            baseline_artifact = Path(root) / "baseline_observations.json"
            self.assertTrue(baseline_artifact.is_file())
            self.assertEqual(
                len(json.loads(baseline_artifact.read_text())["baselines"]),
                3,
            )
            plan = json.loads((Path(root) / "campaign_plan.json").read_text())
            self.assertEqual(plan["history_case_ids"], ["history-01"])
            self.assertEqual(plan["future_case_ids"], ["future-01"])
            self.assertEqual(
                plan["budget_upper_bound"],
                {"provider_calls": 60, "vitis_launches": 54},
            )
        self.assertEqual(
            executor.baseline_calls,
            [("future-01", 1), ("future-01", 2), ("future-01", 3)],
        )
        self.assertEqual(len(result.baselines), 3)
        self.assertEqual(len(result.observations), 21)
        self.assertEqual(result.reduction.pair_count, 3)
        self.assertEqual(result.budget.provider_used, 3)
        self.assertEqual(result.budget.vitis_used, 9)

    def test_runner_rejects_work_in_observation_only_arms(self):
        cases = (
            R5CaseSpec("history-01", _sha("source-history"), _sha("context-history"), "history"),
            R5CaseSpec("future-01", _sha("source-future"), _sha("context-future"), "future"),
        )
        executor = _Executor(arm_usage={R5Arm.A0: (1, 0)})
        with self.assertRaisesRegex(Exception, "A0 exceeded"):
            R5CampaignRunner(
                manifest=_manifest(),
                cases=cases,
                executor=executor,
            ).run()

    def test_runner_fails_budget_preflight_before_any_real_work(self):
        cases = (
            R5CaseSpec("history-01", _sha("source-history"), _sha("context-history"), "history"),
            R5CaseSpec("future-01", _sha("source-future"), _sha("context-future"), "future"),
        )
        executor = _Executor()
        with self.assertRaisesRegex(Exception, "budget preflight"):
            R5CampaignRunner(
                manifest=_manifest(),
                cases=cases,
                executor=executor,
                budget=R5BudgetLedger(
                    provider_used=470,
                    vitis_used=400,
                ),
            ).run()
        self.assertEqual(executor.baseline_calls, [])

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

    def test_memory_binding_rejects_retrieval_or_revision_substitution(self):
        a3 = R5ResearchAuthorization(
            authorization_id="a3",
            arm_id="A3",
            mode=R5AuthorizationMode.SIMILARITY_ONLY,
            calibration_certificate_sha256=_sha("cal"),
            advisory_sha256=_sha("adv"),
            policy_sha256=_sha("pol"),
            ledger_sha256=_sha("ledger"),
            budget_reservation_sha256=_sha("budget"),
            r4_controller_contract_sha256=_sha("r4"),
            memory_mode="similarity_only",
            retrieval_manifest_sha256=_sha("authorized-retrieval"),
        )
        with self.assertRaises(R5RuntimeBindingError):
            R5RuntimeBinding(
                resolve_r5_profile("A3"),
                authorization=a3,
                retrieval_manifest_sha256=_sha("substituted-retrieval"),
                r4_integration_factory=lambda _: object(),
            )

        revision = R5PatternRevision(
            revision_id="trusted-substitution",
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
            snapshot_sha256=_sha("snapshot-substitution"),
            repair_intent_or_recipe="bounded candidate-only rewrite",
            source_episode_hashes=(_sha("episode-substitution"),),
            evidence_refs=("episode-substitution",),
        )
        from agrefactor.runtime.r5_binding import _payload_manifest_sha256
        a4 = R5ResearchAuthorization(
            authorization_id="a4-substitution",
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
            revision_sha256=_sha("different-revision"),
            snapshot_sha256=payload.snapshot_sha256,
            payload_manifest_sha256=_payload_manifest_sha256((payload,)),
        )
        with self.assertRaises(R5RuntimeBindingError):
            R5RuntimeBinding(
                resolve_r5_profile("A4"),
                authorization=a4,
                memory_payloads=(payload,),
                r4_integration_factory=lambda _: object(),
            )


if __name__ == "__main__":
    unittest.main()
