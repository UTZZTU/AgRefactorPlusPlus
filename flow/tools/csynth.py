import json, os, re, requests, shlex, shutil, subprocess
from collections.abc import Mapping
from flow.base_agent import HLSAgentLoader
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
from typing import Optional, Dict, Any

from agrefactor.config import (
    TargetProfile,
    default_target_profile,
    resolve_target_profile,
)
from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)

HLS_SERVER_URL = os.getenv("HLS_SERVER_URL")

CSYNTH_TIMEOUT = 300
ERROR_LINES = 15
CSYNTH_EXECUTABLE_ENV = "AGREFACTOR_VITIS_RUN"
DEFAULT_CSYNTH_EXECUTABLE = "vitis-run"
CSYNTH_ARGUMENTS = "--mode hls --tcl --input_file vitis.tcl"
CSYNTH_CMD = f"{DEFAULT_CSYNTH_EXECUTABLE} {CSYNTH_ARGUMENTS}"
CSYNTH_VERSION_PROBE_TIMEOUT = 20
CSYNTH_BUDGET_INCREMENT = {
    "tool_calls": 1,
    "csynth_calls": 1,
}


def get_error_msg(synth_dir: str) -> str:
    path = os.path.join(synth_dir, "csynth", "solution", "solution.log")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            log_content = f.read()
            return "\n".join(log_content.strip().splitlines()[-ERROR_LINES:])
    return ""


