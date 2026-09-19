from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_formal_future_freeze",
    ROOT / "scripts" / "r5_formal_future_freeze.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R5FormalFutureFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = json.loads(
            (ROOT / "configs/r5/formal_campaign/future_plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.commit = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
        ).strip()

    def test_real_future_plan_is_structurally_valid(self) -> None:
        records, budget = MODULE.validate_plan(
            ROOT,
            self.plan,
            commit=self.commit,
        )
        self.assertEqual(
            [item["case_id"] for item in records],
            ["Pointer_E2_hash_table", "Pointer_E3_double_pointer"],
        )
        self.assertEqual(
            {item["control_role"] for item in records},
            {"positive", "inapplicable_or_confusable"},
        )
        self.assertEqual(budget["provider_calls_before"], 140)

    def test_observed_pilot_case_cannot_reenter_future(self) -> None:
        value = deepcopy(self.plan)
        value["future_cases"][0]["case_id"] = "Exception_E3_Turbo_Encoder"
        with self.assertRaisesRegex(ValueError, "already observed"):
            MODULE.validate_plan(ROOT, value, commit=self.commit)

    def test_public_hidden_identity_cannot_collapse(self) -> None:
        value = deepcopy(self.plan)
        value["future_cases"][0]["hidden_test"] = value["future_cases"][0][
            "public_test"
        ]
        with self.assertRaisesRegex(ValueError, "identical"):
            MODULE.validate_plan(ROOT, value, commit=self.commit)

    def test_formal_budget_matches_two_case_protocol(self) -> None:
        state = {
            "R4_ACCEPTED": True,
            "R5_ACCEPTED": False,
            "R6_STARTED": False,
            "R5_REAL_CAMPAIGN_ALLOWED": False,
            "R5_PROVIDER_CALL_HARD_CAP": 500,
            "R5_VITIS_LAUNCH_HARD_CAP": 500,
            "R5_CONSUMED_PROVIDER_CALLS": 140,
            "R5_CONSUMED_VITIS_LAUNCHES": 67,
        }
        result = MODULE.validate_budget(state, self.plan["budget"])
        self.assertEqual(result["provider_upper_bound"], 120)
        self.assertEqual(result["vitis_upper_bound"], 144)
        self.assertGreater(result["provider_recovery_reserve"], 0)
        self.assertGreater(result["vitis_recovery_reserve"], 0)

    def test_stale_budget_is_rejected(self) -> None:
        state = {
            "R4_ACCEPTED": True,
            "R5_ACCEPTED": False,
            "R6_STARTED": False,
            "R5_REAL_CAMPAIGN_ALLOWED": False,
            "R5_PROVIDER_CALL_HARD_CAP": 500,
            "R5_VITIS_LAUNCH_HARD_CAP": 500,
            "R5_CONSUMED_PROVIDER_CALLS": 141,
            "R5_CONSUMED_VITIS_LAUNCHES": 67,
        }
        with self.assertRaisesRegex(ValueError, "authoritative ledger"):
            MODULE.validate_budget(state, self.plan["budget"])


if __name__ == "__main__":
    unittest.main()
