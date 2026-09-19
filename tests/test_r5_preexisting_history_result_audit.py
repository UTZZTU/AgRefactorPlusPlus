from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_audit_preexisting_history_result",
    ROOT / "scripts" / "r5_audit_preexisting_history_result.py",
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class R5PreexistingHistoryResultAuditTests(unittest.TestCase):
    def test_safe_calibration_abstention_is_distinct_from_positive(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 1,
            "vitis_launches": 2,
            "r5_integration": {
                "status": "abstained",
                "reason": "r2_calibration_unverified",
                "calibration": {
                    "verified": False,
                    "reasons": ["confidence_label_not_calibrated"],
                },
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        shadow = {"advisory": {"confidence": "medium"}}
        value = AUDIT._verify_abstention(result, shadow)
        self.assertEqual(value["status"], "clean_safe_calibration_abstention")

    def test_high_confidence_cannot_be_relabelled_as_calibration_abstention(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 1,
            "vitis_launches": 2,
            "r5_integration": {
                "status": "abstained",
                "reason": "r2_calibration_unverified",
                "calibration": {
                    "verified": False,
                    "reasons": ["confidence_label_not_calibrated"],
                },
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryResultAuditError,
            "abstention invariants failed",
        ):
            AUDIT._verify_abstention(
                result,
                {"advisory": {"confidence": "high"}},
            )

    def test_private_payload_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryResultAuditError,
            "raw private field persisted",
        ):
            AUDIT._assert_privacy(
                {"nested": {"reasoning_content": "must not persist"}}
            )

    def test_high_confidence_pre_provider_failure_is_not_safe_abstention(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 1,
            "vitis_launches": 2,
            "r5_integration": {
                "status": "abstained",
                "reason": "r2_calibration_unverified",
                "calibration": {
                    "verified": False,
                    "reasons": ["confidence_label_not_calibrated"],
                },
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryResultAuditError,
            "abstention invariants failed",
        ):
            AUDIT._verify_abstention(
                result,
                {"advisory": {"confidence": "high"}},
            )


if __name__ == "__main__":
    unittest.main()
