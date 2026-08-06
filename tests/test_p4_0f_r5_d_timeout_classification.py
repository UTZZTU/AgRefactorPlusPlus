import unittest

from agrefactor.recovery import (
    TimeoutClass,
    TimeoutOwner,
    classify_public_timeout,
)


class TimeoutClassificationTests(unittest.TestCase):
    def test_non_timeout(self):
        result = classify_public_timeout({}, stage="public_csim")
        self.assertFalse(result.timed_out)

    def test_launch_timeout_is_infrastructure(self):
        result = classify_public_timeout(
            {"timed_out": True, "tool_launched": False},
            stage="public_csim",
        )
        self.assertEqual(result.owner, TimeoutOwner.INFRASTRUCTURE)
        self.assertFalse(result.repair_eligible)

    def test_toolchain_stall(self):
        result = classify_public_timeout(
            {"timed_out": True, "tool_launched": True, "toolchain_stall": True},
            stage="public_cosim",
        )
        self.assertEqual(result.timeout_class, TimeoutClass.TOOLCHAIN_STALL)
        self.assertEqual(result.owner, TimeoutOwner.TOOLCHAIN)

    def test_candidate_deadlock(self):
        result = classify_public_timeout(
            {"timed_out": True, "cosim_launched": True, "candidate_deadlock": True},
            stage="public_cosim",
        )
        self.assertEqual(result.owner, TimeoutOwner.CANDIDATE)
        self.assertTrue(result.repair_eligible)

    def test_candidate_stream_mismatch(self):
        result = classify_public_timeout(
            {"timeout": True, "csim_launched": True, "candidate_stream_mismatch": True},
            stage="public_csim",
        )
        self.assertEqual(result.timeout_class, TimeoutClass.CANDIDATE_STREAM_MISMATCH)

    def test_testbench_protocol_wait(self):
        result = classify_public_timeout(
            {"timed_out": True, "cosim_launched": True, "public_testbench_protocol_wait": True},
            stage="public_cosim",
        )
        self.assertEqual(result.owner, TimeoutOwner.TESTBENCH)
        self.assertTrue(result.repair_eligible)

    def test_unknown_complete_is_advisory_eligible(self):
        result = classify_public_timeout(
            {"timed_out": True, "tool_launched": True, "evidence_complete": True},
            stage="public_csim",
        )
        self.assertEqual(result.owner, TimeoutOwner.UNKNOWN)
        self.assertTrue(result.advisory_eligible)
        self.assertFalse(result.repair_eligible)

    def test_unknown_incomplete_not_advisory_eligible(self):
        result = classify_public_timeout(
            {"timed_out": True, "tool_launched": True},
            stage="public_csim",
        )
        self.assertFalse(result.advisory_eligible)

    def test_invalid_stage_rejected(self):
        with self.assertRaises(ValueError):
            classify_public_timeout({"timed_out": True}, stage="hidden")

    def test_round_trip_dict_is_typed(self):
        payload = classify_public_timeout(
            {"timed_out": True, "tool_launched": True},
            stage="public_cosim",
        ).to_dict()
        self.assertEqual(payload["schema_version"], 1)
        self.assertIn(payload["owner_authority"], {"unknown", "deterministic_proven"})


if __name__ == "__main__":
    unittest.main()
