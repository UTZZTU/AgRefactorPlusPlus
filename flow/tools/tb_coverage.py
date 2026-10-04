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
import gzip
import json
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Optional
from pathlib import Path

from agrefactor.cpp_interface import extract_top_interface, inspect_top_entry

from agrefactor.reference_source import isolate_reference_program_entry

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
    """Parse `gcov -n` stdout into {basename: (lines_hit, lines_total)}."""
    out: dict[str, tuple[int, int]] = {}
    current: Optional[str] = None
    for line in stdout.splitlines():
        line = line.strip()
        m_file = re.match(r"File '([^']+)'", line)
        if m_file:
            current = os.path.basename(m_file.group(1))
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
) -> str:
    """Classify a real compiler/linker failure from emitted diagnostics."""

    text = str(stderr or "")
    lowered = text.lower()
    if "file not found" in lowered or "no such file or directory" in lowered:
        return "configuration"
    if "undefined reference" in lowered or "multiple definition" in lowered:
        return "abi"

    files: set[str] = set()
    roles = dict(source_roles or {})
    for line in text.splitlines():
        lowered_line = line.lower()
        if "error:" not in lowered_line and "fatal error:" not in lowered_line:
            continue
        for filename, owner in roles.items():
            if filename in line:
                files.add(owner)

    if len(files) == 1:
        return next(iter(files))
    if len(files) > 1:
        return "abi"
    if "collect2:" in lowered or "ld returned" in lowered:
        return "abi"
    return "unknown"


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
        re.search(
            r"(?:file not found|no such file or directory|cannot open source file|没有那个文件|找不到)",
            str(item.get("message", "")),
            flags=re.IGNORECASE,
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
        else _COMPILE_OWNER_ACTION.get(owner, "repair_testbench_stub")
    )
    result["failure_evidence_source"] = evidence_source
    return result


def _original_only_candidate_stub(candidate_decl: str, candidate_name: str) -> str:
    """Define a harmless Candidate body from compiler-resolved ABI facts."""
    declaration = str(candidate_decl or "").strip().rstrip(";").strip()
    if not declaration:
        raise ValueError("candidate declaration is required")
    interface = extract_top_interface(declaration + ";", candidate_name, require_definition=False)
    body = "" if interface is not None and interface.canonical_result_type == "void" else "\n    return {};\n"
    return declaration + " {" + body + "}\n"


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
    no-op is recorded as an observation; sanitizer output, a signal, or a
    timeout is a real Original-side failure.
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
            stub += declaration.rstrip(";").strip() + " {" + body + "}\n"
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
            )
            stub = (
                '#include "orig_code.cpp"\n'
                '#include "testbench.cpp"\n'
                + _candidate_definition(stub)
            )
        else:
            reference = isolate_reference_program_entry(orig_code)
        files = {
            "orig_code.cpp": reference,
            "testbench.cpp": tb_code,
            "original_only_candidate.cpp": stub,
        }
        for name, content in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as file:
                file.write(content)

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
                result.update(
                    status="compile_timeout",
                    failure_owner="toolchain",
                    next_action="repair_testbench",
                    failure_evidence_source="original-only g++ timeout",
                )
                return result
            if completed.returncode == 0:
                compiled = True
                break
            result["compile_stderr"] = completed.stderr[-4000:]

        if not compiled:
            owner = _classify_compile_failure_owner(
                result["compile_stderr"],
                {
                    "testbench.cpp": "testbench",
                    # For template tops, orig_code.cpp also contains the
                    # generated bridge and its call shape comes from the
                    # Testbench. Attribute bridge mismatches to that contract
                    # so the existing Testbench repair loop can correct it.
                    "orig_code.cpp": "testbench" if template_original else "original",
                    "original_only_candidate.cpp": "stub",
                },
            )
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
            )
            return result

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
            result.update(
                status="original_run_timeout",
                run_stderr=str(exc),
                failure_owner="original",
                next_action="repair_testbench",
                failure_evidence_source="original-only timeout",
            )
            return result

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
            or any(marker in stderr for marker in sanitizer_markers)
        )
        if crashed:
            result.update(
                status="original_run_failed",
                failure_owner="original",
                next_action="repair_testbench",
                failure_evidence_source="original-only sanitizer/runtime",
            )
            return result

        if required_original_entry is not None:
            result["original_entry_executed"] = False
            expected_names = {
                required_original_entry,
                f"agrefactor_reference_impl_{required_original_entry}",
            }
            gcov_inputs = list(Path(tmp).glob("*-orig_code.gcda"))
            if template_original:
                gcov_inputs.extend(Path(tmp).glob("*-original_only_candidate.gcda"))
            for path in gcov_inputs:
                _consume_tool_launch(budget)
                coverage = subprocess.run(
                    ["gcov", "--json-format", str(path)], cwd=tmp,
                    capture_output=True, text=True, timeout=GCOV_TIMEOUT,
                )
                if coverage.returncode != 0:
                    continue
                for report in Path(tmp).glob("*.gcov.json.gz"):
                    with gzip.open(report, "rt", encoding="utf-8") as handle:
                        facts = json.load(handle)
                    for unit in facts.get("files", []):
                        if Path(unit.get("file", "")).name != "orig_code.cpp":
                            continue
                        for function in unit.get("functions", []):
                            name = str(function.get("demangled_name", "")).partition("(")[0]
                            name = name.rsplit("::", 1)[-1]
                            name = name.rsplit("<", 1)[0].rsplit(" ", 1)[-1]
                            if name in expected_names and function.get("execution_count", 0) > 0:
                                result["original_entry_executed"] = True
            if not result["original_entry_executed"]:
                result.update(status="original_execution_unknown", failure_owner="unknown",
                              next_action="review_unknown", failure_evidence_source="gcov function execution evidence")
                return result

        result.update(
            status="ok",
            failure_owner="none",
            next_action="continue_validation",
            failure_evidence_source="original-only runtime completed",
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
                return _finish_failure(
                    res,
                    status="compile_timeout",
                    owner="toolchain",
                    evidence_source="g++ timeout",
                )
            if completed.returncode == 0:
                compiled = True
                res["compile_stderr"] = ""
                break
            res["compile_stderr"] = str(getattr(completed, "stderr", ""))[-2000:]
            res["compile_stdout"] = str(getattr(completed, "stdout", ""))[-2000:]

        if not compiled:
            owner = _classify_compile_failure_owner(
                res["compile_stderr"],
                {
                    "testbench.cpp": "testbench",
                    "orig_code.cpp": "original",
                    "refactor_code.cpp": "stub",
                },
            )
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
            return _finish_failure(
                res,
                status="run_timeout",
                owner="testbench",
                action="repair_testbench",
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
                owner="toolchain",
                action="review_toolchain",
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
                owner="toolchain",
                action="review_toolchain",
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
            return _finish_failure(
                res,
                status="gcov_failed",
                owner="toolchain",
                action="review_toolchain",
                evidence_source="gcov timeout",
            )

        if summary.returncode != 0:
            res["run_stderr"] = summary.stderr[-2000:]
            return _finish_failure(
                res,
                status="gcov_failed",
                owner="toolchain",
                action="review_toolchain",
                evidence_source="gcov diagnostics",
            )

        summaries = _parse_gcov_n_stdout(summary.stdout)
        if target_source not in summaries:
            return _finish_failure(
                res,
                status="missing_orig_gcov",
                owner="toolchain",
                action="review_toolchain",
                evidence_source="gcov summary",
            )

        hit, total = summaries[target_source]
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
