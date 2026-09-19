from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_audit_formal_campaign",
    ROOT / "scripts" / "r5_audit_formal_campaign.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R5FormalCampaignAuditTests(unittest.TestCase):
    def test_outcome_counts_keep_research_categories_separate(self) -> None:
        rows = [
            {"status": "baseline_accepted"},
            {"status": "verified_positive"},
            {"status": "verified_negative"},
            {"status": "abstained"},
            {"status": "inconclusive"},
            {"status": "invalid_evidence"},
            {"status": "not-a-real-status"},
        ]
        self.assertEqual(
            MODULE.observation_counts(rows),
            {
                "baseline_accepted": 1,
                "verified_positive": 1,
                "verified_negative": 1,
                "abstained": 1,
                "inconclusive": 1,
                "invalid_evidence": 1,
                "unknown": 1,
            },
        )

    def test_paired_endpoints_compare_each_arm_with_a0(self) -> None:
        rows = [
            {"case_id": "c", "repeat": 1, "arm": "A0", "status": "abstained"},
            {"case_id": "c", "repeat": 1, "arm": "A1", "status": "abstained"},
            {"case_id": "c", "repeat": 1, "arm": "A2", "status": "verified_positive"},
            {"case_id": "c", "repeat": 1, "arm": "A3", "status": "verified_negative"},
        ]
        paired = {item["arm"]: item for item in MODULE.paired_endpoint_rows(rows)}
        self.assertEqual(paired["A1"]["paired_verified_repair_difference"], 0)
        self.assertEqual(paired["A2"]["paired_verified_repair_difference"], 1)
        self.assertEqual(
            paired["A3"]["paired_false_repair_or_negative_transfer_difference"],
            1,
        )

    def test_private_or_raw_content_is_rejected(self) -> None:
        self.assertTrue(MODULE.unsafe_persisted_content({"private_reasoning": "x"}))
        self.assertTrue(MODULE.unsafe_persisted_content({"nested": "<think>secret"}))
        self.assertTrue(MODULE.unsafe_persisted_content({"raw_provider_response_persisted": True}))
        self.assertFalse(
            MODULE.unsafe_persisted_content(
                {
                    "private_reasoning_persisted": False,
                    "raw_provider_response_persisted": False,
                    "policy_sha256": "a" * 64,
                }
            )
        )

    def test_canonical_hash_is_order_independent(self) -> None:
        self.assertEqual(
            MODULE.canonical_sha256({"a": 1, "b": [2, 3]}),
            MODULE.canonical_sha256({"b": [2, 3], "a": 1}),
        )


if __name__ == "__main__":
    unittest.main()
