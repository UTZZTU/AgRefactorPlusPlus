from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from agrefactor.recovery.r5_historical_candidate import canonical_sha256
from agrefactor.recovery.r5_preexisting_history_resume import (
    R5PreexistingHistoryResumeError,
    _git_object_id,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load_script("r5_resume_preexisting_history")
RESULT_AUDITOR = _load_script("r5_audit_preexisting_history_resume_result")


class _Candidate:
    def to_identity(self) -> dict[str, str]:
        return {"case_id": "Recursive_E2_DFS_preexisting_candidate"}


def _preflight(root: Path) -> dict:
    plan_path = root / "plan.json"
    audit_path = root / "audit.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    audit_path.write_text("{}\n", encoding="utf-8")
    bundle = SimpleNamespace(
        plan_sha256="a" * 64,
        continuation_bundle=SimpleNamespace(candidate=_Candidate()),
        failed_audit={"audit_sha256": "b" * 64},
    )
    return {
        "repository_head": "c" * 40,
        "plan_path": plan_path,
        "audit_path": audit_path,
        "audit": {"audit_sha256": "d" * 64},
        "plan": {
            "contract_fix_commit": "e" * 40,
            "future_holdout_case_ids": [
                "Exception_E3_Turbo_Encoder",
                "Pointer_E3_double_pointer",
            ],
        },
        "bundle": bundle,
        "repository": root,
        "state": {},
        "state_path": root / "state.json",
        "calibration_bundle": {},
        "calibration_path": root / "calibration.json",
        "certificate": object(),
        "model_runtime": {"api_key_env": "R5_TEST_MISSING_KEY"},
    }


class R5PreexistingHistoryResumeTests(unittest.TestCase):
    def test_full_sha1_commit_is_not_misclassified_as_sha256(self) -> None:
        self.assertEqual(_git_object_id("a" * 40, "commit"), "a" * 40)
        self.assertEqual(_git_object_id("b" * 64, "commit"), "b" * 64)
        with self.assertRaisesRegex(
            R5PreexistingHistoryResumeError,
            "Git object ID",
        ):
            _git_object_id("c" * 39, "commit")

    def test_resume_executes_exactly_one_safe_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight = _preflight(root)
            output = root / "resume"

            def acquire(*, preflight, output, run_id):
                self.assertEqual(preflight["continuation"]["attempt_ordinal"], 3)
                self.assertEqual(preflight["budget"]["provider_call_upper_bound"], 2)
                self.assertEqual(preflight["budget"]["vitis_launch_upper_bound"], 6)
                output.mkdir(parents=True)
                (output / "acquisition_manifest.json").write_text("{}\n", encoding="utf-8")
                (output / "acquisition_result.json").write_text("{}\n", encoding="utf-8")
                (output / "evidence.zip").write_bytes(b"sealed")
                return {
                    "run_id": run_id,
                    "status": "abstained",
                    "provider_calls": 1,
                    "vitis_launches": 2,
                }

            with mock.patch.object(RUNNER, "acquire", side_effect=acquire) as called, mock.patch.object(
                RUNNER, "_is_safe_abstention", return_value=True
            ):
                result = RUNNER.run_resume(
                    preflight=preflight,
                    output=output,
                    resume_id="resume-test",
                )

            self.assertEqual(called.call_count, 1)
            self.assertEqual(result["status"], "exhausted_safe_abstention")
            self.assertEqual(result["provider_calls_after"], 96)
            self.assertEqual(result["vitis_launches_after"], 39)
            self.assertEqual(result["remaining_attempts"], 0)
            self.assertFalse(result["r5_accepted"])
            self.assertFalse(result["r6_started"])

    def test_result_auditor_rejects_unknown_clean_inconclusive(self) -> None:
        manifest = {
            "status": "frozen_before_one_post_fix_attempt",
            "attempts_authorized_originally": 2,
            "attempts_consumed": 1,
            "attempt_ordinal": 3,
            "maximum_remaining_attempts": 1,
            "maximum_candidate_mutations": 1,
            "provider_calls_before": 95,
            "vitis_launches_before": 37,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
            "confidence_minimum_authorized_label": "high",
            "confidence_threshold_weakened": False,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        manifest["manifest_sha256"] = canonical_sha256(manifest)
        attempt = {
            "attempt_ordinal": 3,
            "run_root": "placeholder",
            "status": "inconclusive",
            "provider_calls": 1,
            "vitis_launches": 2,
            "acquisition_manifest_file_sha256": "file-hash",
            "acquisition_result_file_sha256": "file-hash",
            "evidence_archive_sha256": "file-hash",
        }
        result = {
            "status": "stopped_inconclusive",
            "reason": "remaining_attempt_inconclusive",
            "manifest_sha256": manifest["manifest_sha256"],
            "attempt": attempt,
            "provider_calls": 1,
            "vitis_launches": 2,
            "provider_calls_after": 96,
            "vitis_launches_after": 39,
            "remaining_attempts": 0,
            "verified_positive_episode_count": 0,
            "confidence_threshold_weakened": False,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "trusted_revision_created": False,
            "r5_real_campaign_allowed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        result["result_sha256"] = canonical_sha256(result)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt_root = root / "attempt-03"
            attempt_root.mkdir()
            attempt["run_root"] = str(attempt_root)
            result["result_sha256"] = canonical_sha256(
                {key: value for key, value in result.items() if key != "result_sha256"}
            )
            with mock.patch.object(
                RESULT_AUDITOR,
                "_load",
                side_effect=[manifest, result],
            ), mock.patch.object(
                RESULT_AUDITOR,
                "file_sha256",
                return_value="file-hash",
            ), mock.patch.object(
                RESULT_AUDITOR,
                "audit_attempt",
                return_value={
                    "status": "clean_unrecognized_inconclusive",
                    "critical_finding_count": 0,
                    "blocking_finding_count": 0,
                    "findings": [],
                },
            ):
                with self.assertRaisesRegex(
                    RESULT_AUDITOR.PreexistingHistoryResumeResultAuditError,
                    "outcome does not match",
                ):
                    RESULT_AUDITOR.audit(root)


if __name__ == "__main__":
    unittest.main()
