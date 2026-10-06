"""Coverage measurement for testbench iteration.

Lifts the compile+csim+gcov pipeline from scripts/coverage/run_coverage.py and
packages it as a function callable from the TB optimizer.

Given (orig_code, testbench_code, stub_code, kernel_name), writes the three
files to a temp dir, compiles with `g++ --coverage -O0`, runs `./csim_cov`,
then runs `gcov` to extract per-line hit info for orig_code.cpp.

Returns a dict with at minimum: status, cov_pct, lines_total, lines_hit,
uncovered_lines, run_returncode. status is one of:
    ok | compile_failed | compile_timeout | run_failed | run_timeout |
    gcov_failed | no_gcda | missing_orig_gcov
"""

import os
import hashlib
import gzip
import json
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Optional
from pathlib import Path

from agrefactor.cpp_interface import (
    extract_top_interface, inspect_top_entry, interfaces_equivalent,
)

from agrefactor.reference_source import isolate_reference_program_entry
from agrefactor.evaluation.owner_evidence import OwnerEvidence, resolve_source_owner
from agrefactor.evaluation.diagnostic_catalog import load_catalog
from agrefactor.evaluation.testbench_preflight import (
    classify_compile_failure,
    parse_compiler_diagnostics,
)

SOURCES = ["testbench.cpp", "orig_code.cpp", "refactor_code.cpp"]

# Match coverage script: drop -D__SYNTHESIS__ (HLS headers trip up gcc).
# Match coverage script timeouts (180s/180s) and gcov 60s.
COMPILE_BASE = [
    "g++",
    "-O0",
    "-g",
    "--coverage",
    "-Wno-unknown-pragmas",
    *SOURCES,
    "-o", "csim_cov",
]
XILINX_INCLUDE_FALLBACK = "/mnt/software/xilinx/Vitis/2019.2/include"

COMPILE_TIMEOUT = 180
RUN_TIMEOUT = 180
GCOV_TIMEOUT = 60

ORIGINAL_ONLY_COMPILE_BASE = [
    "g++",
    "-O0",
    "-g",
    "-fsanitize=address,undefined",
    "-fno-omit-frame-pointer",
    "-Wno-unknown-pragmas",
    "testbench.cpp",
    "orig_code.cpp",
    "original_only_candidate.cpp",
    "-o", "original_only",
]


def _is_function_template(
    source: str,
    function_name: str,
    *,
    source_root: str | None = None,
    compile_flags: tuple[str, ...] = (),
) -> bool:
    """Return compiler evidence that the Original top is a function template."""
    facts = inspect_top_entry(
        source,
        function_name,
        source_path=str(Path(source_root) / "orig_code.cpp") if source_root else None,
        include_dirs=(source_root,) if source_root else (),
        compile_flags=compile_flags,
    )
    return (
        facts.get("status") in {"confirmed", "ambiguous"}
        and any(item.get("kind") == "template" for item in facts.get("entries", ()))
    )


def _candidate_definition(stub_code: str) -> str:
    """Remove the harness include from a stub before combined-TU assembly."""
    return re.sub(r"(?m)^\s*#include\s+\"testbench\.cpp\"\s*\n?", "", stub_code, count=1)


def _noop_candidate_definition(declaration: str, body: str, function_name: str) -> str:
    # Keep the included Testbench unchanged. GNU attributes after parameters
    # are valid on declarations but not definitions in GCC. Move those groups
    # before the function name, retaining their function/type semantics and
    # leaving parameter and return-prefix attributes in their original place.
    tokens = [token for token in re.finditer(
        r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|[()]',
        declaration, re.DOTALL,
    ) if not token.group().startswith(("//", "/*"))]
    depth = 0
    spans = []
    function_token = None
    parameter_depth = None
    parameters_closed = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        value = token.group()
        if value == function_name and index + 1 < len(tokens) and tokens[index + 1].group() == "(":
            function_token = token
            parameter_depth = depth
        if value in {"__attribute__", "__attribute"} and depth == 0 and parameters_closed:
            end_index = index + 1
            if end_index < len(tokens) and tokens[end_index].group() == "(":
                nested = 0
                while end_index < len(tokens):
                    part = tokens[end_index].group()
                    nested += int(part == "(") - int(part == ")")
                    if nested == 0:
                        spans.append((token.start(), tokens[end_index].end()))
                        index = end_index
                        break
                    end_index += 1
        else:
            depth += int(value == "(") - int(value == ")")
            if value == ")" and parameter_depth is not None and depth == parameter_depth:
                parameters_closed = True
        index += 1
    attributes = " ".join(declaration[start:end] for start, end in spans)
    for start, end in reversed(spans):
        declaration = declaration[:start] + declaration[end:]
    if attributes and function_token is not None:
        position = function_token.start()
        declaration = declaration[:position] + attributes + " " + declaration[position:]
    return declaration.rstrip(";").strip() + " {" + body + "}\n"

