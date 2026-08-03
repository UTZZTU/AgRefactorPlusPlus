#!/usr/bin/env python3
"""Authoritative model-independent real Vitis native-CSIM smoke."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

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


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-profile")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    fixture = (
        _REPO_ROOT
        / "tests/fixtures/p4_0c_native_vitis"
    )
    candidate_path = fixture / "candidate.cpp"
    reference_path = fixture / "reference.cpp"
    testbench_path = fixture / "public_tb.cpp"
    for path in (
        candidate_path,
        reference_path,
        testbench_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    profile = resolve_target_profile(
        args.target_profile
    )
    budget = BudgetManager(
        BudgetLimits(
            max_llm_calls=0,
            max_tool_calls=1,
            max_compile_calls=0,
            max_csim_calls=1,
            max_csynth_calls=0,
            max_wall_time_s=max(
                float(args.timeout + 60),
                180.0,
            ),
        )
    )

    variables = ContextVariables(
        data={
            "orig_code": reference_path.read_text(
                encoding="utf-8"
            ),
            "curr_code": candidate_path.read_text(
                encoding="utf-8"
            ),
            "testbench": testbench_path.read_text(
                encoding="utf-8"
            ),
            "candidate_top_function": (
                "candidate_top"
            ),
            "target_profile": profile,
        }
    )

    with tempfile.TemporaryDirectory(
        prefix="p4_0c_real_native_vitis_"
    ) as temporary:
        work = Path(temporary)
        status, diagnostic = run_vitis_csim(
            str(work),
            variables,
            args.timeout,
            budget=budget,
        )
        invocation_path = work / "csim_invocation.json"
        invocation = json.loads(
            invocation_path.read_text(
                encoding="utf-8"
            )
        )
        tcl = (work / "vitis.tcl").read_text(
            encoding="utf-8"
        )
        usage = budget.snapshot().to_dict()

        checks = {
            "legacy_status_succeeded": (
                status == "succeeded"
            ),
            "native_backend": (
                invocation.get("execution_backend")
                == "native_vitis"
            ),
            "native_marker": (
                invocation.get("native_vitis_csim")
                is True
            ),
            "phase": (
                invocation.get("phase")
                == "public_native_vitis_csim"
            ),
            "toolchain_verified": (
                invocation.get(
                    "toolchain_version_verification",
                    {},
                ).get("status")
                in {"matched", "detected"}
            ),
            "execution_returncode_zero": (
                invocation.get("execution", {}).get(
                    "returncode"
                )
                == 0
            ),
            "execution_not_timeout": (
                invocation.get("execution", {}).get(
                    "timeout"
                )
                is False
            ),
            "tcl_has_csim": (
                "csim_design -clean" in tcl
            ),
            "tcl_has_no_csynth": (
                "csynth_design" not in tcl
            ),
            "reference_is_tb": (
                'add_files -tb "reference.cpp"'
                in tcl
            ),
            "driver_is_tb": (
                'add_files -tb "testbench.cpp"'
                in tcl
            ),
            "candidate_is_design": (
                'add_files "candidate.cpp"' in tcl
            ),
            "budget_tool_calls": (
                usage["tool_calls"] == 1
            ),
            "budget_csim_calls": (
                usage["csim_calls"] == 1
            ),
            "budget_compile_calls": (
                usage["compile_calls"] == 0
            ),
            "budget_csynth_calls": (
                usage["csynth_calls"] == 0
            ),
            "budget_llm_calls": (
                usage["llm_calls"] == 0
            ),
        }
        passed = all(checks.values())

        payload = {
            "schema_version": 1,
            "smoke_id": (
                "p4-0c.real-public-native-vitis-csim"
            ),
            "passed": passed,
            "network_llm_used": False,
            "real_vitis_used": True,
            "candidate_top_function": (
                "candidate_top"
            ),
            "target_profile": (
                profile.to_effective_dict()
            ),
            "fixture": {
                "candidate_sha256": _sha(
                    candidate_path
                ),
                "reference_sha256": _sha(
                    reference_path
                ),
                "public_testbench_sha256": _sha(
                    testbench_path
                ),
            },
            "status": status,
            "diagnostic_present": bool(
                diagnostic.strip()
            ),
            "checks": checks,
            "budget_usage": usage,
            "invocation": {
                "phase": invocation.get("phase"),
                "execution_backend": invocation.get(
                    "execution_backend"
                ),
                "requested_toolchain_version": (
                    invocation.get(
                        "requested_toolchain_version"
                    )
                ),
                "toolchain_version_verification": (
                    invocation.get(
                        "toolchain_version_verification"
                    )
                ),
                "command_source": invocation.get(
                    "command_source"
                ),
                "execution": invocation.get(
                    "execution"
                ),
                "tcl_sha256": invocation.get(
                    "tcl_sha256"
                ),
            },
        }

    output = args.output.expanduser().resolve()
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
