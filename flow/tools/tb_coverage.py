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
import re
import shutil
import subprocess
import tempfile
from typing import Any, Optional

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
    "testbench": "repair_testbench",
    "stub": "regenerate_stub",
    "original": "review_original",
    "abi": "repair_abi_testbench_stub",
    "toolchain": "review_toolchain",
    "unknown": "repair_testbench_stub",
}


def _classify_compile_failure_owner(stderr: str) -> str:
    """Classify a real compiler/linker failure from emitted diagnostics."""

    text = str(stderr or "")
    lowered = text.lower()
    if "undefined reference" in lowered or "multiple definition" in lowered:
        return "abi"

    files: set[str] = set()
    for line in text.splitlines():
        lowered_line = line.lower()
        if "error:" not in lowered_line and "fatal error:" not in lowered_line:
            continue
        for filename, owner in (
            ("testbench.cpp", "testbench"),
            ("refactor_code.cpp", "stub"),
            ("orig_code.cpp", "original"),
        ):
            if filename in line:
                files.add(owner)

    if len(files) == 1:
        return next(iter(files))
    if len(files) > 1:
        return "abi"
    if "collect2:" in lowered or "ld returned" in lowered:
        return "abi"
    return "unknown"


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


def measure_coverage(
    orig_code: str,
    tb_code: str,
    stub_code: str,
    target_source: str = "orig_code.cpp",
    keep_dir: Optional[str] = None,
    budget: Any = None,
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
        contents = {
            "orig_code.cpp": orig_code,
            "testbench.cpp": tb_code,
            "refactor_code.cpp": stub_code,
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
            cmd = list(COMPILE_BASE)
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
            res["compile_stderr"] = completed.stderr[-2000:]

        if not compiled:
            owner = _classify_compile_failure_owner(
                res["compile_stderr"]
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
            if completed.returncode != 0:
                return _finish_failure(
                    res,
                    status="run_failed",
                    owner="stub",
                    action="regenerate_stub",
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
