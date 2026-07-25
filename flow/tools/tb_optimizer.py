"""Public Testbench generation and held-out evaluator generation.

The data direction is one-way. Public generation sees only Original source and
Public feedback. Candidate generation consumes only Public-derived evidence.
Held-out generation runs after Candidate generation and receives only Original
source plus the frozen Public-derived Candidate ABI.
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


def extract_hls_decl_from_testbench(
    testbench_code: str,
    hls_name: str,
) -> str:
    """Extract one normalized Candidate declaration from a Public Testbench."""

    declaration = _extract_seed_hls_decl(testbench_code, hls_name)
    if not declaration:
        return ""
    return declaration.strip().rstrip(";") + ";"



_FORWARD_DECL_RE = re.compile(
    r'^\s*(?:extern\s+"C"\s+)?'
    r'(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]\s*|\s+))+'
    r'(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*;',
    re.MULTILINE,
)
_DEFINE_LINE_RE = re.compile(
    r"^\s*#define\s+[A-Za-z_]\w*[^\n]*$",
    re.MULTILINE,
)


def _normalize_declaration(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        value.strip().rstrip(";"),
    ) + ";"


def _extract_public_macros(testbench_code: str) -> Tuple[str, ...]:
    return tuple(
        match.group(0).strip()
        for match in _DEFINE_LINE_RE.finditer(testbench_code)
    )


def _function_definition_count(
    source: str,
    function_name: str,
) -> int:
    pattern = re.compile(
        rf"^\s*(?:extern\s+\"C\"\s+)?"
        rf"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]\s*|\s+))+"
        rf"{re.escape(function_name)}\s*"
        rf"\([^;{{}}]*\)\s*\{{",
        re.MULTILINE,
    )
    return len(pattern.findall(source))


def _call_count_without_declarations(
    source: str,
    function_name: str,
) -> int:
    body = _FORWARD_DECL_RE.sub("", source)
    return len(
        re.findall(
            rf"\b{re.escape(function_name)}\s*\(",
            body,
        )
    )


def validate_testbench_top_contract(
    testbench_code: str,
    original_name: str,
    candidate_name: str,
) -> None:
    """Enforce the minimal Public black-box Testbench contract."""

    issues: List[str] = []
    if not re.search(
        r"\b(?:int|auto)\s+main\s*\(",
        testbench_code,
    ):
        issues.append("missing main(...) entry point")

    for function_name in (original_name, candidate_name):
        if _function_definition_count(
            testbench_code,
            function_name,
        ):
            issues.append(
                "testbench must only forward-declare and call "
                f"{function_name}; it must not define, stub, wrap, "
                "alias, or reimplement that top"
            )

    if _call_count_without_declarations(
        testbench_code,
        candidate_name,
    ) < 1:
        issues.append(
            f"testbench does not call Candidate top {candidate_name}"
        )

    declared = tuple(
        match.group("name")
        for match in _FORWARD_DECL_RE.finditer(testbench_code)
    )
    unexpected = sorted(
        {
            name
            for name in declared
            if name not in {original_name, candidate_name}
        }
    )
    if unexpected:
        issues.append(
            "testbench has external helper declarations outside the "
            "Original/Candidate black-box surface: "
            + ", ".join(unexpected)
        )

    if issues:
        raise ModelArtifactError("; ".join(issues))


def validate_stub_contract(
    stub_code: str,
    *,
    original_name: str,
    candidate_name: str,
    frozen_hls_decl: str,
) -> None:
    """Require one temporary Candidate implementation with the frozen ABI."""

    issues: List[str] = []
    if re.search(r"\bmain\s*\(", stub_code):
        issues.append("stub must not define main")
    if _function_definition_count(stub_code, candidate_name) != 1:
        issues.append(
            "stub must define the Candidate top exactly once: "
            + candidate_name
        )
    if _function_definition_count(stub_code, original_name):
        issues.append(
            "stub must not define, wrap, alias, or copy the Original top: "
            + original_name
        )

    observed = _extract_seed_hls_decl(
        stub_code,
        candidate_name,
    )
    if not observed:
        issues.append(
            "stub does not expose a Candidate definition header"
        )
    elif _normalize_declaration(observed) != _normalize_declaration(
        frozen_hls_decl
    ):
        issues.append(
            "stub Candidate definition does not match the frozen ABI"
        )

    if issues:
        raise ModelArtifactError("; ".join(issues))


def _freeze_public_contract(
    testbench_code: str,
    candidate_name: str,
) -> Tuple[str, Tuple[str, ...]]:
    declaration = extract_hls_decl_from_testbench(
        testbench_code,
        candidate_name,
    )
    if not declaration:
        raise ModelArtifactError(
            "qualified Testbench did not expose a Candidate declaration"
        )
    return (
        _normalize_declaration(declaration),
        _extract_public_macros(testbench_code),
    )


def _validate_frozen_public_contract(
    testbench_code: str,
    candidate_name: str,
    frozen_hls_decl: str,
    frozen_macros: Tuple[str, ...],
) -> None:
    observed_decl, observed_macros = _freeze_public_contract(
        testbench_code,
        candidate_name,
    )
    issues: List[str] = []
    if observed_decl != _normalize_declaration(frozen_hls_decl):
        issues.append(
            "coverage-only Testbench changed the frozen Candidate ABI"
        )
    if observed_macros != tuple(frozen_macros):
        issues.append(
            "coverage-only Testbench changed frozen Public macros"
        )
    if issues:
        raise ModelArtifactError("; ".join(issues))


def _validate_frozen_candidate_abi(
    testbench_code: str,
    candidate_name: str,
    frozen_hls_decl: str,
) -> None:
    observed = extract_hls_decl_from_testbench(
        testbench_code,
        candidate_name,
    )
    if not observed:
        raise ModelArtifactError(
            "Testbench lost the externally frozen Candidate ABI"
        )
    if _normalize_declaration(observed) != _normalize_declaration(
        frozen_hls_decl
    ):
        raise ModelArtifactError(
            "Testbench changed the externally frozen Public-derived ABI"
        )


def _coverage_action(record: Dict[str, Any]) -> str:
    status = str(record.get("status") or "unknown")
    if status == "ok":
        return "expand_inputs_preserve_abi"
    action = str(record.get("next_action") or "")
    if action:
        return action
    owner = str(record.get("failure_owner") or "unknown")
    return {
        "stub": "regenerate_stub",
        "testbench": "repair_testbench",
        "abi": "repair_abi_testbench_stub",
    }.get(owner, "repair_testbench_stub")


def _coverage_contract_failure(message: str) -> Dict[str, Any]:
    return {
        "status": "contract_failed",
        "cov_pct": None,
        "lines_total": None,
        "lines_hit": None,
        "uncovered_lines": [],
        "run_returncode": None,
        "compile_stderr": message[-2000:],
        "run_stderr": "",
        "qualification_errors": [],
        "failure_owner": "testbench",
        "next_action": "repair_testbench",
        "failure_evidence_source": "frozen Public ABI contract",
    }


# -------------------------- prompt builders --------------------------

def _initial_user_message(
    orig_code: str,
    kernel_name: str,
    pinned_public_hls_decl: Optional[str] = None,
) -> str:
    """Build a black-box Testbench prompt with no held-out-derived input."""

    hls_name = f"{kernel_name}_hls"
    parts = [
        "Original kernel source code:",
        "```cpp",
        orig_code.rstrip(),
        "```",
        "",
        f"Top function name (original / golden reference): {kernel_name}",
        f"HLS-side function name you MUST use VERBATIM: {hls_name}",
        (
            "Use the original name with `_hls` appended verbatim. "
            "Do not drop or shorten any prefix or suffix."
        ),
        (
            "Generate one complete, normal-strength testbench directly; "
            "do not emit a preliminary or simplified version."
        ),
        (
            "Treat Original and Candidate implementations as read-only black "
            "boxes. The Testbench may own headers, macros, data, and local "
            "helper definitions, but its only external function forward "
            f"declarations must be `{kernel_name}` and `{hls_name}`."
        ),
        (
            "Forward-declare those tops only. Never define, stub, wrap, "
            "alias, or reimplement either top inside the Testbench."
        ),
        (
            "Do not declare, read, write, or reset implementation-private "
            "globals. Do not copy or depend on implementation-private "
            "types, helper functions, allocator state, or internal data "
            "structures. Use only public arguments and outputs."
        ),
    ]
    if pinned_public_hls_decl:
        parts.extend(
            [
                "",
                "CRITICAL — FROZEN PUBLIC-DERIVED `_hls` ABI:",
                (
                    "Preserve the declaration below character-for-character. "
                    "Do not alter language linkage, return type, parameter "
                    "order/types, qualifiers, pointer/array notation, typedef "
                    "spelling, or function name."
                ),
                "```cpp",
                pinned_public_hls_decl.strip().rstrip(";") + ";",
                "```",
            ]
        )
    parts.extend(
        [
            "",
            (
                "Before writing calls, inspect the public interface and "
                "observable state behavior. The Original and `_hls` sides "
                "must start from equivalent clean logical states and use "
                "separate mutable input/output storage."
            ),
            (
                "Reset only Testbench-owned state immediately before EACH "
                "side is invoked. If a complete public reset cannot be "
                "established, do not call the original repeatedly; use one "
                "representative original invocation and a non-delegating "
                "matching stub. State safety takes priority over testcase "
                "count or marginal coverage."
            ),
            (
                "When declaring the original golden function, preserve its "
                "C/C++ language linkage exactly as shown in the source. Never "
                'add or remove `extern "C"`; a linkage mismatch causes an '
                "undefined reference even when the parameter list looks "
                "identical."
            ),
            (
                "Compare public outputs and observable behavior. On mismatch, "
                "emit a useful stderr message and return non-zero."
            ),
            (
                "Correctness and a meaningful golden-vs-Candidate comparison "
                "take priority over testcase count or coverage."
            ),
            (
                "Reply with exactly one complete ```cpp ... ``` block and "
                "no commentary."
            ),
        ]
    )
    return "\n".join(parts)


def _stub_request_message(
    kernel_name: Optional[str] = None,
    pinned_hls_decl: Optional[str] = None,
    failure_excerpt: Optional[str] = None,
) -> str:
    original_name = kernel_name or "the Original top"
    candidate_name = (
        f"{kernel_name}_hls"
        if kernel_name
        else "the Candidate top"
    )
    pinned = ""
    if pinned_hls_decl:
        pinned = (
            "\n\nCRITICAL — EXACT `_hls` DEFINITION HEADER:\n"
            "Use the declaration below character-for-character as the "
            "definition header, replacing only its trailing `;` with the "
            "function body. Preserve `extern \"C\"` presence or absence, "
            "return type, function name, parameters, qualifiers, pointer/"
            "array notation, and typedef spelling exactly.\n"
            "```cpp\n"
            + pinned_hls_decl.strip().rstrip(";")
            + ";\n```"
        )
    evidence = ""
    if failure_excerpt:
        evidence = (
            "\n\nStub-owned tool evidence from the previous attempt:\n"
            "```\n"
            + failure_excerpt.strip()[-1500:]
            + "\n```\nRepair only the Stub. Keep the Testbench unchanged."
        )
    return (
        "Write one complete temporary Stub translation unit. The Stub is "
        f"the only temporary implementation of `{candidate_name}` used "
        "during generation-time qualification. Define that Candidate top "
        "exactly once and match the frozen declaration exactly, including "
        "C/C++ linkage, return type, parameter order/types, qualifiers, and "
        "pointer/array notation. Do not include `main`. Do not define, wrap, "
        f"alias, copy, or reimplement `{original_name}`. Delegation to the "
        "corresponding original function is CONDITIONAL, not mandatory. "
        "Never delegate as a second execution over shared mutable global, "
        "static, heap-backed, allocator, pointer, tree, queue, counter, or "
        "mutated-buffer state. If safe delegation cannot be established, "
        "write an independent minimal stub that matches the tested "
        "observable behavior and does not call or copy the original "
        "implementation. If the Stub calls the Original, include only its "
        "required forward declaration ending in `;`."
        + pinned
        + evidence
        + "\nReply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


def _empty_stub_request_message(
    hls_name: str,
    pinned_hls_decl: Optional[str] = None,
) -> str:
    pinned = ""
    if pinned_hls_decl:
        pinned = (
            "\nUse this exact Candidate definition header, replacing only "
            "the trailing `;` with a body:\n```cpp\n"
            + pinned_hls_decl.strip().rstrip(";")
            + ";\n```"
        )
    return (
        f"Write a minimal empty/dummy implementation of `{hls_name}` for an "
        "ABI-only CSYNTH check. Define the Candidate top exactly once. "
        "Preserve the exact C/C++ linkage and full frozen ABI. Do not include "
        "`main`, do not define the Original top, and do not add an extra "
        '`extern "C"` wrapper that changes linkage. The body may return a '
        "default value or do nothing for `void`."
        + pinned
        + "\nReply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


def _hls_friendly_rewrite_message(
    hls_name: str,
    csynth_err: str,
) -> str:
    excerpt = csynth_err.strip()[-1500:] or "(no detailed error)"
    return (
        "The ABI-only CSYNTH check proved that the frozen Candidate ABI is "
        "not synthesizable. This is an explicit coordinated ABI correction, "
        "not a coverage-only edit.\n\n"
        f"Candidate top: `{hls_name}`\n"
        "CSYNTH evidence:\n```\n"
        + excerpt
        + "\n```\n\n"
        "Rewrite the complete Testbench so that it introduces one new "
        "HLS-synthesizable Candidate declaration, while preserving the "
        "Original declaration and meaningful golden-vs-Candidate checks. "
        "The Testbench must still only forward-declare the Original and "
        "Candidate tops; it must not define, stub, wrap, or depend on "
        "implementation-private globals/types/helpers. Correctness takes "
        "priority over coverage. This coordinated correction will regenerate "
        "a matching Stub and then re-freeze the new ABI.\n\n"
        "Reply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


def _synth_check(
    empty_stub_code: str,
    hls_name: str,
    work_dir: str,
    budget: Any = None,
) -> Tuple[bool, str]:
    """Run csynth on an empty stub. Returns (passed, error_tail_chars)."""
    os.makedirs(work_dir, exist_ok=True)
    cv = ContextVariables(data={
        "curr_code": empty_stub_code,
        "new_kernel_name": hls_name,
    })
    try:
        status, error_msg = tools.csynth.run_csynth(
            work_dir,
            cv,
            timelimit=SYNTH_CHECK_TIMEOUT,
            budget=budget,
        )
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
    failure_owner: str = "unknown",
    next_action: str = "",
    frozen_hls_decl: Optional[str] = None,
    frozen_macros: Tuple[str, ...] = (),
) -> str:
    frozen_block = ""
    if frozen_hls_decl:
        frozen_block = (
            "\n\nFROZEN CANDIDATE ABI — preserve character-for-character:\n"
            "```cpp\n"
            + frozen_hls_decl.strip().rstrip(";")
            + ";\n```"
        )
    if frozen_macros:
        frozen_block += (
            "\nFROZEN PUBLIC MACROS — preserve character-for-character:\n"
            "```cpp\n"
            + "\n".join(frozen_macros)
            + "\n```"
        )

    if prev_status != "ok":
        evidence = (
            prev_compile_stderr.strip()
            or prev_run_stderr.strip()
            or "(no detailed diagnostic)"
        )[-1500:]
        owner_text = failure_owner or "unknown"
        diagnostic_context = ""
        if prev_status == "run_failed":
            diagnostic_context = (
                "The previous Testbench returned a non-zero status, so "
                "coverage alone is not sufficient. "
            )
        elif prev_status == "qualification_failed":
            diagnostic_context = (
                "The previous Testbench failed the lightweight pre-compile "
                "qualification gate. Respect every reported capacity, "
                "language-linkage, and persistent-state constraint. "
            )
        elif prev_status in (
            "no_gcda",
            "gcov_failed",
            "missing_orig_gcov",
        ):
            diagnostic_context = (
                "The previous Testbench likely crashed before usable coverage "
                "data was emitted. Check whether the Original was invoked "
                "repeatedly or again through a delegating stub while shared "
                "state remained live; eliminate unsafe delegation and restore "
                "equivalent clean state. "
            )

        if next_action == "repair_testbench":
            instruction = (
                "Repair only the complete Testbench. Do not modify or "
                "reimplement either top, and do not change a frozen ABI or "
                "frozen Public macros."
            )
        elif next_action == "repair_abi_testbench_stub":
            instruction = (
                "This is ABI/link ownership. Produce a coordinated complete "
                "Testbench correction; a matching Stub will be regenerated "
                "and the corrected ABI will be re-frozen."
            )
        else:
            instruction = (
                "Produce a complete corrected Testbench. A matching Stub will "
                "be regenerated unless a frozen compatible Stub can be reused."
            )
        return (
            f"Round {round_idx - 1} failed with status `{prev_status}` and "
            f"tool-backed owner `{owner_text}`.\n"
            + diagnostic_context
            + "Diagnostic excerpt:\n```\n"
            + evidence
            + "\n```\n"
            + instruction
            + "\nTreat Original and Candidate implementations as black boxes. "
            "Only forward-declare their tops; do not define, stub, wrap, or "
            "depend on implementation-private globals/types/helpers. "
            "Correctness takes priority over coverage."
            + frozen_block
            + "\nReply with exactly one complete ```cpp ... ``` block and no "
            "commentary."
        )

    if not uncovered_lines:
        uncovered_summary = "All measured lines were covered."
    elif len(uncovered_lines) <= MAX_LISTED_UNCOVERED:
        uncovered_summary = (
            "Uncovered line numbers in orig_code.cpp: "
            + str(uncovered_lines)
        )
    else:
        uncovered_summary = (
            f"{len(uncovered_lines)} lines remain uncovered; first "
            f"{MAX_LISTED_UNCOVERED}: "
            + str(uncovered_lines[:MAX_LISTED_UNCOVERED])
        )

    return (
        f"Round {round_idx - 1} passed correctness checks and achieved "
        f"{prev_cov:.1f}% line coverage. {uncovered_summary}\n\n"
        "This is coverage-only refinement. Expand deterministic public inputs, "
        "cases, and checks to exercise additional paths. Do not change the "
        "Candidate ABI, frozen Public macros, Original/Candidate linkage, or "
        "the golden-vs-Candidate correctness contract. Do not add private "
        "globals/types/helpers. The existing matching Stub will be reused."
        + frozen_block
        + "\n\nOriginal source annotated with `// UNCOVERED` markers:\n"
        "```cpp\n"
        + annotated_source.rstrip()
        + "\n```\n\nReply with exactly one complete ```cpp ... ``` block and "
        "no commentary."
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
    budget: Any = None,
) -> Dict[str, Any]:
    # Keep the text-heuristic helpers for later reference, but do not let
    # capacity, linkage, or persistent-state guesses block real tools.
    result = measure_coverage(
        orig_code,
        tb_code,
        stub_code,
        budget=budget,
    )
    result.setdefault("qualification_errors", [])
    return result

# Diagnostic-only fingerprint. It must never control trajectory termination.
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



def _persist_round_artifacts(
    trajectory_idx: int,
    record: Dict[str, Any],
    artifact_root: Optional[str] = None,
) -> None:
    root = artifact_root or os.getenv("AGREFACTOR_TB_DEBUG_DIR")
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
    artifact_root: Optional[str] = None,
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
        "failure_owner": cov.get(
            "failure_owner",
            "none" if cov.get("status") == "ok" else "unknown",
        ),
        "next_action": cov.get(
            "next_action",
            (
                "continue_validation"
                if cov.get("status") == "ok"
                else "repair_testbench_stub"
            ),
        ),
        "failure_evidence_source": cov.get(
            "failure_evidence_source",
            "legacy coverage result",
        ),
        **extra,
    }
    rounds.append(record)
    _persist_round_artifacts(
        trajectory_idx,
        record,
        artifact_root=artifact_root,
    )
    return record



# -------------------------- trajectory runner --------------------------

def run_trajectory(
    orig_code: str,
    kernel_name: str,
    K: int,
    target_pct: float,
    llm_config: Optional[Dict[str, Any]],
    want_sig_spec: bool,
    trajectory_idx: int = 0,
    pinned_hls_decl: Optional[str] = None,
    emit_final_text: bool = True,
    budget: Any = None,
    artifact_root: Optional[str] = None,
) -> Dict[str, Any]:
    if isinstance(K, bool) or not isinstance(K, int) or K < 1:
        raise ValueError("K must be a positive integer")

    loader = HLSAgentLoader(
        AGENT_YAML,
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent(AGENT_NAME)
    hls_name = f"{kernel_name}_hls"
    rounds: List[Dict[str, Any]] = []
    external_abi_frozen = bool(
        isinstance(pinned_hls_decl, str)
        and pinned_hls_decl.strip()
    )
    frozen_hls_decl = (
        _normalize_declaration(pinned_hls_decl)
        if external_abi_frozen
        else ""
    )
    frozen_macros: Tuple[str, ...] = ()
    reusable_stub = ""

    def request_testbench(
        message: str,
        *,
        first_turn: bool,
    ) -> str:
        value = _request_cpp_artifact(
            agent,
            message,
            first_turn=first_turn,
            artifact_kind="testbench",
            required_symbol=hls_name,
        )
        validate_testbench_top_contract(
            value,
            kernel_name,
            hls_name,
        )
        return value

    def request_stub(
        testbench_code: str,
        *,
        message: Optional[str] = None,
    ) -> Tuple[str, str]:
        declaration = extract_hls_decl_from_testbench(
            testbench_code,
            hls_name,
        )
        if not declaration:
            raise ModelArtifactError(
                "Testbench does not expose a Candidate declaration"
            )
        value = _request_cpp_artifact(
            agent,
            (
                message
                if message is not None
                else _stub_request_message(
                    kernel_name,
                    declaration,
                )
            ),
            first_turn=False,
            artifact_kind="stub",
            required_symbol=hls_name,
        )
        value = _ensure_original_forward_declaration(
            value,
            orig_code,
            kernel_name,
        )
        validate_stub_contract(
            value,
            original_name=kernel_name,
            candidate_name=hls_name,
            frozen_hls_decl=declaration,
        )
        return value, _normalize_declaration(declaration)

    testbench_code = request_testbench(
        _initial_user_message(
            orig_code,
            kernel_name,
            pinned_public_hls_decl=pinned_hls_decl,
        ),
        first_turn=True,
    )
    if external_abi_frozen:
        _validate_frozen_candidate_abi(
            testbench_code,
            hls_name,
            frozen_hls_decl,
        )
    stub_code, current_decl = request_stub(testbench_code)
    coverage = _measure_qualified_coverage(
        orig_code,
        testbench_code,
        stub_code,
        kernel_name,
        budget=budget,
    )

    if coverage.get("status") == "ok":
        observed_decl, observed_macros = _freeze_public_contract(
            testbench_code,
            hls_name,
        )
        if external_abi_frozen:
            _validate_frozen_candidate_abi(
                testbench_code,
                hls_name,
                frozen_hls_decl,
            )
            frozen_macros = observed_macros
        else:
            frozen_hls_decl = observed_decl
            frozen_macros = observed_macros
        reusable_stub = stub_code

    _append_round(
        rounds,
        trajectory_idx=trajectory_idx,
        round_index=1,
        tb_code=testbench_code,
        stub_code=stub_code,
        cov=coverage,
        artifact_root=artifact_root,
        ownership_action="initial_generation",
        testbench_reused=False,
        stub_reused=False,
        frozen_public_hls_decl=frozen_hls_decl,
        frozen_public_macros=list(frozen_macros),
        abi_decl=current_decl,
    )

    for round_index in range(2, K + 1):
        previous = rounds[-1]
        if (
            previous.get("status") == "ok"
            and (previous.get("cov_pct") or 0.0) >= target_pct
        ):
            break

        action = _coverage_action(previous)
        if (
            external_abi_frozen
            and action == "repair_abi_testbench_stub"
        ):
            action = "repair_testbench_stub"
        if action in {"review_toolchain", "review_original"}:
            break
        testbench_reused = False
        stub_reused = False
        previous_error = (
            str(previous.get("compile_stderr") or "")
            or str(previous.get("run_stderr") or "")
            or str(previous.get("status") or "unknown")
        )

        if action == "regenerate_stub":
            testbench_code = str(previous["tb_code"])
            declaration = (
                frozen_hls_decl
                or extract_hls_decl_from_testbench(
                    testbench_code,
                    hls_name,
                )
            )
            stub_code, current_decl = request_stub(
                testbench_code,
                message=_stub_request_message(
                    kernel_name,
                    declaration,
                    failure_excerpt=previous_error,
                ),
            )
            testbench_reused = True

        elif action == "expand_inputs_preserve_abi":
            if not frozen_hls_decl or not reusable_stub:
                action = "repair_testbench_stub"
            else:
                annotated = annotate_uncovered_source(
                    orig_code,
                    previous.get("uncovered_lines", []),
                )
                testbench_code = request_testbench(
                    _feedback_message(
                        round_index,
                        previous.get("cov_pct") or 0.0,
                        previous.get("uncovered_lines", []),
                        annotated,
                        "ok",
                        failure_owner="coverage",
                        next_action=action,
                        frozen_hls_decl=frozen_hls_decl,
                        frozen_macros=frozen_macros,
                    ),
                    first_turn=False,
                )
                try:
                    _validate_frozen_public_contract(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        frozen_macros,
                    )
                except ModelArtifactError as exc:
                    _append_round(
                        rounds,
                        trajectory_idx=trajectory_idx,
                        round_index=round_index,
                        tb_code=testbench_code,
                        stub_code=reusable_stub,
                        cov=_coverage_contract_failure(str(exc)),
                        artifact_root=artifact_root,
                        ownership_action=action,
                        testbench_reused=False,
                        stub_reused=True,
                        frozen_public_hls_decl=frozen_hls_decl,
                        frozen_public_macros=list(frozen_macros),
                        abi_decl=frozen_hls_decl,
                    )
                    continue
                stub_code = reusable_stub
                current_decl = frozen_hls_decl
                stub_reused = True

        if action == "repair_testbench":
            annotated = (
                annotate_uncovered_source(
                    orig_code,
                    previous.get("uncovered_lines", []),
                )
                if previous.get("status") == "ok"
                else orig_code
            )
            testbench_code = request_testbench(
                _feedback_message(
                    round_index,
                    previous.get("cov_pct") or 0.0,
                    previous.get("uncovered_lines", []),
                    annotated,
                    str(previous.get("status") or "unknown"),
                    prev_compile_stderr=str(
                        previous.get("compile_stderr") or ""
                    ),
                    prev_run_stderr=str(
                        previous.get("run_stderr") or ""
                    ),
                    failure_owner=str(
                        previous.get("failure_owner") or "testbench"
                    ),
                    next_action=action,
                    frozen_hls_decl=(
                        frozen_hls_decl or None
                    ),
                    frozen_macros=frozen_macros,
                ),
                first_turn=False,
            )
            if frozen_hls_decl and reusable_stub:
                try:
                    _validate_frozen_public_contract(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        frozen_macros,
                    )
                except ModelArtifactError as exc:
                    _append_round(
                        rounds,
                        trajectory_idx=trajectory_idx,
                        round_index=round_index,
                        tb_code=testbench_code,
                        stub_code=reusable_stub,
                        cov=_coverage_contract_failure(str(exc)),
                        artifact_root=artifact_root,
                        ownership_action=action,
                        testbench_reused=False,
                        stub_reused=True,
                        frozen_public_hls_decl=frozen_hls_decl,
                        frozen_public_macros=list(frozen_macros),
                        abi_decl=frozen_hls_decl,
                    )
                    continue
                stub_code = reusable_stub
                current_decl = frozen_hls_decl
                stub_reused = True
            else:
                stub_code, current_decl = request_stub(
                    testbench_code
                )

        elif action == "repair_abi_testbench_stub":
            testbench_code = request_testbench(
                _hls_friendly_rewrite_message(
                    hls_name,
                    previous_error,
                ),
                first_turn=False,
            )
            stub_code, current_decl = request_stub(testbench_code)

        elif action == "repair_testbench_stub":
            testbench_code = request_testbench(
                _feedback_message(
                    round_index,
                    previous.get("cov_pct") or 0.0,
                    previous.get("uncovered_lines", []),
                    orig_code,
                    str(previous.get("status") or "unknown"),
                    prev_compile_stderr=str(
                        previous.get("compile_stderr") or ""
                    ),
                    prev_run_stderr=str(
                        previous.get("run_stderr") or ""
                    ),
                    failure_owner=str(
                        previous.get("failure_owner") or "unknown"
                    ),
                    next_action=action,
                    frozen_hls_decl=(
                        frozen_hls_decl or None
                    ),
                    frozen_macros=frozen_macros,
                ),
                first_turn=False,
            )
            stub_code, current_decl = request_stub(testbench_code)

        coverage = _measure_qualified_coverage(
            orig_code,
            testbench_code,
            stub_code,
            kernel_name,
            budget=budget,
        )

        if coverage.get("status") == "ok":
            observed_decl, observed_macros = _freeze_public_contract(
                testbench_code,
                hls_name,
            )
            if external_abi_frozen:
                _validate_frozen_candidate_abi(
                    testbench_code,
                    hls_name,
                    frozen_hls_decl,
                )
                if frozen_macros:
                    _validate_frozen_public_contract(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        frozen_macros,
                    )
                else:
                    frozen_macros = observed_macros
            elif (
                not frozen_hls_decl
                or action == "repair_abi_testbench_stub"
            ):
                frozen_hls_decl = observed_decl
                frozen_macros = observed_macros
            else:
                _validate_frozen_public_contract(
                    testbench_code,
                    hls_name,
                    frozen_hls_decl,
                    frozen_macros,
                )
            reusable_stub = stub_code

        _append_round(
            rounds,
            trajectory_idx=trajectory_idx,
            round_index=round_index,
            tb_code=testbench_code,
            stub_code=stub_code,
            cov=coverage,
            artifact_root=artifact_root,
            ownership_action=action,
            testbench_reused=testbench_reused,
            stub_reused=stub_reused,
            frozen_public_hls_decl=frozen_hls_decl,
            frozen_public_macros=list(frozen_macros),
            abi_decl=current_decl,
        )

    return _finalize_trajectory(
        agent,
        rounds,
        want_sig_spec,
        trajectory_idx,
        expected_hls_name=hls_name,
        orig_code=orig_code,
        emit_final_text=emit_final_text,
        budget=budget,
        artifact_root=artifact_root,
        allow_abi_correction=not external_abi_frozen,
    )


def _finalize_trajectory(
    agent,
    rounds: List[Dict[str, Any]],
    want_sig_spec: bool,
    trajectory_idx: int,
    expected_hls_name: Optional[str] = None,
    orig_code: Optional[str] = None,
    emit_final_text: bool = True,
    synth_retry_budget: int = 1,
    budget: Any = None,
    artifact_root: Optional[str] = None,
    allow_abi_correction: bool = True,
) -> Dict[str, Any]:
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
            "frozen_public_hls_decl": "",
            "frozen_public_macros": [],
        }

    best = max(
        ok_rounds,
        key=lambda record: record["cov_pct"],
    )
    synth_ok = False
    synth_error = ""
    empty_stub = ""

    if expected_hls_name and orig_code is not None:
        original_name = (
            expected_hls_name[:-4]
            if expected_hls_name.endswith("_hls")
            else expected_hls_name
        )
        retries_left = synth_retry_budget
        while True:
            best_decl = (
                str(best.get("frozen_public_hls_decl") or "")
                or extract_hls_decl_from_testbench(
                    str(best["tb_code"]),
                    expected_hls_name,
                )
            )
            if not best_decl:
                raise ModelArtifactError(
                    "qualified Testbench lost its Candidate ABI"
                )
            empty_stub = _request_cpp_artifact(
                agent,
                _empty_stub_request_message(
                    expected_hls_name,
                    best_decl,
                ),
                first_turn=False,
                artifact_kind="empty_stub",
                required_symbol=expected_hls_name,
            )
            validate_stub_contract(
                empty_stub,
                original_name=original_name,
                candidate_name=expected_hls_name,
                frozen_hls_decl=best_decl,
            )
            with tempfile.TemporaryDirectory(
                prefix=f"synth_check_traj{trajectory_idx}_"
            ) as work_dir:
                synth_ok, synth_error = _synth_check(
                    empty_stub,
                    expected_hls_name,
                    work_dir,
                    budget=budget,
                )
            if (
                synth_ok
                or retries_left <= 0
                or not allow_abi_correction
            ):
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
            validate_testbench_top_contract(
                new_tb,
                original_name,
                expected_hls_name,
            )
            new_decl = extract_hls_decl_from_testbench(
                new_tb,
                expected_hls_name,
            )
            if not new_decl:
                raise ModelArtifactError(
                    "coordinated ABI correction returned no Candidate ABI"
                )
            new_stub = _request_cpp_artifact(
                agent,
                _stub_request_message(
                    original_name,
                    new_decl,
                    failure_excerpt=synth_error,
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
            validate_stub_contract(
                new_stub,
                original_name=original_name,
                candidate_name=expected_hls_name,
                frozen_hls_decl=new_decl,
            )
            new_coverage = _measure_qualified_coverage(
                orig_code,
                new_tb,
                new_stub,
                original_name,
                budget=budget,
            )
            frozen_decl = ""
            frozen_macros: Tuple[str, ...] = ()
            if new_coverage.get("status") == "ok":
                frozen_decl, frozen_macros = _freeze_public_contract(
                    new_tb,
                    expected_hls_name,
                )
            new_record = _append_round(
                rounds,
                trajectory_idx=trajectory_idx,
                round_index=rounds[-1]["round"] + 1,
                tb_code=new_tb,
                stub_code=new_stub,
                cov=new_coverage,
                artifact_root=artifact_root,
                synth_retry=True,
                ownership_action="repair_abi_testbench_stub",
                testbench_reused=False,
                stub_reused=False,
                abi_refrozen=bool(frozen_decl),
                frozen_public_hls_decl=frozen_decl,
                frozen_public_macros=list(frozen_macros),
                abi_decl=_normalize_declaration(new_decl),
            )
            if (
                new_record.get("status") != "ok"
                or new_record.get("cov_pct") is None
            ):
                synth_error = (
                    new_record.get("compile_stderr")
                    or new_record.get("run_stderr")
                    or "coordinated ABI correction failed"
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
            "best_uncovered_lines": best.get(
                "uncovered_lines",
                [],
            ),
            "final_text": "",
            "rounds": rounds,
            "synth_ok": False,
            "synth_error": synth_error,
            "qualified": False,
            "trajectory_status": "synth_failed",
            "frozen_public_hls_decl": best.get(
                "frozen_public_hls_decl",
                "",
            ),
            "frozen_public_macros": best.get(
                "frozen_public_macros",
                [],
            ),
        }

    common = {
        "trajectory_idx": trajectory_idx,
        "best_round": best["round"],
        "best_cov": best.get("cov_pct") or 0.0,
        "best_tb": best["tb_code"],
        "best_stub": best["stub_code"],
        "best_empty_stub": empty_stub,
        "best_uncovered_lines": best.get(
            "uncovered_lines",
            [],
        ),
        "rounds": rounds,
        "synth_ok": True,
        "synth_error": "",
        "qualified": True,
        "trajectory_status": "qualified",
        "frozen_public_hls_decl": best.get(
            "frozen_public_hls_decl",
            "",
        ),
        "frozen_public_macros": best.get(
            "frozen_public_macros",
            [],
        ),
    }

    if not emit_final_text:
        return {
            **common,
            "final_text": "",
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
    final_text = tools.general.strip_thinking(
        final_raw
    ).strip()
    if not final_text:
        raise ModelArtifactError(
            "model returned an empty final instruction/specification"
        )
    return {
        **common,
        "final_text": final_text,
    }


# -------------------------- public-facing entrypoints --------------------------

def optimize_tb_public(
    orig_code: str,
    kernel_name: str,
    K: int = 3,
    target_pct: float = 80.0,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Any = None,
    M: int = 1,
    artifact_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate Public evidence across one or more independent trajectories."""

    if isinstance(M, bool) or not isinstance(M, int) or M < 1:
        raise ValueError("M must be a positive integer")

    if M == 1:
        trajectories = [
            run_trajectory(
                orig_code=orig_code,
                kernel_name=kernel_name,
                K=K,
                target_pct=target_pct,
                llm_config=llm_config,
                want_sig_spec=False,
                trajectory_idx=0,
                emit_final_text=True,
                budget=budget,
                artifact_root=artifact_root,
            )
        ]
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=M
        ) as executor:
            futures = {
                executor.submit(
                    run_trajectory,
                    orig_code=orig_code,
                    kernel_name=kernel_name,
                    K=K,
                    target_pct=target_pct,
                    llm_config=llm_config,
                    want_sig_spec=False,
                    trajectory_idx=index,
                    emit_final_text=True,
                    budget=budget,
                    artifact_root=artifact_root,
                ): index
                for index in range(M)
            }
            trajectories = []
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

    trajectories.sort(
        key=lambda item: int(item.get("trajectory_idx", 0))
    )
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
            "public testbench generation produced no qualified trajectory: "
            + " | ".join(reasons)
        )

    best = max(
        qualified,
        key=lambda trajectory: (
            float(trajectory.get("best_cov", 0.0)),
            -int(trajectory.get("trajectory_idx", 0)),
        ),
    )
    return {
        "best_tb": best["best_tb"],
        "best_stub": best["best_stub"],
        "best_cov": best["best_cov"],
        "best_round": best["best_round"],
        "best_trajectory": best.get("trajectory_idx", 0),
        "instruction": best["final_text"],
        "new_kernel_name": f"{kernel_name}_hls",
        "rounds": best["rounds"],
        "trajectories": trajectories,
        "qualified": True,
        "frozen_public_hls_decl": best.get(
            "frozen_public_hls_decl",
            "",
        ),
        "frozen_public_macros": best.get(
            "frozen_public_macros",
            [],
        ),
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
    budget: Any = None,
    M: int = 1,
) -> Tuple[str, str, str]:
    """Coverage-enhanced Public generation with no held-out input channel."""

    artifact_root = None
    getter = getattr(cv, "get", None)
    if callable(getter):
        artifact_root = getter("public_tb_artifact_dir")
    result = optimize_tb_public(
        orig_code=cv["orig_code"],
        kernel_name=cv["kernel_name"],
        K=K,
        target_pct=target_pct,
        llm_config=llm_config,
        budget=budget,
        M=M,
        artifact_root=artifact_root,
    )
    cv["public_testbench_coverage"] = {
        "schema_version": 1,
        "profile": (
            getter("test_generation_profile")
            if callable(getter)
            else "coverage-enhanced"
        ),
        "requested_rounds": K,
        "requested_trajectories": M,
        "trajectory_count": len(result["trajectories"]),
        "best_trajectory": result["best_trajectory"],
        "best_round": result["best_round"],
        "best_cov": result["best_cov"],
        "artifact_root": artifact_root,
        "trajectories": result["trajectories"],
    }
    return (
        result["best_tb"],
        result["instruction"],
        result["new_kernel_name"],
    )


