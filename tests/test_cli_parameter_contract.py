
from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from agrefactor.cli import build_parser
from agrefactor.config import (
    CSIM_TIMEOUT_SAFETY_CEILING,
    CSYNTH_TIMEOUT_SAFETY_CEILING,
    DEFAULT_CSIM_TIMEOUT_S,
    DEFAULT_CSYNTH_TIMEOUT_S,
    DEFAULT_HIDDEN_COVERAGE_ROUNDS,
    DEFAULT_HIDDEN_GENERATION_TRAJECTORIES,
    DEFAULT_PUBLIC_COVERAGE_ROUNDS,
    DEFAULT_PUBLIC_GENERATION_TRAJECTORIES,
    REPAIR_ATTEMPT_SAFETY_CEILING,
    TEST_GENERATION_COUNT_SAFETY_CEILING,
)
from agrefactor.models import (
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    KNOWN_MODEL_FAMILY_PROFILES,
)
from agrefactor.product.source_bootstrap import (
    SourceRunLayout,
    _generation_trajectories_from_cli,
    _reasoning_effort_from_cli,
    _target_from_cli,
)
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
)


def source_args(*extra: str):
    return build_parser().parse_args(
        [
            "refactor",
            "kernel.cpp",
            "--top",
            "process_top",
            "--model",
            "deepseek-v4-flash",
            *extra,
        ]
    )


