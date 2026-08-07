from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.cli import build_parser
from agrefactor.config import EvaluationSplit, TestSuiteSpec, resolve_target_profile
from agrefactor.product.source_bootstrap import _load_public_test_contracts
from flow.tools.vitis_cosim import (
    _candidate_returncode_authorized as cosim_authorized,
    make_vitis_cosim_tcl,
)
from flow.tools.vitis_csim import (
    _candidate_returncode_authorized as csim_authorized,
)


V2 = {
    "schema_version": 2,
    "kind": "public_differential_self_check_v1",
    "candidate_mismatch_returncodes": [1],
    "cosim_interface_depths": {
        "fallback": 1,
        "input": 32,
        "output": 32,
    },
}


class P40FPrR1CCosimDepthContractTests(unittest.TestCase):
    def test_v1_round_trip_unchanged(self):
        suite = TestSuiteSpec(
            suite_id="v1",
            split=EvaluationSplit.PUBLIC,
            runtime_contract={
                "schema_version": 1,
                "kind": "public_differential_self_check_v1",
                "candidate_mismatch_returncodes": [1],
            },
        )
        self.assertEqual(
            suite.to_dict()["runtime_contract"],
            {
                "schema_version": 1,
                "kind": "public_differential_self_check_v1",
                "candidate_mismatch_returncodes": [1],
            },
        )

    def test_v2_round_trip_preserves_depths(self):
        suite = TestSuiteSpec(
            suite_id="v2",
            split=EvaluationSplit.PUBLIC,
            runtime_contract=V2,
        )
        self.assertEqual(suite.to_dict()["runtime_contract"], V2)
        rebuilt = TestSuiteSpec.from_dict(suite.to_dict())
        self.assertEqual(rebuilt.to_dict()["runtime_contract"], V2)

    def test_v2_empty_depths_rejected(self):
        bad = dict(V2)
        bad["cosim_interface_depths"] = {}
        with self.assertRaises(ValueError):
            TestSuiteSpec(
                suite_id="bad",
                split=EvaluationSplit.PUBLIC,
                runtime_contract=bad,
            )

    def test_v2_invalid_port_or_depth_rejected(self):
        for depths in (
            {"bad-port": 4},
            {"input": 0},
            {"input": True},
        ):
            bad = dict(V2)
            bad["cosim_interface_depths"] = depths
            with self.subTest(depths=depths):
                with self.assertRaises((TypeError, ValueError)):
                    TestSuiteSpec(
                        suite_id="bad",
                        split=EvaluationSplit.PUBLIC,
                        runtime_contract=bad,
                    )

    def test_tcl_depth_directives_are_deterministic(self):
        root = Path("/tmp/pr-r1c-depth-test")
        files = {
            "candidate": root / "candidate.cpp",
            "reference": root / "reference.cpp",
            "testbench": root / "tb.cpp",
        }
        tcl = make_vitis_cosim_tcl(
            root=root,
            top="process_top_hls",
            files=files,
            profile=resolve_target_profile("vitis-2023.2-default"),
            typed_execution_id="1" * 32,
            interface_depths=V2["cosim_interface_depths"],
        )
        directives = [
            'set_directive_interface -mode m_axi -depth 1 "process_top_hls" "fallback"',
            'set_directive_interface -mode m_axi -depth 32 "process_top_hls" "input"',
            'set_directive_interface -mode m_axi -depth 32 "process_top_hls" "output"',
        ]
        positions = [tcl.index(item) for item in directives]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(tcl.index("create_clock"), positions[0])
        self.assertLess(positions[-1], tcl.index("csim_design"))

    def test_v2_csim_candidate_mismatch_authority_preserved(self):
        self.assertTrue(csim_authorized(V2, 1))
        self.assertFalse(csim_authorized(V2, 2))

    def test_v2_cosim_candidate_mismatch_authority_preserved(self):
        self.assertTrue(cosim_authorized(V2, 1))
        self.assertFalse(cosim_authorized(V2, 2))

    def test_contract_loader_pairs_by_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            public = root / "public.cpp"
            public.write_text("int main(){return 0;}\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(json.dumps(V2), encoding="utf-8")
            loaded = _load_public_test_contracts(
                (contract,),
                (str(public),),
            )
            self.assertEqual(len(loaded), 1)
            suite = TestSuiteSpec(
                suite_id="loaded",
                split=EvaluationSplit.PUBLIC,
                runtime_contract=loaded[0],
            )
            self.assertEqual(suite.to_dict()["runtime_contract"], V2)

    def test_cli_accepts_public_test_contract(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "process_top",
                "--public-test",
                "public.cpp",
                "--public-test-contract",
                "public.contract.json",
            ]
        )
        self.assertEqual(
            args.public_test_contracts_provided,
            [Path("public.contract.json")],
        )


if __name__ == "__main__":
    unittest.main()