def make_golden_hidden_tb(
    orig_code: str,
    kernel_name: str,
    pinned_public_hls_decl: str,
    M: int = 3,
    K: int = 6,
    target_pct: float = 90.0,
    llm_config: Optional[Dict[str, Any]] = None,
    cache_dir: Optional[str] = None,
    cache_key: Optional[str] = None,
    budget: Any = None,
    artifact_root: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(pinned_public_hls_decl, str) or not (
        pinned_public_hls_decl.strip()
    ):
        raise ValueError(
            "held-out generation requires a frozen Public-derived ABI"
        )
    normalized_public_decl = (
        pinned_public_hls_decl.strip().rstrip(";") + ";"
    )
    public_decl_sha = hashlib.sha256(
        normalized_public_decl.encode("utf-8")
    ).hexdigest()
    orig_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()
    cache_key = cache_key or kernel_name
    if cache_dir is not None:
        cached = _load_golden_cache(cache_dir, cache_key, orig_sha)
        if (
            cached is not None
            and cached.get("synth_ok")
            and cached.get("hidden_tb")
            and cached.get("public_hls_decl_sha256")
            == public_decl_sha
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
                llm_config=llm_config,
                want_sig_spec=False,
                trajectory_idx=index,
                pinned_hls_decl=normalized_public_decl,
                emit_final_text=False,
                budget=budget,
                artifact_root=artifact_root,
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
        "hidden_cov": best["best_cov"],
        "public_hls_decl": normalized_public_decl,
        "public_hls_decl_sha256": public_decl_sha,
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
