"""Public Testbench generation and held-out evaluator generation.

The data direction is one-way. Public generation sees only Original source and
Public feedback. Candidate generation consumes only Public-derived evidence.
Held-out generation runs after Candidate generation and receives only Original
source plus the frozen Public-derived Candidate ABI.
"""

import concurrent.futures
from contextlib import nullcontext
import json
import os
import tempfile
import hashlib
import re
import subprocess
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autogen.agentchat.group import ContextVariables  # type: ignore
from agrefactor.config import EvaluationSplit, TestSuiteSpec
from agrefactor.reference_source import isolate_reference_program_entry
from agrefactor.config.tool_timeouts import DEFAULT_CSYNTH_TIMEOUT_S
from agrefactor.cpp_interface import (
    extract_top_interface,
    interfaces_equivalent,
)
from flow.base_agent import HLSAgentLoader
import flow.tools as tools
from flow.tools.tb_coverage import (
    annotate_uncovered_source,
    check_original_execution,
    measure_coverage,
)
from flow.tools.result_mapping import (
    freeze_result_mapping,
    frozen_mapping_instruction,
    mapping_generation_instruction,
    validate_frozen_result_mapping,
)

# Reuse the compiler-backed interface extractor from the inflight package.
from flow.inflight_tb.checks import extract_hls_decl_from_tb as _extract_seed_hls_decl  # noqa: E402


AGENT_YAML = "flow/agents/testbench_coverage.yaml"
AGENT_NAME = "tb_engineer"
SYNTH_CHECK_TIMEOUT = DEFAULT_CSYNTH_TIMEOUT_S
# Maximum number of uncovered lines to enumerate explicitly in the prompt;
# beyond this we just include the annotated source. Keeps prompts bounded.
MAX_LISTED_UNCOVERED = 60



