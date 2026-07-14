import tempfile
import unittest

from agrefactor.evaluation import TestbenchPreflight
from agrefactor.models import (
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.testing import TestbenchRepairRequest
from agrefactor.testing.model_testbench_repairer import (
    ModelTestbenchRepairer,
    TestbenchRepairContract,
    TestbenchRepairResponseError,
    build_testbench_repair_messages,
    extract_complete_cpp_block,
)


ORIGINAL = r"""
extern "C" void process_top(int n, int *input, int *output) {
    for (int i = 0; i < n; ++i) output[i] = input[i];
}
"""

CANDIDATE = r"""
extern "C" void process_top_hls(int n, int *input, int *output) {
    for (int i = 0; i < n; ++i) output[i] = input[i];
}
"""

BROKEN_TB = r"""
#include <cstdio>
#define N 2

extern "C" void process_top(int, int *, int *);
extern "C" void process_top_hls(int, int *, int *);
extern node *root;

int main() {
    root = nullptr;
    int input[N] = {2, 1};
    int original[N] = {};
    int candidate[N] = {};
    process_top(N, input, original);
    process_top_hls(N, input, candidate);
    if (original[0] != candidate[0]) {
        return 1;
    }
    return 0;
}
"""

FIXED_TB = r"""
#include <cstdio>
#define N 2

extern "C" void process_top(int, int *, int *);
extern "C" void process_top_hls(int, int *, int *);

int main() {
    int input[N] = {2, 1};
    int original[N] = {};
    int candidate[N] = {};
    process_top(N, input, original);
    process_top_hls(N, input, candidate);
    if (original[0] != candidate[0]) {
        return 1;
    }
    return 0;
}
"""


class FakeProvider(ModelProvider):
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        return ModelResponse(
            text=self.response_text,
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.001,
            ),
            finish_reason="stop",
        )


def make_preflight_result():
    with tempfile.TemporaryDirectory() as directory:
        return TestbenchPreflight().compile_and_link(
            work_dir=directory,
            testbench_code=BROKEN_TB,
            original_code=ORIGINAL,
            candidate_code=CANDIDATE,
        )


def make_request():
    return TestbenchRepairRequest(
        attempt=1,
        max_attempts=2,
        current_testbench=BROKEN_TB,
        original_code=ORIGINAL,
        candidate_code=CANDIDATE,
        preflight=make_preflight_result(),
    )


def make_registry(provider):
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="repair-model",
            provider="fake",
            model="fake-repair-1",
            family="reasoning",
            default_parameters={
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        )
    )
    return registry


class ModelTestbenchRepairerTests(unittest.TestCase):
    def test_extracts_one_cpp_block_after_thinking(self) -> None:
        text = (
            "<think>private reasoning</think>\n"
            "```cpp\nint main() { return 0; }\n```"
        )
        self.assertEqual(
            extract_complete_cpp_block(text),
            "int main() { return 0; }",
        )

    def test_rejects_commentary_outside_block(self) -> None:
        with self.assertRaises(TestbenchRepairResponseError):
            extract_complete_cpp_block(
                "Here is the fix:\n```cpp\n"
                "int main() { return 0; }\n```"
            )

    def test_prompt_contains_evidence_and_read_only_boundaries(self) -> None:
        messages = build_testbench_repair_messages(
            make_request(),
            family_instruction=(
                "Reason internally, emit only final code."
            ),
        )

        self.assertEqual(messages[0].role, "system")
        self.assertIn("testbench-only", messages[0].content)
        self.assertIn(
            "Never modify or propose changes to the candidate",
            messages[0].content,
        )
        self.assertIn(
            "Model-family instruction",
            messages[0].content,
        )
        self.assertIn(
            "declarations are not authoritative",
            messages[0].content,
        )
        self.assertIn(
            "Never define, stub, wrap, or reimplement",
            messages[0].content,
        )
        self.assertIn(
            "file-scope variables",
            messages[0].content,
        )
        self.assertIn(
            "fresh child process",
            messages[0].content,
        )
        self.assertIn(
            '"failure_owner": "testbench"',
            messages[1].content,
        )
        self.assertIn(
            "Read-only original program context",
            messages[1].content,
        )

    def test_contract_rejects_removed_macro_or_public_call(self) -> None:
        contract = TestbenchRepairContract.from_testbench(
            BROKEN_TB
        )
        weakened = FIXED_TB.replace(
            "#define N 2",
            "",
        ).replace(
            "process_top_hls(N, input, candidate);",
            "",
        )

        issues = contract.validate(weakened)

        self.assertTrue(
            any("missing required macro" in issue for issue in issues)
        )
        self.assertTrue(
            any("reduced call count" in issue for issue in issues)
        )

    def test_contract_allows_linkage_correction(self) -> None:
        contract = TestbenchRepairContract.from_testbench(
            BROKEN_TB
        )
        corrected = FIXED_TB.replace('extern "C" ', '')

        self.assertEqual(
            contract.validate(corrected),
            (),
        )

    def test_contract_rejects_top_function_stub(self) -> None:
        contract = TestbenchRepairContract.from_testbench(
            BROKEN_TB
        )
        stubbed = FIXED_TB.replace(
            'extern "C" void process_top(int, int *, int *);',
            (
                'extern "C" void process_top(int, int *, int *);\n'
                'extern "C" void process_top(int, int *, int *) {}'
            ),
        )

        issues = contract.validate(stubbed)
        self.assertTrue(
            any(
                "must not define, stub, or wrap" in issue
                for issue in issues
            )
        )

    def test_model_adapter_uses_registry_and_merges_parameters(self) -> None:
        provider = FakeProvider(
            f"```cpp\n{FIXED_TB}\n```"
        )
        repairer = ModelTestbenchRepairer(
            registry=make_registry(provider),
            model_name="repair-model",
            parameters={
                "temperature": 0,
            },
            family_instructions={
                "reasoning": (
                    "Reason internally and emit only the code block."
                ),
            },
        )

        repaired = repairer.repair(make_request())

        self.assertEqual(repaired.strip(), FIXED_TB.strip())
        self.assertEqual(len(provider.calls), 1)
        model, request = provider.calls[0]
        self.assertEqual(model.name, "repair-model")
        self.assertEqual(request.parameters["temperature"], 0)
        self.assertEqual(request.parameters["max_tokens"], 4096)
        self.assertIn(
            "Reason internally and emit only the code block.",
            request.messages[0].content,
        )
        self.assertEqual(
            repairer.last_response.usage.total_tokens,
            150,
        )

    def test_model_adapter_rejects_weakened_response(self) -> None:
        weakened = FIXED_TB.replace(
            "process_top_hls(N, input, candidate);",
            "",
        )
        provider = FakeProvider(
            f"```cpp\n{weakened}\n```"
        )
        repairer = ModelTestbenchRepairer(
            registry=make_registry(provider),
            model_name="repair-model",
        )

        with self.assertRaises(TestbenchRepairResponseError):
            repairer.repair(make_request())

    def test_model_adapter_rejects_multiple_code_blocks(self) -> None:
        provider = FakeProvider(
            "```cpp\nint main() { return 0; }\n```\n"
            "```cpp\nint main() { return 1; }\n```"
        )
        repairer = ModelTestbenchRepairer(
            registry=make_registry(provider),
            model_name="repair-model",
        )

        with self.assertRaises(TestbenchRepairResponseError):
            repairer.repair(make_request())


if __name__ == "__main__":
    unittest.main()
