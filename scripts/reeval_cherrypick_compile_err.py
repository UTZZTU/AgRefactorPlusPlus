#!/usr/bin/env python3
"""Cherry-pick re-run of `scripts/reeval_paper_refactors.py` on ONLY the attempts
that previously produced `compile_err` (sig drift artifacts).

The patched `optimize_tb_seeded` now extracts the verbatim `_hls(...)` decl from
the seed TB and pins it into every prompt. This should resolve compile_err into
either `pass_hidden` or `mismatch` (real bug).

Existing reeval_results/<cond>/<kernel>/<repeat>{_2}.json files for these
attempts are OVERWRITTEN in place (the eval_dir subdir is also reset).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Reuse the same per-attempt processing logic
from scripts.reeval_paper_refactors import process_one, CONDITIONS, find_attempts


def discover_compile_err_attempts(reeval_dir: Path) -> list:
    """Return list of (condition, kernel, parent, repeat, ctx_path) tuples for
    every existing JSON whose hidden_failure_kind=='compile_err'."""
    out = []
    for p in reeval_dir.glob("*/*/*.json"):
        if "_eval" in str(p):
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("hidden_failure_kind") != "compile_err":
            continue
        cond = d["condition"]
        kernel = d["kernel"]
        parent = d.get("paper_parent", "")
        repeat = d["repeat"]
        ctx_path = Path(parent) / kernel / str(repeat) / "context_final.json"
        if ctx_path.is_file():
            out.append((cond, kernel, parent, repeat, str(ctx_path)))
    return out


class _Args:
    def __init__(self, out_dir, K, target_pct, overwrite):
        self.out_dir = out_dir
        self.K = K
        self.target_pct = target_pct
        self.overwrite = overwrite


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reeval-dir", default=str(REPO / "reeval_results"))
    ap.add_argument("--K", type=int, default=3)
    ap.add_argument("--target-pct", type=float, default=90.0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    reeval_dir = Path(args.reeval_dir)
    jobs = discover_compile_err_attempts(reeval_dir)
    print(f"[cherrypick] {len(jobs)} compile_err attempts to re-evaluate (with verbatim sig pinning)",
          file=sys.stderr)

    if not jobs:
        return 0

    runtime_args = _Args(out_dir=str(reeval_dir), K=args.K, target_pct=args.target_pct, overwrite=True)

    t_start = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as exe:
        futs = {exe.submit(process_one, runtime_args, *j): j for j in jobs}
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

    print(f"\n[cherrypick] {done} attempts re-run in {time.time()-t_start:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
