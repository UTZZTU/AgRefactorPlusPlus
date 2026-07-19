from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from agrefactor.config import (
    DEFAULT_TARGET_PROFILE_NAME,
    TargetProfile,
    TargetResourceLimits,
    TaskSpec,
    available_target_profile_names,
    default_target_profile,
    resolve_target_profile,
    target_profile_config_dir,
)
from agrefactor.runtime import (
    BudgetManager,
    CsynthStageInputs,
    CsynthValidationStageHandler,
    RunContext,
    TraceRecorder,
    read_csynth_invocation_summary,
)
from flow.tools import csynth


CANDIDATE = (
    'extern "C" int candidate_top(int x) '
    "{ return x + 1; }\n"
)


class TargetProfileBatchATests(unittest.TestCase):
    def test_named_profile_registry_is_finite_and_stable(self):
        self.assertEqual(
            available_target_profile_names(),
            (DEFAULT_TARGET_PROFILE_NAME,),
        )

    def test_named_profile_json_exists_and_matches_name(self):
        path = (
            target_profile_config_dir()
            / "vitis-2023.2-default.json"
        )
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(
            data["name"],
            DEFAULT_TARGET_PROFILE_NAME,
        )

    def test_named_profile_template_contains_no_secret_keys(self):
        text = (
            target_profile_config_dir()
            / "vitis-2023.2-default.json"
        ).read_text(encoding="utf-8").lower()
        for forbidden in (
            "api_key",
            "access_token",
            "password",
            "authorization",
            "secret",
        ):
            self.assertNotIn(forbidden, text)

    def test_default_profile_preserves_legacy_values(self):
        profile = default_target_profile()
        self.assertEqual(
            profile.name,
            "vitis-2023.2-default",
        )
        self.assertEqual(
            profile.executable,
            "vitis-run",
        )
        self.assertIsNone(profile.settings_path)
        self.assertEqual(
            profile.parser_profile,
            "vitis-hls-2023.2",
        )
        self.assertEqual(
            profile.resource_limits.to_dict(),
            {
                "max_bram_18k": None,
                "max_dsp": None,
                "max_ff": None,
                "max_lut": None,
                "max_uram": None,
            },
        )
        self.assertEqual(
            profile.device,
            "xcu200-fsgd2104-2-e",
        )
        self.assertEqual(
            profile.clock_period_ns,
            5.0,
        )
        self.assertEqual(
            profile.compile_flags,
            ("-D XILINX",),
        )

    def test_named_profile_provenance_covers_every_field(self):
        profile = default_target_profile()
        self.assertEqual(
            set(profile.field_provenance),
            {
                "name",
                "toolchain",
                "toolchain_version",
                "device",
                "clock_period_ns",
                "compile_flags",
                "executable",
                "settings_path",
                "parser_profile",
                "resource_limits.max_bram_18k",
                "resource_limits.max_dsp",
                "resource_limits.max_ff",
                "resource_limits.max_lut",
                "resource_limits.max_uram",
            },
        )
        self.assertTrue(
            all(
                value.startswith(
                    "named_profile:vitis-2023.2-default"
                )
                for value in (
                    profile.field_provenance.values()
                )
            )
        )

    def test_effective_dict_separates_values_and_provenance(self):
        value = default_target_profile().to_effective_dict()
        self.assertEqual(value["schema_version"], 2)
        self.assertIn("profile", value)
        self.assertIn("field_provenance", value)
        self.assertNotIn(
            "field_provenance",
            value["profile"],
        )

    def test_direct_constructor_uses_direct_provenance(self):
        profile = TargetProfile(
            name="direct",
            toolchain="vitis_hls",
        )
        self.assertTrue(
            all(
                value == "direct_constructor"
                for value in (
                    profile.field_provenance.values()
                )
            )
        )

    def test_resource_limits_round_trip(self):
        limits = TargetResourceLimits(
            max_bram_18k=100,
            max_dsp=20,
            max_ff=3000,
            max_lut=2000,
            max_uram=2,
        )
        self.assertEqual(
            TargetResourceLimits.from_dict(
                limits.to_dict()
            ),
            limits,
        )

    def test_resource_limits_reject_bool_negative_and_unknown(self):
        with self.assertRaises(TypeError):
            TargetResourceLimits(max_dsp=True)
        with self.assertRaises(ValueError):
            TargetResourceLimits(max_lut=-1)
        with self.assertRaises(ValueError):
            TargetResourceLimits.from_dict(
                {"max_unknown": 1}
            )

    def test_partial_resource_override_preserves_other_limits(self):
        base = resolve_target_profile(
            {
                "resource_limits": {
                    "max_dsp": 10,
                    "max_lut": 20,
                }
            }
        )
        updated = resolve_target_profile(
            {
                "resource_limits": {
                    "max_dsp": 12,
                }
            }
        )
        self.assertEqual(
            base.resource_limits.max_lut,
            20,
        )
        self.assertEqual(
            updated.resource_limits.max_dsp,
            12,
        )
        self.assertIsNone(
            updated.resource_limits.max_lut
        )
        self.assertEqual(
            updated.field_provenance[
                "resource_limits.max_dsp"
            ],
            (
                "task_override:"
                "resource_limits.max_dsp"
            ),
        )

    def test_override_provenance_records_clock_and_flags(self):
        profile = resolve_target_profile(
            {
                "clock_frequency_mhz": 250,
                "append_compile_flags": [
                    "-I include"
                ],
            }
        )
        self.assertEqual(
            profile.field_provenance[
                "clock_period_ns"
            ],
            "task_override:clock_frequency_mhz",
        )
        self.assertEqual(
            profile.field_provenance[
                "compile_flags"
            ],
            (
                "task_override:"
                "append_compile_flags"
            ),
        )

    def test_settings_path_must_be_absolute(self):
        with self.assertRaises(ValueError):
            resolve_target_profile(
                {"settings_path": "relative/settings64.sh"}
            )

    def test_parser_profile_uses_stable_identifier(self):
        with self.assertRaises(ValueError):
            resolve_target_profile(
                {"parser_profile": "Vitis HLS 2023.2"}
            )

    def test_profile_round_trip_preserves_new_values(self):
        profile = TargetProfile(
            name="round-trip",
            toolchain="vitis_hls",
            executable="/opt/vitis-run",
            settings_path="/opt/settings64.sh",
            parser_profile="vitis-hls-test",
            resource_limits=TargetResourceLimits(
                max_dsp=4
            ),
        )
        restored = TargetProfile.from_dict(
            profile.to_dict()
        )
        self.assertEqual(restored, profile)


