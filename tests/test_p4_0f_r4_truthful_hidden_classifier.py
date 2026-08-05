from __future__ import annotations
import unittest
from agrefactor.product.run_output import _product_failure_fields, _validation_failure_fields


class TruthfulHiddenClassifierTests(unittest.TestCase):
    def _identity(self):
        return {
            "suites": [
                {"suite_id": "public", "evaluation_status": "passed", "reason_code": "cosim_passed"},
                {"suite_id": "hidden", "evaluation_status": "failed", "compile": {
                    "blocking": True, "failure_kind": "linkage_mismatch",
                    "failure_owner": "testbench", "route_action": "review_unknown",
                }},
            ]
        }

    def test_hidden_blocking_typed_evidence_precedes_previous_success(self):
        fields = _product_failure_fields(self._identity(), failed_stage="hidden",
                                         optimizer={"reason_code": "cosim_passed"}, accepted=False)
        self.assertEqual(fields["reason_code"], "linkage_mismatch")
        self.assertEqual(fields["failure_owner"], "testbench")
        self.assertEqual(fields["route_action"], "review_unknown")
        self.assertTrue(fields["review_required"])

    def test_hidden_without_unambiguous_safe_reason_is_unknown_safe(self):
        identity = {"hidden": {"compile": {"blocking": True, "failure_owner": "testbench"}}}
        fields = _validation_failure_fields(identity, failed_stage="hidden")
        self.assertIsNotNone(fields)
        self.assertEqual(fields["reason_code"], "hidden_validation_unknown")
        self.assertTrue(fields["review_required"])

    def test_conflicting_hidden_failure_kinds_do_not_guess(self):
        identity = {"hidden": {"records": [
            {"blocking": True, "failure_kind": "linkage_mismatch"},
            {"blocking": True, "failure_kind": "compile_contract_mismatch"},
        ]}}
        fields = _validation_failure_fields(identity, failed_stage="hidden")
        self.assertEqual(fields["reason_code"], "hidden_validation_unknown")

    def test_success_codes_are_never_failure_reasons(self):
        identity = {"hidden": {"blocking": True, "reason_code": "cosim_passed"}}
        fields = _validation_failure_fields(identity, failed_stage="hidden")
        self.assertEqual(fields["reason_code"], "hidden_validation_unknown")

    def test_failure_kind_precedes_other_reason_code(self):
        identity = {"hidden": {"blocking": True, "failure_kind": "linkage_mismatch",
                               "reason_code": "compile_failed"}}
        fields = _validation_failure_fields(identity, failed_stage="hidden")
        self.assertEqual(fields["reason_code"], "linkage_mismatch")

    def test_accepted_run_has_no_failure_fields(self):
        fields = _product_failure_fields(self._identity(), failed_stage=None,
                                         optimizer={"reason_code": "provider_persistent"}, accepted=True)
        self.assertEqual(fields, {"reason_code": None, "failure_owner": None,
                                  "route_action": None, "review_required": False})

    def test_optimize_keeps_existing_optimizer_reason_path(self):
        fields = _product_failure_fields({}, failed_stage="optimize",
                                         optimizer={"terminal_error": {"provider_reason_codes": ["provider_empty_final_content"]}},
                                         accepted=False)
        self.assertEqual(fields["reason_code"], "provider_empty_final_content")
        self.assertIsNone(fields["failure_owner"])


if __name__ == "__main__":
    unittest.main()
