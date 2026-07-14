import tempfile
import unittest
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.models import (
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.testing import ModelTestbenchRepairer
from flow.tools import general


ORIGINAL = r"""
extern "C" void process_top(int n, int *input, int *output) {
    for (int i = 0; i < n; ++i) {
        output[i] = input[i];
    }
}
"""

CANDIDATE = r"""
extern "C" void process_top_hls(int n, int *input, int *output) {
    for (int i = 0; i < n; ++i) {
        output[i] = input[i];
    }
}
"""

BAD_CANDIDATE = CANDIDATE.replace(
    "output[i] = input[i];",
    "this is invalid C++;",
)

BROKEN_TB = r"""
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
    return original[0] != candidate[0]
        || original[1] != candidate[1];
}
"""

FIXED_TB = r"""
#define N 2

extern "C" void process_top(int, int *, int *);
extern "C" void process_top_hls(int, int *, int *);

int main() {
    int input[N] = {2, 1};
    int original[N] = {};
    int candidate[N] = {};
    process_top(N, input, original);
    process_top_hls(N, input, candidate);
    return original[0] != candidate[0]
        || original[1] != candidate[1];
}
"""


class FakeRepairProvider(ModelProvider):
    def __init__(self, repaired_testbench):
        self.repaired_testbench = repaired_testbench
        self.calls = []

    @property
    def name(self):
        return "fake-repair"

    def generate(self, model, request):
        self.calls.append((model, request))
        return ModelResponse(
            text=f"```cpp\n{self.repaired_testbench}\n```",
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=0.001,
            ),
            finish_reason="stop",
        )


def make_repairer(provider):
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="testbench-repair",
            provider="fake-repair",
            model="fake-testbench-repair",
            family="reasoning",
        )
    )
    return ModelTestbenchRepairer(
        registry=registry,
        model_name="testbench-repair",
    )


def make_context(testbench, candidate=CANDIDATE):
    return ContextVariables(
        data={
            "orig_code": ORIGINAL,
            "curr_code": candidate,
            "testbench": testbench,
            "new_kernel_name": "process_top_hls",
            "code_for_hetero": "",
        }
    )


class LegacyTestbenchRepairIntegrationTests(unittest.TestCase):
    def test_repairs_then_continues_to_csynth_and_csim(self) -> None:
        provider = FakeRepairProvider(FIXED_TB)
        repairer = make_repairer(provider)
        context = make_context(BROKEN_TB)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general.tools.csynth,
                "run_csynth",
                return_value=("succeeded", ""),
            ) as run_csynth:
                with patch.object(
                    general.tools.csim,
                    "run_csim",
                    return_value=("succeeded", ""),
                ) as run_csim:
                    result = general.csynth_and_csim(
                        directory,
                        context,
                        True,
                        testbench_repairer=repairer,
                        max_testbench_repair_attempts=1,
                    )

        self.assertFalse(result[0])
        self.assertEqual(result[1], "csynth")
        self.assertEqual(result[2], ("succeeded", ""))
        self.assertEqual(result[3], "csim")
        self.assertEqual(result[4], ("succeeded", ""))
        run_csynth.assert_called_once()
        run_csim.assert_called_once()
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(context["testbench"].strip(), FIXED_TB.strip())

        repair = context["testbench_repair"]
        self.assertEqual(repair["status"], "passed")
        self.assertEqual(repair["repair_attempts_used"], 1)
        self.assertEqual(repair["gate_decision"], "continue_to_csynth")
        self.assertEqual(repair["model_usage"]["calls"], 1)
        self.assertEqual(repair["model_usage"]["total_tokens"], 15)
        self.assertEqual(repair["model_usage"]["cost_usd"], 0.001)
        self.assertEqual(
            context["testbench_preflight"]["status"],
            "passed",
        )

    def test_zero_repair_budget_stops_before_vitis(self) -> None:
        provider = FakeRepairProvider(FIXED_TB)
        repairer = make_repairer(provider)
        context = make_context(BROKEN_TB)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general.tools.csynth,
                "run_csynth",
            ) as run_csynth:
                with patch.object(
                    general.tools.csim,
                    "run_csim",
                ) as run_csim:
                    result = general.csynth_and_csim(
                        directory,
                        context,
                        True,
                        testbench_repairer=repairer,
                        max_testbench_repair_attempts=0,
                    )

        self.assertTrue(result[0])
        self.assertEqual(result[1], "testbench_preflight")
        self.assertEqual(result[2][0], "tb_compile_failed")
        self.assertEqual(provider.calls, [])
        run_csynth.assert_not_called()
        run_csim.assert_not_called()
        self.assertNotIn("testbench_repair", context)

    def test_candidate_owned_failure_does_not_call_repair_model(self) -> None:
        provider = FakeRepairProvider(FIXED_TB)
        repairer = make_repairer(provider)
        context = make_context(FIXED_TB, BAD_CANDIDATE)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general.tools.csynth,
                "run_csynth",
            ) as run_csynth:
                with patch.object(
                    general.tools.csim,
                    "run_csim",
                ) as run_csim:
                    result = general.csynth_and_csim(
                        directory,
                        context,
                        True,
                        testbench_repairer=repairer,
                        max_testbench_repair_attempts=2,
                    )

        self.assertTrue(result[0])
        self.assertEqual(result[1], "testbench_preflight")
        self.assertEqual(
            result[2][0],
            "candidate_compile_failed",
        )
        self.assertEqual(provider.calls, [])
        run_csynth.assert_not_called()
        run_csim.assert_not_called()
        self.assertEqual(
            context["testbench_repair"]["status"],
            "failed",
        )
        self.assertEqual(
            context["testbench_repair"]["model_usage"]["calls"],
            0,
        )
        self.assertEqual(
            context["testbench_preflight"]["failure_owner"],
            "candidate",
        )

    def test_model_usage_is_scoped_to_each_gate_invocation(self) -> None:
        provider = FakeRepairProvider(FIXED_TB)
        repairer = make_repairer(provider)

        for invocation in range(2):
            context = make_context(BROKEN_TB)
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(
                    general.tools.csynth,
                    "run_csynth",
                    return_value=("succeeded", ""),
                ):
                    with patch.object(
                        general.tools.csim,
                        "run_csim",
                        return_value=("succeeded", ""),
                    ):
                        result = general.csynth_and_csim(
                            directory,
                            context,
                            True,
                            testbench_repairer=repairer,
                            max_testbench_repair_attempts=1,
                        )

            self.assertFalse(result[0])
            self.assertEqual(
                context["testbench_repair"]["model_usage"]["calls"],
                1,
            )
            self.assertEqual(
                context["testbench_repair"]["model_usage"][
                    "total_tokens"
                ],
                15,
            )
            self.assertEqual(len(provider.calls), invocation + 1)


if __name__ == "__main__":
    unittest.main()
