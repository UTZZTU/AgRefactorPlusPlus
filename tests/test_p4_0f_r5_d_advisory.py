import unittest

from agrefactor.recovery import (
    AdvisoryConfidence,
    AdvisoryOwner,
    AdvisoryRepairScope,
    DiagnosticAdvisory,
    DiagnosticAdvisoryRequest,
    validate_advisory_result,
)


def request(**overrides):
    payload = dict(
        stage="public_cosim",
        evidence_ids=("e1", "e2"),
        evidence_summary={"failure_kind": "ownership_unknown"},
        run_identity_complete=True,
        physical_tool_launched=True,
    )
    payload.update(overrides)
    return DiagnosticAdvisoryRequest(**payload)


def advisory(**overrides):
    payload = dict(
        suspected_owner=AdvisoryOwner.CANDIDATE,
        suspected_failure_class="stream_protocol_mismatch",
        evidence_refs=("e1",),
        repair_scope=AdvisoryRepairScope.CANDIDATE_ONLY,
        confidence=AdvisoryConfidence.HIGH,
    )
    payload.update(overrides)
    return DiagnosticAdvisory(**payload)


class AdvisoryTests(unittest.TestCase):
    def test_high_confidence_candidate_is_exploratory_only(self):
        item = advisory()
        self.assertTrue(item.exploratory_repair_eligible)
        self.assertFalse(item.to_dict()["accepted"])

    def test_testbench_advisory_not_exploratory(self):
        item = advisory(
            suspected_owner=AdvisoryOwner.TESTBENCH,
            repair_scope=AdvisoryRepairScope.TESTBENCH_ONLY,
        )
        self.assertFalse(item.exploratory_repair_eligible)

    def test_medium_confidence_not_exploratory(self):
        self.assertFalse(advisory(confidence=AdvisoryConfidence.MEDIUM).exploratory_repair_eligible)

    def test_abstention_requires_none_scope(self):
        with self.assertRaises(ValueError):
            advisory(abstain_reason="insufficient evidence")

    def test_valid_abstention(self):
        item = advisory(
            suspected_owner=AdvisoryOwner.UNKNOWN,
            repair_scope=AdvisoryRepairScope.NONE,
            confidence=AdvisoryConfidence.LOW,
            evidence_refs=(),
            abstain_reason="insufficient evidence",
        )
        self.assertFalse(item.exploratory_repair_eligible)

    def test_hidden_input_rejected(self):
        with self.assertRaises(ValueError):
            request(hidden_input_count=1)

    def test_secret_rejected(self):
        with self.assertRaises(ValueError):
            request(secret_present=True)

    def test_private_reasoning_rejected(self):
        with self.assertRaises(ValueError):
            request(private_reasoning_present=True)

    def test_foreign_evidence_reference_rejected(self):
        with self.assertRaises(ValueError):
            validate_advisory_result(request(), advisory(evidence_refs=("other",)))

    def test_incomplete_identity_rejected(self):
        with self.assertRaises(ValueError):
            validate_advisory_result(
                request(run_identity_complete=False), advisory()
            )


if __name__ == "__main__":
    unittest.main()