class CliParameterContractTests(unittest.TestCase):
    def test_normal_reasoning_default_is_medium(self):
        args = source_args()
        self.assertEqual(args.reasoning_effort, "medium")
        self.assertFalse(args.reasoning_effort_explicit)
        self.assertEqual(_reasoning_effort_from_cli(args), "medium")

    def test_implicit_generic_reasoning_default_is_provider_managed(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "process_top",
                "--model",
                "custom-model",
            ]
        )
        self.assertEqual(args.reasoning_effort, "medium")
        self.assertIsNone(_reasoning_effort_from_cli(args))
        self.assertEqual(
            GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
            .reasoning_policy.to_manifest()["actions"],
            {"low": "reject", "medium": "reject", "high": "reject"},
        )

    def test_explicit_generic_reasoning_is_not_silently_rewritten(self):
        args = build_parser().parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "process_top",
                "--model",
                "custom-model",
                "--reasoning-effort",
                "high",
            ]
        )
        self.assertTrue(args.reasoning_effort_explicit)
        self.assertEqual(_reasoning_effort_from_cli(args), "high")

    def test_public_none_is_rejected_by_normal_parser(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                source_args("--public-tests", "none")

    def test_generation_defaults_are_independent(self):
        args = source_args()
        self.assertEqual(
            args.public_coverage_rounds,
            DEFAULT_PUBLIC_COVERAGE_ROUNDS,
        )
        self.assertEqual(
            args.hidden_coverage_rounds,
            DEFAULT_HIDDEN_COVERAGE_ROUNDS,
        )
        self.assertEqual(
            args.public_generation_trajectories,
            DEFAULT_PUBLIC_GENERATION_TRAJECTORIES,
        )
        self.assertEqual(
            args.hidden_generation_trajectories,
            DEFAULT_HIDDEN_GENERATION_TRAJECTORIES,
        )
        self.assertEqual(
            _generation_trajectories_from_cli(args),
            (
                DEFAULT_PUBLIC_GENERATION_TRAJECTORIES,
                DEFAULT_HIDDEN_GENERATION_TRAJECTORIES,
            ),
        )

    def test_generation_count_ceiling_is_twenty(self):
        self.assertEqual(TEST_GENERATION_COUNT_SAFETY_CEILING, 20)
        args = source_args(
            "--public-coverage-rounds",
            "20",
            "--hidden-coverage-rounds",
            "20",
            "--public-generation-trajectories",
            "20",
            "--hidden-generation-trajectories",
            "20",
        )
        self.assertEqual(args.public_coverage_rounds, 20)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                source_args("--hidden-coverage-rounds", "21")

    def test_deprecated_shared_trajectory_alias_is_bounded(self):
        args = source_args("--test-generation-trajectories", "7")
        self.assertEqual(
            _generation_trajectories_from_cli(args),
            (7, 7),
        )
        conflict = source_args(
            "--test-generation-trajectories",
            "7",
            "--public-generation-trajectories",
            "4",
        )
        with self.assertRaises(ValueError):
            _generation_trajectories_from_cli(conflict)

    def test_repair_ceiling_is_twenty(self):
        self.assertEqual(REPAIR_ATTEMPT_SAFETY_CEILING, 20)
        args = source_args(
            "--max-testbench-repairs",
            "20",
            "--max-candidate-repairs",
            "20",
        )
        self.assertEqual(args.max_testbench_repairs, 20)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                source_args("--max-candidate-repairs", "21")

    def test_source_budget_defaults_and_ceilings(self):
        defaults = (
            DEFAULT_SOURCE_RUN_BUDGET_PROFILE.system_defaults
        )
        ceilings = (
            DEFAULT_SOURCE_RUN_BUDGET_PROFILE.system_safety_ceilings
        )
        self.assertEqual(
            (
                defaults.max_llm_calls,
                defaults.max_tool_calls,
                defaults.max_compile_calls,
                defaults.max_csim_calls,
                defaults.max_csynth_calls,
                defaults.max_wall_time_s,
            ),
            (64, 128, 48, 32, 16, 7200.0),
        )
        self.assertEqual(
            (
                ceilings.max_llm_calls,
                ceilings.max_tool_calls,
                ceilings.max_compile_calls,
                ceilings.max_csim_calls,
                ceilings.max_csynth_calls,
                ceilings.max_wall_time_s,
            ),
            (256, 512, 192, 128, 64, 14400.0),
        )

    def test_model_request_timeout_is_240(self):
        self.assertTrue(KNOWN_MODEL_FAMILY_PROFILES)
        for profile in KNOWN_MODEL_FAMILY_PROFILES:
            self.assertEqual(profile.request_timeout_s, 240.0)

    def test_normal_and_advanced_tool_timeout_defaults(self):
        source = source_args()
        self.assertEqual(source.csim_timeout_s, DEFAULT_CSIM_TIMEOUT_S)
        self.assertEqual(
            source.csynth_timeout_s,
            DEFAULT_CSYNTH_TIMEOUT_S,
        )
        advanced = build_parser().parse_args(
            ["run", "task.json", "--dry-run"]
        )
        self.assertEqual(
            advanced.csim_timelimit,
            DEFAULT_CSIM_TIMEOUT_S,
        )
        self.assertEqual(
            advanced.csynth_timelimit,
            DEFAULT_CSYNTH_TIMEOUT_S,
        )
        self.assertEqual(CSIM_TIMEOUT_SAFETY_CEILING, 600)
        self.assertEqual(CSYNTH_TIMEOUT_SAFETY_CEILING, 3600)

    def test_timeout_ceilings_are_enforced(self):
        source_args(
            "--csim-timeout-s",
            "600",
            "--csynth-timeout-s",
            "3600",
        )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                source_args("--csim-timeout-s", "601")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                source_args("--csynth-timeout-s", "3601")

    def test_replace_compile_flag_replaces_profile_defaults(self):
        args = source_args(
            "--replace-compile-flag=-O2",
            "--replace-compile-flag=-Iinclude",
        )
        target = _target_from_cli(args)
        self.assertEqual(target.compile_flags, ("-O2", "-Iinclude"))

    def test_hidden_compile_flag_alias_remains_compatible(self):
        args = source_args("--compile-flag=-O3")
        target = _target_from_cli(args)
        self.assertEqual(target.compile_flags, ("-O3",))
        conflict = source_args(
            "--replace-compile-flag=-O2",
            "--compile-flag=-O3",
        )
        with self.assertRaises(ValueError):
            _target_from_cli(conflict)

    def test_output_dir_is_exact_persistent_artifact_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "artifacts"
            work = base / "work"
            layout = SourceRunLayout.create(
                "example",
                work_base=work,
                artifact_root_override=output,
            )
            self.assertEqual(layout.artifact_root, output.resolve())
            self.assertEqual(
                layout.work_root,
                work / "source_run_example",
            )
            self.assertNotEqual(
                layout.artifact_root,
                layout.work_root,
            )

    def test_help_exposes_new_contract_and_hides_aliases(self):
        parser = build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        help_text = subparsers.choices["refactor"].format_help()
        for option in (
            "--replace-compile-flag",
            "--hidden-coverage-rounds",
            "--public-generation-trajectories",
            "--hidden-generation-trajectories",
            "--csim-timeout-s",
            "--csynth-timeout-s",
            "--output-dir",
            "--run-id",
        ):
            self.assertIn(option, help_text)
        self.assertNotIn("--compile-flag", help_text)
        self.assertNotIn("--test-generation-trajectories", help_text)
        self.assertIn("does not stop execution", help_text)


if __name__ == "__main__":
    unittest.main()
