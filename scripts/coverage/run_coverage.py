#!/usr/bin/env python3
"""Measure line coverage of LLM-generated testbenches for the AgRefactor rebuttal.

For each run dir under the two paper run trees (`[paper] gpt5_mini_test_with_hybrid_rag_new`
and `..._new_2`), this script picks the latest `csim_<HHMMSS>/` subdir, rebuilds with
`g++ --coverage -O0`, runs `./csim_cov`, and computes line coverage of `orig_code.cpp`
and `refactor_code.cpp` via `gcov`.

Aggregation per kernel (20 runs = 10 from each parent):
  * per-run mean +- std for both files
  * cross-run union for orig_code.cpp only (refactor source differs per run)

Outputs under `--out` (default ./coverage_results/, or $COVERAGE_OUT):
  per_run.jsonl                          one JSON line per (kernel, parent, idx)
  raw/<kernel>/<parent>_<idx>_orig.json  per-line hit map for orig_code.cpp
  union/<kernel>_orig_union.json         union per-line hit map across runs
  summary.csv                            one row per kernel
  summary.md                             rebuttal-ready markdown table

Self-contained: no lcov dependency, only `g++` and `gcov`.

Typical usage on a compute node:
  python run_coverage.py                          # full sweep, 16 workers
  python run_coverage.py --workers 32             # more parallelism
  python run_coverage.py --kernel hetero_dfs --limit 2  # smoke test
  python run_coverage.py --dry-run                # discovery only
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import dataclasses
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


# Run dirs are not shipped with the repo (available on request). Point these at
# your own run dirs via $RUN_DIR or the --run-root CLI arg.
_RUN_BASE = os.environ.get("RUN_DIR", "runs")
DEFAULT_RUN_DIRS = [
    os.path.join(_RUN_BASE, "[paper] gpt5_mini_test_with_hybrid_rag_new"),
    os.path.join(_RUN_BASE, "[paper] gpt5_mini_test_with_hybrid_rag_new_2"),
]

SOURCES = ["testbench.cpp", "orig_code.cpp", "refactor_code.cpp"]
COVERED_SOURCES = ["orig_code.cpp", "refactor_code.cpp"]

# NB: We drop -D__SYNTHESIS__ even though the paper's csim.py compile used it.
# Reason: none of the LLM-generated files (testbench/orig/refactor) reference
# __SYNTHESIS__ themselves; it's only consulted inside Xilinx HLS headers, where
# it triggers __fp16/half type usage that gcc 9-12 cannot parse on this host.
# Dropping it is behaviorally equivalent for coverage and lets HLS-typed
# testbenches compile.
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


@dataclasses.dataclass
class RunResult:
    kernel: str
    parent: str
    idx: int
    csim_dir: str
    status: str
    orig_lines_found: Optional[int] = None
    orig_lines_hit: Optional[int] = None
    refactor_lines_found: Optional[int] = None
    refactor_lines_hit: Optional[int] = None
    run_returncode: Optional[int] = None
    compile_stderr: str = ""
    run_stderr: str = ""
    orig_gcov_lines_json: Optional[str] = None


_LINES_RE = re.compile(r"Lines executed:\s*([\d.]+)%\s+of\s+(\d+)")


def parse_gcov_n_stdout(stdout: str) -> dict[str, tuple[int, int]]:
    """Parse `gcov -n` stdout into {basename: (lines_hit, lines_found)}.

    `gcov -n` emits per source file:
        File 'orig_code.cpp'
        Lines executed:53.49% of 215
    """
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


def parse_gcov_file(path: Path) -> dict[int, int]:
    """Parse a .gcov file into {line_number: hit_count}.

    A line in a .gcov file looks like:
        <count>:<lineno>:<source line>
    where <count> is digits, '-' for non-instrumented, or '#####'/'=====' for never-executed.
    Header lines have lineno 0 and are skipped.
    """
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
                # gcov suffixes "*" on counts where execution was observed but
                # some branches were not taken (e.g., "52*"). Treat as hit.
                count_clean = count_s.rstrip("*").replace(",", "")
                try:
                    count = int(count_clean)
                except ValueError:
                    continue
            hits[lineno] = count
    return hits


def parent_short_name(root: Path) -> str:
    return "rag_new_2" if root.name.endswith("_2") else "rag_new"


def latest_csim_dir(run_dir: Path) -> Optional[Path]:
    """Return the latest csim_<timestamp>/ dir that contains all three sources.

    The LLM flow may create a csim_<timestamp> dir for a retry that gets killed
    before it writes files; the *absolute* latest may therefore be incomplete.
    We want the latest *complete* one.
    """
    candidates = [p for p in run_dir.glob("csim_*") if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime)
    for c in reversed(candidates):
        if all((c / s).is_file() for s in SOURCES):
            return c
    return None


def discover_tasks(run_roots: list[Path]) -> list[tuple[str, str, int, Optional[Path]]]:
    tasks: list[tuple[str, str, int, Optional[Path]]] = []
    for root in run_roots:
        if not root.is_dir():
            print(f"WARNING: run root not found: {root}", file=sys.stderr)
            continue
        parent = parent_short_name(root)
        for kernel_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            kernel = kernel_dir.name
            for run_dir in sorted(p for p in kernel_dir.iterdir() if p.is_dir() and p.name.isdigit()):
                idx = int(run_dir.name)
                tasks.append((kernel, parent, idx, latest_csim_dir(run_dir)))
    return tasks


def run_single(task, out_dir_str: str, tmp_root: Optional[str]) -> RunResult:
    kernel, parent, idx, csim_dir = task
    out_dir = Path(out_dir_str)
    rr = RunResult(
        kernel=kernel,
        parent=parent,
        idx=idx,
        csim_dir=str(csim_dir) if csim_dir else "",
        status="",
    )

    if csim_dir is None:
        rr.status = "no_csim_dir"
        return rr

    for s in SOURCES:
        if not (csim_dir / s).is_file():
            rr.status = "missing_sources"
            return rr

    with tempfile.TemporaryDirectory(prefix="agcov_", dir=tmp_root) as tmp_s:
        tmp = Path(tmp_s)
        for s in SOURCES:
            shutil.copy2(csim_dir / s, tmp / s)

        compiled = False
        for include_flag in (None, f"-I{XILINX_INCLUDE_FALLBACK}"):
            cmd = list(COMPILE_BASE)
            if include_flag is not None:
                cmd.insert(1, include_flag)
            try:
                r = subprocess.run(
                    cmd, cwd=tmp, capture_output=True, text=True, timeout=COMPILE_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                rr.status = "compile_timeout"
                return rr
            if r.returncode == 0:
                compiled = True
                break
            rr.compile_stderr = r.stderr[-2000:]
        if not compiled:
            rr.status = "compile_failed"
            return rr

        try:
            r = subprocess.run(
                ["./csim_cov"], cwd=tmp, capture_output=True, text=True, timeout=RUN_TIMEOUT
            )
            rr.run_returncode = r.returncode
            rr.run_stderr = r.stderr[-2000:]
        except subprocess.TimeoutExpired:
            rr.status = "timeout"
            return rr

        # gcc >= 9 prefixes gcno/gcda with the executable name when compiling
        # multiple sources into one binary, e.g. "csim_cov-orig_code.gcda".
        gcda_files = sorted(tmp.glob("*.gcda"))
        if not gcda_files:
            rr.status = "no_gcda"
            return rr
        orig_gcda = next((g for g in gcda_files if g.name.endswith("-orig_code.gcda")
                          or g.name == "orig_code.gcda"), None)
        refactor_gcda = next((g for g in gcda_files if g.name.endswith("-refactor_code.gcda")
                              or g.name == "refactor_code.gcda"), None)
        gcda_to_pass = [p.name for p in (orig_gcda, refactor_gcda) if p is not None]
        if not gcda_to_pass:
            rr.status = "no_gcda_for_targets"
            return rr

        try:
            g = subprocess.run(
                ["gcov", "-n", *gcda_to_pass],
                cwd=tmp, capture_output=True, text=True, timeout=GCOV_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            rr.status = "gcov_timeout"
            return rr
        if g.returncode != 0:
            rr.status = "gcov_failed"
            rr.run_stderr = g.stderr[-2000:]
            return rr

        summaries = parse_gcov_n_stdout(g.stdout)
        if "orig_code.cpp" in summaries:
            rr.orig_lines_hit, rr.orig_lines_found = summaries["orig_code.cpp"]
        if "refactor_code.cpp" in summaries:
            rr.refactor_lines_hit, rr.refactor_lines_found = summaries["refactor_code.cpp"]

        # Per-line dump for orig_code.cpp so we can compute cross-run union later.
        if orig_gcda is not None:
            try:
                subprocess.run(
                    ["gcov", orig_gcda.name],
                    cwd=tmp, capture_output=True, text=True, timeout=GCOV_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                pass
        orig_gcov_path = tmp / "orig_code.cpp.gcov"
        if orig_gcov_path.is_file():
            line_hits = parse_gcov_file(orig_gcov_path)
            dst = out_dir / "raw" / kernel / f"{parent}_{idx:02d}_orig.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "w") as f:
                json.dump(line_hits, f)
            rr.orig_gcov_lines_json = str(dst)

        rr.status = "ok"
        return rr


def _mean_std(xs: list[float]) -> tuple[Optional[float], Optional[float]]:
    if not xs:
        return (None, None)
    if len(xs) == 1:
        return (xs[0], 0.0)
    return (statistics.mean(xs), statistics.stdev(xs))


def aggregate(results: list[RunResult], out_dir: Path) -> list[dict]:
    by_kernel: dict[str, list[RunResult]] = {}
    for r in results:
        by_kernel.setdefault(r.kernel, []).append(r)

    rows: list[dict] = []
    for kernel in sorted(by_kernel):
        runs = by_kernel[kernel]
        n_total = len(runs)
        n_no_csim = sum(1 for r in runs if r.status == "no_csim_dir")
        n_compile_failed = sum(1 for r in runs if r.status == "compile_failed")
        n_timeout = sum(1 for r in runs if r.status == "timeout")
        n_no_gcda = sum(1 for r in runs if r.status == "no_gcda")
        ok_runs = [r for r in runs if r.status == "ok"]
        n_ok = len(ok_runs)

        orig_pcts = [
            100.0 * r.orig_lines_hit / r.orig_lines_found
            for r in ok_runs
            if r.orig_lines_found and r.orig_lines_hit is not None
        ]
        refactor_pcts = [
            100.0 * r.refactor_lines_hit / r.refactor_lines_found
            for r in ok_runs
            if r.refactor_lines_found and r.refactor_lines_hit is not None
        ]
        orig_sizes = [r.orig_lines_found for r in ok_runs if r.orig_lines_found]
        refactor_sizes = [r.refactor_lines_found for r in ok_runs if r.refactor_lines_found]

        orig_mean, orig_std = _mean_std(orig_pcts)
        refactor_mean, refactor_std = _mean_std(refactor_pcts)

        # Cross-run union for orig_code.cpp.
        if len(set(orig_sizes)) > 1:
            print(
                f"WARNING: orig_code.cpp size varies for {kernel}: {sorted(set(orig_sizes))}",
                file=sys.stderr,
            )
        union_lines: dict[int, int] = {}
        for r in ok_runs:
            if not r.orig_gcov_lines_json:
                continue
            with open(r.orig_gcov_lines_json) as f:
                lh = json.load(f)
            for lineno_s, count in lh.items():
                lineno = int(lineno_s)
                union_lines[lineno] = max(union_lines.get(lineno, 0), int(count))
        if union_lines:
            (out_dir / "union").mkdir(parents=True, exist_ok=True)
            with open(out_dir / "union" / f"{kernel}_orig_union.json", "w") as f:
                json.dump(union_lines, f)
            union_total = len(union_lines)
            union_hit = sum(1 for v in union_lines.values() if v > 0)
            union_pct: Optional[float] = 100.0 * union_hit / union_total
        else:
            union_pct = None

        rows.append({
            "kernel": kernel,
            "n_total": n_total,
            "n_ok": n_ok,
            "n_no_csim": n_no_csim,
            "n_compile_failed": n_compile_failed,
            "n_timeout": n_timeout,
            "n_no_gcda": n_no_gcda,
            "orig_lines_total": orig_sizes[0] if orig_sizes else None,
            "orig_pct_mean": orig_mean,
            "orig_pct_std": orig_std,
            "orig_pct_union": union_pct,
            "refactor_pct_mean": refactor_mean,
            "refactor_pct_std": refactor_std,
            "refactor_lines_found_mean": statistics.mean(refactor_sizes) if refactor_sizes else None,
        })
    return rows


def write_outputs(rows: list[dict], out_dir: Path) -> None:
    if not rows:
        print("No rows to write.", file=sys.stderr)
        return

    cols = list(rows[0].keys())
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    def fmt(x, d=1):
        if x is None:
            return "-"
        if isinstance(x, float):
            return f"{x:.{d}f}"
        return str(x)

    md = [
        "# Testbench Line Coverage",
        "",
        ("| Kernel | n_ok/n_total | orig lines | orig % (mean ± std) "
         "| orig % (union) | refactor lines (mean) | refactor % (mean ± std) |"),
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        md.append(
            f"| {row['kernel']} "
            f"| {row['n_ok']}/{row['n_total']} "
            f"| {fmt(row['orig_lines_total'], 0)} "
            f"| {fmt(row['orig_pct_mean'])} ± {fmt(row['orig_pct_std'])} "
            f"| {fmt(row['orig_pct_union'])} "
            f"| {fmt(row['refactor_lines_found_mean'], 1)} "
            f"| {fmt(row['refactor_pct_mean'])} ± {fmt(row['refactor_pct_std'])} |"
        )
    with open(out_dir / "summary.md", "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-root", action="append", default=[],
                   help="Paper run root dir; pass multiple times. Defaults to both _new and _new_2.")
    p.add_argument("--out", default=Path(os.environ.get("COVERAGE_OUT", "coverage_results")), type=Path,
                   help="Output directory.")
    p.add_argument("--workers", type=int, default=16, help="Parallel workers.")
    p.add_argument("--tmp-root", default=None,
                   help="Per-worker tmp dir parent (default: $TMPDIR or /tmp). "
                        "Use a local SSD path on the compute node for speed.")
    p.add_argument("--limit", type=int, default=None,
                   help="Process only the first N runnable tasks (smoke test).")
    p.add_argument("--kernel", action="append", default=None,
                   help="Filter to specific kernel name(s); repeat the flag for multiple.")
    p.add_argument("--dry-run", action="store_true", help="Discover tasks and exit.")
    args = p.parse_args()

    run_roots = [Path(r) for r in (args.run_root or DEFAULT_RUN_DIRS)]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "raw").mkdir(exist_ok=True)
    (args.out / "union").mkdir(exist_ok=True)
    if args.tmp_root:
        os.makedirs(args.tmp_root, exist_ok=True)

    tasks_all = discover_tasks(run_roots)
    print(f"Discovered {len(tasks_all)} run dirs across {len(run_roots)} roots.")

    if args.kernel:
        tasks_all = [t for t in tasks_all if t[0] in set(args.kernel)]
        print(f"Filtered to {len(tasks_all)} runs for kernels: {args.kernel}")

    # Split: tasks with a csim dir go through the worker pool; ones without become
    # immediate RunResult(status='no_csim_dir').
    runnable = [t for t in tasks_all if t[3] is not None]
    no_csim = [
        RunResult(kernel=k, parent=p, idx=i, csim_dir="", status="no_csim_dir")
        for (k, p, i, c) in tasks_all if c is None
    ]
    if args.limit is not None:
        runnable = runnable[: args.limit]

    print(f"Will execute {len(runnable)} runs ({len(no_csim)} have no csim_*/ dir).")
    if args.dry_run:
        for t in runnable[:20]:
            print(t)
        if len(runnable) > 20:
            print(f"... and {len(runnable) - 20} more")
        return

    t0 = time.time()
    results: list[RunResult] = list(no_csim)
    with cf.ProcessPoolExecutor(max_workers=args.workers) as exe:
        futures = {
            exe.submit(run_single, t, str(args.out), args.tmp_root): t
            for t in runnable
        }
        completed = 0
        for fut in cf.as_completed(futures):
            t = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                k, par, i, c = t
                r = RunResult(
                    kernel=k, parent=par, idx=i,
                    csim_dir=str(c) if c else "",
                    status=f"crash:{type(e).__name__}:{e}",
                )
            results.append(r)
            completed += 1
            o = (f"{r.orig_lines_hit}/{r.orig_lines_found}"
                 if r.orig_lines_found else "-")
            ref = (f"{r.refactor_lines_hit}/{r.refactor_lines_found}"
                   if r.refactor_lines_found else "-")
            print(f"[{completed}/{len(runnable)}] {r.kernel}/{r.parent}/{r.idx} "
                  f"-> {r.status} orig={o} refactor={ref}")

    with open(args.out / "per_run.jsonl", "w") as f:
        for r in results:
            f.write(json.dumps(dataclasses.asdict(r)) + "\n")

    rows = aggregate(results, args.out)
    write_outputs(rows, args.out)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s. Outputs in {args.out}/")


if __name__ == "__main__":
    main()
