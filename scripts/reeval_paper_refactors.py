#!/usr/bin/env python3
"""Re-evaluate paper refactors against per-attempt coverage-optimized hidden TBs.

For each (kernel, attempt) in the selected paper runs:
  1. Load (paper_tb, paper_refactor, paper_pass) from context_final.json
  2. Optimize paper_tb's coverage via `tb_optimizer.optimize_tb_seeded`
     (K rounds, stub-for-measurement, signature locked)
  3. Run the resulting strong_tb against the REAL paper refactor → records
     pass_hidden, failure_kind, cov_strong_tb, cov_paper_tb
  4. Persist one JSON per (condition, kernel, repeat) under --out-dir for later
     aggregation.

Usage:
    python scripts/reeval_paper_refactors.py \\
        --out-dir ./reeval_results \\
        --workers 4 \\
        --kernels minimap2_mm_chain_dp_orig libjpeg_idct_generic av1_compound_type_rd libjpeg_encode_one_block

Conditions are hard-coded to the two paper test pools (no-mem, with-mem) pooled
across `_new` and `_new_2`. Pass `--condition` to restrict.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from flow.tools.tb_optimizer import optimize_tb_seeded  # noqa: E402
from flow.tools.tb_coverage import measure_coverage     # noqa: E402
from flow.tools.tb_hidden_eval import eval_against_hidden_tb  # noqa: E402


CONDITIONS: Dict[str, List[str]] = {
    "no_mem": [
        str(REPO / "runs/[paper] gpt5_mini_test_no_rag_new"),
        str(REPO / "runs/[paper] gpt5_mini_test_no_rag_new_2"),
    ],
    "with_mem": [
        str(REPO / "runs/[paper] gpt5_mini_test_with_hybrid_rag_new"),
        str(REPO / "runs/[paper] gpt5_mini_test_with_hybrid_rag_new_2"),
    ],
}


def find_attempts(condition_dirs: List[str], kernel_suffix: str) -> List[Tuple[str, int, str]]:
    """Return (parent_dir, repeat_idx, context_final_path) for each attempt."""
    out = []
    for parent in condition_dirs:
        kdir = os.path.join(parent, kernel_suffix)
        if not os.path.isdir(kdir):
            continue
        for rep_name in sorted(os.listdir(kdir)):
            rep_path = os.path.join(kdir, rep_name)
            ctx_path = os.path.join(rep_path, "context_final.json")
            if rep_name.isdigit() and os.path.isfile(ctx_path):
                out.append((parent, int(rep_name), ctx_path))
    return out


def process_one(args, condition: str, kernel: str, parent: str, repeat: int, ctx_path: str) -> Optional[dict]:
    """Run the full re-eval pipeline for one attempt. Returns the result dict."""
    # Disambiguate by parent dir tag: `_new_2` parents get an "_2" suffix to avoid
    # collision with `_new` (both pool repeat indices 0–9).
    parent_tag = "_2" if parent.rstrip("/").endswith("_2") else ""
    out_path = os.path.join(args.out_dir, condition, kernel, f"{repeat}{parent_tag}.json")
    if not args.overwrite and os.path.isfile(out_path):
        return None  # already done

    try:
        ctx = json.load(open(ctx_path))
    except Exception as e:
        return {"condition": condition, "kernel": kernel, "repeat": repeat, "error": f"ctx_load_failed:{e}"}

    orig_code = ctx.get("orig_code", "")
    paper_tb = ctx.get("testbench", "")
    hist = ctx.get("csynth_csim_history", [])
    paper_refactor = hist[-1].get("refactored_code", "") if hist else ""
    paper_pass = bool(hist) and hist[-1].get("status") in ("succeeded", "succeeded by hetero")

    if not (orig_code and paper_tb and paper_refactor):
        return {"condition": condition, "kernel": kernel, "repeat": repeat,
                "error": "missing_data",
                "has_orig": bool(orig_code), "has_tb": bool(paper_tb),
                "has_refactor": bool(paper_refactor)}

    # Kernel function name → look up from kernel suffix
    # Easier: use ctx's kernel_name and new_kernel_name as ground truth
    kernel_name = ctx.get("kernel_name", "")
    new_kernel_name = ctx.get("new_kernel_name", f"{kernel_name}_hls")

    # --- baseline: measure paper_tb coverage with paper_tb's own stub-equivalent ---
    # We measure paper_tb coverage by compiling paper_tb + orig + paper_refactor.
    # If refactor is buggy, this can early-exit; but we record what the paper saw.
    cov_paper_with_paper_refactor = measure_coverage(orig_code, paper_tb, paper_refactor)

    # --- coverage-optimize seeded by paper_tb (uses stub, immune to refactor bugs) ---
    t0 = time.time()
    try:
        result = optimize_tb_seeded(
            orig_code=orig_code,
            kernel_name=kernel_name,
            seed_tb=paper_tb,
            K=args.K,
            target_pct=args.target_pct,
            llm_config=None,
        )
    except Exception as e:
        return {"condition": condition, "kernel": kernel, "repeat": repeat,
                "error": f"optimize_failed:{type(e).__name__}:{e}"}
    opt_dt = time.time() - t0

    strong_tb = result["best_tb"]
    cov_strong_with_stub = result["best_cov"]

    # --- eval gate: strong_tb + orig + REAL paper refactor ---
    eval_dir = os.path.join(args.out_dir, condition, kernel, f"{repeat}_eval")
    os.makedirs(eval_dir, exist_ok=True)
    eval_result = eval_against_hidden_tb(
        orig_code=orig_code,
        refactor_code=paper_refactor,
        hidden_tb=strong_tb,
        work_dir=eval_dir,
    )

    record = {
        "condition": condition,
        "kernel": kernel,
        "repeat": repeat,
        "paper_parent": parent,
        "paper_pass": paper_pass,
        "new_kernel_name": new_kernel_name,
        "cov_paper_tb_with_refactor": cov_paper_with_paper_refactor.get("cov_pct"),
        "cov_strong_tb_with_stub": cov_strong_with_stub,
        "cov_strong_tb_with_refactor": eval_result.get("cov_hidden"),
        "pass_hidden": eval_result["passed"],
        "hidden_failure_kind": eval_result["failure_kind"],
        "optimize_seconds": opt_dt,
        "n_rounds": len(result["rounds"]),
        "best_round": result["best_round"],
        # Optional: persist full strong_tb for debugging (often large; comment out if not needed)
        # "strong_tb": strong_tb,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2)
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(REPO / "reeval_results"))
    ap.add_argument("--K", type=int, default=3)
    ap.add_argument("--target-pct", type=float, default=90.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--kernels", nargs="+", default=[
        "minimap2_mm_chain_dp_orig",
        "libjpeg_idct_generic",
        "av1_compound_type_rd",
        "libjpeg_encode_one_block",
    ])
    ap.add_argument("--condition", choices=list(CONDITIONS.keys()), default=None,
                    help="If set, only run this one condition.")
    ap.add_argument("--overwrite", action="store_true",
                    help="Re-run attempts even if output JSON already exists.")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    conds = [args.condition] if args.condition else list(CONDITIONS.keys())

    # Discover all jobs
    jobs = []
    for cond in conds:
        for kernel in args.kernels:
            for parent, repeat, ctx in find_attempts(CONDITIONS[cond], kernel):
                jobs.append((cond, kernel, parent, repeat, ctx))
    print(f"[discover] {len(jobs)} attempts to re-evaluate ({len(args.kernels)} kernels × {len(conds)} conditions)", file=sys.stderr)

    t_start = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as exe:
        futs = {exe.submit(process_one, args, *j): j for j in jobs}
        for fut in cf.as_completed(futs):
            cond, kernel, parent, repeat, _ = futs[fut]
            done += 1
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"error": f"exec:{type(e).__name__}:{e}"}
            tag = f"[{done}/{len(jobs)}]"
            if rec is None:
                print(f"{tag} {cond}/{kernel}/{repeat}: cached, skipped", file=sys.stderr)
            elif "error" in rec:
                print(f"{tag} {cond}/{kernel}/{repeat}: ERROR {rec.get('error')}", file=sys.stderr)
            else:
                print(f"{tag} {cond}/{kernel}/{repeat}: "
                      f"paper_pass={rec['paper_pass']} pass_hidden={rec['pass_hidden']} "
                      f"kind={rec['hidden_failure_kind']} "
                      f"cov_strong_stub={rec['cov_strong_tb_with_stub']:.1f}% "
                      f"({rec['n_rounds']} rounds, best={rec['best_round']}, {rec['optimize_seconds']:.0f}s)",
                      file=sys.stderr)
    print(f"\n[done] {done} attempts in {time.time()-t_start:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
