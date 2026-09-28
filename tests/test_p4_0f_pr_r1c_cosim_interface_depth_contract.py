from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cli import build_parser
from agrefactor.config import EvaluationSplit, TestSuiteSpec, resolve_target_profile
from agrefactor.product.source_bootstrap import _load_public_test_contracts
from flow.tools.vitis_cosim import (
    _candidate_returncode_authorized as cosim_authorized,
    make_vitis_cosim_tcl,
)
from flow.tools.tb_optimizer import (
    ModelArtifactError,
    _initial_user_message,
    _public_runtime_contract_prompt,
    generate_public_runtime_contract,
    public_runtime_contract_depth_ports,
    validate_public_runtime_contract,
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
    def test_runtime_depth_prompt_uses_immediate_pointee_units(self):
        prompt = _public_runtime_contract_prompt(
            orig_code="void top(unsigned char (*state)[4][4]);",
            testbench_code=(
                "void top_hls(unsigned char (*state)[4][4]);\n"
                "int main(){unsigned char state[4][4]{}; "
                "top_hls(&state); return 0;}\n"
            ),
            candidate_top_function="top_hls",
            frozen_hls_decl=(
                "void top_hls(unsigned char (*state)[4][4]);"
            ),
            required_ports=("state",),
        )
        self.assertIn("immediate pointee type", prompt)
        self.assertIn("needs depth 1, not R*C", prompt)
        self.assertIn("Do not add depths across sequential test cases", prompt)

    def test_public_prompt_requires_fixed_capacity_pointer_storage(self):
        prompt = _initial_user_message(
            "void top(int n, int* data);",
            "top",
        )
        self.assertIn(
            "one fixed capacity across every Candidate call",
            prompt,
        )
        self.assertIn("full declared interface depth", prompt)

    def test_model_generated_depth_contract_retries_json_only(self):
        class Response:
            def __init__(self, content):
                self.messages = [{"content": content}]

            def process(self):
                return None

        class Agent:
            def __init__(self):
                self.responses = iter(
                    [
                        "```json\n{}\n```",
                        '{"cosim_interface_depths":{"input":64}}',
                    ]
                )
                self.calls = 0

            def run(self, **_kwargs):
                self.calls += 1
                return Response(next(self.responses))

        agent = Agent()
        with patch(
            "flow.tools.tb_optimizer.HLSAgentLoader"
        ) as loader:
            loader.return_value.load_agent.return_value = agent
            contract = generate_public_runtime_contract(
                orig_code="int top(const int* input);",
                testbench_code=(
                    "int top_hls(const int* input);\n"
                    "int main(){int x[64]={}; return top_hls(x);}\n"
                ),
                candidate_top_function="top_hls",
                frozen_hls_decl="int top_hls(const int* input);",
            )

        self.assertEqual(agent.calls, 2)
        self.assertEqual(
            contract["cosim_interface_depths"],
            {"input": 64},
        )

    def test_public_abi_depth_ports_exclude_scalars(self):
        testbench = (
            "extern int shared[64];\n"
            "int top_hls(const int* input, int output[32], int count);\n"
            "int main(){return 0;}\n"
        )
        self.assertEqual(
            public_runtime_contract_depth_ports(
                testbench_code=testbench,
                candidate_top_function="top_hls",
                frozen_hls_decl=(
                    "int top_hls(const int* input, "
                    "int output[32], int count);"
                ),
            ),
            ("input", "output"),
        )

    def test_public_abi_depth_ports_support_pointer_to_array_parameters(self):
        declaration = (
            "void des_crypt_hls(unsigned char (*in)[8], "
            "unsigned char (*out)[8], unsigned char (*key)[16][6]);"
        )
        self.assertEqual(
            public_runtime_contract_depth_ports(
                testbench_code=declaration + "\nint main(){return 0;}\n",
                candidate_top_function="des_crypt_hls",
                frozen_hls_decl=declaration,
            ),
            ("in", "key", "out"),
        )

    def test_generated_contract_requires_every_pointer_or_array_port(self):
        testbench = (
            "int top_hls(const int* input, int* output, int count);\n"
            "int main(){return 0;}\n"
        )
        with self.assertRaisesRegex(
            ModelArtifactError,
            "missing required",
        ):
            validate_public_runtime_contract(
                {
                    **V2,
                    "cosim_interface_depths": {"input": 32},
                },
                testbench_code=testbench,
                candidate_top_function="top_hls",
                frozen_hls_decl=(
                    "int top_hls(const int* input, int* output, int count);"
                ),
            )

    def test_generated_contract_rejects_scalar_or_unknown_port(self):
        testbench = (
            "int top_hls(const int* input, int count);\n"
            "int main(){return 0;}\n"
        )
        for depths in (
            {"input": 32, "count": 1},
            {"input": 32, "guessed": 1},
        ):
            with self.subTest(depths=depths):
                with self.assertRaisesRegex(
                    ModelArtifactError,
                    "not pointer or array",
                ):
                    validate_public_runtime_contract(
                        {
                            **V2,
                            "cosim_interface_depths": depths,
                        },
                        testbench_code=testbench,
                        candidate_top_function="top_hls",
                        frozen_hls_decl=(
                            "int top_hls(const int* input, int count);"
                        ),
                    )

    def test_scalar_only_public_abi_uses_v1_contract(self):
        contract = validate_public_runtime_contract(
            {
                "schema_version": 1,
                "kind": "public_differential_self_check_v1",
                "candidate_mismatch_returncodes": [1],
            },
            testbench_code=(
                "int top_hls(int value);\n"
                "int main(){return top_hls(1);}\n"
            ),
            candidate_top_function="top_hls",
            frozen_hls_decl="int top_hls(int value);",
        )
        self.assertEqual(contract["schema_version"], 1)

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

    def test_contract_loader_binds_depth_ports_to_public_abi(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            public = root / "public.cpp"
            public.write_text(
                "int top_hls(const int* input, int* output);\n"
                "int main(){return 0;}\n",
                encoding="utf-8",
            )
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        **V2,
                        "cosim_interface_depths": {
                            "input": 32,
                            "output": 32,
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = _load_public_test_contracts(
                (contract,),
                (public,),
                candidate_top_function="top_hls",
            )
            self.assertEqual(
                loaded[0]["cosim_interface_depths"],
                {"input": 32, "output": 32},
            )

    def test_contract_loader_rejects_depth_port_outside_public_abi(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            public = root / "public.cpp"
            public.write_text(
                "int top_hls(const int* input);\nint main(){return 0;}\n",
                encoding="utf-8",
            )
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        **V2,
                        "cosim_interface_depths": {"guessed_name": 32},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "absent from the Public Candidate ABI",
            ):
                _load_public_test_contracts(
                    (contract,),
                    (public,),
                    candidate_top_function="top_hls",
                )

    def test_contract_loader_accepts_explicit_extern_global_port(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            public = root / "public.cpp"
            public.write_text(
                "extern int epsilon[65535];\n"
                "void top_hls(int* output);\n"
                "int main(){return 0;}\n",
                encoding="utf-8",
            )
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        **V2,
                        "cosim_interface_depths": {
                            "epsilon": 65535,
                            "output": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = _load_public_test_contracts(
                (contract,),
                (public,),
                candidate_top_function="top_hls",
            )
            self.assertEqual(
                loaded[0]["cosim_interface_depths"],
                {"epsilon": 65535, "output": 1},
            )

    def test_comment_or_string_cannot_authorize_global_port(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            public = root / "public.cpp"
            public.write_text(
                "// extern int guessed[32];\n"
                'const char* note = "extern int guessed[32];";\n'
                "void top_hls(int* output);\n"
                "int main(){return 0;}\n",
                encoding="utf-8",
            )
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        **V2,
                        "cosim_interface_depths": {"guessed": 32},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "absent from the Public Candidate ABI",
            ):
                _load_public_test_contracts(
                    (contract,),
                    (public,),
                    candidate_top_function="top_hls",
                )

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
