#!/usr/bin/env python3
"""Aggregate reeval_results/*.json into a per-kernel × per-condition inflation-gap
LaTeX table and write it to a .txt file.

Usage:
    python scripts/build_inflation_table.py --in-dir ./reeval_results --out ./inflation_gap_table.txt
"""

from __future__ import annotations
import argparse
import glob
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


SHORT_NAME = {
    "av1_compound_type_rd":         "av1\\_compound\\_type\\_rd",
    "libjpeg_encode_one_block":     "encode\\_one\\_block",
    "libjpeg_idct_generic":         "idct\\_generic",
    "libjpeg_median_cut":           "median\\_cut",
    "libsodium_argon2_fill_segment": "argon2\\_fill\\_segment",
    "minimap2_mm_chain_dp_orig":    "mm\\_chain\\_dp\\_orig",
    "hetero_ahocorasick":           "ahocorasick",
    "hetero_dfs":                   "dfs",
    "hetero_strassen":              "strassen",
    "leetcode_wordBreak":           "wordbreak",
    "leetcode_skyline":             "skyline",
}
KERNEL_ORDER_HARD = [
    "av1_compound_type_rd",
    "libjpeg_encode_one_block",
    "libjpeg_idct_generic",
    "minimap2_mm_chain_dp_orig",
]


def load_results(in_dir: str) -> List[dict]:
    out = []
    for p in glob.glob(os.path.join(in_dir, "*/*/*.json")):
        if "_eval" in p:
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if isinstance(d, dict) and "condition" in d:
            out.append(d)
    return out


def aggregate(results: List[dict]) -> Dict[Tuple[str, str], dict]:
    agg: Dict[Tuple[str, str], dict] = defaultdict(lambda: {
        "n": 0,
        "paper_pass": 0,
        "pass_hidden": 0,
        "both": 0,
        "pp_only_mismatch": 0,
        "pp_only_compile_err": 0,
        "pp_only_other": 0,
        "ph_only": 0,
        "cov_strong_stub_sum": 0.0,
        "cov_strong_stub_n": 0,
    })
    for r in results:
        k = (r["condition"], r["kernel"])
        a = agg[k]
        a["n"] += 1
        pp = bool(r.get("paper_pass"))
        ph = bool(r.get("pass_hidden"))
        if pp: a["paper_pass"] += 1
        if ph: a["pass_hidden"] += 1
        if pp and ph: a["both"] += 1
        if pp and not ph:
            kind = r.get("hidden_failure_kind") or "other"
            if kind == "mismatch": a["pp_only_mismatch"] += 1
            elif kind == "compile_err": a["pp_only_compile_err"] += 1
            else: a["pp_only_other"] += 1
        if ph and not pp:
            a["ph_only"] += 1
        cov = r.get("cov_strong_tb_with_stub")
        if isinstance(cov, (int, float)) and cov > 0:
            a["cov_strong_stub_sum"] += cov
            a["cov_strong_stub_n"] += 1
    return agg


