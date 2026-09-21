from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inheritance_stage2_preflight.py"
SPEC = importlib.util.spec_from_file_location("inheritance_stage2_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InheritanceStage2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol_path = (
            ROOT / "configs" / "inheritance" / "stage2" / "protocol.json"
        )
        self.protocol = MODULE.load_object(self.protocol_path)

    def test_protocol_is_frozen_ordinary_refactor(self) -> None:
        MODULE.verify_protocol(ROOT, self.protocol)
        self.assertEqual(
            [case["case_id"] for case in self.protocol["cases"]],
            ["dfs", "mergesort", "ahocorasick", "strassen"],
        )
        self.assertFalse(self.protocol["execution"]["r2_r4_enabled"])
        self.assertFalse(self.protocol["execution"]["memory_enabled"])
        self.assertFalse(self.protocol["execution"]["optimize_enabled"])

    def test_runtime_contracts_are_typed_and_nonempty(self) -> None:
        for case in self.protocol["cases"]:
            contract = json.loads((ROOT / case["contract_path"]).read_text())
            self.assertEqual(contract["schema_version"], 2)
            self.assertEqual(
                contract["kind"], "public_differential_self_check_v1"
            )
            self.assertEqual(contract["candidate_mismatch_returncodes"], [1])
            self.assertTrue(contract["cosim_interface_depths"])

    def test_public_oracles_bind_both_tops_and_forbid_future_data(self) -> None:
        for case in self.protocol["cases"]:
            text = (ROOT / case["public_test_path"]).read_text(encoding="utf-8")
            self.assertIn("process_top(", text)
            self.assertIn("process_top_hls(", text)
            self.assertIn(case["public_marker"], text)
            lowered = text.lower()
            self.assertNotIn("hidden", lowered)
            self.assertNotIn("future", lowered)

    def test_ahocorasick_fixed_length_matches_the_frozen_input(self) -> None:
        case = next(
            item for item in self.protocol["cases"] if item["case_id"] == "ahocorasick"
        )
        for key in ("source_oracle_path", "public_test_path"):
            text = (ROOT / case[key]).read_text(encoding="utf-8")
            self.assertIn('"he%she%his%hers%"', text)
            self.assertRegex(text, r"(?:substring_length|source_length) = 16;")

    def test_wrong_asset_hash_fails_closed(self) -> None:
        altered = json.loads(json.dumps(self.protocol))
        altered["cases"][0]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "frozen asset hash mismatch"):
            MODULE.verify_protocol(ROOT, altered)

    def test_preflight_refuses_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            (output / "occupied").write_text("x", encoding="utf-8")
            self.assertTrue(any(output.iterdir()))


if __name__ == "__main__":
    unittest.main()
