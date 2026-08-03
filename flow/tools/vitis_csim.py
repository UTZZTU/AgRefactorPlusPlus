"""Real Vitis HLS C simulation with typed invocation evidence."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
from typing import Any

from autogen.agentchat.group import ContextVariables

from agrefactor.config import TargetProfile, resolve_target_profile
from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)
from flow.tools.csynth import (
    probe_csynth_version,
    resolve_csynth_command,
)
import flow.tools as tools


NATIVE_VITIS_CSIM_TIMEOUT = 60
NATIVE_VITIS_CSIM_BUDGET_INCREMENT = {
    "tool_calls": 1,
    "csim_calls": 1,
}
NATIVE_VITIS_CSIM_SCHEMA_VERSION = 1
_COMPILER_ERROR_RE = re.compile(
    r"(?:\berror:|compilation\s+failed|failed\s+to\s+compile|"
    r"fatal\s+error:|undefined\s+reference)",
    re.IGNORECASE,
)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _usage(usage: BudgetUsage) -> dict[str, Any]:
    return {
        "llm_calls": usage.llm_calls,
        "tool_calls": usage.tool_calls,
        "compile_calls": usage.compile_calls,
        "csim_calls": usage.csim_calls,
        "csynth_calls": usage.csynth_calls,
        "tokens": usage.tokens,
        "cost_usd": usage.cost_usd,
        "elapsed_s": usage.elapsed_s,
    }


def _tcl_quote(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(
            f"{field_name} must not contain NUL or newline characters"
        )
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return f'"{escaped}"'


def make_native_vitis_csim_tcl(
    *,
    top_kernel: str,
    design_source: str,
    reference_source: str,
    testbench_source: str,
    target_profile: TargetProfile | Mapping[str, Any] | str | None,
) -> str:
    """Build one truthful native Vitis CSIM Tcl program."""

    profile = resolve_target_profile(target_profile)
    if profile.toolchain != "vitis_hls":
        raise ValueError(
            "native Vitis CSIM requires toolchain='vitis_hls'"
        )
    if profile.device is None:
        raise ValueError(
            "native Vitis CSIM requires a target device"
        )

    compile_flags = " ".join(profile.compile_flags)
    lines = [
        "open_project -reset native_csim",
        f"set_top {_tcl_quote(top_kernel, 'top_kernel')}",
    ]

    design = (
        f"add_files {_tcl_quote(design_source, 'design source')}"
    )
    if compile_flags:
        design += (
            " -cflags "
            + _tcl_quote(compile_flags, "compile flags")
        )
    lines.append(design)

    for source, label in (
        (reference_source, "reference source"),
        (testbench_source, "testbench source"),
    ):
        line = f"add_files -tb {_tcl_quote(source, label)}"
        if compile_flags:
            line += (
                " -cflags "
                + _tcl_quote(compile_flags, "compile flags")
            )
        lines.append(line)

    lines.extend(
        [
            "open_solution -reset -flow_target vitis solution",
            f"set_part {_tcl_quote(profile.device, 'target device')}",
            (
                "create_clock -period "
                f"{profile.clock_period_ns} -name default"
            ),
            "csim_design -clean",
            "close_project",
            "exit",
        ]
    )
    rendered = "\n".join(lines) + "\n"
    if "csynth_design" in rendered:
        raise AssertionError(
            "native CSIM Tcl must not synthesize"
        )
    return rendered


def make_native_vitis_csim_script(
    *,
    work_dir: str | os.PathLike[str],
    original_code: str,
    candidate_code: str,
    testbench_code: str,
    top_kernel: str,
    target_profile: TargetProfile | Mapping[str, Any] | str | None,
) -> dict[str, Any]:
    root = Path(work_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    files = {
        "candidate.cpp": candidate_code,
        "reference.cpp": original_code,
        "testbench.cpp": testbench_code,
    }
    for name, content in files.items():
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{name} content must not be empty")
        (root / name).write_text(content, encoding="utf-8")

    profile = resolve_target_profile(target_profile)
    tcl = make_native_vitis_csim_tcl(
        top_kernel=top_kernel,
        design_source="candidate.cpp",
        reference_source="reference.cpp",
        testbench_source="testbench.cpp",
        target_profile=profile,
    )
    (root / "vitis.tcl").write_text(tcl, encoding="utf-8")
    return {
        "root": root,
        "profile": profile,
        "tcl": tcl,
        "source_files": [
            {
                "path": "candidate.cpp",
                "role": "design",
            },
            {
                "path": "reference.cpp",
                "role": "testbench_reference",
            },
            {
                "path": "testbench.cpp",
                "role": "testbench_driver",
            },
        ],
    }


def _diagnostic(root: Path, result: Mapping[str, Any]) -> str:
    parts = [
        str(result.get("stdout") or ""),
        str(result.get("stderr") or ""),
    ]
    for path in (
        root / "native_csim" / "solution" / "solution.log",
        root / "native_csim" / "solution" / "csim" / "report" / "candidate_csim.log",
    ):
        if path.is_file():
            try:
                parts.append(
                    path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                )
            except OSError:
                pass
    return "\n".join(parts).strip()[-12000:]


def _compatible_toolchain(
    verification: Mapping[str, Any],
) -> None:
    if verification.get("status") in {"matched", "detected"}:
        return
    raise RuntimeError(
        "Vitis toolchain verification failed before native CSIM: "
        f"status={verification.get('status')}, "
        f"requested={verification.get('requested')!r}, "
        f"actual={verification.get('actual')!r}"
    )


def run_vitis_csim(
    work_dir: str,
    cv: ContextVariables,
    timelimit: int = NATIVE_VITIS_CSIM_TIMEOUT,
    *,
    budget: BudgetManager | None = None,
):
    """Run one Public suite through real Vitis HLS ``csim_design``."""

    if isinstance(timelimit, bool) or not isinstance(timelimit, int):
        raise TypeError("timelimit must be an integer")
    if timelimit <= 0:
        raise ValueError("timelimit must be positive")

    top_kernel = str(cv["candidate_top_function"]).strip()
    if not top_kernel:
        raise ValueError("candidate_top_function must not be empty")
    profile = resolve_target_profile(cv["target_profile"])

    material = make_native_vitis_csim_script(
        work_dir=work_dir,
        original_code=cv["orig_code"],
        candidate_code=cv["curr_code"],
        testbench_code=cv["testbench"],
        top_kernel=top_kernel,
        target_profile=profile,
    )
    root: Path = material["root"]
    command_resolution = resolve_csynth_command(profile)
    invocation_path = root / "csim_invocation.json"
    invocation: dict[str, Any] = {
        "schema_version": NATIVE_VITIS_CSIM_SCHEMA_VERSION,
        "phase": "public_native_vitis_csim",
        "execution_backend": "native_vitis",
        "native_vitis_csim": True,
        "work_dir": str(root),
        "top_kernel": top_kernel,
        "source_files": material["source_files"],
        "tcl_path": str(root / "vitis.tcl"),
        "tcl_sha256": __import__("hashlib").sha256(
            material["tcl"].encode("utf-8")
        ).hexdigest(),
        "timeout_seconds": timelimit,
        "target_profile": profile.to_effective_dict(),
        "requested_toolchain_version": (
            profile.toolchain_version
        ),
        "toolchain_version_verification": {
            "status": "pending",
            "requested": profile.toolchain_version,
            "actual": None,
        },
        "command": command_resolution["command"],
        "command_source": command_resolution[
            "command_source"
        ],
        "resolved_executable": command_resolution[
            "resolved_executable"
        ],
        "resolved_settings_path": command_resolution[
            "resolved_settings_path"
        ],
        "budget": {
            "status": (
                "not_configured"
                if budget is None
                else "pending"
            ),
            "planned_increment": dict(
                NATIVE_VITIS_CSIM_BUDGET_INCREMENT
            ),
        },
        "compile_execution": {
            "status": "native_vitis_internal",
            "returncode": None,
            "timeout": False,
        },
        "simulation_execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
        "execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
    }
    _write_json(invocation_path, invocation)

    if budget is not None:
        try:
            budget.ensure_available(
                **NATIVE_VITIS_CSIM_BUDGET_INCREMENT
            )
            usage_before = budget.snapshot()
        except BudgetExceededError as exc:
            invocation["budget"] = {
                "status": "blocked",
                "checkpoint": "before_version_probe",
                "planned_increment": dict(
                    NATIVE_VITIS_CSIM_BUDGET_INCREMENT
                ),
                "resource": exc.resource,
                "limit": exc.limit,
                "attempted": exc.attempted,
            }
            invocation["simulation_execution"][
                "status"
            ] = "blocked_by_budget"
            invocation["execution"][
                "status"
            ] = "blocked_by_budget"
            _write_json(invocation_path, invocation)
            raise
        invocation["budget"].update(
            {
                "status": "available",
                "checkpoint": "before_version_probe",
                "usage_before": _usage(usage_before),
            }
        )
        _write_json(invocation_path, invocation)

    verification = probe_csynth_version(
        command_resolution,
        profile.toolchain_version,
    )
    invocation[
        "toolchain_version_verification"
    ] = verification
    try:
        _compatible_toolchain(verification)
    except RuntimeError:
        invocation["execution"] = {
            "status": "blocked_before_native_csim",
            "returncode": None,
            "timeout": False,
        }
        invocation["simulation_execution"] = dict(
            invocation["execution"]
        )
        _write_json(invocation_path, invocation)
        raise

    if budget is not None:
        try:
            usage_after = budget.consume(
                **NATIVE_VITIS_CSIM_BUDGET_INCREMENT
            )
        except BudgetExceededError as exc:
            invocation["budget"] = {
                "status": "blocked",
                "checkpoint": "before_native_csim_launch",
                "planned_increment": dict(
                    NATIVE_VITIS_CSIM_BUDGET_INCREMENT
                ),
                "resource": exc.resource,
                "limit": exc.limit,
                "attempted": exc.attempted,
            }
            invocation["execution"] = {
                "status": "blocked_by_budget",
                "returncode": None,
                "timeout": False,
            }
            invocation["simulation_execution"] = dict(
                invocation["execution"]
            )
            _write_json(invocation_path, invocation)
            raise
        invocation["budget"] = {
            "status": "consumed",
            "checkpoint": "before_native_csim_launch",
            "planned_increment": dict(
                NATIVE_VITIS_CSIM_BUDGET_INCREMENT
            ),
            "usage_before": invocation["budget"].get(
                "usage_before"
            ),
            "usage_after": _usage(usage_after),
        }

    _write_json(invocation_path, invocation)
    command = command_resolution["command"]
    try:
        result = tools.general.run_cmd(
            str(root),
            command,
            timelimit,
        )
    except Exception as exc:
        invocation["execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
        }
        invocation["simulation_execution"] = dict(
            invocation["execution"]
        )
        _write_json(invocation_path, invocation)
        raise

    execution = {
        "status": "completed",
        "returncode": result.get("returncode"),
        "timeout": bool(result.get("timeout", False)),
    }
    invocation["execution"] = execution
    invocation["simulation_execution"] = dict(execution)
    _write_json(invocation_path, invocation)

    diagnostic = _diagnostic(root, result)
    if execution["timeout"]:
        return (
            "csim_failed",
            "Native Vitis CSIM timed out.\n" + diagnostic,
        )
    if execution["returncode"] != 0:
        status = (
            "tb_compile_failed"
            if _COMPILER_ERROR_RE.search(diagnostic)
            else "csim_failed"
        )
        return status, diagnostic
    return "succeeded", ""
