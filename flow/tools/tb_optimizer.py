"""Iterative testbench optimization loops (public + hidden).

Both loops follow the same shape:
  1. Initialize a tb_engineer agent (single conversation, clear_history=False).
  2. Round k: ask for a testbench → ask for a matching stub → measure coverage
     → feed back uncovered-line summary in next round's request.
  3. After K rounds, pick max-cov round; ask agent for either an instruction
     (public TB, downstream refactor agent consumes) or a sig_spec (hidden TB,
     constrains future public TB generation).

The hidden loop runs M trajectories in parallel via ThreadPoolExecutor and
picks the best trajectory by max-cov-within-trajectory.

All artifacts (per-round TB, stub, coverage stats, final pick) are returned as
plain dicts so callers can log/cache them without depending on autogen types.
"""

import concurrent.futures
import json
import os
import tempfile
import hashlib
from typing import Any, Dict, List, Optional, Tuple

from autogen.agentchat.group import ContextVariables  # type: ignore
from flow.base_agent import HLSAgentLoader
import flow.tools as tools
from flow.tools.tb_coverage import measure_coverage, annotate_uncovered_source

# Reuse the line-based regex extractor from the new inflight package.
from flow.inflight_tb.checks import extract_hls_decl_from_tb as _extract_seed_hls_decl  # noqa: E402


AGENT_YAML = "flow/agents/testbench_coverage.yaml"
AGENT_NAME = "tb_engineer"
SYNTH_CHECK_TIMEOUT = 300  # seconds for csynth on empty stub

# Maximum number of uncovered lines to enumerate explicitly in the prompt;
# beyond this we just include the annotated source. Keeps prompts bounded.
MAX_LISTED_UNCOVERED = 60


# -------------------------- prompt builders --------------------------

def _initial_user_message(
    orig_code: str,
    kernel_name: str,
    sig_spec_constraint: Optional[str],
    pinned_hls_decl: Optional[str] = None,
) -> str:
    """Build the round-1 user prompt.

    If `pinned_hls_decl` is provided, it is injected as a "PINNED DECLARATION"
    block that the LLM MUST reproduce character-for-character — this is the
    canonical signature extracted from the hidden TB and is what downstream
    eval (hidden TB eval, refactor agent) will use.
    """
    hls_name = f"{kernel_name}_hls"
    parts = [
        "Original kernel source code:",
        "```cpp",
        orig_code.rstrip(),
        "```",
        "",
        f"Top function name (original / golden reference): {kernel_name}",
        f"HLS-side function name you MUST use VERBATIM: {hls_name}",
        "  (i.e. the original name with `_hls` appended verbatim. Do NOT drop or shorten any prefix/suffix of the original name, even if it contains tokens like `_orig`, `_ref`, `_v1`, etc. The downstream synthesis flow uses this exact name as the top function.)",
    ]
    if pinned_hls_decl:
        parts.extend([
            "",
            "CRITICAL — PINNED `_hls` DECLARATION (use character-for-character):",
            "Your testbench MUST contain the following forward declaration of `_hls` EXACTLY as written below. Do NOT change whitespace, do NOT add or remove `const`, do NOT change pointer/array notation (`T*` vs `T[N]`), do NOT add or remove `extern \"C\"`, do NOT reorder parameters, do NOT rename a typedef to its underlying type. The downstream refactor agent and hidden-TB evaluator will use this exact declaration — any deviation causes link failure.",
            "",
            "```cpp",
            pinned_hls_decl.rstrip(),
            ";",
            "```",
        ])
    if sig_spec_constraint:
        parts.extend([
            "",
            "Additional MACROs and types you MUST preserve from the canonical hidden testbench (this constraint downstream evaluation will use). The `_hls` function name in the spec below MUST already be `{hls_name}`; if it is not, the PINNED DECLARATION above takes priority:",
            "",
            sig_spec_constraint.strip(),
        ])
    parts.extend([
        "",
        "Generate the first testbench. Aim for high line coverage of the original kernel source above. Reply with one ```cpp ... ``` block containing the complete testbench, no commentary outside it.",
    ])
    return "\n".join(parts)


def _stub_request_message() -> str:
    return (
        "Now write a minimal stub HLS implementation that matches the testbench you just produced. "
        "The stub MUST define every `_hls` function declared in the testbench with EXACTLY the same signature, "
        "and each `_hls` MUST delegate to the corresponding original function in a way that makes the testbench's "
        "golden-vs-HLS output comparison always pass. Do NOT include a `main`. Do NOT redeclare the original function "
        "(assume it is linked from `orig_code.cpp`). Reply with one ```cpp ... ``` block containing the complete stub, "
        "no commentary outside it."
    )


