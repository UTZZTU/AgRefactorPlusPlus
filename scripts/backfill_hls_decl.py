#!/usr/bin/env python3
"""Backfill `hidden_hls_decl_verbatim` into existing golden_tb cache files.

For each .json in the cache dir, extract the canonical `_hls(...)` declaration
text from hidden_tb via a tight regex (skips preceding `#define`/`typedef`),
and add it as a new field. Idempotent — overwrites existing field if present.
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def extract_hls_decl(hidden_tb: str, hls_name: str) -> str:
    """Extract the verbatim `_hls(...)` declaration text from hidden_tb.

    Line-based approach: find the line containing `hls_name(`, walk BACKWARD
    to find the start of the declaration (a line that starts with a C++ return
    type, NOT `#`, `//`, `typedef`, `#define`, `*`, etc.), then walk FORWARD
    to balance parens.
    """
    lines = hidden_tb.splitlines()
    # Find line index where the function name opens parens
    name_pat = re.compile(rf'\b{re.escape(hls_name)}\s*\(')
    candidates = [i for i, ln in enumerate(lines) if name_pat.search(ln)]
    if not candidates:
        return ""

    def is_decl_start(line: str) -> bool:
        """True if `line` looks like the start of a function declaration
        (return type + maybe extern "C"), not a preprocessor or comment line."""
        s = line.lstrip()
        if not s:
            return False
        if s.startswith(('#', '//', '/*', '*')):
            return False
        if s.startswith(('typedef ', 'using ', 'namespace ', 'struct ', 'class ', 'enum ')):
            return False
        # Must contain alphanumeric (return type), and NOT be just an identifier
        # at the start of a function call like `mm_chain_dp_orig(...)` from main
        return True

    for hit_idx in candidates:
        # Walk back to find start of decl line
        start = hit_idx
        # The name might be in same line as return type, or on a later line
        # (multi-line decl). Walk back while we don't have a return type yet.
        while start >= 0:
            line = lines[start]
            stripped = line.lstrip()
            if is_decl_start(line) and (
                # has tokens BEFORE the name (return type)
                bool(re.match(rf'^[\s]*[\w][\w\s\*&:\[\]<>"]*\s+(\b{re.escape(hls_name)}\b|\(.*\b{re.escape(hls_name)}\b)', line))
                # OR has 'extern "C"' followed by return type even if name is on next line
                or re.match(r'^[\s]*(extern\s+"C"\s+)?[\w][\w\s\*&:\[\]<>"]*$', line)
            ):
                break
            start -= 1
        if start < 0:
            continue

        # Walk forward, counting parens, until balanced and we hit ; or {
        sig_lines = []
        paren_depth = 0
        seen_open = False
        end_line_idx = start
        for j in range(start, len(lines)):
            line = lines[j]
            sig_lines.append(line)
            for ch in line:
                if ch == '(':
                    paren_depth += 1
                    seen_open = True
                elif ch == ')':
                    paren_depth -= 1
            if seen_open and paren_depth == 0:
                # Check if the very next non-whitespace char in following content is ; or {
                # Strip trailing ; or { from this line
                # Find ;/{ position after the last ')'
                end_line_idx = j
                break

        sig = "\n".join(sig_lines).strip()
        # Trim trailing ; or { (and content beyond)
        # Find the LAST ')' before any ; or { after it
        m = re.search(rf'\b{re.escape(hls_name)}\s*\([^)]*\)', sig, re.DOTALL)
        # Better: find the matching ')' for the first '(' after hls_name
        name_match = re.search(rf'\b{re.escape(hls_name)}\s*\(', sig)
        if not name_match:
            continue
        idx = name_match.end() - 1  # position of '('
        depth = 0
        end_paren = -1
        for k in range(idx, len(sig)):
            if sig[k] == '(':
                depth += 1
            elif sig[k] == ')':
                depth -= 1
                if depth == 0:
                    end_paren = k
                    break
        if end_paren < 0:
            continue
        # Return text from start of sig up to (and including) the closing ')'
        # (Drop trailing ;/{ and any extra)
        clean = sig[:end_paren + 1].strip()
        if clean and hls_name in clean:
            return clean
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", default=str(REPO / "golden_tb_rater_flow"))
    ap.add_argument("--kernels-file", default=str(REPO / "flow/test_kernels.json"))
    args = ap.parse_args()

    # Build {cache_key: hls_name} from kernels_file
    suffix_to_fn = {}
    with open(args.kernels_file) as f:
        for entry in json.load(f):
            if isinstance(entry, list) and len(entry) == 3:
                suffix_to_fn[entry[2]] = entry[1]  # suffix → function_name

    cache_dir = Path(args.cache_dir)
    files = sorted(cache_dir.glob("*.json"))
    print(f"Scanning {len(files)} cache files in {cache_dir}\n")

    for p in files:
        try:
            d = json.load(open(p))
        except Exception as e:
            print(f"  {p.name}: PARSE_ERROR ({e})")
            continue
        # Determine the hls function name to extract.
        key = p.stem
        fn_name = suffix_to_fn.get(key)
        if not fn_name:
            # Fallback: try kernel_name field, else key + "_hls"
            kn = d.get("kernel_name") or key
            fn_name = f"{kn}_hls"
        # Try the canonical {fn}_hls if not already suffixed
        hls_name = fn_name if fn_name.endswith("_hls") else f"{fn_name}_hls"

        tb = d.get("hidden_tb", "")
        if not tb:
            print(f"  {p.name}: no hidden_tb — skipping")
            continue
        decl = extract_hls_decl(tb, hls_name)
        if not decl:
            print(f"  {p.name}: COULD NOT EXTRACT decl for `{hls_name}` from hidden_tb")
            continue
        prev = d.get("hidden_hls_decl_verbatim")
        d["hidden_hls_decl_verbatim"] = decl
        # Persist
        tmp_path = str(p) + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        Path(tmp_path).replace(p)
        status = "UPDATED" if prev != decl else "UNCHANGED"
        first_line = decl.splitlines()[0][:120] if decl.splitlines() else ""
        print(f"  {p.name}: {status} ({len(decl)} chars) | {first_line}")


if __name__ == "__main__":
    main()
