from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_external_adapter_smoke",
    ROOT / "scripts" / "r5_1_external_adapter_smoke.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R51ExternalAdapterSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "external_adapter_smoke_plan.json"
        )

    def test_checked_plan_is_valid_and_one_case_per_source(self) -> None:
        MODULE.validate_plan(self.plan)
        source_ids = [case["source_id"] for case in self.plan["cases"]]
        self.assertEqual(len(source_ids), 4)
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertEqual(self.plan["invariants"]["provider_calls"], 0)
        self.assertEqual(self.plan["invariants"]["vitis_launches"], 0)

    def test_path_escape_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["cases"][0]["source_files"][0]["path"] = "../secret"
        with self.assertRaises(ValueError):
            MODULE.validate_plan(plan)

    def test_unenforceable_pass_markers_are_rejected(self) -> None:
        invalid_markers = ["", "   ", "PASS\nINJECT", "PASS\rINJECT", "P" * 257]
        for marker in invalid_markers:
            with self.subTest(marker=marker):
                plan = copy.deepcopy(self.plan)
                plan["cases"][0]["pass_marker"] = marker
                with self.assertRaises(ValueError):
                    MODULE.validate_plan(plan)

    def test_unlicensed_source_cannot_be_admitted(self) -> None:
        for case in self.plan["cases"]:
            if case["license"] == "NOASSERTION":
                self.assertEqual(case["decision"], "external-only")

    def test_local_adapter_hashes_are_frozen(self) -> None:
        for case in self.plan["cases"]:
            if case["adapter_path"] is None:
                self.assertIsNone(case["adapter_sha256"])
                self.assertTrue(
                    any(item["role"] == "oracle" for item in case["source_files"])
                )
                continue
            path = ROOT / case["adapter_path"]
            self.assertEqual(MODULE.sha_file(path), case["adapter_sha256"])


if __name__ == "__main__":
    unittest.main()
