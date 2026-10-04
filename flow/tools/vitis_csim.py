"""Real Vitis HLS C simulation with typed invocation evidence."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from autogen.agentchat.group import ContextVariables

from agrefactor.config import DEFAULT_CSIM_TIMEOUT_S, TargetProfile, resolve_target_profile
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
from flow.tools.typed_testbench_outcome import (
    build_typed_testbench_adapter,
    make_typed_outcome_identity,
    read_typed_testbench_outcome,
    read_typed_testbench_started,
)


NATIVE_VITIS_CSIM_TIMEOUT = DEFAULT_CSIM_TIMEOUT_S
NATIVE_VITIS_CSIM_BUDGET_INCREMENT = {
    "tool_calls": 1,
    "csim_calls": 1,
}
NATIVE_VITIS_CSIM_SCHEMA_VERSION = 1
_PUBLIC_DIFFERENTIAL_RUNTIME_CONTRACT_KIND = (
    "public_differential_self_check_v1"
)


def _public_runtime_contract_valid(
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
    if version == 2 and not isinstance(
        contract.get("cosim_interface_depths"), Mapping
    ):
        return False
    codes = contract.get("candidate_mismatch_returncodes")
    return isinstance(codes, (list, tuple))


def _candidate_returncode_authorized(
    contract: Mapping[str, Any] | None,
    returncode: int,
) -> bool:
    if not _public_runtime_contract_valid(contract):
        return False
    return returncode in contract["candidate_mismatch_returncodes"]


def _preflight_authorizes_candidate_runtime(
    authority: Mapping[str, Any] | None,
    identity: Mapping[str, str],
) -> bool:
    if not isinstance(authority, Mapping):
        return False
    return (
        authority.get("status") == "passed"
        and authority.get("authority") == "staged_preflight_typed"
        and authority.get("suite_id_sha256") == identity.get("suite_id_sha256")
        and authority.get("candidate_sha256") == identity.get("candidate_sha256")
        and authority.get("testbench_sha256") == identity.get("testbench_sha256")
        and isinstance(authority.get("reference_sha256"), str)
        and isinstance(authority.get("evidence_sha256"), str)
        and len(authority.get("evidence_sha256")) == 64
    )


def _original_isolation_evidence(
    *,
    original_code: str,
    testbench_code: str,
    candidate_code: str,
    candidate_top_function: str,
    identity: Mapping[str, str],
    budget: BudgetManager | None,
    source_root: str | None = None,
    extra_sources: tuple[str, ...] = (),
    compile_flags: tuple[str, ...] = (),
    original_top_function: str | None = None,
    keep_dir: str | None = None,
) -> dict[str, Any]:
    """Re-run the frozen Public inputs against only the Original implementation."""

    from flow.tools.tb_coverage import check_original_execution
    from flow.tools.tb_optimizer import extract_hls_decl_from_testbench

    evidence = {
        "status": "unknown",
        "authority": "original_only_runtime",
        "original_sha256": __import__("hashlib").sha256(
            original_code.encode("utf-8")
        ).hexdigest(),
        "candidate_sha256": __import__("hashlib").sha256(
            candidate_code.encode("utf-8")
        ).hexdigest(),
        "testbench_sha256": __import__("hashlib").sha256(
            testbench_code.encode("utf-8")
        ).hexdigest(),
        "suite_id_sha256": identity.get("suite_id_sha256"),
    }
    declaration = extract_hls_decl_from_testbench(
        testbench_code,
        candidate_top_function,
        source_path=str(Path(source_root) / "testbench.cpp") if source_root else None,
        include_dirs=(source_root,) if source_root else (),
        compile_flags=compile_flags,
    )
    result = check_original_execution(
        original_code,
        testbench_code,
        declaration,
        candidate_top_function,
        budget=budget,
        original_name=original_top_function,
        source_root=source_root,
        extra_sources=extra_sources,
        compile_flags=compile_flags,
        required_original_entry=original_top_function,
        keep_dir=keep_dir,
    )
    evidence.update(
        status=(
            "passed"
            if result.get("status") == "ok" and declaration
            and (original_top_function is None or result.get("original_entry_executed") is True)
            else "unknown" if result.get("status") == "ok"
            else "failed"
        ),
        reason_code=str(result.get("status") or "unknown"),
        failure_owner=str(result.get("failure_owner") or "unknown"),
        run_returncode=result.get("run_returncode"),
        failure_evidence_source=str(
            result.get("failure_evidence_source") or "unknown"
        ),
        original_entry_executed=result.get("original_entry_executed"),
    )
    return evidence


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


def _normalize_vitis_toolchain_headers(
    root: Path,
) -> dict[str, Any]:
    """Keep staged package headers from shadowing Vitis runtime headers.

    Source packages are staged into the suite directory so relative includes
    remain available. Vitis still resolves angle-bracket includes from its
    own include tree, while quoted includes can resolve a staged copy first.
    That would give different translation units different hls::stream
    definitions. Preserve the package copy for evidence and put the active
    toolchain header at the shared include name instead.
    """

    toolchain_root = os.environ.get("XILINX_HLS")
    vendor_header = (
        Path(toolchain_root).expanduser().resolve() / "include" / "hls_stream.h"
        if isinstance(toolchain_root, str) and toolchain_root.strip()
        else None
    )
    staged_header = root / "hls_stream.h"
    if vendor_header is None or not vendor_header.is_file():
        return {
            "status": "vendor_header_unavailable",
            "header": "hls_stream.h",
        }
    if not staged_header.is_file():
        return {
            "status": "vendor_header_used",
            "header": "hls_stream.h",
            "vendor_path": str(vendor_header),
        }
    if staged_header.read_bytes() == vendor_header.read_bytes():
        return {
            "status": "already_canonical",
            "header": "hls_stream.h",
            "vendor_path": str(vendor_header),
        }

    backup = root / "hls_stream.h.source_package"
    if backup.exists():
        raise FileExistsError(
            "cannot preserve staged hls_stream.h: "
            f"{backup} already exists"
        )
    staged_header.replace(backup)
    shutil.copy2(vendor_header, staged_header)
    return {
        "status": "canonical_vendor",
        "header": "hls_stream.h",
        "vendor_path": str(vendor_header),
        "source_package_backup": str(backup),
    }


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
    wrapper_source: str | None = None,
    typed_outcome_path: str | None = None,
    typed_execution_id: str | None = None,
    typed_phase: str = "public_native_vitis_csim",
    extra_sources: tuple[str, ...] = (),
) -> str:
    """Build one truthful native Vitis CSIM Tcl program."""

    profile = resolve_target_profile(target_profile)
    if profile.toolchain != "vitis_hls":
        raise ValueError("native Vitis CSIM requires toolchain='vitis_hls'")
    if profile.device is None:
        raise ValueError("native Vitis CSIM requires a target device")
    typed_values = (wrapper_source, typed_outcome_path, typed_execution_id)
    if any(value is not None for value in typed_values) and not all(
        value is not None for value in typed_values
    ):
        raise ValueError(
            "native CSIM typed wrapper, outcome path and execution id must be supplied together"
        )

    compile_flags = " ".join(profile.compile_flags)
    lines = [
        "open_project -reset native_csim",
        f"set_top {_tcl_quote(top_kernel, 'top_kernel')}",
    ]
    design = f"add_files {_tcl_quote(design_source, 'design source')}"
    if compile_flags:
        design += " -cflags " + _tcl_quote(compile_flags, "compile flags")
    lines.append(design)
    tb_sources = [
        (reference_source, "reference source"),
        (testbench_source, "testbench source"),
    ]
    if wrapper_source is not None:
        tb_sources.append((wrapper_source, "typed wrapper source"))
    tb_sources.extend((source, f"extra source {index}") for index, source in enumerate(extra_sources, start=1))
    for source, label in tb_sources:
        line = f"add_files -tb {_tcl_quote(source, label)}"
        if compile_flags:
            line += " -cflags " + _tcl_quote(compile_flags, "compile flags")
        lines.append(line)

    csim = "csim_design -clean"
    if typed_outcome_path is not None:
        csim += (
            " -argv [list "
            + _tcl_quote(typed_outcome_path, "typed outcome path")
            + " "
            + _tcl_quote(str(typed_execution_id), "typed execution id")
            + " "
            + _tcl_quote(typed_phase, "typed phase")
            + "]"
        )
    lines.extend(
        [
            "open_solution -reset -flow_target vitis solution",
            f"set_part {_tcl_quote(profile.device, 'target device')}",
            f"create_clock -period {profile.clock_period_ns} -name default",
            csim,
            "close_project",
            "exit",
        ]
    )
    rendered = "\n".join(lines) + "\n"
    if "csynth_design" in rendered:
        raise AssertionError("native CSIM Tcl must not synthesize")
    return rendered

def make_native_vitis_csim_script(
    *,
    work_dir: str | os.PathLike[str],
    original_code: str,
    candidate_code: str,
    testbench_code: str,
    top_kernel: str,
    target_profile: TargetProfile | Mapping[str, Any] | str | None,
    suite_id: str = "public",
    extra_sources: tuple[str, ...] = (),
) -> dict[str, Any]:
    root = Path(work_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    identity = make_typed_outcome_identity(
        phase="public_native_vitis_csim",
        suite_id=suite_id,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
    )
    instrumented, wrapper, adapter = build_typed_testbench_adapter(
        testbench_code,
        wrapped_main_name="agrefactor_native_csim_testbench_main",
        base_identity=identity,
        allowed_phases=("public_native_vitis_csim",),
    )
    files = {
        "candidate.cpp": candidate_code,
        "reference.cpp": original_code,
        "testbench.cpp": instrumented,
        "agrefactor_csim_wrapper.cpp": wrapper,
    }
    normalized_extra_sources = tuple(
        dict.fromkeys(str(item) for item in (extra_sources or ()))
    )
    for raw_path in normalized_extra_sources:
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                "extra source must be a relative path inside the CSIM work directory: "
                f"{raw_path!r}"
            )
        if not (root / relative).is_file():
            raise FileNotFoundError(
                f"extra source not found in CSIM work directory: {raw_path}"
            )
    for name, content in files.items():
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{name} content must not be empty")
        (root / name).write_text(content, encoding="utf-8")
    toolchain_header_resolution = _normalize_vitis_toolchain_headers(root)
    typed_outcome_path = root / "agrefactor_csim_outcome.json"
    adapter_path = root / "typed_outcome_adapter.json"
    _write_json(adapter_path, adapter)
    profile = resolve_target_profile(target_profile)
    tcl = make_native_vitis_csim_tcl(
        top_kernel=top_kernel,
        design_source="candidate.cpp",
        reference_source="reference.cpp",
        testbench_source="testbench.cpp",
        wrapper_source="agrefactor_csim_wrapper.cpp",
        typed_outcome_path=str(typed_outcome_path),
        typed_execution_id=identity["execution_id"],
        typed_phase=identity["phase"],
        target_profile=profile,
        extra_sources=normalized_extra_sources,
    )
    (root / "vitis.tcl").write_text(tcl, encoding="utf-8")
    return {
        "root": root,
        "profile": profile,
        "tcl": tcl,
        "typed_outcome_path": typed_outcome_path,
        "typed_outcome_identity": identity,
        "toolchain_header_resolution": toolchain_header_resolution,
        "typed_outcome_adapter_path": adapter_path,
        "source_files": [
            {"path": "candidate.cpp", "role": "design"},
            {"path": "reference.cpp", "role": "testbench_reference"},
            {"path": "testbench.cpp", "role": "testbench_driver"},
            {"path": "agrefactor_csim_wrapper.cpp", "role": "testbench_wrapper"},
            *[
                {"path": path, "role": "extra_source"}
                for path in normalized_extra_sources
            ],
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

    try:
        raw_suite_id = cv["csim_suite_id"]
    except (KeyError, TypeError):
        raw_suite_id = "public"
    suite_id = str(raw_suite_id).strip() or "public"
    try:
        runtime_contract = cv["csim_runtime_contract"]
    except (KeyError, TypeError):
        runtime_contract = None
    try:
        preflight_authority = cv["csim_preflight_authority"]
    except (KeyError, TypeError):
        preflight_authority = None
    try:
        raw_extra_sources = cv["csim_extra_sources"]
    except (KeyError, TypeError):
        raw_extra_sources = ()
    extra_sources = tuple(dict.fromkeys(str(item) for item in (raw_extra_sources or ())))
    material = make_native_vitis_csim_script(
        work_dir=work_dir,
        original_code=cv["orig_code"],
        candidate_code=cv["curr_code"],
        testbench_code=cv["testbench"],
        top_kernel=top_kernel,
        target_profile=profile,
        suite_id=suite_id,
        extra_sources=extra_sources,
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
        "typed_outcome_identity": material["typed_outcome_identity"],
        "toolchain_header_resolution": material["toolchain_header_resolution"],
        "typed_outcome_adapter_path": str(material["typed_outcome_adapter_path"]),
        "typed_outcome": {"status": "pending"},
        "runtime_contract": runtime_contract,
        "preflight_authority": preflight_authority,
        "runtime_classification": {"status": "pending"},
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

    typed_outcome_path: Path = material["typed_outcome_path"]
    started_outcome_path = Path(str(typed_outcome_path) + ".started")
    for stale in (typed_outcome_path, started_outcome_path):
        if stale.exists():
            if stale.is_symlink() or not stale.is_file():
                raise ValueError(f"unsafe stale native CSIM outcome path: {stale}")
            stale.unlink()

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

    typed = read_typed_testbench_outcome(
        typed_outcome_path,
        expected_identity=material["typed_outcome_identity"],
    )
    started = read_typed_testbench_started(
        started_outcome_path,
        expected_identity=material["typed_outcome_identity"],
    )
    invocation["typed_outcome"] = (
        typed if typed is not None else {"status": "missing_or_invalid"}
    )
    invocation["testbench_started"] = (
        started if started is not None else {"status": "missing_or_invalid"}
    )
    diagnostic = _diagnostic(root, result)
    if execution["timeout"]:
        invocation["runtime_classification"] = {
            "status": "failed",
            "failure_kind": "timeout",
            "failure_owner": "unknown",
            "owner_authority": "unknown",
            "reason_code": "native_csim_timeout",
        }
        _write_json(invocation_path, invocation)
        return "csim_failed", "Native Vitis CSIM timed out.\n" + diagnostic
    if execution["returncode"] != 0:
        typed_returncode = (
            typed.get("testbench_returncode")
            if isinstance(typed, Mapping)
            else None
        )
        deterministic_candidate = (
            isinstance(typed_returncode, int)
            and not isinstance(typed_returncode, bool)
            and typed is not None
            and typed.get("status") == "failed"
            and _candidate_returncode_authorized(runtime_contract, typed_returncode)
            and _preflight_authorizes_candidate_runtime(
                preflight_authority,
                material["typed_outcome_identity"],
            )
        )
        if deterministic_candidate:
            invocation["runtime_classification"] = {
                "status": "failed",
                "failure_kind": "candidate_csim_functional_failure",
                "failure_owner": "candidate",
                "owner_authority": "deterministic_proven",
                "reason_code": "public_csim_mismatch",
                "testbench_returncode": typed_returncode,
                "runtime_contract_kind": runtime_contract.get("kind"),
                "preflight_evidence_sha256": preflight_authority.get(
                    "evidence_sha256"
                ),
            }
            _write_json(invocation_path, invocation)
            return "csim_failed", diagnostic
        if (
            typed is None
            and started is not None
            and _public_runtime_contract_valid(runtime_contract)
            and _preflight_authorizes_candidate_runtime(
                preflight_authority,
                material["typed_outcome_identity"],
            )
        ):
            isolation = _original_isolation_evidence(
                original_code=cv["orig_code"],
                testbench_code=cv["testbench"],
                candidate_code=cv["curr_code"],
                candidate_top_function=top_kernel,
                identity=material["typed_outcome_identity"],
                budget=budget,
                source_root=str(Path(work_dir).resolve()),
                extra_sources=tuple(str(Path(work_dir).resolve() / name) for name in cv.get("csim_extra_sources", ())),
                compile_flags=profile.compile_flags,
            )
            invocation["original_isolation"] = isolation
            if isolation.get("status") == "passed":
                invocation["runtime_classification"] = {
                    "status": "failed",
                    "failure_kind": "candidate_csim_abnormal_termination",
                    "failure_owner": "candidate",
                    "owner_authority": "differential_isolation_proven",
                    "reason_code": "public_csim_candidate_abnormal_termination",
                    "testbench_returncode": None,
                    "preflight_evidence_sha256": preflight_authority.get(
                        "evidence_sha256"
                    ),
                    "original_isolation": isolation,
                }
                _write_json(invocation_path, invocation)
                return "csim_failed", diagnostic
        invocation["runtime_classification"] = {
            "status": "failed",
            "failure_kind": "ownership_unknown",
            "failure_owner": "unknown",
            "owner_authority": "unknown",
            "reason_code": "native_csim_nonzero_without_deterministic_owner",
            "testbench_returncode": typed_returncode,
        }
        _write_json(invocation_path, invocation)
        return "csim_execution_failed", diagnostic
    invocation["runtime_classification"] = {
        "status": "passed",
        "failure_kind": None,
        "failure_owner": "none",
        "owner_authority": "deterministic_proven",
        "reason_code": "csim_passed",
    }
    _write_json(invocation_path, invocation)
    return "succeeded", ""
