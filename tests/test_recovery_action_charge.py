from concurrent.futures import ThreadPoolExecutor
import unittest

from agrefactor.recovery import (
    RecoveryBudgetBlockedError,
    RecoveryDeniedError,
    RecoveryLedger,
    RecoveryLimits,
    RepairActionCharge,
    RepairComponentBucket,
    RepairComponentLimits,
)


def make_charge(
    action_id,
    *components,
    scope_id="case-1",
    authorization_ref="decision-1",
):
    return RepairActionCharge(
        scope_id=scope_id,
        logical_action_id=action_id,
        components=components or (RepairComponentBucket.CANDIDATE,),
        authorization_ref=authorization_ref,
    )


class RepairActionChargeTests(unittest.TestCase):
    def test_component_limits_match_confirmed_lightweight_baseline(self):
        limits = RepairComponentLimits()
        self.assertEqual(limits.candidate, 3)
        self.assertEqual(limits.testbench_public, 3)
        self.assertEqual(limits.testbench_hidden_generation, 3)
        self.assertEqual(limits.stub_public, 3)
        self.assertEqual(limits.stub_hidden_generation, 3)
        self.assertEqual(limits.total_actions, 15)
        self.assertEqual(RecoveryLimits().total_recovery_actions, 5)

    def test_dispatch_commits_each_component_once_and_is_idempotent(self):
        ledger = RecoveryLedger()
        charge = make_charge(
            "action-1",
            RepairComponentBucket.TESTBENCH_PUBLIC,
            RepairComponentBucket.STUB_PUBLIC,
        )
        reservation = ledger.reserve_component_action(charge)

        ledger.commit_dispatched_action(reservation)
        ledger.commit_dispatched_action(reservation)
        snapshot = ledger.component_action_snapshot("case-1")

        self.assertEqual(len(snapshot["actions"]), 1)
        self.assertEqual(snapshot["actions"][0]["status"], "committed")
        self.assertEqual(
            snapshot["actions"][0]["charge"]["components"],
            ["stub_public", "testbench_public"],
        )
        self.assertEqual(
            [event["event"] for event in snapshot["events"]],
            ["reserved", "committed"],
        )

    def test_release_before_dispatch_frees_all_reserved_buckets(self):
        ledger = RecoveryLedger(
            component_limits=RepairComponentLimits(
                testbench_public=1,
                stub_public=1,
                total_actions=1,
            )
        )
        first = ledger.reserve_component_action(
            make_charge(
                "action-1",
                RepairComponentBucket.TESTBENCH_PUBLIC,
                RepairComponentBucket.STUB_PUBLIC,
            )
        )
        ledger.release_component_action(first)
        ledger.release_component_action(first)

        second = ledger.reserve_component_action(
            make_charge(
                "action-2",
                RepairComponentBucket.TESTBENCH_PUBLIC,
                RepairComponentBucket.STUB_PUBLIC,
            )
        )
        ledger.commit_dispatched_action(second)
        self.assertEqual(
            ledger.component_action_snapshot("case-1")["actions"][-1]["status"],
            "committed",
        )

    def test_component_and_global_limits_are_scoped_per_case(self):
        ledger = RecoveryLedger(
            component_limits=RepairComponentLimits(
                candidate=1,
                total_actions=1,
            )
        )
        first = ledger.reserve_component_action(make_charge("action-1"))
        ledger.commit_dispatched_action(first)
        with self.assertRaises(RecoveryBudgetBlockedError):
            ledger.reserve_component_action(make_charge("action-2"))

        other_case = ledger.reserve_component_action(
            make_charge("action-1", scope_id="case-2")
        )
        ledger.commit_dispatched_action(other_case)
        self.assertEqual(len(ledger.committed_component_action_charges("case-1")), 1)
        self.assertEqual(len(ledger.committed_component_action_charges("case-2")), 1)

    def test_public_and_hidden_generation_use_separate_buckets(self):
        ledger = RecoveryLedger(
            component_limits=RepairComponentLimits(
                testbench_public=1,
                testbench_hidden_generation=1,
                total_actions=2,
            )
        )
        public = ledger.reserve_component_action(
            make_charge("public-1", RepairComponentBucket.TESTBENCH_PUBLIC)
        )
        hidden_generation = ledger.reserve_component_action(
            make_charge(
                "hidden-1",
                RepairComponentBucket.TESTBENCH_HIDDEN_GENERATION,
            )
        )
        ledger.commit_dispatched_action(public)
        ledger.commit_dispatched_action(hidden_generation)
        with self.assertRaises(RecoveryBudgetBlockedError):
            ledger.reserve_component_action(
                make_charge("public-2", RepairComponentBucket.TESTBENCH_PUBLIC)
            )

    def test_restore_is_idempotent_and_overflow_is_atomic(self):
        source = RecoveryLedger()
        charge = make_charge("action-1")
        reservation = source.reserve_component_action(charge)
        source.commit_dispatched_action(reservation)

        restored = RecoveryLedger()
        committed = source.committed_component_action_charges()
        restored.restore_committed_component_actions(committed)
        restored.restore_committed_component_actions(committed)
        self.assertEqual(restored.committed_component_action_charges(), committed)

        limited = RecoveryLedger(
            component_limits=RepairComponentLimits(candidate=1, total_actions=2)
        )
        with self.assertRaises(RecoveryBudgetBlockedError):
            limited.restore_committed_component_actions(
                (
                    make_charge("action-a"),
                    make_charge("action-b"),
                )
            )
        self.assertEqual(limited.component_action_snapshot()["actions"], [])

    def test_duplicate_action_ids_cannot_be_reserved_twice(self):
        ledger = RecoveryLedger()
        charge = make_charge("action-1")
        ledger.reserve_component_action(charge)
        with self.assertRaisesRegex(RecoveryDeniedError, "already_reserved"):
            ledger.reserve_component_action(charge)
        with self.assertRaisesRegex(ValueError, "different charge"):
            ledger.reserve_component_action(
                make_charge("action-1", RepairComponentBucket.TESTBENCH_PUBLIC)
            )

    def test_concurrent_reservations_cannot_exceed_a_component_or_global_limit(self):
        ledger = RecoveryLedger(
            component_limits=RepairComponentLimits(candidate=3, total_actions=3)
        )

        def reserve(index):
            try:
                return ledger.reserve_component_action(make_charge(f"action-{index}"))
            except RecoveryBudgetBlockedError:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            reservations = list(pool.map(reserve, range(12)))

        self.assertEqual(sum(item is not None for item in reservations), 3)
        snapshot = ledger.component_action_snapshot("case-1")
        self.assertEqual(len(snapshot["actions"]), 3)
        self.assertEqual(sum(item["accepted"] for item in snapshot["events"]), 3)


if __name__ == "__main__":
    unittest.main()
