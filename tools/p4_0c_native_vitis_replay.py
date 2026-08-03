#!/usr/bin/env python3
"""Self-contained deterministic P4-0C native-CSIM replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autogen.agentchat.group import ContextVariables

from agrefactor.config import resolve_target_profile
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
)
from flow.tools.vitis_csim import run_vitis_csim


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    fixture = (
        _REPO_ROOT
        / "tests/fixtures/p4_0c_native_vitis"
    )
    profile = resolve_target_profile(None)
    variables = ContextVariables(
        data={
            "orig_code": (
                fixture / "reference.cpp"
            ).read_text(encoding="utf-8"),
            "curr_code": (
                fixture / "candidate.cpp"
            ).read_text(encoding="utf-8"),
            "testbench": (
                fixture / "public_tb.cpp"
            ).read_text(encoding="utf-8"),
            "candidate_top_function": (
                "candidate_top"
            ),
            "target_profile": profile,
        }
    )
    budget = BudgetManager(
        BudgetLimits(
            max_tool_calls=1,
            max_csim_calls=1,
            max_compile_calls=0,
            max_csynth_calls=0,
        )
    )

    def resolution(_profile):
        return {
            "command": (
                "fake-vitis-run --mode hls "
                "--tcl --input_file vitis.tcl"
            ),
            "command_source": "fixture",
            "resolved_executable": (
                "/fixture/fake-vitis-run"
            ),
            "resolved_settings_path": None,
        }

    def runner(work_dir, _command, _timeout):
        root = Path(work_dir)
        log = (
            root
            / "native_csim/solution/solution.log"
        )
        log.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        log.write_text(
            "INFO: C simulation completed.\n",
            encoding="utf-8",
        )
        return {
            "returncode": 0,
            "timeout": False,
            "stdout": "CSim done with 0 errors.",
            "stderr": "",
        }

    with tempfile.TemporaryDirectory(
        prefix="p4_0c_replay_"
    ) as temporary:
        with (
            patch(
                "flow.tools.vitis_csim."
                "resolve_csynth_command",
                resolution,
            ),
            patch(
                "flow.tools.vitis_csim."
                "probe_csynth_version",
                lambda _resolution, requested: {
                    "status": "matched",
                    "requested": requested,
                    "actual": requested,
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                },
            ),
            patch(
                "flow.tools.general.run_cmd",
                runner,
            ),
        ):
            status, diagnostic = run_vitis_csim(
                temporary,
                variables,
                30,
                budget=budget,
            )
        root = Path(temporary)
        invocation = json.loads(
            (
                root / "csim_invocation.json"
            ).read_text(encoding="utf-8")
        )
        tcl = (root / "vitis.tcl").read_text(
            encoding="utf-8"
        )

    usage = budget.snapshot().to_dict()
    payload = {
        "schema_version": 1,
        "replay_id": (
            "p4-0c.native-vitis-csim-contract"
        ),
        "status": status,
        "diagnostic_empty": not diagnostic,
        "execution_backend": invocation.get(
            "execution_backend"
        ),
        "native_vitis_csim": invocation.get(
            "native_vitis_csim"
        ),
        "toolchain_status": invocation.get(
            "toolchain_version_verification",
            {},
        ).get("status"),
        "execution_returncode": invocation.get(
            "execution",
            {},
        ).get("returncode"),
        "tcl_has_csim": (
            "csim_design -clean" in tcl
        ),
        "tcl_has_no_csynth": (
            "csynth_design" not in tcl
        ),
        "reference_is_tb": (
            'add_files -tb "reference.cpp"' in tcl
        ),
        "testbench_is_tb": (
            'add_files -tb "testbench.cpp"' in tcl
        ),
        "candidate_is_design": (
            'add_files "candidate.cpp"' in tcl
        ),
        "budget_usage": usage,
        "network_llm_used": False,
        "real_vitis_used": False,
    }
    payload["passed"] = bool(
        payload["status"] == "succeeded"
        and payload["diagnostic_empty"] is True
        and payload["execution_backend"]
        == "native_vitis"
        and payload["native_vitis_csim"] is True
        and payload["toolchain_status"] == "matched"
        and payload["execution_returncode"] == 0
        and payload["tcl_has_csim"] is True
        and payload["tcl_has_no_csynth"] is True
        and payload["reference_is_tb"] is True
        and payload["testbench_is_tb"] is True
        and payload["candidate_is_design"] is True
        and usage["tool_calls"] == 1
        and usage["csim_calls"] == 1
        and usage["compile_calls"] == 0
        and usage["csynth_calls"] == 0
        and payload["network_llm_used"] is False
    )

    rendered = (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output.write_text(
            rendered,
            encoding="utf-8",
        )
    print(rendered, end="")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
