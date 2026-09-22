from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import inheritance_stage3 as stage3


ROOT = Path(__file__).resolve().parents[1]


class InheritanceStage3Tests(unittest.TestCase):
    def test_prepare_freezes_expected_internal_cohort(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "protocol.json"
            protocol = stage3.prepare_protocol(
                ROOT,
                ROOT / "configs" / "inheritance" / "inheritance_manifest_v1.json",
                output,
            )
            self.assertEqual(len(protocol["cases"]), 56)
            self.assertEqual(
                protocol["cohort_counts"],
                {"app": 13, "c2hlsc": 9, "hlsrewritter": 20, "leetcode": 10, "opt": 4},
            )
            self.assertEqual(protocol["execution"], stage3.EXECUTION)
            self.assertEqual(protocol["budget"]["provider_calls_hard_cap"], 600)
            self.assertTrue(output.is_file())

    def test_forwarding_stub_preserves_linkage_and_arguments(self) -> None:
        declaration = 'extern "C" int work_hls(const int input[8], int *output, int count)'
        stub = stage3.synthesize_forwarding_stub(declaration, "work", "work_hls")
        self.assertIn('extern "C" int work(', stub)
        self.assertIn("return work(input, output, count);", stub)

    def test_contract_only_assigns_depths_to_pointer_or_array_ports(self) -> None:
        declaration = "void work_hls(const int input[8], int *output, int count)"
        contract = stage3._contract(declaration, "work_hls")
        self.assertEqual(contract["schema_version"], 2)
        self.assertEqual(contract["cosim_interface_depths"], {"input": 8, "output": 4096})
        self.assertNotIn("count", contract["cosim_interface_depths"])

    def test_scalar_contract_uses_schema_v1(self) -> None:
        contract = stage3._contract("int work_hls(int value)", "work_hls")
        self.assertEqual(
            contract,
            {
                "candidate_mismatch_returncodes": [1],
                "kind": "public_differential_self_check_v1",
                "schema_version": 1,
            },
        )

    def test_safe_json_rejects_private_reasoning(self) -> None:
        with self.assertRaises(stage3.Stage3Error):
            stage3._assert_safe_json({"private_reasoning": "no"})

    def test_committed_protocol_matches_builder(self) -> None:
        protocol_path = ROOT / "configs" / "inheritance" / "stage3" / "protocol.json"
        if not protocol_path.exists():
            self.skipTest("protocol is generated during Stage III preparation")
        committed = json.loads(protocol_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            generated = stage3.prepare_protocol(
                ROOT,
                ROOT / "configs" / "inheritance" / "inheritance_manifest_v1.json",
                Path(temporary) / "protocol.json",
            )
        self.assertEqual(committed, generated)


if __name__ == "__main__":
    unittest.main()
