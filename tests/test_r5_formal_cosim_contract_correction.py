from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

from flow.new import _build_external_candidate_abi_instruction
from flow.tools.tb_optimizer import extract_hls_decl_from_testbench

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

    def test_depth_ports_are_declared_by_public_candidate_abi(self) -> None:
        cases = {
            "Pointer_E2_hash_table": "length_hls",
            "Pointer_E3_double_pointer": "trap_hls",
        }
        for case_id, candidate_top in cases.items():
            public = (
                ROOT / "configs/r5/oracle_adapters" / case_id / "public.cpp"
            ).read_text(encoding="utf-8")
            declaration = extract_hls_decl_from_testbench(public, candidate_top)
            self.assertTrue(declaration)
            contract = json.loads(
                (
                    ROOT / "configs/r5/formal_campaign/cosim_contracts"
                    / f"{case_id}.public.json"
                ).read_text(encoding="utf-8")
            )
            for port in contract["cosim_interface_depths"]:
                self.assertRegex(declaration, rf"\b{re.escape(port)}\b")

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
        self.assertTrue(plan["defect"]["public_abi_is_authoritative"])
        self.assertTrue(
            plan["invariants"]["prompt_changed_only_to_bind_public_abi"]
        )

    def test_external_candidate_prompt_binds_exact_public_abi(self) -> None:
        declaration = "int length_hls(char* value);"
        instruction = _build_external_candidate_abi_instruction(
            public_hls_decl=declaration,
            candidate_name="length_hls",
            external_tb_instruction=None,
        )
        self.assertIn(declaration, instruction)
        self.assertIn("parameter count", instruction)
        self.assertIn("pointer/array forms fixed", instruction)
        self.assertNotIn("HLS 214-", instruction)

    def test_explicit_external_instruction_is_retained_but_cannot_replace_abi(self) -> None:
        instruction = _build_external_candidate_abi_instruction(
            public_hls_decl="int trap_hls(const int* height);",
            candidate_name="trap_hls",
            external_tb_instruction="Preserve the numerical algorithm.",
        )
        self.assertIn("int trap_hls(const int* height);", instruction)
        self.assertIn("Preserve the numerical algorithm.", instruction)
        self.assertLess(
            instruction.index("int trap_hls"),
            instruction.index("Preserve the numerical algorithm"),
        )

    def test_candidate_name_mismatch_is_rejected_before_prompt(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            _build_external_candidate_abi_instruction(
                public_hls_decl="int wrong_hls(int value);",
                candidate_name="expected_hls",
                external_tb_instruction=None,
            )

    def test_refactoring_message_consumes_the_bound_instruction(self) -> None:
        source = (ROOT / "flow/tools/refactoring.py").read_text(encoding="utf-8")
        self.assertIn("cv['tb_aligned_instruction']", source)
        self.assertIn("signature and constraints", source)

    def test_original_cli_surface_remains_three_commands(self) -> None:
        from agrefactor.cli import build_parser

        parser = build_parser()
        for command in ("refactor", "optimize", "full"):
            args = parser.parse_args(
                [command, "kernel.cpp", "--top", "top"]
            )
            self.assertEqual(args.command, command)


if __name__ == "__main__":
    unittest.main()
