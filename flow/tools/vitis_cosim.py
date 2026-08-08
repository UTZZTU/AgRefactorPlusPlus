"""Real Vitis HLS Public RTL COSIM with typed, fail-closed evidence."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import flow.tools as tools
from flow.tools.typed_testbench_outcome import (
    build_typed_testbench_adapter,
    make_typed_outcome_identity,
    read_typed_testbench_outcome,
)
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
_PUBLIC_DIFFERENTIAL_RUNTIME_CONTRACT_KIND = (
    "public_differential_self_check_v1"
)


def _normalize_cosim_interface_depths(
    value: Mapping[str, Any] | None,
) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("COSIM interface depths must be a mapping")
    out: dict[str, int] = {}
    for raw_port, raw_depth in value.items():
        if not isinstance(raw_port, str):
            raise TypeError("COSIM depth port names must be strings")
        port = raw_port.strip()
        if (
            not port
            or port != raw_port
            or not (port[0].isalpha() or port[0] == "_")
            or not all(ch.isalnum() or ch == "_" for ch in port)
        ):
            raise ValueError(
                "COSIM depth port names must be exact C identifier names"
            )
        if isinstance(raw_depth, bool) or not isinstance(raw_depth, int):
            raise TypeError("COSIM interface depth must be an integer")
        if raw_depth <= 0:
            raise ValueError("COSIM interface depth must be positive")
        out[port] = raw_depth
    return dict(sorted(out.items()))


def _runtime_contract_shape_valid(
    contract: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(contract, Mapping):
        return False
    version = contract.get("schema_version")
    base = {"schema_version", "kind", "candidate_mismatch_returncodes"}
    expected = (
        base
        if version == 1
        else (base | {"cosim_interface_depths"} if version == 2 else None)
    )
    if expected is None or set(contract) != expected:
        return False
    if contract.get("kind") != _PUBLIC_DIFFERENTIAL_RUNTIME_CONTRACT_KIND:
        return False
    if not isinstance(contract.get("candidate_mismatch_returncodes"), (list, tuple)):
        return False
    if version == 2:
        try:
            depths = _normalize_cosim_interface_depths(
                contract.get("cosim_interface_depths")
            )
        except (TypeError, ValueError):
            return False
        if not depths:
            return False
    return True


def _candidate_returncode_authorized(
    contract: Mapping[str, Any] | None,
    returncode: int,
) -> bool:
    return bool(
        _runtime_contract_shape_valid(contract)
        and returncode in contract.get("candidate_mismatch_returncodes", ())
    )


def _cosim_interface_depths(
    contract: Mapping[str, Any] | None,
) -> dict[str, int]:
    if contract is None:
        return {}
    if not _runtime_contract_shape_valid(contract):
        raise ValueError("invalid Public runtime contract for COSIM")
    if contract.get("schema_version") == 1:
        return {}
    return _normalize_cosim_interface_depths(
        contract.get("cosim_interface_depths")
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

_WRAPPED_MAIN_NAME = "agrefactor_public_testbench_main"
_MAIN_DEFINITION_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<return_type>int|auto)[ \t]+"
    r"main[ \t]*\((?P<params>[^)]*)\)"
    r"(?P<suffix>[ \t]*(?:->[ \t]*int[ \t]*)?\{)"
)


def _main_call_contract(parameters: str) -> tuple[str, str, str]:
    cleaned = re.sub(r"\s+", " ", parameters.strip())
    if cleaned in {"", "void"}:
        return (
            "no_args",
            f"int {_WRAPPED_MAIN_NAME}();",
            f"{_WRAPPED_MAIN_NAME}()",
        )
    parts = [part.strip() for part in cleaned.split(",")]
    if (
        len(parts) == 2
        and re.search(r"\bint\b", parts[0])
        and re.search(r"\bchar\b", parts[1])
        and ("*" in parts[1] or "[" in parts[1])
    ):
        return (
            "argc_argv",
            f"int {_WRAPPED_MAIN_NAME}(int, char **);",
            f"{_WRAPPED_MAIN_NAME}(argc, argv)",
        )
    raise ValueError(
        "Public Testbench main signature is unsupported by the deterministic "
        "COSIM typed-outcome adapter; expected main(), main(void), or "
        "main(int, char **)."
    )


def _build_typed_outcome_adapter(
    testbench_code: str,
    *,
    base_identity: Mapping[str, str],
) -> tuple[str, str, dict[str, Any]]:
    return build_typed_testbench_adapter(
        testbench_code,
        wrapped_main_name=_WRAPPED_MAIN_NAME,
        base_identity=base_identity,
        allowed_phases=("csim_prerequisite", "cosim"),
    )

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
    base_identity: Mapping[str, str],
) -> dict[str, Path]:
    instrumented, wrapper, adapter = _build_typed_outcome_adapter(
        testbench_code,
        base_identity=base_identity,
    )
    files = {
        "candidate": root / "candidate.cpp",
        "reference": root / "reference.cpp",
        "testbench_original": root / "public_testbench_original.cpp",
        "testbench": root / "public_testbench.cpp",
        "wrapper": root / "agrefactor_cosim_wrapper.cpp",
    }
    for role, code in (
        ("candidate", candidate_code),
        ("reference", original_code),
        ("testbench_original", testbench_code),
        ("testbench", instrumented),
        ("wrapper", wrapper),
    ):
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{role} source must not be empty")
        _atomic_text(files[role], code.rstrip() + "\n")
    adapter_path = root / "typed_outcome_adapter.json"
    adapter["original_testbench_sha256"] = _file_sha256(
        files["testbench_original"]
    )
    adapter["instrumented_testbench_sha256"] = _file_sha256(
        files["testbench"]
    )
    adapter["wrapper_sha256"] = _file_sha256(files["wrapper"])
    _atomic_json(adapter_path, adapter)
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
    typed_execution_id: str | None = None,
    interface_depths: Mapping[str, int] | None = None,
) -> str:
    """Build a Vitis Tcl chain with structured per-command outcome phases."""

    if not isinstance(root, Path):
        raise TypeError("root must be Path")
    top = _required_text(top, "top")
    if not isinstance(profile, TargetProfile):
        raise TypeError("profile must be TargetProfile")
    if profile.toolchain != "vitis_hls":
        raise ValueError("COSIM requires toolchain='vitis_hls'")
    if profile.device is None or profile.clock_period_ns is None:
        raise ValueError("COSIM requires a concrete device and clock")
    required_roles = {"candidate", "reference", "testbench"}
    adapter_roles = {"testbench_original", "wrapper"}
    observed_roles = set(files)
    if not required_roles.issubset(observed_roles):
        raise ValueError("files must contain candidate/reference/testbench")
    unexpected_roles = observed_roles - required_roles - adapter_roles
    if unexpected_roles:
        raise ValueError("files contain unexpected COSIM roles: " + ", ".join(sorted(unexpected_roles)))
    if ("wrapper" in files) != ("testbench_original" in files):
        raise ValueError("deterministic COSIM wrapper roles must be supplied together")
    execution_id = typed_execution_id or ("0" * 32)
    if re.fullmatch(r"[0-9a-f]{32}", execution_id) is None:
        raise ValueError("typed_execution_id must be 32 lowercase hex characters")
    normalized_depths = _normalize_cosim_interface_depths(interface_depths)

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
        f"set ag_csim_argv [list {_tcl_quote(outcome_argv, 'typed outcome argv')} {_tcl_quote(execution_id, 'execution id')} {_tcl_quote('csim_prerequisite', 'phase')}]",
        f"set ag_cosim_argv [list {_tcl_quote(outcome_argv, 'typed outcome argv')} {_tcl_quote(execution_id, 'execution id')} {_tcl_quote('cosim', 'phase')}]",
        "open_project -reset agrefactor_public_cosim",
        f"set_top {_tcl_quote(top, 'top')}",
        add_line(files["candidate"], testbench=False, flags=compile_flags),
        add_line(files["reference"], testbench=True, flags=testbench_flags),
        add_line(files["testbench"], testbench=True, flags=testbench_flags),
        *([add_line(files["wrapper"], testbench=True, flags=testbench_flags)] if "wrapper" in files else []),
        "open_solution -reset -flow_target vitis solution",
        f"set_part {_tcl_quote(profile.device, 'target device')}",
        f"create_clock -period {profile.clock_period_ns} -name default",
        *[
            "set_directive_interface -mode m_axi -depth "
            f"{depth} {_tcl_quote(top, 'top')} "
            f"{_tcl_quote(port, 'COSIM depth port')}"
            for port, depth in normalized_depths.items()
        ],
        (
            "if {[catch {csim_design -clean -argv $ag_csim_argv} ag_msg]} { "
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
            "if {[catch {cosim_design -tool xsim -rtl verilog -argv $ag_cosim_argv} ag_msg]} { "
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


def _typed_outcome(
    path: Path,
    *,
    expected_identity: Mapping[str, str],
) -> dict[str, Any] | None:
    return read_typed_testbench_outcome(
        path,
        expected_identity=expected_identity,
    )

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
    runtime_contract: Mapping[str, Any] | None = None,
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
    interface_depths = _cosim_interface_depths(runtime_contract)

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
    csim_identity = make_typed_outcome_identity(
        phase="csim_prerequisite",
        suite_id=suite_id,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
    )
    cosim_identity = make_typed_outcome_identity(
        phase="cosim",
        suite_id=suite_id,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
        execution_id=csim_identity["execution_id"],
    )
    files = _write_sources(
        root,
        original_code=original_code,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
        base_identity=csim_identity,
    )

    invocation: dict[str, Any] = {
        "schema_version": COSIM_SCHEMA_VERSION,
        "phase": "public_rtl_cosim",
        "suite_id": suite_id,
        "execution_backend": "native_vitis",
        "native_vitis_cosim": True,
        "outcome_transport": "testbench_argv",
        "typed_outcome_identities": {
            "csim_prerequisite": csim_identity,
            "cosim": cosim_identity,
        },
        "runtime_contract": runtime_contract,
        "cosim_interface_depths": interface_depths,
        "typed_outcome_adapter": {
            "kind": "raw_runtime_atomic_wrapper_v2",
            "evidence_path": str(
                root / "typed_outcome_adapter.json"
            ),
            "evidence_sha256": _file_sha256(
                root / "typed_outcome_adapter.json"
            ),
            "failure_owner_inferred": False,
        },
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
            typed_execution_id=csim_identity["execution_id"],
            interface_depths=interface_depths,
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
    typed = _typed_outcome(
        typed_path,
        expected_identity=cosim_identity,
    )
    invocation["command_status"] = (
        command_status
        if command_status is not None
        else {"status": "missing_or_invalid"}
    )
    invocation["typed_outcome"] = (
        typed if typed is not None else {"status": "missing_or_invalid"}
    )

    post_completion_pass = (
        timed_out
        and command_status is not None
        and command_status.get("status") == "passed"
        and command_status.get("phase") == "cosim"
        and command_status.get("reason_code") == "cosim_passed"
        and typed is not None
        and typed.get("status") == "passed"
        and typed.get("identity_verified") is True
        and typed.get("testbench_returncode") == 0
    )
    if post_completion_pass:
        result = _finalize_result(
            invocation_path,
            invocation,
            status="passed",
            failure_kind=None,
            failure_owner="none",
            reason_code="cosim_passed_post_completion_process_linger",
            timed_out=True,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )
        completion = {
            "completion_authority": (
                "fresh_tcl_status_and_identity_bound_typed_outcome_v1"
            ),
            "command_completion_proven": True,
            "post_completion_process_linger": True,
            "process_exit_observed": False,
        }
        result.update(completion)
        invocation["result_summary"].update(completion)
        _atomic_json(invocation_path, invocation)
        evidence_sha = _file_sha256(invocation_path)
        if evidence_sha is None:
            raise RuntimeError(
                "COSIM post-completion evidence was not persisted"
            )
        result["evidence_sha256"] = evidence_sha
        return result

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

    typed_returncode = (
        typed.get("testbench_returncode")
        if isinstance(typed, Mapping)
        else None
    )
    deterministic_candidate = (
        typed is not None
        and typed.get("status") == "failed"
        and isinstance(typed_returncode, int)
        and not isinstance(typed_returncode, bool)
        and command_status is not None
        and command_status.get("status") == "failed"
        and command_status.get("phase") == "cosim"
        and _candidate_returncode_authorized(runtime_contract, typed_returncode)
    )
    if deterministic_candidate:
        result = _finalize_result(
            invocation_path,
            invocation,
            status="failed",
            failure_kind="candidate_rtl_functional_failure",
            failure_owner="candidate",
            reason_code="public_rtl_mismatch",
            timed_out=False,
            returncode=returncode,
            version_probe_launched=True,
            cosim_launched=True,
        )
        result["owner_authority"] = "deterministic_proven"
        result["testbench_returncode"] = typed_returncode
        invocation["result_summary"]["owner_authority"] = "deterministic_proven"
        invocation["result_summary"]["testbench_returncode"] = typed_returncode
        _atomic_json(invocation_path, invocation)
        result["evidence_sha256"] = _file_sha256(invocation_path)
        return result

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
