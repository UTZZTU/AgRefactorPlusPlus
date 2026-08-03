"""Typed, reference-isolated host harness for Legacy ``simple_iter``.

The harness uses physical compiler/object/symbol evidence.  It does not infer
ownership from free-form compiler text and it never treats its internal host
run as the final S3.8 correctness authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence


LEGACY_HARNESS_CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class LegacyHarnessResult:
    status: str
    reason_code: str
    failure_owner: str
    return_code: int
    compile_calls: int
    csim_calls: int
    tool_calls: int
    message: str | None
    reference_symbols: Mapping[str, str]
    candidate_symbols: Mapping[str, str]
    artifact_path: str

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_safe_dict(self) -> dict[str, Any]:
        message = self.message or ""
        return {
            "schema_version": LEGACY_HARNESS_CONTRACT_VERSION,
            "status": self.status,
            "reason_code": self.reason_code,
            "failure_owner": self.failure_owner,
            "return_code": self.return_code,
            "compile_calls": self.compile_calls,
            "csim_calls": self.csim_calls,
            "tool_calls": self.tool_calls,
            "message_sha256": sha256(message.encode("utf-8")).hexdigest(),
            "message_chars": len(message),
            "reference_symbols": dict(sorted(self.reference_symbols.items())),
            "candidate_symbols": dict(sorted(self.candidate_symbols.items())),
            "reference_isolated": True,
            "candidate_symbol_contract_enforced": True,
            "synthesis_macro_defined": True,
        }


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(dict(value), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    log_name: str,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    (cwd / log_name).write_text(completed.stdout or "", encoding="utf-8")
    return completed


def _global_defined_symbols(
    object_path: Path,
    *,
    cwd: Path,
    log_name: str,
) -> tuple[dict[str, str], subprocess.CompletedProcess[str]]:
    completed = _run_logged(
        ["nm", "-g", "--defined-only", "--format=posix", object_path.name],
        cwd=cwd,
        log_name=log_name,
    )
    symbols: dict[str, str] = {}
    if completed.returncode == 0:
        for raw_line in (completed.stdout or "").splitlines():
            parts = raw_line.split()
            if len(parts) >= 2:
                symbols[parts[0]] = parts[1]
    return symbols, completed


def _strong_text_symbol(symbols: Mapping[str, str], name: str) -> bool:
    return symbols.get(name) == "T"


def run_legacy_harness(
    *,
    output_dir: str | os.PathLike[str],
    reference_path: str | os.PathLike[str],
    candidate_path: str | os.PathLike[str],
    testbench_path: str | os.PathLike[str],
    reference_top_name: str,
    candidate_top_name: str,
) -> LegacyHarnessResult:
    """Compile, symbol-check, link, and run one Legacy candidate.

    Reference, candidate, and testbench are compiled in one physical compiler
    launch to remain inside the frozen S3.8 compile budget.  Object existence
    and ``nm`` symbol evidence determine ownership without regex-gating stderr.
    """

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    reference = (root / Path(reference_path)).resolve() if not Path(reference_path).is_absolute() else Path(reference_path).resolve()
    candidate = (root / Path(candidate_path)).resolve() if not Path(candidate_path).is_absolute() else Path(candidate_path).resolve()
    testbench = (root / Path(testbench_path)).resolve() if not Path(testbench_path).is_absolute() else Path(testbench_path).resolve()
    result_path = root / "legacy_harness_result.json"

    for path, name in (
        (reference, "reference"),
        (candidate, "candidate"),
        (testbench, "testbench"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{name} source not found: {path}")

    object_paths = {
        "reference": root / f"{reference.stem}.o",
        "candidate": root / f"{candidate.stem}.o",
        "testbench": root / f"{testbench.stem}.o",
    }
    for path in (*object_paths.values(), root / "csim"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    include_args: list[str] = []
    xilinx_hls = os.getenv("XILINX_HLS")
    if xilinx_hls:
        include_args.extend(["-I", str(Path(xilinx_hls) / "include")])
    compile_command = [
        "g++",
        "-D__SYNTHESIS__",
        "-O2",
        "-Wno-unknown-pragmas",
        *include_args,
        "-c",
        reference.name,
        candidate.name,
        testbench.name,
    ]
    compile_result = _run_logged(
        compile_command,
        cwd=root,
        log_name="legacy_harness_compile.log",
    )
    if compile_result.returncode != 0:
        missing = [name for name, path in object_paths.items() if not path.is_file()]
        owner = missing[0] if len(missing) == 1 else "unknown"
        reason = f"{owner}_compile_failed" if owner != "unknown" else "compile_failure_owner_unknown"
        result = LegacyHarnessResult(
            status="compile_failed",
            reason_code=reason,
            failure_owner=owner,
            return_code=compile_result.returncode,
            compile_calls=1,
            csim_calls=0,
            tool_calls=1,
            message=compile_result.stdout or "",
            reference_symbols={},
            candidate_symbols={},
            artifact_path=str(result_path),
        )
        _write_json_atomic(result_path, result.to_safe_dict())
        return result

    reference_symbols, reference_nm = _global_defined_symbols(
        object_paths["reference"], cwd=root, log_name="legacy_harness_reference_nm.log"
    )
    candidate_symbols, candidate_nm = _global_defined_symbols(
        object_paths["candidate"], cwd=root, log_name="legacy_harness_candidate_nm.log"
    )
    if reference_nm.returncode != 0 or candidate_nm.returncode != 0:
        message = (reference_nm.stdout or "") + (candidate_nm.stdout or "")
        result = LegacyHarnessResult(
            status="infrastructure_error",
            reason_code="nm_failed",
            failure_owner="toolchain",
            return_code=reference_nm.returncode or candidate_nm.returncode,
            compile_calls=1,
            csim_calls=0,
            tool_calls=3,
            message=message,
            reference_symbols=reference_symbols,
            candidate_symbols=candidate_symbols,
            artifact_path=str(result_path),
        )
        _write_json_atomic(result_path, result.to_safe_dict())
        return result

    symbol_reason: str | None = None
    symbol_owner = "none"
    if not _strong_text_symbol(reference_symbols, reference_top_name):
        symbol_reason, symbol_owner = "reference_top_missing_or_not_strong", "reference"
    elif candidate_top_name in reference_symbols:
        symbol_reason, symbol_owner = "reference_defines_candidate_top", "reference"
    elif not _strong_text_symbol(candidate_symbols, candidate_top_name):
        symbol_reason, symbol_owner = "candidate_top_missing_or_not_strong", "candidate"
    elif reference_top_name in candidate_symbols:
        symbol_reason, symbol_owner = "candidate_defines_reference_top", "candidate"

    if symbol_reason is not None:
        result = LegacyHarnessResult(
            status="symbol_contract_failed",
            reason_code=symbol_reason,
            failure_owner=symbol_owner,
            return_code=1,
            compile_calls=1,
            csim_calls=0,
            tool_calls=3,
            message=symbol_reason,
            reference_symbols=reference_symbols,
            candidate_symbols=candidate_symbols,
            artifact_path=str(result_path),
        )
        _write_json_atomic(result_path, result.to_safe_dict())
        return result

    link_result = _run_logged(
        [
            "g++",
            object_paths["testbench"].name,
            object_paths["reference"].name,
            object_paths["candidate"].name,
            "-o",
            "csim",
        ],
        cwd=root,
        log_name="legacy_harness_link.log",
    )
    if link_result.returncode != 0:
        result = LegacyHarnessResult(
            status="link_failed",
            reason_code="link_failed_after_symbol_validation",
            failure_owner="unknown",
            return_code=link_result.returncode,
            compile_calls=2,
            csim_calls=0,
            tool_calls=4,
            message=link_result.stdout or "",
            reference_symbols=reference_symbols,
            candidate_symbols=candidate_symbols,
            artifact_path=str(result_path),
        )
        _write_json_atomic(result_path, result.to_safe_dict())
        return result

    run_result = _run_logged(
        [str(root / "csim")],
        cwd=root,
        log_name="legacy_harness_run.log",
    )
    if run_result.returncode != 0:
        result = LegacyHarnessResult(
            status="run_failed",
            reason_code="functional_test_failed",
            failure_owner="candidate",
            return_code=run_result.returncode,
            compile_calls=2,
            csim_calls=1,
            tool_calls=5,
            message=run_result.stdout or "",
            reference_symbols=reference_symbols,
            candidate_symbols=candidate_symbols,
            artifact_path=str(result_path),
        )
        _write_json_atomic(result_path, result.to_safe_dict())
        return result

    result = LegacyHarnessResult(
        status="passed",
        reason_code="reference_isolated_host_test_passed",
        failure_owner="none",
        return_code=0,
        compile_calls=2,
        csim_calls=1,
        tool_calls=5,
        message=None,
        reference_symbols=reference_symbols,
        candidate_symbols=candidate_symbols,
        artifact_path=str(result_path),
    )
    _write_json_atomic(result_path, result.to_safe_dict())
    return result
