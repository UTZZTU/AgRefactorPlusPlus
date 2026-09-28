import unittest
from unittest.mock import patch

from flow.tools.input_domain import (
    _source_parameters,
    input_domain_sha256,
    normalize_input_domain_contract,
)
from flow.tools.tb_optimizer import (
    ModelArtifactError,
    _initial_user_message,
    generate_public_runtime_contract,
    validate_testbench_input_domain,
)
from flow.tools.testbench import _build_testbench_request


VECTOR_SOURCE = 'extern "C" void process_top(int *data, int n) { data[0] = n; }\n'
MATRIX_SOURCE = (
    'void matmul(int *a, int *b, int *c, int n, int l, int m) '\
    '{ c[0] = a[0] + b[0]; }\n'
)


def vector_contract():
    return {
        "dimensions": {"n": {"port": "n", "min": 1, "max": 512}},
        "buffers": {"data": {"extent": ["n"], "max_elements": 512}},
    }


class InputDomainContractTests(unittest.TestCase):
    def test_interface_parser_handles_nested_templates_and_function_pointers(self):
        source = (
            "template <typename T> struct stream {}; "
            "void process_top(void (*callback)(int, int), "
            "stream<stream<int> > &input, int *out) {}\n"
        )
        self.assertEqual(
            _source_parameters(source, "process_top"),
            {"callback": False, "input": False, "out": True},
        )
    def test_normalizes_vector_contract_and_hash_is_stable(self):
        normalized = normalize_input_domain_contract(
            vector_contract(),
            source_code=VECTOR_SOURCE,
            kernel_name="process_top",
        )
        self.assertEqual(normalized["kind"], "input_domain_v1")
        self.assertEqual(normalized["buffers"]["data"]["max_elements"], 512)
        self.assertEqual(input_domain_sha256(normalized), input_domain_sha256(dict(normalized)))

    def test_normalizes_matrix_product(self):
        contract = {
            "dimensions": {
                "n": {"port": "n", "min": 1, "max": 8},
                "l": {"port": "l", "min": 1, "max": 8},
                "m": {"port": "m", "min": 1, "max": 8},
            },
            "buffers": {
                "a": {"extent": ["n", "l"], "max_elements": 64},
                "b": {"extent": ["l", "m"], "max_elements": 64},
                "c": {"extent": ["n", "m"], "max_elements": 64},
            },
        }
        normalized = normalize_input_domain_contract(
            contract,
            source_code=MATRIX_SOURCE,
            kernel_name="matmul",
        )
        self.assertEqual(normalized["buffers"]["c"]["max_elements"], 64)

    def test_fixed_pointer_capacity_may_exceed_one_without_dynamic_extent(self):
        source = (
            "typedef unsigned char block[8];\n"
            "typedef unsigned char keys[16][6];\n"
            "void des_crypt(block *in, block *out, keys *key) {}\n"
        )
        normalized = normalize_input_domain_contract(
            {
                "dimensions": {},
                "buffers": {
                    "in": {"extent": [], "max_elements": 1},
                    "out": {"extent": [], "max_elements": 1},
                    "key": {"extent": [], "max_elements": 16},
                },
            },
            source_code=source,
            kernel_name="des_crypt",
        )
        self.assertEqual(normalized["buffers"]["key"]["max_elements"], 16)

    def test_allows_signed_scalar_ranges(self):
        contract = vector_contract()
        contract["dimensions"]["n"].update(min=-8, max=8)
        normalized = normalize_input_domain_contract(
            contract,
            source_code=VECTOR_SOURCE,
            kernel_name="process_top",
        )
        self.assertEqual(normalized["dimensions"]["n"]["min"], -8)

    def test_rejects_unknown_port_bad_range_and_bad_product(self):
        for mutate, message in (
            (lambda c: c["dimensions"]["n"].update(port="missing"), "unknown port"),
            (lambda c: c["dimensions"]["n"].update(min=9, max=8), ">= min"),
            (lambda c: c["buffers"]["data"].update(max_elements=511), "maximum extent"),
        ):
            contract = vector_contract()
            mutate(contract)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                normalize_input_domain_contract(
                    contract,
                    source_code=VECTOR_SOURCE,
                    kernel_name="process_top",
                )

    def test_rejects_missing_pointer_capacity_and_unknown_extent(self):
        contract = vector_contract()
        contract["buffers"] = {}
        with self.assertRaisesRegex(ValueError, "missing buffer capacity"):
            normalize_input_domain_contract(contract, source_code=VECTOR_SOURCE, kernel_name="process_top")
        contract = vector_contract()
        contract["buffers"]["data"]["extent"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "unknown dimensions"):
            normalize_input_domain_contract(contract, source_code=VECTOR_SOURCE, kernel_name="process_top")

    def test_public_and_hidden_prompt_share_range_and_public_requires_maximum(self):
        normalized = normalize_input_domain_contract(
            vector_contract(), source_code=VECTOR_SOURCE, kernel_name="process_top"
        )
        public = _build_testbench_request(VECTOR_SOURCE, "process_top", normalized)
        hidden = _initial_user_message(
            VECTOR_SOURCE,
            "process_top",
            pinned_public_hls_decl='extern "C" void process_top_hls(int *data, int n);',
            input_domain_contract=normalized,
        )
        self.assertIn('"max": 512', public)
        self.assertIn("include its maximum values", public)
        self.assertIn('"max": 512', hidden)
        self.assertIn("shared by Public, Candidate, and Hidden", hidden)

    def test_cosim_depth_is_generated_from_interface_and_testbench(self):
        normalized = normalize_input_domain_contract(
            vector_contract(), source_code=VECTOR_SOURCE, kernel_name="process_top"
        )
        tb = (
            'extern "C" void process_top(int *data, int n);\n'
            'extern "C" void process_top_hls(int *data, int n);\n'
            'int main() { int data[512] = {}; process_top_hls(data, 512); return 0; }\n'
        )
        class Response:
            messages = [{"content": '{"cosim_interface_depths":{"data":640}}'}]

            def process(self):
                return None

        class Agent:
            calls = 0

            def run(self, **_kwargs):
                self.calls += 1
                return Response()

        agent = Agent()
        with patch("flow.tools.tb_optimizer.HLSAgentLoader") as loader:
            loader.return_value.load_agent.return_value = agent
            runtime = generate_public_runtime_contract(
                orig_code=VECTOR_SOURCE,
                testbench_code=tb,
                candidate_top_function="process_top_hls",
                frozen_hls_decl='extern "C" void process_top_hls(int *data, int n);',
                input_domain_contract=normalized,
            )
        self.assertEqual(agent.calls, 1)
        self.assertEqual(runtime["cosim_interface_depths"], {"data": 640})

    def test_cosim_depth_rejects_candidate_port_rename(self):
        tb = 'extern "C" void process_top_hls(int *renamed, int n);\nint main(){return 0;}\n'
        with self.assertRaisesRegex(ModelArtifactError, "not pointer or array"):
            from flow.tools.tb_optimizer import validate_public_runtime_contract

            validate_public_runtime_contract(
                {
                    "schema_version": 2,
                    "kind": "public_differential_self_check_v1",
                    "candidate_mismatch_returncodes": [1],
                    "cosim_interface_depths": {"data": 512},
                },
                testbench_code=tb,
                candidate_top_function="process_top_hls",
                frozen_hls_decl='extern "C" void process_top_hls(int *renamed, int n);',
            )

    def test_arbitrary_cpp_input_expressions_are_deferred_to_execution(self):
        normalized = normalize_input_domain_contract(
            vector_contract(), source_code=VECTOR_SOURCE, kernel_name="process_top"
        )
        declaration = 'extern "C" void process_top_hls(int *data, int n);'
        below_max = declaration + "\nint main(){ int data[512] = {}; process_top_hls(data, 128); return 0; }\n"
        validate_testbench_input_domain(
            below_max, "process_top_hls", declaration, normalized, require_maximum=True
        )
        in_range_hidden = declaration + "\nint main(){ int data[512] = {}; process_top_hls(data, 257); return 0; }\n"
        validate_testbench_input_domain(
            in_range_hidden, "process_top_hls", declaration, normalized, require_maximum=False
        )
        helper_hidden = (
            declaration
            + "\nvoid run_case(int n) { int data[512] = {}; process_top_hls(data, n); }\n"
            + "int main(){ run_case(257); return 0; }\n"
        )
        validate_testbench_input_domain(
            helper_hidden, "process_top_hls", declaration, normalized, require_maximum=False
        )
        helper_public = (
            declaration
            + "\nvoid run_case(int n) { int data[512] = {}; process_top_hls(data, n); }\n"
            + "int main(){ run_case(512); return 0; }\n"
        )
        validate_testbench_input_domain(
            helper_public, "process_top_hls", declaration, normalized, require_maximum=True
        )
        out_of_range = declaration + "\nint main(){ int data[513] = {}; process_top_hls(data, 513); return 0; }\n"
        validate_testbench_input_domain(
            out_of_range, "process_top_hls", declaration, normalized, require_maximum=False
        )

        mutable_initial_value = (
            declaration
            + "\nvoid run_case(int n) { int data[512] = {}; process_top_hls(data, n); }\n"
            + "int main(){ int n = 0; n = 512; run_case(n); return 0; }\n"
        )
        validate_testbench_input_domain(
            mutable_initial_value,
            "process_top_hls",
            declaration,
            normalized,
            require_maximum=True,
        )

        ambiguous_scope = (
            declaration
            + "\nint main(){ int data[512] = {}; { const int N = 512; "
            + "process_top_hls(data, N); } { const int N = 0; } return 0; }\n"
        )
        validate_testbench_input_domain(
            ambiguous_scope,
            "process_top_hls",
            declaration,
            normalized,
            require_maximum=True,
        )

    def test_public_maximum_inside_macro_is_audited_after_preprocessing(self):
        normalized = normalize_input_domain_contract(
            vector_contract(), source_code=VECTOR_SOURCE, kernel_name="process_top"
        )
        declaration = 'extern "C" void process_top_hls(int *data, int n);'
        testbench = (
            declaration
            + "\n#define RUN(N) do { int data[512] = {}; process_top_hls(data, (N)); } while (0)\n"
            + "int main(){ RUN(512); return 0; }\n"
        )
        validate_testbench_input_domain(
            testbench,
            "process_top_hls",
            declaration,
            normalized,
            require_maximum=True,
        )


if __name__ == "__main__":
    unittest.main()
