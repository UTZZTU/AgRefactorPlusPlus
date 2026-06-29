#!/usr/bin/env python3
"""Pre-generate held-out hidden TBs for the test kernels.

Reads flow/test_kernels.json, iterates over the 11 test kernels, and runs
`tb_optimizer.make_golden_hidden_tb` for each, caching to <cache_dir>.

Cache key is the kernel_name_suffix (not the function name) so different
kernels that share `process_top` (hetero_ahocorasick, hetero_dfs, hetero_strassen)
don't collide.

Usage:
    python scripts/pregen_hidden_tbs.py --cache-dir ./golden_tb_rater_flow --workers 4
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

from flow.tools.tb_optimizer import make_golden_hidden_tb  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kernels-file", default=str(REPO / "flow/test_kernels.json"))
    ap.add_argument("--src-root", default=str(REPO / "src"),
                    help="Root dir under which kernel paths are resolved.")
    ap.add_argument("--cache-dir", default=str(REPO / "golden_tb_rater_flow"),
                    help="Where to write golden_tb/<suffix>.json files.")
    ap.add_argument("--M", type=int, default=5)
    ap.add_argument("--K", type=int, default=6)
    ap.add_argument("--target-pct", type=float, default=90.0)
    ap.add_argument("--workers", type=int, default=1,
                    help="Outer parallelism across kernels (each kernel still uses M inner threads).")
    ap.add_argument("--only", default=None,
                    help="Comma-separated subset of kernel suffixes to run (e.g. 'hetero_dfs,leetcode_skyline').")
    args = ap.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)

    with open(args.kernels_file) as f:
        kernels = json.load(f)
    if args.only:
        wanted = set(args.only.split(","))
        kernels = [k for k in kernels if k[2] in wanted]

    print(f"Pre-generating hidden TBs for {len(kernels)} kernels → {args.cache_dir}", file=sys.stderr)

    def _one(entry):
        rel_path, fname, suffix = entry
        kpath = os.path.join(args.src_root, rel_path)
        with open(kpath) as f:
            src = f.read()
        # Skip if cached and sha matches (make_golden_hidden_tb handles that)
        t0 = time.time()
        try:
            res = make_golden_hidden_tb(
                orig_code=src,
                kernel_name=fname,
                M=args.M,
                K=args.K,
                target_pct=args.target_pct,
                llm_config=None,
                cache_dir=args.cache_dir,
                cache_key=suffix,
            )
        except Exception as e:
            return suffix, None, str(e), time.time() - t0
        return suffix, res, None, time.time() - t0

    if args.workers > 1:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as exe:
            futs = [exe.submit(_one, k) for k in kernels]
            results = [f.result() for f in futs]
    else:
        results = [_one(k) for k in kernels]

    # Summary
    print(f"\n{'kernel_suffix':<35} {'cov':>6} {'best_traj':>10} {'best_round':>11}  {'time':>8}", file=sys.stderr)
    print("-" * 75, file=sys.stderr)
    for suffix, res, err, dt in results:
        if err is not None:
            print(f"{suffix:<35} ERROR: {err}", file=sys.stderr)
            continue
        print(f"{suffix:<35} {res.get('hidden_cov'):>5.1f}%  {str(res.get('best_trajectory')):>10}  {str(res.get('best_round')):>11}  {dt:>7.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
