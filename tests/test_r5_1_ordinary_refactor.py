from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_ordinary_refactor",
    ROOT / "scripts" / "r5_1_ordinary_refactor.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R51OrdinaryRefactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "ordinary_refactor_protocol.json"
        )

    def test_frozen_protocol_is_valid_and_fully_reserved(self) -> None:
        MODULE.validate_protocol(self.protocol)
        product = self.protocol["product_contract"]
        reserve = self.protocol["budget"]["campaign_reserve"]
        self.assertEqual(
            reserve["provider_calls"],
            len(self.protocol["cases"]) * product["max_llm_calls_per_case"],
        )
        self.assertEqual(
            reserve["vitis_launches"],
            len(self.protocol["cases"]) * product["max_vitis_launches_per_case"],
        )

    def test_protocol_rejects_future_reuse_and_hidden_enablement(self) -> None:
        for mutation in ("future", "hidden"):
            with self.subTest(mutation=mutation):
                protocol = copy.deepcopy(self.protocol)
                if mutation == "future":
                    protocol["cases"][0]["eligible_as_pristine_p9_future"] = True
                else:
                    protocol["product_contract"]["hidden_tests"] = "auto"
                with self.assertRaises(MODULE.P5Error):
                    MODULE.validate_protocol(protocol)

    def test_identifier_rewrite_skips_comments_strings_and_longer_names(self) -> None:
        code = (
            "// top stays in a comment\n"
            "const char *label = \"top stays in a string\";\n"
            "void top(int value);\n"
            "void helper() { int top_extra = 0; top(top_extra); }\n"
        )
        rewritten, count = MODULE.rewrite_cpp_identifier(code, "top", "top_hls")
        self.assertEqual(count, 2)
        self.assertIn("// top stays in a comment", rewritten)
        self.assertIn('"top stays in a string"', rewritten)
        self.assertIn("top_extra", rewritten)
        self.assertIn("void top_hls(int value);", rewritten)
        self.assertIn("top_hls(top_extra)", rewritten)

    def test_product_arguments_leave_r5_unselected_and_disable_hidden(self) -> None:
        args = MODULE.build_product_args(
            protocol=self.protocol,
            source=Path("/tmp/source.cpp"),
            public_test=Path("/tmp/public.cpp"),
            public_contract=Path("/tmp/contract.json"),
            top="kernel",
            product_root=Path("/tmp/product"),
            run_id="p5-test",
        )
        self.assertEqual(args.command, "refactor")
        self.assertEqual(args.hidden_tests, "none")
        self.assertFalse(hasattr(args, "_r5_arm_override"))
        self.assertEqual(args.max_llm_calls, 11)
        self.assertEqual(args.max_csim_calls, 3)
        self.assertEqual(args.max_csynth_calls, 3)
        self.assertEqual(args.max_cosim_calls, 2)

    def test_public_contract_ports_follow_candidate_abi_positionally(self) -> None:
        contract = {
            "schema_version": 2,
            "kind": "public_differential_self_check_v1",
            "candidate_mismatch_returncodes": [1],
            "cosim_interface_depths": {"res_S": 1, "res_V": 1},
        }
        adapted = MODULE.adapt_public_runtime_contract(
            contract=contract,
            raw_design="void Runs(int *res_S, int *res_V) {}\n",
            adapted_public_test="void Runs_hls(int *res_s, int *res_v);\n",
            raw_top="Runs",
            candidate_top="Runs_hls",
        )
        self.assertEqual(
            adapted["cosim_interface_depths"],
            {"res_s": 1, "res_v": 1},
        )

    def test_outcome_classification_preserves_raw_control_semantics(self) -> None:
        self.assertEqual(
            MODULE.classify_outcome("raw_pass_all", True, True, False),
            "unnecessary_rewrite",
        )
        self.assertEqual(
            MODULE.classify_outcome("raw_pass_all", False, None, False),
            "regression",
        )
        self.assertEqual(
            MODULE.classify_outcome("raw_fail_actionable", True, True, False),
            "public_refactor_lift_actionable",
        )
        self.assertEqual(
            MODULE.classify_outcome("raw_fail_ambiguous", True, True, False),
            "public_refactor_lift_ambiguous",
        )
        self.assertEqual(
            MODULE.classify_outcome("raw_fail_actionable", True, True, True),
            "infrastructure_failure",
        )


if __name__ == "__main__":
    unittest.main()