def _write_json(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(
            payload,
            stream,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        stream.write("\n")


def resolve_csynth_command() -> dict[str, Any]:
    configured = os.getenv(CSYNTH_EXECUTABLE_ENV)
    if configured is None:
        executable = DEFAULT_CSYNTH_EXECUTABLE
        source = "builtin_default"
    else:
        executable = configured.strip()
        if not executable:
            raise ValueError(
                f"{CSYNTH_EXECUTABLE_ENV} must not be empty"
            )
        source = f"environment:{CSYNTH_EXECUTABLE_ENV}"

    if any(character in executable for character in ("\x00", "\r", "\n")):
        raise ValueError(
            "Vitis executable must not contain NUL or newline characters"
        )

    resolved_executable = shutil.which(executable)
    if resolved_executable is None and os.path.isabs(executable):
        if os.path.isfile(executable):
            resolved_executable = executable

    command = f"{shlex.quote(executable)} {CSYNTH_ARGUMENTS}"
    return {
        "command": command,
        "command_source": source,
        "executable": executable,
        "resolved_executable": resolved_executable,
    }


def _extract_vitis_version(output: str) -> str | None:
    match = re.search(
        r"\bv(?P<version>\d{4}\.\d+(?:\.\d+)?)\b",
        output,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group("version")


def _normalize_toolchain_version(
    value: str | None,
) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    return cleaned or None


def probe_csynth_version(
    command_resolution: Mapping[str, Any],
    requested_version: str | None,
    *,
    timeout_seconds: int = CSYNTH_VERSION_PROBE_TIMEOUT,
) -> dict[str, Any]:
    executable = command_resolution.get("resolved_executable")
    if executable is None:
        executable = command_resolution.get("executable")

    probe_command = [str(executable), "--version"]
    base = {
        "requested": requested_version,
        "actual": None,
        "probe_command": shlex.join(probe_command),
        "probe_source": (
            "resolved_executable"
            if command_resolution.get("resolved_executable")
            else "configured_executable"
        ),
        "returncode": None,
        "stdout": "",
        "stderr": "",
    }

    if command_resolution.get("resolved_executable") is None:
        return {
            **base,
            "status": "executable_not_found",
        }

    try:
        completed = subprocess.run(
            probe_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            **base,
            "status": "probe_timeout",
            "stdout": stdout[:4000],
            "stderr": stderr[:4000],
        }
    except OSError as exc:
        return {
            **base,
            "status": "probe_failed",
            "stderr": str(exc)[:4000],
        }

    stdout = completed.stdout[:4000]
    stderr = completed.stderr[:4000]
    combined = f"{stdout}\n{stderr}"
    actual = _extract_vitis_version(combined)
    requested = _normalize_toolchain_version(requested_version)

    if completed.returncode != 0:
        status = "probe_failed"
    elif actual is None:
        status = "unparseable"
    elif requested is None:
        status = "detected"
    elif actual == requested:
        status = "matched"
    else:
        status = "mismatch"

    return {
        **base,
        "status": status,
        "actual": actual,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _require_compatible_toolchain(
    verification: Mapping[str, Any],
) -> None:
    if verification.get("status") in {"matched", "detected"}:
        return

    raise RuntimeError(
        "Vitis toolchain verification failed before csynth: "
        f"status={verification.get('status')}, "
        f"requested={verification.get('requested')!r}, "
        f"actual={verification.get('actual')!r}, "
        f"probe={verification.get('probe_command')!r}"
    )


def _build_csynth_invocation(
    *,
    work_dir: str,
    top_kernel_name: str,
    source_files: list[str],
    profile: TargetProfile,
    command_resolution: dict[str, Any],
    timelimit: int,
    budget: BudgetManager | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": "csynth",
        "work_dir": os.path.abspath(work_dir),
        "top_kernel": top_kernel_name,
        "source_files": source_files,
        "tcl_path": os.path.abspath(
            os.path.join(work_dir, "vitis.tcl")
        ),
        "timeout_seconds": timelimit,
        "target_profile": profile.to_dict(),
        "requested_toolchain_version": profile.toolchain_version,
        "toolchain_version_verification": {
            "status": "pending",
            "requested": profile.toolchain_version,
            "actual": None,
        },
        **command_resolution,
        "budget": {
            "status": (
                "not_configured"
                if budget is None
                else "pending"
            ),
            "requested_increment": dict(CSYNTH_BUDGET_INCREMENT),
        },
        "execution": {
            "status": "pending",
            "returncode": None,
            "timeout": None,
        },
    }


def _budget_usage_to_dict(usage: BudgetUsage) -> dict[str, Any]:
    return {
        "llm_calls": usage.llm_calls,
        "tool_calls": usage.tool_calls,
        "csynth_calls": usage.csynth_calls,
        "tokens": usage.tokens,
        "cost_usd": usage.cost_usd,
        "elapsed_s": usage.elapsed_s,
    }


def _blocked_budget_evidence(
    exc: BudgetExceededError,
    *,
    checkpoint: str,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "checkpoint": checkpoint,
        "requested_increment": dict(CSYNTH_BUDGET_INCREMENT),
        "resource": exc.resource,
        "limit": exc.limit,
        "attempted": exc.attempted,
    }


TargetProfileInput = (
    TargetProfile
    | Mapping[str, Any]
    | str
    | None
)


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


def make_vitis_tcl(
    top_kernel: str,
    file_list: list[str],
    target_profile: TargetProfileInput = None,
) -> str:
    profile = resolve_target_profile(target_profile)
    if profile.toolchain != "vitis_hls":
        raise ValueError(
            "make_vitis_tcl supports only toolchain='vitis_hls'"
        )
    if profile.device is None:
        raise ValueError(
            "target profile device is required for Vitis synthesis"
        )
    if not file_list:
        raise ValueError("file_list must not be empty")

    quoted_top = _tcl_quote(top_kernel, "top_kernel")
    quoted_device = _tcl_quote(profile.device, "target device")
    compile_flags = " ".join(profile.compile_flags)

    tcl_lines = []
    tcl_lines.append("open_project csynth")
    tcl_lines.append(f"set_top {quoted_top}")
    for fname in file_list:
        line = f"add_files {_tcl_quote(fname, 'source file')}"
        if compile_flags:
            line += (
                " -cflags "
                + _tcl_quote(compile_flags, "compile flags")
            )
        tcl_lines.append(line)
    tcl_lines.append("open_solution -flow_target vitis solution")
    tcl_lines.append(f"set_part {quoted_device}")
    tcl_lines.append(
        "create_clock -period "
        f"{profile.clock_period_ns} -name default"
    )
    tcl_lines.append("csynth_design")
    tcl_lines.append("close_project")
    tcl_lines.append("exit")
    return "\n".join(tcl_lines)


def make_csynth_script(
    work_dir: str,
    top_kernel: str,
    file_list: dict[str, str],
    target_profile: TargetProfileInput = None,
):
    for fname, fcontent in file_list.items():
        file_path = os.path.join(work_dir, fname)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(fcontent)

    tcl_content = make_vitis_tcl(
        top_kernel,
        list(file_list.keys()),
        target_profile=target_profile,
    )
    tcl_path = os.path.join(work_dir, "vitis.tcl")
    with open(tcl_path, "w", encoding="utf-8") as f:
        f.write(tcl_content)


def run_csynth(
    work_dir: str,
    cv: ContextVariables,
    timelimit: int = CSYNTH_TIMEOUT,
    *,
    budget: BudgetManager | None = None,
):
    top_kernel_name = cv["new_kernel_name"]
    file_list = {f"{top_kernel_name}.cpp": cv["curr_code"]}
    profile = resolve_target_profile(cv.get("target_profile"))
    make_csynth_script(
        work_dir,
        top_kernel_name,
        file_list,
        target_profile=profile,
    )

    command_resolution = resolve_csynth_command()
    invocation = _build_csynth_invocation(
        work_dir=work_dir,
        top_kernel_name=top_kernel_name,
        source_files=list(file_list.keys()),
        profile=profile,
        command_resolution=command_resolution,
        timelimit=timelimit,
        budget=budget,
    )
    effective_profile_path = os.path.join(
        work_dir,
        "effective_target_profile.json",
    )
    invocation_path = os.path.join(
        work_dir,
        "csynth_invocation.json",
    )
    _write_json(
        effective_profile_path,
        {
            "schema_version": 1,
            "profile": profile.to_dict(),
        },
    )
    _write_json(invocation_path, invocation)

    if budget is not None:
        try:
            budget.ensure_available(**CSYNTH_BUDGET_INCREMENT)
            usage_before = budget.snapshot()
        except BudgetExceededError as exc:
            invocation["budget"] = _blocked_budget_evidence(
                exc,
                checkpoint="before_version_probe",
            )
            invocation["execution"] = {
                "status": "blocked_by_budget",
                "returncode": None,
                "timeout": False,
            }
            _write_json(invocation_path, invocation)
            raise

        invocation["budget"] = {
            "status": "available",
            "checkpoint": "before_version_probe",
            "requested_increment": dict(CSYNTH_BUDGET_INCREMENT),
            "usage_before": _budget_usage_to_dict(usage_before),
        }
        _write_json(invocation_path, invocation)

    verification = probe_csynth_version(
        command_resolution,
        profile.toolchain_version,
    )
    invocation["toolchain_version_verification"] = verification
    try:
        _require_compatible_toolchain(verification)
    except RuntimeError:
        invocation["execution"] = {
            "status": "blocked_before_csynth",
            "returncode": None,
            "timeout": False,
        }
        _write_json(invocation_path, invocation)
        raise
    _write_json(invocation_path, invocation)

    if budget is not None:
        try:
            usage_after = budget.consume(**CSYNTH_BUDGET_INCREMENT)
        except BudgetExceededError as exc:
            invocation["budget"] = _blocked_budget_evidence(
                exc,
                checkpoint="before_csynth_launch",
            )
            invocation["execution"] = {
                "status": "blocked_by_budget",
                "returncode": None,
                "timeout": False,
            }
            _write_json(invocation_path, invocation)
            raise

        invocation["budget"] = {
            "status": "consumed",
            "checkpoint": "before_csynth_launch",
            "requested_increment": dict(CSYNTH_BUDGET_INCREMENT),
            "usage_before": invocation["budget"].get("usage_before"),
            "usage_after": _budget_usage_to_dict(usage_after),
        }
        _write_json(invocation_path, invocation)

    command = command_resolution["command"]
    print(f">>> Synthesizing in {work_dir}... <<<")
    try:
        result = tools.general.run_cmd(work_dir, command, timelimit)
    except Exception as exc:
        invocation["execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
        }
        _write_json(invocation_path, invocation)
        raise

    invocation["execution"] = {
        "status": "completed",
        "returncode": result.get("returncode"),
        "timeout": bool(result.get("timeout", False)),
    }
    _write_json(invocation_path, invocation)
    # Check if the synthesis tool actually ran
    if result["returncode"] != 0 and not result.get("timeout", False):
        stderr = result.get("stderr", "")
        if "not found" in stderr or "No such file" in stderr or result["returncode"] == 127:
            raise RuntimeError(
                f"CSYNTH command failed to launch: {command}\n"
                f"stderr: {stderr[:500]}"
            )
    rpt_path = os.path.join(work_dir, "csynth", "solution", "syn", "report", f"{top_kernel_name}_csynth.rpt")
    if result["timeout"]:
        if (not os.path.exists(rpt_path)):
            status = "timeout"
        else:
            status = "succeeded"
    elif (result["returncode"] != 0) or (not os.path.exists(rpt_path)):
        status = "csynth_failed"
    else:
        status = "succeeded"
    # Override status if synthesizability check passed (even on timeout/failure)
    log_path = os.path.join(work_dir, "csynth", "solution", "solution.log")
    if status != "succeeded" and os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if "Finished Checking Synthesizability:" in f.read():
                status = "succeeded"
    error_msg = get_error_msg(work_dir)
    return status, error_msg


def require_remote_default_target(
    cv: ContextVariables,
) -> TargetProfile:
    """Reject target overrides that the legacy remote API would drop."""

    profile = resolve_target_profile(cv.get("target_profile"))
    if profile != default_target_profile():
        raise ValueError(
            "remote synthesis currently supports only the default "
            "target profile; use local execution for target overrides"
        )
    return profile


def run_csynth_remote(
    cv: ContextVariables,
    timelimit: int = CSYNTH_TIMEOUT,
):
    if not HLS_SERVER_URL:
        raise RuntimeError("HLS_SERVER_URL environment variable not set")
    require_remote_default_target(cv)
    payload = {
        "curr_code": cv["curr_code"],
        "new_kernel_name": cv["new_kernel_name"],
        "timelimit": timelimit,
    }
    resp = requests.post(f"{HLS_SERVER_URL}/csynth", json=payload, timeout=timelimit + 30)
    resp.raise_for_status()
    data = resp.json()
    return data["status"], data["error_msg"]


def fixing_csynth(
    cv: ContextVariables,
    status: str,
    error_msg: str,
    llm_config: Optional[Dict[str, Any]] = None
):
    fixer_loader = HLSAgentLoader("flow/agents/fixing.yaml", llm_config_override=llm_config)
    csynth_fixer = fixer_loader.load_agent("csynth_fixer")
    if status == "timeout":
        issue = f"Synthesis timeout, the last stage stdout is:\n{error_msg}\n\n"
    elif status == "csynth_failed":
        issue = f"Synthesis failed with the error message:\n{error_msg}\n\n"
    msg = issue +(
        f"Current code:\n {cv['curr_code']}\n\n"
        f"Top level kernel name: {cv['new_kernel_name']}"
    )
    resp = csynth_fixer.run(message=msg, max_turns=1)
    resp.process()
    response_content = tools.general.strip_thinking(resp.messages[1]["content"])
    cv["curr_code"] = tools.general.extract_code(response_content)[0]