def _empty_stub_request_message(hls_name: str) -> str:
    return (
        f"Now write a MINIMAL EMPTY stub HLS implementation of `{hls_name}` (and any other `_hls` function in the testbench) "
        f"with EMPTY/DUMMY bodies. This stub is used to verify that the function SIGNATURE is HLS-synthesizable.\n"
        f"  - The signature MUST match the testbench's declaration EXACTLY.\n"
        f"  - The body must compile and synthesize but does NOT need to be functionally correct. Just return 0 / a default value / nothing (for void).\n"
        f"  - Do NOT include any reference to the original function, no `extern \"C\"` wrappers, no `main`.\n"
        f"  - Do NOT include declarations of `_hls` only (we need full definitions with bodies).\n"
        f"Reply with one ```cpp ... ``` block, no commentary."
    )


def _hls_friendly_rewrite_message(hls_name: str, csynth_err: str) -> str:
    err_excerpt = csynth_err.strip()[-1200:] or "(no detailed error)"
    return (
        f"The `{hls_name}` signature in your previous testbench is NOT HLS-synthesizable. csynth reported:\n"
        f"```\n{err_excerpt}\n```\n\n"
        f"Rewrite the testbench so that the `_hls` declaration is HLS-synthesizable. Apply the following transformations as needed:\n"
        f"  - Function pointers in args → DROP entirely, or replace with `int` enum dispatch.\n"
        f"  - `FILE*`, `std::ostream`, mutex types in args (or as referenced struct fields) → DROP the parameter or shrink the struct.\n"
        f"  - `std::vector`, `std::string`, STL containers → fixed-size arrays parameterized by MACROs.\n"
        f"  - Pointer-to-pointer args → flatten to single pointer + size scalar.\n"
        f"The new `_hls` signature can be ANY HLS-synthesizable shape that lets you still validate the golden function's behavior. "
        f"Keep MACROs and test cases consistent with the new sig. Reply with one ```cpp ... ``` block, no commentary."
    )


def _synth_check(empty_stub_code: str, hls_name: str, work_dir: str) -> Tuple[bool, str]:
    """Run csynth on an empty stub. Returns (passed, error_tail_chars)."""
    os.makedirs(work_dir, exist_ok=True)
    cv = ContextVariables(data={
        "curr_code": empty_stub_code,
        "new_kernel_name": hls_name,
    })
    try:
        status, error_msg = tools.csynth.run_csynth(work_dir, cv, timelimit=SYNTH_CHECK_TIMEOUT)
    except Exception as e:
        return False, f"exception: {type(e).__name__}: {e}"[:1500]
    return status == "succeeded", (error_msg or "")[-1500:]


