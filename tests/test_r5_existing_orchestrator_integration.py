from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.recovery import (
    R5EpisodeOutcome,
    R5LifecycleReducer,
    R5MemoryPayload,
    R5SnapshotBuilder,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    canonical_sha256,
)
from agrefactor.recovery.gated_candidate_repair import R4CanaryManifest
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.runtime.budget import BudgetLimits
from agrefactor.runtime.r5_integration import (
    ExistingOrchestratorR5Integration,
    R5IntegrationConfig,
)
from agrefactor.runtime.r5_profile import resolve_r5_profile
from tests.test_r4_existing_orchestrator_integration import (
    Mutation,
    R4ExistingOrchestratorIntegrationTests,
    calibration_certificate,
    h,
    helpers,
)
from tests.test_r5_continual_memory import envelope


class R5ExistingOrchestratorIntegrationTests(unittest.TestCase):
    def _baseline(self):
        m = helpers()
        base = R4ExistingOrchestratorIntegrationTests()
        result, request, _, _ = base._run_main(m)
        return m, result, request, result.metadata["diagnostic_events"][0]

    def _identity_and_canary(self, event, request):
        target = event["target_identity"]["fingerprint"]
        toolchain = event["toolchain_identity"]["fingerprint"]
        identity = {
            "run_id": event["run_id"],
            "case_id": "case-1",
            "stage": event["stage"],
            "identity_complete": True,
            "hidden_input_count": 0,
            "secret_present": False,
            "private_reasoning_present": False,
            "source_sha256": h(request.original_code),
            "target_identity": target,
            "toolchain_identity": toolchain,
            "parser_identity": "parser-1",
            "model_identity": "model-1",
            "prompt_sha256": h("prompt"),
        }
        provisional = R4CanaryManifest(
            manifest_id="r5-canary-1",
            manifest_sha256="0" * 64,
            enabled=True,
            operator_enabled=True,
            case_ids=("case-1",),
            source_sha256=h(request.original_code),
            target_identity=target,
            toolchain_identity=toolchain,
            parser_identity="parser-1",
            model_identity="model-1",
            prompt_sha256=h("prompt"),
            allowed_stage=event["stage"],
            expires_at="2099-01-01T00:00:00Z",
        )
        canary = replace(
            provisional,
            manifest_sha256=canonical_artifact_sha256(provisional.to_dict()),
        )
        return identity, canary

    def _trusted_memory(self, certificate, *, facts=None):
        episodes = (
            envelope(
                episode_id="history-positive-1",
                outcome=R5EpisodeOutcome.VERIFIED_POSITIVE,
                source="source-1",
                context="context-1",
            ),
            envelope(
                episode_id="history-positive-2",
                outcome=R5EpisodeOutcome.VERIFIED_POSITIVE,
                source="source-2",
                context="context-2",
            ),
        )
        reduction = R5LifecycleReducer().reduce(
            episodes,
            revision_id="trusted-r5",
            failure_family="unsupported_construct",
            stage="csynth",
            owner="candidate",
            supported_when={"stage": "csynth", "owner": "candidate"},
            avoid_when={},
            exact_exclusions={},
            required_evidence=(),
            calibration_refs=(certificate.certificate_id,),
            memory_payload_manifest_sha256=R5_MEMORY_PAYLOAD_POLICY_SHA256,
            created_at="2026-09-18T01:00:00Z",
        )
        snapshot = R5SnapshotBuilder().build(
            episodes,
            (reduction,),
            latest_allowed_timestamp="2026-09-18T00:00:00Z",
            frozen_at="2026-09-18T02:00:00Z",
            evidence_inventory_sha256=h("inventory"),
            exact_exclusions={},
            conflict_sparsity_ood_facts=dict(facts or {}),
        )
        payload = R5MemoryPayload.from_revision(
            reduction.revision,
            snapshot_sha256=snapshot.snapshot_sha256,
            repair_intent_or_recipe="Replace unsupported dynamic allocation with bounded local storage.",
            source_episode_hashes=tuple(item.envelope_sha256 for item in episodes),
            evidence_refs=tuple(item.episode_id for item in episodes),
        )
        return reduction, snapshot, payload

    def _integration(
        self,
        *,
        arm,
        event,
        request,
        root,
        mutation,
        facts=None,
    ):
        certificate = calibration_certificate()
        identity, canary = self._identity_and_canary(event, request)
        kwargs = {}
        if arm == "A3":
            kwargs["retrieval_manifest_sha256"] = h("retrieval")
        if arm in {"A4", "A5", "A6"}:
            reduction, snapshot, payload = self._trusted_memory(
                certificate,
                facts=facts,
            )
            kwargs.update(
                memory_snapshot=snapshot,
                revision=reduction.revision,
                lifecycle_reduction=reduction,
                memory_payloads=(payload,),
            )
        return ExistingOrchestratorR5Integration(
            R5IntegrationConfig(
                profile=resolve_r5_profile(arm),
                canary=canary,
                execution_identity=identity,
                calibration_certificate=certificate,
                episode_ledger_root=root,
                campaign_manifest_sha256=h("campaign"),
                mutation_adapter=mutation,
                validation_wall_time_s=100,
                **kwargs,
            )
        )

    def _run(self, arm, *, facts=None):
        m, result, request, event = self._baseline()
        mutation = Mutation(m.P1)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        integration = self._integration(
            arm=arm,
            event=event,
            request=request,
            root=root.name,
            mutation=mutation,
            facts=facts,
        )
        arm_request = replace(request, r5_arm=arm)
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
            request=arm_request,
            main_result=result,
            handler_factory=m.ScenarioFactory(m.pass_scenario),
        )
        return outcome, mutation, Path(root.name)

    def test_a2_uses_adjacent_authorization_without_fake_revision(self):
        outcome, mutation, root = self._run("A2")
        self.assertEqual(outcome["status"], "verified_positive")
        research = outcome["authorization"]["research_authorization"]
        self.assertEqual(research["mode"], "advisor_only")
        self.assertIsNone(research["revision_sha256"])
        self.assertIsNone(research["gate_contract_sha256"])
        self.assertEqual(mutation.calls, 1)
        self.assertEqual(len(tuple(root.glob("*.json"))), 2)

    def test_a3_binds_similarity_manifest_without_claiming_gate(self):
        outcome, mutation, _ = self._run("A3")
        research = outcome["authorization"]["research_authorization"]
        self.assertEqual(outcome["status"], "verified_positive")
        self.assertEqual(research["mode"], "similarity_only")
        self.assertEqual(research["retrieval_manifest_sha256"], h("retrieval"))
        self.assertIsNone(research["gate_contract_sha256"])
        self.assertEqual(mutation.calls, 1)

    def test_a4_binds_real_reducer_snapshot_gate_and_payload(self):
        outcome, mutation, root = self._run("A4")
        research = outcome["authorization"]["research_authorization"]
        self.assertEqual(outcome["status"], "verified_positive")
        self.assertEqual(outcome["gate"]["decision"], "accept")
        self.assertEqual(research["mode"], "gated_memory")
        self.assertEqual(len(research["revision_sha256"]), 64)
        self.assertEqual(len(research["snapshot_sha256"]), 64)
        self.assertEqual(len(research["payload_manifest_sha256"]), 64)
        self.assertEqual(mutation.calls, 1)
        episode = json.loads(Path(outcome["episode_path"]).read_text())
        self.assertEqual(episode["outcome"], "verified_positive")
        self.assertEqual(episode["payload"]["arm"], "A4")
        self.assertTrue((root / "ledger_manifest.json").is_file())

    def test_gated_conflict_abstains_before_mutation(self):
        outcome, mutation, _ = self._run("A4", facts={"conflict": True})
        self.assertEqual(outcome["status"], "abstained")
        self.assertEqual(outcome["reason"], "gate_abstain")
        self.assertEqual(mutation.calls, 0)


if __name__ == "__main__":
    unittest.main()
