"""Hidden-TB eval gate. Compile orig + REAL refactor code + hidden TB; run; report.

Runs only after the in-flow refactor has passed csim against TB-public. Same
compile/run pipeline as tb_coverage but classifies the outcome as a final
pass/fail with a structured failure_kind, AND records orig_code coverage as a
side product (so we can report `cov_hidden` alongside `cov_public`).

failure_kind values:
    pass           - testbench returned 0; refactor is correct under hidden TB
    mismatch       - testbench returned nonzero; refactor disagrees with golden
    compile_err    - g++ compile failed (refactor code can't even build with hidden TB)
    compile_timeout - g++ compile took > COMPILE_TIMEOUT
    run_timeout    - ./csim_cov took > RUN_TIMEOUT
    no_gcda        - run completed but no coverage info; treat run rc as truth
    gcov_failed    - run completed but gcov errored; treat run rc as truth
"""

from typing import Any, Dict, Optional

from flow.tools.tb_coverage import measure_coverage


def eval_against_hidden_tb(
    orig_code: str,
    refactor_code: str,
    hidden_tb: str,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run hidden TB against the agent's refactored code.

    Args:
        orig_code: original kernel source (orig_code.cpp)
        refactor_code: REAL refactored HLS code (refactor_code.cpp) — NOT a stub
        hidden_tb: hidden testbench source (testbench.cpp)
        work_dir: optional; if set, artifacts are preserved here for debugging

    Returns dict with keys:
        passed: bool
        failure_kind: str (one of values listed in module docstring)
        cov_hidden: float | None (orig_code coverage % under hidden TB)
        lines_total, lines_hit, uncovered_lines: from gcov, may be None
        run_returncode: int | None
        compile_stderr, run_stderr: trailing chars for diagnostics
    """
    cov = measure_coverage(
        orig_code=orig_code,
        tb_code=hidden_tb,
        stub_code=refactor_code,
        target_source="orig_code.cpp",
        keep_dir=work_dir,
    )

    status = cov.get("status")
    rc = cov.get("run_returncode")

    if status == "compile_failed":
        kind = "compile_err"
        passed = False
    elif status == "compile_timeout":
        kind = "compile_timeout"
        passed = False
    elif status == "run_timeout":
        kind = "run_timeout"
        passed = False
    elif status in ("no_gcda", "gcov_failed", "missing_orig_gcov"):
        # Run finished (we have rc) but coverage data is unavailable. Treat rc as truth.
        if rc == 0:
            kind = "pass"
            passed = True
        elif rc is None:
            kind = "run_timeout"
            passed = False
        else:
            kind = "mismatch"
            passed = False
    elif status == "ok":
        if rc == 0:
            kind = "pass"
            passed = True
        else:
            kind = "mismatch"
            passed = False
    else:
        # Unknown status; be conservative and fail.
        kind = "mismatch"
        passed = False

    return {
        "passed": passed,
        "failure_kind": kind,
        "cov_hidden": cov.get("cov_pct"),
        "lines_total": cov.get("lines_total"),
        "lines_hit": cov.get("lines_hit"),
        "uncovered_lines": cov.get("uncovered_lines", []),
        "run_returncode": rc,
        "compile_stderr": cov.get("compile_stderr", "")[-1000:],
        "run_stderr": cov.get("run_stderr", "")[-1000:],
    }