def build_latex_inflation(agg: Dict, conditions: List[str], kernels: List[str]) -> str:
    rows = []
    sums = {c: {"n": 0, "pp": 0, "ph": 0, "both": 0, "mis": 0, "ce": 0} for c in conditions}
    for k in kernels:
        cells = []
        for c in conditions:
            d = agg.get((c, k), {})
            n = d.get("n", 0)
            pp = d.get("paper_pass", 0)
            ph = d.get("pass_hidden", 0)
            mis = d.get("pp_only_mismatch", 0)
            ce = d.get("pp_only_compile_err", 0)
            sums[c]["n"] += n
            sums[c]["pp"] += pp
            sums[c]["ph"] += ph
            sums[c]["both"] += d.get("both", 0)
            sums[c]["mis"] += mis
            sums[c]["ce"] += ce
            cells.extend([str(pp), str(ph), str(mis)])
        rows.append(f"{SHORT_NAME.get(k, k):<28} & " + " & ".join(f"{v:>3}" for v in cells) + f"  \\\\ % n={[agg.get((c, k), {}).get('n', 0) for c in conditions]}")
    body = "\n".join(rows)

    # Summary row
    summary_cells = []
    for c in conditions:
        s = sums[c]
        gap = s["pp"] - s["ph"]
        pct_gap = (100.0 * gap / s["pp"]) if s["pp"] else 0.0
        summary_cells.extend([
            f"\\textbf{{{s['pp']}}}",
            f"\\textbf{{{s['ph']}}}",
            f"\\textbf{{{s['mis']}}}",
        ])
    sum_row = "Total" + " " * (28 - 5) + " & " + " & ".join(f"{v:>3}" for v in summary_cells) + " \\\\"
    # And a gap row
    gap_cells = []
    for c in conditions:
        s = sums[c]
        gap = s["pp"] - s["ph"]
        pct_gap = (100.0 * gap / s["pp"]) if s["pp"] else 0.0
        gap_cells.extend([
            f"\\multicolumn{{2}}{{r}}{{{gap} ({pct_gap:.0f}\\%)}}",
            "",  # mismatch column unused for gap row
        ])
    gap_row = "Inflation gap" + " " * (28 - 13) + " & " + " & ".join(gap_cells) + " \\\\"

    col_count = 3 * len(conditions)
    col_spec = "l " + " ".join(["rrr" for _ in conditions])
    header_top = " & " + " & ".join([f"\\multicolumn{{3}}{{c}}{{\\textbf{{{c.replace('_', '-')}}}}}" for c in conditions]) + " \\\\"
    cmidrules = " ".join(f"\\cmidrule(lr){{{2 + 3*i}-{4 + 3*i}}}" for i in range(len(conditions)))
    header_sub_cells = []
    for _ in conditions:
        header_sub_cells.extend(["\\# pass\\textsubscript{public}", "\\# pass\\textsubscript{hidden}", "\\# inflated\\textsubscript{mismatch}"])
    header_sub = "\\textbf{Task} & " + " & ".join(header_sub_cells) + " \\\\"

    tex = f"""\\begin{{table*}}[h]
\\centering
\\small
\\caption{{Held-out testbench re-evaluation. For each paper attempt on the four lowest-coverage test kernels, we coverage-optimize the paper's testbench (preserving its signature) and re-run the paper's refactored code against the resulting stronger TB. ``Inflated\\textsubscript{{mismatch}}'' counts cases where the paper marked the attempt as passing but the stronger TB exposed an output mismatch in the same refactor. Conditions pool $N=20$ paper attempts per kernel across \\texttt{{\\_new}} and \\texttt{{\\_new\\_2}}.}}
\\vspace{{-5pt}}
\\begin{{tabular}}{{{col_spec}}}
\\toprule
{header_top}
{cmidrules}
{header_sub}
\\midrule
{body}
\\midrule
{sum_row}
{gap_row}
\\bottomrule
\\end{{tabular}}
\\label{{tab:inflation_gap}}
\\end{{table*}}
"""
    return tex


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-dir", default="./reeval_results")
    ap.add_argument("--out", default="./inflation_gap_table.txt")
    args = ap.parse_args()

    results = load_results(args.in_dir)
    print(f"Loaded {len(results)} per-attempt results from {args.in_dir}")

    agg = aggregate(results)

    # Print stat summary
    print(f"\n{'cond':<10} {'kernel':<32} {'n':>4} {'p_pub':>6} {'p_hid':>6} {'both':>5} {'mis':>4} {'compile_err':>11} {'ph_only':>8}")
    for (c, k), d in sorted(agg.items()):
        print(f"{c:<10} {k:<32} {d['n']:>4} {d['paper_pass']:>6} {d['pass_hidden']:>6} {d['both']:>5} {d['pp_only_mismatch']:>4} {d['pp_only_compile_err']:>11} {d['ph_only']:>8}")

    # Build table for the 4 hard kernels
    tex = build_latex_inflation(agg, conditions=["no_mem", "with_mem"], kernels=KERNEL_ORDER_HARD)
    Path(args.out).write_text(tex)
    print(f"\nWrote {args.out}")
    print(tex)


if __name__ == "__main__":
    main()
