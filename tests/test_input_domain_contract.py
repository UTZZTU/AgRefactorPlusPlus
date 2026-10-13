import json
from pathlib import Path
import unittest
from unittest.mock import patch

from flow.tools.input_domain import (
    _source_parameters,
    generate_input_domain_contract,
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


class InputDomainFeedbackTests(unittest.TestCase):
    def error_for(self, value):
        with self.assertRaises(ValueError) as raised:
            normalize_input_domain_contract(
                value, source_code=VECTOR_SOURCE, kernel_name="process_top"
            )
        return str(raised.exception)

    def test_reports_each_independent_port_error_and_dynamic_allowed_ports(self):
        error = self.error_for({
            "dimensions": {
                "rows": {"port": "header.rows", "min": 1, "max": 8},
                "columns": {"port": "missing", "min": 1, "max": 4},
            },
            "buffers": {
                "data": {"extent": [], "max_elements": 8},
                "header.data": {"extent": [], "max_elements": 8},
            },
        })
        for detail in (
            "path=/dimensions/rows/port", 'actual="header.rows"',
            "path=/dimensions/columns/port", 'actual="missing"',
            "path=/buffers/header.data", 'actual="header.data"',
            'allowed_ports=["data", "n"]',
        ):
            self.assertIn(detail, error)

    def test_json_pointer_escapes_invalid_names_without_hiding_actual_value(self):
        error = self.error_for({
            "dimensions": {"row/~name": {"port": "n", "min": 1, "max": 4}},
            "buffers": {"data": {"extent": [], "max_elements": 4}},
        })
        self.assertIn("path=/dimensions/row~1~0name", error)
        self.assertIn('actual="row/~name"', error)

    def test_invalid_dimension_prevents_dependent_extent_and_capacity_errors(self):
        error = self.error_for({
            "dimensions": {"n": {"port": "n", "min": "bad", "max": 8}},
            "buffers": {"data": {"extent": ["n"], "max_elements": 1}},
        })
        self.assertIn("path=/dimensions/n/min", error)
        self.assertIn('actual="bad"', error)
        self.assertNotIn("unknown dimensions", error)
        self.assertNotIn("maximum extent", error)

    def test_invalid_container_has_precise_error_without_cascading(self):
        error = self.error_for({"dimensions": [], "buffers": "bad"})
        self.assertIn("path=/dimensions", error)
        self.assertIn("path=/buffers", error)
        self.assertNotIn("missing buffer capacity", error)

    def test_invalid_buffer_capacity_is_not_also_reported_as_missing(self):
        error = self.error_for({
            "dimensions": {},
            "buffers": {"data": {"extent": [], "max_elements": 0}},
        })
        self.assertIn("path=/buffers/data/max_elements", error)
        self.assertIn("actual=0", error)
        self.assertNotIn("missing buffer capacity", error)

    def test_unknown_extent_reports_specific_index_and_known_dimensions(self):
        contract = vector_contract()
        contract["buffers"]["data"]["extent"] = ["n", "other"]
        error = self.error_for(contract)
        self.assertIn("path=/buffers/data/extent/1", error)
        self.assertIn('actual="other"', error)
        self.assertIn('allowed_dimensions=["n"]', error)

    def test_unavailable_interface_does_not_invent_ports_or_reject_valid_names(self):
        with patch("flow.tools.input_domain._source_parameters", return_value=None):
            normalized = normalize_input_domain_contract(
                vector_contract(), source_code=VECTOR_SOURCE, kernel_name="process_top"
            )
            error = self.error_for({
                "dimensions": {"n": {"port": "header.n", "min": 1, "max": 8}},
                "buffers": {"data": {"extent": [], "max_elements": 8}},
            })
        self.assertEqual(normalized["buffers"]["data"]["max_elements"], 512)
        self.assertIn("interface_facts=unavailable", error)
        self.assertNotIn("allowed_ports=", error)

    def test_accepted_normalization_and_hash_remain_unchanged(self):
        contract = vector_contract()
        contract.update(schema_version=99, kind="ignored", origin="provided", finite_domain=True)
        contract["dimensions"]["n"]["ignored"] = 3
        contract["buffers"]["data"]["ignored"] = 4
        expected = {
            "schema_version": 1, "kind": "input_domain_v1",
            "dimensions": {"n": {"port": "n", "min": 1, "max": 512}},
            "buffers": {"data": {"extent": ["n"], "max_elements": 512}},
            "origin": "provided", "finite_domain": True,
        }
        normalized = normalize_input_domain_contract(
            contract, source_code=VECTOR_SOURCE, kernel_name="process_top"
        )
        self.assertEqual(normalized, expected)
        self.assertEqual(input_domain_sha256(normalized), input_domain_sha256(expected))

    def test_capacity_above_product_remains_accepted_and_short_capacity_rejected(self):
        contract = vector_contract()
        contract["buffers"]["data"]["max_elements"] = 640
        self.assertEqual(normalize_input_domain_contract(
            contract, source_code=VECTOR_SOURCE, kernel_name="process_top"
        )["buffers"]["data"]["max_elements"], 640)
        contract["buffers"]["data"]["max_elements"] = 511
        error = self.error_for(contract)
        self.assertIn("maximum extent", error)
        self.assertIn("actual=511", error)
        self.assertIn("512", error)

    def test_repair_requests_include_specific_feedback_and_complete_history(self):
        contracts = []
        for port in ("header.n", "first_missing", "second_missing"):
            contract = vector_contract()
            contract["dimensions"]["n"]["port"] = port
            contracts.append(json.dumps(contract))
        requests = []
        responses = iter(contracts + [json.dumps(vector_contract())])

        class Response:
            def __init__(self, content):
                self.messages = [{"content": content}]

            def process(self):
                return None

        class Agent:
            def run(self, **kwargs):
                requests.append((kwargs["message"], "clear_history" not in kwargs))
                self.options.append(kwargs)
                return Response(next(responses))

        agent = Agent()
        agent.options = []

        budget = object()
        with patch("flow.tools.input_domain.HLSAgentLoader") as loader:
            loader.return_value.load_agent.return_value = agent
            normalized = generate_input_domain_contract(
                orig_code=VECTOR_SOURCE, kernel_name="process_top", budget=budget
            )
        self.assertIs(loader.call_args.kwargs["budget"], budget)
        self.assertEqual(normalized["buffers"]["data"]["max_elements"], 512)
        self.assertEqual(len(requests), 4)
        self.assertEqual([first for _, first in requests], [True, False, False, False])
        self.assertTrue(all(options["max_turns"] == 1 for options in agent.options))
        self.assertTrue(all(options["clear_history"] is False for options in agent.options[1:]))
        self.assertIn('allowed_ports=["data", "n"]', requests[0][0])
        for index, port in enumerate(("header.n", "first_missing", "second_missing")):
            for message, _ in requests[index + 1:]:
                self.assertIn(f'actual="{port}"', message)
                self.assertIn("path=/dimensions/n/port", message)

    def test_exhaustion_preserves_four_causes_and_json_error_coordinates(self):
        invalid = vector_contract()
        invalid["dimensions"]["n"]["port"] = "unknown_last"
        responses = ["{", "[]", json.dumps({"dimensions": [], "buffers": {}}), json.dumps(invalid)]
        with patch("flow.tools.input_domain.HLSAgentLoader"), \
                patch("flow.tools.input_domain._json_response", side_effect=responses) as request:
            with self.assertRaises(ValueError) as raised:
                generate_input_domain_contract(orig_code=VECTOR_SOURCE, kernel_name="process_top")
        error = str(raised.exception)
        self.assertEqual(request.call_count, 4)
        for attempt in range(4):
            self.assertIn(f"Attempt {attempt}:", error)
        self.assertIn("line 1 column 2", error)
        self.assertIn('actual="unknown_last"', error)
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertIn('actual="unknown_last"', str(raised.exception.__cause__))

    def test_agent_capacity_instruction_matches_existing_validator(self):
        message = (Path(__file__).resolve().parents[1] / "flow/agents/input_domain.yaml").read_text()
        self.assertNotIn("max_elements exactly", message)
        self.assertIn("at least", message)


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
