"""Authoritative staged host preflight with typed component ownership."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Iterable

from agrefactor.evidence import (
    TestbenchDiagnostic,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightComponent,
    TestbenchPreflightReasonCode,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchPreflightSubstage,
    TestbenchPreflightSubstep,
    TestbenchPreflightSubstepStatus,
    TestbenchStage,
)
from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)


_COMPILE_INCREMENT = {"tool_calls": 1, "compile_calls": 1}
_SYMBOL_INCREMENT = {"tool_calls": 1}
_TOP_RE = re.compile(r"^[A-Za-z_]\w*$")
_NM_LINE_RE = re.compile(
    r"^\s*(?:[0-9A-Fa-f]+\s+)?(?P<kind>[A-Za-z?])\s+"
    r"(?P<symbol>.+?)\s*$"
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _usage_dict(usage: BudgetUsage) -> dict[str, Any]:
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


def _clean_top(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null")
    cleaned = value.strip()
    if not cleaned:
        return None
    if _TOP_RE.fullmatch(cleaned) is None:
        raise ValueError(
            f"{name} must be an unqualified C/C++ identifier"
        )
    return cleaned


def _base_flags(
    *,
    compiler: str,
    extra_flags: Iterable[str],
    include_dirs: Iterable[Path],
) -> list[str]:
    command = [
        compiler,
        "-D__SYNTHESIS__",
        "-O2",
        "-flto",
        "-Wno-unknown-pragmas",
    ]
    resolved = list(include_dirs)
    xilinx_hls = os.getenv("XILINX_HLS")
    if xilinx_hls:
        resolved.append(Path(xilinx_hls) / "include")
    for include_dir in dict.fromkeys(resolved):
        command.extend(["-I", str(include_dir)])
    command.extend(str(item) for item in extra_flags)
    return command


def _prospective_increment(
    *,
    reference_top: str | None,
    candidate_top: str | None,
) -> dict[str, int]:
    interface_checks = int(reference_top is not None) + int(
        candidate_top is not None
    )
    symbol_checks = (
        0
        if interface_checks == 0
        else 1 + interface_checks
    )
    compile_launches = 4 + interface_checks
    return {
        "tool_calls": compile_launches + symbol_checks,
        "compile_calls": compile_launches,
    }


def _reserve_total(
    budget: BudgetManager | None,
    *,
    requested: dict[str, int],
    invocation: dict[str, Any],
    invocation_path: Path,
) -> None:
    invocation["budget"]["requested_total_increment"] = dict(
        requested
    )
    if budget is None:
        invocation["budget"]["status"] = "not_configured"
        _write_json(invocation_path, invocation)
        return
    before = budget.snapshot()
    try:
        budget.ensure_available(**requested)
    except BudgetExceededError as exc:
        invocation["budget"] = {
            "status": "blocked",
            "checkpoint": "before_staged_preflight",
            "requested_total_increment": dict(requested),
            "resource": exc.resource,
            "limit": exc.limit,
            "attempted": exc.attempted,
            "usage_before": _usage_dict(before),
        }
        invocation["execution"] = {
            "status": "blocked_by_budget",
            "returncode": None,
            "timeout": False,
        }
        _write_json(invocation_path, invocation)
        raise
    invocation["budget"] = {
        "status": "reserved",
        "checkpoint": "before_staged_preflight",
        "requested_total_increment": dict(requested),
        "usage_before": _usage_dict(before),
    }
    _write_json(invocation_path, invocation)


def _consume(
    budget: BudgetManager | None,
    increment: dict[str, int],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if budget is None:
        return None, None
    before = budget.snapshot()
    after = budget.consume(**increment)
    return _usage_dict(before), _usage_dict(after)


def _launch(
    *,
    command: list[str],
    directory: Path,
    timeout_s: float,
    component: TestbenchPreflightComponent,
    substage: TestbenchPreflightSubstage,
    artifact: Path | None,
    budget: BudgetManager | None,
    increment: dict[str, int],
    invocation: dict[str, Any],
    invocation_path: Path,
) -> TestbenchPreflightSubstep:
    usage_before, usage_after = _consume(budget, increment)
    record: dict[str, Any] = {
        "component": component.value,
        "substage": substage.value,
        "command": list(command),
        "status": "running",
        "returncode": None,
        "timeout": False,
        "artifact": None if artifact is None else str(artifact),
        "budget_increment": dict(increment),
        "usage_before": usage_before,
        "usage_after": usage_after,
    }
    invocation["command"] = list(command)
    invocation["substeps"].append(record)
    _write_json(invocation_path, invocation)

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        record.update(
            {
                "status": "timeout",
                "timeout": True,
                "duration_s": duration,
            }
        )
        _write_json(invocation_path, invocation)
        return TestbenchPreflightSubstep(
            substage=substage,
            component=component,
            status=TestbenchPreflightSubstepStatus.ERROR,
            command=tuple(command),
            return_code=None,
            failure_kind=TestbenchFailureKind.COMPILE_TIMEOUT,
            stdout=stdout,
            stderr=stderr,
            artifact=None if artifact is None else str(artifact),
            duration_s=duration,
        )
    except FileNotFoundError as exc:
        duration = time.monotonic() - started
        record.update(
            {
                "status": "launch_error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:4000],
                "duration_s": duration,
            }
        )
        _write_json(invocation_path, invocation)
        return TestbenchPreflightSubstep(
            substage=substage,
            component=component,
            status=TestbenchPreflightSubstepStatus.ERROR,
            command=tuple(command),
            return_code=None,
            failure_kind=TestbenchFailureKind.COMPILER_NOT_FOUND,
            stderr=str(exc),
            artifact=None if artifact is None else str(artifact),
            duration_s=duration,
        )

    duration = time.monotonic() - started
    status = (
        TestbenchPreflightSubstepStatus.PASSED
        if completed.returncode == 0
        else TestbenchPreflightSubstepStatus.FAILED
    )
    record.update(
        {
            "status": status.value,
            "returncode": completed.returncode,
            "duration_s": duration,
        }
    )
    _write_json(invocation_path, invocation)
    return TestbenchPreflightSubstep(
        substage=substage,
        component=component,
        status=status,
        command=tuple(command),
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        artifact=None if artifact is None else str(artifact),
        duration_s=duration,
    )


def _compiler_diagnostics(
    stderr: str,
    *,
    fallback_message: str,
) -> tuple[TestbenchFailureKind, tuple[TestbenchDiagnostic, ...]]:
    from agrefactor.evaluation.testbench_preflight import (
        classify_compile_failure,
        parse_compiler_diagnostics,
    )

    kind = classify_compile_failure(stderr)
    diagnostics = parse_compiler_diagnostics(
        stderr,
        default_kind=kind,
    )
    if not diagnostics:
        diagnostics = (
            TestbenchDiagnostic(
                kind=kind,
                message=fallback_message,
                raw=stderr or None,
            ),
        )
    return kind, diagnostics


def _artifacts(directory: Path, names: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        str(directory / name)
        for name in names
        if (directory / name).exists()
    )


def _finish_invocation(
    invocation: dict[str, Any],
    path: Path,
    *,
    status: str,
    returncode: int | None,
    reason_codes: tuple[TestbenchPreflightReasonCode, ...],
    failed_component: TestbenchPreflightComponent | None,
    timeout: bool = False,
    budget: BudgetManager | None = None,
) -> None:
    invocation["reason_code"] = reason_codes[0].value
    invocation["reason_codes"] = [
        item.value for item in reason_codes
    ]
    invocation["failed_component"] = (
        None if failed_component is None else failed_component.value
    )
    invocation["execution"] = {
        "status": status,
        "returncode": returncode,
        "timeout": timeout,
    }
    if budget is not None:
        invocation["budget"]["status"] = "consumed"
        invocation["budget"]["usage_after"] = _usage_dict(
            budget.snapshot()
        )
    _write_json(path, invocation)


def _tool_error_result(
    *,
    step: TestbenchPreflightSubstep,
    directory: Path,
    substeps: list[TestbenchPreflightSubstep],
    component: TestbenchPreflightComponent,
) -> TestbenchPreflightResult:
    timed_out = (
        step.failure_kind is TestbenchFailureKind.COMPILE_TIMEOUT
    )
    message = step.stderr or (
        f"{step.substage.value} timed out"
        if timed_out
        else f"{step.substage.value} failed to launch"
    )
    reason_codes = (
        TestbenchPreflightReasonCode.TOOLCHAIN_FAILED,
        TestbenchPreflightReasonCode.OWNERSHIP_UNKNOWN,
    )
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.ERROR,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=step.failure_kind,
        failure_owner=TestbenchFailureOwner.TOOLCHAIN,
        return_code=None,
        command=step.command,
        diagnostics=(
            TestbenchDiagnostic(
                kind=step.failure_kind,
                message=message,
                raw=message,
            ),
        ),
        stderr=message,
        artifacts=_artifacts(
            directory,
            (
                "testbench.cpp",
                "orig_code.cpp",
                "refactor_code.cpp",
                "testbench.o",
                "orig_code.o",
                "refactor_code.o",
                "reference_interface.o",
                "candidate_interface.o",
            ),
        ),
        duration_s=sum(item.duration_s for item in substeps),
        reason_codes=reason_codes,
        failed_component=component,
        substeps=tuple(substeps),
    )


def _compile_failure_result(
    *,
    step: TestbenchPreflightSubstep,
    directory: Path,
    substeps: list[TestbenchPreflightSubstep],
    component: TestbenchPreflightComponent,
    owner: TestbenchFailureOwner,
    reason: TestbenchPreflightReasonCode,
) -> TestbenchPreflightResult:
    kind, diagnostics = _compiler_diagnostics(
        step.stderr,
        fallback_message=f"{component.value} compilation failed",
    )
    substeps[-1] = replace(step, failure_kind=kind)
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.FAILED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=kind,
        failure_owner=owner,
        return_code=step.return_code,
        command=step.command,
        diagnostics=diagnostics,
        stdout=step.stdout,
        stderr=step.stderr,
        artifacts=_artifacts(
            directory,
            (
                "testbench.cpp",
                "orig_code.cpp",
                "refactor_code.cpp",
                "testbench.o",
                "orig_code.o",
                "refactor_code.o",
            ),
        ),
        duration_s=sum(item.duration_s for item in substeps),
        reason_codes=(reason,),
        failed_component=component,
        substeps=tuple(substeps),
    )


def _parse_nm_symbols(output: str) -> tuple[str, ...]:
    symbols: list[str] = []
    for raw in output.splitlines():
        match = _NM_LINE_RE.match(raw)
        if not match:
            continue
        symbol = re.sub(r"\s+", " ", match.group("symbol")).strip()
        for old, new in (
            (" *", "*"),
            ("* ", "*"),
            (" &", "&"),
            ("& ", "&"),
            (", ", ","),
        ):
            symbol = symbol.replace(old, new)
        symbols.append(symbol)
    return tuple(dict.fromkeys(symbols))


def _symbol_base(symbol: str) -> str:
    base = symbol.split("(", 1)[0].strip()
    return base.rsplit("::", 1)[-1]


def _semantic_failure_step(
    *,
    substage: TestbenchPreflightSubstage,
    component: TestbenchPreflightComponent,
    kind: TestbenchFailureKind,
    message: str,
) -> TestbenchPreflightSubstep:
    return TestbenchPreflightSubstep(
        substage=substage,
        component=component,
        status=TestbenchPreflightSubstepStatus.FAILED,
        command=(),
        return_code=1,
        failure_kind=kind,
        stderr=message,
    )


def _semantic_failure_result(
    *,
    directory: Path,
    substeps: list[TestbenchPreflightSubstep],
    reason_codes: tuple[TestbenchPreflightReasonCode, ...],
    owner: TestbenchFailureOwner,
    component: TestbenchPreflightComponent,
    substage: TestbenchPreflightSubstage,
    message: str,
    kind: TestbenchFailureKind,
) -> TestbenchPreflightResult:
    substeps.append(
        _semantic_failure_step(
            substage=substage,
            component=component,
            kind=kind,
            message=message,
        )
    )
    return TestbenchPreflightResult(
        status=TestbenchPreflightStatus.FAILED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=kind,
        failure_owner=owner,
        return_code=1,
        command=(),
        diagnostics=(
            TestbenchDiagnostic(
                kind=kind,
                message=message,
            ),
        ),
        stderr=message,
        artifacts=_artifacts(
            directory,
            (
                "testbench.cpp",
                "orig_code.cpp",
                "refactor_code.cpp",
                "testbench.o",
                "orig_code.o",
                "refactor_code.o",
                "reference_interface.o",
                "candidate_interface.o",
            ),
        ),
        duration_s=sum(item.duration_s for item in substeps),
        reason_codes=reason_codes,
        failed_component=component,
        substeps=tuple(substeps),
    )


def _record_semantic_step(
    invocation: dict[str, Any],
    invocation_path: Path,
    step: TestbenchPreflightSubstep,
) -> None:
    invocation["substeps"].append(
        {
            "component": step.component.value,
            "substage": step.substage.value,
            "command": [],
            "status": step.status.value,
            "returncode": step.return_code,
            "timeout": False,
            "artifact": step.artifact,
            "budget_increment": {
                "tool_calls": 0,
                "compile_calls": 0,
            },
            "usage_before": None,
            "usage_after": None,
            "semantic_check": True,
        }
    )
    _write_json(invocation_path, invocation)


def _return_failure(
    *,
    result: TestbenchPreflightResult,
    invocation: dict[str, Any],
    invocation_path: Path,
    budget: BudgetManager | None,
) -> TestbenchPreflightResult:
    if (
        result.substeps
        and not result.substeps[-1].command
    ):
        _record_semantic_step(
            invocation,
            invocation_path,
            result.substeps[-1],
        )
    if result.failure_kind is TestbenchFailureKind.COMPILER_NOT_FOUND:
        execution_status = "launch_error"
    elif result.failure_kind is TestbenchFailureKind.COMPILE_TIMEOUT:
        execution_status = "timeout"
    elif result.status is TestbenchPreflightStatus.ERROR:
        execution_status = "error"
    else:
        # Compatibility: execution.status records whether the physical
        # compiler/linker invocation completed normally. Typed Preflight
        # failure remains authoritative in result.status, reason_code,
        # failed_component, and substep status/returncode.
        execution_status = "completed"
    _finish_invocation(
        invocation,
        invocation_path,
        status=execution_status,
        returncode=result.return_code,
        reason_codes=result.reason_codes,
        failed_component=result.failed_component,
        timeout=(
            result.failure_kind
            is TestbenchFailureKind.COMPILE_TIMEOUT
        ),
        budget=budget,
    )
    return result


def run_staged_preflight(
    *,
    compiler: str,
    timeout_s: float,
    extra_flags: Iterable[str],
    include_dirs: Iterable[Path],
    work_dir: str | Path,
    testbench_code: str,
    original_code: str,
    candidate_code: str,
    budget: BudgetManager | None = None,
    original_top_function: str | None = None,
    candidate_top_function: str | None = None,
) -> TestbenchPreflightResult:
    """Run independently owned compile, symbol, ABI, and link stages."""

    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    sources = {
        "testbench.cpp": testbench_code,
        "orig_code.cpp": original_code,
        "refactor_code.cpp": candidate_code,
    }
    for name, content in sources.items():
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{name} source must not be empty")
        (directory / name).write_text(content, encoding="utf-8")

    invocation_path = directory / "testbench_preflight_invocation.json"
    invocation: dict[str, Any] = {
        "schema_version": 2,
        "phase": "testbench_preflight_staged",
        "work_dir": str(directory.resolve()),
        "source_files": list(sources),
        "compiler": compiler,
        "symbol_tool": os.getenv("AGREFACTOR_NM", "nm"),
        "timeout_seconds": timeout_s,
        "top_contract": {
            "reference_top_function": original_top_function,
            "candidate_top_function": candidate_top_function,
        },
        "substeps": [],
        "command": [],
        "reason_code": None,
        "reason_codes": [],
        "failed_component": None,
        "budget": {"status": "pending"},
        "execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
    }
    _write_json(invocation_path, invocation)

    try:
        reference_top = _clean_top(
            original_top_function,
            "original_top_function",
        )
        candidate_top = _clean_top(
            candidate_top_function,
            "candidate_top_function",
        )
    except (TypeError, ValueError) as exc:
        result = TestbenchPreflightResult(
            status=TestbenchPreflightStatus.ERROR,
            stage=TestbenchStage.STATIC_CHECK,
            failure_kind=TestbenchFailureKind.UNKNOWN,
            failure_owner=TestbenchFailureOwner.CONFIGURATION,
            return_code=None,
            command=(),
            diagnostics=(
                TestbenchDiagnostic(
                    kind=TestbenchFailureKind.UNKNOWN,
                    message=str(exc),
                ),
            ),
            stderr=str(exc),
            artifacts=_artifacts(directory, sources),
            reason_codes=(
                TestbenchPreflightReasonCode.CONFIGURATION_FAILED,
            ),
            failed_component=(
                TestbenchPreflightComponent.CONFIGURATION
            ),
        )
        return _return_failure(
            result=result,
            invocation=invocation,
            invocation_path=invocation_path,
            budget=budget,
        )

    requested = _prospective_increment(
        reference_top=reference_top,
        candidate_top=candidate_top,
    )
    _reserve_total(
        budget,
        requested=requested,
        invocation=invocation,
        invocation_path=invocation_path,
    )

    base = _base_flags(
        compiler=compiler,
        extra_flags=extra_flags,
        include_dirs=include_dirs,
    )
    substeps: list[TestbenchPreflightSubstep] = []
    compile_specs = (
        (
            "testbench.cpp",
            "testbench.o",
            TestbenchPreflightComponent.TESTBENCH,
            TestbenchPreflightSubstage.TESTBENCH_COMPILE,
            TestbenchFailureOwner.TESTBENCH,
            TestbenchPreflightReasonCode.TESTBENCH_COMPILE_FAILED,
        ),
        (
            "orig_code.cpp",
            "orig_code.o",
            TestbenchPreflightComponent.REFERENCE,
            TestbenchPreflightSubstage.REFERENCE_COMPILE,
            TestbenchFailureOwner.ORIGINAL,
            TestbenchPreflightReasonCode.REFERENCE_COMPILE_FAILED,
        ),
        (
            "refactor_code.cpp",
            "refactor_code.o",
            TestbenchPreflightComponent.CANDIDATE,
            TestbenchPreflightSubstage.CANDIDATE_COMPILE,
            TestbenchFailureOwner.CANDIDATE,
            TestbenchPreflightReasonCode.CANDIDATE_COMPILE_FAILED,
        ),
    )

    for (
        source_name,
        object_name,
        component,
        substage,
        owner,
        reason,
    ) in compile_specs:
        step = _launch(
            command=[
                *base,
                "-c",
                source_name,
                "-o",
                object_name,
            ],
            directory=directory,
            timeout_s=timeout_s,
            component=component,
            substage=substage,
            artifact=directory / object_name,
            budget=budget,
            increment=_COMPILE_INCREMENT,
            invocation=invocation,
            invocation_path=invocation_path,
        )
        substeps.append(step)
        if step.status is TestbenchPreflightSubstepStatus.ERROR:
            return _return_failure(
                result=_tool_error_result(
                    step=step,
                    directory=directory,
                    substeps=substeps,
                    component=component,
                ),
                invocation=invocation,
                invocation_path=invocation_path,
                budget=budget,
            )
        if step.status is TestbenchPreflightSubstepStatus.FAILED:
            return _return_failure(
                result=_compile_failure_result(
                    step=step,
                    directory=directory,
                    substeps=substeps,
                    component=component,
                    owner=owner,
                    reason=reason,
                ),
                invocation=invocation,
                invocation_path=invocation_path,
                budget=budget,
            )

    if reference_top is not None or candidate_top is not None:
        nm = os.getenv("AGREFACTOR_NM", "nm")
        symbol_specs: list[
            tuple[
                str,
                str,
                TestbenchPreflightComponent,
                TestbenchPreflightSubstage,
                str | None,
            ]
        ] = [
            (
                "testbench.o",
                "testbench",
                TestbenchPreflightComponent.SYMBOL_CHECK,
                TestbenchPreflightSubstage.TESTBENCH_SYMBOL_CHECK,
                None,
            )
        ]
        if reference_top is not None:
            symbol_specs.append(
                (
                    "orig_code.o",
                    "reference",
                    TestbenchPreflightComponent.REFERENCE,
                    TestbenchPreflightSubstage.REFERENCE_SYMBOL_CHECK,
                    reference_top,
                )
            )
        if candidate_top is not None:
            symbol_specs.append(
                (
                    "refactor_code.o",
                    "candidate",
                    TestbenchPreflightComponent.CANDIDATE,
                    TestbenchPreflightSubstage.CANDIDATE_SYMBOL_CHECK,
                    candidate_top,
                )
            )

        symbol_outputs: dict[str, tuple[str, ...]] = {}
        for object_name, key, component, substage, top in symbol_specs:
            flags = (
                ["-C", "--undefined-only", object_name]
                if key == "testbench"
                else ["-C", "--defined-only", object_name]
            )
            step = _launch(
                command=[nm, *flags],
                directory=directory,
                timeout_s=timeout_s,
                component=component,
                substage=substage,
                artifact=None,
                budget=budget,
                increment=_SYMBOL_INCREMENT,
                invocation=invocation,
                invocation_path=invocation_path,
            )
            substeps.append(step)
            if step.status is not TestbenchPreflightSubstepStatus.PASSED:
                return _return_failure(
                    result=_tool_error_result(
                        step=step,
                        directory=directory,
                        substeps=substeps,
                        component=(
                            TestbenchPreflightComponent.SYMBOL_CHECK
                        ),
                    ),
                    invocation=invocation,
                    invocation_path=invocation_path,
                    budget=budget,
                )
            symbol_outputs[key] = _parse_nm_symbols(step.stdout)
            if top is not None:
                defined = {
                    symbol
                    for symbol in symbol_outputs[key]
                    if _symbol_base(symbol) == top
                }
                if not defined:
                    reason = (
                        TestbenchPreflightReasonCode.REFERENCE_TOP_MISSING
                        if key == "reference"
                        else TestbenchPreflightReasonCode.CANDIDATE_TOP_MISSING
                    )
                    owner = (
                        TestbenchFailureOwner.ORIGINAL
                        if key == "reference"
                        else TestbenchFailureOwner.CANDIDATE
                    )
                    result = _semantic_failure_result(
                        directory=directory,
                        substeps=substeps,
                        reason_codes=(reason,),
                        owner=owner,
                        component=component,
                        substage=substage,
                        message=(
                            f"{key} object does not define the required "
                            f"top symbol: {top}"
                        ),
                        kind=TestbenchFailureKind.UNDECLARED_SYMBOL,
                    )
                    return _return_failure(
                        result=result,
                        invocation=invocation,
                        invocation_path=invocation_path,
                        budget=budget,
                    )

        testbench_symbols = symbol_outputs.get("testbench", ())
        for key, top, component, substage, owner in (
            (
                "reference",
                reference_top,
                TestbenchPreflightComponent.REFERENCE,
                TestbenchPreflightSubstage.REFERENCE_SYMBOL_CHECK,
                TestbenchFailureOwner.TESTBENCH,
            ),
            (
                "candidate",
                candidate_top,
                TestbenchPreflightComponent.CANDIDATE,
                TestbenchPreflightSubstage.CANDIDATE_SYMBOL_CHECK,
                TestbenchFailureOwner.CANDIDATE,
            ),
        ):
            if top is None:
                continue
            expected = {
                symbol
                for symbol in testbench_symbols
                if _symbol_base(symbol) == top
            }
            defined = {
                symbol
                for symbol in symbol_outputs.get(key, ())
                if _symbol_base(symbol) == top
            }
            if not expected or defined.isdisjoint(expected):
                result = _semantic_failure_result(
                    directory=directory,
                    substeps=substeps,
                    reason_codes=(
                        TestbenchPreflightReasonCode.INTERFACE_MISMATCH,
                    ),
                    owner=owner,
                    component=component,
                    substage=substage,
                    message=(
                        "Public Testbench symbol contract does not match "
                        f"the {key} top interface: {top}"
                    ),
                    kind=TestbenchFailureKind.LINKAGE_MISMATCH,
                )
                return _return_failure(
                    result=result,
                    invocation=invocation,
                    invocation_path=invocation_path,
                    budget=budget,
                )

    for (
        top,
        object_name,
        output_name,
        component,
        substage,
        owner,
    ) in (
        (
            reference_top,
            "orig_code.o",
            "reference_interface.o",
            TestbenchPreflightComponent.REFERENCE,
            TestbenchPreflightSubstage.REFERENCE_INTERFACE_CHECK,
            TestbenchFailureOwner.TESTBENCH,
        ),
        (
            candidate_top,
            "refactor_code.o",
            "candidate_interface.o",
            TestbenchPreflightComponent.CANDIDATE,
            TestbenchPreflightSubstage.CANDIDATE_INTERFACE_CHECK,
            TestbenchFailureOwner.CANDIDATE,
        ),
    ):
        if top is None:
            continue
        step = _launch(
            command=[
                compiler,
                "-flto",
                "-Werror=lto-type-mismatch",
                "-r",
                "testbench.o",
                object_name,
                "-o",
                output_name,
            ],
            directory=directory,
            timeout_s=timeout_s,
            component=component,
            substage=substage,
            artifact=directory / output_name,
            budget=budget,
            increment=_COMPILE_INCREMENT,
            invocation=invocation,
            invocation_path=invocation_path,
        )
        substeps.append(step)
        if step.status is TestbenchPreflightSubstepStatus.ERROR:
            return _return_failure(
                result=_tool_error_result(
                    step=step,
                    directory=directory,
                    substeps=substeps,
                    component=component,
                ),
                invocation=invocation,
                invocation_path=invocation_path,
                budget=budget,
            )
        if step.status is TestbenchPreflightSubstepStatus.FAILED:
            kind, diagnostics = _compiler_diagnostics(
                step.stderr,
                fallback_message=(
                    f"{component.value} interface probe failed"
                ),
            )
            kind = TestbenchFailureKind.LINKAGE_MISMATCH
            substeps[-1] = replace(step, failure_kind=kind)
            result = TestbenchPreflightResult(
                status=TestbenchPreflightStatus.FAILED,
                stage=TestbenchStage.COMPILE_LINK,
                failure_kind=kind,
                failure_owner=owner,
                return_code=step.return_code,
                command=step.command,
                diagnostics=diagnostics,
                stdout=step.stdout,
                stderr=step.stderr,
                artifacts=_artifacts(
                    directory,
                    (
                        "testbench.cpp",
                        "orig_code.cpp",
                        "refactor_code.cpp",
                        "testbench.o",
                        "orig_code.o",
                        "refactor_code.o",
                        "reference_interface.o",
                        "candidate_interface.o",
                    ),
                ),
                duration_s=sum(
                    item.duration_s for item in substeps
                ),
                reason_codes=(
                    TestbenchPreflightReasonCode.INTERFACE_MISMATCH,
                ),
                failed_component=component,
                substeps=tuple(substeps),
            )
            return _return_failure(
                result=result,
                invocation=invocation,
                invocation_path=invocation_path,
                budget=budget,
            )

    link_step = _launch(
        command=[
            *base,
            "-Werror=lto-type-mismatch",
            "testbench.o",
            "orig_code.o",
            "refactor_code.o",
            "-o",
            "testbench_preflight",
        ],
        directory=directory,
        timeout_s=timeout_s,
        component=TestbenchPreflightComponent.LINK,
        substage=TestbenchPreflightSubstage.LINK,
        artifact=directory / "testbench_preflight",
        budget=budget,
        increment=_COMPILE_INCREMENT,
        invocation=invocation,
        invocation_path=invocation_path,
    )
    substeps.append(link_step)
    if link_step.status is TestbenchPreflightSubstepStatus.ERROR:
        return _return_failure(
            result=_tool_error_result(
                step=link_step,
                directory=directory,
                substeps=substeps,
                component=TestbenchPreflightComponent.LINK,
            ),
            invocation=invocation,
            invocation_path=invocation_path,
            budget=budget,
        )
    if link_step.status is TestbenchPreflightSubstepStatus.FAILED:
        from agrefactor.evaluation.testbench_preflight import (
            infer_linkage_mismatch,
        )

        mismatch_names = ()
        if reference_top is None and candidate_top is None:
            mismatch_names = infer_linkage_mismatch(
                link_step.stderr,
                testbench_code=testbench_code,
                original_code=original_code,
                candidate_code=candidate_code,
            )
        if mismatch_names:
            kind = TestbenchFailureKind.LINKAGE_MISMATCH
            owner = TestbenchFailureOwner.TESTBENCH
            reason_codes = (
                TestbenchPreflightReasonCode.INTERFACE_MISMATCH,
            )
            diagnostics = (
                TestbenchDiagnostic(
                    kind=kind,
                    message=(
                        "testbench C/C++ language linkage does not "
                        "match implementation definitions: "
                        + ", ".join(mismatch_names)
                    ),
                    file="testbench.cpp",
                    raw=link_step.stderr or None,
                ),
            )
            failed_component = (
                TestbenchPreflightComponent.SYMBOL_CHECK
            )
        else:
            kind, diagnostics = _compiler_diagnostics(
                link_step.stderr,
                fallback_message="final preflight link failed",
            )
            owner = TestbenchFailureOwner.UNKNOWN
            reason_codes = (
                TestbenchPreflightReasonCode.LINK_FAILED,
                TestbenchPreflightReasonCode.OWNERSHIP_UNKNOWN,
            )
            failed_component = TestbenchPreflightComponent.LINK
        substeps[-1] = replace(link_step, failure_kind=kind)
        result = TestbenchPreflightResult(
            status=TestbenchPreflightStatus.FAILED,
            stage=TestbenchStage.COMPILE_LINK,
            failure_kind=kind,
            failure_owner=owner,
            return_code=link_step.return_code,
            command=link_step.command,
            diagnostics=diagnostics,
            stdout=link_step.stdout,
            stderr=link_step.stderr,
            artifacts=_artifacts(
                directory,
                (
                    "testbench.cpp",
                    "orig_code.cpp",
                    "refactor_code.cpp",
                    "testbench.o",
                    "orig_code.o",
                    "refactor_code.o",
                    "reference_interface.o",
                    "candidate_interface.o",
                ),
            ),
            duration_s=sum(item.duration_s for item in substeps),
            reason_codes=reason_codes,
            failed_component=failed_component,
            substeps=tuple(substeps),
        )
        return _return_failure(
            result=result,
            invocation=invocation,
            invocation_path=invocation_path,
            budget=budget,
        )

    result = TestbenchPreflightResult(
        status=TestbenchPreflightStatus.PASSED,
        stage=TestbenchStage.COMPILE_LINK,
        failure_kind=TestbenchFailureKind.NONE,
        failure_owner=TestbenchFailureOwner.NONE,
        return_code=0,
        command=link_step.command,
        stdout=link_step.stdout,
        stderr=link_step.stderr,
        artifacts=_artifacts(
            directory,
            (
                "testbench.cpp",
                "orig_code.cpp",
                "refactor_code.cpp",
                "testbench.o",
                "orig_code.o",
                "refactor_code.o",
                "reference_interface.o",
                "candidate_interface.o",
                "testbench_preflight",
            ),
        ),
        duration_s=sum(item.duration_s for item in substeps),
        reason_codes=(TestbenchPreflightReasonCode.PASSED,),
        failed_component=None,
        substeps=tuple(substeps),
    )
    _finish_invocation(
        invocation,
        invocation_path,
        status="completed",
        returncode=0,
        reason_codes=result.reason_codes,
        failed_component=None,
        budget=budget,
    )
    return result