_LINES_RE = re.compile(r"Lines executed:\s*([\d.]+)%\s+of\s+(\d+)")


def _consume_tool_launch(
    budget: Any,
    *,
    compile_calls: int = 0,
    csim_calls: int = 0,
) -> None:
    if budget is None:
        return
    budget.consume(
        tool_calls=1,
        compile_calls=compile_calls,
        csim_calls=csim_calls,
    )


def _parse_gcov_n_stdout(stdout: str) -> dict[str, tuple[int, int]]:
    """Parse `gcov -n` stdout while preserving reported source identities."""
    out: dict[str, tuple[int, int]] = {}
    current: Optional[str] = None
    for line in stdout.splitlines():
        line = line.strip()
        m_file = re.match(r"File '([^']+)'", line)
        if m_file:
            current = m_file.group(1)
            continue
        m = _LINES_RE.search(line)
        if m and current is not None:
            pct = float(m.group(1))
            total = int(m.group(2))
            hit = round(pct / 100.0 * total)
            out[current] = (hit, total)
            current = None
    return out


def _parse_gcov_file(path: str) -> dict[int, int]:
    """Parse a .gcov file into {line_number: hit_count}."""
    hits: dict[int, int] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            parts = raw.split(":", 2)
            if len(parts) < 3:
                continue
            count_s = parts[0].strip()
            lineno_s = parts[1].strip()
            if not lineno_s.isdigit():
                continue
            lineno = int(lineno_s)
            if lineno == 0:
                continue
            if count_s == "-":
                continue
            if count_s in ("#####", "====="):
                count = 0
            else:
                count_clean = count_s.rstrip("*").replace(",", "")
                try:
                    count = int(count_clean)
                except ValueError:
                    continue
            hits[lineno] = count
    return hits


_COMPILE_OWNER_ACTION = {
    "configuration": "review_configuration",
    "testbench": "repair_testbench",
    "stub": "regenerate_stub",
    "original": "review_original",
    "abi": "repair_abi_testbench_stub",
    "toolchain": "review_toolchain",
    "unknown": "review_unknown",
}


def _classify_compile_failure_owner(
    stderr: str,
    source_roles: Mapping[str, str] | None = None,
    work_dir: str | None = None,
    execution: Mapping[str, Any] | None = None,
) -> str:
    """Classify a real compiler/linker failure from emitted diagnostics."""

    return _compile_failure_evidence(
        stderr, source_roles or {}, work_dir, execution
    ).owner


def _compile_failure_evidence(
    stderr: str,
    source_roles: Mapping[str, str],
    work_dir: str | None,
    execution: Mapping[str, Any] | None,
    source_spans: Mapping[str, tuple[dict, ...] | list[dict]] | None = None,
    compile_units: tuple[str, ...] | None = None,
) -> OwnerEvidence:
    if work_dir is None:
        return OwnerEvidence("unknown", "insufficient_provenance", False)
    text = str(stderr or "")
    if load_catalog().match(text, stage="link") is not None:
        return OwnerEvidence(
            "unknown", "unresolved_link_ownership", False, (),
            "link diagnostics do not identify the definition or ABI owner",
        )
    kind = classify_compile_failure(text)
    diagnostics = parse_compiler_diagnostics(text, default_kind=kind)
    evidence = resolve_source_owner(
        diagnostics,
        source_roles=source_roles,
        work_dir=work_dir,
        execution=execution,
        source_spans=source_spans,
        compile_units=compile_units,
    )
    return evidence


def _record_execution(
    result: dict,
    *,
    component: str,
    command: list[str],
    work_dir: str,
    status: str,
    returncode: int | None = None,
    timeout: bool = False,
    stdout: str | None = None,
    stderr: str | None = None,
) -> dict:
    record = {
        "component": component,
        "command": list(command),
        "work_dir": str(Path(work_dir).resolve()),
        "status": status,
        "returncode": returncode,
        "timeout": timeout,
    }
    for stream, contents in (("stdout", stdout), ("stderr", stderr)):
        if contents is not None:
            path = Path(work_dir) / f"execution_{len(result.get('executions', [])):03d}.{stream}.log"
            path.write_text(contents, encoding="utf-8")
            record[f"{stream}_path"] = str(path.resolve())
            record[f"{stream}_sha256"] = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    result.setdefault("executions", []).append(record)
    return record


def _entry_contract_failure(
    source: str,
    entry_name: str | None,
    *,
    source_root: str | None,
    compile_flags: tuple[str, ...],
) -> tuple[str, dict] | None:
    """Return a generic preflight failure for an unusable Original entry."""

    if not entry_name:
        return None
    facts = inspect_top_entry(
        source,
        entry_name,
        source_path=(
            os.path.join(source_root, "orig_code.cpp")
            if source_root else None
        ),
        include_dirs=(source_root,) if source_root else (),
        compile_flags=compile_flags,
    )
    entries = facts.get("entries", ())
    if any(entry.get("linkage") == 2 for entry in entries):
        return "internal_linkage_entry", facts
    diagnostics = facts.get("diagnostics", ())
    if any(
        (
            (match := load_catalog().match(
                str(item.get("message", "")), stage="compile"
            )) is not None
            and match.category_id == "missing_dependency"
        )
        for item in diagnostics
        if isinstance(item, Mapping)
    ):
        return "missing_external_dependency", facts
    return None


