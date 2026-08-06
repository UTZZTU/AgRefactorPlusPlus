from __future__ import annotations

import inspect
import tempfile
import unittest

from agrefactor.evidence import (
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)
from agrefactor.product import source_bootstrap
from agrefactor.testing import TestbenchRepairLoop


def _result(*, passed: bool) -> TestbenchPreflightResult:
    return TestbenchPreflightResult(
        status=(
            TestbenchPreflightStatus.PASSED
            if passed else TestbenchPreflightStatus.FAILED
        ),
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=(
            TestbenchFailureKind.NONE
            if passed else TestbenchFailureKind.SYNTAX_ERROR
        ),
        failure_owner=(
            TestbenchFailureOwner.NONE
            if passed else TestbenchFailureOwner.TESTBENCH
        ),
        return_code=0 if passed else 1,
        command=("c++",),
    )


class _RecordingPreflight:
    def __init__(self):
        self.calls = []

    def compile_and_link(self, **kwargs):
        self.calls.append(dict(kwargs))
        return _result(passed=len(self.calls) > 1)


class _Repairer:
    def repair(self, request):
        return request.current_testbench + "\n// repaired"


class R5ABTopPropagationTests(unittest.TestCase):
    def test_distinct_tops_reach_initial_and_repaired_preflight(self):
        preflight = _RecordingPreflight()
        loop = TestbenchRepairLoop(
            preflight=preflight,
            repairer=_Repairer(),
            max_repair_attempts=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = loop.run(
                work_dir=directory,
                testbench_code="int main(){return 1;}",
                original_code='extern "C" void original_top(){}',
                candidate_code='extern "C" void candidate_top(){}',
                original_top_function="original_top",
                candidate_top_function="candidate_top",
            )
        self.assertTrue(result.succeeded)
        self.assertEqual(len(preflight.calls), 2)
        for call in preflight.calls:
            self.assertEqual(
                call["original_top_function"], "original_top"
            )
            self.assertEqual(
                call["candidate_top_function"], "candidate_top"
            )

    def test_none_tops_remain_backward_compatible(self):
        preflight = _RecordingPreflight()
        loop = TestbenchRepairLoop(
            preflight=preflight,
            repairer=_Repairer(),
            max_repair_attempts=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            loop.run(
                work_dir=directory,
                testbench_code="int main(){return 1;}",
                original_code="void a(){}",
                candidate_code="void b(){}",
            )
        self.assertNotIn("original_top_function", preflight.calls[0])
        self.assertNotIn("candidate_top_function", preflight.calls[0])

    def test_product_preparation_forwards_both_tops(self):
        source = inspect.getsource(
            source_bootstrap._prepare_public_testbench
        )
        self.assertIn(
            "original_top_function=original_top_function", source
        )
        self.assertIn(
            "candidate_top_function=candidate_top_function", source
        )
        module_source = inspect.getsource(source_bootstrap)
        self.assertIn(
            "original_top_function=self._request.top_function",
            module_source,
        )
        self.assertIn(
            "candidate_top_function=candidate_top",
            module_source,
        )


if __name__ == "__main__":
    unittest.main()