def _feedback_message(
    round_idx: int,
    prev_cov: float,
    uncovered_lines: List[int],
    annotated_source: str,
    prev_status: str,
    prev_compile_stderr: str = "",
    prev_run_stderr: str = "",
) -> str:
    if prev_status != "ok":
        # Feed back the concrete error so the agent can actually fix the bug
        # (vs. blind retry that repeats the same mistake).
        error_chunk = ""
        if prev_status in ("compile_failed", "compile_timeout") and prev_compile_stderr:
            error_chunk = (
                f"\n\nCompiler error from the previous attempt (last excerpt):\n"
                f"```\n{prev_compile_stderr.strip()[-1500:]}\n```\n"
                f"Diagnose this error and DO NOT repeat the same mistake. "
                f"If a function declaration / stub signature was wrong, fix that first."
            )
        elif prev_status in ("run_timeout",) and prev_run_stderr:
            error_chunk = (
                f"\n\nThe previous testbench timed out at runtime. Trailing stderr (last excerpt):\n"
                f"```\n{prev_run_stderr.strip()[-1500:]}\n```\n"
                f"Make inputs smaller or avoid the runaway code path; the run must finish within the time limit."
            )
        elif prev_status in ("no_gcda", "gcov_failed", "missing_orig_gcov") and prev_run_stderr:
            error_chunk = (
                f"\n\nThe previous testbench likely crashed during execution (no coverage data was emitted). "
                f"Trailing stderr (last excerpt):\n"
                f"```\n{prev_run_stderr.strip()[-1500:]}\n```\n"
                f"Diagnose the crash (heap corruption, OOB access, etc.) and write a NEW testbench whose runs all return normally."
            )
        return (
            f"The previous testbench / stub did not measure cleanly (status: {prev_status})."
            f"{error_chunk}\n\n"
            f"Please write a NEW testbench that compiles cleanly, runs to completion, and exercises as much "
            f"of the original kernel source as possible. Reply with one ```cpp ... ``` block containing the "
            f"complete testbench, no commentary outside it."
        )

    uncovered_summary: str
    if not uncovered_lines:
        uncovered_summary = "All lines were covered."
    elif len(uncovered_lines) <= MAX_LISTED_UNCOVERED:
        uncovered_summary = f"Uncovered line numbers in orig_code.cpp: {uncovered_lines}"
    else:
        uncovered_summary = (
            f"Uncovered line numbers in orig_code.cpp ({len(uncovered_lines)} lines total; first "
            f"{MAX_LISTED_UNCOVERED} shown): {uncovered_lines[:MAX_LISTED_UNCOVERED]}"
        )

    return (
        f"Round {round_idx - 1} testbench achieved {prev_cov:.1f}% line coverage of orig_code.cpp. "
        f"{uncovered_summary}\n\n"
        f"Below is the original source with `// UNCOVERED` markers appended to lines the previous testbench did not exercise. "
        f"Write the next testbench (a new version, not a diff) that ADDITIONALLY covers the marked paths. "
        f"You may add cases, vary inputs, or add seeds; do not remove cases unless they are dominated by others.\n\n"
        f"```cpp\n{annotated_source.rstrip()}\n```\n\n"
        f"Reply with one ```cpp ... ``` block containing the complete next testbench, no commentary outside it."
    )


def _final_text_request(
    best_round_idx: int,
    best_cov: float,
    want_sig_spec: bool,
    expected_hls_name: Optional[str] = None,
) -> str:
    name_constraint = ""
    if expected_hls_name:
        name_constraint = (
            f" CRITICAL: the `_hls` function name in this spec MUST be exactly `{expected_hls_name}` "
            f"— the original kernel name with `_hls` appended verbatim. If the round-{best_round_idx} "
            f"testbench used any other name, the spec you emit MUST use `{expected_hls_name}` instead "
            f"(the downstream synthesis flow uses this exact name as the top function)."
        )
    if want_sig_spec:
        return (
            f"The testbench you produced in round {best_round_idx} had the highest line coverage ({best_cov:.1f}%). "
            f"For THAT testbench, write a self-contained specification message that any OTHER testbench must follow "
            f"to be link-compatible with it. The spec MUST include:\n"
            f"  - All `#define` MACRO declarations from that testbench (verbatim).\n"
            f"  - The full declaration(s) of every `_hls` function the testbench expects (return type, name, "
            f"parameter list, qualifiers), as forward declarations ending with `;`.\n"
            f"{name_constraint}\n"
            f"Format the spec as a single ```cpp ... ``` block, then a short bullet list (no more than 3 bullets) "
            f"of any non-obvious type/size constraints that downstream testbenches must respect. No other commentary."
        )
    else:
        return (
            f"The testbench you produced in round {best_round_idx} had the highest line coverage ({best_cov:.1f}%). "
            f"For THAT testbench, create a VERY SHORT instruction for the future refactoring (at most 4 bullet points). "
            f"You must include the new function signature and the MACROs that have to be passed to the refactoring flow. "
            f"For others, include ONLY necessary constraints/information the new function needs to run properly with this "
            f"testbench. Do NOT speak about function equivalence or HLS synthesizability at this point."
            f"{name_constraint}"
        )


# -------------------------- helpers --------------------------

def _extract_one_cpp_block(content: str) -> str:
    """Extract first code block from response; fall back to raw text."""
    blocks = tools.general.extract_code(tools.general.strip_thinking(content))
    if blocks:
        return blocks[0]
    return content.strip()


def _agent_run_once(agent, message: str, first_turn: bool) -> str:
    """One agent.run() call. first_turn=True clears history; otherwise preserves it."""
    if first_turn:
        resp = agent.run(message=message, max_turns=1)
    else:
        resp = agent.run(message=message, max_turns=1, clear_history=False)
    resp.process()
    return resp.messages[-1]["content"]


# -------------------------- trajectory runner --------------------------

