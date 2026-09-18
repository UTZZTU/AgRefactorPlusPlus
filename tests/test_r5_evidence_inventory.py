from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_evidence_inventory",
    ROOT / "scripts" / "r5_evidence_inventory.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R5EvidenceInventoryTests(unittest.TestCase):
    def test_authoritative_r5_consumption_is_not_reset(self) -> None:
        inventory = MODULE.build_inventory(())
        ledger = inventory["r5_budget_ledger"]
        self.assertEqual(ledger["provider_cap"], 500)
        self.assertEqual(ledger["vitis_cap"], 500)
        self.assertEqual(ledger["consumed_provider_calls"], 1)
        self.assertEqual(ledger["consumed_vitis_launches"], 0)
        self.assertEqual(ledger["remaining_provider_calls"], 499)
        self.assertEqual(ledger["remaining_vitis_launches"], 500)


if __name__ == "__main__":
    unittest.main()
