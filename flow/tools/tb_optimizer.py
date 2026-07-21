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
import re
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
        "Generate one complete, normal-strength testbench now; do not emit a preliminary or simplified version. Aim for high line coverage while respecting every explicit macro, fixed array capacity, memory limit, and interface constraint in the source.",
        "Before writing calls, inspect the original for mutable global/static state, heap-backed structures, allocator state, counters, and other state that survives a return. The original and `_hls` sides must start from equivalent clean logical states and must use separate mutable input/output storage.",
        "Reset all relevant explicit state immediately before EACH side is invoked. If a complete reset cannot be established, do not call the original repeatedly; use one representative original invocation and design the testbench for a non-delegating matching stub. State safety takes priority over testcase count or marginal coverage.",
        "When declaring the original golden function, preserve its C/C++ language linkage exactly as shown in the source. Never add or remove `extern \"C\"`; a linkage mismatch causes an undefined reference even when the parameter list looks identical.",
        "Reply with one ```cpp ... ``` block containing the complete testbench, no commentary outside it.",
    ])
    return "\n".join(parts)


def _stub_request_message(
    kernel_name: Optional[str] = None,
    pinned_hls_decl: Optional[str] = None,
) -> str:
    original_clause = (
        f"for the original `{kernel_name}` function "
        if kernel_name
        else "for every original function it calls "
    )
    pinned_clause = ""
    if pinned_hls_decl:
        pinned_clause = (
            "\n\nCRITICAL — EXACT `_hls` DEFINITION HEADER:\n"
            "Use the declaration below character-for-character as the "
            "definition header, replacing only its trailing `;` with the "
            "function body. Preserve `extern \"C\"` presence or absence, "
            "return type, function name, parameters, and qualifiers exactly.\n"
            "```cpp\n"
            + pinned_hls_decl.rstrip().rstrip(";")
            + ";\n```"
        )
    return (
        "Now write a minimal stub HLS implementation that matches the testbench you just produced. "
        "The stub MUST define every `_hls` function declared in the testbench with EXACTLY the same "
        "signature and C/C++ language linkage. Delegation to the corresponding original function is "
        "CONDITIONAL, not mandatory. Delegate only if the original is stateless/reentrant, or if the "
        "testbench establishes a complete clean state immediately before the delegated `_hls` call. "
        "Never delegate as a second execution over shared mutable global, static, heap-backed, "
        "allocator, pointer, tree, queue, counter, or mutated-buffer state. If safe delegation cannot "
        "be established, write an independent minimal stub that matches the tested observable behavior "
        "and does not call or copy the original implementation. Do NOT include a `main`. If the stub "
        "calls the original, it is compiled in a separate translation unit from `orig_code.cpp`, so it "
        "MUST contain a forward declaration ending in `;` "
        + original_clause
        + "before calling it."
        + pinned_clause
        + "\nReply with exactly one complete ```cpp ... ``` block and no commentary."
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
        elif prev_status == "run_failed" and prev_run_stderr:
            error_chunk = (
                f"\n\nThe previous testbench returned a non-zero status, so it is not qualified. "
                f"Trailing stderr (last excerpt):\n"
                f"```\n{prev_run_stderr.strip()[-1500:]}\n```\n"
                f"Fix the golden-vs-HLS mismatch; coverage alone is not sufficient."
            )
        elif prev_status == "qualification_failed" and prev_run_stderr:
            error_chunk = (
                f"\n\nThe previous testbench failed the lightweight pre-compile qualification gate:\n"
                f"```\n{prev_run_stderr.strip()[-1500:]}\n```\n"
                f"Respect every reported capacity, language-linkage, and persistent-state constraint. Do not repeatedly call a stateful original or delegate the `_hls` stub back to it unless a complete reset is explicitly verified."
            )
        elif prev_status in ("no_gcda", "gcov_failed", "missing_orig_gcov") and prev_run_stderr:
            error_chunk = (
                f"\n\nThe previous testbench likely crashed during execution (no coverage data was emitted). "
                f"Trailing stderr (last excerpt):\n"
                f"```\n{prev_run_stderr.strip()[-1500:]}\n```\n"
                f"Diagnose the crash (heap corruption, OOB access, etc.). Before merely shrinking inputs, explicitly check whether the original was invoked repeatedly or again through a delegating stub while global/static/heap/allocator state, pointers, trees, queues, counters, or mutated buffers were shared. Regenerate the testbench and matching stub so each side starts from an equivalent clean state; otherwise eliminate repeated original calls or unsafe delegation. Write a NEW testbench whose runs all return normally."
            )
        return (
            f"The previous testbench / stub did not measure cleanly (status: {prev_status})."
            f"{error_chunk}\n\n"
            f"Please write a NEW testbench that compiles cleanly, runs to completion, preserves equivalent clean state and independent mutable storage for the original and `_hls` sides, and exercises as much "
            f"of the original kernel source as possible without violating declared bounds. Reply with one ```cpp ... ``` block containing the "
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
        f"You may add cases, vary inputs, or add seeds; do not remove cases unless they are dominated by others. Preserve state safety: do not add repeated original calls unless every persistent state component is reset immediately before each invocation, and do not introduce unsafe original delegation in the matching stub.\n\n"
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

class ModelArtifactError(ValueError):
    pass


_CPP_FENCE_RE = re.compile(
    r"```\s*(?:cpp|c\+\+|hpp|h\+\+)\s*(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_PROMPT_ECHO_MARKERS = (
    "Original kernel source code:",
    "Top function name (original / golden reference):",
    "Reply with one ```cpp",
    "userOriginal kernel source code",
)
_ARTIFACT_RESPONSE_RETRIES = 1
_REPEATED_FAILURE_LIMIT = 2


def _extract_one_cpp_block(
    content: str,
    *,
    artifact_kind: str = "cpp",
    required_symbol: Optional[str] = None,
) -> str:
    if not isinstance(content, str):
        raise ModelArtifactError("model response content must be a string")
    cleaned = tools.general.strip_thinking(content).strip()
    if not cleaned:
        raise ModelArtifactError("model response is empty")
    matches = list(_CPP_FENCE_RE.finditer(cleaned))
    if len(matches) != 1:
        raise ModelArtifactError(
            "model response must contain exactly one fenced C++ block"
        )
    match = matches[0]
    outside = (cleaned[: match.start()] + cleaned[match.end() :]).strip()
    if outside:
        raise ModelArtifactError(
            "model response contains text outside the C++ block"
        )
    code = match.group(1).strip()
    if not code:
        raise ModelArtifactError("model returned an empty C++ block")
    for marker in _PROMPT_ECHO_MARKERS:
        if marker in code:
            raise ModelArtifactError(
                "model response contains prompt text instead of C++"
            )
    if artifact_kind == "testbench":
        if not re.search(r"\b(?:int|auto)\s+main\s*\(", code):
            raise ModelArtifactError("testbench artifact must define main")
        if required_symbol and not re.search(
            rf"\b{re.escape(required_symbol)}\s*\(",
            code,
        ):
            raise ModelArtifactError(
                f"testbench artifact does not reference {required_symbol}"
            )
    elif artifact_kind in {"stub", "empty_stub"}:
        if re.search(r"\bmain\s*\(", code):
            raise ModelArtifactError("stub artifact must not define main")
        if required_symbol and not re.search(
            rf"\b{re.escape(required_symbol)}\s*\([^;{{}}]*\)\s*\{{",
            code,
            re.DOTALL,
        ):
            raise ModelArtifactError(
                f"stub artifact must define {required_symbol}"
            )
    return code + "\n"



def _agent_run_once(agent, message: str, first_turn: bool) -> str:
    if first_turn:
        response = agent.run(message=message, max_turns=1)
    else:
        response = agent.run(
            message=message,
            max_turns=1,
            clear_history=False,
        )
    response.process()
    messages = getattr(response, "messages", None)
    if not isinstance(messages, list) or not messages:
        raise ModelArtifactError("agent response contains no messages")
    terminal = messages[-1]
    if not isinstance(terminal, dict):
        raise ModelArtifactError("agent terminal message must be a mapping")
    content = terminal.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelArtifactError(
            "agent terminal assistant message is empty"
        )
    return content


def _request_cpp_artifact(
    agent,
    message: str,
    *,
    first_turn: bool,
    artifact_kind: str,
    required_symbol: Optional[str],
) -> str:
    current_message = message
    current_first_turn = first_turn
    last_error: Exception | None = None
    for attempt in range(_ARTIFACT_RESPONSE_RETRIES + 1):
        try:
            raw = _agent_run_once(
                agent,
                current_message,
                current_first_turn,
            )
            return _extract_one_cpp_block(
                raw,
                artifact_kind=artifact_kind,
                required_symbol=required_symbol,
            )
        except ModelArtifactError as exc:
            last_error = exc
            if attempt >= _ARTIFACT_RESPONSE_RETRIES:
                break
            current_first_turn = False
            current_message = (
                "Your previous response was invalid: "
                f"{exc}. Return exactly one complete fenced ```cpp block "
                "for the requested artifact, with no other text."
            )
    raise ModelArtifactError(
        f"model did not return a valid {artifact_kind} artifact"
    ) from last_error


def _ensure_original_forward_declaration(
    stub_code: str,
    orig_code: str,
    kernel_name: str,
) -> str:
    call_re = re.compile(rf"\b{re.escape(kernel_name)}\s*\(")
    declaration_re = re.compile(
        rf"(?m)^\s*(?:extern\s+\"C\"\s+)?"
        r"(?:[\w:<>,*&]+\s+)+"
        rf"{re.escape(kernel_name)}\s*\([^;{{}}]*\)\s*[;{{]",
        re.DOTALL,
    )
    if not call_re.search(stub_code):
        return stub_code
    if declaration_re.search(stub_code):
        return stub_code
    declaration = _extract_seed_hls_decl(orig_code, kernel_name)
    if not declaration:
        raise ModelArtifactError(
            f"could not derive original declaration for {kernel_name}"
        )
    return declaration.rstrip() + ";\n\n" + stub_code.lstrip()


_SIZE_PARAMETER_NAMES = {
    "n", "len", "length", "size", "count",
    "num", "num_items", "num_elements",
}


def _obvious_capacity_conflicts(
    orig_code: str,
    tb_code: str,
    kernel_name: str,
) -> List[str]:
    # Deliberately recognizes only simple direct calls and fixed arrays.
    declaration = _extract_seed_hls_decl(orig_code, kernel_name)
    if not declaration or "(" not in declaration:
        return []

    params = declaration.split("(", 1)[1].rsplit(")", 1)[0].split(",")
    size_index: Optional[int] = None
    pointer_indices: List[int] = []
    for index, parameter in enumerate(params):
        match = re.search(r"([A-Za-z_]\w*)\s*$", parameter.strip())
        if not match:
            continue
        name = match.group(1).lower()
        if size_index is None and (
            name in _SIZE_PARAMETER_NAMES
            or name.endswith(("_size", "_count", "_length"))
        ):
            size_index = index
        if "*" in parameter or "[" in parameter:
            pointer_indices.append(index)
    if size_index is None or not pointer_indices:
        return []

    values: Dict[str, int] = {}

    def resolve(token: str) -> Optional[int]:
        token = re.sub(r"[uUlL]+$", "", token.strip().strip("()"))
        if re.fullmatch(r"[+-]?\d+", token):
            return int(token)
        return values.get(token)

    pairs = re.findall(
        r"(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\s+"
        r"([A-Za-z_]\w*|[+-]?\d+[uUlL]*)\s*$",
        tb_code,
    ) + re.findall(
        r"\b(?:const\s+)?(?:unsigned\s+|signed\s+)?"
        r"(?:int|long|size_t|std::size_t)\s+([A-Za-z_]\w*)\s*=\s*"
        r"([A-Za-z_]\w*|[+-]?\d+[uUlL]*)\s*;",
        tb_code,
    )
    for _ in range(2):
        for name, raw in pairs:
            value = resolve(raw)
            if value is not None:
                values[name] = value

    capacities: Dict[str, int] = {}
    for name, raw in re.findall(
        r"(?m)^\s*(?:const\s+)?(?:[\w:<>]+\s+)+"
        r"([A-Za-z_]\w*)\s*\[\s*"
        r"([A-Za-z_]\w*|[+-]?\d+[uUlL]*)\s*\]",
        tb_code,
    ):
        value = resolve(raw)
        if value is not None and value >= 0:
            capacities[name] = value

    errors: List[str] = []
    for call in re.findall(
        rf"\b{re.escape(kernel_name)}\s*\(([^()]*)\)\s*;",
        tb_code,
    ):
        arguments = [item.strip() for item in call.split(",")]
        if size_index >= len(arguments):
            continue
        requested = resolve(arguments[size_index])
        if requested is None or requested < 0:
            continue
        for index in pointer_indices:
            if index >= len(arguments):
                continue
            argument = arguments[index].lstrip("&*").strip()
            capacity = capacities.get(argument)
            if capacity is not None and requested > capacity:
                errors.append(
                    f"{kernel_name} requests {requested} elements but "
                    f"array {argument} has fixed capacity {capacity}"
                )
    return sorted(set(errors))


def _obvious_linkage_conflicts(
    orig_code: str,
    tb_code: str,
    stub_code: str,
    kernel_name: str,
) -> List[str]:
    hls_name = f"{kernel_name}_hls"

    def extern_c(declaration: str) -> bool:
        return bool(
            re.search(r'\bextern\s+"C"', declaration or "")
        )

    original_decl = _extract_seed_hls_decl(orig_code, kernel_name)
    tb_original_decl = _extract_seed_hls_decl(tb_code, kernel_name)
    tb_hls_decl = _extract_seed_hls_decl(tb_code, hls_name)
    stub_hls_decl = _extract_seed_hls_decl(stub_code, hls_name)

    errors: List[str] = []
    if (
        original_decl
        and tb_original_decl
        and extern_c(original_decl) != extern_c(tb_original_decl)
    ):
        errors.append(
            f"testbench changes C/C++ language linkage of original "
            f"{kernel_name}"
        )
    if (
        tb_hls_decl
        and stub_hls_decl
        and extern_c(tb_hls_decl) != extern_c(stub_hls_decl)
    ):
        errors.append(
            f"stub changes C/C++ language linkage of {hls_name} "
            "relative to the testbench declaration"
        )
    return errors


def _obvious_persistent_state_markers(orig_code: str) -> List[str]:
    # Return only obvious mutable file-scope or function-static state.
    source = re.sub(r"/\*.*?\*/", "", orig_code, flags=re.DOTALL)
    markers: List[str] = []
    depth = 0
    ignored = (
        "#",
        "typedef ",
        "using ",
        "extern ",
        "const ",
        "constexpr ",
        "struct ",
        "class ",
        "enum ",
        "union ",
        "namespace ",
        "template ",
        "static_assert",
    )

    for lineno, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue

        if (
            depth == 0
            and line.endswith(";")
            and "(" not in line
            and not line.startswith(ignored)
            and not re.search(r"\b(?:const|constexpr)\b", line)
            and re.match(
                r"^(?:static\s+)?"
                r"(?:unsigned\s+|signed\s+|long\s+|short\s+|volatile\s+)*"
                r"[\w:<>]+\s+.+;$",
                line,
            )
        ):
            markers.append(
                f"line {lineno}: mutable file-scope declaration"
            )
        elif (
            depth > 0
            and line.endswith(";")
            and re.search(r"\bstatic\b", line)
            and not re.search(r"\b(?:const|constexpr)\b", line)
        ):
            markers.append(
                f"line {lineno}: mutable function-static declaration"
            )

        depth = max(0, depth + line.count("{") - line.count("}"))

    return markers[:8]


def _obvious_state_safety_conflicts(
    orig_code: str,
    tb_code: str,
    stub_code: str,
    kernel_name: str,
) -> List[str]:
    markers = _obvious_persistent_state_markers(orig_code)
    if not markers:
        return []

    call_re = re.compile(rf"\b{re.escape(kernel_name)}\s*\(")

    def direct_call_positions(code: str) -> Tuple[str, List[int]]:
        source = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        positions: List[int] = []
        for match in call_re.finditer(source):
            line_start = source.rfind("\n", 0, match.start()) + 1
            prefix = source[line_start:match.start()]
            if re.fullmatch(
                r"\s*(?:extern\s+\"C\"\s+)?"
                r"(?:[\w:<>,*&]+\s+)+",
                prefix,
            ):
                continue
            positions.append(match.start())
        return source, positions

    def matching_close(
        source: str,
        start: int,
        opening: str,
        closing: str,
    ) -> Optional[int]:
        depth = 0
        for index in range(start, len(source)):
            if source[index] == opening:
                depth += 1
            elif source[index] == closing:
                depth -= 1
                if depth == 0:
                    return index
        return None

    def call_is_in_obvious_loop(
        source: str,
        positions: List[int],
    ) -> bool:
        for loop in re.finditer(r"\b(?:for|while)\s*\(", source):
            opening = source.find("(", loop.start())
            closing = matching_close(source, opening, "(", ")")
            if closing is None:
                continue
            body_start = closing + 1
            while (
                body_start < len(source)
                and source[body_start].isspace()
            ):
                body_start += 1
            if body_start >= len(source):
                continue
            if source[body_start] == "{":
                body_end = matching_close(
                    source,
                    body_start,
                    "{",
                    "}",
                )
            else:
                body_end = source.find(";", body_start)
            if body_end is None or body_end < 0:
                continue
            if any(body_start <= pos <= body_end for pos in positions):
                return True

        for loop in re.finditer(r"\bdo\b", source):
            body_start = loop.end()
            while (
                body_start < len(source)
                and source[body_start].isspace()
            ):
                body_start += 1
            if body_start >= len(source):
                continue
            if source[body_start] == "{":
                body_end = matching_close(
                    source,
                    body_start,
                    "{",
                    "}",
                )
            else:
                body_end = source.find(";", body_start)
            if body_end is None or body_end < 0:
                continue
            if any(body_start <= pos <= body_end for pos in positions):
                return True
        return False

    errors: List[str] = []
    state_hint = markers[0]
    _stub_source, stub_positions = direct_call_positions(stub_code)
    tb_source, tb_positions = direct_call_positions(tb_code)

    if stub_positions:
        errors.append(
            f"stub delegates to stateful original {kernel_name}; "
            f"{state_hint}"
        )
    if call_is_in_obvious_loop(tb_source, tb_positions):
        errors.append(
            f"testbench calls stateful original {kernel_name} inside "
            f"an obvious loop without a verified reset; {state_hint}"
        )
    elif len(tb_positions) > 1:
        errors.append(
            f"testbench calls stateful original {kernel_name} "
            f"{len(tb_positions)} times without a verified reset; "
            f"{state_hint}"
        )
    return errors


def _measure_qualified_coverage(
    orig_code: str,
    tb_code: str,
    stub_code: str,
    kernel_name: str,
) -> Dict[str, Any]:
    errors = _obvious_capacity_conflicts(
        orig_code,
        tb_code,
        kernel_name,
    )
    errors.extend(
        _obvious_linkage_conflicts(
            orig_code,
            tb_code,
            stub_code,
            kernel_name,
        )
    )
    errors.extend(
        _obvious_state_safety_conflicts(
            orig_code,
            tb_code,
            stub_code,
            kernel_name,
        )
    )
    errors = sorted(set(errors))
    if not errors:
        result = measure_coverage(orig_code, tb_code, stub_code)
        result.setdefault("qualification_errors", [])
        return result
    return {
        "status": "qualification_failed",
        "cov_pct": None,
        "lines_total": None,
        "lines_hit": None,
        "uncovered_lines": [],
        "run_returncode": None,
        "compile_stderr": "",
        "run_stderr": (
            "Lightweight testbench qualification failed:\n"
            + "\n".join(f"- {error}" for error in errors)
        ),
        "qualification_errors": errors,
    }


def _coverage_failure_fingerprint(record: Dict[str, Any]) -> str:
    diagnostic = (
        str(record.get("status") or "unknown")
        + "\n"
        + str(record.get("compile_stderr") or "")
        + "\n"
        + str(record.get("run_stderr") or "")
    )
    diagnostic = re.sub(r"/tmp/[^\s:]+", "/tmp/<path>", diagnostic)
    diagnostic = re.sub(r"\s+", " ", diagnostic).strip()
    return hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()


def _repeated_failure(rounds: List[Dict[str, Any]]) -> bool:
    failed = [
        record
        for record in rounds
        if record.get("status") != "ok"
    ]
    if len(failed) < _REPEATED_FAILURE_LIMIT:
        return False
    recent = failed[-_REPEATED_FAILURE_LIMIT:]
    return len(
        {_coverage_failure_fingerprint(record) for record in recent}
    ) == 1


def _persist_round_artifacts(
    trajectory_idx: int,
    record: Dict[str, Any],
) -> None:
    root = os.getenv("AGREFACTOR_TB_DEBUG_DIR")
    if not root:
        return
    round_root = os.path.join(
        root,
        f"trajectory_{trajectory_idx:03d}",
        f"round_{int(record['round']):03d}",
    )
    os.makedirs(round_root, exist_ok=True)
    with open(
        os.path.join(round_root, "testbench.cpp"),
        "w",
        encoding="utf-8",
    ) as file:
        file.write(str(record.get("tb_code") or ""))
    with open(
        os.path.join(round_root, "stub.cpp"),
        "w",
        encoding="utf-8",
    ) as file:
        file.write(str(record.get("stub_code") or ""))
    safe_record = {
        key: value
        for key, value in record.items()
        if key not in {"tb_code", "stub_code"}
    }
    with open(
        os.path.join(round_root, "coverage.json"),
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            safe_record,
            file,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )


def _append_round(
    rounds: List[Dict[str, Any]],
    *,
    trajectory_idx: int,
    round_index: int,
    tb_code: str,
    stub_code: str,
    cov: Dict[str, Any],
    **extra: Any,
) -> Dict[str, Any]:
    record = {
        "round": round_index,
        "tb_code": tb_code,
        "stub_code": stub_code,
        "cov_pct": cov.get("cov_pct"),
        "lines_total": cov.get("lines_total"),
        "lines_hit": cov.get("lines_hit"),
        "uncovered_lines": cov.get("uncovered_lines", []),
        "status": cov.get("status"),
        "run_returncode": cov.get("run_returncode"),
        "compile_stderr": cov.get("compile_stderr", "")[-2000:],
        "run_stderr": cov.get("run_stderr", "")[-2000:],
        "qualification_errors": list(
            cov.get("qualification_errors", [])
        ),
        **extra,
    }
    rounds.append(record)
    _persist_round_artifacts(trajectory_idx, record)
    return record



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
    if isinstance(K, bool) or not isinstance(K, int) or K < 1:
        raise ValueError("K must be a positive integer")
    loader = HLSAgentLoader(
        AGENT_YAML,
        llm_config_override=llm_config,
    )
    agent = loader.load_agent(AGENT_NAME)
    hls_name = f"{kernel_name}_hls"
    rounds: List[Dict[str, Any]] = []

    tb_code = _request_cpp_artifact(
        agent,
        _initial_user_message(
            orig_code,
            kernel_name,
            sig_spec_constraint,
            pinned_hls_decl=pinned_hls_decl,
        ),
        first_turn=True,
        artifact_kind="testbench",
        required_symbol=hls_name,
    )
    stub_code = _request_cpp_artifact(
        agent,
        _stub_request_message(
            kernel_name,
            _extract_seed_hls_decl(tb_code, hls_name),
        ),
        first_turn=False,
        artifact_kind="stub",
        required_symbol=hls_name,
    )
    stub_code = _ensure_original_forward_declaration(
        stub_code,
        orig_code,
        kernel_name,
    )
    coverage = _measure_qualified_coverage(
        orig_code,
        tb_code,
        stub_code,
        kernel_name,
    )
    _append_round(
        rounds,
        trajectory_idx=trajectory_idx,
        round_index=1,
        tb_code=tb_code,
        stub_code=stub_code,
        cov=coverage,
    )

    for round_index in range(2, K + 1):
        if (rounds[-1].get("cov_pct") or 0.0) >= target_pct:
            break
        if _repeated_failure(rounds):
            rounds[-1]["early_stop_reason"] = (
                "repeated_identical_coverage_failure"
            )
            break
        previous = rounds[-1]
        previous_status = previous.get("status") or "unknown"
        annotated = (
            annotate_uncovered_source(
                orig_code,
                previous.get("uncovered_lines", []),
            )
            if previous_status == "ok"
            else orig_code
        )
        feedback = _feedback_message(
            round_index,
            previous.get("cov_pct") or 0.0,
            previous.get("uncovered_lines", []),
            annotated,
            previous_status,
            prev_compile_stderr=previous.get(
                "compile_stderr",
                "",
            ),
            prev_run_stderr=previous.get("run_stderr", ""),
        )
        feedback += (
            f"\n\nREMINDER: The `{hls_name}` declaration and all MACROs "
            "must remain compatible. A new matching stub will be generated "
            "for this round."
        )
        tb_code = _request_cpp_artifact(
            agent,
            feedback,
            first_turn=False,
            artifact_kind="testbench",
            required_symbol=hls_name,
        )
        stub_code = _request_cpp_artifact(
            agent,
            _stub_request_message(
                kernel_name,
                _extract_seed_hls_decl(tb_code, hls_name),
            ),
            first_turn=False,
            artifact_kind="stub",
            required_symbol=hls_name,
        )
        stub_code = _ensure_original_forward_declaration(
            stub_code,
            orig_code,
            kernel_name,
        )
        coverage = _measure_qualified_coverage(
            orig_code,
            tb_code,
            stub_code,
            kernel_name,
        )
        _append_round(
            rounds,
            trajectory_idx=trajectory_idx,
            round_index=round_index,
            tb_code=tb_code,
            stub_code=stub_code,
            cov=coverage,
        )

    return _finalize_trajectory(
        agent,
        rounds,
        want_sig_spec,
        trajectory_idx,
        expected_hls_name=hls_name,
        orig_code=orig_code,
        sig_spec_constraint=sig_spec_constraint,
    )



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
    del sig_spec_constraint
    ok_rounds = [
        record
        for record in rounds
        if record.get("status") == "ok"
        and record.get("cov_pct") is not None
    ]
    if not ok_rounds:
        last = rounds[-1]
        return {
            "trajectory_idx": trajectory_idx,
            "best_round": last["round"],
            "best_cov": 0.0,
            "best_tb": last["tb_code"],
            "best_stub": last["stub_code"],
            "best_empty_stub": "",
            "best_uncovered_lines": [],
            "final_text": "",
            "rounds": rounds,
            "synth_ok": False,
            "synth_error": (
                last.get("compile_stderr")
                or last.get("run_stderr")
                or "coverage qualification failed"
            ),
            "qualified": False,
            "trajectory_status": "coverage_failed",
        }

    best = max(ok_rounds, key=lambda record: record["cov_pct"])
    synth_ok = False
    synth_error = ""
    empty_stub = ""

    if expected_hls_name and orig_code is not None:
        retries_left = synth_retry_budget
        while True:
            message = _empty_stub_request_message(expected_hls_name)
            best_decl = _extract_seed_hls_decl(
                best["tb_code"],
                expected_hls_name,
            )
            if best_decl:
                message += (
                    "\n\nUse this exact declaration:\n```cpp\n"
                    + best_decl.rstrip()
                    + ";\n```"
                )
            empty_stub = _request_cpp_artifact(
                agent,
                message,
                first_turn=False,
                artifact_kind="empty_stub",
                required_symbol=expected_hls_name,
            )
            with tempfile.TemporaryDirectory(
                prefix=f"synth_check_traj{trajectory_idx}_"
            ) as work_dir:
                synth_ok, synth_error = _synth_check(
                    empty_stub,
                    expected_hls_name,
                    work_dir,
                )
            if synth_ok or retries_left <= 0:
                break
            retries_left -= 1
            new_tb = _request_cpp_artifact(
                agent,
                _hls_friendly_rewrite_message(
                    expected_hls_name,
                    synth_error,
                ),
                first_turn=False,
                artifact_kind="testbench",
                required_symbol=expected_hls_name,
            )
            original_name = (
                expected_hls_name[:-4]
                if expected_hls_name.endswith("_hls")
                else expected_hls_name
            )
            new_stub = _request_cpp_artifact(
                agent,
                _stub_request_message(
                    original_name,
                    _extract_seed_hls_decl(
                        new_tb,
                        expected_hls_name,
                    ),
                ),
                first_turn=False,
                artifact_kind="stub",
                required_symbol=expected_hls_name,
            )
            new_stub = _ensure_original_forward_declaration(
                new_stub,
                orig_code,
                original_name,
            )
            new_coverage = _measure_qualified_coverage(
                orig_code,
                new_tb,
                new_stub,
                original_name,
            )
            new_record = _append_round(
                rounds,
                trajectory_idx=trajectory_idx,
                round_index=rounds[-1]["round"] + 1,
                tb_code=new_tb,
                stub_code=new_stub,
                cov=new_coverage,
                synth_retry=True,
            )
            if (
                new_record.get("status") != "ok"
                or new_record.get("cov_pct") is None
            ):
                synth_error = (
                    new_record.get("compile_stderr")
                    or new_record.get("run_stderr")
                    or "synth-retry coverage failed"
                )
                break
            best = new_record

    if not synth_ok:
        return {
            "trajectory_idx": trajectory_idx,
            "best_round": best["round"],
            "best_cov": best.get("cov_pct") or 0.0,
            "best_tb": best["tb_code"],
            "best_stub": best["stub_code"],
            "best_empty_stub": empty_stub,
            "best_uncovered_lines": best.get("uncovered_lines", []),
            "final_text": "",
            "rounds": rounds,
            "synth_ok": False,
            "synth_error": synth_error,
            "qualified": False,
            "trajectory_status": "synth_failed",
        }

    final_raw = _agent_run_once(
        agent,
        _final_text_request(
            best_round_idx=best["round"],
            best_cov=best.get("cov_pct") or 0.0,
            want_sig_spec=want_sig_spec,
            expected_hls_name=expected_hls_name,
        ),
        first_turn=False,
    )
    final_text = tools.general.strip_thinking(final_raw).strip()
    if not final_text:
        raise ModelArtifactError(
            "model returned an empty final instruction/specification"
        )
    return {
        "trajectory_idx": trajectory_idx,
        "best_round": best["round"],
        "best_cov": best.get("cov_pct") or 0.0,
        "best_tb": best["tb_code"],
        "best_stub": best["stub_code"],
        "best_empty_stub": empty_stub,
        "best_uncovered_lines": best.get("uncovered_lines", []),
        "final_text": final_text,
        "rounds": rounds,
        "synth_ok": True,
        "synth_error": "",
        "qualified": True,
        "trajectory_status": "qualified",
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
    trajectory = run_trajectory(
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
    if not trajectory.get("qualified"):
        raise RuntimeError(
            "public testbench generation produced no qualified trajectory: "
            f"{trajectory.get('trajectory_status')}"
        )
    instruction = trajectory["final_text"]
    if pinned_hls_decl:
        instruction = (
            (instruction or "").rstrip()
            + "\n\nPINNED `_hls` DECLARATION:\n```cpp\n"
            + pinned_hls_decl.rstrip()
            + ";\n```\n"
        )
    return {
        "best_tb": trajectory["best_tb"],
        "best_stub": trajectory["best_stub"],
        "best_cov": trajectory["best_cov"],
        "best_round": trajectory["best_round"],
        "instruction": instruction,
        "new_kernel_name": f"{kernel_name}_hls",
        "rounds": trajectory["rounds"],
        "qualified": True,
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
    stub_raw = _agent_run_once(
        agent,
        _stub_request_message(
            kernel_name,
            _extract_seed_hls_decl(tb_code, hls_name),
        ),
        first_turn=False,
    )
    stub_code = _extract_one_cpp_block(stub_raw)

    rounds: List[Dict[str, Any]] = []
    cov = _measure_qualified_coverage(
        orig_code,
        tb_code,
        stub_code,
        kernel_name,
    )
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
        "qualification_errors": list(
            cov.get("qualification_errors", [])
        ),
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
        cov = _measure_qualified_coverage(
            orig_code,
            tb_code,
            stub_code,
            kernel_name,
        )  # reuse stub_code
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
            "qualification_errors": list(
                cov.get("qualification_errors", [])
            ),
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
    orig_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()
    cache_key = cache_key or kernel_name
    if cache_dir is not None:
        cached = _load_golden_cache(cache_dir, cache_key, orig_sha)
        if (
            cached is not None
            and cached.get("synth_ok")
            and cached.get("hidden_tb")
            and cached.get("hidden_sig_spec")
        ):
            return cached

    with concurrent.futures.ThreadPoolExecutor(max_workers=M) as executor:
        futures = {
            executor.submit(
                run_trajectory,
                orig_code=orig_code,
                kernel_name=kernel_name,
                K=K,
                target_pct=target_pct,
                sig_spec_constraint=None,
                llm_config=llm_config,
                want_sig_spec=True,
                trajectory_idx=index,
            ): index
            for index in range(M)
        }
        trajectories: List[Dict[str, Any]] = []
        for future in concurrent.futures.as_completed(futures):
            try:
                trajectories.append(future.result())
            except Exception as exc:
                trajectories.append(
                    {
                        "trajectory_idx": futures[future],
                        "best_cov": 0.0,
                        "best_tb": "",
                        "best_stub": "",
                        "best_round": -1,
                        "final_text": "",
                        "rounds": [],
                        "synth_ok": False,
                        "qualified": False,
                        "trajectory_status": "exception",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    trajectories.sort(key=lambda item: item.get("trajectory_idx", 0))
    qualified = [
        trajectory
        for trajectory in trajectories
        if trajectory.get("qualified")
        and trajectory.get("synth_ok")
        and trajectory.get("best_tb")
        and trajectory.get("final_text")
    ]
    if not qualified:
        reasons = [
            str(
                trajectory.get("error")
                or trajectory.get("synth_error")
                or trajectory.get("trajectory_status")
                or "unknown"
            )[-500:]
            for trajectory in trajectories
        ]
        raise RuntimeError(
            "golden hidden testbench generation produced no "
            "qualified trajectory: "
            + " | ".join(reasons)
        )
    best = max(
        qualified,
        key=lambda trajectory: trajectory.get("best_cov", 0.0),
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
        "synth_ok": True,
        "synth_error": "",
        "qualified": True,
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