class TestbenchGenerationExhausted(RuntimeError):
    # Bounded, model-generated Testbench/Stub qualification exhaustion.
    # The payload is code-free and safe for product/operator metadata.

    schema_version = 1
    _DIAGNOSTIC_LIMIT = 2000

    def __init__(
        self,
        *,
        split: str,
        stage: str,
        trajectories: List[Dict[str, Any]],
        qualification_mode: str = "generated",
    ) -> None:
        if split not in {"public", "hidden"}:
            raise ValueError("split must be public or hidden")
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError("stage must not be empty")
        if not isinstance(trajectories, list) or not trajectories:
            raise ValueError("trajectories must be a non-empty list")
        if qualification_mode not in {"generated", "provided"}:
            raise ValueError(
                "qualification_mode must be generated or provided"
            )

        summaries = [
            self._summarize_trajectory(
                trajectory,
                split=split,
            )
            for trajectory in trajectories
        ]
        owners = {
            item["failure_owner"]
            for item in summaries
            if item["failure_owner"] not in {"", "none", "unknown"}
        }
        actions = {
            item["next_action"]
            for item in summaries
            if item["next_action"] not in {"", "none", "unknown"}
        }
        diagnostic_kinds = {
            item["diagnostic_kind"]
            for item in summaries
            if item["diagnostic_kind"] not in {"", "none", "unknown"}
        }

        failure_owner = (
            next(iter(owners))
            if len(owners) == 1
            else "mixed"
            if owners
            else "unknown"
        )
        next_action = (
            next(iter(actions))
            if len(actions) == 1
            else "change_generation_strategy"
        )
        diagnostic_kind = (
            next(iter(diagnostic_kinds))
            if len(diagnostic_kinds) == 1
            else "mixed_qualification_failure"
            if diagnostic_kinds
            else "generation_qualification_failed"
        )
        attempt_count = sum(
            max(1, int(item["round_count"]))
            for item in summaries
        )
        excerpts = [
            item["diagnostic_excerpt"]
            for item in summaries
            if item["diagnostic_excerpt"]
        ]
        diagnostic_excerpt = " | ".join(excerpts)[
            -self._DIAGNOSTIC_LIMIT:
        ]

        is_provided = qualification_mode == "provided"
        self._payload = {
            "schema_version": self.schema_version,
            "failure_kind": (
                "testbench_qualification_failed"
                if is_provided
                else "testbench_generation_exhausted"
            ),
            "terminal_class": (
                "provided_qualification_failure"
                if is_provided
                else "bounded_generation_failure"
            ),
            "bounded": True,
            "split": split,
            "stage": stage.strip(),
            "qualification_mode": qualification_mode,
            "trajectory_count": len(summaries),
            "qualified_count": 0,
            "attempt_count": attempt_count,
            "repair_attempt_count": 0 if is_provided else attempt_count,
            "retry_exhausted": False if is_provided else True,
            "failure_owner": failure_owner,
            "next_action": (
                "review_required_provided"
                if is_provided
                else next_action
            ),
            "diagnostic_kind": diagnostic_kind,
            "diagnostic_excerpt": diagnostic_excerpt,
            "trajectory_summaries": summaries,
            "hidden_testbench_exposed_to_model": False,
            "retry_guidance": (
                [
                    "review the provided Public Testbench and its qualification receipt",
                    "supply a corrected and independently qualified Public Testbench",
                    "do not modify the supplied Testbench automatically",
                ]
                if is_provided
                else [
                    "retry with a different bounded generation profile",
                    "retry with another compatible model",
                    "increase bounded trajectory diversity within safety ceilings",
                    "provide qualified Public/Hidden suites when available",
                    "use a future memory-assisted strategy when available",
                ]
            ),
        }
        compatibility_prefix = (
            "provided Public Testbench qualification"
            if is_provided
            else
            "golden hidden testbench generation"
            if split == "hidden"
            else "public testbench generation"
        )
        if is_provided:
            message = (
                f"{compatibility_prefix} failed before formal validation; "
                f"owner={failure_owner}; kind={diagnostic_kind}"
            )
        else:
            message = (
                f"{compatibility_prefix} produced no qualified trajectory: "
                f"exhausted {len(summaries)} trajectory(s) after "
                f"{attempt_count} bounded qualification attempt(s); "
                f"owner={failure_owner}; kind={diagnostic_kind}"
            )
        super().__init__(message)

    @classmethod
    def _summarize_trajectory(
        cls,
        trajectory: Dict[str, Any],
        *,
        split: str,
    ) -> Dict[str, Any]:
        if not isinstance(trajectory, dict):
            trajectory = {}
        raw_rounds = trajectory.get("rounds")
        rounds = (
            [item for item in raw_rounds if isinstance(item, dict)]
            if isinstance(raw_rounds, list)
            else []
        )
        last = rounds[-1] if rounds else {}

        owner = str(
            last.get("failure_owner")
            or trajectory.get("failure_owner")
            or "unknown"
        )
        action = str(
            last.get("next_action")
            or trajectory.get("next_action")
            or "change_generation_strategy"
        )
        diagnostic_kind = str(
            last.get("status")
            or trajectory.get("trajectory_status")
            or "generation_qualification_failed"
        )
        if trajectory.get("trajectory_status") == "synth_failed":
            diagnostic_kind = "synth_failed"
            action = "review_unknown"
        raw_diagnostic = str(
            last.get("compile_stderr")
            or last.get("run_stderr")
            or trajectory.get("error")
            or trajectory.get("synth_error")
            or diagnostic_kind
        )

        if split == "hidden" and owner not in {"stub", "toolchain"}:
            diagnostic_excerpt = (
                "held-out qualification failed; detailed diagnostics "
                "remain in operator-only artifacts"
            )
        else:
            diagnostic_excerpt = raw_diagnostic[-cls._DIAGNOSTIC_LIMIT:]

        return {
            "trajectory_idx": int(
                trajectory.get("trajectory_idx", 0)
            ),
            "trajectory_status": str(
                trajectory.get("trajectory_status")
                or diagnostic_kind
            ),
            "qualified": bool(trajectory.get("qualified", False)),
            "synth_ok": bool(trajectory.get("synth_ok", False)),
            "round_count": len(rounds),
            "failure_owner": owner,
            "next_action": action,
            "diagnostic_kind": diagnostic_kind,
            "diagnostic_excerpt": diagnostic_excerpt,
        }

    def to_dict(self) -> Dict[str, Any]:
        return json.loads(
            json.dumps(
                self._payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
        )


def extract_hls_decl_from_testbench(
    testbench_code: str,
    hls_name: str,
    *,
    source_path: Optional[str] = None,
    include_dirs: Tuple[str, ...] = (),
    compile_flags: Tuple[str, ...] = (),
) -> str:
    """Extract one normalized Candidate declaration from a Public Testbench."""

    interface = extract_top_interface(
        testbench_code, hls_name, require_definition=False,
        source_path=source_path, include_dirs=include_dirs,
        compile_flags=compile_flags,
    )
    declaration = interface.source_declaration if interface is not None else ""
    if not declaration:
        return ""
    declaration = declaration.strip().rstrip(";")
    # ``clang_getCursorLinkage`` cannot distinguish C and C++ external
    # linkage. ``cpp_interface`` records the source-level language linkage so
    # the frozen declaration remains link-compatible with generated HLS code.
    if interface.language_linkage == "c":
        declaration = 'extern "C" ' + declaration
    return declaration + ";"



def _normalize_declaration(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        value.strip().rstrip(";"),
    ) + ";"


def public_runtime_contract_depth_ports(
    *,
    testbench_code: str,
    candidate_top_function: str,
    frozen_hls_decl: str,
    source_path: Optional[str] = None,
    include_dirs: Tuple[str, ...] = (),
    compile_flags: Tuple[str, ...] = (),
) -> Tuple[str, ...]:
    """Return pointer/array Candidate ports that require COSIM depths."""

    if not isinstance(testbench_code, str):
        raise TypeError("testbench_code must be a string")
    if not isinstance(candidate_top_function, str) or not (
        candidate_top_function.strip()
    ):
        raise ValueError("candidate_top_function must not be empty")
    if not isinstance(frozen_hls_decl, str) or not frozen_hls_decl.strip():
        raise ValueError("frozen_hls_decl must not be empty")

    candidate = candidate_top_function.strip()
    interface = extract_top_interface(
        frozen_hls_decl,
        candidate,
        require_definition=False,
    )
    # A declaration may use a typedef declared only in the Public testbench
    # (for example tb_data_t * where tb_data_t is an array typedef).
    # Parse the complete testbench when the isolated declaration cannot
    # resolve that type, so depth requirements remain tied to the frozen ABI.
    if interface is None:
        interface = extract_top_interface(
            testbench_code,
            candidate,
            require_definition=False,
            source_path=source_path,
            include_dirs=include_dirs,
            compile_flags=compile_flags,
        )
    if interface is None:
        return ()
    return tuple(
        sorted(
            parameter.name
            for parameter in interface.parameters
            if parameter.pointer_like
        )
    )


def validate_public_runtime_contract(
    value: Dict[str, Any],
    *,
    testbench_code: str,
    candidate_top_function: str,
    frozen_hls_decl: str,
    source_path: Optional[str] = None,
    include_dirs: Tuple[str, ...] = (),
    compile_flags: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Normalize a generated contract and bind it to the frozen Public ABI."""

    try:
        suite = TestSuiteSpec(
            suite_id="generated-public-contract",
            split=EvaluationSplit.PUBLIC,
            runtime_contract=value,
        )
    except (TypeError, ValueError) as exc:
        raise ModelArtifactError(str(exc)) from exc
    assert suite.runtime_contract is not None
    contract = suite.to_dict()["runtime_contract"]
    required = set(
        public_runtime_contract_depth_ports(
            testbench_code=testbench_code,
            candidate_top_function=candidate_top_function,
            frozen_hls_decl=frozen_hls_decl,
            source_path=source_path,
            include_dirs=include_dirs,
            compile_flags=compile_flags,
        )
    )

    if not required:
        if contract["schema_version"] != 1:
            raise ModelArtifactError(
                "scalar-only Public ABI must not declare COSIM depth ports"
            )
        return contract
    if contract["schema_version"] != 2:
        raise ModelArtifactError(
            "pointer/array Public ABI requires a version 2 runtime contract"
        )

    observed = set(contract["cosim_interface_depths"])
    unexpected = sorted(observed - required)
    if unexpected:
        raise ModelArtifactError(
            "COSIM depth port(s) are not pointer or array ports in the "
            "frozen Public Candidate ABI: " + ", ".join(unexpected)
        )
    missing = sorted(required - observed)
    if missing:
        raise ModelArtifactError(
            "COSIM depth contract is missing required pointer/array port(s): "
            + ", ".join(missing)
        )
    return contract


def validate_testbench_top_contract(
    testbench_code: str,
    original_name: str,
    candidate_name: str,
    *,
    require_original_call: bool = False,
) -> None:
    """Enforce the minimal Public black-box Testbench contract."""

    if not isinstance(testbench_code, str) or not testbench_code.strip():
        raise ModelArtifactError("testbench must not be empty")
    # The staged preflight owns C++ structure, symbol, linkage, and execution
    # checks.  A lexical approximation here must not reject valid C++.


def validate_stub_contract(
    stub_code: str,
    *,
    original_name: str,
    candidate_name: str,
    frozen_hls_decl: str,
) -> None:
    """Require one temporary Candidate implementation with the frozen ABI."""

    if not isinstance(stub_code, str) or not stub_code.strip():
        raise ModelArtifactError("stub must not be empty")


def _freeze_public_contract(
    testbench_code: str,
    candidate_name: str,
    **parse_context,
) -> Tuple[str, Tuple[str, ...]]:
    declaration = extract_hls_decl_from_testbench(
        testbench_code,
        candidate_name,
        **parse_context,
    )
    if not declaration:
        raise ModelArtifactError(
            "compiler could not derive the Candidate declaration"
        )
    return (_normalize_declaration(declaration), ())


def _validate_frozen_public_contract(
    testbench_code: str,
    candidate_name: str,
    frozen_hls_decl: str,
    frozen_macros: Tuple[str, ...],
    **parse_context,
) -> None:
    _validate_frozen_candidate_abi(
        testbench_code,
        candidate_name,
        frozen_hls_decl,
        **parse_context,
    )


def _validate_frozen_candidate_abi(
    testbench_code: str,
    candidate_name: str,
    frozen_hls_decl: str,
    **parse_context,
) -> None:
    observed = extract_top_interface(
        testbench_code,
        candidate_name,
        require_definition=False,
        **parse_context,
    )
    frozen = extract_top_interface(
        frozen_hls_decl,
        candidate_name,
        require_definition=False,
        **parse_context,
    )
    if frozen is None:
        # Frozen declarations may use a typedef or vendor type declared by the
        # Testbench's source package. Parse the declaration in that context,
        # while keeping the observed declaration check independent.
        frozen = extract_top_interface(
            testbench_code + "\n" + frozen_hls_decl,
            candidate_name,
            require_definition=False,
            **parse_context,
        )
    if frozen is None:
        raise ModelArtifactError(
            "externally frozen Candidate ABI could not be parsed"
        )
    if observed is None:
        raise ModelArtifactError(
            "Testbench does not expose the externally frozen Candidate "
            "declaration"
        )
    if not interfaces_equivalent(observed, frozen):
        raise ModelArtifactError(
            "Testbench changed the externally frozen Public-derived ABI"
        )


def _validate_original_language_linkage(
    testbench_code: str,
    original_code: str,
    original_name: str,
    **parse_context,
) -> str:
    """Require the Original declaration to match its definition linkage."""

    observed = extract_top_interface(
        testbench_code, original_name, require_definition=False, **parse_context
    )
    definition = extract_top_interface(
        original_code, original_name, require_definition=True, **parse_context
    )
    if observed is None or definition is None:
        return ""
    if (
        observed.language_linkage is not None
        and definition.language_linkage is not None
        and observed.language_linkage != definition.language_linkage
    ):
        raise ModelArtifactError(
            "Testbench Original declaration language linkage does not match "
            f"the Original definition for `{original_name}` "
            f"(declared={observed.language_linkage}, "
            f"defined={definition.language_linkage})"
        )
    return extract_hls_decl_from_testbench(
        original_code, original_name, **parse_context
    )


def _coverage_action(record: Dict[str, Any]) -> str:
    status = str(record.get("status") or "unknown")
    if status == "ok":
        return "expand_inputs_preserve_abi"
    action = str(record.get("next_action") or "")
    if action:
        return action
    owner = str(record.get("failure_owner") or "unknown")
    if owner == "unknown":
        return "review_unknown"
    return {
        "stub": "regenerate_stub",
        "testbench": "repair_testbench",
        "original": "repair_testbench",
        "abi": "repair_abi_testbench_stub",
    }.get(owner, "repair_testbench_stub")


def _completed_returncode(record: Dict[str, Any], component: str) -> Optional[int]:
    executions = [item for item in record.get("executions", []) if item.get("component") == component]
    if not executions:
        return None
    execution = executions[-1]
    returncode = execution.get("returncode")
    if (execution.get("status") == "completed" and execution.get("timeout") is False
            and isinstance(returncode, int) and not isinstance(returncode, bool)):
        return returncode
    return None


def _hidden_generation_action(record: Dict[str, Any], *, orig_code: Optional[str] = None) -> str:
    if (
        record.get("status") == "original_run_failed"
        and record.get("failure_evidence_source")
        == "original-only sanitizer/runtime"
        and record.get("failure_owner") == "unknown"
        and record.get("next_action") == "review_unknown"
        and record.get("owner_authority") == "runtime_not_isolated"
        and record.get("evidence_complete") is False
    ):
        return "repair_testbench"
    if (record.get("status") == "run_failed" and record.get("failure_owner") == "unknown"
            and record.get("next_action") == "review_unknown" and orig_code is not None):
        original = record.get("original_qualification_evidence", {})
        identity = record.get("qualification_identity", {})
        expected = {
            "original_sha256": hashlib.sha256(orig_code.encode("utf-8")).hexdigest(),
            "testbench_sha256": hashlib.sha256(str(record.get("tb_code") or "").encode("utf-8")).hexdigest(),
            "stub_sha256": hashlib.sha256(str(record.get("stub_code") or "").encode("utf-8")).hexdigest(),
        }
        original_returncode = _completed_returncode(original, "runtime")
        differential_returncode = _completed_returncode(record, "runtime")
        if (all(identity.get(key) == value for key, value in expected.items())
                and all(original.get("source_identity", {}).get(key) == expected[key]
                        for key in ("original_sha256", "testbench_sha256"))
                and original.get("status") == "ok"
                and original.get("original_entry_observed") is True
                and original.get("original_entry_executed") is True
                and _completed_returncode(original, "compile") == 0
                and _completed_returncode(original, "gcov") == 0
                and original_returncode is not None and original_returncode >= 0
                and _completed_returncode(record, "compile") == 0
                and differential_returncode is not None and differential_returncode > 0
                and differential_returncode == record.get("run_returncode")):
            # This authorizes an unfrozen generation attempt, not ownership.
            # Keeping the exact Testbench preserves its inputs and oracle.
            return "regenerate_stub"
    return _coverage_action(record)


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


def _failure_history(rounds: List[Dict[str, Any]]) -> str:
    entries = []
    for record in rounds:
        initial_error = str(record.get("initial_testbench_contract_error") or "")
        if initial_error:
            entries.append("Initial Testbench contract correction: " + initial_error)
        if record.get("status") == "ok":
            continue
        evidence = "\n".join(
            f"{stream}: {str(record[stream])[-1200:]}"
            for stream in ("compile_stderr", "run_stderr", "run_stdout")
            if record.get(stream)
        ) or str(record.get("status") or "unknown")
        entries.append(
            "Round {}: status={}; owner={}; action={}; evidence={}".format(
                record.get("round"),
                record.get("status"),
                record.get("failure_owner"),
                record.get("next_action"),
                evidence,
            )
        )
        interface_evidence = record.get("original_interface_evidence")
        if isinstance(interface_evidence, dict) and interface_evidence.get("status") == "mismatch":
            entries.append("Original interface evidence: " + json.dumps(interface_evidence, sort_keys=True))
    return chr(10).join(entries)


# -------------------------- prompt builders --------------------------

def _initial_user_message(
    orig_code: str,
    kernel_name: str,
    pinned_public_hls_decl: Optional[str] = None,
    input_domain_contract: Optional[Dict[str, Any]] = None,
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
        (
            "For every Candidate pointer or array argument, use backing "
            "storage with one fixed capacity across every Candidate call. "
            "The capacity must cover the largest tested access range; use "
            "the logical length argument to select each smaller case. Do "
            "not pass variable-sized vector storage or allocations sized "
            "only to the current case, because RTL cosimulation dumps the "
            "full declared interface depth on every call."
        ),
    ]
    if input_domain_contract is not None:
        parts.extend(
            [
                "",
                "FROZEN LEGAL INPUT-DOMAIN CONTRACT (shared by Public, Candidate, and Hidden):",
                json.dumps(input_domain_contract, ensure_ascii=False, sort_keys=True),
                "Every Public case must stay inside this finite range and include the maximum declared values. "
                "Fixed backing storage must cover the declared maximum capacity; do not infer the legal range from a smaller example. "
                "Give every Candidate declaration parameter an explicit name. Use the contract's top-level port names exactly; never emit an unnamed parameter.",
            ]
        )
    parts.extend([
        "",
        "If the compiler later confirms that the public Candidate ABI is "
        "transformed from the Original interface, the qualification step will "
        "request a source-grounded shared observation block. Do not invent an "
        "adapter or change observable meaning merely to satisfy this note.",
    ])
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
                "CRITICAL — HELD-OUT GOLDEN ORACLE CONTRACT:",
                (
                    f"The Testbench must make at least one actual call to "
                    f"`{kernel_name}` and at least one actual call to "
                    f"`{hls_name}`."
                ),
                (
                    "Expected outputs must come from the actual Original-top "
                    "call. Do not implement or use a Testbench-owned semantic "
                    "reference model, golden model, oracle algorithm, copied "
                    "implementation, or local replacement for the Original."
                ),
                (
                    "For stateful Originals, use one representative Original "
                    "invocation in the fresh Testbench process rather than "
                    "repeated calls without a verified public reset."
                ),
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
                "When the Testbench exercises multiple cases, do not return "
                "from the first mismatch. Complete every planned Original "
                "call first, so Original-only qualification can observe a "
                "later crash or memory error on another legal input. Accumulate "
                "mismatches and return non-zero only after all Original and "
                "Candidate calls for the planned cases have completed."
            ),
            (
                "Keep the Original call sequence independent of Candidate "
                "results: a Candidate mismatch must never skip a later "
                "Original call."
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


def _initial_hidden_testbench_contract_repair_message(
    kernel_name: str,
    pinned_hls_decl: str,
    failure_excerpt: str,
) -> str:
    hls_name = f"{kernel_name}_hls"
    evidence = (failure_excerpt.strip() or "unknown contract failure")[-1500:]
    return (
        "Your previous held-out Testbench artifact failed the static "
        "black-box/frozen-ABI contract before tool qualification.\n\n"
        "Contract evidence:\n```\n"
        + evidence
        + "\n```\n\n"
        "Return one complete replacement held-out Testbench. Preserve the "
        "externally frozen Public-derived Candidate declaration below "
        "character-for-character; do not change linkage, return type, "
        "function name, parameter order/types, qualifiers, pointer/array "
        "notation, typedef spelling, or the trailing declaration form.\n"
        "```cpp\n"
        + pinned_hls_decl.strip().rstrip(";")
        + ";\n```\n"
        f"Forward-declare and actually call both `{kernel_name}` and "
        f"`{hls_name}`. Do not define, stub, wrap, alias, or reimplement "
        "either top. Preserve the Original top's C/C++ language linkage "
        "exactly as shown in the Original source; do not add or remove "
        '``extern \"C\"``. Expected outputs must come from the actual Original '
        "call; do not add a local semantic oracle or depend on "
        "implementation-private state. Keep all external declarations "
        "within the Original/Candidate black-box surface.\n"
        "Reply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


def _stub_request_message(
    kernel_name: Optional[str] = None,
    pinned_hls_decl: Optional[str] = None,
    failure_excerpt: Optional[str] = None,
    failure_owner: str = "stub",
    previous_stub: str = "",
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
            ("\n\nGeneration-time evidence; ownership remains unknown:\n"
             if failure_owner == "unknown" else "\n\nStub-owned tool evidence from the previous attempt:\n")
            + "```\n"
            + failure_excerpt.strip()
            + "\n```\nAttempt to correct only the Stub. Keep the Testbench unchanged. "
            "Do not assume this proves a Stub defect or change comparison semantics."
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
        "implementation. Preserve the Original's observable output and state "
        "behavior for legal inputs, including incoming values of outputs that "
        "the Original does not update. Do not replace that behavior with the "
        "Testbench's sample values or suppress a failed comparison. "
        "Prefer straightforward, compile-safe, HLS-friendly "
        "C++ with fixed-size storage, ordinary loops, and named helper "
        "functions. Avoid dynamic allocation, exceptions, recursion, generic "
        "or nested lambdas, complex capture rules, and unnecessary STL unless "
        "the frozen ABI makes them unavoidable. Verify that every identifier "
        "is declared in scope, every referenced local is legally accessible, "
        "and every required standard header is present. When tool evidence is "
        "provided, materially replace the failing construct instead of "
        "repeating it with superficial edits. If the Stub calls the Original, "
        "include only its "
        "required forward declaration ending in `;`."
        + pinned
        + evidence
        + ("\nPrevious qualification Stub:\n```cpp\n" + previous_stub + "\n```" if previous_stub else "")
        + "\nReply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


def _empty_stub_request_message(
    hls_name: str,
    pinned_hls_decl: Optional[str] = None,
    failure_history: str = "",
    previous_stub: str = "",
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
        f"Write a minimal synthesis probe implementation of `{hls_name}` for an "
        "ABI-only CSYNTH check. Define the Candidate top exactly once. "
        "Preserve the exact C/C++ linkage and full frozen ABI. Do not include "
        "`main`, do not define the Original top, and do not add an extra "
        '`extern "C"` wrapper that changes linkage. The body may return a '
        "default value or do nothing for `void`."
        " Preserve enough legal port use or explicit tool interface directives "
        "for the synthesizer to infer the actual interface; an unused port "
        "being optimized away is not evidence that the frozen ABI is unsupported. "
        "Use synthesis-compatible toolchain types and headers. Do not implement "
        "the algorithm or change any frozen declaration. Include official headers "
        "for declared types. Never invent or redeclare types inside `std` or any "
        "other standard-library or vendor-tool namespace, and never shadow a "
        "library or toolchain type. If CSYNTH rejects the exact ABI, leave that "
        "evidence visible for the existing coordinated ABI-correction path."
        + pinned
        + ("\nPrevious synthesis probe:\n```cpp\n" + previous_stub + "\n```" if previous_stub else "")
        + ("\nAll previous synthesis failures:\n```\n" + failure_history + "\n```" if failure_history else "")
        + "\nReply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


def _hls_friendly_rewrite_message(
    hls_name: str,
    csynth_err: str,
) -> str:
    excerpt = csynth_err.strip()[-1500:] or "(no detailed error)"
    return (
        "The temporary synthesis probe failed CSYNTH. This does not prove "
        "that every implementation of the frozen ABI is unsynthesizable. "
        "This is an explicit coordinated ABI correction, "
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
    source_context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Run csynth on an empty stub. Returns (passed, error_tail_chars)."""
    os.makedirs(work_dir, exist_ok=True)
    context = dict(source_context or {})
    if context.get("source_root"):
        import shutil
        shutil.copytree(context["source_root"], work_dir, dirs_exist_ok=True)
    cv = ContextVariables(data={
        "curr_code": empty_stub_code,
        "new_kernel_name": hls_name,
        "target_profile": context.get("target_profile") or {"compile_flags": context.get("compile_flags", ())},
        "csynth_extra_sources": context.get("extra_sources", ()),
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
    previous_testbench_code: str = "",
    failure_history: str = "",
    feedback_scope: str = "public",
    original_interface_evidence: Optional[Dict[str, Any]] = None,
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

    oracle_block = ""
    if frozen_hls_decl:
        oracle_block = (
            "\n\nHELD-OUT GOLDEN ORACLE CONTRACT:\n"
            "Call the actual Original top at least once and the Candidate top "
            "at least once. Expected outputs must come from the actual "
            "Original call. Do not substitute a local semantic reference, "
            "golden model, oracle algorithm, or copied implementation."
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
        newline = chr(10)
        testbench_block = ""
        if previous_testbench_code:
            testbench_block = (
                newline * 2
                + "THE COMPLETE TESTBENCH THAT FAILED:"
                + newline
                + "CPP_BLOCK"
                + newline
                + previous_testbench_code[-24000:]
                + newline
                + "END_CPP_BLOCK"
                + newline
                + "Inspect this exact code and the tool output yourself. "
                + "Do not assume the reported owner is the root cause."
                + newline
            )
        history_block = ""
        if failure_history:
            history_block = (
                newline * 2
                + "PREVIOUS FAILED ATTEMPTS (do not repeat them):"
                + newline
                + failure_history
                + newline
            )
        scope_block = ""
        if feedback_scope == "hidden":
            scope_block = (
                newline
                + "This is Hidden-test generation before the Hidden suite is "
                + "frozen. You may change the Hidden inputs and checks, but "
                + "stay inside the frozen legal input range. Never modify the "
                + "Original or Candidate source. This feedback remains inside "
                + "Hidden generation and must not be passed to Candidate repair."
                + newline
            )
        original_interface_block = ""
        if original_interface_evidence and original_interface_evidence.get("status") == "mismatch":
            original_interface_block = (
                newline * 2
                + "COMPILER-RESOLVED ORIGINAL INTERFACE DIFFERENTIAL:"
                + newline
                + json.dumps(original_interface_evidence, sort_keys=True)
                + newline
                + "Use the Original source below to resolve typedefs and headers. "
                + "Correct the Testbench declaration and calls; preserve the "
                + "frozen Candidate ABI, legal input domain, and output checks. "
                + "Do not copy the Original implementation into the Testbench."
                + newline + "```cpp" + newline + annotated_source
                + newline + "```" + newline
            )
        return (
            f"Round {round_idx - 1} failed with status [{prev_status}] and "
            f"tool-backed owner [{owner_text}]."
            + newline
            + diagnostic_context
            + "Diagnostic excerpt:"
            + newline
            + "DIAGNOSTIC_BLOCK"
            + newline
            + evidence
            + newline
            + "END_DIAGNOSTIC_BLOCK"
            + newline
            + testbench_block
            + history_block
            + scope_block
            + original_interface_block
            + instruction
            + "\nTreat Original and Candidate implementations as black boxes. "
            "Only forward-declare their tops; do not define, stub, wrap, or "
            "depend on implementation-private globals/types/helpers. "
            "Correctness takes priority over coverage."
            + oracle_block
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
        + oracle_block
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
_ARTIFACT_RESPONSE_RETRIES = 3


def _input_domain_block(
    input_domain_contract: Optional[Dict[str, Any]],
) -> str:
    if input_domain_contract is None:
        return ""
    return (
        "\n\nFROZEN LEGAL INPUT-DOMAIN CONTRACT shared by Public, Candidate, and Hidden:\n"
        + json.dumps(input_domain_contract, ensure_ascii=False, sort_keys=True)
        + "\nAll generated and repaired test cases must remain inside this finite range. "
        "Public must exercise the declared maximum values. Hidden may choose different cases, values, combinations, and call order, but must not expand the legal range. "
        "Fixed backing storage must cover the declared maximum capacities."
    )


def validate_testbench_input_domain(
    testbench_code: str,
    candidate_name: str,
    candidate_decl: str,
    input_domain_contract: Optional[Dict[str, Any]],
    *,
    require_maximum: bool,
) -> None:
    """Leave arbitrary C++ input behavior to Original-backed execution."""

    if input_domain_contract is not None and not isinstance(
        input_domain_contract,
        dict,
    ):
        raise ModelArtifactError("input-domain contract must be a mapping")


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
    # C++ structure, symbols, declarations and linkage are compiler-owned.
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
    max_repairs: int = _ARTIFACT_RESPONSE_RETRIES,
) -> str:
    current_message = message
    current_first_turn = first_turn
    last_error: Exception | None = None
    failures: list[str] = []
    for attempt in range(max_repairs + 1):
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
            failures.append(f"Attempt {attempt}: {exc}")
            if attempt >= max_repairs:
                break
            current_first_turn = False
            current_message = (
                "Your previous response was invalid: "
                + "\n".join(failures)
                + "\nReturn exactly one complete fenced ```cpp block "
                "for the requested artifact, with no other text.\n\n" + message
            )
    raise ModelArtifactError(
        f"model did not return a valid {artifact_kind} artifact"
    ) from last_error


def _public_runtime_contract_prompt(
    *,
    orig_code: str,
    testbench_code: str,
    candidate_top_function: str,
    frozen_hls_decl: str,
    required_ports: Tuple[str, ...],
) -> str:
    return (
        "Determine conservative Vitis HLS C/RTL cosimulation depths for the "
        "frozen Candidate pointer/array interfaces used by this Public "
        "testbench. A depth counts objects of the immediate pointee type, "
        "not scalar values recursively contained inside that type. For "
        "example, an int* that points to int values[64] needs depth 64, but "
        "T (*p)[R][C] passed the address of one T[R][C] object needs depth "
        "1, not R*C. A pointer to an array of structs similarly counts "
        "struct objects, not their fields. Use the maximum contiguous "
        "top-level object span used by any single Candidate call. Do not "
        "add depths across sequential test cases or repeated calls that "
        "reuse storage. Prefer a safe upper bound in these top-level units "
        "when an exact bound is unclear.\n\n"
        f"Candidate top: {candidate_top_function}\n"
        "Frozen Candidate ABI:\n```cpp\n"
        + frozen_hls_decl.strip()
        + "\n```\n"
        "Required depth port names (include each exactly once): "
        + ", ".join(required_ports)
        + "\n\nOriginal source:\n```cpp\n"
        + orig_code.rstrip()
        + "\n```\n\nPublic testbench:\n```cpp\n"
        + testbench_code.rstrip()
        + "\n```\n\nReturn exactly one JSON object and no Markdown or commentary:\n"
        '{"cosim_interface_depths":{"port_name":1}}\n'
        "Use only the required port names. Every depth must be a positive "
        "integer. Do not change or propose Candidate code."
    )


def _runtime_contract_from_model_response(
    content: str,
    *,
    testbench_code: str,
    candidate_top_function: str,
    frozen_hls_decl: str,
    source_path: Optional[str] = None,
    include_dirs: Tuple[str, ...] = (),
    compile_flags: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    if not isinstance(content, str):
        raise ModelArtifactError("model response content must be a string")
    cleaned = tools.general.strip_thinking(content).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ModelArtifactError(
            "interface depth response must be one strict JSON object"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "cosim_interface_depths"
    }:
        raise ModelArtifactError(
            "interface depth response must contain only "
            "cosim_interface_depths"
        )
    return validate_public_runtime_contract(
        {
            "schema_version": 2,
            "kind": "public_differential_self_check_v1",
            "candidate_mismatch_returncodes": [1],
            "cosim_interface_depths": payload["cosim_interface_depths"],
        },
        testbench_code=testbench_code,
        candidate_top_function=candidate_top_function,
        frozen_hls_decl=frozen_hls_decl,
        source_path=source_path,
        include_dirs=include_dirs,
        compile_flags=compile_flags,
    )


def generate_public_runtime_contract(
    *,
    orig_code: str,
    testbench_code: str,
    candidate_top_function: str,
    frozen_hls_decl: str,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Any = None,
    input_domain_contract: Optional[Dict[str, Any]] = None,
    source_path: Optional[str] = None,
    include_dirs: Tuple[str, ...] = (),
    compile_flags: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Generate and validate the Public COSIM depth contract."""

    required_ports = public_runtime_contract_depth_ports(
        testbench_code=testbench_code,
        candidate_top_function=candidate_top_function,
        frozen_hls_decl=frozen_hls_decl,
        source_path=source_path,
        include_dirs=include_dirs,
        compile_flags=compile_flags,
    )
    if not required_ports:
        return {
            "schema_version": 1,
            "kind": "public_differential_self_check_v1",
            "candidate_mismatch_returncodes": [1],
        }
    loader = HLSAgentLoader(
        AGENT_YAML,
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent(AGENT_NAME)
    message = _public_runtime_contract_prompt(
        orig_code=orig_code,
        testbench_code=testbench_code,
        candidate_top_function=candidate_top_function,
        frozen_hls_decl=frozen_hls_decl,
        required_ports=required_ports,
    )
    first_turn = True
    initial_message = message
    failures: list[str] = []
    last_error: Exception | None = None
    for attempt in range(_ARTIFACT_RESPONSE_RETRIES + 1):
        try:
            raw = _agent_run_once(agent, message, first_turn)
            return _runtime_contract_from_model_response(
                raw,
                testbench_code=testbench_code,
                candidate_top_function=candidate_top_function,
                frozen_hls_decl=frozen_hls_decl,
                source_path=source_path,
                include_dirs=include_dirs,
                compile_flags=compile_flags,
            )
        except ModelArtifactError as exc:
            last_error = exc
            failures.append(f"Attempt {attempt}: {exc}")
            if attempt >= _ARTIFACT_RESPONSE_RETRIES:
                break
            first_turn = False
            message = (
                "Your previous interface depth response was invalid: "
                + "\n".join(failures) + "\nReturn exactly one JSON object with only "
                "cosim_interface_depths. Include every required pointer/"
                "array port exactly once with a positive integer depth. "
                "Do not return Candidate code or commentary.\n\n" + initial_message
            )
    raise ModelArtifactError(
        "model did not return a valid Public runtime contract"
    ) from last_error


def _ensure_original_forward_declaration(
    stub_code: str,
    orig_code: str,
    kernel_name: str,
    **parse_context,
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
    declaration = extract_hls_decl_from_testbench(orig_code, kernel_name, **parse_context)
    if not declaration:
        return stub_code
    return declaration.rstrip() + ";\n\n" + stub_code.lstrip()


def _measure_qualified_coverage(
    orig_code: str,
    tb_code: str,
    stub_code: str,
    kernel_name: str,
    budget: Any = None,
    *,
    require_original_execution: bool = False,
    source_context: Optional[Dict[str, Any]] = None,
    qualification_dir: Optional[str] = None,
) -> Dict[str, Any]:
    # First qualify the exact generated inputs against the Original with a
    # no-op Candidate and sanitizers.  The model receives the raw Testbench
    # and tool evidence when this fails; no inferred root cause is inserted.
    candidate_name = f"{kernel_name}_hls"
    if qualification_dir:
        Path(qualification_dir).mkdir(parents=True, exist_ok=True)
        qualification_dir = tempfile.mkdtemp(prefix="qualification_", dir=qualification_dir)
    context = dict(source_context or {})
    context.pop("target_profile", None)
    parse_context = {
        "source_path": str(Path(context["source_root"]) / "testbench.cpp") if context.get("source_root") else None,
        "include_dirs": (context["source_root"],) if context.get("source_root") else (),
        "compile_flags": context.get("compile_flags", ()),
    }
    candidate_decl = extract_hls_decl_from_testbench(tb_code, candidate_name, **parse_context)
    if candidate_decl or require_original_execution:
        original_check = check_original_execution(
            orig_code,
            tb_code,
            candidate_decl,
            candidate_name,
            budget=budget,
            original_name=kernel_name,
            required_original_entry=kernel_name if require_original_execution else None,
            keep_dir=str(Path(qualification_dir) / "original_only") if qualification_dir else None,
            **context,
        )
        if original_check.get("status") != "ok":
            original_check.setdefault("qualification_errors", [])
            original_check["qualification_errors"].append(
                "Original-only qualification failed before differential coverage."
            )
            return {**original_check, "original_qualification_evidence": dict(original_check)}

    result = measure_coverage(
        orig_code,
        tb_code,
        stub_code,
        budget=budget,
        original_name=kernel_name,
        keep_dir=str(Path(qualification_dir) / "coverage") if qualification_dir else None,
        **context,
    )
    if candidate_decl or require_original_execution:
        result["original_qualification_evidence"] = original_check
        result["qualification_identity"] = result.get("source_identity", {})
        result["original_interface_evidence"] = original_check.get(
            "original_interface_evidence", {"status": "unknown"}
        )
    result.setdefault("qualification_errors", [])
    if (result.get("status") == "run_failed" and result.get("failure_owner") == "unknown"
            and candidate_decl and original_check.get("status") == "ok"):
        # Safe Original execution does not qualify the comparison logic.
        # Keep ownership unknown; do not silently select a repair target.
        result["original_qualification_status"] = "passed"
    lines_total = result.get("lines_total")
    lines_hit = result.get("lines_hit")
    if (
        require_original_execution
        and result.get("status") == "ok"
        and isinstance(lines_total, int)
        and not isinstance(lines_total, bool)
        and lines_total > 0
        and isinstance(lines_hit, int)
        and not isinstance(lines_hit, bool)
        and lines_hit < 1
    ):
        message = (
            f"held-out Testbench did not execute Original top {kernel_name}; "
            "gcov recorded zero executed lines in orig_code.cpp. Call the "
            "actual Original top and derive expected outputs from that call "
            "instead of a local semantic oracle."
        )
        result["status"] = "qualification_failed"
        result["failure_owner"] = "testbench"
        result["next_action"] = "repair_testbench"
        result["failure_evidence_source"] = (
            "gcov Original execution evidence"
        )
        result["compile_stderr"] = message
        result["qualification_errors"].append(message)
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



def _qualification_dir(artifact_root: Optional[str], trajectory_idx: int, round_index: int) -> Optional[str]:
    root = artifact_root or os.getenv("AGREFACTOR_TB_DEBUG_DIR")
    return str(Path(root) / f"trajectory_{trajectory_idx:03d}" / f"round_{round_index:03d}") if root else None


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
        "run_stdout": cov.get("run_stdout", "")[-2000:],
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
                else (
                    "repair_testbench"
                    if cov.get("failure_owner") == "testbench"
                    else "regenerate_stub"
                    if cov.get("failure_owner") == "stub"
                    else "review_unknown"
                )
            ),
        ),
        "failure_evidence_source": cov.get(
            "failure_evidence_source",
            "legacy coverage result",
        ),
        **{
            key: cov[key]
            for key in (
                "owner_authority", "evidence_complete", "ownership_evidence",
                "executions", "source_roles", "compile_units", "source_spans",
                "diagnostic_classification", "original_entry_observed",
                "original_entry_executed",
                "original_interface_evidence",
                "original_qualification_status", "original_qualification_evidence",
                "qualification_identity", "source_identity",
            )
            if key in cov
        },
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
    input_domain_contract: Optional[Dict[str, Any]] = None,
    source_context: Optional[Dict[str, Any]] = None,
    max_repairs: int = 3,
    hidden_generation: bool = False,
    pinned_result_mapping: Optional[Dict[str, Any]] = None,
    mapping_original_code: Optional[str] = None,
) -> Dict[str, Any]:
    if isinstance(K, bool) or not isinstance(K, int) or K < 1:
        raise ValueError("K must be a positive integer")

    loader = HLSAgentLoader(
        AGENT_YAML,
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent(AGENT_NAME)
    context = dict(source_context or {})
    parse_context = {
        "source_path": str(Path(context["source_root"]) / "testbench.cpp") if context.get("source_root") else None,
        "include_dirs": (context["source_root"],) if context.get("source_root") else (),
        "compile_flags": context.get("compile_flags", ()),
    }
    hls_name = f"{kernel_name}_hls"
    rounds: List[Dict[str, Any]] = []
    external_abi_frozen = bool(
        isinstance(pinned_hls_decl, str)
        and pinned_hls_decl.strip()
    )
    if hidden_generation and not external_abi_frozen:
        raise ValueError("Hidden generation requires a frozen Public ABI")
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
        domain_block = _input_domain_block(input_domain_contract)
        if domain_block and "FROZEN LEGAL INPUT-DOMAIN CONTRACT" not in message:
            message += domain_block
        mapping_block = frozen_mapping_instruction(pinned_result_mapping)
        if mapping_block:
            message += mapping_block
        current_message = message
        current_first_turn = first_turn
        last_error: Exception | None = None
        failures: list[str] = []
        for attempt in range(_ARTIFACT_RESPONSE_RETRIES + 1):
            try:
                value = _request_cpp_artifact(
                    agent,
                    current_message,
                    first_turn=current_first_turn,
                    artifact_kind="testbench",
                    required_symbol=hls_name,
                    max_repairs=0,
                )
                validate_testbench_top_contract(
                    value,
                    kernel_name,
                    hls_name,
                    require_original_call=external_abi_frozen,
                )
                declaration = extract_hls_decl_from_testbench(value, hls_name, **parse_context)
                if not declaration:
                    raise ModelArtifactError(
                        "Testbench does not expose a Candidate declaration"
                    )
                validate_testbench_input_domain(
                    value,
                    hls_name,
                    declaration,
                    input_domain_contract,
                    require_maximum=not external_abi_frozen,
                )
                try:
                    if pinned_result_mapping:
                        validate_frozen_result_mapping(value, pinned_result_mapping, **parse_context)
                    elif not hidden_generation:
                        mapping_source = mapping_original_code or orig_code
                        adapted_reference_code = isolate_reference_program_entry(
                            mapping_source,
                            top_function=kernel_name,
                            testbench_code=value,
                            **parse_context,
                        )
                        freeze_result_mapping(
                            value,
                            adapted_reference_code,
                            kernel_name,
                            hls_name,
                            require_explicit=True,
                            **parse_context,
                        )
                except ValueError as exc:
                    raise ModelArtifactError(str(exc)) from exc
                return value
            except ModelArtifactError as exc:
                last_error = exc
                failures.append(f"Attempt {attempt}: {exc}")
                if attempt >= _ARTIFACT_RESPONSE_RETRIES:
                    break
                current_first_turn = False
                current_message = (
                    "The previous Testbench violated the required Candidate/Testbench contract: "
                    + "\n".join(failures) + "\nGenerate a complete replacement with simple, directly auditable Candidate calls. "
                    "Every Candidate forward-declaration parameter must have an explicit name matching the frozen contract port name; never omit parameter names."
                    + domain_block + "\n\n" + message
                    + (
                        "\n\n" + mapping_generation_instruction()
                        if "no explicit common result mapping" in str(exc)
                        else ""
                    )
                )
        raise ModelArtifactError(
            "model did not return a Testbench compatible with the required "
            "Candidate/Testbench contract:\n" + "\n".join(failures)[-1500:]
        ) from last_error

    def request_stub(
        testbench_code: str,
        *,
        message: Optional[str] = None,
    ) -> Tuple[str, str]:
        declaration = extract_hls_decl_from_testbench(
            testbench_code,
            hls_name,
            **parse_context,
        )
        if not declaration:
            raise ModelArtifactError(
                "Testbench does not expose a Candidate declaration"
            )
        current_message = (
            message
            if message is not None
            else _stub_request_message(kernel_name, declaration)
        )
        last_error: Exception | None = None
        failures: list[str] = []
        for attempt in range(_ARTIFACT_RESPONSE_RETRIES + 1):
            try:
                value = _request_cpp_artifact(
                    agent,
                    current_message,
                    first_turn=False,
                    artifact_kind="stub",
                    required_symbol=hls_name,
                    max_repairs=0,
                )
                value = _ensure_original_forward_declaration(
                    value,
                    orig_code,
                    kernel_name,
                    **(parse_context if source_context else {}),
                )
                validate_stub_contract(
                    value,
                    original_name=kernel_name,
                    candidate_name=hls_name,
                    frozen_hls_decl=declaration,
                )
                return value, _normalize_declaration(declaration)
            except ModelArtifactError as exc:
                last_error = exc
                failures.append(f"Attempt {attempt}: {exc}")
                if attempt >= _ARTIFACT_RESPONSE_RETRIES:
                    break
                current_message = (
                    (message or _stub_request_message(kernel_name, declaration))
                    + "\n\nStub artifact contract failures:\n" + "\n".join(failures)
                )
        raise ModelArtifactError(
            "model did not return a Stub matching the frozen Candidate ABI"
        ) from last_error

    initial_testbench_contract_repaired = False
    initial_testbench_contract_error = ""
    try:
        testbench_code = request_testbench(
            _initial_user_message(
                orig_code,
                kernel_name,
                pinned_public_hls_decl=pinned_hls_decl,
                input_domain_contract=input_domain_contract,
            ),
            first_turn=True,
        )
        if external_abi_frozen:
            _validate_frozen_candidate_abi(
                testbench_code,
                hls_name,
                frozen_hls_decl,
                **parse_context,
            )
    except ModelArtifactError as exc:
        if not external_abi_frozen or max_repairs < 1:
            raise
        initial_testbench_contract_error = str(exc)
        testbench_code = request_testbench(
            _initial_hidden_testbench_contract_repair_message(
                kernel_name,
                frozen_hls_decl,
                initial_testbench_contract_error,
            ),
            first_turn=False,
        )
        _validate_frozen_candidate_abi(
            testbench_code,
            hls_name,
            frozen_hls_decl,
            **parse_context,
        )
        initial_testbench_contract_repaired = True

    stub_code, current_decl = request_stub(testbench_code)
    coverage = _measure_qualified_coverage(
        orig_code,
        testbench_code,
        stub_code,
        kernel_name,
        budget=budget,
        require_original_execution=external_abi_frozen,
        source_context=source_context,
        qualification_dir=_qualification_dir(artifact_root, trajectory_idx, 1),
    )

    if coverage.get("status") == "ok":
        observed_decl, observed_macros = _freeze_public_contract(
            testbench_code,
            hls_name,
            **parse_context,
        )
        if external_abi_frozen:
            _validate_frozen_candidate_abi(
                testbench_code,
                hls_name,
                frozen_hls_decl,
                **parse_context,
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
        initial_testbench_contract_repaired=(
            initial_testbench_contract_repaired
        ),
        initial_testbench_contract_error=(
            initial_testbench_contract_error[-1500:]
        ),
        frozen_public_hls_decl=frozen_hls_decl,
        frozen_public_macros=list(frozen_macros),
        abi_decl=current_decl,
    )

    # Lightweight generation keeps one successful round, but an invalid
    # generated Testbench gets three bounded correction rounds. Reusing the
    # normal round machinery preserves the exact tool diagnostic and history.
    lightweight_mode = K == 1
    remaining_repairs = max(0, max_repairs - int(initial_testbench_contract_repaired))
    if lightweight_mode and rounds[-1].get("status") != "ok":
        K = remaining_repairs + 1
    if hidden_generation:
        K = min(K, remaining_repairs + 1)
    if K == 1 and remaining_repairs > 0:
        previous = rounds[-1]
        reported_action = _coverage_action(previous)
        action = (
            _hidden_generation_action(previous, orig_code=orig_code)
            if hidden_generation
            else reported_action
        )
        if (
            external_abi_frozen
            and action == "repair_abi_testbench_stub"
        ):
            # Hidden generation may repair Original/Testbench/Stub linkage, but
            # it must never rewrite the externally frozen Candidate ABI.
            action = "repair_testbench_stub"

        if action in {
            "regenerate_stub",
            "repair_testbench",
            "repair_testbench_stub",
        }:
            testbench_reused = False
            stub_reused = False

            if action == "regenerate_stub":
                testbench_code = str(previous["tb_code"])
                declaration = (
                    frozen_hls_decl
                    or extract_hls_decl_from_testbench(
                        testbench_code,
                        hls_name,
                        **parse_context,
                    )
                )
                stub_code, current_decl = request_stub(
                    testbench_code,
                    message=_stub_request_message(
                        kernel_name,
                        declaration,
                        failure_excerpt=_failure_history(rounds),
                        failure_owner=str(previous.get("failure_owner") or "unknown"),
                        previous_stub=str(previous.get("stub_code") or ""),
                    ),
                )
                testbench_reused = True
            else:
                repair_message = _feedback_message(
                    2,
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
                    previous_testbench_code=str(
                        previous.get("tb_code") or ""
                    ),
                    failure_history=_failure_history(rounds),
                    feedback_scope=(
                        "hidden" if external_abi_frozen else "public"
                    ),
                    original_interface_evidence=previous.get("original_interface_evidence"),
                )
                if reported_action == "repair_abi_testbench_stub":
                    original_decl = _extract_seed_hls_decl(
                        orig_code,
                        kernel_name,
                    )
                    repair_message += (
                        "\n\nThis is a link/ABI failure during held-out "
                        "qualification. The Candidate ABI remains externally "
                        "frozen and MUST NOT change. Re-inspect the Original "
                        "top declaration and preserve its exact C/C++ language "
                        "linkage, return type, name, parameter types/order, and "
                        "qualifiers in the replacement Testbench."
                    )
                    if original_decl:
                        repair_message += (
                            "\n\nEXACT ORIGINAL TOP DECLARATION FROM SOURCE:\n"
                            "```cpp\n"
                            + original_decl.strip().rstrip(";")
                            + ";\n```"
                        )
                testbench_code = request_testbench(
                    repair_message,
                    first_turn=False,
                )
                if external_abi_frozen:
                    _validate_frozen_candidate_abi(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        **parse_context,
                    )
                stub_code, current_decl = request_stub(testbench_code)

            coverage = _measure_qualified_coverage(
                orig_code,
                testbench_code,
                stub_code,
                kernel_name,
                budget=budget,
                require_original_execution=external_abi_frozen,
                source_context=source_context,
                qualification_dir=_qualification_dir(artifact_root, trajectory_idx, 2),
            )
            if coverage.get("status") == "ok":
                observed_decl, observed_macros = _freeze_public_contract(
                    testbench_code,
                    hls_name,
                    **parse_context,
                )
                if external_abi_frozen:
                    _validate_frozen_candidate_abi(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        **parse_context,
                    )
                    frozen_macros = observed_macros
                else:
                    frozen_hls_decl = observed_decl
                    frozen_macros = observed_macros
                reusable_stub = stub_code
            _append_round(
                rounds,
                trajectory_idx=trajectory_idx,
                round_index=2,
                tb_code=testbench_code,
                stub_code=stub_code,
                cov=coverage,
                artifact_root=artifact_root,
                ownership_action=action,
                testbench_reused=testbench_reused,
                stub_reused=stub_reused,
                lightweight_bounded_recovery=True,
                lightweight_bounded_recovery_reported_action=(
                    reported_action
                ),
                lightweight_bounded_stub_recovery=(
                    action == "regenerate_stub"
                ),
                lightweight_bounded_testbench_recovery=(
                    action in {"repair_testbench", "repair_testbench_stub"}
                ),
                frozen_public_hls_decl=frozen_hls_decl,
                frozen_public_macros=list(frozen_macros),
                abi_decl=current_decl,
            )

    for round_index in range(2, K + 1):
        previous = rounds[-1]
        if (
            previous.get("status") == "ok"
            and (lightweight_mode or (previous.get("cov_pct") or 0.0) >= target_pct)
        ):
            break

        reported_action = _coverage_action(previous)
        action = (
            _hidden_generation_action(previous, orig_code=orig_code)
            if hidden_generation
            else reported_action
        )
        if (
            lightweight_mode
            and external_abi_frozen
            and action == "repair_abi_testbench_stub"
        ):
            action = "repair_testbench_stub"
        if action in {"review_toolchain", "review_original", "review_unknown"}:
            break
        testbench_reused = False
        stub_reused = False
        if action == "regenerate_stub":
            testbench_code = str(previous["tb_code"])
            declaration = (
                frozen_hls_decl
                or extract_hls_decl_from_testbench(
                    testbench_code,
                    hls_name,
                    **parse_context,
                )
            )
            stub_code, current_decl = request_stub(
                testbench_code,
                message=_stub_request_message(
                    kernel_name,
                    declaration,
                    failure_excerpt=_failure_history(rounds),
                    failure_owner=str(previous.get("failure_owner") or "unknown"),
                    previous_stub=str(previous.get("stub_code") or ""),
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
                    previous_testbench_code=str(
                        previous.get("tb_code") or ""
                    ),
                    failure_history=_failure_history(rounds),
                    feedback_scope=(
                        "hidden" if external_abi_frozen else "public"
                    ),
                    original_interface_evidence=previous.get("original_interface_evidence"),
                    ),
                    first_turn=False,
                )
                try:
                    _validate_frozen_public_contract(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        frozen_macros,
                        **parse_context,
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
                    previous_testbench_code=str(
                        previous.get("tb_code") or ""
                    ),
                    failure_history=_failure_history(rounds),
                    feedback_scope=(
                        "hidden" if external_abi_frozen else "public"
                    ),
                    original_interface_evidence=previous.get("original_interface_evidence"),
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
                        **parse_context,
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
                    _failure_history(rounds),
                ),
                first_turn=False,
            )
            stub_code, current_decl = request_stub(testbench_code)

        elif action == "repair_testbench_stub":
            repair_message = _feedback_message(
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
                    previous_testbench_code=str(
                        previous.get("tb_code") or ""
                    ),
                    failure_history=_failure_history(rounds),
                    feedback_scope=(
                        "hidden" if external_abi_frozen else "public"
                    ),
                    original_interface_evidence=previous.get("original_interface_evidence"),
                )

            if reported_action == "repair_abi_testbench_stub":
                original_decl = _extract_seed_hls_decl(
                    orig_code,
                    kernel_name,
                    **parse_context,
                )
                repair_message += (
                    chr(10) * 2
                    + "This is a link/ABI failure during held-out qualification. "
                    + "The Candidate ABI remains externally frozen and MUST NOT "
                    + "change. Re-inspect the Original top declaration and "
                    + "preserve its exact C/C++ language linkage, return type, "
                    + "name, parameter types/order, and qualifiers."
                )
                if original_decl:
                    repair_message += (
                        chr(10) * 2
                        + "EXACT ORIGINAL TOP DECLARATION FROM SOURCE:"
                        + chr(10)
                        + original_decl.strip().rstrip(";")
                        + ";"
                    )
            testbench_code = request_testbench(
                repair_message,
                first_turn=False,
            )
            if external_abi_frozen:
                _validate_frozen_candidate_abi(
                    testbench_code,
                    hls_name,
                    frozen_hls_decl,
                    **parse_context,
                )
            stub_code, current_decl = request_stub(testbench_code)

        try:
            if external_abi_frozen:
                _validate_frozen_candidate_abi(
                    testbench_code, hls_name, frozen_hls_decl, **parse_context,
                )
            coverage = _measure_qualified_coverage(
                orig_code,
                testbench_code,
                stub_code,
                kernel_name,
                budget=budget,
                require_original_execution=external_abi_frozen,
                source_context=source_context,
                qualification_dir=_qualification_dir(artifact_root, trajectory_idx, round_index),
            )
        except ModelArtifactError as exc:
            coverage = _coverage_contract_failure(str(exc))

        if coverage.get("status") == "ok":
            observed_decl, observed_macros = _freeze_public_contract(
                testbench_code,
                hls_name,
                **parse_context,
            )
            if external_abi_frozen:
                _validate_frozen_candidate_abi(
                    testbench_code,
                    hls_name,
                    frozen_hls_decl,
                    **parse_context,
                )
                if frozen_macros:
                    _validate_frozen_public_contract(
                        testbench_code,
                        hls_name,
                        frozen_hls_decl,
                        frozen_macros,
                        **parse_context,
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
                    **parse_context,
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
            lightweight_bounded_recovery=(
                lightweight_mode and round_index > 1
            ),
            lightweight_bounded_recovery_reported_action=(
                reported_action if lightweight_mode else None
            ),
            lightweight_bounded_stub_recovery=(
                lightweight_mode and action == "regenerate_stub"
            ),
            lightweight_bounded_testbench_recovery=(
                lightweight_mode
                and action in {
                    "repair_testbench",
                    "repair_testbench_stub",
                    "repair_abi_testbench_stub",
                }
            ),
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
        source_context=source_context,
        synth_retry_budget=max_repairs,
    )


def _finalize_trajectory(
    agent,
    rounds: List[Dict[str, Any]],
    want_sig_spec: bool,
    trajectory_idx: int,
    expected_hls_name: Optional[str] = None,
    orig_code: Optional[str] = None,
    emit_final_text: bool = True,
    synth_retry_budget: int = 3,
    budget: Any = None,
    artifact_root: Optional[str] = None,
    allow_abi_correction: bool = True,
    source_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context = dict(source_context or {})
    parse_context = {
        "source_path": str(Path(context["source_root"]) / "testbench.cpp") if context.get("source_root") else None,
        "include_dirs": (context["source_root"],) if context.get("source_root") else (),
        "compile_flags": context.get("compile_flags", ()),
    }
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
        synth_failures: List[str] = []
        while True:
            best_decl = (
                str(best.get("frozen_public_hls_decl") or "")
                or extract_hls_decl_from_testbench(
                    str(best["tb_code"]),
                    expected_hls_name,
                    **parse_context,
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
                    failure_history="\n\n".join(synth_failures),
                    previous_stub=empty_stub,
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
            synth_attempt = synth_retry_budget - retries_left
            synth_context = (
                nullcontext(str(Path(artifact_root) / f"trajectory_{trajectory_idx:03d}" / f"synth_attempt_{synth_attempt:03d}"))
                if artifact_root else tempfile.TemporaryDirectory(prefix=f"synth_check_traj{trajectory_idx}_")
            )
            with synth_context as work_dir:
                synth_ok, synth_error = _synth_check(
                    empty_stub,
                    expected_hls_name,
                    work_dir,
                    budget=budget,
                    source_context=source_context,
                )
            if (
                synth_ok
                or retries_left <= 0
            ):
                break

            retries_left -= 1
            synth_failures.append(synth_error)
            if not allow_abi_correction:
                continue
            new_tb = _request_cpp_artifact(
                agent,
                _hls_friendly_rewrite_message(
                    expected_hls_name,
                    "\n\n".join(synth_failures),
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
                **parse_context,
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
                    failure_excerpt="\n\n".join(synth_failures),
                ),
                first_turn=False,
                artifact_kind="stub",
                required_symbol=expected_hls_name,
            )
            new_stub = _ensure_original_forward_declaration(
                new_stub,
                orig_code,
                original_name,
                **parse_context,
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
                source_context=source_context,
                qualification_dir=_qualification_dir(artifact_root, trajectory_idx, rounds[-1]["round"] + 1),
            )
            frozen_decl = ""
            frozen_macros: Tuple[str, ...] = ()
            if new_coverage.get("status") == "ok":
                frozen_decl, frozen_macros = _freeze_public_contract(
                    new_tb,
                    expected_hls_name,
                    **parse_context,
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
    input_domain_contract: Optional[Dict[str, Any]] = None,
    mapping_original_code: Optional[str] = None,
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
                input_domain_contract=input_domain_contract,
                mapping_original_code=mapping_original_code,
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
                    input_domain_contract=input_domain_contract,
                    mapping_original_code=mapping_original_code,
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
        raise TestbenchGenerationExhausted(
            split="public",
            stage="public_generation_qualification",
            trajectories=trajectories,
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
            previous_testbench_code=str(prev.get("tb_code") or ""),
            failure_history=_failure_history(rounds),
            feedback_scope="hidden",
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
    input_domain_contract: Optional[Dict[str, Any]] = None,
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
        input_domain_contract=input_domain_contract,
        mapping_original_code=(
            getter("curr_code") if callable(getter) else None
        ),
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
    input_domain_contract: Optional[Dict[str, Any]] = None,
    source_context: Optional[Dict[str, Any]] = None,
    max_repairs: int = 3,
    pinned_result_mapping: Optional[Dict[str, Any]] = None,
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
    domain_sha = (
        hashlib.sha256(
            json.dumps(
                input_domain_contract,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if input_domain_contract is not None
        else ""
    )
    orig_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()
    mapping_sha = pinned_result_mapping.get("sha256", "") if pinned_result_mapping else ""
    cache_key = cache_key or kernel_name
    if cache_dir is not None:
        cached = _load_golden_cache(
            cache_dir,
            cache_key,
            orig_sha,
            expected_domain_sha=domain_sha,
            expected_result_mapping_sha=mapping_sha,
        )
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
                hidden_generation=True,
                emit_final_text=False,
                budget=budget,
                artifact_root=artifact_root,
                input_domain_contract=input_domain_contract,
                source_context=source_context,
                max_repairs=max_repairs,
                pinned_result_mapping=pinned_result_mapping,
            ): index
            for index in range(M)
        }
        trajectories: List[Dict[str, Any]] = []
        for future in concurrent.futures.as_completed(futures):
            try:
                trajectories.append(future.result())
            except Exception as exc:
                if artifact_root:
                    error_dir = Path(artifact_root) / f"trajectory_{futures[future]:03d}"
                    error_dir.mkdir(parents=True, exist_ok=True)
                    (error_dir / "exception.json").write_text(
                        json.dumps({
                            "evidence_view": "operator_full",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "traceback": traceback.format_exc(),
                        }, indent=2, ensure_ascii=True),
                        encoding="utf-8",
                    )
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
        raise TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=trajectories,
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
        "input_domain_contract_sha256": domain_sha,
        "result_mapping_sha256": mapping_sha,
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


def _load_golden_cache(
    cache_dir: str,
    kernel_name: str,
    expected_sha: str,
    expected_domain_sha: str = "",
    expected_result_mapping_sha: str = "",
) -> Optional[Dict[str, Any]]:
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
    if expected_domain_sha and data.get("input_domain_contract_sha256") != expected_domain_sha:
        return None
    if expected_result_mapping_sha and data.get("result_mapping_sha256") != expected_result_mapping_sha:
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
