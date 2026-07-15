import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autogen.agentchat.group import ContextVariables

from flow.tools import csynth


CUSTOM_TARGET = {
    "device": "xcu250-figd2104-2L-e",
    "clock_frequency_mhz": 250,
    "append_compile_flags": ["-I include"],
}


MATCHED_VERIFICATION = {
    "status": "matched",
    "requested": "2023.2",
    "actual": "2023.2",
    "probe_command": "/mock/bin/vitis-run --version",
    "probe_source": "resolved_executable",
    "returncode": 0,
    "stdout": "****** vitis-run v2023.2 (64-bit)\n",
    "stderr": "",
}


def make_context(target_profile=None) -> ContextVariables:
    data = {
        "curr_code": 'extern "C" void top_hls() {}\n',
        "new_kernel_name": "top_hls",
    }
    if target_profile is not None:
        data["target_profile"] = target_profile
    return ContextVariables(data=data)


class CsynthExecutionEvidenceTests(unittest.TestCase):
    def test_resolve_command_uses_builtin_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(csynth.CSYNTH_EXECUTABLE_ENV, None)
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                resolution = csynth.resolve_csynth_command()

        self.assertEqual(
            resolution["command"],
            "vitis-run --mode hls --tcl --input_file vitis.tcl",
        )
        self.assertEqual(
            resolution["command_source"],
            "builtin_default",
        )
        self.assertEqual(
            resolution["resolved_executable"],
            "/mock/bin/vitis-run",
        )

    def test_resolve_command_allows_optional_environment_override(
        self,
    ) -> None:
        executable = "/opt/AMD Vitis/2023.2/bin/vitis-run"
        with patch.dict(
            os.environ,
            {csynth.CSYNTH_EXECUTABLE_ENV: executable},
            clear=False,
        ):
            with patch.object(
                csynth.shutil,
                "which",
                return_value=executable,
            ):
                resolution = csynth.resolve_csynth_command()

        self.assertEqual(
            resolution["command"],
            (
                "'/opt/AMD Vitis/2023.2/bin/vitis-run' "
                "--mode hls --tcl --input_file vitis.tcl"
            ),
        )
        self.assertEqual(
            resolution["command_source"],
            "environment:" + csynth.CSYNTH_EXECUTABLE_ENV,
        )

    def test_rejects_empty_environment_override(self) -> None:
        with patch.dict(
            os.environ,
            {csynth.CSYNTH_EXECUTABLE_ENV: "   "},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                csynth.resolve_csynth_command()

    def test_run_csynth_persists_profile_and_invocation(self) -> None:
        observed = {}

        def fake_run_cmd(work_dir, command, timelimit):
            observed["command"] = command
            observed["timelimit"] = timelimit
            observed["profile_before"] = json.loads(
                (
                    Path(work_dir)
                    / "effective_target_profile.json"
                ).read_text(encoding="utf-8")
            )
            observed["invocation_before"] = json.loads(
                (
                    Path(work_dir)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "synthetic failure",
                "timeout": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                    return_value=MATCHED_VERIFICATION,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                        side_effect=fake_run_cmd,
                    ):
                        result = csynth.run_csynth(
                            directory,
                            make_context(CUSTOM_TARGET),
                            timelimit=23,
                        )

            invocation_after = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result[0], "csynth_failed")
        self.assertEqual(observed["timelimit"], 23)
        self.assertEqual(
            observed["profile_before"]["profile"][
                "clock_period_ns"
            ],
            4.0,
        )
        self.assertEqual(
            observed["profile_before"]["profile"]["device"],
            "xcu250-figd2104-2L-e",
        )
        self.assertEqual(
            observed["invocation_before"]["execution"]["status"],
            "pending",
        )
        self.assertEqual(
            observed["invocation_before"][
                "requested_toolchain_version"
            ],
            "2023.2",
        )
        self.assertEqual(
            observed["invocation_before"][
                "toolchain_version_verification"
            ]["status"],
            "matched",
        )
        self.assertEqual(
            observed["invocation_before"][
                "toolchain_version_verification"
            ]["actual"],
            "2023.2",
        )
        self.assertEqual(
            observed["invocation_before"]["resolved_executable"],
            "/mock/bin/vitis-run",
        )
        self.assertEqual(
            invocation_after["execution"],
            {
                "status": "completed",
                "returncode": 1,
                "timeout": False,
            },
        )

    def test_default_profile_is_also_persisted(self) -> None:
        def fake_run_cmd(work_dir, command, timelimit):
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "synthetic failure",
                "timeout": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.shutil,
                "which",
                return_value="/mock/bin/vitis-run",
            ):
                with patch.object(
                    csynth,
                    "probe_csynth_version",
                    return_value=MATCHED_VERIFICATION,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                        side_effect=fake_run_cmd,
                    ):
                        csynth.run_csynth(
                            directory,
                            make_context(),
                        )

            saved = json.loads(
                (
                    Path(directory)
                    / "effective_target_profile.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(
            saved["profile"]["clock_period_ns"],
            5.0,
        )
        self.assertEqual(
            saved["profile"]["device"],
            "xcu200-fsgd2104-2-e",
        )


if __name__ == "__main__":
    unittest.main()
