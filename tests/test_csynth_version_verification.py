import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autogen.agentchat.group import ContextVariables

from flow.tools import csynth


def command_resolution() -> dict:
    return {
        "command": (
            "vitis-run --mode hls --tcl --input_file vitis.tcl"
        ),
        "command_source": "builtin_default",
        "executable": "vitis-run",
        "resolved_executable": "/mock/bin/vitis-run",
    }


def make_context(version: str = "2023.2") -> ContextVariables:
    return ContextVariables(
        data={
            "curr_code": 'extern "C" void top_hls() {}\n',
            "new_kernel_name": "top_hls",
            "target_profile": {
                "toolchain_version": version,
            },
        }
    )


class CsynthVersionVerificationTests(unittest.TestCase):
    def test_extracts_version_from_real_banner_shape(self) -> None:
        version = csynth._extract_vitis_version(
            "****** vitis-run v2023.2 (64-bit)\n"
            "**** SW Build 4026344\n"
        )

        self.assertEqual(version, "2023.2")

    def test_probe_reports_matched_version(self) -> None:
        completed = MagicMock(
            returncode=0,
            stdout="****** vitis-run v2023.2 (64-bit)\n",
            stderr="",
        )

        with patch.object(
            csynth.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = csynth.probe_csynth_version(
                command_resolution(),
                "2023.2",
            )

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["actual"], "2023.2")
        run.assert_called_once_with(
            ["/mock/bin/vitis-run", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )

    def test_probe_reports_mismatch(self) -> None:
        completed = MagicMock(
            returncode=0,
            stdout="****** vitis-run v2024.1 (64-bit)\n",
            stderr="",
        )

        with patch.object(
            csynth.subprocess,
            "run",
            return_value=completed,
        ):
            result = csynth.probe_csynth_version(
                command_resolution(),
                "2023.2",
            )

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["actual"], "2024.1")

    def test_probe_reports_unparseable_output(self) -> None:
        completed = MagicMock(
            returncode=0,
            stdout="Vitis launcher\n",
            stderr="",
        )

        with patch.object(
            csynth.subprocess,
            "run",
            return_value=completed,
        ):
            result = csynth.probe_csynth_version(
                command_resolution(),
                "2023.2",
            )

        self.assertEqual(result["status"], "unparseable")
        self.assertIsNone(result["actual"])

    def test_probe_reports_timeout(self) -> None:
        with patch.object(
            csynth.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["vitis-run", "--version"],
                timeout=20,
            ),
        ):
            result = csynth.probe_csynth_version(
                command_resolution(),
                "2023.2",
            )

        self.assertEqual(result["status"], "probe_timeout")

    def test_mismatch_blocks_synthesis_and_persists_evidence(
        self,
    ) -> None:
        mismatch = {
            "status": "mismatch",
            "requested": "2023.2",
            "actual": "2024.1",
            "probe_command": "/mock/bin/vitis-run --version",
            "probe_source": "resolved_executable",
            "returncode": 0,
            "stdout": "****** vitis-run v2024.1 (64-bit)\n",
            "stderr": "",
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
                    return_value=mismatch,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                    ) as run_cmd:
                        with self.assertRaises(RuntimeError):
                            csynth.run_csynth(
                                directory,
                                make_context(),
                            )

            saved = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        run_cmd.assert_not_called()
        self.assertEqual(
            saved["toolchain_version_verification"]["status"],
            "mismatch",
        )
        self.assertEqual(
            saved["execution"]["status"],
            "blocked_before_csynth",
        )

    def test_matched_version_allows_synthesis(self) -> None:
        matched = {
            "status": "matched",
            "requested": "2023.2",
            "actual": "2023.2",
            "probe_command": "/mock/bin/vitis-run --version",
            "probe_source": "resolved_executable",
            "returncode": 0,
            "stdout": "****** vitis-run v2023.2 (64-bit)\n",
            "stderr": "",
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
                    return_value=matched,
                ):
                    with patch.object(
                        csynth.tools.general,
                        "run_cmd",
                        return_value={
                            "returncode": 1,
                            "stdout": "",
                            "stderr": "synthetic failure",
                            "timeout": False,
                        },
                    ) as run_cmd:
                        result = csynth.run_csynth(
                            directory,
                            make_context(),
                        )

            saved = json.loads(
                (
                    Path(directory)
                    / "csynth_invocation.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result[0], "csynth_failed")
        run_cmd.assert_called_once()
        self.assertEqual(
            saved["toolchain_version_verification"]["status"],
            "matched",
        )
        self.assertEqual(
            saved["execution"]["status"],
            "completed",
        )


if __name__ == "__main__":
    unittest.main()