class CsynthExecutionContractBatchATests(
    unittest.TestCase
):
    def test_default_command_remains_backward_compatible(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(
                csynth.CSYNTH_EXECUTABLE_ENV,
                None,
            )
            os.environ.pop(
                csynth.CSYNTH_SETTINGS_ENV,
                None,
            )
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                resolution = (
                    csynth.resolve_csynth_command()
                )
        self.assertEqual(
            resolution["command"],
            (
                "vitis-run --mode hls --tcl "
                "--input_file vitis.tcl"
            ),
        )
        self.assertEqual(
            resolution["command_source"],
            "builtin_default",
        )
        self.assertEqual(
            resolution["profile_name"],
            "vitis-2023.2-default",
        )

    def test_profile_executable_is_used_without_routing(self):
        profile = resolve_target_profile(
            {
                "executable": "/opt/vitis/bin/vitis-run",
            }
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(
                csynth.CSYNTH_EXECUTABLE_ENV,
                None,
            )
            os.environ.pop(
                csynth.CSYNTH_SETTINGS_ENV,
                None,
            )
            with patch.object(
                csynth.shutil,
                "which",
                return_value=(
                    "/opt/vitis/bin/vitis-run"
                ),
            ):
                resolution = (
                    csynth.resolve_csynth_command(
                        profile
                    )
                )
        self.assertEqual(
            resolution["command_source"],
            "target_profile:vitis-2023.2-default",
        )
        self.assertEqual(
            resolution[
                "effective_value_provenance"
            ]["executable"],
            "task_override:executable",
        )

    def test_environment_executable_override_has_provenance(self):
        with patch.dict(
            os.environ,
            {
                csynth.CSYNTH_EXECUTABLE_ENV: (
                    "/env/vitis-run"
                )
            },
            clear=False,
        ):
            os.environ.pop(
                csynth.CSYNTH_SETTINGS_ENV,
                None,
            )
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/env/vitis-run",
            ):
                resolution = (
                    csynth.resolve_csynth_command()
                )
        self.assertEqual(
            resolution["command_source"],
            (
                "environment:"
                + csynth.CSYNTH_EXECUTABLE_ENV
            ),
        )
        self.assertEqual(
            resolution[
                "effective_value_provenance"
            ]["executable"],
            (
                "environment:"
                + csynth.CSYNTH_EXECUTABLE_ENV
            ),
        )

    def test_settings_path_builds_sourced_command_and_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = (
                Path(directory) / "settings64.sh"
            )
            settings.write_text(
                "export PATH=/mock/bin:$PATH\n",
                encoding="utf-8",
            )
            profile = resolve_target_profile(
                {
                    "settings_path": str(settings),
                }
            )
            with patch.dict(
                os.environ,
                {},
                clear=False,
            ):
                os.environ.pop(
                    csynth.CSYNTH_EXECUTABLE_ENV,
                    None,
                )
                os.environ.pop(
                    csynth.CSYNTH_SETTINGS_ENV,
                    None,
                )
                with patch.object(
                    csynth.shutil,
                    "which",
                    return_value=None,
                ):
                    resolution = (
                        csynth.resolve_csynth_command(
                            profile
                        )
                    )
        self.assertIn("bash -lc", resolution["command"])
        self.assertIn("source", resolution["command"])
        self.assertEqual(
            resolution["probe_argv"][:2],
            ["bash", "-lc"],
        )
        self.assertEqual(
            resolution["resolved_settings_path"],
            str(settings),
        )

    def test_empty_settings_environment_is_rejected(self):
        with patch.dict(
            os.environ,
            {csynth.CSYNTH_SETTINGS_ENV: "   "},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                csynth.resolve_csynth_command()

    def test_missing_settings_is_reported_before_probe(self):
        profile = resolve_target_profile(
            {"settings_path": "/missing/settings64.sh"}
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(
                csynth.CSYNTH_EXECUTABLE_ENV,
                None,
            )
            os.environ.pop(
                csynth.CSYNTH_SETTINGS_ENV,
                None,
            )
            resolution = (
                csynth.resolve_csynth_command(profile)
            )
        with patch.object(
            csynth.subprocess,
            "run",
        ) as run:
            result = csynth.probe_csynth_version(
                resolution,
                "2023.2",
            )
        run.assert_not_called()
        self.assertEqual(
            result["status"],
            "settings_not_found",
        )

    def test_run_csynth_persists_parser_resources_and_provenance(self):
        target = {
            "parser_profile": "vitis-hls-test",
            "resource_limits": {
                "max_dsp": 8,
                "max_lut": 100,
            },
        }
        context = ContextVariables(
            data={
                "curr_code": CANDIDATE,
                "new_kernel_name": "candidate_top",
                "target_profile": target,
            }
        )
        matched = {
            "status": "matched",
            "requested": "2023.2",
            "actual": "2023.2",
            "probe_command": (
                "/mock/bin/vitis-run --version"
            ),
            "probe_source": "resolved_executable",
            "returncode": 0,
            "stdout": "vitis-run v2023.2\n",
            "stderr": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ), patch.object(
                csynth,
                "probe_csynth_version",
                return_value=matched,
            ), patch.object(
                csynth.tools.general,
                "run_cmd",
                return_value={
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "synthetic failure",
                    "timeout": False,
                },
            ):
                csynth.run_csynth(
                    directory,
                    context,
                )
            invocation = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )
            effective = json.loads(
                (
                    Path(directory)
                    / "effective_target_profile.json"
                ).read_text(encoding="utf-8")
            )
        self.assertEqual(
            invocation["parser_profile"],
            "vitis-hls-test",
        )
        self.assertEqual(
            invocation["resource_limits"][
                "max_dsp"
            ],
            8,
        )
        self.assertEqual(
            invocation["target_profile_provenance"][
                "parser_profile"
            ],
            "task_override:parser_profile",
        )
        self.assertEqual(
            effective["schema_version"],
            2,
        )
        self.assertEqual(
            effective["field_provenance"][
                "resource_limits.max_dsp"
            ],
            (
                "task_override:"
                "resource_limits.max_dsp"
            ),
        )

    def test_invocation_summary_exposes_safe_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (
                path / "csynth_invocation.json"
            ).write_text(
                json.dumps(
                    {
                        "budget": {
                            "status": "consumed"
                        },
                        "execution": {
                            "status": "completed",
                            "returncode": 0,
                            "timeout": False,
                        },
                        "toolchain_version_verification": {
                            "status": "matched",
                            "requested": "2023.2",
                            "actual": "2023.2",
                        },
                        "target_profile": {
                            "name": "default",
                            "device": "device",
                        },
                        "parser_profile": (
                            "vitis-hls-2023.2"
                        ),
                        "resource_limits": {
                            "max_dsp": 4
                        },
                        "target_profile_provenance": {
                            "parser_profile": (
                                "named_profile:default"
                            )
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = (
                read_csynth_invocation_summary(path)
            )
        self.assertEqual(
            summary["parser_profile"],
            "vitis-hls-2023.2",
        )
        self.assertEqual(
            summary["resource_limits"][
                "max_dsp"
            ],
            4,
        )
        self.assertEqual(
            summary["target_profile_provenance"][
                "parser_profile"
            ],
            "named_profile:default",
        )

    def test_stage_handler_preserves_target_profile_object(self):
        target = resolve_target_profile(
            {
                "parser_profile": "vitis-hls-stage",
                "resource_limits": {
                    "max_dsp": 2
                },
            }
        )
        task = TaskSpec(
            task_id="batch-a-stage",
            kernel_path="candidate.cpp",
            kernel_name="candidate_top",
            target=target,
        )
        observed = {}

        def executor(
            work_dir,
            variables,
            timelimit,
            *,
            budget,
        ):
            observed["target"] = variables[
                "target_profile"
            ]
            path = Path(work_dir)
            path.mkdir(
                parents=True,
                exist_ok=True,
            )
            (
                path / "csynth_invocation.json"
            ).write_text(
                json.dumps(
                    {
                        "budget": {
                            "status": "consumed"
                        },
                        "execution": {
                            "status": "completed",
                            "returncode": 0,
                            "timeout": False,
                        },
                        "toolchain_version_verification": {
                            "status": "matched",
                            "requested": "2023.2",
                            "actual": "2023.2",
                        },
                        "target_profile": (
                            target.to_dict()
                        ),
                        "parser_profile": (
                            target.parser_profile
                        ),
                        "resource_limits": (
                            target.resource_limits.to_dict()
                        ),
                        "target_profile_provenance": (
                            dict(
                                target.field_provenance
                            )
                        ),
                    }
                ),
                encoding="utf-8",
            )
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as directory:
            context = RunContext(
                run_id="batch-a-stage",
                task=task,
                budget=BudgetManager(),
                trace=TraceRecorder(
                    "batch-a-stage",
                    task_id=task.task_id,
                ),
            )
            report = CsynthValidationStageHandler(
                CsynthStageInputs(
                    work_dir=directory,
                    candidate_code=CANDIDATE,
                ),
                executor=executor,
            )(context)
        self.assertIs(observed["target"], target)
        self.assertEqual(
            report.metadata["parser_profile"],
            "vitis-hls-stage",
        )
        self.assertEqual(
            report.metadata["target_resource_limits"][
                "max_dsp"
            ],
            2,
        )

    def test_env_template_contains_no_committed_api_key(self):
        root = Path(__file__).resolve().parents[1]
        text = (
            root / ".env.example"
        ).read_text(encoding="utf-8")
        self.assertIn("OPENAI_API_KEY=\n", text)
        self.assertNotIn(
            "sk-your-key-here",
            text,
        )
        self.assertIn(
            "AGREFACTOR_VITIS_SETTINGS",
            text,
        )


if __name__ == "__main__":
    unittest.main()
