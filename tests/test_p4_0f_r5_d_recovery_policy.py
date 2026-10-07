import unittest

from agrefactor.recovery import (
    RecoveryAction,
    RecoveryAuthority,
    RecoveryBudgetBlockedError,
    RecoveryDeniedError,
    RecoveryLedger,
    RecoveryLimits,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
    conservative_v1_policy,
    default_restart_reserve,
)


def req(*, action=RecoveryAction.REPAIR, role=RecoveryRole.CANDIDATE,
        stage=RecoveryStage.PUBLIC_CSIM, authority=RecoveryAuthority.DETERMINISTIC_PROVEN,
        mode="off", view="agent_safe", timeout_class=None, launched=True,
        complete=True, execution_complete=False):
    return RecoveryRequest(
        action=action,
        role=role,
        stage=stage,
        evidence_view=view,
        owner_authority=authority,
        lineage_id="task.lineage",
        advisory_mode=mode,
        timeout_class=timeout_class,
        physical_tool_launched=launched,
        evidence_complete=complete,
        execution_evidence_complete=execution_complete,
    )


class FakeBudget:
    def __init__(self, blocked=False):
        self.blocked = blocked
        self.calls = []

    def ensure_available(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.blocked:
            raise RuntimeError("blocked")


class RecoveryPolicyTests(unittest.TestCase):
    def test_candidate_public_csim_allowed(self):
        self.assertTrue(conservative_v1_policy().decide(req()).allowed)

    def test_candidate_public_cosim_allowed(self):
        self.assertTrue(conservative_v1_policy().decide(
            req(stage=RecoveryStage.PUBLIC_COSIM)
        ).allowed)

    def test_public_csynth_bounded_trial_requires_execution_evidence(self):
        decision = conservative_v1_policy().decide(req(
            stage=RecoveryStage.CSYNTH,
            authority=RecoveryAuthority.PUBLIC_CSYNTH_BOUNDED_TRIAL,
            complete=False,
            execution_complete=True,
        ))
        self.assertTrue(decision.allowed)
        self.assertEqual(
            decision.to_dict()["owner_authority"],
            "public_csynth_bounded_trial",
        )
        self.assertTrue(decision.to_dict()["execution_evidence_complete"])

    def test_public_csynth_trial_rejects_owner_proven_or_timeout(self):
        policy = conservative_v1_policy()
        self.assertFalse(policy.decide(req(
            stage=RecoveryStage.CSYNTH,
            authority=RecoveryAuthority.PUBLIC_CSYNTH_BOUNDED_TRIAL,
            complete=True,
            execution_complete=True,
        )).allowed)
        self.assertFalse(policy.decide(req(
            stage=RecoveryStage.CSYNTH,
            authority=RecoveryAuthority.PUBLIC_CSYNTH_BOUNDED_TRIAL,
            complete=False,
            execution_complete=True,
            timeout_class="candidate_deadlock",
        )).allowed)

    def test_legacy_public_reference_qualification_does_not_cover_csynth(self):
        decision = conservative_v1_policy().decide(req(
            stage=RecoveryStage.CSYNTH,
            authority=RecoveryAuthority.PUBLIC_REFERENCE_QUALIFIED,
        ))
        self.assertFalse(decision.allowed)

    def test_testbench_public_csim_allowed(self):
        self.assertTrue(conservative_v1_policy().decide(
            req(role=RecoveryRole.TESTBENCH)
        ).allowed)

    def test_testbench_public_cosim_allowed(self):
        self.assertTrue(conservative_v1_policy().decide(
            req(role=RecoveryRole.TESTBENCH, stage=RecoveryStage.PUBLIC_COSIM)
        ).allowed)

    def test_hidden_candidate_denied(self):
        self.assertFalse(conservative_v1_policy().decide(
            req(stage=RecoveryStage.HIDDEN)
        ).allowed)

    def test_hidden_testbench_denied(self):
        self.assertFalse(conservative_v1_policy().decide(
            req(role=RecoveryRole.TESTBENCH, stage=RecoveryStage.HIDDEN)
        ).allowed)

    def test_original_repair_denied(self):
        self.assertFalse(conservative_v1_policy().decide(
            req(role=RecoveryRole.ORIGINAL)
        ).allowed)

    def test_operator_full_repair_denied(self):
        self.assertFalse(conservative_v1_policy().decide(
            req(view="operator_full")
        ).allowed)

    def test_llm_advisory_repair_is_candidate_only(self):
        decision = conservative_v1_policy().decide(req(
            authority=RecoveryAuthority.LLM_ADVISORY,
            mode="candidate-only",
        ))
        self.assertTrue(decision.allowed)
        self.assertFalse(conservative_v1_policy().decide(req(
            authority=RecoveryAuthority.LLM_ADVISORY,
            mode="candidate-only",
            role=RecoveryRole.TESTBENCH,
        )).allowed)

    def test_unknown_owner_requires_review(self):
        decision = conservative_v1_policy().decide(req(
            authority=RecoveryAuthority.UNKNOWN,
        ))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status.value, "review_required")

    def test_candidate_timeout_requires_proven_class(self):
        self.assertFalse(conservative_v1_policy().decide(req(
            timeout_class="ownership_unknown"
        )).allowed)
        self.assertTrue(conservative_v1_policy().decide(req(
            timeout_class="candidate_deadlock"
        )).allowed)

    def test_testbench_timeout_requires_protocol_wait(self):
        self.assertFalse(conservative_v1_policy().decide(req(
            role=RecoveryRole.TESTBENCH,
            timeout_class="ownership_unknown",
        )).allowed)
        self.assertTrue(conservative_v1_policy().decide(req(
            role=RecoveryRole.TESTBENCH,
            timeout_class="public_testbench_protocol_wait",
        )).allowed)

    def test_ledger_enforces_candidate_stage_limit(self):
        for stage in (RecoveryStage.PUBLIC_CSIM, RecoveryStage.PUBLIC_COSIM):
            with self.subTest(stage=stage):
                ledger = RecoveryLedger()
                for _ in range(3):
                    ledger.reserve(req(stage=stage), restart_reserve={})
                with self.assertRaises(RecoveryDeniedError):
                    ledger.reserve(req(stage=stage), restart_reserve={})

    def test_ledger_enforces_candidate_total_across_stage_lanes(self):
        policy = RecoveryPolicy(limits=RecoveryLimits(
            refactor_candidate_repairs_total=2,
            candidate_public_csim_repairs=3,
            candidate_public_cosim_repairs=3,
        ))
        ledger = RecoveryLedger(policy)
        ledger.reserve(req(stage=RecoveryStage.PUBLIC_CSIM), restart_reserve={})
        ledger.reserve(req(stage=RecoveryStage.PUBLIC_COSIM), restart_reserve={})
        with self.assertRaises(RecoveryDeniedError):
            ledger.reserve(req(stage=RecoveryStage.CSYNTH), restart_reserve={})
        payload = ledger.to_dict()
        aggregate_key = "lineage:task.lineage:candidate:repairs_total"
        self.assertEqual(payload["counts"][aggregate_key], 2)
        self.assertEqual(payload["events"][-1]["limit_key"], aggregate_key)

    def test_ledger_checks_restart_budget(self):
        ledger = RecoveryLedger()
        budget = FakeBudget()
        ledger.reserve(req(), budget=budget, restart_reserve={"llm_calls": 1})
        self.assertEqual(budget.calls, [{"llm_calls": 1}])

        class Value:
            max_wall_time_s = 100.0

        class Usage:
            elapsed_s = 90.0

        class WallBudget(FakeBudget):
            limits = Value()
            active_reserve = None

            def snapshot(self):
                return Usage()

        with self.assertRaises(RecoveryBudgetBlockedError):
            RecoveryLedger().reserve(
                req(),
                budget=WallBudget(),
                restart_reserve={"wall_time_s": 20.0},
            )

    def test_restart_is_counted_separately(self):
        ledger = RecoveryLedger()
        ledger.record_validation_restart(
            lineage_id="task.lineage",
            stage=RecoveryStage.PUBLIC_CSIM,
            restart_reserve={},
        )
        payload = ledger.to_dict()
        self.assertEqual(payload["counts"]["lineage:task.lineage:validation_restart"], 1)
        self.assertEqual(payload["counts"].get("run:total_recovery_actions", 0), 0)

    def test_default_restart_reserve_has_full_public_chain(self):
        reserve = default_restart_reserve(RecoveryStage.PUBLIC_COSIM)
        self.assertEqual(reserve["llm_calls"], 1)
        self.assertEqual(reserve["csim_calls"], 1)
        self.assertEqual(reserve["csynth_calls"], 1)
        self.assertEqual(reserve["cosim_calls"], 1)
        self.assertGreaterEqual(reserve["wall_time_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
