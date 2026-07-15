import json, os, requests, shlex, shutil
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

HLS_SERVER_URL = os.getenv("HLS_SERVER_URL")

CSYNTH_TIMEOUT = 300
ERROR_LINES = 15
CSYNTH_EXECUTABLE_ENV = "AGREFACTOR_VITIS_RUN"
DEFAULT_CSYNTH_EXECUTABLE = "vitis-run"
CSYNTH_ARGUMENTS = "--mode hls --tcl --input_file vitis.tcl"
CSYNTH_CMD = f"{DEFAULT_CSYNTH_EXECUTABLE} {CSYNTH_ARGUMENTS}"


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


def _build_csynth_invocation(
    *,
    work_dir: str,
    top_kernel_name: str,
    source_files: list[str],
    profile: TargetProfile,
    command_resolution: dict[str, Any],
    timelimit: int,
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
            "status": "not_checked",
            "actual": None,
        },
        **command_resolution,
        "execution": {
            "status": "pending",
            "returncode": None,
            "timeout": None,
        },
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

    command = command_resolution["command"]
    print(f">>> Synthesizing in {work_dir}... <<<")
    result = tools.general.run_cmd(work_dir, command, timelimit)
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
