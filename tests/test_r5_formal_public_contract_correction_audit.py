from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_audit_formal_public_contract_correction",
    ROOT / "scripts" / "r5_audit_formal_public_contract_correction.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R5FormalPublicContractCorrectionAuditTests(unittest.TestCase):
    def test_counts_keep_negative_categories_separate(self) -> None:
        rows = [
            {"status": "baseline_accepted"},
            {"status": "verified_positive"},
            {"status": "verified_negative"},
            {"status": "abstained"},
            {"status": "inconclusive"},
            {"status": "invalid_evidence"},
            {"status": "other"},
        ]
        self.assertEqual(
            MODULE.outcome_counts(rows),
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

    def test_auditor_is_file_only(self) -> None:
        source = (
            ROOT / "scripts" / "r5_audit_formal_public_contract_correction.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("resolve_model_runtime", source)
        self.assertNotIn("vitis_hls", source)
        self.assertNotIn("subprocess", source)

    def test_private_content_helper_remains_fail_closed(self) -> None:
        self.assertTrue(MODULE.unsafe_persisted_content({"private_reasoning": "x"}))
        self.assertFalse(
            MODULE.unsafe_persisted_content(
                {"private_reasoning_persisted": False, "policy_sha256": "a" * 64}
            )
        )


if __name__ == "__main__":
    unittest.main()
