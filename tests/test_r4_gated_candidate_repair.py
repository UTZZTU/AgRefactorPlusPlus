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
from agrefactor.recovery.r4_budget import (
    build_r4_reserve_plan,
    record_r4_budget_reservation,
)
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.runtime.budget import BudgetManager


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


def producer_artifacts():
    policy = {
        "schema_version": 1,
        "status": "allowed",
        "reason_code": "candidate_repair_allowed",
        "action": "repair",
        "role": "candidate",
        "stage": "csynth",
        "evidence_view": "agent_safe",
        "owner_authority": "llm_advisory",
        "lineage_id": "run-1",
    }
    ledger = {**policy, "sequence": 1, "count_key": "run-1", "accepted": True}
    plan = build_r4_reserve_plan(
        public_csim=False,
        csynth=True,
        public_cosim=False,
        hidden_evaluation=False,
        tool_calls=2,
        compile_calls=1,
        wall_time_s=100,
    )
    budget = BudgetManager()
    before = budget.snapshot().to_dict()
    plan.ensure_available(budget)
    reservation = record_r4_budget_reservation(
        plan,
        budget_before=before,
        budget_after_admission=budget.snapshot().to_dict(),
    ).to_dict()
    return policy, ledger, reservation


def execution(*, enabled: bool = True, kill: bool = False) -> R4ExecutionInput:
    policy, ledger, reservation = producer_artifacts()
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
        policy_decision_id=canonical_artifact_sha256(policy),
        budget_reservation_id=reservation["reservation_id"],
        ledger_reservation_id=canonical_artifact_sha256(ledger),
        deterministic_terminal_ref=digest("terminal"),
    )
    return R4ExecutionInput(
        authorization=auth,
        canary=canary(enabled=enabled),
        kill_switch=R4KillSwitchState(
            active=kill,
            trigger="test" if kill else None,
        ),
        execution_identity=IDENTITY,
        advisory={"accepted": False, "owner_authority": "llm_advisory"},
        candidate=CANDIDATE,
        original=ORIGINAL,
        testbench_hashes={"public": digest("public-tb")},
        route_fingerprint=digest("terminal"),
        policy_decision=policy,
        ledger_event=ledger,
        budget_reservation=reservation,
    )


def validation(*, passed: bool) -> dict:
    return {
        "passed": passed,
        "full_prefix": True,
        "prefix_executed_to_terminal": True,
        "fresh_validation": True,
        "validation_id": "validation-1",
        "testbench_hashes_after": {"public": digest("public-tb")},
    }


def clean_audit(*, attributable: bool = False, environment_excluded: bool = False):
    return {
        "status": "clean",
        "has_errors": False,
        "failure_attributable": attributable,
        "environment_excluded": environment_excluded,
    }


class R4GateContractTests(unittest.TestCase):
    def test_disabled_canary_abstains_without_callbacks(self):
        controller = R4CandidateRepairController()
        result = controller.run(
            execution(enabled=False),
            mutate_candidate=lambda _: self.fail(),
            validate_candidate=lambda _: {},
            audit=lambda _: {},
        )
        self.assertEqual(result.outcome, R4Outcome.ABSTAINED)
        self.assertEqual(result.provider_call_count, 0)

    def test_gate_and_trust_are_required_by_authorization(self):
        values = execution().authorization.to_dict(include_id=False)
        with self.assertRaises(ValueError):
            R4CandidateRepairAuthorization(**{**values, "gate_decision": "reject"})
        with self.assertRaises(ValueError):
            R4CandidateRepairAuthorization(
                **{**values, "pattern_lifecycle": "Provisional"}
            )

    def test_authorization_hash_is_canonical_and_immutable(self):
        request = execution()
        self.assertEqual(len(request.authorization.authorization_id), 64)
        with self.assertRaises(ValueError):
            R4CandidateRepairAuthorization(
                **{
                    **request.authorization.to_dict(),
                    "authorization_id": digest("stale"),
                }
            )

    def test_producer_hash_mismatch_is_rejected(self):
        request = execution()
        with self.assertRaises(ValueError):
            R4ExecutionInput(
                **{
                    name: getattr(request, name)
                    for name in request.__dataclass_fields__
                    if name != "policy_decision"
                },
                policy_decision={**request.policy_decision, "status": "denied"},
            )

    def test_pre_provider_kill_switch_blocks_provider(self):
        controller = R4CandidateRepairController()
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: self.fail(),
            validate_candidate=lambda _: {},
            audit=lambda _: {},
            kill_switch_reader=lambda: R4KillSwitchState(
                active=True,
                trigger="test",
            ),
        )
        self.assertEqual(result.outcome, R4Outcome.ABSTAINED)
        self.assertEqual(result.provider_call_count, 0)

    def test_valid_run_requires_full_prefix_and_independent_audit(self):
        controller = R4CandidateRepairController()
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: "void kernel(int *x) { *x = 2; }",
            validate_candidate=lambda _: validation(passed=True),
            audit=lambda _: clean_audit(),
        )
        self.assertEqual(result.outcome, R4Outcome.VERIFIED_POSITIVE)
        self.assertEqual(result.mutation_count, 1)

    def test_attributed_failure_is_verified_negative(self):
        controller = R4CandidateRepairController()
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: "changed",
            validate_candidate=lambda _: validation(passed=False),
            audit=lambda _: clean_audit(
                attributable=True,
                environment_excluded=True,
            ),
        )
        self.assertEqual(result.outcome, R4Outcome.VERIFIED_NEGATIVE)

    def test_unattributed_failure_is_inconclusive(self):
        controller = R4CandidateRepairController()
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: "changed",
            validate_candidate=lambda _: validation(passed=False),
            audit=lambda _: clean_audit(),
        )
        self.assertEqual(result.outcome, R4Outcome.INCONCLUSIVE)
        self.assertEqual(result.reasons, ("negative_attribution_incomplete",))

    def test_auditor_contradiction_is_inconclusive(self):
        controller = R4CandidateRepairController()
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: "changed",
            validate_candidate=lambda _: validation(passed=False),
            audit=lambda _: {"status": "contradiction", "has_errors": True},
        )
        self.assertEqual(result.outcome, R4Outcome.INCONCLUSIVE)

    def test_testbench_identity_change_is_invalid_and_quarantined(self):
        controller = R4CandidateRepairController()
        changed = validation(passed=False)
        changed["testbench_hashes_after"] = {"public": digest("changed")}
        result = controller.run(
            execution(),
            mutate_candidate=lambda _: "changed",
            validate_candidate=lambda _: changed,
            audit=lambda _: clean_audit(),
        )
        self.assertEqual(result.outcome, R4Outcome.INVALID_EVIDENCE)
        self.assertIsNotNone(result.quarantine)

    def test_one_attempt_cap(self):
        controller = R4CandidateRepairController()
        kwargs = {
            "mutate_candidate": lambda _: "changed",
            "validate_candidate": lambda _: validation(passed=False),
            "audit": lambda _: clean_audit(),
        }
        controller.run(execution(), **kwargs)
        result = controller.run(execution(), **kwargs)
        self.assertEqual(result.outcome, R4Outcome.INVALID_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