def _finish_failure(
    result: dict,
    *,
    status: str,
    owner: str,
    action: str | None = None,
    evidence_source: str,
) -> dict:
    result["status"] = status
    result["failure_owner"] = owner
    result["next_action"] = (
        action
        if action is not None
        else _COMPILE_OWNER_ACTION.get(owner, "review_unknown")
    )
    result["failure_evidence_source"] = evidence_source
    result.setdefault("owner_authority", (
        "explicit_tool_failure" if owner == "toolchain"
        else "unknown" if owner == "unknown" else "source_compile_unit"
    ))
    result.setdefault("evidence_complete", owner not in {"unknown", "none"})
    return result


def _original_only_candidate_stub(candidate_decl: str, candidate_name: str) -> str:
    """Define a harmless Candidate body from compiler-resolved ABI facts."""
    declaration = str(candidate_decl or "").strip().rstrip(";").strip()
    if not declaration:
        raise ValueError("candidate declaration is required")
    interface = extract_top_interface(declaration + ";", candidate_name, require_definition=False)
    body = "" if interface is not None and interface.canonical_result_type == "void" else "\n    return {};\n"
    return declaration + " {" + body + "}\n"


def _original_interface_evidence(
    orig_code: str,
    tb_code: str,
    original_name: str | None,
    *,
    source_root: str | None,
    work_dir: str,
    compile_flags: tuple[str, ...],
    include_dirs: tuple[str, ...],
) -> dict:
    if not original_name:
        return {"status": "unknown"}
    resolved = []
    for source, filename, definition, role in (
        (orig_code, "orig_code.cpp", True, "original"),
        (tb_code, "testbench.cpp", False, "testbench"),
    ):
        interface = extract_top_interface(
            source,
            original_name,
            require_definition=definition,
            source_path=os.path.join(source_root or work_dir, filename),
            include_dirs=include_dirs,
            compile_flags=compile_flags,
        )
        if (
            interface is None
            or not interface.canonical_function_type
            or not interface.linker_symbol
        ):
            return {"status": "unknown"}
        resolved.append((interface, {
            "source_role": role,
            "source_file": filename,
            "line": source.encode("utf-8")[:interface.source_start].count(b"\n") + 1,
            "declaration": interface.source_declaration,
            "canonical_function_type": interface.canonical_function_type,
            "linker_symbol": interface.linker_symbol,
            "language_linkage": interface.language_linkage,
        }))
    (expected, expected_facts), (observed, observed_facts) = resolved
    equivalent = interfaces_equivalent(
        expected, observed, require_parameter_names=False, semantic_types=True,
    )
    differences = [
        field for field in ("canonical_function_type", "linker_symbol")
        if expected_facts[field] != observed_facts[field]
    ]
    return {
        "status": "equivalent" if equivalent else "mismatch",
        "expected": expected_facts,
        "observed": observed_facts,
        "differences": differences,
    }