def run_trajectory(
    orig_code: str,
    kernel_name: str,
    K: int,
    target_pct: float,
    sig_spec_constraint: Optional[str],
    llm_config: Optional[Dict[str, Any]],
    want_sig_spec: bool,
    trajectory_idx: int = 0,
    pinned_hls_decl: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one K-round TB+stub iterative trajectory.

    Args:
        orig_code: original kernel source
        kernel_name: kernel function name
        K: max rounds
        target_pct: early-stop coverage threshold
        sig_spec_constraint: if set, injected into round-1 prompt as hard constraint
                             (used by public TB loop; None for hidden TB loop)
        llm_config: optional autogen LLM config override
        want_sig_spec: if True, final text call asks for sig_spec; else asks for instruction
        trajectory_idx: just for logging
    """
    loader = HLSAgentLoader(AGENT_YAML, llm_config_override=llm_config)
    agent = loader.load_agent(AGENT_NAME)
    hls_name = f"{kernel_name}_hls"

    rounds: List[Dict[str, Any]] = []

    # ---- Round 1: TB + delegating stub (the stub is REUSED across all subsequent rounds) ----
    msg = _initial_user_message(orig_code, kernel_name, sig_spec_constraint, pinned_hls_decl=pinned_hls_decl)
    tb_raw = _agent_run_once(agent, msg, first_turn=True)
    tb_code = _extract_one_cpp_block(tb_raw)
    stub_raw = _agent_run_once(agent, _stub_request_message(), first_turn=False)
    delegating_stub = _extract_one_cpp_block(stub_raw)  # reused for all coverage measurements
    cov = measure_coverage(orig_code, tb_code, delegating_stub)
    rounds.append({
        "round": 1,
        "tb_code": tb_code,
        "stub_code": delegating_stub,
        "cov_pct": cov.get("cov_pct"),
        "lines_total": cov.get("lines_total"),
        "lines_hit": cov.get("lines_hit"),
        "uncovered_lines": cov.get("uncovered_lines", []),
        "status": cov.get("status"),
        "compile_stderr": cov.get("compile_stderr", "")[-2000:],
        "run_stderr": cov.get("run_stderr", "")[-2000:],
    })

    # Early stop?
    best_so_far = rounds[0]
    if (best_so_far["cov_pct"] or 0.0) >= target_pct:
        return _finalize_trajectory(agent, rounds, want_sig_spec, trajectory_idx,
                                    expected_hls_name=hls_name, orig_code=orig_code,
                                    sig_spec_constraint=sig_spec_constraint)

    # ---- Rounds 2..K: TB only (REUSE delegating_stub; sig must stay stable) ----
    for k in range(2, K + 1):
        prev = rounds[-1]
        prev_cov = prev["cov_pct"] or 0.0
        prev_status = prev["status"] or "unknown"
        if prev_status == "ok":
            annotated = annotate_uncovered_source(orig_code, prev["uncovered_lines"])
        else:
            annotated = orig_code  # fallback: don't annotate when prior round failed
        fb_msg = _feedback_message(
            k, prev_cov, prev["uncovered_lines"], annotated, prev_status,
            prev_compile_stderr=prev.get("compile_stderr", ""),
            prev_run_stderr=prev.get("run_stderr", ""),
        )
        # Append a hard reminder so the LLM keeps sig stable → delegating_stub stays valid.
        fb_msg += (
            f"\n\nREMINDER: The `{hls_name}` declaration (return type, parameter types/qualifiers, "
            f"and order) and all MACROs MUST stay IDENTICAL to your round-1 testbench. We reuse the "
            f"round-1 stub for coverage measurement; any sig drift will fail compile and waste this round."
        )

        tb_raw = _agent_run_once(agent, fb_msg, first_turn=False)
        tb_code = _extract_one_cpp_block(tb_raw)
        cov = measure_coverage(orig_code, tb_code, delegating_stub)  # ← reuse stub
        rounds.append({
            "round": k,
            "tb_code": tb_code,
            "stub_code": delegating_stub,  # reused
            "cov_pct": cov.get("cov_pct"),
            "lines_total": cov.get("lines_total"),
            "lines_hit": cov.get("lines_hit"),
            "uncovered_lines": cov.get("uncovered_lines", []),
            "status": cov.get("status"),
            "compile_stderr": cov.get("compile_stderr", "")[-2000:],
            "run_stderr": cov.get("run_stderr", "")[-2000:],
        })
        if (cov.get("cov_pct") or 0.0) >= target_pct:
            break

    return _finalize_trajectory(agent, rounds, want_sig_spec, trajectory_idx,
                                expected_hls_name=hls_name, orig_code=orig_code,
                                sig_spec_constraint=sig_spec_constraint)


def _finalize_trajectory(
    agent,
    rounds: List[Dict[str, Any]],
    want_sig_spec: bool,
    trajectory_idx: int,
    expected_hls_name: Optional[str] = None,
    orig_code: Optional[str] = None,
    sig_spec_constraint: Optional[str] = None,
    synth_retry_budget: int = 1,
) -> Dict[str, Any]:
    """Pick best round by cov, run synth check on _hls sig, retry if it fails.

    Adds `synth_ok` and `synth_error` to the returned dict.
    """
    ok_rounds = [r for r in rounds if r["status"] == "ok" and r["cov_pct"] is not None]
    if ok_rounds:
        best = max(ok_rounds, key=lambda r: r["cov_pct"])
    else:
        best = rounds[0]

    # ---- Synth check on best-cov TB ----
    synth_ok = False
    synth_error = ""
    empty_stub = ""
    if expected_hls_name and orig_code is not None:
        retries_left = synth_retry_budget
        while True:
            # Ask the agent for an empty stub matching the CURRENT best testbench.
            empty_raw = _agent_run_once(agent, _empty_stub_request_message(expected_hls_name), first_turn=False)
            empty_stub = _extract_one_cpp_block(empty_raw)
            with tempfile.TemporaryDirectory(prefix=f"synth_check_traj{trajectory_idx}_") as work_dir:
                synth_ok, synth_error = _synth_check(empty_stub, expected_hls_name, work_dir)
            if synth_ok or retries_left <= 0:
                break
            # Retry: ask the LLM to rewrite the testbench with HLS-friendly sig.
            retries_left -= 1
            rewrite_msg = _hls_friendly_rewrite_message(expected_hls_name, synth_error)
            new_tb_raw = _agent_run_once(agent, rewrite_msg, first_turn=False)
            new_tb_code = _extract_one_cpp_block(new_tb_raw)
            # Regenerate the delegating stub for the new sig.
            new_stub_raw = _agent_run_once(agent, _stub_request_message(), first_turn=False)
            new_stub_code = _extract_one_cpp_block(new_stub_raw)
            new_cov = measure_coverage(orig_code, new_tb_code, new_stub_code)
            rounds.append({
                "round": (rounds[-1]["round"] + 1) if rounds else 1,
                "tb_code": new_tb_code,
                "stub_code": new_stub_code,
                "cov_pct": new_cov.get("cov_pct"),
                "lines_total": new_cov.get("lines_total"),
                "lines_hit": new_cov.get("lines_hit"),
                "uncovered_lines": new_cov.get("uncovered_lines", []),
                "status": new_cov.get("status"),
                "compile_stderr": new_cov.get("compile_stderr", "")[-2000:],
                "run_stderr": new_cov.get("run_stderr", "")[-2000:],
                "synth_retry": True,
            })
            # Make this the new "best" candidate (it should pass synth on next loop).
            best = rounds[-1]

    # ---- Final sig_spec / instruction ----
    final_msg = _final_text_request(
        best_round_idx=best["round"],
        best_cov=best.get("cov_pct") or 0.0,
        want_sig_spec=want_sig_spec,
        expected_hls_name=expected_hls_name,
    )
    final_raw = _agent_run_once(agent, final_msg, first_turn=False)
    final_text = tools.general.strip_thinking(final_raw).strip()

    return {
        "trajectory_idx": trajectory_idx,
        "best_round": best["round"],
        "best_cov": best.get("cov_pct") or 0.0,
        "best_tb": best["tb_code"],
        "best_stub": best["stub_code"],
        "best_empty_stub": empty_stub,
        "best_uncovered_lines": best.get("uncovered_lines", []),
        "final_text": final_text,  # instruction or sig_spec depending on want_sig_spec
        "rounds": rounds,
        "synth_ok": synth_ok,
        "synth_error": synth_error,
    }


# -------------------------- public-facing entrypoints --------------------------

def optimize_tb_public(
    orig_code: str,
    kernel_name: str,
    hidden_sig_spec: Optional[str],
    K: int = 3,
    target_pct: float = 80.0,
    llm_config: Optional[Dict[str, Any]] = None,
    pinned_hls_decl: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a coverage-optimized public TB for one refactor attempt.

    Returns dict with keys: best_tb, best_stub, best_cov, instruction (str),
    best_round, rounds (per-round debug info).
    """
    traj = run_trajectory(
        orig_code=orig_code,
        kernel_name=kernel_name,
        K=K,
        target_pct=target_pct,
        sig_spec_constraint=hidden_sig_spec,
        llm_config=llm_config,
        want_sig_spec=False,
        trajectory_idx=0,
        pinned_hls_decl=pinned_hls_decl,
    )
    # If we have a pinned decl, append it to the instruction so the downstream
    # refactor agent also sees it as a hard constraint (not just the public TB).
    instruction = traj["final_text"]
    if pinned_hls_decl:
        instruction = (
            (instruction or "").rstrip()
            + "\n\nPINNED `_hls` DECLARATION — your refactored kernel MUST define a function with this EXACT signature (character-for-character; do NOT change whitespace, const, pointer/array notation, or `extern \"C\"`):\n"
            f"```cpp\n{pinned_hls_decl.rstrip()};\n```\n"
        )
    return {
        "best_tb": traj["best_tb"],
        "best_stub": traj["best_stub"],
        "best_cov": traj["best_cov"],
        "best_round": traj["best_round"],
        "instruction": instruction,
        "new_kernel_name": f"{kernel_name}_hls",
        "rounds": traj["rounds"],
    }


def optimize_tb_seeded(
    orig_code: str,
    kernel_name: str,
    seed_tb: str,
    K: int = 3,
    target_pct: float = 90.0,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Coverage-optimize an EXISTING testbench (e.g., from a paper run) while
    preserving its `_hls` signature + MACROs, for use as a held-out evaluator.

    Key differences vs run_trajectory:
      - Seeded with paper's TB (not generated from scratch). Round 1 prompt asks
        the LLM to produce an *improved* version of the given TB.
      - Stub is generated ONCE in round 1 (right after the first TB) and reused
        for cov measurement in all subsequent rounds (signature is fixed).
      - Returns the best (TB, stub, cov, round) pair across K rounds; no
        sig_spec / instruction final call.

    Args:
        orig_code: original kernel source (for coverage measurement)
        kernel_name: kernel function name (for the canonical-name reminder)
        seed_tb: the existing testbench to improve (typically the paper's TB)
        K: max number of LLM-generated TB iterations
        target_pct: early-stop coverage threshold
        llm_config: optional LLM config override

    Returns dict with:
        best_tb, best_stub, best_cov, best_round, rounds (full trace).
    """
    hls_name = f"{kernel_name}_hls"
    loader = HLSAgentLoader(AGENT_YAML, llm_config_override=llm_config)
    agent = loader.load_agent(AGENT_NAME)

    # Extract the verbatim `_hls(...)` declaration from the seed TB. The pinned
    # decl is injected into every prompt so the LLM can't paraphrase the sig
    # (paraphrasing was the source of the compile_err artifacts in v1 re-eval).
    seed_decl_verbatim = _extract_seed_hls_decl(seed_tb, hls_name)

    # --- Round 1 user message: seed-aware ---
    pin_block = ""
    if seed_decl_verbatim:
        pin_block = (
            "\nCRITICAL — PINNED `_hls` DECLARATION (use character-for-character):\n"
            f"Your improved testbench MUST contain the following forward declaration of `{hls_name}` "
            "EXACTLY as written below. Do NOT change whitespace, do NOT add or remove `const`, "
            "do NOT change pointer/array notation, do NOT add or remove `extern \"C\"`, "
            "do NOT rename typedefs to their underlying types. The downstream eval gate will "
            "link this verbatim declaration with the paper's existing refactor — any deviation "
            "causes undefined-reference link failure.\n\n"
            "```cpp\n"
            f"{seed_decl_verbatim.rstrip()};\n"
            "```\n"
        )

    initial_msg = (
        "Original kernel source code:\n"
        "```cpp\n"
        f"{orig_code.rstrip()}\n"
        "```\n\n"
        f"Top function name (original / golden reference): {kernel_name}\n"
        f"HLS-side function name you MUST use VERBATIM: {hls_name}\n"
        + pin_block +
        "\nBelow is an EXISTING baseline testbench from a previous run. Your job is to produce an "
        "IMPROVED version that achieves higher line coverage of the original kernel source above. "
        "CRITICAL CONSTRAINTS:\n"
        f"  - The HLS-side function declaration (return type, name `{hls_name}`, parameter list) "
        "MUST be IDENTICAL to the one in the baseline / pinned block above (any other signature will fail to link with the downstream code).\n"
        "  - All `#define` MACRO declarations and their values MUST stay IDENTICAL.\n"
        "  - You MAY add more test cases, vary inputs, change seeds, or extend coverage in any other way.\n"
        "  - Do NOT remove test cases that the baseline has, unless they are dominated by new ones.\n\n"
        "Baseline testbench:\n"
        f"```cpp\n{seed_tb.rstrip()}\n```\n\n"
        "Reply with one ```cpp ... ``` block containing the complete improved testbench, no commentary."
    )
    tb_raw = _agent_run_once(agent, initial_msg, first_turn=True)
    tb_code = _extract_one_cpp_block(tb_raw)

    # Generate stub ONCE — signature is fixed across all rounds, so reuse it.
    stub_raw = _agent_run_once(agent, _stub_request_message(), first_turn=False)
    stub_code = _extract_one_cpp_block(stub_raw)

    rounds: List[Dict[str, Any]] = []
    cov = measure_coverage(orig_code, tb_code, stub_code)
    rounds.append({
        "round": 1,
        "tb_code": tb_code,
        "stub_code": stub_code,
        "cov_pct": cov.get("cov_pct"),
        "lines_total": cov.get("lines_total"),
        "lines_hit": cov.get("lines_hit"),
        "uncovered_lines": cov.get("uncovered_lines", []),
        "status": cov.get("status"),
        "compile_stderr": cov.get("compile_stderr", "")[-2000:],
        "run_stderr": cov.get("run_stderr", "")[-2000:],
    })

    # Early stop?
    if (rounds[0]["cov_pct"] or 0.0) >= target_pct:
        return _pick_best_seeded(rounds)

    # --- Rounds 2..K: TB only (stub is reused) ---
    for k in range(2, K + 1):
        prev = rounds[-1]
        prev_cov = prev["cov_pct"] or 0.0
        prev_status = prev["status"] or "unknown"
        annotated = (annotate_uncovered_source(orig_code, prev["uncovered_lines"])
                     if prev_status == "ok" else orig_code)
        fb_msg = _feedback_message(
            k, prev_cov, prev["uncovered_lines"], annotated, prev_status,
            prev_compile_stderr=prev.get("compile_stderr", ""),
            prev_run_stderr=prev.get("run_stderr", ""),
        )
        # Append a hard reminder to preserve the signature so the round-1 stub stays compatible.
        fb_msg += (
            f"\n\nREMINDER: The HLS-side `{hls_name}` declaration and all MACROs must remain "
            "IDENTICAL to your round-1 testbench. We reuse the round-1 stub for measurement; "
            "any signature drift will fail compile and waste this round."
        )
        if seed_decl_verbatim:
            fb_msg += (
                "\n\nPINNED `_hls` DECLARATION (use character-for-character — same as in your round 1):\n"
                "```cpp\n"
                f"{seed_decl_verbatim.rstrip()};\n"
                "```"
            )
        tb_raw = _agent_run_once(agent, fb_msg, first_turn=False)
        tb_code = _extract_one_cpp_block(tb_raw)
        cov = measure_coverage(orig_code, tb_code, stub_code)  # ← reuse stub_code!
        rounds.append({
            "round": k,
            "tb_code": tb_code,
            "stub_code": stub_code,  # same as round 1
            "cov_pct": cov.get("cov_pct"),
            "lines_total": cov.get("lines_total"),
            "lines_hit": cov.get("lines_hit"),
            "uncovered_lines": cov.get("uncovered_lines", []),
            "status": cov.get("status"),
            "compile_stderr": cov.get("compile_stderr", "")[-2000:],
            "run_stderr": cov.get("run_stderr", "")[-2000:],
        })
        if (cov.get("cov_pct") or 0.0) >= target_pct:
            break

    return _pick_best_seeded(rounds)


def _pick_best_seeded(rounds: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rounds if r["status"] == "ok" and r["cov_pct"] is not None]
    if ok:
        best = max(ok, key=lambda r: r["cov_pct"])
    else:
        best = rounds[0]
    return {
        "best_tb": best["tb_code"],
        "best_stub": best["stub_code"],
        "best_cov": best.get("cov_pct") or 0.0,
        "best_round": best["round"],
        "rounds": rounds,
    }


def gen_tb_with_coverage(
    cv,
    llm_config: Optional[Dict[str, Any]] = None,
    K: int = 3,
    target_pct: float = 80.0,
    hidden_sig_spec: Optional[str] = None,
    pinned_hls_decl: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Drop-in replacement for tools.testbench.gen_tb_prior with the coverage loop.

    Returns (testbench, refactor_instruction, new_kernel_name) matching the
    existing gen_tb_prior signature so the call site in flow/new.py can swap
    behind a flag.
    """
    result = optimize_tb_public(
        orig_code=cv["orig_code"],
        kernel_name=cv["kernel_name"],
        hidden_sig_spec=hidden_sig_spec,
        K=K,
        target_pct=target_pct,
        llm_config=llm_config,
        pinned_hls_decl=pinned_hls_decl,
    )
    return result["best_tb"], result["instruction"], result["new_kernel_name"]


def make_golden_hidden_tb(
    orig_code: str,
    kernel_name: str,
    M: int = 3,
    K: int = 6,
    target_pct: float = 90.0,
    llm_config: Optional[Dict[str, Any]] = None,
    cache_dir: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate (or load from cache) the golden hidden TB for a kernel.

    Runs M trajectories in parallel, each K rounds; picks the trajectory with
    highest max-cov; returns that trajectory's best TB + its sig_spec.

    Args:
        cache_key: optional override for the cache filename (default: kernel_name).
                   Use a unique-per-kernel value (e.g., kernel_name_suffix) when
                   multiple kernels share the same `kernel_name` (e.g., several
                   hetero kernels all use `process_top`).

    Returns dict with keys: hidden_tb, hidden_sig_spec, hidden_stub,
    hidden_cov, best_trajectory, best_round, trajectories (debug), orig_sha.
    """
    orig_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()
    cache_key = cache_key or kernel_name

    if cache_dir is not None:
        cached = _load_golden_cache(cache_dir, cache_key, orig_sha)
        if cached is not None:
            return cached

    with concurrent.futures.ThreadPoolExecutor(max_workers=M) as ex:
        futures = {
            ex.submit(
                run_trajectory,
                orig_code=orig_code,
                kernel_name=kernel_name,
                K=K,
                target_pct=target_pct,
                sig_spec_constraint=None,
                llm_config=llm_config,
                want_sig_spec=True,
                trajectory_idx=m,
            ): m
            for m in range(M)
        }
        trajectories: List[Dict[str, Any]] = []
        for fut in concurrent.futures.as_completed(futures):
            try:
                trajectories.append(fut.result())
            except Exception as e:
                # Don't lose the whole golden TB to a single trajectory crash.
                trajectories.append({
                    "trajectory_idx": futures[fut],
                    "best_cov": 0.0,
                    "best_tb": "",
                    "best_stub": "",
                    "best_round": -1,
                    "final_text": "",
                    "rounds": [],
                    "error": f"{type(e).__name__}: {e}",
                })

    trajectories.sort(key=lambda t: t.get("trajectory_idx", 0))
    # Pick by (synth_ok=True FIRST, then max cov). If no trajectory has synth_ok,
    # fall back to max-cov but flag synth_ok=False in the result.
    best = max(
        trajectories,
        key=lambda t: (1 if t.get("synth_ok") else 0, t.get("best_cov", 0.0)),
    )

    result = {
        "kernel_name": kernel_name,
        "orig_sha256": orig_sha,
        "hidden_tb": best["best_tb"],
        "hidden_stub": best["best_stub"],
        "hidden_empty_stub": best.get("best_empty_stub", ""),
        "hidden_sig_spec": best["final_text"],
        "hidden_cov": best["best_cov"],
        "best_trajectory": best.get("trajectory_idx", -1),
        "best_round": best["best_round"],
        "synth_ok": bool(best.get("synth_ok", False)),
        "synth_error": best.get("synth_error", "") or "",
        "trajectories": trajectories,
    }

    if cache_dir is not None:
        _write_golden_cache(cache_dir, cache_key, result)

    return result


# -------------------------- golden TB cache --------------------------

def _golden_cache_path(cache_dir: str, kernel_name: str) -> str:
    return os.path.join(cache_dir, f"{kernel_name}.json")


def _load_golden_cache(cache_dir: str, kernel_name: str, expected_sha: str) -> Optional[Dict[str, Any]]:
    path = _golden_cache_path(cache_dir, kernel_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if data.get("orig_sha256") != expected_sha:
        return None
    return data


def _write_golden_cache(cache_dir: str, kernel_name: str, result: Dict[str, Any]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    path = _golden_cache_path(cache_dir, kernel_name)
    # Strip per-round artifacts to keep cache files small? Keep them for now —
    # useful for debugging "why did hidden TB cover X% only".
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)  # atomic on POSIX
