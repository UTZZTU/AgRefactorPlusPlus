from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_history_continuation_audit",
    ROOT / "scripts" / "r5_history_continuation_audit.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _signed(value: dict, field: str) -> dict:
    value[field] = MODULE._sha(value)
    return value


def _fixture():
    manifest = _signed(
        {
            "history_case_ids": ["h1", "h2"],
            "future_case_ids": ["f1", "f2"],
        },
        "manifest_sha256",
    )
    prior = _signed(
        {
            "status": "ready_for_history_acquisition",
            "adapter_manifest_file_sha256": "a" * 64,
            "adapter_manifest_sha256": manifest["manifest_sha256"],
        },
        "protocol_audit_sha256",
    )
    reconciliation = {
        "status": "reconciled_data_insufficient_common_baseline_accepted",
        "history_manifest": {
            "future_outcomes_observed": False,
            "future_files_executed": False,
        },
        "observed_case": {"case_id": "h1"},
        "data_decision": {
            "verified_positive_episode_created": False,
            "trusted_revision_creation_allowed": False,
            "second_history_case_outcome_observed": False,
        },
    }
    state = {
        "R4_ACCEPTED": True,
        "R5_STARTED": True,
        "R5_ACCEPTED": False,
        "R6_STARTED": False,
        "R5_PROVIDER_CALL_HARD_CAP": 500,
        "R5_VITIS_LAUNCH_HARD_CAP": 500,
        "R5_REAL_CAMPAIGN_ALLOWED": False,
        "R5_CONSUMED_PROVIDER_CALLS": 66,
        "R5_CONSUMED_VITIS_LAUNCHES": 24,
        "R5_DATASET_V4_PROTOCOL_AUDIT_FILE_SHA256": "b" * 64,
        "R5_HISTORY_BASELINE_OUTCOME_RECONCILIATION_SHA256": "c" * 64,
    }
    return state, manifest, prior, reconciliation


class R5HistoryContinuationAuditTests(unittest.TestCase):
    def test_only_unobserved_history_case_is_authorized(self):
        state, manifest, prior, reconciliation = _fixture()
        result = MODULE.build_continuation_audit(
            state=state,
            adapter_manifest=manifest,
            prior_audit=prior,
            reconciliation=reconciliation,
            adapter_manifest_file_sha256="a" * 64,
            prior_audit_file_sha256="b" * 64,
            reconciliation_file_sha256="c" * 64,
            repository_head="d" * 40,
        )
        self.assertEqual(result["status"], "ready_for_history_continuation")
        self.assertEqual(result["authorized_history_case_ids"], ["h2"])
        self.assertEqual(result["continuation_provider_upper_bound"], 33)
        self.assertEqual(result["continuation_vitis_upper_bound"], 24)
        self.assertEqual(result["future_formal_provider_upper_bound"], 120)
        self.assertEqual(result["future_formal_vitis_upper_bound"], 144)
        unsigned = dict(result)
        digest = unsigned.pop("continuation_audit_sha256")
        self.assertEqual(digest, MODULE._sha(unsigned))

    def test_future_observation_blocks_continuation(self):
        state, manifest, prior, reconciliation = _fixture()
        reconciliation["history_manifest"]["future_outcomes_observed"] = True
        with self.assertRaisesRegex(ValueError, "crosses the continuation boundary"):
            MODULE.build_continuation_audit(
                state=state,
                adapter_manifest=manifest,
                prior_audit=prior,
                reconciliation=reconciliation,
                adapter_manifest_file_sha256="a" * 64,
                prior_audit_file_sha256="b" * 64,
                reconciliation_file_sha256="c" * 64,
                repository_head="d" * 40,
            )

    def test_ledger_mismatch_blocks_continuation(self):
        state, manifest, prior, reconciliation = _fixture()
        state["R5_CONSUMED_PROVIDER_CALLS"] = 65
        with self.assertRaisesRegex(ValueError, "authoritative R5 state"):
            MODULE.build_continuation_audit(
                state=state,
                adapter_manifest=manifest,
                prior_audit=prior,
                reconciliation=reconciliation,
                adapter_manifest_file_sha256="a" * 64,
                prior_audit_file_sha256="b" * 64,
                reconciliation_file_sha256="c" * 64,
                repository_head="d" * 40,
            )


if __name__ == "__main__":
    unittest.main()
