from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from autogen.agentchat.group import ContextVariables  # type: ignore

from flow.base_agent import HLSAgentLoader
from agrefactor.reference_source import isolate_reference_program_entry
import flow.tools as tools


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
    contract_failures: list[str] = []
    for repair_index in range(repair_limit + 1):
        try:
            tools.tb_optimizer.validate_testbench_top_contract(tb, kernel_name, hls_name)
            tools.tb_optimizer.validate_testbench_input_domain(
                tb, hls_name,
                tools.tb_optimizer.extract_hls_decl_from_testbench(tb, hls_name, **parse_context),
                cv.get("input_domain_contract"), require_maximum=True,
            )
            break
        except tools.tb_optimizer.ModelArtifactError as exc:
            contract_failures.append(f"Validation {repair_index}: {exc}")
            cv["public_contract_failures"] = list(contract_failures)
            if repair_index == repair_limit:
                raise
            tb = tools.tb_optimizer._request_cpp_artifact(
                agent,
                "Public Testbench contract failures:\n" + "\n".join(contract_failures)
                + "\nReturn a complete replacement.\n\n"
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
                prior_failures=tuple(qualification_failures),
                source_package_context=cv.get("source_package_context"),
            ),
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

    # Validate the public Candidate ABI with the same Vitis probe used by the
    # held-out path before freezing it for downstream stages. This keeps an
    # unsynthesizable public declaration from becoming an unrecoverable
    # hidden-stage contract.
    if cv.get("target_profile"):
        synth_failures: list[str] = []
        for synth_index in range(repair_limit + 1):
            candidate_decl = tools.tb_optimizer.extract_hls_decl_from_testbench(
                tb, hls_name, **parse_context,
            )
            stub = tools.tb_optimizer._request_cpp_artifact(
                agent,
                tools.tb_optimizer._empty_stub_request_message(
                    hls_name,
                    candidate_decl,
                    failure_history="\n\n".join(synth_failures),
                ),
                first_turn=False,
                artifact_kind="empty_stub",
                required_symbol=hls_name,
                max_repairs=repair_limit,
            )
            tools.tb_optimizer.validate_stub_contract(
                stub,
                original_name=kernel_name,
                candidate_name=hls_name,
                frozen_hls_decl=candidate_decl,
            )
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
            if synth_ok:
                break
            synth_failures.append(synth_error)
            if synth_index >= repair_limit:
                raise tools.tb_optimizer.TestbenchGenerationExhausted(
                    split="public",
                    stage="public_abi_synthesis_qualification",
                    trajectories=[{
                        "trajectory_idx": 0,
                        "synth_error": "\n\n".join(synth_failures),
                        "rounds": [{
                            "status": "synth_failed",
                            "failure_owner": "unknown",
                            "next_action": "review_unknown",
                            "synth_error": "\n\n".join(synth_failures),
                        }],
                    }],
                )
            tb = tools.tb_optimizer._request_cpp_artifact(
                agent,
                tools.tb_optimizer._hls_friendly_rewrite_message(
                    hls_name,
                    "\n\n".join(synth_failures),
                ),
                first_turn=False,
                artifact_kind="testbench",
                required_symbol=hls_name,
                max_repairs=repair_limit,
            )
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
        original_name=kernel_name,
        **execution_context,
    )
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
        )

    loader = HLSAgentLoader(
        "flow/agents/testbench.yaml",
        llm_config_override=llm_config,
        budget=budget,
    )
    agent = loader.load_agent("tb_creator")
    stub = tools.tb_optimizer._request_cpp_artifact(
        agent,
        tools.tb_optimizer._empty_stub_request_message(
            hls_name,
            candidate_decl,
        ),
        first_turn=False,
        artifact_kind="empty_stub",
        required_symbol=hls_name,
        max_repairs=0,
    )
    tools.tb_optimizer.validate_stub_contract(
        stub,
        original_name=kernel_name,
        candidate_name=hls_name,
        frozen_hls_decl=candidate_decl,
    )
    with tempfile.TemporaryDirectory(prefix="external_public_synth_check_") as work_dir:
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
        )
    return testbench_code
