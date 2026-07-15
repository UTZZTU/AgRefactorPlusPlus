import unittest

from agrefactor.config import (
    RunMode,
    TargetProfile,
    TaskSpec,
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

    def test_constructor_defaults_target_and_mode(self) -> None:
        task = TaskSpec(
            task_id="minimal",
            kernel_path="kernel.cpp",
            kernel_name="top",
        )

        self.assertEqual(task.target, default_target_profile())
        self.assertEqual(task.mode, RunMode.REFACTOR)

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


if __name__ == "__main__":
    unittest.main()
