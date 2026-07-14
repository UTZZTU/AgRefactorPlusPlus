import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from flow.tools import general


ORIGINAL = r'''
extern "C" void process_top(
    int n,
    int *input,
    int *output,
    int *fallback
) {
    for (int i = 0; i < n; ++i) {
        output[i] = input[i];
    }
    *fallback = 0;
}
'''

CANDIDATE = r'''
extern "C" void process_top_hls(
    int n,
    int *input,
    int *output,
    int *fallback
) {
    for (int i = 0; i < n; ++i) {
        output[i] = input[i];
    }
    *fallback = 0;
}
'''

BROKEN_TESTBENCH = r'''
extern "C" void process_top(int, int *, int *, int *);
extern "C" void process_top_hls(int, int *, int *, int *);
extern node *root;

int main() {
    root = nullptr;
    int input[1] = {1};
    int original[1] = {};
    int candidate[1] = {};
    int original_fallback = 0;
    int candidate_fallback = 0;
    process_top(1, input, original, &original_fallback);
    process_top_hls(1, input, candidate, &candidate_fallback);
    return 0;
}
'''

VALID_TESTBENCH = r'''
extern "C" void process_top(int, int *, int *, int *);
extern "C" void process_top_hls(int, int *, int *, int *);

int main() {
    int input[1] = {1};
    int original[1] = {};
    int candidate[1] = {};
    int original_fallback = 0;
    int candidate_fallback = 0;
    process_top(1, input, original, &original_fallback);
    process_top_hls(1, input, candidate, &candidate_fallback);
    return original[0] != candidate[0];
}
'''


def make_context(testbench: str) -> ContextVariables:
    return ContextVariables(
        data={
            "orig_code": ORIGINAL,
            "curr_code": CANDIDATE,
            "testbench": testbench,
            "new_kernel_name": "process_top_hls",
            "code_for_hetero": "",
        }
    )


class LegacyPreflightGateTests(unittest.TestCase):
    def test_broken_testbench_stops_before_vitis(self) -> None:
        context = make_context(BROKEN_TESTBENCH)

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
                    )

            evidence_files = list(
                Path(directory).glob(
                    "testbench_preflight_*/testbench_preflight.json"
                )
            )
            main_csynth_dirs = list(
                Path(directory).glob("csynth_[0-9]*")
            )
            self.assertEqual(len(evidence_files), 1)
            saved = json.loads(
                evidence_files[0].read_text(encoding="utf-8")
            )

        kill_other, first_task, first_res, second_task, second_res = result

        self.assertTrue(kill_other)
        self.assertEqual(first_task, "testbench_preflight")
        self.assertEqual(first_res[0], "tb_compile_failed")
        self.assertIsNone(second_task)
        self.assertIsNone(second_res)
        run_csynth.assert_not_called()
        run_csim.assert_not_called()
        self.assertFalse(main_csynth_dirs)

        evidence = context["testbench_preflight"]
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failure_kind"], "undeclared_type")
        self.assertEqual(evidence["failure_owner"], "testbench")
        self.assertEqual(evidence["next_action"], "repair_testbench")
        self.assertEqual(
            evidence["gate_decision"],
            "stop_before_csynth",
        )

        self.assertEqual(saved["failure_owner"], "testbench")

    def test_valid_testbench_continues_to_csynth_and_csim(self) -> None:
        context = make_context(VALID_TESTBENCH)

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
                    )

        self.assertFalse(result[0])
        self.assertEqual(result[1], "csynth")
        self.assertEqual(result[2], ("succeeded", ""))
        self.assertEqual(result[3], "csim")
        self.assertEqual(result[4], ("succeeded", ""))
        run_csynth.assert_called_once()
        run_csim.assert_called_once()
        self.assertEqual(
            context["testbench_preflight"]["status"],
            "passed",
        )
        self.assertEqual(
            context["testbench_preflight"]["gate_decision"],
            "continue_to_csynth",
        )

    def test_preflight_failure_is_terminal_for_kernel_retry(self) -> None:
        self.assertTrue(
            general.is_terminal_validation_failure(
                "testbench_preflight"
            )
        )
        self.assertFalse(
            general.is_terminal_validation_failure("csynth")
        )
        self.assertFalse(
            general.is_terminal_validation_failure("csim")
        )


if __name__ == "__main__":
    unittest.main()
