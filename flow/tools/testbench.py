from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from autogen.agentchat.group import ContextVariables  # type: ignore

from flow.base_agent import HLSAgentLoader
from agrefactor.reference_source import isolate_reference_program_entry
from agrefactor.runtime.budget import BudgetExceededError
import flow.tools as tools
from flow.tools.result_mapping import (
    ResultMappingVerificationError,
    freeze_result_mapping,
    mapping_generation_instruction,
)


_ORIGINAL_EXECUTION_REPAIRS = 3


def _build_testbench_request(
    original_code: str,
    kernel_name: str,
    input_domain_contract: Optional[Dict[str, Any]] = None,
    source_package_context: Optional[list[Dict[str, str]]] = None,
) -> str:
    hls_name = f"{kernel_name}_hls"
    request = (
        "Original kernel source code:\n"
        f"```cpp\n{original_code.rstrip()}\n```\n\n"
        f"Original golden top: {kernel_name}\n"
        f"Candidate HLS top: {hls_name}\n\n"
        "Generate exactly one complete deterministic C++ host testbench. "
        "Treat the original and Candidate implementations as read-only black "
        "boxes. The testbench may own headers, macros, data, and local helper "
        "definitions, but its only external function forward declarations "
        f"must be `{kernel_name}` and `{hls_name}`. Forward-declare those tops "
        "only; never define, stub, wrap, alias, or reimplement either top in "
        "the testbench. Do not declare, read, write, or reset implementation-"
        "private globals. Do not copy or depend on implementation-private "
        "types, helper functions, allocator state, or internal data "
        "structures. Use only public arguments and outputs.\n\n"
        "Use independent mutable input/output storage for the golden and "
        "Candidate calls, preserve exact C/C++ language linkage, and keep "
        "each side in an equivalent clean logical state. Correctness and a "
        "real golden-vs-Candidate comparison take priority over testcase "
        "count or coverage. For every Candidate pointer or array argument, "
        "use backing storage with one fixed capacity across every Candidate "
        "call. That capacity must cover the largest tested access range; "
        "smaller cases vary only the logical length. Do not pass variable-"
        "sized vector storage or allocations sized only to the current case, "
        "because RTL cosimulation dumps the full declared interface depth on "
        "every call. When exercising multiple cases, complete every planned "
        "Original call even if an earlier comparison fails. Accumulate "
        "mismatches and return non-zero only after all planned cases finish, "
        "so a Candidate mismatch cannot skip a later Original call. On "
        "mismatch, emit a useful stderr message and return non-zero; otherwise "
        "return zero.\n\n"
        "Reply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )
    if input_domain_contract is not None:
        request += (
            "\n\nFROZEN LEGAL INPUT-DOMAIN CONTRACT shared by Public, Candidate, and Hidden:\n"
            + json.dumps(input_domain_contract, ensure_ascii=False, sort_keys=True)
            + "\nEvery Public case must remain inside this range and include its maximum values; fixed storage must cover the declared capacities. Preserve the contract's top-level port names in the Candidate declaration."
        )
    if source_package_context:
        request += "\n\nREAD-ONLY SOURCE PACKAGE CONTEXT:\n" + "\n".join(
            f"// {item.get('path')}\n{item.get('content')}"
            for item in source_package_context
        )
    request += (
        "\n\nIf compiler-backed qualification later confirms a transformed "
        "public Candidate ABI, the qualification step will request a shared "
        "source-grounded observation block. Do not invent an adapter or "
        "change observable meaning merely to satisfy this note."
    )
    return request


def _build_instruction_request(
    kernel_name: str,
    hls_name: str,
) -> str:
    return (
        "Using only the Public Testbench you just produced, write a VERY "
        "SHORT refactoring instruction of at most four bullet points. "
        f"Include the exact `{hls_name}` declaration and only the public "
        "Testbench-owned macros/types that the Candidate implementation must "
        "share. Do not mention or require implementation-private globals, "
        f"private types, private helpers, or internals of `{kernel_name}`. "
        "Do not add commentary about equivalence or synthesizability."
    )


def _original_execution_repair_request(
    *,
    original_code: str,
    kernel_name: str,
    testbench_code: str,
    result: Dict[str, Any],
    input_domain_contract: Optional[Dict[str, Any]],
    prior_failures: tuple[str, ...] = (),
    source_package_context: Optional[list[Dict[str, str]]] = None,
) -> str:
    evidence = "\n".join(
        str(result.get(field) or "").strip()
        for field in ("compile_stderr", "run_stderr", "run_stdout")
        if str(result.get(field) or "").strip()
    )
    return (
        "The generated Public Testbench failed Original-only execution "
        "qualification. The Original source must not be modified. Inspect the "
        "complete failing Testbench and the real tool output below, determine "
        "which generated inputs trigger the Original failure, and return one "
        "complete replacement Public Testbench. Keep all replacement inputs "
        "inside the frozen legal input-domain contract and still exercise its "
        "declared maximums. Complete every planned Original call before "
        "returning a mismatch, and do not let Candidate results skip a later "
        "Original call.\n\n"
        "PREVIOUS PUBLIC TESTBENCH:\n```cpp\n"
        + testbench_code.rstrip()
        + "\n```\n\nREAL TOOL OUTPUT:\n```text\n"
        + (evidence[-12000:] or "(no detailed tool output)")
        + "\n```\n\n"
        + "PREVIOUS QUALIFICATION FAILURES:\n"
        + "\n\n".join(prior_failures)
        + "\n\n"
        + _build_testbench_request(
            original_code,
            kernel_name,
            input_domain_contract,
            source_package_context,
        )
    )


def gen_tb_prior(
    cv: ContextVariables,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Any = None,
):
    loader = HLSAgentLoader(
        "flow/agents/testbench.yaml",
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent("tb_creator")
    kernel_name = str(cv["kernel_name"])
    hls_name = f"{kernel_name}_hls"
    package = cv.get("source_package") or {}
    repair_limit = cv.get("max_testbench_repair_attempts", _ORIGINAL_EXECUTION_REPAIRS)
    source_root = package.get("root")
    compile_flags = tuple((cv.get("target_profile") or {}).get("compile_flags", ()))
    parse_context = {
        "source_path": str(Path(source_root) / "testbench.cpp") if source_root else None,
        "include_dirs": (source_root,) if source_root else (),
        "compile_flags": compile_flags,
    }
    execution_context = {
        "source_root": source_root,
        "compile_flags": compile_flags,
        "extra_sources": tuple(str(Path(source_root) / name) for name in package.get("extra_sources", ())),
    }
    reference_code = str(cv.get("orig_code") or cv["curr_code"])
    contract_failures: list[str] = []
    mapping_failures: list[str] = []
    public_failures: list[str] = []
    mapping_rounds: list[Dict[str, Any]] = []
    mapping_repairs_used = 0

    def save_mapping_events() -> None:
        cv["public_result_mapping_qualification"] = {
            "qualification_checks_used": len(mapping_rounds),
            "mapping_repair_requests_used": mapping_repairs_used,
            "rounds": [dict(item) for item in mapping_rounds],
        }

    def raise_mapping_exhausted(*, verification_incomplete: bool = False) -> None:
        raise tools.tb_optimizer.TestbenchGenerationExhausted(
            split="public",
            stage=("public_abi_mapping_verification" if verification_incomplete
                   else "public_abi_mapping_qualification"),
            trajectories=[{
                "trajectory_idx": 0,
                "error": "\n".join(mapping_failures),
                "qualification_checks_used": len(mapping_rounds),
                "repairs_used": mapping_repairs_used,
                "mapping_qualification_checks_used": len(mapping_rounds),
                "mapping_repair_requests_used": mapping_repairs_used,
                "rounds": [dict(item) for item in mapping_rounds],
            }],
        )

    def validate_mapping_contract(current_tb: str, context: str) -> None:
        event: Dict[str, Any] = {
            "round": len(mapping_rounds) + 1,
            "qualification_context": context,
            "repair_requested": False,
        }
        mapping_rounds.append(event)
        try:
            adapted_reference_code = isolate_reference_program_entry(
                str(cv.get("curr_code") or reference_code),
                top_function=kernel_name,
                testbench_code=current_tb,
                **parse_context,
            )
            mapping = freeze_result_mapping(
                current_tb,
                adapted_reference_code,
                kernel_name,
                hls_name,
                require_explicit=True,
                **parse_context,
            )
        except ValueError as exc:
            evidence = f"{context}: {exc}"
            mapping_failures.append(evidence)
            public_failures.append(evidence)
            event.update(status="contract_failed", error=str(exc),
                         failure_owner="testbench", next_action="repair_testbench")
            if isinstance(exc, ResultMappingVerificationError):
                cv["public_result_mapping_unverified"] = exc.partial_mapping
                cv["public_result_mapping_diagnostics"] = exc.diagnostics
                event.update(partial_mapping=exc.partial_mapping, diagnostics=exc.diagnostics)
            if isinstance(exc, ResultMappingVerificationError) and not exc.repair_eligible:
                event.update(status="contract_incomplete", failure_owner="unknown",
                             next_action="review_unknown", repair_eligible=False)
                save_mapping_events()
                raise_mapping_exhausted(verification_incomplete=True)
            save_mapping_events()
            raise
        event.update(status="ok", failure_owner="none", next_action="continue_validation")
        cv["public_result_mapping"] = mapping
        cv["public_result_mapping_unverified"] = None
        cv["public_result_mapping_diagnostics"] = None
        save_mapping_events()

    def request_mapping_repair(current_tb: str) -> str:
        nonlocal mapping_repairs_used
        if mapping_repairs_used >= repair_limit:
            raise_mapping_exhausted()
        message = (
            "All previous Public generation and qualification failures:\n"
            + "\n\n".join(public_failures)
            + "\n\nCURRENT PUBLIC TESTBENCH:\n```cpp\n"
            + current_tb.rstrip()
            + "\n```\n\nReturn a complete replacement with an explicit shared result mapping. "
            "Preserve the current Candidate ABI unless the preceding evidence explicitly "
            "requested coordinated ABI correction.\n\n"
            + mapping_generation_instruction() + "\n\n"
            + _build_testbench_request(reference_code, kernel_name,
                cv.get("input_domain_contract"), cv.get("source_package_context"))
        )
        request_started = True
        try:
            return tools.tb_optimizer._request_cpp_artifact(
                agent, message, first_turn=False, artifact_kind="testbench_repair",
                required_symbol=hls_name, max_repairs=0,
            )
        except BudgetExceededError:
            request_started = False
            raise
        finally:
            if request_started:
                mapping_repairs_used += 1
                mapping_rounds[-1]["repair_requested"] = True
                save_mapping_events()

    tb = tools.tb_optimizer._request_cpp_artifact(
        agent,
        _build_testbench_request(
            reference_code,
            kernel_name,
            cv.get("input_domain_contract"),
            cv.get("source_package_context"),
        ),
        first_turn=True,
        artifact_kind="testbench",
        required_symbol=hls_name,
        max_repairs=repair_limit,
    )
    for repair_index in range(repair_limit + 1):
        mapping_failure = False
        try:
            tools.tb_optimizer.validate_testbench_top_contract(tb, kernel_name, hls_name)
            tools.tb_optimizer.validate_testbench_input_domain(
                tb, hls_name,
                tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
                cv.get("input_domain_contract"), require_maximum=True,
            )
            try:
                # Use the same adapted Original context as flow/new.py's final
                # gate for every generated Testbench revision.
                validate_mapping_contract(tb, f"Validation {repair_index}")
            except ValueError as exc:
                mapping_failure = True
                raise tools.tb_optimizer.ModelArtifactError(str(exc)) from exc
            break
        except tools.tb_optimizer.ModelArtifactError as exc:
            contract_failures.append(f"Validation {repair_index}: {exc}")
            cv["public_contract_failures"] = list(contract_failures)
            if mapping_failure:
                if repair_index == repair_limit:
                    raise_mapping_exhausted()
                tb = request_mapping_repair(tb)
                continue
            public_failures.append(contract_failures[-1])
            if repair_index == repair_limit:
                raise
            tb = tools.tb_optimizer._request_cpp_artifact(
                agent,
                "Public Testbench contract failures:\n" + "\n".join(public_failures)
                + "\nReturn a complete replacement.\n\n"
                + mapping_generation_instruction() + "\n\n"
                + _build_testbench_request(reference_code, kernel_name,
                    cv.get("input_domain_contract"), cv.get("source_package_context")),
                first_turn=False, artifact_kind="testbench", required_symbol=hls_name,
                max_repairs=repair_limit,
            )

    qualification_failures: list[str] = []
    for repair_index in range(repair_limit + 1):
        candidate_decl = tools.tb_optimizer.extract_hls_decl_from_testbench(
            tb,
            hls_name,
            **parse_context,
        )
        original_result = tools.tb_coverage.check_original_execution(
            isolate_reference_program_entry(
                str(cv["curr_code"]), top_function=kernel_name, testbench_code=tb,
                source_path=parse_context["source_path"], include_dirs=parse_context["include_dirs"],
                compile_flags=compile_flags,
            ),
            tb,
            candidate_decl,
            hls_name,
            budget=budget,
            original_name=kernel_name,
            **execution_context,
        )
        if original_result.get("status") == "ok":
            break
        owner = original_result.get("failure_owner", "unknown")
        if owner in {"stub", "toolchain"}:
            raise tools.tb_optimizer.TestbenchGenerationExhausted(
                split="public", stage="public_original_qualification",
                trajectories=[{"trajectory_idx": 0, "rounds": [original_result]}],
            )
        qualification_failures.append(
            f"Validation {repair_index}; owner={owner}; status={original_result.get('status')}\n"
            + "\n".join(str(original_result.get(key) or '') for key in ("compile_stderr", "run_stderr", "run_stdout"))[-6000:]
        )
        public_failures.append(qualification_failures[-1])
        cv["public_original_qualification_failures"] = list(qualification_failures)
        if repair_index >= repair_limit:
            raise tools.tb_optimizer.ModelArtifactError(
                "Public Testbench still fails Original-only execution after "
                f"{repair_limit} repair attempts"
            )
        tb = tools.tb_optimizer._request_cpp_artifact(
            agent,
            _original_execution_repair_request(
                original_code=reference_code,
                kernel_name=kernel_name,
                testbench_code=tb,
                result=original_result,
                input_domain_contract=cv.get("input_domain_contract"),
                prior_failures=tuple(public_failures),
                source_package_context=cv.get("source_package_context"),
            ) + "\n\n" + mapping_generation_instruction(),
            first_turn=False,
            artifact_kind="testbench_repair",
            required_symbol=hls_name,
            max_repairs=repair_limit,
        )
        tools.tb_optimizer.validate_testbench_top_contract(
            tb,
            kernel_name,
            hls_name,
        )
        tools.tb_optimizer.validate_testbench_input_domain(
            tb,
            hls_name,
            tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
            cv.get("input_domain_contract"),
            require_maximum=True,
        )
        while True:
            try:
                validate_mapping_contract(tb, f"Original qualification repair {repair_index}")
                break
            except ValueError:
                tb = request_mapping_repair(tb)
                tools.tb_optimizer.validate_testbench_top_contract(tb, kernel_name, hls_name)
                tools.tb_optimizer.validate_testbench_input_domain(
                    tb,
                    hls_name,
                    tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
                    cv.get("input_domain_contract"),
                    require_maximum=True,
                )

    # Validate the public Candidate ABI with the same Vitis probe used by the
    # held-out path before freezing it for downstream stages. This keeps an
    # unsynthesizable public declaration from becoming an unrecoverable
    # hidden-stage contract.
    if cv.get("target_profile"):
        synth_failures: list[str] = []
        synth_rounds: list[Dict[str, Any]] = []
        previous_probe = ""
        probe_repair_requests_used = 0
        abi_repair_requests_used = 0

        def probe_trajectory() -> Dict[str, Any]:
            return {
                "trajectory_idx": 0,
                "rounds": [dict(item) for item in synth_rounds],
                "qualification_checks_used": len(synth_rounds),
                "repairs_used": probe_repair_requests_used + abi_repair_requests_used,
                "probe_qualification_checks_used": len(synth_rounds),
                "probe_repair_requests_used": probe_repair_requests_used,
                "abi_repair_requests_used": abi_repair_requests_used,
                "synthesis_calls_used": sum(bool(item["synthesis_attempted"]) for item in synth_rounds),
                "error": "\n\n".join(synth_failures),
            }

        for synth_index in range(repair_limit + 1):
            candidate_decl = tools.tb_optimizer.extract_hls_decl_from_testbench(
                tb, hls_name, **parse_context,
            )
            probe_type_contract = tools.tb_optimizer.freeze_public_type_contract(
                tb, hls_name, **parse_context,
            )
            probe_request_started = True
            try:
                stub = tools.tb_optimizer._request_cpp_artifact(
                    agent,
                    tools.tb_optimizer._empty_stub_request_message(
                        hls_name,
                        candidate_decl,
                        failure_history="\n\n".join(synth_failures),
                        previous_stub=previous_probe,
                    ) + tools.tb_optimizer.frozen_type_instruction(probe_type_contract),
                    first_turn=False,
                    artifact_kind="empty_stub",
                    required_symbol=hls_name,
                    max_repairs=repair_limit,
                )
            except BudgetExceededError:
                probe_request_started = False
                raise
            finally:
                if synth_index > 0 and probe_request_started:
                    probe_repair_requests_used += 1
                cv["public_abi_probe_qualification"] = probe_trajectory()
            try:
                tools.tb_optimizer.validate_stub_contract(
                    stub,
                    original_name=kernel_name,
                    candidate_name=hls_name,
                    frozen_hls_decl=candidate_decl,
                    frozen_type_contract=probe_type_contract,
                    **parse_context,
                )
            except tools.tb_optimizer.ModelArtifactError as exc:
                if getattr(exc, "repair_eligible", True) is False:
                    raise
                synth_failures.append(str(exc))
                previous_probe = stub
                public_failures.append(f"ABI probe contract {synth_index}: {exc}")
                synth_rounds.append({
                    "round": synth_index + 1, "status": "probe_contract_failed",
                    "failure_owner": "stub", "next_action": "regenerate_stub",
                    "error": str(exc), "compile_stderr": str(exc),
                    "synthesis_attempted": False,
                })
                cv["public_abi_probe_qualification"] = probe_trajectory()
                if synth_index >= repair_limit:
                    raise tools.tb_optimizer.TestbenchGenerationExhausted(
                        split="public", stage="public_abi_probe_qualification",
                        trajectories=[probe_trajectory()],
                    ) from exc
                continue
            with tempfile.TemporaryDirectory(prefix="public_synth_check_") as work_dir:
                synth_ok, synth_error = tools.tb_optimizer._synth_check(
                    stub,
                    hls_name,
                    work_dir,
                    budget=budget,
                    source_context={
                        **execution_context,
                        "target_profile": cv.get("target_profile") or {},
                    },
                )
            synth_rounds.append({
                "round": synth_index + 1,
                "status": "ok" if synth_ok else "synth_failed",
                "failure_owner": "none" if synth_ok else "unknown",
                "next_action": "continue_validation" if synth_ok else "review_unknown",
                "synth_error": synth_error, "synthesis_attempted": True,
            })
            cv["public_abi_probe_qualification"] = probe_trajectory()
            if synth_ok:
                break
            synth_failures.append(synth_error)
            public_failures.append(f"ABI synthesis qualification {synth_index}: {synth_error}")
            if synth_index >= repair_limit:
                raise tools.tb_optimizer.TestbenchGenerationExhausted(
                    split="public",
                    stage="public_abi_synthesis_qualification",
                    trajectories=[probe_trajectory()],
                )
            abi_request_started = True
            try:
                tb = tools.tb_optimizer._request_cpp_artifact(
                    agent,
                    tools.tb_optimizer._hls_friendly_rewrite_message(
                        hls_name,
                        "\n\n".join(synth_failures),
                    ) + "\n\nAll previous Public generation and qualification failures:\n"
                    + "\n\n".join(public_failures)
                    + "\n\n" + mapping_generation_instruction()
                    + "\n\n" + _build_testbench_request(reference_code, kernel_name,
                        cv.get("input_domain_contract"), cv.get("source_package_context")),
                    first_turn=False,
                    artifact_kind="testbench",
                    required_symbol=hls_name,
                    max_repairs=repair_limit,
                )
            except BudgetExceededError:
                abi_request_started = False
                raise
            finally:
                if abi_request_started:
                    abi_repair_requests_used += 1
                cv["public_abi_probe_qualification"] = probe_trajectory()
            previous_probe = ""
            tools.tb_optimizer.validate_testbench_top_contract(tb, kernel_name, hls_name)
            tools.tb_optimizer.validate_testbench_input_domain(
                tb,
                hls_name,
                tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
                cv.get("input_domain_contract"),
                require_maximum=True,
            )
            while True:
                try:
                    validate_mapping_contract(tb, f"ABI correction {synth_index}")
                    break
                except ValueError:
                    tb = request_mapping_repair(tb)
                tools.tb_optimizer.validate_testbench_top_contract(tb, kernel_name, hls_name)
                tools.tb_optimizer.validate_testbench_input_domain(
                    tb,
                    hls_name,
                    tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
                    cv.get("input_domain_contract"),
                    require_maximum=True,
                )
            original_result = tools.tb_coverage.check_original_execution(
                isolate_reference_program_entry(
                    str(cv["curr_code"]), top_function=kernel_name, testbench_code=tb,
                    source_path=parse_context["source_path"], include_dirs=parse_context["include_dirs"],
                    compile_flags=compile_flags,
                ),
                tb,
                tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
                hls_name,
                budget=budget,
                original_name=kernel_name,
                **execution_context,
            )
            if original_result.get("status") != "ok":
                raise tools.tb_optimizer.TestbenchGenerationExhausted(
                    split="public",
                    stage="public_original_qualification_after_abi_correction",
                    trajectories=[{"trajectory_idx": 0, "rounds": [original_result]}],
                )

    response = agent.run(
        message=_build_instruction_request(kernel_name, hls_name),
        max_turns=1,
        clear_history=False,
    )
    response.process()
    messages = getattr(response, "messages", None)
    if not isinstance(messages, list) or not messages:
        raise RuntimeError("instruction agent returned no messages")
    terminal = messages[-1]
    if not isinstance(terminal, dict):
        raise RuntimeError("instruction agent terminal message is invalid")
    instruction = terminal.get("content")
    if not isinstance(instruction, str) or not instruction.strip():
        raise RuntimeError("instruction agent returned empty content")
    instruction = tools.general.strip_thinking(instruction).strip()
    return tb, instruction, hls_name

def qualify_external_public_testbench(
    cv: ContextVariables,
    testbench_code: str,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Any = None,
    *,
    require_explicit: bool = False,
) -> str:
    """Qualify supplied Public Testbench before binding its Candidate ABI."""
    if not isinstance(testbench_code, str) or not testbench_code.strip():
        raise tools.tb_optimizer.ModelArtifactError(
            "external Public Testbench must not be empty"
        )

    kernel_name = str(cv["kernel_name"])
    hls_name = str(cv.get("new_kernel_name") or f"{kernel_name}_hls")
    package = cv.get("source_package") or {}
    source_root = package.get("root")
    compile_flags = tuple(
        (cv.get("target_profile") or {}).get("compile_flags", ())
    )
    parse_context = {
        "source_path": (
            str(Path(source_root) / "testbench.cpp")
            if source_root
            else None
        ),
        "include_dirs": (source_root,) if source_root else (),
        "compile_flags": compile_flags,
    }
    execution_context = {
        "source_root": source_root,
        "compile_flags": compile_flags,
        "extra_sources": tuple(
            str(Path(source_root) / name)
            for name in package.get("extra_sources", ())
        ),
    }

    qualification_root = None
    artifact_root = cv.get("public_tb_artifact_dir")
    if artifact_root:
        qualification_root = Path(str(artifact_root)) / "provided_qualification"
        qualification_root.mkdir(parents=True, exist_ok=True)
        existing = sorted(qualification_root.glob("attempt_*"))
        attempt_dir = qualification_root / f"attempt_{len(existing) + 1:03d}"
        attempt_dir.mkdir()
        original_dir = attempt_dir / "original_execution"
        stub_dir = attempt_dir / "stub_synthesis"
        original_dir.mkdir()
        stub_dir.mkdir()
    else:
        attempt_dir = original_dir = stub_dir = None

    tools.tb_optimizer.validate_testbench_top_contract(
        testbench_code,
        kernel_name,
        hls_name,
    )
    candidate_decl = tools.tb_optimizer.extract_hls_decl_from_testbench(
        testbench_code,
        hls_name,
        **parse_context,
    )
    if not candidate_decl:
        raise tools.tb_optimizer.ModelArtifactError(
            "external Public Testbench did not expose a Candidate declaration"
        )

    # Provided/legacy Public suites remain observational when they do not carry
    # the new common-result block.  If a block is present, validate it with the
    # same compiler-backed evidence checks used by generated suites; callers can
    # opt into the transformed-interface gate without changing identity cases.
    try:
        mapping = freeze_result_mapping(
            testbench_code,
            str(cv.get("orig_code") or cv.get("curr_code") or ""),
            kernel_name,
            hls_name,
            require_explicit=require_explicit,
            **parse_context,
        )
    except ResultMappingVerificationError as exc:
        cv["public_result_mapping_unverified"] = exc.partial_mapping
        cv["public_result_mapping_diagnostics"] = exc.diagnostics
        raise tools.tb_optimizer.TestbenchGenerationExhausted(
            split="public", stage="external_public_mapping_verification",
            trajectories=[{
                "trajectory_idx": 0, "qualification_checks_used": 1,
                "repairs_used": 0, "error": str(exc),
                "rounds": [{"status": "contract_incomplete", "error": str(exc),
                            "failure_owner": "unknown", "next_action": "review_unknown"}],
            }],
            qualification_mode="provided",
        ) from exc
    except ValueError as exc:
        raise tools.tb_optimizer.ModelArtifactError(str(exc)) from exc
    if mapping is not None:
        cv["public_result_mapping"] = mapping

    original_result = tools.tb_coverage.check_original_execution(
        isolate_reference_program_entry(
            str(cv["curr_code"]),
            top_function=kernel_name,
            testbench_code=testbench_code,
            source_path=parse_context["source_path"],
            include_dirs=parse_context["include_dirs"],
            compile_flags=compile_flags,
        ),
        testbench_code,
        candidate_decl,
        hls_name,
        budget=budget,
        keep_dir=str(original_dir) if original_dir is not None else None,
        original_name=kernel_name,
        **execution_context,
    )
    original_receipt_path = None
    if attempt_dir is not None:
        original_receipt_path = attempt_dir / "original_qualification_receipt.json"
        original_receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "qualification_mode": "provided",
                    "split": "public",
                    "stage": "external_public_original_qualification",
                    "result": original_result,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
                default=str,
            ),
            encoding="utf-8",
        )
    cv["provided_public_qualification"] = {
        "qualification_mode": "provided",
        "split": "public",
        "artifact_dir": str(attempt_dir) if attempt_dir is not None else None,
        "original_receipt": (
            str(original_receipt_path)
            if original_receipt_path is not None
            else None
        ),
        "status": original_result.get("status"),
        "failure_owner": original_result.get("failure_owner", "unknown"),
        "diagnostic_kind": original_result.get(
            "diagnostic_kind",
            original_result.get("status", "unknown"),
        ),
        "repair_attempt_count": 0,
        "retry_exhausted": False,
        "formal_validation_started": False,
    }
    if original_result.get("status") != "ok":
        raise tools.tb_optimizer.TestbenchGenerationExhausted(
            split="public",
            stage="external_public_original_qualification",
            trajectories=[
                {
                    "trajectory_idx": 0,
                    "rounds": [original_result],
                }
            ],
            qualification_mode="provided",
        )

    loader = HLSAgentLoader(
        "flow/agents/testbench.yaml",
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent("tb_creator")
    probe_type_contract = tools.tb_optimizer.freeze_public_type_contract(
        testbench_code, hls_name, **parse_context,
    )
    stub = tools.tb_optimizer._request_cpp_artifact(
        agent,
        tools.tb_optimizer._empty_stub_request_message(
            hls_name,
            candidate_decl,
        ) + tools.tb_optimizer.frozen_type_instruction(probe_type_contract),
        first_turn=False,
        artifact_kind="empty_stub",
        required_symbol=hls_name,
        max_repairs=0,
    )
    try:
        tools.tb_optimizer.validate_stub_contract(
            stub,
            original_name=kernel_name,
            candidate_name=hls_name,
            frozen_hls_decl=candidate_decl,
            frozen_type_contract=probe_type_contract,
            **parse_context,
        )
    except tools.tb_optimizer.ModelArtifactError as exc:
        failure_owner = "unknown" if getattr(exc, "repair_eligible", True) is False else "stub"
        if getattr(exc, "type_contract", None) is not None:
            cv["public_type_contract_unverified"] = exc.type_contract
        raise tools.tb_optimizer.TestbenchGenerationExhausted(
            split="public", stage="external_public_probe_qualification",
            trajectories=[{
                "trajectory_idx": 0, "qualification_checks_used": 1,
                "repairs_used": 0, "error": str(exc),
                "rounds": [{"status": "probe_contract_failed", "error": str(exc),
                            "failure_owner": failure_owner,
                            "next_action": "review_unknown" if failure_owner == "unknown" else "regenerate_stub"}],
            }], qualification_mode="provided",
        ) from exc
    if stub_dir is None:
        synth_context = tempfile.TemporaryDirectory(
            prefix="external_public_synth_check_"
        )
        work_dir = synth_context.name
    else:
        synth_context = None
        work_dir = str(stub_dir)
    try:
        synth_ok, synth_error = tools.tb_optimizer._synth_check(
            stub,
            hls_name,
            work_dir,
            budget=budget,
            source_context={
                **execution_context,
                "target_profile": cv.get("target_profile") or {},
            },
        )
    finally:
        if synth_context is not None:
            synth_context.cleanup()
    synth_invocation = Path(work_dir) / "csynth_invocation.json"
    cv["provided_public_qualification"].update(
        {
            "status": "ok" if synth_ok else "synth_failed",
            "stub_synthesis_artifact_dir": (
                str(stub_dir) if stub_dir is not None else None
            ),
            "stub_synthesis_invocation": (
                str(synth_invocation) if synth_invocation.exists() else None
            ),
            "synth_error_tail": str(synth_error or "")[-1500:],
        }
    )
    if attempt_dir is not None:
        (attempt_dir / "stub_synthesis_receipt.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "qualification_mode": "provided",
                    "split": "public",
                    "stage": "external_public_abi_synthesis_qualification",
                    "status": "ok" if synth_ok else "synth_failed",
                    "invocation_path": (
                        str(synth_invocation)
                        if synth_invocation.exists()
                        else None
                    ),
                    "error_tail": str(synth_error or "")[-1500:],
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
    if not synth_ok:
        raise tools.tb_optimizer.TestbenchGenerationExhausted(
            split="public",
            stage="external_public_abi_synthesis_qualification",
            trajectories=[
                {
                    "trajectory_idx": 0,
                    "synth_error": synth_error,
                    "rounds": [
                        {
                            "status": "synth_failed",
                            "failure_owner": "unknown",
                            "next_action": "review_unknown",
                            "synth_error": synth_error,
                        }
                    ],
                }
            ],
            qualification_mode="provided",
        )
    return testbench_code
