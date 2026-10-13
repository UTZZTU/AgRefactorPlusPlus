import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agrefactor.config import TargetProfile, TaskSpec
from agrefactor.evaluation import TestbenchPreflight
from agrefactor.testing import (
    TestbenchRepairLoop,
    TestbenchRepairResponseError,
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
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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

    def test_custom_repairer_keeps_complete_mapping_failure_artifact(self) -> None:
        from flow.tools.result_mapping import shared_helper_identity
        from tests.test_public_result_mapping import ORIGINAL as mapping_original, testbench

        public = testbench([3, -2])
        broken = public.replace("int mismatch = 0;", "int mismatch = missing_name;")
        actual = "different observation " * 1000
        altered = public.replace("Original call return status", actual)
        candidate = """void source_task_hls(int input, int *out, int *status) {
            *out = input * 2;
            *status = input < 0 ? 7 : 0;
        }"""
        repairer = RecordingRepairer([altered, public])
        frozen = shared_helper_identity(public)

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(repairer).run(
                work_dir=directory,
                testbench_code=broken,
                original_code=mapping_original,
                candidate_code=candidate,
                original_top_function="source_task",
                candidate_top_function="source_task_hls",
            )
            saved = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
            failure = saved["attempts"][1]
            self.assertIn("evidence_ref=", failure["error"])
            reference = failure["error"].split("\nevidence_ref=", 1)[1]
            artifact = json.loads((Path(directory) / reference).read_text(encoding="utf-8"))
            self.assertEqual(artifact["evidence_view"], "operator_full")
            self.assertEqual(artifact["attempt"], 1)
            self.assertEqual(artifact["proposed_testbench_sha256"],
                             hashlib.sha256(altered.encode("utf-8")).hexdigest())
            self.assertEqual(artifact["frozen_result_mapping_identity"], {
                "shared_cpp_sha256": frozen["shared_cpp_sha256"],
                "manifest_sha256": frozen["manifest_sha256"],
            })
            difference = artifact["contract_evidence"]["differences"][0]
            self.assertEqual(difference["path"], "/observables/0/original")
            self.assertEqual(difference["expected"], "Original call return status")
            self.assertEqual(difference["actual"], actual)
            self.assertIsNone(json.loads(Path(result.repair_attempt_paths[1]).read_text())["model_response"])

        self.assertTrue(result.succeeded, result.reason)
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertEqual(len(repairer.requests), 2)
        summary = repairer.requests[1].prior_attempt_summaries[-1]
        self.assertLessEqual(len(summary), 2200)
        self.assertIn("/observables/0/original", summary)
        self.assertIn("expected", summary)
        self.assertIn("truncated", summary)
        self.assertNotIn(actual, summary)

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

    def test_unchanged_proposal_exhausts_one_attempt(self) -> None:
        repairer = RecordingRepairer([BROKEN_TB])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(
                repairer,
                attempts=1,
            ).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        self.assertEqual(result.status, TestbenchRepairStatus.EXHAUSTED)
        self.assertIn("unchanged", result.reason)
        self.assertEqual(result.repair_attempts_used, 1)
        self.assertEqual(
            result.attempts[-1].action,
            "repair_rejected_unchanged",
        )

    def test_empty_proposal_counts_and_exhausts_one_attempt(
        self,
    ) -> None:
        repairer = RecordingRepairer([""])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(
                repairer,
                attempts=1,
            ).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
            artifact = json.loads(
                Path(result.artifact_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, TestbenchRepairStatus.EXHAUSTED)
        self.assertIn("empty", result.reason)
        self.assertEqual(result.repair_attempts_used, 1)
        self.assertEqual(artifact["repair_attempts_used"], 1)
        self.assertEqual(len(repairer.requests), 1)
        self.assertEqual(
            artifact["attempts"][-1]["action"],
            "repair_rejected_empty",
        )

    def test_provider_exception_counts_and_exhausts_one_attempt(
        self,
    ) -> None:
        repairer = RecordingRepairer([RuntimeError("provider failed")])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(
                repairer,
                attempts=1,
            ).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )
            artifact = json.loads(
                Path(result.artifact_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, TestbenchRepairStatus.EXHAUSTED)
        self.assertIn("provider failed", result.reason)
        self.assertEqual(result.repair_attempts_used, 1)
        self.assertEqual(artifact["repair_attempts_used"], 1)
        self.assertEqual(len(repairer.requests), 1)
        self.assertEqual(
            artifact["attempts"][-1]["action"],
            "repair_provider_error",
        )

    def test_empty_proposal_uses_remaining_budget(self) -> None:
        repairer = RecordingRepairer(["", VALID_TB])

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

        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertEqual(len(repairer.requests), 2)
        self.assertEqual(
            result.attempts[1].action,
            "repair_rejected_empty",
        )
        self.assertEqual(
            result.attempts[2].action,
            "repair_and_preflight",
        )

    def test_provider_exception_uses_remaining_budget(self) -> None:
        repairer = RecordingRepairer(
            [RuntimeError("temporary failure"), VALID_TB]
        )

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

        self.assertTrue(result.succeeded)
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertEqual(len(repairer.requests), 2)
        self.assertEqual(
            result.attempts[1].action,
            "repair_provider_error",
        )
        self.assertIn(
            "temporary failure",
            result.attempts[1].error or "",
        )

    def test_contract_rejection_is_forwarded_to_next_attempt(
        self,
    ) -> None:
        repairer = RecordingRepairer(
            [
                TestbenchRepairResponseError(
                    "repaired testbench violated deterministic "
                    "contract: missing required declaration for "
                    "function: insert; missing required declaration "
                    "for function: dfs_traverse"
                ),
                VALID_TB,
            ]
        )

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

        self.assertTrue(result.succeeded)
        self.assertEqual(len(repairer.requests), 2)
        self.assertEqual(
            repairer.requests[0].prior_attempt_summaries,
            (),
        )
        summaries = (
            repairer.requests[1].prior_attempt_summaries
        )
        self.assertEqual(len(summaries), 2)
        self.assertIn("Attempt 0", summaries[0])
        self.assertIn("Attempt 1", summaries[1])
        self.assertIn("insert", summaries[1])
        self.assertIn("dfs_traverse", summaries[1])

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

    def test_third_repair_receives_both_prior_preflight_failures(
        self,
    ) -> None:
        broken_one = BROKEN_TB.replace(
            "extern node *root;",
            "extern unknown_one *root;",
        )
        broken_two = BROKEN_TB.replace(
            "extern node *root;",
            "extern unknown_two *root;",
        )
        repairer = RecordingRepairer(
            [broken_one, broken_two, VALID_TB]
        )

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(
                repairer,
                attempts=3,
            ).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            repairer.requests[0].prior_attempt_summaries,
            (),
        )
        self.assertEqual(
            len(repairer.requests[2].prior_attempt_summaries),
            3,
        )
        initial, first, second = repairer.requests[2].prior_attempt_summaries
        self.assertIn("Attempt 0", initial)
        self.assertIn("node", initial)
        self.assertIn("Attempt 1", first)
        self.assertIn("preflight status: failed", first)
        self.assertIn("failure owner: testbench", first)
        self.assertIn("next action: repair_testbench", first)
        self.assertIn("unknown_one", first)
        self.assertIn("Attempt 2", second)
        self.assertIn("unknown_two", second)


    def test_explicit_task_is_forwarded_to_repair_request(
        self,
    ) -> None:
        task = TaskSpec(
            task_id="repair-task",
            kernel_path="candidate.cpp",
            kernel_name="process_top_hls",
            target=TargetProfile(
                name="repair-target",
                toolchain="vitis_hls",
                toolchain_version="2024.1",
                device="repair-device",
                clock_period_ns=3.5,
            ),
        )
        repairer = RecordingRepairer([VALID_TB])

        with tempfile.TemporaryDirectory() as directory:
            result = self.make_loop(repairer).run(
                work_dir=directory,
                testbench_code=BROKEN_TB,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
                task=task,
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(repairer.requests), 1)
        self.assertIs(repairer.requests[0].task, task)
        self.assertEqual(
            repairer.requests[0].task.target.name,
            "repair-target",
        )


if __name__ == "__main__":
    unittest.main()
