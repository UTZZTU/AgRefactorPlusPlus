import json
import tempfile
import unittest
from pathlib import Path

from agrefactor.evaluation import TestbenchPreflight
from agrefactor.testing import (
    TestbenchRepairLoop,
    TestbenchRepairStatus,
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

VALID_TB = r"""
extern "C" void process_top(int, int *, int *);
extern "C" void process_top_hls(int, int *, int *);

int main() {
    int input[2] = {2, 1};
    int original[2] = {};
    int candidate[2] = {};
    process_top(2, input, original);
    process_top_hls(2, input, candidate);
    return original[0] != candidate[0]
        || original[1] != candidate[1];
}
"""

BROKEN_TB = r"""
extern "C" void process_top(int, int *, int *);
extern "C" void process_top_hls(int, int *, int *);
extern node *root;

int main() {
    root = nullptr;
    int input[2] = {2, 1};
    int original[2] = {};
    int candidate[2] = {};
    process_top(2, input, original);
    process_top_hls(2, input, candidate);
    return 0;
}
"""


class RecordingRepairer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def repair(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected repair call")
        return self.responses.pop(0)


class TestbenchRepairLoopTests(unittest.TestCase):
    def make_loop(self, repairer, attempts=2):
        return TestbenchRepairLoop(
            preflight=TestbenchPreflight(),
            repairer=repairer,
            max_repair_attempts=attempts,
        )

    def test_valid_testbench_skips_repair(self) -> None:
        repairer = RecordingRepairer([])
        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(repairer).run(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempts_used, 0)
        self.assertEqual(repairer.requests, [])

    def test_repairs_testbench_owned_compile_failure(self) -> None:
        repairer = RecordingRepairer([VALID_TB])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(repairer).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
            artifact = json.loads(
                Path(result.artifact_path).read_text(encoding="utf-8")
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempts_used, 1)
        self.assertEqual(len(repairer.requests), 1)
        self.assertEqual(
            repairer.requests[0].preflight.failure_owner.value,
            "testbench",
        )
        self.assertEqual(artifact["status"], "passed")
        self.assertEqual(
            artifact["attempts"][0]["preflight"]["failure_owner"],
            "testbench",
        )
        self.assertEqual(
            artifact["attempts"][1]["preflight"]["status"],
            "passed",
        )

    def test_candidate_failure_does_not_call_testbench_repairer(self) -> None:
        bad_candidate = CANDIDATE.replace(
            "for (int i = 0; i < n; ++i) output[i] = input[i];",
            "this is invalid C++;",
        )
        repairer = RecordingRepairer([])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(repairer).run(
                work_dir=directory,
                testbench_code=VALID_TB,
                original_code=ORIGINAL,
                candidate_code=bad_candidate,
            )

        self.assertEqual(result.status, TestbenchRepairStatus.FAILED)
        self.assertIn("owner=candidate", result.reason)
        self.assertEqual(repairer.requests, [])

    def test_unchanged_proposal_is_an_error(self) -> None:
        repairer = RecordingRepairer([BROKEN_TB])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(repairer).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        self.assertEqual(result.status, TestbenchRepairStatus.ERROR)
        self.assertIn("unchanged", result.reason)
        self.assertEqual(result.repair_attempts_used, 0)

    def test_zero_budget_returns_exhausted(self) -> None:
        repairer = RecordingRepairer([])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(
                repairer,
                attempts=0,
            ).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        self.assertEqual(result.status, TestbenchRepairStatus.EXHAUSTED)
        self.assertEqual(result.repair_attempts_used, 0)
        self.assertEqual(repairer.requests, [])

    def test_repair_budget_is_bounded(self) -> None:
        broken_one = BROKEN_TB.replace(
            "extern node *root;",
            "extern unknown_one *root;",
        )
        broken_two = BROKEN_TB.replace(
            "extern node *root;",
            "extern unknown_two *root;",
        )
        repairer = RecordingRepairer([broken_one, broken_two])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(
                repairer,
                attempts=2,
            ).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        self.assertEqual(result.status, TestbenchRepairStatus.EXHAUSTED)
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertEqual(len(repairer.requests), 2)


if __name__ == "__main__":
    unittest.main()