def check_original_execution(
    orig_code: str,
    tb_code: str,
    candidate_decl: str,
    candidate_name: str,
    *,
    budget: Any = None,
    keep_dir: Optional[str] = None,
    source_root: Optional[str] = None,
    extra_sources: tuple[str, ...] = (),
    compile_flags: tuple[str, ...] = (),
    required_original_entry: str | None = None,
    original_name: str | None = None,
) -> dict:
    """Run the generated Testbench with a no-op Candidate and sanitizers.

    A non-zero return caused only by the Testbench comparing against the
    no-op is recorded as an observation. Runtime failures still involve
    Testbench and Stub code and do not alone prove an Original defect.
    """
    result = {
        "status": "",
        "original_only": True,
        "run_returncode": None,
        "compile_stderr": "",
        "run_stderr": "",
        "run_stdout": "",
        "failure_owner": "none",
        "next_action": "continue_validation",
        "failure_evidence_source": "none",
        "owner_authority": "unknown",
        "evidence_complete": False,
        "executions": [],
        "source_identity": {
            "original_sha256": hashlib.sha256(orig_code.encode("utf-8")).hexdigest(),
            "testbench_sha256": hashlib.sha256(tb_code.encode("utf-8")).hexdigest(),
        },
    }
    if keep_dir is not None:
        os.makedirs(keep_dir, exist_ok=True)
        ctx = _NullCtx(keep_dir)
    else:
        ctx = tempfile.TemporaryDirectory(prefix="tb_orig_only_")

    with ctx as tmp_s:
        tmp = tmp_s if isinstance(tmp_s, str) else tmp_s
        interface = extract_top_interface(
            tb_code + ("\n" + candidate_decl if candidate_decl else ""), candidate_name, require_definition=False,
            source_path=os.path.join(source_root or tmp, "testbench.cpp"),
            include_dirs=(source_root,) if source_root else (),
            compile_flags=compile_flags,
        )
        declaration = interface.source_declaration if interface is not None else candidate_decl
        result["interface_status"] = "resolved" if interface is not None else "unknown"
        body = "" if interface is not None and interface.canonical_result_type == "void" else "return {};"
        original_name = original_name or (
            candidate_name[:-4] if candidate_name.endswith("_hls") else None
        )
        entry_failure = _entry_contract_failure(
            orig_code,
            original_name,
            source_root=source_root,
            compile_flags=compile_flags,
        )
        if entry_failure is not None:
            reason, entry_facts = entry_failure
            result.update(
                status="entry_contract_failed",
                failure_owner="configuration",
                next_action="review_configuration",
                diagnostic_kind=reason,
                source_entry=entry_facts,
                failure_evidence_source="libclang source entry contract",
                owner_authority="source_entry_contract",
                evidence_complete=True,
            )
            return result
        template_original = bool(
            original_name
            and _is_function_template(
                orig_code,
                original_name,
                source_root=source_root,
                compile_flags=compile_flags,
            )
        )
        stub = '#include "testbench.cpp"\n'
        if declaration:
            stub += _noop_candidate_definition(declaration, body, candidate_name)
        reference_spans: list[dict] = []
        if template_original:
            # Function templates must be instantiated where their definition
            # is visible. Keep the Original and Testbench in one TU and omit
            # the separate Original object from the compile command below.
            reference = isolate_reference_program_entry(
                orig_code,
                top_function=original_name,
                testbench_code=tb_code,
                source_path=os.path.join(source_root or tmp, "orig_code.cpp"),
                include_dirs=(source_root,) if source_root else (),
                compile_flags=compile_flags,
                source_provenance=reference_spans,
            )
            stub = (
                '#include "orig_code.cpp"\n'
                '#include "testbench.cpp"\n'
                + _candidate_definition(stub)
            )
        else:
            reference = isolate_reference_program_entry(
                orig_code, source_provenance=reference_spans,
            )
        files = {
            "orig_code.cpp": reference,
            "testbench.cpp": tb_code,
            "original_only_candidate.cpp": stub,
        }
        for name, content in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as file:
                file.write(content)
        result["source_roles"] = {
            "testbench.cpp": "testbench",
            # The template bridge is generated into the reference file, so
            # file-level evidence cannot distinguish it from Original code.
            "orig_code.cpp": "unknown" if template_original else "original",
            "original_only_candidate.cpp": "stub",
            **{name: "configuration" for name in extra_sources},
        }
        result["source_spans"] = {"orig_code.cpp": reference_spans}

        compiled = False
        for include_flag in (None, f"-I{XILINX_INCLUDE_FALLBACK}"):
            cmd = list(ORIGINAL_ONLY_COMPILE_BASE)
            if required_original_entry is not None:
                cmd.insert(1, "--coverage")
            if template_original:
                cmd.remove("testbench.cpp")
                cmd.remove("orig_code.cpp")
            else:
                cmd.remove("testbench.cpp")
            cmd[1:1] = [*compile_flags, *([f"-I{source_root}"] if source_root else [])]
            cmd.extend(extra_sources)
            if include_flag is not None:
                cmd.insert(1, include_flag)
            result["compile_units"] = {
                "original_only_candidate.cpp": [
                    "original_only_candidate.cpp", "testbench.cpp",
                    *(["orig_code.cpp"] if template_original else []),
                ],
                **({} if template_original else {"orig_code.cpp": ["orig_code.cpp"]}),
                **{name: [name] for name in extra_sources},
            }
            try:
                _consume_tool_launch(budget, compile_calls=1)
                completed = subprocess.run(
                    cmd,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=COMPILE_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                _record_execution(result, component="compile", command=cmd,
                                  work_dir=tmp, status="timeout", timeout=True)
                result.update(
                    status="compile_timeout",
                    failure_owner="unknown",
                    next_action="review_unknown",
                    failure_evidence_source="original-only g++ timeout",
                )
                return result
            except FileNotFoundError as exc:
                _record_execution(result, component="compile", command=cmd,
                                  work_dir=tmp, status="launch_error")
                result.update(
                    status="compile_failed",
                    failure_owner="toolchain",
                    next_action="review_toolchain",
                    compile_stderr=str(exc)[-4000:],
                    failure_evidence_source="original-only compiler launch error",
                )
                return result
            execution = _record_execution(
                result, component="compile", command=cmd, work_dir=tmp,
                status="completed", returncode=completed.returncode,
                stdout=getattr(completed, "stdout", "") or "", stderr=getattr(completed, "stderr", "") or "",
            )
            if completed.returncode == 0:
                compiled = True
                break
            result["compile_stderr"] = completed.stderr

        # Interface facts supplement the real execution; an unresolved AST
        # never prevents compilation or grants a repair opportunity.
        interface_evidence = _original_interface_evidence(
            orig_code, tb_code, original_name,
            source_root=source_root,
            work_dir=tmp,
            compile_flags=compile_flags,
            include_dirs=(
                *((source_root,) if source_root else ()),
                *((XILINX_INCLUDE_FALLBACK,) if include_flag else ()),
            ),
        )
        result["original_interface_evidence"] = interface_evidence
        if not compiled:
            evidence = _compile_failure_evidence(
                result["compile_stderr"],
                result["source_roles"],
                tmp,
                execution,
                result["source_spans"],
                tuple({member for members in result["compile_units"].values() for member in members}),
            )
            match = load_catalog().match(result["compile_stderr"], stage="link")
            if (
                match is not None
                and completed.returncode > 0
                and interface_evidence["status"] == "mismatch"
            ):
                evidence = OwnerEvidence(
                    "testbench", "original_interface_differential", True,
                    (os.path.join(tmp, "testbench.cpp"),),
                    "generated Original declaration differs from its definition; "
                    "repair this proven interface defect and requalify the link",
                )
            owner = evidence.owner
            result["ownership_evidence"] = evidence.to_dict()
            result["owner_authority"] = evidence.owner_authority
            result["evidence_complete"] = evidence.evidence_complete
            if match is None:
                match = load_catalog().match(result["compile_stderr"], stage="compile")
            if match is not None:
                result["diagnostic_classification"] = match.to_metadata()
            result.update(
                status="compile_failed",
                failure_owner=owner,
                next_action=(
                    "repair_testbench"
                    if owner in {"testbench", "abi"}
                    else "review_unknown"
                    if owner == "unknown"
                    else "regenerate_stub"
                    if owner == "stub"
                    else "review_toolchain"
                    if owner == "toolchain"
                    else "review_original"
                ),
                failure_evidence_source="original-only g++ diagnostics",
                owner_authority=evidence.owner_authority,
                evidence_complete=evidence.evidence_complete,
            )
            return result

        if interface_evidence["status"] == "mismatch":
            # C linkage and return-type mismatches can link successfully.
            # Compilation alone cannot qualify an incompatible declaration.
            evidence = OwnerEvidence(
                "testbench", "original_interface_differential", True,
                (os.path.join(tmp, "testbench.cpp"),),
                "compiled Testbench declares an incompatible Original interface",
            )
            result.update(
                ownership_evidence=evidence.to_dict(),
                owner_authority=evidence.owner_authority,
                evidence_complete=evidence.evidence_complete,
                compile_stderr=evidence.reason,
            )
            return _finish_failure(
                result, status="contract_failed", owner="testbench",
                evidence_source="compiled Original interface differential",
            )

        try:
            _consume_tool_launch(budget, csim_calls=1)
            env = dict(os.environ)
            env["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=1"
            env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            completed = subprocess.run(
                ["./original_only"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            _record_execution(result, component="runtime", command=["./original_only"],
                              work_dir=tmp, status="timeout", timeout=True)
            result.update(
                status="original_run_timeout",
                run_stderr=str(exc),
                failure_owner="unknown",
                next_action="review_unknown",
                failure_evidence_source="original-only timeout",
            )
            return result
        _record_execution(
            result, component="runtime", command=["./original_only"], work_dir=tmp,
            status="completed", returncode=completed.returncode,
            stdout=getattr(completed, "stdout", "") or "", stderr=getattr(completed, "stderr", "") or "",
        )

        stderr = (completed.stderr or "")[-3000:]
        stdout = (completed.stdout or "")[-1000:]
        result["run_returncode"] = completed.returncode
        result["run_stdout"] = stdout
        result["run_stderr"] = (
            stderr + (chr(10) + "[stdout]" + chr(10) + stdout if stdout else "")
        )[-4000:]
        sanitizer_markers = (
            "AddressSanitizer",
            "UndefinedBehaviorSanitizer",
            "runtime error:",
            "heap-buffer-overflow",
            "stack-buffer-overflow",
            "use-after-free",
            "SEGV",
        )
        crashed = (
            completed.returncode < 0
            or any(marker in (completed.stderr or "") for marker in sanitizer_markers)
        )
        if crashed:
            result.update(
                status="original_run_failed",
                failure_owner="unknown",
                next_action="review_unknown",
                failure_evidence_source="original-only sanitizer/runtime",
                owner_authority="runtime_not_isolated",
                evidence_complete=False,
            )
            return result

        if required_original_entry is not None:
            result["original_entry_executed"] = False
            result["original_entry_observed"] = False
            expected_names = {
                required_original_entry,
                f"agrefactor_reference_impl_{required_original_entry}",
            }
            gcov_inputs = list(Path(tmp).glob("*-orig_code.gcda"))
            if template_original:
                gcov_inputs.extend(Path(tmp).glob("*-original_only_candidate.gcda"))
            for path in gcov_inputs:
                _consume_tool_launch(budget)
                command = ["gcov", "--json-format", str(path)]
                try:
                    coverage = subprocess.run(
                        command, cwd=tmp, capture_output=True,
                        text=True, timeout=GCOV_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    _record_execution(result, component="gcov", command=command,
                                      work_dir=tmp, status="timeout", timeout=True)
                    result.update(status="original_execution_unknown", failure_owner="unknown",
                                  next_action="review_unknown", failure_evidence_source="gcov timeout")
                    return result
                except FileNotFoundError as exc:
                    _record_execution(result, component="gcov", command=command,
                                      work_dir=tmp, status="launch_error")
                    result.update(status="gcov_failed", failure_owner="toolchain",
                                  next_action="review_toolchain", run_stderr=str(exc),
                                  failure_evidence_source="gcov launch error")
                    return result
                _record_execution(result, component="gcov", command=command, work_dir=tmp,
                                  status="completed", returncode=coverage.returncode,
                                  stdout=getattr(coverage, "stdout", "") or "", stderr=getattr(coverage, "stderr", "") or "")
                if coverage.returncode != 0:
                    continue
                for report in Path(tmp).glob("*.gcov.json.gz"):
                    with gzip.open(report, "rt", encoding="utf-8") as handle:
                        facts = json.load(handle)
                    for unit in facts.get("files", []):
                        unit_path = Path(str(unit.get("file", "")))
                        if not unit_path.is_absolute():
                            unit_path = Path(tmp) / unit_path
                        if unit_path.resolve() != (Path(tmp) / "orig_code.cpp").resolve():
                            continue
                        for function in unit.get("functions", []):
                            name = str(function.get("demangled_name", "")).partition("(")[0]
                            name = name.rsplit("::", 1)[-1]
                            name = name.rsplit("<", 1)[0].rsplit(" ", 1)[-1]
                            if name in expected_names:
                                result["original_entry_observed"] = True
                                if function.get("execution_count", 0) > 0:
                                    result["original_entry_executed"] = True
            if not result["original_entry_executed"]:
                observed = result["original_entry_observed"]
                result.update(
                    status="qualification_failed" if observed else "original_execution_unknown",
                    failure_owner="testbench" if observed else "unknown",
                    next_action="repair_testbench" if observed else "review_unknown",
                    failure_evidence_source="gcov function execution evidence",
                    owner_authority="original_entry_not_executed" if observed else "unknown",
                    evidence_complete=observed,
                )
                return result

        result.update(
            status="ok",
            failure_owner="none",
            next_action="continue_validation",
            failure_evidence_source="original-only runtime completed",
            owner_authority="original_only_completed",
            evidence_complete=True,
        )
        return result


def _classify_run_failure_owner(stderr: str) -> tuple[str, str | None]:
    """Keep runtime ownership unknown without structured evidence."""
    return "unknown", "review_unknown"


def measure_coverage(
    orig_code: str,
    tb_code: str,
    stub_code: str,
    target_source: str = "orig_code.cpp",
    keep_dir: Optional[str] = None,
    budget: Any = None,
    source_root: Optional[str] = None,
    extra_sources: tuple[str, ...] = (),
    compile_flags: tuple[str, ...] = (),
    original_name: str | None = None,
) -> dict:
    """Compile, run, and measure coverage with tool-backed ownership evidence."""

    res = {
        "status": "",
        "cov_pct": None,
        "lines_total": None,
        "lines_hit": None,
        "uncovered_lines": [],
        "run_returncode": None,
        "compile_stderr": "",
        "compile_stdout": "",
        "run_stderr": "",
        "failure_owner": "none",
        "next_action": "continue_validation",
        "failure_evidence_source": "none",
        "owner_authority": "unknown",
        "evidence_complete": False,
        "executions": [],
        "source_identity": {
            "original_sha256": hashlib.sha256(orig_code.encode("utf-8")).hexdigest(),
            "testbench_sha256": hashlib.sha256(tb_code.encode("utf-8")).hexdigest(),
            "stub_sha256": hashlib.sha256(stub_code.encode("utf-8")).hexdigest(),
        },
    }

    if keep_dir is not None:
        os.makedirs(keep_dir, exist_ok=True)
        ctx = _NullCtx(keep_dir)
    else:
        ctx = tempfile.TemporaryDirectory(prefix="tbcov_")

    with ctx as tmp_s:
        tmp = tmp_s if isinstance(tmp_s, str) else tmp_s
        template_original = bool(
            original_name
            and _is_function_template(
                orig_code,
                original_name,
                source_root=source_root,
                compile_flags=compile_flags,
            )
        )
        refactor_contents = stub_code
        compile_sources = list(SOURCES)
        reference_spans: list[dict] = []
        if template_original:
            # The template definition and its instantiating testbench must
            # share a translation unit. The candidate stub remains the final
            # definition in that unit.
            original_contents = isolate_reference_program_entry(
                orig_code,
                top_function=original_name,
                testbench_code=tb_code,
                source_path=os.path.join(source_root or tmp, "orig_code.cpp"),
                include_dirs=(source_root,) if source_root else (),
                compile_flags=compile_flags,
                source_provenance=reference_spans,
            )
            refactor_contents = (
                '#include "orig_code.cpp"\n'
                '#include "testbench.cpp"\n'
                + _candidate_definition(stub_code)
            )
            compile_sources = ["refactor_code.cpp"]
        else:
            original_contents = orig_code
        contents = {
            "orig_code.cpp": original_contents,
            "testbench.cpp": tb_code,
            "refactor_code.cpp": refactor_contents,
        }
        for name, content in contents.items():
            with open(
                os.path.join(tmp, name),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(content)
        res["source_roles"] = {
            "orig_code.cpp": "unknown" if template_original else "original",
            "testbench.cpp": "testbench",
            "refactor_code.cpp": "stub",
            **{name: "configuration" for name in extra_sources},
        }
        res["source_spans"] = {"orig_code.cpp": reference_spans} if template_original else {}
        res["compile_units"] = {
            name: (
                ["refactor_code.cpp", "orig_code.cpp", "testbench.cpp"]
                if template_original and name == "refactor_code.cpp"
                else [name]
            )
            for name in (*compile_sources, *extra_sources)
        }

        compiled = False
        for include_flag in (
            None,
            f"-I{XILINX_INCLUDE_FALLBACK}",
        ):
            cmd = [
                "g++",
                "-O0",
                "-g",
                "--coverage",
                "-Wno-unknown-pragmas",
                *compile_sources,
                "-o",
                "csim_cov",
            ]
            cmd[1:1] = [*compile_flags, *([f"-I{source_root}"] if source_root else [])]
            cmd.extend(extra_sources)
            if include_flag is not None:
                cmd.insert(1, include_flag)
            try:
                _consume_tool_launch(
                    budget,
                    compile_calls=1,
                )
                completed = subprocess.run(
                    cmd,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=COMPILE_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                _record_execution(res, component="compile", command=cmd,
                                  work_dir=tmp, status="timeout", timeout=True)
                return _finish_failure(
                    res,
                    status="compile_timeout",
                    owner="unknown",
                    action="review_unknown",
                    evidence_source="g++ timeout",
                )
            except FileNotFoundError as exc:
                _record_execution(res, component="compile", command=cmd,
                                  work_dir=tmp, status="launch_error")
                res["compile_stderr"] = str(exc)[-2000:]
                return _finish_failure(
                    res,
                    status="compile_failed",
                    owner="toolchain",
                    action="review_toolchain",
                    evidence_source="g++ launch error",
                )
            execution = _record_execution(
                res, component="compile", command=cmd, work_dir=tmp,
                status="completed", returncode=completed.returncode,
                stdout=getattr(completed, "stdout", "") or "", stderr=getattr(completed, "stderr", "") or "",
            )
            if completed.returncode == 0:
                compiled = True
                res["compile_stderr"] = ""
                break
            res["compile_stderr"] = str(getattr(completed, "stderr", ""))
            res["compile_stdout"] = str(getattr(completed, "stdout", ""))

        if not compiled:
            evidence = _compile_failure_evidence(
                res["compile_stderr"],
                res["source_roles"],
                tmp,
                execution,
                res["source_spans"],
                tuple({member for members in res["compile_units"].values() for member in members}),
            )
            owner = evidence.owner
            res["ownership_evidence"] = evidence.to_dict()
            res["owner_authority"] = evidence.owner_authority
            res["evidence_complete"] = evidence.evidence_complete
            match = load_catalog().match(res["compile_stderr"], stage="link")
            if match is None:
                match = load_catalog().match(res["compile_stderr"], stage="compile")
            if match is not None:
                res["diagnostic_classification"] = match.to_metadata()
            return _finish_failure(
                res,
                status="compile_failed",
                owner=owner,
                evidence_source="g++ diagnostics",
            )

        try:
            _consume_tool_launch(
                budget,
                csim_calls=1,
            )
            completed = subprocess.run(
                ["./csim_cov"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
            )
            res["run_returncode"] = completed.returncode
            _record_execution(
                res, component="runtime", command=["./csim_cov"], work_dir=tmp,
                status="completed", returncode=completed.returncode,
                stdout=getattr(completed, "stdout", "") or "", stderr=getattr(completed, "stderr", "") or "",
            )
            res["run_stderr"] = completed.stderr[-2000:]
            res["run_stdout"] = completed.stdout[-2000:]
            if completed.returncode != 0:
                owner, action = _classify_run_failure_owner(
                    completed.stderr + "\n" + completed.stdout
                )
                return _finish_failure(
                    res,
                    status="run_failed",
                    owner=owner,
                    action=action,
                    evidence_source="program return code",
                )
        except subprocess.TimeoutExpired:
            _record_execution(res, component="runtime", command=["./csim_cov"],
                              work_dir=tmp, status="timeout", timeout=True)
            return _finish_failure(
                res,
                status="run_timeout",
                owner="unknown",
                action="review_unknown",
                evidence_source="program timeout",
            )

        gcda_files = sorted(
            name
            for name in os.listdir(tmp)
            if name.endswith(".gcda")
        )
        if not gcda_files:
            return _finish_failure(
                res,
                status="no_gcda",
                owner="unknown",
                action="review_unknown",
                evidence_source="gcov artifact discovery",
            )

        stem = target_source.replace(".cpp", "")
        target_gcda = next(
            (
                item
                for item in gcda_files
                if item.endswith(f"-{stem}.gcda")
                or item == f"{stem}.gcda"
            ),
            None,
        )
        if target_gcda is None and template_original:
            # Combined template translation units record the original source
            # under the TU's gcda filename; gcov still reports orig_code.cpp.
            target_gcda = next(
                (item for item in gcda_files if item.endswith("-refactor_code.gcda") or item == "refactor_code.gcda"),
                None,
            )
        if target_gcda is None:
            return _finish_failure(
                res,
                status="no_gcda",
                owner="unknown",
                action="review_unknown",
                evidence_source="gcov target discovery",
            )

        try:
            _consume_tool_launch(budget)
            summary = subprocess.run(
                ["gcov", "-n", target_gcda],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=GCOV_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            _record_execution(res, component="gcov", command=["gcov", "-n", target_gcda],
                              work_dir=tmp, status="timeout", timeout=True)
            return _finish_failure(
                res,
                status="gcov_failed",
                owner="unknown",
                action="review_unknown",
                evidence_source="gcov timeout",
            )
        except FileNotFoundError as exc:
            _record_execution(res, component="gcov", command=["gcov", "-n", target_gcda],
                              work_dir=tmp, status="launch_error")
            res["run_stderr"] = str(exc)[-2000:]
            return _finish_failure(
                res,
                status="gcov_failed",
                owner="toolchain",
                action="review_toolchain",
                evidence_source="gcov launch error",
            )

        _record_execution(
            res, component="gcov", command=["gcov", "-n", target_gcda],
            work_dir=tmp, status="completed", returncode=summary.returncode,
        )
        if summary.returncode != 0:
            res["run_stderr"] = summary.stderr[-2000:]
            return _finish_failure(
                res,
                status="gcov_failed",
                owner="unknown",
                action="review_unknown",
                evidence_source="gcov diagnostics",
            )

        summaries = _parse_gcov_n_stdout(summary.stdout)
        expected_source = (Path(tmp) / target_source).resolve()
        target_summaries = [
            counts for source, counts in summaries.items()
            if (
                Path(source).resolve()
                if Path(source).is_absolute()
                else (Path(tmp) / source).resolve()
            ) == expected_source
        ]
        if len(target_summaries) != 1:
            return _finish_failure(
                res,
                status="missing_orig_gcov",
                owner="unknown",
                action="review_unknown",
                evidence_source="gcov summary",
            )

        hit, total = target_summaries[0]
        res["lines_hit"] = hit
        res["lines_total"] = total
        res["cov_pct"] = (
            100.0 * hit / total
            if total
            else 0.0
        )

        try:
            _consume_tool_launch(budget)
            subprocess.run(
                ["gcov", target_gcda],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=GCOV_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pass

        gcov_path = os.path.join(
            tmp,
            f"{target_source}.gcov",
        )
        if os.path.isfile(gcov_path):
            line_hits = _parse_gcov_file(gcov_path)
            res["uncovered_lines"] = sorted(
                line_number
                for line_number, count in line_hits.items()
                if count == 0
            )

        res["status"] = "ok"
        res["failure_owner"] = "none"
        res["next_action"] = "continue_validation"
        res["failure_evidence_source"] = "g++/runtime/gcov"
        return res


class _NullCtx:
    """Context-manager wrapper for a pre-existing dir; doesn't delete on exit."""
    def __init__(self, path: str):
        self.path = path
    def __enter__(self):
        return self.path
    def __exit__(self, *a):
        return False


def annotate_uncovered_source(orig_code: str, uncovered_lines: list[int]) -> str:
    """Return orig_code with `// UNCOVERED` markers prefixed on uncovered lines.

    Used as feedback to the LLM in the coverage loop. Line numbers are 1-based.
    """
    if not uncovered_lines:
        return orig_code
    uncovered_set = set(uncovered_lines)
    out_lines = []
    for idx, line in enumerate(orig_code.splitlines(), start=1):
        if idx in uncovered_set:
            out_lines.append(f"{line}  // UNCOVERED")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
