#!/usr/bin/env python3
"""Reproduce paper Tables III/IV/V from saved logs in `runs/`.

Aggregates pass counts per (kernel × model × memory) from two result
schemas (different runs use different layouts):

  A) "flat repeats" layout (e.g. [paper] gpt5_mini_test_*):
       runs/<exp>/<kernel_suffix>/<repeat>/context_final.json
     status from `csynth_csim_history[-1].status in ('succeeded','succeeded by hetero')`.

  B) "parallel_flow_eval" layout (e.g. gpt5_flow_new, gpt5_flow_new_rag_epoch=2):
       runs/<exp>/run<N>/<exp_kernel_dir>/parallel_flow_eval_<ts>.json
     read `metrics.successful_runs` / `metrics.total_runs` and `parameters.enable_rag`.
"""

from __future__ import annotations
import glob
import json
import os
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"

# Paper-relevant kernels (test set)
KERNELS = [
    "av1_compound_type_rd",
    "libjpeg_encode_one_block",      # listed as "encode_one_block" in paper
    "libjpeg_idct_generic",          # listed as "idct_generic"
    "libjpeg_median_cut",            # "median_cut"
    "libsodium_argon2_fill_segment", # "argon2_fill_segment"
    "minimap2_mm_chain_dp_orig",     # "mm_chain_dp_orig"
    "hetero_ahocorasick",            # "ahocorasick"
    "hetero_dfs",                    # "dfs"
    "hetero_strassen",               # "strassen"
    "leetcode_wordBreak",            # "wordbreak"
    "leetcode_skyline",              # "skyline"
]


# ---------- schema A: flat repeats ----------
def count_passes_flat(exp_root: Path, kernel: str) -> tuple[int, int]:
    """Return (pass, total) for a kernel under a flat-repeats experiment dir."""
    kdir = exp_root / kernel
    if not kdir.is_dir():
        return 0, 0
    total = 0; passes = 0
    for rep in sorted(os.listdir(kdir)):
        repd = kdir / rep
        if not (repd.is_dir() and rep.isdigit()):
            continue
        ctx = repd / "context_final.json"
        if not ctx.is_file():
            continue
        try:
            d = json.load(open(ctx))
        except Exception:
            continue
        total += 1
        hist = d.get("csynth_csim_history", [])
        if hist and hist[-1].get("status") in ("succeeded", "succeeded by hetero"):
            passes += 1
    return passes, total


def aggregate_flat(exp_roots: list[Path]) -> dict[str, tuple[int, int]]:
    """Sum (pass, total) across multiple flat-repeats experiment dirs (pools repeats)."""
    out: dict[str, tuple[int, int]] = {}
    for k in KERNELS:
        p_total = 0; t_total = 0
        for er in exp_roots:
            p, t = count_passes_flat(er, k)
            p_total += p; t_total += t
        out[k] = (p_total, t_total)
    return out


# ---------- schema B: parallel_flow_eval ----------
def find_pfe_results(roots: list[Path], rag_filter: bool | None = None) -> dict[str, list[tuple[int, int]]]:
    """For each kernel, collect list of (pass, total) tuples across parallel_flow_eval_*.json files.

    rag_filter: True → only enable_rag=True, False → only False, None → both.
    """
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("parallel_flow_eval_*.json")):
            try:
                d = json.load(open(p))
            except Exception:
                continue
            pp = d.get("parameters", {})
            m  = d.get("metrics", {})
            kname = pp.get("kernel_name", "")
            enable_rag = bool(pp.get("enable_rag", False))
            if rag_filter is not None and enable_rag != rag_filter:
                continue
            # Map kernel_name → suffix
            suffix = None
            for k in KERNELS:
                if k.endswith(kname) or kname in k or kname == k.split("_", 1)[-1]:
                    suffix = k; break
            if not suffix:
                # try partial match by path
                for k in KERNELS:
                    if k in str(p):
                        suffix = k; break
            if not suffix:
                continue
            tot = m.get("total_runs", 0) or 0
            psucc = m.get("successful_runs", 0) or 0
            if tot > 0:
                out[suffix].append((psucc, tot))
    return out


