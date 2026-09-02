from __future__ import annotations

import hashlib
import unittest

from agrefactor.recovery.gated_candidate_repair import (
    R4CanaryManifest,
    R4CandidateRepairAuthorization,
    R4CandidateRepairController,
    R4ExecutionInput,
    R4KillSwitchState,
    R4Outcome,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


ORIGINAL = "void kernel(int *x) { *x = 1; }"
CANDIDATE = ORIGINAL
IDENTITY = {
    "identity_complete": True,
    "hidden_input_count": 0,
    "secret_present": False,
    "case_id": "case-1",
    "source_sha256": digest(ORIGINAL),
    "target_identity": "target-a",
    "toolchain_identity": "vitis-2023.2",
    "parser_identity": "parser-1",
    "model_identity": "deepseek-v4-flash",
    "prompt_sha256": digest("prompt"),
    "stage": "csynth",
    "identity_complete": True,
    "hidden_input_count": 0,
    "secret_present": False,
}


def canary(*, enabled: bool = True) -> R4CanaryManifest:
    return R4CanaryManifest(
        manifest_id="canary-1",
        manifest_sha256=digest("canary"),
        enabled=enabled,
        operator_enabled=enabled,
        case_ids=("case-1",),
        source_sha256=digest(ORIGINAL),
        target_identity="target-a",
        toolchain_identity="vitis-2023.2",
        parser_identity="parser-1",
        model_identity="deepseek-v4-flash",
        prompt_sha256=digest("prompt"),
        allowed_stage="csynth",
        expires_at="2099-01-01T00:00:00Z",
    )


def execution(*, enabled: bool = True, kill: bool = False) -> R4ExecutionInput:
    auth = R4CandidateRepairAuthorization(
        run_id="run-1",
        event_ref="event-1",
        advisory_id="advisory-1",
        gate_decision="accept",
        pattern_lifecycle="Trusted",
        gate_contract_hash=digest("gate"),
        revision_sha256=digest("revision"),
        canary_manifest_sha256=digest("canary"),
        before_candidate_sha256=digest(CANDIDATE),
        policy_decision_id="policy-1",
        budget_reservation_id="budget-1",
        deterministic_terminal_ref="terminal-1",
    )
    return R4ExecutionInput(
        authorization=auth,
        canary=canary(enabled=enabled),
        kill_switch=R4KillSwitchState(active=kill, trigger="test" if kill else None),
        execution_identity=IDENTITY,
        advisory={"accepted": False, "owner_authority": "llm_advisory"},
        candidate=CANDIDATE,
        original=ORIGINAL,
        testbench_hashes={"public": digest("public-tb")},
        route_fingerprint=digest("route"),
    )


class R4GateContractTests(unittest.TestCase):
    def test_disabled_canary_abstains_without_callbacks(self):
        controller = R4CandidateRepairController()
        result = controller.run(execution(enabled=False), mutate_candidate=lambda _: self.fail(), validate_candidate=lambda _: {}, audit=lambda _: True)
        self.assertEqual(result.outcome, R4Outcome.ABSTAINED)
        self.assertEqual(result.provider_call_count, 0)

    def test_gate_and_trust_are_required_by_authorization(self):
        with self.assertRaises(ValueError):
            R4CandidateRepairAuthorization("r", "e", "a", "reject", "Trusted", digest("g"), digest("r"), digest("c"), digest(CANDIDATE), "p", "b", "t")
        with self.assertRaises(ValueError):
            R4CandidateRepairAuthorization("r", "e", "a", "accept", "Provisional", digest("g"), digest("r"), digest("c"), digest(CANDIDATE), "p", "b", "t")

    def test_authorization_hash_is_canonical_and_immutable(self):
        request = execution()
        self.assertEqual(len(request.authorization.authorization_id), 64)
        with self.assertRaises(ValueError):
            R4CandidateRepairAuthorization(**{**request.authorization.to_dict(), "authorization_id": digest("stale")})

    def test_pre_provider_kill_switch_blocks_provider(self):
        controller = R4CandidateRepairController()
        result = controller.run(execution(), mutate_candidate=lambda _: self.fail(), validate_candidate=lambda _: {}, audit=lambda _: True, kill_switch_reader=lambda: R4KillSwitchState(active=True, trigger="test"))
        self.assertEqual(result.outcome, R4Outcome.ABSTAINED)
        self.assertEqual(result.provider_call_count, 0)

    def test_valid_run_requires_full_prefix_and_independent_audit(self):
        controller = R4CandidateRepairController()
        proposed = "void kernel(int *x) { *x = 2; }"
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: proposed,
            validate_candidate=lambda _: {"passed": True, "full_prefix": True, "validation_id": "validation-1", "testbench_hashes_after": {"public": digest("public-tb")}},
            audit=lambda artifact: artifact["authorization_id"] == execution().authorization.authorization_id,
        )
        self.assertEqual(result.outcome, R4Outcome.VERIFIED_POSITIVE)
        self.assertEqual(result.mutation_count, 1)

    def test_validation_failure_is_not_positive(self):
        controller = R4CandidateRepairController()
        result = controller.run(execution(), mutate_candidate=lambda _: "changed", validate_candidate=lambda _: {"passed": False, "full_prefix": True, "validation_id": "v", "testbench_hashes_after": {"public": digest("public-tb")}}, audit=lambda _: True)
        self.assertEqual(result.outcome, R4Outcome.VERIFIED_NEGATIVE)
        self.assertFalse(result.accepted)

    def test_testbench_identity_change_is_invalid_and_quarantined(self):
        controller = R4CandidateRepairController()
        result = controller.run(execution(), mutate_candidate=lambda _: "changed", validate_candidate=lambda _: {"passed": False, "full_prefix": True, "testbench_hashes_after": {"public": digest("changed")}}, audit=lambda _: True)
        self.assertEqual(result.outcome, R4Outcome.INVALID_EVIDENCE)
        self.assertIsNotNone(result.quarantine)

    def test_one_attempt_cap(self):
        controller = R4CandidateRepairController()
        kwargs = {"mutate_candidate": lambda _: "changed", "validate_candidate": lambda _: {"passed": False, "full_prefix": True, "testbench_hashes_after": {"public": digest("public-tb")}}, "audit": lambda _: True}
        controller.run(execution(), **kwargs)
        result = controller.run(execution(), **kwargs)
        self.assertEqual(result.outcome, R4Outcome.INVALID_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
