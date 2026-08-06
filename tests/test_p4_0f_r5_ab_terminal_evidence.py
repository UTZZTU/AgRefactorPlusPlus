from __future__ import annotations

import unittest

from agrefactor.product.run_output import (
    _csynth_status,
    _validation_failure_fields,
)


class R5ABTerminalEvidenceTests(unittest.TestCase):
    def test_terminal_candidate_action_ignores_stale_unknown(self):
        identity = {
            "history": {
                "stage": "public",
                "blocking": True,
                "failure_kind": "ownership_unknown",
                "failure_owner": "unknown",
                "route_action": "review_unknown",
            },
            "validation": {
                "stage": "csynth",
                "blocking": True,
                "category": "unsupported_construct",
                "owner": "candidate",
                "next_action": "repair_candidate",
            },
        }
        fields = _validation_failure_fields(
            identity, failed_stage="csynth"
        )
        self.assertEqual(
            fields["reason_code"], "unsupported_construct"
        )
        self.assertEqual(fields["failure_owner"], "candidate")
        self.assertEqual(fields["route_action"], "repair_candidate")
        self.assertFalse(fields["review_required"])

    def test_csynth_failure_precedes_later_status_presence(self):
        self.assertEqual(
            _csynth_status(
                accepted=False,
                failed_stage="csynth",
                public_status="passed",
                hidden_status="not_run",
            ),
            "failed",
        )

    def test_conflicting_terminal_owner_fails_closed(self):
        identity = {
            "validation": [
                {
                    "stage": "csynth",
                    "blocking": True,
                    "failure_kind": "unsupported_construct",
                    "failure_owner": "candidate",
                    "route_action": "repair_candidate",
                },
                {
                    "stage": "csynth",
                    "blocking": True,
                    "failure_kind": "toolchain_failure",
                    "failure_owner": "toolchain",
                    "route_action": "fix_toolchain",
                },
            ]
        }
        fields = _validation_failure_fields(
            identity, failed_stage="csynth"
        )
        self.assertEqual(
            fields["reason_code"], "unknown_conflicting_evidence"
        )
        self.assertEqual(fields["failure_owner"], "unknown")
        self.assertEqual(fields["route_action"], "review_unknown")
        self.assertTrue(fields["review_required"])

    def test_cosim_item_owner_and_next_action_are_promoted(self):
        identity = {
            "validation": {
                "stage": "cosim",
                "blocking": True,
                "category": "functional_mismatch",
                "owner": "candidate",
                "next_action": "reject_candidate_no_repair",
            }
        }
        fields = _validation_failure_fields(
            identity, failed_stage="public_cosim"
        )
        self.assertEqual(
            fields["reason_code"], "functional_mismatch"
        )
        self.assertEqual(fields["failure_owner"], "candidate")
        self.assertEqual(
            fields["route_action"], "reject_candidate_no_repair"
        )

    def test_split_scopes_noncanonical_suite_ids(self):
        identity = {
            "suites": [
                {
                    "suite_id": "public-001",
                    "split": "public",
                    "compile": {
                        "blocking": True,
                        "failure_kind": "linkage_mismatch",
                        "failure_owner": "testbench",
                        "route_action": "review_unknown",
                    },
                }
            ]
        }
        fields = _validation_failure_fields(
            identity, failed_stage="public"
        )
        self.assertEqual(fields["reason_code"], "linkage_mismatch")
        self.assertEqual(fields["failure_owner"], "testbench")
        self.assertEqual(fields["route_action"], "review_unknown")

    def test_missing_terminal_evidence_is_unknown_safe(self):
        fields = _validation_failure_fields(
            {"history": {"reason_code": "cosim_passed"}},
            failed_stage="csynth",
        )
        self.assertEqual(
            fields["reason_code"], "csynth_validation_unknown"
        )
        self.assertIsNone(fields["failure_owner"])
        self.assertTrue(fields["review_required"])


if __name__ == "__main__":
    unittest.main()