def fmt_pass_list(pairs: list[tuple[int, int]]) -> str:
    if not pairs:
        return "—"
    total_pass = sum(p for p, _ in pairs)
    total = sum(t for _, t in pairs)
    return f"{total_pass}/{total}  ({len(pairs)} trial-sets: {','.join(f'{p}/{t}' for p,t in pairs)})"


# ---------- main ----------
def main():
    # Paper Table IV: GPT-5-mini ablation
    mini_no_mem = aggregate_flat([
        RUNS / "[paper] gpt5_mini_test_no_rag_new",
        RUNS / "[paper] gpt5_mini_test_no_rag_new_2",
    ])
    mini_with_mem = aggregate_flat([
        RUNS / "[paper] gpt5_mini_test_with_hybrid_rag_new",
        RUNS / "[paper] gpt5_mini_test_with_hybrid_rag_new_2",
    ])

    # Paper Table V: GPT-5 ablation
    gpt5_roots_no = [
        RUNS / "gpt5_flow_new",
    ]
    gpt5_roots_with = [
        RUNS / "[paper] gpt5_flow_new_rag_epoch=2",
    ]
    gpt5_no_mem = find_pfe_results(gpt5_roots_no, rag_filter=False)
    gpt5_with_mem = find_pfe_results(gpt5_roots_with, rag_filter=True)

    # ---- Print Table IV ----
    print()
    print("=== Paper Table IV reproduction (GPT-5-mini, N=20 expected) ===")
    print(f"{'Kernel':<32}  {'paper no-mem':>14}  {'reproduced':>12}  {'paper with-mem':>16}  {'reproduced':>12}")
    print("-" * 100)
    paper_IV = {
        # (kernel, no_mem_pass, with_mem_pass) — from Table IV
        "av1_compound_type_rd":           (0, 1),
        "libjpeg_encode_one_block":       (3, 5),
        "libjpeg_idct_generic":           (3, 5),
        "libjpeg_median_cut":             (13, 18),
        "libsodium_argon2_fill_segment":  (12, 13),
        "minimap2_mm_chain_dp_orig":      (9, 6),
        "hetero_ahocorasick":             (17, 18),
        "hetero_dfs":                     (18, 20),
        "hetero_strassen":                (17, 19),
        "leetcode_wordBreak":             (17, 20),
        "leetcode_skyline":               (20, 20),
    }
    for k in KERNELS:
        p_no, t_no = mini_no_mem.get(k, (0, 0))
        p_wi, t_wi = mini_with_mem.get(k, (0, 0))
        pap_no, pap_wi = paper_IV.get(k, ("?", "?"))
        print(f"{k:<32}  {str(pap_no):>14}  {f'{p_no}/{t_no}':>12}  {str(pap_wi):>16}  {f'{p_wi}/{t_wi}':>12}")

    # ---- Print Table V ----
    print()
    print("=== Paper Table V reproduction (GPT-5, N=20 expected) ===")
    print(f"{'Kernel':<32}  {'paper no-mem':>14}  {'reproduced':>20}  {'paper with-mem':>16}  {'reproduced':>20}")
    print("-" * 120)
    paper_V = {
        "av1_compound_type_rd":           (6, 5),
        "libjpeg_encode_one_block":       (2, 9),
        "libjpeg_idct_generic":           (0, 2),
        "libjpeg_median_cut":             (15, 18),
        "libsodium_argon2_fill_segment":  (14, 18),
        "minimap2_mm_chain_dp_orig":      (18, 18),
    }
    for k in KERNELS:
        if k not in paper_V:
            continue
        no_pairs = gpt5_no_mem.get(k, [])
        wi_pairs = gpt5_with_mem.get(k, [])
        pap_no, pap_wi = paper_V[k]
        print(f"{k:<32}  {pap_no:>14}  {fmt_pass_list(no_pairs):>20}  {pap_wi:>16}  {fmt_pass_list(wi_pairs):>20}")


if __name__ == "__main__":
    main()
