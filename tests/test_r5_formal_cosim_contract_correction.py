from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class R5FormalCosimContractCorrectionTests(unittest.TestCase):
    def test_public_contracts_are_typed_and_positive_depths(self) -> None:
        contracts = sorted(
            (ROOT / "configs/r5/formal_campaign/cosim_contracts").glob("*.json")
        )
        self.assertEqual(len(contracts), 2)
        for path in contracts:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["schema_version"], 2)
            self.assertEqual(value["kind"], "public_differential_self_check_v1")
            self.assertEqual(value["candidate_mismatch_returncodes"], [1])
            self.assertTrue(value["cosim_interface_depths"])
            self.assertTrue(
                all(depth > 0 for depth in value["cosim_interface_depths"].values())
            )

    def test_baseline_args_forward_runtime_contract(self) -> None:
        from scripts.r5_history_acquisition import _baseline_args

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("source.cpp", "public.cpp", "hidden.cpp", "contract.json"):
                (root / name).write_text("{}" if name.endswith(".json") else "", encoding="utf-8")
            args = _baseline_args(
                repo=root,
                case={
                    "paths": {
                        "source": "source.cpp",
                        "public_test": "public.cpp",
                        "hidden_test": "hidden.cpp",
                    },
                    "top": "top",
                },
                runtime={
                    "model": "deepseek-flash",
                    "family": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "api_key_env": "DEEPSEEK_API_KEY",
                },
                output=root / "output",
                run_id="contract-forwarding",
                public_test_contract="contract.json",
            )
            self.assertEqual(
                args.public_test_contracts_provided,
                [(root / "contract.json").resolve()],
            )

    def test_correction_plan_preserves_parent_and_claim_limits(self) -> None:
        plan = json.loads(
            (ROOT / "configs/r5/formal_campaign/cosim_contract_correction_plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(plan["claim_limits"]["parent_campaign_is_preserved"])
        self.assertTrue(plan["claim_limits"]["corrective_replication_is_posthoc"])
        self.assertTrue(
            plan["claim_limits"]["corrective_replication_is_not_a_new_future_holdout"]
        )
        self.assertFalse(plan["invariants"]["arms_changed"])
        self.assertFalse(plan["defect"]["sample_specific_error_rule_added"])


if __name__ == "__main__":
    unittest.main()
