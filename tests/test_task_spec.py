import unittest

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TargetProfile,
    TaskSpec,
    TestSuiteSpec,
    default_target_profile,
)


class TaskSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        )

    def test_create_refactor_task(self) -> None:
        task = TaskSpec(
            task_id="dfs-refactor",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top",
            target=self.target,
            mode=RunMode.REFACTOR,
        )

        self.assertEqual(task.mode, RunMode.REFACTOR)
        self.assertEqual(task.kernel_name, "process_top")
        self.assertIsNone(task.testbench_path)
        self.assertEqual(task.test_suites, ())

    def test_constructor_defaults_target_and_mode(self) -> None:
        task = TaskSpec(
            task_id="minimal",
            kernel_path="kernel.cpp",
            kernel_name="top",
        )

        self.assertEqual(task.target, default_target_profile())
        self.assertEqual(task.mode, RunMode.REFACTOR)
        self.assertEqual(task.test_suites, ())

    def test_accept_mode_string(self) -> None:
        task = TaskSpec(
            task_id="dfs-full",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top",
            target=self.target,
            mode="full",
        )

        self.assertEqual(task.mode, RunMode.FULL)

    def test_round_trip_dict(self) -> None:
        original = TaskSpec(
            task_id="dfs-optimize",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top_hls",
            target=self.target,
            mode=RunMode.OPTIMIZE,
            testbench_path="src/heterorefactor/dfs/testbench.cpp",
        )

        restored = TaskSpec.from_dict(original.to_dict())

        self.assertEqual(restored, original)

    def test_minimal_dict_uses_defaults(self) -> None:
        task = TaskSpec.from_dict(
            {
                "task_id": "minimal",
                "kernel_path": "kernel.cpp",
                "kernel_name": "top",
            }
        )

        self.assertEqual(task.target, default_target_profile())
        self.assertEqual(task.mode, RunMode.REFACTOR)
        self.assertEqual(task.test_suites, ())

    def test_partial_target_override(self) -> None:
        task = TaskSpec.from_dict(
            {
                "task_id": "faster-clock",
                "kernel_path": "kernel.cpp",
                "kernel_name": "top",
                "target": {
                    "clock_frequency_mhz": 250,
                },
            }
        )

        self.assertEqual(task.target.clock_period_ns, 4.0)
        self.assertEqual(
            task.target.device,
            "xcu200-fsgd2104-2-e",
        )

    def test_named_target_profile(self) -> None:
        task = TaskSpec.from_dict(
            {
                "task_id": "named-target",
                "kernel_path": "kernel.cpp",
                "kernel_name": "top",
                "target": "default",
            }
        )

        self.assertEqual(task.target, default_target_profile())

    def test_reject_empty_kernel_name(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec(
                task_id="invalid",
                kernel_path="kernel.cpp",
                kernel_name="  ",
                target=self.target,
            )

    def test_reject_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec(
                task_id="invalid-mode",
                kernel_path="kernel.cpp",
                kernel_name="top",
                target=self.target,
                mode="unknown",
            )

    def test_legacy_task_omits_empty_test_suites_from_dict(self) -> None:
        task = TaskSpec(
            task_id="legacy",
            kernel_path="kernel.cpp",
            kernel_name="top",
            testbench_path="testbench.cpp",
        )

        payload = task.to_dict()

        self.assertEqual(task.test_suites, ())
        self.assertNotIn("test_suites", payload)
        self.assertEqual(payload["testbench_path"], "testbench.cpp")

    def test_task_accepts_public_and_hidden_suites(self) -> None:
        public = TestSuiteSpec(
            suite_id="generic-public",
            split=EvaluationSplit.PUBLIC,
            testbench_path="tests/public.cpp",
        )
        hidden = TestSuiteSpec(
            suite_id="generic-hidden",
            split=EvaluationSplit.HIDDEN,
            testbench_path="tests/hidden.cpp",
        )

        task = TaskSpec(
            task_id="with-suites",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=[public, hidden],
        )

        self.assertEqual(task.test_suites, (public, hidden))
        self.assertTrue(
            task.test_suites[0].feedback_visible_to_agent
        )
        self.assertFalse(
            task.test_suites[1].feedback_visible_to_agent
        )

    def test_task_suite_round_trip_dict(self) -> None:
        original = TaskSpec(
            task_id="round-trip-suites",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=(
                TestSuiteSpec(
                    suite_id="array-map-public",
                    suite_version="1",
                    split=EvaluationSplit.PUBLIC,
                    case_count=8,
                    testbench_path="tests/public.cpp",
                ),
                TestSuiteSpec(
                    suite_id="array-map-hidden",
                    suite_version="1",
                    split=EvaluationSplit.HIDDEN,
                    case_count=20,
                    testbench_path="tests/hidden.cpp",
                ),
            ),
        )

        payload = original.to_dict()
        restored = TaskSpec.from_dict(payload)

        self.assertEqual(restored, original)
        self.assertEqual(len(payload["test_suites"]), 2)
        self.assertTrue(
            payload["test_suites"][0][
                "feedback_visible_to_agent"
            ]
        )
        self.assertFalse(
            payload["test_suites"][1][
                "feedback_visible_to_agent"
            ]
        )

    def test_from_dict_accepts_suite_mappings(self) -> None:
        task = TaskSpec.from_dict(
            {
                "task_id": "mapped-suites",
                "kernel_path": "kernel.cpp",
                "kernel_name": "top",
                "test_suites": [
                    {
                        "suite_id": "reduction-public",
                        "split": "public",
                        "case_count": 5,
                    },
                    {
                        "suite_id": "reduction-hidden",
                        "split": "hidden",
                        "case_count": 10,
                    },
                ],
            }
        )

        self.assertEqual(
            [suite.suite_id for suite in task.test_suites],
            ["reduction-public", "reduction-hidden"],
        )

    def test_from_dict_null_suites_preserves_legacy_behavior(self) -> None:
        task = TaskSpec.from_dict(
            {
                "task_id": "legacy-null",
                "kernel_path": "kernel.cpp",
                "kernel_name": "top",
                "testbench_path": "testbench.cpp",
                "test_suites": None,
            }
        )

        self.assertEqual(task.test_suites, ())
        self.assertEqual(task.testbench_path, "testbench.cpp")

    def test_legacy_testbench_and_suite_metadata_can_coexist(self) -> None:
        suite = TestSuiteSpec(
            suite_id="public-metadata",
            split=EvaluationSplit.PUBLIC,
            testbench_path="tests/public.cpp",
        )

        task = TaskSpec(
            task_id="transition",
            kernel_path="kernel.cpp",
            kernel_name="top",
            testbench_path="legacy_testbench.cpp",
            test_suites=(suite,),
        )

        self.assertEqual(
            task.testbench_path,
            "legacy_testbench.cpp",
        )
        self.assertEqual(task.test_suites, (suite,))

    def test_reject_duplicate_suite_ids(self) -> None:
        first = TestSuiteSpec(
            suite_id="duplicate",
            split=EvaluationSplit.PUBLIC,
        )
        second = TestSuiteSpec(
            suite_id="duplicate",
            split=EvaluationSplit.HIDDEN,
        )

        with self.assertRaises(ValueError):
            TaskSpec(
                task_id="invalid",
                kernel_path="kernel.cpp",
                kernel_name="top",
                test_suites=(first, second),
            )

    def test_reject_string_test_suites(self) -> None:
        with self.assertRaises(TypeError):
            TaskSpec(
                task_id="invalid",
                kernel_path="kernel.cpp",
                kernel_name="top",
                test_suites="public",
            )

    def test_reject_mapping_as_test_suites_sequence(self) -> None:
        with self.assertRaises(TypeError):
            TaskSpec.from_dict(
                {
                    "task_id": "invalid",
                    "kernel_path": "kernel.cpp",
                    "kernel_name": "top",
                    "test_suites": {
                        "suite_id": "not-an-array",
                    },
                }
            )

    def test_reject_non_mapping_suite_entry(self) -> None:
        with self.assertRaises(TypeError):
            TaskSpec.from_dict(
                {
                    "task_id": "invalid",
                    "kernel_path": "kernel.cpp",
                    "kernel_name": "top",
                    "test_suites": ["public"],
                }
            )

    def test_suite_metadata_is_kernel_agnostic(self) -> None:
        families = (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        )
        suites = tuple(
            TestSuiteSpec(
                suite_id=f"{family}-public",
                split=EvaluationSplit.PUBLIC,
            )
            for family in families
        )

        task = TaskSpec(
            task_id="generic-families",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=suites,
        )

        self.assertEqual(len(task.test_suites), len(families))


if __name__ == "__main__":
    unittest.main()
