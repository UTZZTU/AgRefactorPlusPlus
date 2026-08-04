"""Real Vitis HLS Public RTL COSIM with typed, fail-closed evidence."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import flow.tools as tools
from agrefactor.config import TargetProfile, resolve_target_profile
from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)
from flow.tools.csynth import resolve_csynth_command


VERSION_PROBE_BUDGET_INCREMENT = {"tool_calls": 1}
COSIM_BUDGET_INCREMENT = {"tool_calls": 1, "cosim_calls": 1}
COSIM_SCHEMA_VERSION = 1
_VERSION_PROBE_TIMEOUT_S = 60
_TYPED_FAILURE_PAIRS = frozenset(
    {
        ("candidate_rtl_functional_failure", "candidate"),
        ("public_testbench_failure", "testbench"),
    }
)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    copied = json.loads(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                copied,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_text(path: Path, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"text must not be empty: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _file_sha256(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _usage(value: BudgetUsage) -> dict[str, Any]:
    return value.to_dict()


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if any(character in cleaned for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{name} must not contain NUL or newline characters")
    return cleaned


def _tcl_quote(value: str, name: str) -> str:
    cleaned = _required_text(value, name)
    escaped = (
        cleaned.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return f'"{escaped}"'


def _cosim_argv_value(path: Path) -> str:
    """Return one fail-closed testbench argument for Vitis HLS 2023.2.

    Both ``csim_design -argv`` and ``cosim_design -argv`` are documented to
    pass the supplied argument string to the C/C++ testbench ``main``.  The
    typed outcome path therefore travels as runtime data rather than through
    the Tcl/config/Make/C-preprocessor flag pipeline.
    """

    if not isinstance(path, Path):
        raise TypeError("COSIM outcome path must be Path")
    value = _required_text(str(path), "COSIM outcome argv path")
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789/._-:+@%"
    )
    if any(character not in allowed for character in value):
        raise ValueError(
            "COSIM outcome argv path contains a character unsafe for the "
            "Vitis 2023.2 testbench argument contract"
        )
    return value

def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.casefold().startswith("v"):
        cleaned = cleaned[1:]
    return cleaned or None


def _write_sources(
    root: Path,
    *,
    original_code: str,
    candidate_code: str,
    testbench_code: str,
) -> dict[str, Path]:
    files = {
        "candidate": root / "candidate.cpp",
        "reference": root / "reference.cpp",
        "testbench": root / "public_testbench.cpp",
    }
    for role, code in (
        ("candidate", candidate_code),
        ("reference", original_code),
        ("testbench", testbench_code),
    ):
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{role} source must not be empty")
        _atomic_text(files[role], code.rstrip() + "\n")
    return files


def _version_probe_tcl(path: Path) -> str:
    return "\n".join(
        (
            f"set f [open {_tcl_quote(str(path), 'version evidence path')} w]",
            "puts $f [version -short]",
            "close $f",
            "exit 0",
            "",
        )
    )


def make_vitis_cosim_tcl(
    *,
    root: Path,
    top: str,
    files: Mapping[str, Path],
    profile: TargetProfile,
) -> str:
    """Build a Vitis Tcl chain whose final command is ``cosim_design``."""

    if not isinstance(root, Path):
        raise TypeError("root must be Path")
    top = _required_text(top, "top")
    if not isinstance(profile, TargetProfile):
        raise TypeError("profile must be TargetProfile")
    if profile.toolchain != "vitis_hls":
        raise ValueError("COSIM requires toolchain='vitis_hls'")
    if profile.device is None or profile.clock_period_ns is None:
        raise ValueError("COSIM requires a concrete device and clock")
    expected_roles = {"candidate", "reference", "testbench"}
    if set(files) != expected_roles:
        raise ValueError("files must contain candidate/reference/testbench")

    status_path = root / "cosim_command_status.json"
    typed_outcome_path = root / "agrefactor_cosim_outcome.json"
    outcome_argv = _cosim_argv_value(typed_outcome_path)
    compile_flags = " ".join(profile.compile_flags)
    testbench_flags = compile_flags

    def add_line(path: Path, *, testbench: bool, flags: str) -> str:
        line = "add_files "
        if testbench:
            line += "-tb "
        line += _tcl_quote(str(path), "source path")
        if flags:
            line += " -cflags " + _tcl_quote(flags, "compile flags")
        return line

    lines = [
        "proc ag_write_status {path status phase reason} {",
        "  set f [open $path w]",
        '  puts $f "{\\"schema_version\\":1,\\"status\\":\\"$status\\",\\"phase\\":\\"$phase\\",\\"reason_code\\":\\"$reason\\"}"',
        "  close $f",
        "}",
        f"set ag_status {_tcl_quote(str(status_path), 'status path')}",
        f"set ag_typed {_tcl_quote(str(typed_outcome_path), 'typed outcome path')}",
        f"set ag_argv [list {_tcl_quote(outcome_argv, 'typed outcome argv')} ]",
        "open_project -reset agrefactor_public_cosim",
        f"set_top {_tcl_quote(top, 'top')}",
        add_line(files["candidate"], testbench=False, flags=compile_flags),
        add_line(files["reference"], testbench=True, flags=testbench_flags),
        add_line(files["testbench"], testbench=True, flags=testbench_flags),
        "open_solution -reset -flow_target vitis solution",
        f"set_part {_tcl_quote(profile.device, 'target device')}",
        f"create_clock -period {profile.clock_period_ns} -name default",
        (
            "if {[catch {csim_design -clean -argv $ag_argv} ag_msg]} { "
            "ag_write_status $ag_status failed csim_prerequisite "
            "cosim_csim_prerequisite_failed; close_project; exit 21 }"
        ),
        "file delete -force $ag_typed",
        (
            "if {[catch {csynth_design} ag_msg]} { "
            "ag_write_status $ag_status failed csynth_prerequisite "
            "cosim_csynth_prerequisite_failed; close_project; exit 22 }"
        ),
        (
            "if {[catch {cosim_design -tool xsim -rtl verilog -argv $ag_argv} ag_msg]} { "
            "ag_write_status $ag_status failed cosim "
            "cosim_command_failed; close_project; exit 23 }"
        ),
        "ag_write_status $ag_status passed cosim cosim_passed",
        "close_project",
        "exit 0",
    ]
    rendered = "\n".join(lines) + "\n"
    if "cosim_design" not in rendered:
        raise AssertionError("COSIM Tcl must invoke cosim_design")
    return rendered


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _typed_outcome(path: Path) -> dict[str, Any] | None:
    value = _read_json_object(path)
    required = {
        "schema_version",
        "status",
        "failure_kind",
        "failure_owner",
        "reason_code",
    }
    if value is None or set(value) != required:
        return None
    if value.get("schema_version") != 1:
        return None
    status = value.get("status")
    if status == "passed":
        if (
            value.get("failure_kind") not in {None, ""}
            or value.get("failure_owner") != "none"
            or value.get("reason_code") != "cosim_passed"
        ):
            return None
        return {
            "status": "passed",
            "failure_kind": None,
            "failure_owner": "none",
            "reason_code": "cosim_passed",
        }
    pair = (value.get("failure_kind"), value.get("failure_owner"))
    reason = value.get("reason_code")
    if status != "failed" or pair not in _TYPED_FAILURE_PAIRS:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return {
        "status": "failed",
        "failure_kind": pair[0],
        "failure_owner": pair[1],
        "reason_code": reason.strip(),
    }


def _command_status(path: Path) -> dict[str, Any] | None:
    value = _read_json_object(path)
    if value is None or set(value) != {
        "schema_version",
        "status",
        "phase",
        "reason_code",
    }:
        return None
    if value.get("schema_version") != 1:
        return None
    if value.get("status") not in {"passed", "failed"}:
        return None
    if value.get("phase") not in {
        "csim_prerequisite",
        "csynth_prerequisite",
        "cosim",
    }:
        return None
    reason = value.get("reason_code")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return value


def _budget_block(
    invocation: dict[str, Any],
    *,
    section: str,
    checkpoint: str,
    increment: Mapping[str, int],
    exc: BudgetExceededError,
) -> None:
    invocation["budget"]["status"] = "blocked"
    invocation["budget"][section] = {
        "status": "blocked",
        "checkpoint": checkpoint,
        "requested_increment": dict(increment),
        "resource": exc.resource,
        "limit": exc.limit,
        "attempted": exc.attempted,
    }


def _finalize_result(
    invocation_path: Path,
    invocation: dict[str, Any],
    *,
    status: str,
    failure_kind: str | None,
    failure_owner: str,
    reason_code: str,
    timed_out: bool,
    returncode: int | None,
    version_probe_launched: bool,
    cosim_launched: bool,
) -> dict[str, Any]:
    summary = {
        "schema_version": COSIM_SCHEMA_VERSION,
        "status": status,
        "failure_kind": failure_kind,
        "failure_owner": failure_owner,
        "reason_code": reason_code,
        "timed_out": timed_out,
        "returncode": returncode,
        "tool_launched": version_probe_launched or cosim_launched,
        "version_probe_launched": version_probe_launched,
        "cosim_launched": cosim_launched,
    }
    invocation["result_summary"] = summary
    _atomic_json(invocation_path, invocation)
    evidence_sha = _file_sha256(invocation_path)
    if evidence_sha is None:
        raise RuntimeError("COSIM invocation evidence was not persisted")
    return {
        **summary,
        "evidence_sha256": evidence_sha,
        "invocation_path": str(invocation_path),
    }


def run_vitis_cosim(
    *,
    work_dir: str | os.PathLike[str],
    original_code: str,
    candidate_code: str,
    testbench_code: str,
    candidate_top_function: str,
    target_profile: TargetProfile | Mapping[str, Any] | str | None,
    timelimit: int,
    budget: BudgetManager | None = None,
    suite_id: str = "public",
) -> dict[str, Any]:
    """Run one Public suite through a real Vitis RTL COSIM chain."""

    if isinstance(timelimit, bool) or not isinstance(timelimit, int):
        raise TypeError("timelimit must be an integer")
    if timelimit <= 0:
        raise ValueError("timelimit must be positive")
    if budget is not None and not isinstance(budget, BudgetManager):
        raise TypeError("budget must be BudgetManager or None")
    suite_id = _required_text(suite_id, "suite_id")
    top = _required_text(candidate_top_function, "candidate_top_function")

    root = Path(work_dir).expanduser().resolve()
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValueError("work_dir must be a real directory")
    root.mkdir(parents=True, exist_ok=True)

    invocation_path = root / "cosim_invocation.json"
    version_path = root / "toolchain_version.txt"
    tcl_path = root / "vitis.tcl"
    status_path = root / "cosim_command_status.json"
    typed_path = root / "agrefactor_cosim_outcome.json"
    for stale in (version_path, status_path, typed_path):
        if stale.exists():
            if stale.is_symlink() or not stale.is_file():
                raise ValueError(f"unsafe stale COSIM evidence path: {stale}")
            stale.unlink()

    profile = resolve_target_profile(target_profile)
    resolution = resolve_csynth_command(profile)
    files = _write_sources(
        root,
        original_code=original_code,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
    )

    invocation: dict[str, Any] = {
        "schema_version": COSIM_SCHEMA_VERSION,
        "phase": "public_rtl_cosim",
        "suite_id": suite_id,
        "execution_backend": "native_vitis",
        "native_vitis_cosim": True,
        "outcome_transport": "testbench_argv",
        "rtl_language": "verilog",
        "simulator": "xsim",
        "work_dir": str(root),
        "top_kernel": top,
        "source_roles": {
            "design": str(files["candidate"]),
            "testbench_reference": str(files["reference"]),
            "testbench_driver": str(files["testbench"]),
        },
        "target_profile": profile.to_effective_dict(),
        "requested_toolchain_version": profile.toolchain_version,
        "toolchain_version_verification": {
            "status": "pending",
            "requested": profile.toolchain_version,
            "actual": None,
            "evidence_source": "vitis_tcl_version_file",
            "evidence_sha256": None,
        },
        "command": resolution.get("command"),
        "command_source": resolution.get("command_source"),
        "executable": resolution.get("executable"),
        "resolved_executable": resolution.get("resolved_executable"),
        "settings_path": resolution.get("settings_path"),
        "resolved_settings_path": resolution.get("resolved_settings_path"),
        "probe_source": resolution.get("probe_source"),
        "profile_name": resolution.get("profile_name"),
        "effective_value_provenance": resolution.get(
            "effective_value_provenance",
            {},
        ),
        "timeout_seconds": timelimit,
        "tcl_path": str(tcl_path),
        "tcl_sha256": None,
        "budget": {
            "status": "not_configured" if budget is None else "pending",
            "version_probe": {
                "status": "not_configured" if budget is None else "pending",
                "requested_increment": dict(VERSION_PROBE_BUDGET_INCREMENT),
            },
            "cosim_launch": {
                "status": "not_configured" if budget is None else "pending",
                "requested_increment": dict(COSIM_BUDGET_INCREMENT),
            },
        },
        "version_probe_execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
        "execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
            "cosim_launched": False,
        },
        "command_status": {"status": "pending"},
        "typed_outcome": {"status": "pending"},
    }
    _atomic_json(invocation_path, invocation)

    version_probe_launched = False
    if budget is not None:
        try:
            usage_before = budget.snapshot()
            budget.ensure_available(**VERSION_PROBE_BUDGET_INCREMENT)
            usage_after = budget.consume(**VERSION_PROBE_BUDGET_INCREMENT)
        except BudgetExceededError as exc:
            _budget_block(
                invocation,
                section="version_probe",
                checkpoint="before_version_probe_launch",
                increment=VERSION_PROBE_BUDGET_INCREMENT,
                exc=exc,
            )
            invocation["version_probe_execution"]["status"] = (
                "blocked_by_budget"
            )
            invocation["execution"]["status"] = "blocked_by_budget"
            return _finalize_result(
                invocation_path,
                invocation,
                status="blocked",
                failure_kind="budget_exhausted",
                failure_owner="configuration",
                reason_code="version_probe_tool_budget_exhausted",
                timed_out=False,
                returncode=None,
                version_probe_launched=False,
                cosim_launched=False,
            )
        invocation["budget"]["status"] = "partially_consumed"
        invocation["budget"]["version_probe"] = {
            "status": "consumed",
            "checkpoint": "before_version_probe_launch",
            "requested_increment": dict(VERSION_PROBE_BUDGET_INCREMENT),
            "usage_before": _usage(usage_before),
            "usage_after": _usage(usage_after),
        }

    _atomic_text(tcl_path, _version_probe_tcl(version_path))
    invocation["tcl_sha256"] = _file_sha256(tcl_path)
    _atomic_json(invocation_path, invocation)
    try:
        version_probe_launched = True
        probe = tools.general.run_cmd(
            str(root),
            resolution["command"],
            min(timelimit, _VERSION_PROBE_TIMEOUT_S),
        )
    except Exception as exc:
        invocation["version_probe_execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": type(exc).__name__,
        }
        invocation["execution"]["status"] = "blocked_before_cosim"
        invocation["toolchain_version_verification"]["status"] = (
            "probe_launch_failed"
        )
        return _finalize_result(
            invocation_path,
            invocation,
            status="error",
            failure_kind="toolchain_failure",
            failure_owner="toolchain",
            reason_code="cosim_toolchain_probe_launch_failed",
            timed_out=False,
            returncode=None,
            version_probe_launched=True,
            cosim_launched=False,
        )

    probe_returncode = (
        probe.get("returncode")
        if isinstance(probe.get("returncode"), int)
        else None
    )
    probe_timeout = probe.get("timeout") is True
    invocation["version_probe_execution"] = {
        "status": "completed",
        "returncode": probe_returncode,
        "timeout": probe_timeout,
    }
    actual = None
    if version_path.is_file() and not version_path.is_symlink():
        actual = _normalize_version(
            version_path.read_text(encoding="utf-8", errors="replace")
        )
    requested = _normalize_version(profile.toolchain_version)
    matched = (
        not probe_timeout
        and probe_returncode == 0
        and actual is not None
        and (requested is None or actual == requested)
    )
    invocation["toolchain_version_verification"] = {
        "status": "matched" if matched else "unverified",
        "requested": requested,
        "actual": actual,
        "probe_returncode": probe_returncode,
        "probe_timeout": probe_timeout,
        "evidence_source": "vitis_tcl_version_file",
        "evidence_sha256": _file_sha256(version_path),
    }
    if not matched:
        invocation["execution"]["status"] = "blocked_before_cosim"
        return _finalize_result(
            invocation_path,
            invocation,
            status="error",
            failure_kind=("timeout" if probe_timeout else "toolchain_failure"),
            failure_owner=("unknown" if probe_timeout else "toolchain"),
            reason_code=(
                "cosim_version_probe_timeout"
                if probe_timeout
                else "cosim_toolchain_version_unverified"
            ),
            timed_out=probe_timeout,
            returncode=probe_returncode,
            version_probe_launched=True,
            cosim_launched=False,
        )

    for stale in (status_path, typed_path):
        stale.unlink(missing_ok=True)
    _atomic_text(
        tcl_path,
        make_vitis_cosim_tcl(
            root=root,
            top=top,
            files=files,
            profile=profile,
        ),
    )
    invocation["tcl_sha256"] = _file_sha256(tcl_path)

    if budget is not None:
        try:
            usage_before = budget.snapshot()
            budget.ensure_available(**COSIM_BUDGET_INCREMENT)
            usage_after = budget.consume(**COSIM_BUDGET_INCREMENT)
        except BudgetExceededError as exc:
            _budget_block(
                invocation,
                section="cosim_launch",
                checkpoint="before_cosim_launch",
                increment=COSIM_BUDGET_INCREMENT,
                exc=exc,
            )
            invocation["execution"] = {
                "status": "blocked_by_budget",
                "returncode": None,
                "timeout": False,
                "cosim_launched": False,
            }
            return _finalize_result(
                invocation_path,
                invocation,
                status="blocked",
                failure_kind="budget_exhausted",
                failure_owner="configuration",
                reason_code="cosim_launch_budget_exhausted",
                timed_out=False,
                returncode=None,
                version_probe_launched=True,
                cosim_launched=False,
            )
        invocation["budget"]["status"] = "consumed"
        invocation["budget"]["cosim_launch"] = {
            "status": "consumed",
            "checkpoint": "before_cosim_launch",
            "requested_increment": dict(COSIM_BUDGET_INCREMENT),
            "usage_before": _usage(usage_before),
            "usage_after": _usage(usage_after),
        }
    _atomic_json(invocation_path, invocation)

    try:
        result = tools.general.run_cmd(
            str(root),
            resolution["command"],
            timelimit,
        )
    except Exception as exc:
        invocation["execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "cosim_launched": False,
            "error_type": type(exc).__name__,
        }
        return _finalize_result(
            invocation_path,
            invocation,
            status="error",
            failure_kind="toolchain_failure",
            failure_owner="toolchain",
            reason_code="cosim_launch_failed",
            timed_out=False,
            returncode=None,
            version_probe_launched=True,
            cosim_launched=False,
        )

    returncode = (
        result.get("returncode")
        if isinstance(result.get("returncode"), int)
        else None
    )
    timed_out = result.get("timeout") is True
    invocation["execution"] = {
        "status": "completed",
        "returncode": returncode,
        "timeout": timed_out,
        "cosim_launched": True,
    }
    command_status = _command_status(status_path)
    typed = _typed_outcome(typed_path)
    invocation["command_status"] = (
        command_status
        if command_status is not None
        else {"status": "missing_or_invalid"}
    )
    invocation["typed_outcome"] = (
        typed if typed is not None else {"status": "missing_or_invalid"}
    )

    if timed_out:
        return _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind="timeout",
            failure_owner="unknown",
            reason_code="cosim_timeout",
            timed_out=True,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )

    passed = (
        returncode == 0
        and command_status is not None
        and command_status.get("status") == "passed"
        and command_status.get("phase") == "cosim"
        and command_status.get("reason_code") == "cosim_passed"
        and typed is not None
        and typed.get("status") == "passed"
    )
    if passed:
        return _finalize_result(
            invocation_path,
            invocation,
            status="passed",
            failure_kind=None,
            failure_owner="none",
            reason_code="cosim_passed",
            timed_out=False,
            returncode=0,
            version_probe_launched=True,
            cosim_launched=True,
        )

    if typed is not None and typed.get("status") == "failed":
        return _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind=typed["failure_kind"],
            failure_owner=typed["failure_owner"],
            reason_code=typed["reason_code"],
            timed_out=False,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )

    return _finalize_result(
        invocation_path,
        invocation,
        status="failed",
        failure_kind="ownership_unknown",
        failure_owner="unknown",
        reason_code="cosim_failed_without_typed_owner",
        timed_out=False,
        returncode=returncode,
        version_probe_launched=True,
        cosim_launched=True,
    )
