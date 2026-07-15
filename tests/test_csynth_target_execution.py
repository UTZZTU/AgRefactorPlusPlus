import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autogen.agentchat.group import ContextVariables

from agrefactor.config import default_target_profile
from flow.tools import csynth, general


CUSTOM_TARGET = {
    "device": "xcu250-figd2104-2L-e",
    "clock_frequency_mhz": 250,
    "append_compile_flags": ["-I include"],
}


def make_context(
    *,
    target_profile=None,
    code_for_hetero: str = "",
) -> ContextVariables:
    data = {
        "orig_code": 'extern "C" void top() {}\n',
        "curr_code": 'extern "C" void top_hls() {}\n',
        "testbench": "int main() { return 0; }\n",
        "new_kernel_name": "top_hls",
        "code_for_hetero": code_for_hetero,
    }
    if target_profile is not None:
        data["target_profile"] = target_profile
    return ContextVariables(data=data)


class CsynthTargetExecutionTests(unittest.TestCase):
    def test_make_csynth_script_writes_custom_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csynth.make_csynth_script(
                directory,
                "top_hls",
                {"top_hls.cpp": "void top_hls() {}\n"},
                target_profile=CUSTOM_TARGET,
            )

            tcl = (
                Path(directory) / "vitis.tcl"
            ).read_text(encoding="utf-8")
            source = (
                Path(directory) / "top_hls.cpp"
            ).read_text(encoding="utf-8")

        self.assertEqual(source, "void top_hls() {}\n")
        self.assertIn(
            'set_part "xcu250-figd2104-2L-e"',
            tcl,
        )
        self.assertIn(
            "create_clock -period 4.0 -name default",
            tcl,
        )
        self.assertIn(
            '-cflags "-D XILINX -I include"',
            tcl,
        )

    def test_run_csynth_uses_context_target_before_command(self) -> None:
        observed = {}

        def fake_run_cmd(work_dir, command, timelimit):
            observed["tcl"] = (
                Path(work_dir) / "vitis.tcl"
            ).read_text(encoding="utf-8")
            observed["command"] = command
            observed["timelimit"] = timelimit
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "synthetic failure",
                "timeout": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.tools.general,
                "run_cmd",
                side_effect=fake_run_cmd,
            ):
                result = csynth.run_csynth(
                    directory,
                    make_context(target_profile=CUSTOM_TARGET),
                    timelimit=17,
                )

        self.assertEqual(result[0], "csynth_failed")
        self.assertEqual(observed["command"], csynth.CSYNTH_CMD)
        self.assertEqual(observed["timelimit"], 17)
        self.assertIn(
            'set_part "xcu250-figd2104-2L-e"',
            observed["tcl"],
        )
        self.assertIn(
            "create_clock -period 4.0 -name default",
            observed["tcl"],
        )

    def test_run_csynth_without_target_uses_default(self) -> None:
        observed = {}

        def fake_run_cmd(work_dir, command, timelimit):
            observed["tcl"] = (
                Path(work_dir) / "vitis.tcl"
            ).read_text(encoding="utf-8")
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "synthetic failure",
                "timeout": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                csynth.tools.general,
                "run_cmd",
                side_effect=fake_run_cmd,
            ):
                csynth.run_csynth(
                    directory,
                    make_context(),
                )

        self.assertIn(
            'set_part "xcu200-fsgd2104-2-e"',
            observed["tcl"],
        )
        self.assertIn(
            "create_clock -period 5.0 -name default",
            observed["tcl"],
        )

    def test_hetero_csynth_child_receives_target(self) -> None:
        context = make_context(
            target_profile=CUSTOM_TARGET,
            code_for_hetero="void top_hls() {}\n",
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                general,
                "run_testbench_validation_gate",
                return_value=SimpleNamespace(succeeded=True),
            ):
                with patch.object(
                    general.tools.csynth,
                    "run_csynth",
                    side_effect=[
                        ("csynth_failed", "hetero"),
                        ("csynth_failed", "main"),
                    ],
                ) as run_csynth:
                    general.csynth_and_csim(
                        directory,
                        context,
                        True,
                    )

        hetero_context = run_csynth.call_args_list[0].kwargs["cv"]
        self.assertEqual(
            hetero_context["target_profile"],
            CUSTOM_TARGET,
        )

    def test_csynth_remote_rejects_target_override(self) -> None:
        context = make_context(target_profile=CUSTOM_TARGET)

        with patch.object(
            csynth,
            "HLS_SERVER_URL",
            "https://example.invalid",
        ):
            with patch.object(
                csynth.requests,
                "post",
            ) as post:
                with self.assertRaises(ValueError):
                    csynth.run_csynth_remote(context)

        post.assert_not_called()

    def test_csynth_remote_allows_default_target(self) -> None:
        context = make_context(
            target_profile=default_target_profile().to_dict()
        )
        response = MagicMock()
        response.json.return_value = {
            "status": "succeeded",
            "error_msg": "",
        }

        with patch.object(
            csynth,
            "HLS_SERVER_URL",
            "https://example.invalid",
        ):
            with patch.object(
                csynth.requests,
                "post",
                return_value=response,
            ) as post:
                result = csynth.run_csynth_remote(context)

        self.assertEqual(result, ("succeeded", ""))
        post.assert_called_once()
        response.raise_for_status.assert_called_once()

    def test_combined_remote_rejects_target_override(self) -> None:
        context = make_context(target_profile=CUSTOM_TARGET)

        with patch.object(
            general,
            "HLS_SERVER_URL",
            "https://example.invalid",
        ):
            with patch.object(
                general.requests,
                "post",
            ) as post:
                with self.assertRaises(ValueError):
                    general.csynth_and_csim_remote(
                        context,
                        True,
                    )

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
