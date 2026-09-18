from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from agrefactor.recovery import (
    Lifecycle,
    R4RepairEpisode,
    R5LifecycleReducer,
    R5PredecessorImportError,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    canonical_artifact_sha256,
    import_package_d_predecessor,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class R5PredecessorImportTests(unittest.TestCase):
    certificate_id = "r2-calibration-test"
    canary_manifest_sha256 = _sha("canary-manifest")
    source_sha256 = _sha("shared-source")
    context_signature = _sha("shared-context")

    def _episode(self, run_id: str) -> R4RepairEpisode:
        event_ref = f"diagnostic-{run_id}"
        policy = {
            "status": "allowed",
            "action": "repair",
            "role": "candidate",
            "stage": "csynth",
            "evidence_view": "agent_safe",
            "owner_authority": "llm_advisory",
            "lineage_id": f"{run_id}.initial-validation",
        }
        ledger = {**policy, "accepted": True}
        plan_sha256 = _sha("plan")
        reservation_id = _sha("reservation")
        before = _sha(f"before-{run_id}")
        after = _sha(f"after-{run_id}")
        return R4RepairEpisode(
            episode_id=f"r4-{run_id}",
            created_at="2026-09-18T07:42:45Z",
            event_ref=event_ref,
            lineage=(event_ref,),
            execution_identity={
                "case_id": "heldout-recursive",
                "run_id": f"{run_id}.initial-validation",
                "source_sha256": self.source_sha256,
                "stage": "csynth",
                "identity_complete": True,
                "hidden_input_count": 0,
                "secret_present": False,
                "private_reasoning_present": False,
                "target_identity": _sha("target"),
                "toolchain_identity": _sha("toolchain"),
                "parser_identity": "vitis-hls-2023.2",
                "model_identity": _sha("model"),
                "prompt_sha256": _sha("prompt"),
            },
            request_identity={"run_id": f"{run_id}.initial-validation"},
            advisory_identity={
                "advisory_id": _sha(f"advisory-{run_id}"),
                "accepted": False,
                "owner_authority": "llm_advisory",
                "calibration_verified": True,
                "calibration_certificate_id": self.certificate_id,
                "suspected_failure_class": "unsupported_construct",
                "suspected_owner": "candidate",
                "repair_scope": "candidate_only",
                "confidence": "high",
                "abstain_reason": None,
                "evidence_refs": [f"{event_ref}.item.1"],
            },
            gate_identity={"decision": "accept"},
            authorization_identity={
                "before_candidate_sha256": before,
                "policy_decision_id": canonical_artifact_sha256(policy),
                "ledger_reservation_id": canonical_artifact_sha256(ledger),
                "budget_reservation_id": reservation_id,
                "canary_manifest_sha256": self.canary_manifest_sha256,
            },
            policy_decision=policy,
            ledger_event=ledger,
            budget_requested={"plan_sha256": plan_sha256},
            budget_effective={
                "admitted": True,
                "reservation_id": reservation_id,
                "requested": {"plan_sha256": plan_sha256},
            },
            budget_actual={"plan_sha256": plan_sha256, "budget_delta": {}},
            candidate_before_sha256=before,
            candidate_after_sha256=after,
            public_testbench_before={"public": _sha("public")},
            public_testbench_after={"public": _sha("public")},
            hidden_testbench_before={"private-suite": _sha("private-suite")},
            hidden_testbench_after={"private-suite": _sha("private-suite")},
            formal_validation_id=f"{run_id}.r4",
            validation_evidence_refs=(f"{run_id}.r4.step.1",),
            auditor_result={"status": "clean"},
            budget_delta={},
            provider_call_count=1,
            vitis_phase_count=3,
            outcome="verified_positive",
            outcome_reason="fresh_full_prefix_and_audit_passed",
        )

    def _build_evidence(self, root: Path, *, clean_audit: bool = True) -> str:
        audit = {
            "status": "ready_for_manual_checkpoint",
            "r4_ready_for_manual_checkpoint": True,
            "critical_finding_count": 0 if clean_audit else 1,
            "findings": [] if clean_audit else [{"severity": "critical"}],
            "execution_boundary": "separate_python_process_file_only",
        }
        _write(root / "independent_audit.json", audit)
        frozen = {
            "manifest_schema_version": 2,
            "repository_branch": "research-roadmap-v2.3",
            "canary_manifest_sha256": self.canary_manifest_sha256,
            "original_source_sha256": self.source_sha256,
            "calibration_evidence": {
                "certificate": {
                    "certificate_id": self.certificate_id,
                    "accepted": True,
                }
            },
        }
        _write(root / "frozen_manifest.json", frozen)
        records = []
        for run_id in ("canary-02", "canary-03"):
            episode = self._episode(run_id)
            episode_path = root / "runs" / run_id / "episodes" / f"{episode.episode_id}.json"
            _write(episode_path, episode.to_dict())
            result = {
                "schema_version": 1,
                "run_id": run_id,
                "arm": "canary",
                "status": "verified_positive",
                "frozen_manifest_sha256": self.canary_manifest_sha256,
                "hidden_content_exposed_to_model": False,
                "raw_provider_response_persisted": False,
                "secret_values_persisted": False,
                "fixture_hashes": {"reference.cpp": self.source_sha256},
                "orchestration_result": {
                    "metadata": {
                        "diagnostic_events": [
                            {
                                "event_id": episode.event_ref,
                                "context_signature": self.context_signature,
                                "evidence_view": "agent_safe",
                                "evidence_complete": True,
                                "physical_tool_launched": True,
                                "hidden_input_count": 0,
                                "secret_present": False,
                                "private_reasoning_present": False,
                                "stage": "csynth",
                            }
                        ],
                        "r4_integration": {
                            "status": "verified_positive",
                            "main_result_unchanged": True,
                            "accepted_by_integration": False,
                            "episode_path": str(episode_path),
                            "episode_hash": episode.episode_hash,
                            "provenance": {"valid": True},
                            "r4_result": {
                                "outcome": "verified_positive",
                                "accepted": True,
                                "mutation_count": 1,
                            },
                        },
                    }
                },
            }
            _write(root / "runs" / run_id / "result.json", result)
            records.append({"run_id": run_id, "status": "verified_positive"})
        _write(
            root / "campaign_index.json",
            {
                "frozen_manifest_sha256": self.canary_manifest_sha256,
                "records": records,
            },
        )
        relative_files = [
            "independent_audit.json",
            "frozen_manifest.json",
            "campaign_index.json",
        ]
        for run_id in ("canary-02", "canary-03"):
            relative_files.extend(
                [
                    f"runs/{run_id}/result.json",
                    f"runs/{run_id}/episodes/r4-{run_id}.json",
                ]
            )
        _write(
            root / "package_d_content_manifest.json",
            {
                "schema_version": 1,
                "files": {
                    relative: _file_sha(root / relative)
                    for relative in relative_files
                },
            },
        )
        archive = root / "package_d_v1_6_evidence.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for relative in (*relative_files, "package_d_content_manifest.json"):
                output.write(root / relative, relative)
        archive_sha256 = _file_sha(archive)
        (root / "package_d_v1_6_evidence.zip.sha256").write_text(
            f"{archive_sha256}  {archive.name}\n", encoding="utf-8"
        )
        _write(
            root / "PACKAGE_D_RESULT.json",
            {
                "evidence_archive_sha256": archive_sha256,
                **audit,
            },
        )
        return archive_sha256

    def test_real_predecessor_shape_imports_as_provisional_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            root.mkdir()
            archive_sha256 = self._build_evidence(root)
            imported = import_package_d_predecessor(
                evidence_root=root,
                ledger_root=Path(temporary) / "ledger",
                expected_archive_sha256=archive_sha256,
                expected_calibration_certificate_id=self.certificate_id,
            )
            self.assertEqual(len(imported.envelopes), 2)
            self.assertEqual(imported.independent_source_count, 1)
            self.assertEqual(imported.independent_context_count, 1)
            reduction = R5LifecycleReducer().reduce(
                imported.envelopes,
                revision_id="predecessor",
                failure_family="unsupported_construct",
                stage="csynth",
                owner="candidate",
                supported_when={},
                avoid_when={},
                exact_exclusions={},
                required_evidence=(),
                calibration_refs=(self.certificate_id,),
                memory_payload_manifest_sha256=R5_MEMORY_PAYLOAD_POLICY_SHA256,
                created_at="2026-09-19T00:00:00Z",
            )
            self.assertEqual(reduction.revision.lifecycle, Lifecycle.PROVISIONAL)

    def test_exact_reimport_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            root.mkdir()
            archive_sha256 = self._build_evidence(root)
            kwargs = {
                "evidence_root": root,
                "ledger_root": Path(temporary) / "ledger",
                "expected_archive_sha256": archive_sha256,
                "expected_calibration_certificate_id": self.certificate_id,
            }
            first = import_package_d_predecessor(**kwargs)
            second = import_package_d_predecessor(**kwargs)
            self.assertEqual(len(first.appended_episode_ids), 2)
            self.assertEqual(len(second.existing_episode_ids), 2)

    def test_unclean_independent_audit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            root.mkdir()
            archive_sha256 = self._build_evidence(root, clean_audit=False)
            with self.assertRaisesRegex(
                R5PredecessorImportError, "independent acceptance evidence"
            ):
                import_package_d_predecessor(
                    evidence_root=root,
                    ledger_root=Path(temporary) / "ledger",
                    expected_archive_sha256=archive_sha256,
                    expected_calibration_certificate_id=self.certificate_id,
                )

    def test_content_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            root.mkdir()
            archive_sha256 = self._build_evidence(root)
            path = root / "runs" / "canary-02" / "result.json"
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(R5PredecessorImportError, "content hash mismatch"):
                import_package_d_predecessor(
                    evidence_root=root,
                    ledger_root=Path(temporary) / "ledger",
                    expected_archive_sha256=archive_sha256,
                    expected_calibration_certificate_id=self.certificate_id,
                )

    def test_rewritten_directory_manifest_cannot_escape_archive_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            root.mkdir()
            archive_sha256 = self._build_evidence(root)
            result_path = root / "runs" / "canary-02" / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["forged"] = True
            _write(result_path, result)
            manifest_path = root / "package_d_content_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["runs/canary-02/result.json"] = _file_sha(result_path)
            _write(manifest_path, manifest)
            with self.assertRaisesRegex(
                R5PredecessorImportError, "not bound to the accepted archive"
            ):
                import_package_d_predecessor(
                    evidence_root=root,
                    ledger_root=Path(temporary) / "ledger",
                    expected_archive_sha256=archive_sha256,
                    expected_calibration_certificate_id=self.certificate_id,
                )


if __name__ == "__main__":
    unittest.main()
