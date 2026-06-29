#!/usr/bin/env python3
"""Audit cached golden hidden TBs: which kernels have a synthesizable `_hls` sig?

For each JSON in <cache-dir>, report:
  - hidden_cov
  - synth_ok (True/False/UNKNOWN if field missing — older cache entries)
  - synth_error (truncated)
  - whether the entry is stale (i.e., orig_sha256 in JSON does not match the
    current src file's sha256, if --kernels-file is provided to locate sources).

Usage:
    python scripts/audit_golden_tbs.py [--cache-dir ./golden_tb_rater_flow] [--kernels-file flow/test_kernels.json]

Exit code:
    0 if all entries have synth_ok=True
    1 if any entry has synth_ok=False or UNKNOWN
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional


REPO = Path(__file__).resolve().parent.parent


def load_src_shas(kernels_file: Optional[str], src_root: str) -> Dict[str, str]:
    """Return {suffix: sha256_of_src_file}. Empty if kernels_file is None or missing."""
    out: Dict[str, str] = {}
    if not kernels_file or not Path(kernels_file).is_file():
        return out
    with open(kernels_file) as f:
        kernels = json.load(f)
    for entry in kernels:
        if not (isinstance(entry, list) and len(entry) == 3):
            continue
        rel_path, fn_name, suffix = entry
        src_path = Path(src_root) / rel_path
        if src_path.is_file():
            src = src_path.read_text()
            out[suffix] = hashlib.sha256(src.encode("utf-8")).hexdigest()
            out[fn_name] = out[suffix]  # also key by function name (legacy caches)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", default=str(REPO / "golden_tb_rater_flow"))
    ap.add_argument("--kernels-file", default=str(REPO / "flow/test_kernels.json"))
    ap.add_argument("--src-root", default=str(REPO / "src"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_dir():
        print(f"ERROR: cache dir not found: {cache_dir}", file=sys.stderr)
        return 2

    src_shas = load_src_shas(args.kernels_file, args.src_root)

    files = sorted(cache_dir.glob("*.json"))
    if not files:
        print(f"(no .json files in {cache_dir})")
        return 0

    print(f"{'file':<40} {'cov':>6} {'synth':>9} {'stale':>7}  notes")
    print("-" * 85)

    any_bad = False
    for p in files:
        try:
            d = json.load(open(p))
        except Exception as e:
            print(f"{p.name:<40}  PARSE_ERROR: {e}")
            any_bad = True
            continue
        cov = d.get("hidden_cov")
        cov_s = f"{cov:.1f}%" if isinstance(cov, (int, float)) else "  ?  "
        synth = d.get("synth_ok")
        if synth is True:
            synth_s = "PASS"
        elif synth is False:
            synth_s = "FAIL"
            any_bad = True
        else:
            synth_s = "UNKNOWN"
            any_bad = True
        # Stale check: orig_sha256 in JSON vs current src
        key = p.stem  # filename without .json — used as cache_key when written
        expected_sha = src_shas.get(key)
        stored_sha = d.get("orig_sha256", "")
        stale = "?"
        if expected_sha:
            stale = "no" if expected_sha == stored_sha else "YES"
        notes = ""
        if synth is False:
            err = (d.get("synth_error") or "").strip().splitlines()
            if err:
                last_err = next((ln for ln in reversed(err) if "ERROR" in ln or "error:" in ln.lower()), err[-1])
                notes = last_err[:80]
        print(f"{p.name:<40} {cov_s:>6} {synth_s:>9} {stale:>7}  {notes}")

    print()
    print(f"Cache dir: {cache_dir}")
    print(f"Total entries: {len(files)}")
    n_pass = sum(1 for p in files if json.load(open(p)).get("synth_ok") is True)
    print(f"Synth pass: {n_pass}/{len(files)}")

    return 0 if not any_bad else 1


if __name__ == "__main__":
    sys.exit(main())
