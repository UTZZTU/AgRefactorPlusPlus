"""Mechanical (no-LLM) checks the rater can run.

All checks operate on a Workspace and return structured results.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


CSYNTH_CHECK_TIMEOUT = 300  # seconds for `vitis-run --mode hls` on the empty stub


# ---- sig extraction / lint ----

def extract_hls_decl_from_tb(tb_code: str, hls_name: str) -> str:
    """Line-based extraction of the verbatim `_hls(...)` declaration line(s).

    Walks back from a line containing `<hls_name>(` to a line that looks like
    a return-type-start (rejects `#define`/`typedef`/comments). Then walks
    forward until parens are balanced.
    """
    lines = tb_code.splitlines()
    name_re = re.compile(rf'\b{re.escape(hls_name)}\s*\(')
    candidates = [i for i, ln in enumerate(lines) if name_re.search(ln)]

    def is_decl_start(line: str) -> bool:
        s = line.lstrip()
        if not s or s.startswith(('#', '//', '/*', '*')):
            return False
        if s.startswith(('typedef ', 'using ', 'namespace ', 'struct ', 'class ', 'enum ')):
            return False
        return True

    for hit in candidates:
        start = hit
        while start >= 0 and not is_decl_start(lines[start]):
            start -= 1
        if start < 0:
            continue
        # collect forward, balance parens
        depth = 0
        seen_open = False
        end = start
        for j in range(start, len(lines)):
            for ch in lines[j]:
                if ch == '(':
                    depth += 1; seen_open = True
                elif ch == ')':
                    depth -= 1
            end = j
            if seen_open and depth == 0:
                break
        sig = "\n".join(lines[start:end + 1])
        # trim trailing ;/{ and content beyond the closing ')'
        nm = re.search(rf'\b{re.escape(hls_name)}\s*\(', sig)
        if not nm:
            continue
        idx = nm.end() - 1
        depth = 0; close = -1
        for k in range(idx, len(sig)):
            if sig[k] == '(':
                depth += 1
            elif sig[k] == ')':
                depth -= 1
                if depth == 0:
                    close = k; break
        if close < 0:
            continue
        return sig[:close + 1].strip()
    return ""


@dataclass
class SigLintResult:
    ok: bool
    issues: List[str]


_FORBIDDEN_TYPE_PATTERNS = [
    (r'\(\s*\*\s*\w+\s*\)\s*\(', 'function_pointer_in_param'),
    (r'\bFILE\s*\*', 'FILE_star_in_sig'),
    (r'\bpthread_\w+', 'pthread_type_in_sig'),
    (r'\bstd::\w+', 'std_container_in_sig'),
    (r'\*\s*\*', 'pointer_to_pointer'),
]


def sig_lint(hls_decl: str) -> SigLintResult:
    """Mechanical signature lint: flag patterns we know cause downstream problems."""
    issues: List[str] = []
    s = hls_decl

    # 1. Forbidden type patterns
    for pat, name in _FORBIDDEN_TYPE_PATTERNS:
        if re.search(pat, s):
            issues.append(name)

    # 2. *_in / *_out scalar pair pattern (invites trivial pass-through)
    # Strip the function name + return type so we only see params.
    params_match = re.search(r'\(([^)]*)\)\s*$', s, re.DOTALL)
    if params_match:
        params = params_match.group(1)
        # Find param names
        param_names = []
        for tok in re.split(r',(?![^<]*>)', params):
            tok = tok.strip()
            # Last identifier on the line is conventionally the param name
            m = re.search(r'\b([A-Za-z_]\w*)\s*(\[[^]]*\])?\s*$', tok)
            if m:
                param_names.append(m.group(1))
        ins  = {n[:-3]: n for n in param_names if n.endswith('_in')}
        outs = {n[:-4]: n for n in param_names if n.endswith('_out')}
        paired = sorted(set(ins.keys()) & set(outs.keys()))
        if paired:
            issues.append(f"in_out_scalar_pair_pattern:{','.join(paired[:5])}")

    return SigLintResult(ok=(not issues), issues=issues)


# ---- anti-echo stub synthesis (mechanical) ----

@dataclass
class EchoSynthResult:
    """An echo stub generated mechanically from the signature.

    `body_kind` is informational: which echo strategy was used.
    """
    stub_code: str
    body_kind: str  # 'echo_in_out_pairs' / 'echo_default_only'


def _echo_return_candidates(rettype_clean: str, parsed_params) -> list:
    """Yield return-statement candidates for echo stubs.

    The strongest gameable TBs we've seen accept refactors that return one of
    the input params (e.g., `return ret_in;`), not just `return 0;`.
    We try multiple candidates and consider the TB gameable if ANY makes csim pass.
    """
    cands = []
    if rettype_clean == "void":
        cands.append("return;")
        return cands
    # Always try literal-0 / default-value-for-type
    if rettype_clean.endswith("*"):
        cands.append("return nullptr;")
    elif rettype_clean in ("int", "long", "long long", "size_t", "unsigned", "unsigned int",
                           "uint64_t", "int64_t", "uint32_t", "int32_t"):
        cands.append("return 0;")
    else:
        cands.append(f"return ({rettype_clean})0;")
    # Try returning each scalar input param (commonly the last `ret_in`-style scalar).
    # Look for params that look "scalar-like": no `*`, no `[`, no `const T*`.
    for (t, n, _) in parsed_params:
        if not n:
            continue
        # Skip pointer/array types (won't convert to return type cleanly)
        if '*' in t or '[' in t:
            continue
        cands.append(f"return ({rettype_clean}){n};")
    return cands


def synthesize_echo_stub(hls_decl: str, extra_includes: str = "",
                         return_variant_idx: int = 0) -> EchoSynthResult:
    """Build a C++ stub whose body trivially echoes inputs to outputs.

    Strategy:
      - For each `*_out` / `*_out`-array parameter that has a matching `*_in`
        sibling, write `*X_out = X_in[0]` (or equivalent for arrays).
      - For any remaining output pointer params, default-initialize (`*p = 0;`).
      - Return value: tries `return_variant_idx`-th candidate (see _echo_return_candidates).
        Caller should loop through variants when the first one fails to expose the TB as gameable.
    """
    # Parse: return_type, name, params
    m = re.match(
        r'^\s*(?P<rettype>(?:extern\s+"C"\s+)?[\w\s\*&:<>]+?)\s+'
        r'(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*$',
        hls_decl,
        re.DOTALL,
    )
    if not m:
        # Fallback: just return an empty body returning 0.
        return EchoSynthResult(
            stub_code=f"// could not parse sig; raw was:\n// {hls_decl}\n",
            body_kind='echo_default_only',
        )
    rettype = re.sub(r'\s+', ' ', m.group('rettype').strip())
    name = m.group('name')
    params_raw = m.group('params')

    # Tokenize params (naive — no nested templates / function pointers)
    raw_params = [p.strip() for p in re.split(r',(?![^<]*>)', params_raw) if p.strip()]
    parsed: List[Tuple[str, str, str]] = []  # (type_text, name, full_text)
    for p in raw_params:
        # Last identifier is param name
        nm = re.search(r'\b([A-Za-z_]\w*)\s*(\[[^]]*\])?\s*$', p)
        pname = nm.group(1) if nm else ""
        # Type is everything before the param name
        ptype = p[: p.rfind(pname)].strip() if pname else p
        parsed.append((ptype, pname, p))

    in_map = {n[:-3]: (t, n, full) for (t, n, full) in parsed if n.endswith("_in")}
    out_map = {n[:-4]: (t, n, full) for (t, n, full) in parsed if n.endswith("_out")}
    paired = sorted(set(in_map.keys()) & set(out_map.keys()))

    body_lines: List[str] = ["    // Echo body — used to detect TBs whose comparison is gameable by trivial pass-through."]
    for key in paired:
        in_t, in_n, _ = in_map[key]
        out_t, out_n, _ = out_map[key]
        # Outputs are usually pointer or array; inputs usually pointer/array or scalar
        # Most common case our coverage loop produces: `int *X_in` / `int *X_out` → `*X_out = X_in[0];`
        body_lines.append(
            f"    if ({out_n} && {in_n}) *{out_n} = {in_n}[0];"
            f"  // echo {key}"
        )
    # For remaining output pointers (no matching _in), default to 0
    for (t, n, _) in parsed:
        if n in [x[1] for x in [out_map[k] for k in paired]]:
            continue
        if '*' in t and 'const' not in t and n.endswith('_out'):
            body_lines.append(f"    if ({n}) *{n} = 0;")
    # Mark unused params to silence warnings
    if parsed:
        unused = "(void)" + "; (void)".join(n for (_, n, _) in parsed if n) + ";"
        body_lines.append("    " + unused)
    # Return statement — pick from candidate list
    rettype_clean = rettype.replace('extern "C"', '').strip()
    candidates = _echo_return_candidates(rettype_clean, parsed)
    if not candidates:
        candidates = ["return 0;"]
    chosen = candidates[min(return_variant_idx, len(candidates) - 1)]
    body_lines.append("    " + chosen)

    body = "\n".join(body_lines)
    full = (
        (extra_includes.rstrip() + "\n\n" if extra_includes else "")
        + f"// MECHANICAL ECHO STUB — generated by checks.synthesize_echo_stub\n"
        + f"{hls_decl} {{\n"
        + body
        + "\n}\n"
    )
    return EchoSynthResult(
        stub_code=full,
        body_kind='echo_in_out_pairs' if paired else 'echo_default_only',
    )


# ---- compile + run helpers (lift from existing tb_coverage but local) ----

def compile_csim(work_dir: Path, sources: List[str], out_bin: str = "csim_bin",
                 with_coverage: bool = False, timeout: int = 120) -> Tuple[int, str]:
    """g++ compile. Returns (returncode, stderr tail)."""
    cmd = ["g++", "-O0", "-g", "-Wno-unknown-pragmas"]
    if with_coverage:
        cmd.append("--coverage")
    cmd += sources + ["-o", out_bin]
    try:
        r = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "compile_timeout"
    return r.returncode, r.stderr[-2000:]


def run_csim(work_dir: Path, bin_name: str = "csim_bin", timeout: int = 60) -> Tuple[int, str]:
    """./<bin>. Returns (returncode, stdout+stderr tail)."""
    try:
        r = subprocess.run([f"./{bin_name}"], cwd=work_dir, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "run_timeout"
    return r.returncode, ((r.stdout or "")[-1000:] + (r.stderr or "")[-1000:])


# ---- driver-enforced csynth check on stub_empty.cpp -----------------------------

def mechanical_csynth_check(stub_empty_path: Path, hls_name: str,
                             work_dir: Path,
                             timeout: int = CSYNTH_CHECK_TIMEOUT) -> Tuple[bool, str]:
    """Run `vitis-run --mode hls` on a copy of stub_empty.cpp with set_top=<hls_name>.

    Returns (synth_passed: bool, error_tail: str). Designed for the driver to
    enforce — does NOT depend on the LLM agent running csynth correctly.

    Strategy:
        - Stage `stub_empty.cpp` as `refactor_code.cpp` in `work_dir` (so the
          standard `tools.csynth.make_csynth_script` recipe can consume it).
        - Write a minimal vitis.tcl with `set_top <hls_name>`.
        - Invoke `vitis-run --mode hls --tcl --input_file vitis.tcl`.
        - Parse the return code: 0 → success; non-zero → failure.
    """
    if not stub_empty_path.is_file():
        return False, f"stub_empty.cpp not found at {stub_empty_path}"
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Stage as refactor_code.cpp (vitis.tcl convention)
    (work_dir / "refactor_code.cpp").write_text(stub_empty_path.read_text())
    # Minimal vitis.tcl: open project, add file, set_top, csynth, exit
    tcl = (
        "open_project synth_check_proj -reset\n"
        f"set_top {hls_name}\n"
        'add_files "refactor_code.cpp" -cflags " -D XILINX "\n'
        "open_solution -reset solution -flow_target vitis\n"
        "set_part xcu200-fsgd2104-2-e\n"
        "create_clock -period 5 -name default\n"
        "csynth_design\n"
        "exit\n"
    )
    (work_dir / "vitis.tcl").write_text(tcl)
    cmd = ["vitis-run", "--mode", "hls", "--tcl", "--input_file", "vitis.tcl"]
    try:
        r = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "csynth_timeout"
    except FileNotFoundError:
        return False, "vitis-run not in PATH"
    # vitis-run returns 0 on csynth success
    if r.returncode == 0:
        return True, ""
    # Capture last 2000 chars of stderr+stdout for diagnostics
    combined = (r.stderr or "") + "\n" + (r.stdout or "")
    return False, combined[-2000:]


@dataclass
class AntiEchoResult:
    """True `ok` = TB rejects echo refactor (good TB).
    `ok=False` means echo refactor PASSED → TB is gameable."""
    ok: bool
    echo_stub_body_kind: str
    csim_returncode: int
    detail: str  # last compile/run tail for diagnostics


def _extract_tb_preamble(tb_code: str) -> str:
    """Extract everything in the TB BEFORE `int main(` — this contains the
    includes, typedefs, struct definitions, MACROs, and forward declarations
    the echo stub needs to compile standalone."""
    m = re.search(r'^[ \t]*(?:static\s+)?int\s+main\s*\(', tb_code, re.MULTILINE)
    if not m:
        # fallback: take first ~100 lines
        return "\n".join(tb_code.splitlines()[:100]) + "\n"
    return tb_code[: m.start()]


def run_anti_echo_check(orig_code: str, tb_code: str, hls_decl: str,
                        scratch_dir: Path, max_variants: int = 5) -> AntiEchoResult:
    """Try MULTIPLE echo variants; if ANY makes csim pass, TB is gameable.

    Variant 0 = `return 0` (or default). Variants 1..N-1 = `return <scalar_in_param>`.
    Returns the FIRST passing variant (if any) or the last attempt's result.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    preamble = _extract_tb_preamble(tb_code)
    # Generate variant 0 to discover how many candidates we have
    proto_echo = synthesize_echo_stub(hls_decl, extra_includes=preamble, return_variant_idx=0)
    # Count candidates by re-parsing once
    parsed_count = proto_echo.stub_code.count("return ")  # rough but harmless if 1
    # We'll iterate variant indices until either echo passes (BAD) or all fail (GOOD).
    last_result: Optional[AntiEchoResult] = None
    for v in range(max_variants):
        echo = synthesize_echo_stub(hls_decl, extra_includes=preamble, return_variant_idx=v)
        (scratch_dir / "orig_code.cpp").write_text(orig_code)
        (scratch_dir / "testbench.cpp").write_text(tb_code)
        (scratch_dir / "refactor_code.cpp").write_text(echo.stub_code)
        rc, err = compile_csim(scratch_dir,
                               ["testbench.cpp", "orig_code.cpp", "refactor_code.cpp"],
                               out_bin="csim_echo")
        if rc != 0:
            # Skip this variant — compile failed (e.g. type mismatch). Try the next.
            last_result = AntiEchoResult(ok=True, echo_stub_body_kind=f"v{v}:{echo.body_kind}",
                                         csim_returncode=rc, detail=f"compile_failed (v{v}):\n{err[:400]}")
            continue
        rc, out = run_csim(scratch_dir, bin_name="csim_echo")
        if rc == 0:
            # ECHO PASSED → TB is gameable. Stop and report.
            return AntiEchoResult(ok=False, echo_stub_body_kind=f"v{v}:{echo.body_kind}",
                                  csim_returncode=rc, detail=f"ECHO v{v} PASSED:\n{out}")
        last_result = AntiEchoResult(ok=True, echo_stub_body_kind=f"v{v}:{echo.body_kind}",
                                     csim_returncode=rc, detail=f"v{v} rejected: {out[:300]}")
    return last_result  # all variants rejected → GOOD
