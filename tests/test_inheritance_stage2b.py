from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inheritance_stage2_preflight.py"
SPEC = importlib.util.spec_from_file_location("inheritance_stage2_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InheritanceStage2BTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol_path = ROOT / "configs" / "inheritance" / "stage2b" / "protocol.json"
        self.protocol = MODULE.load_object(self.protocol_path)

    def test_protocol_is_frozen_as_separate_stage2b_route(self) -> None:
        MODULE.verify_protocol(ROOT, self.protocol)
        self.assertEqual(self.protocol["route"], "V2.3-INHERITANCE-FIRST-STAGE-II-B")
        self.assertEqual(
            [case["case_id"] for case in self.protocol["cases"]],
            ["linkedlist", "strassen_break"],
        )
        self.assertEqual(self.protocol["budget"]["provider_calls_hard_cap"], 600)
        self.assertEqual(self.protocol["budget"]["vitis_launches_hard_cap"], 600)

    def test_public_oracles_bind_source_and_candidate(self) -> None:
        for case in self.protocol["cases"]:
            text = (ROOT / case["public_test_path"]).read_text(encoding="utf-8")
            self.assertIn("process_top(", text)
            self.assertIn("process_top_hls(", text)
            self.assertIn(case["public_marker"], text)
            self.assertNotIn("hidden", text.lower())
            self.assertNotIn("future", text.lower())

    def test_contracts_are_typed_and_nonempty(self) -> None:
        for case in self.protocol["cases"]:
            contract = json.loads((ROOT / case["contract_path"]).read_text())
            self.assertEqual(contract["schema_version"], 2)
            self.assertEqual(contract["kind"], "public_differential_self_check_v1")
            self.assertEqual(contract["candidate_mismatch_returncodes"], [1])
            self.assertTrue(contract["cosim_interface_depths"])


if __name__ == "__main__":
    unittest.main()
