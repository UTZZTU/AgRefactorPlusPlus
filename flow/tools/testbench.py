from __future__ import annotations

from typing import Any, Dict, Optional

from autogen.agentchat.group import ContextVariables  # type: ignore

from flow.base_agent import HLSAgentLoader
import flow.tools as tools


def _build_testbench_request(
    original_code: str,
    kernel_name: str,
) -> str:
    hls_name = f"{kernel_name}_hls"
    return (
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
        "count or coverage. On mismatch, emit a useful stderr message and "
        "return non-zero; otherwise return zero.\n\n"
        "Reply with exactly one complete ```cpp ... ``` block and no "
        "commentary."
    )


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
    response = agent.run(
        message=_build_testbench_request(
            str(cv["curr_code"]),
            kernel_name,
        ),
        max_turns=1,
    )
    response.process()
    messages = getattr(response, "messages", None)
    if not isinstance(messages, list) or not messages:
        raise RuntimeError("testbench agent returned no messages")
    terminal = messages[-1]
    if not isinstance(terminal, dict):
        raise RuntimeError("testbench agent terminal message is invalid")
    raw = terminal.get("content")
    tb = tools.tb_optimizer._extract_one_cpp_block(
        raw,
        artifact_kind="testbench",
        required_symbol=hls_name,
    )
    tools.tb_optimizer.validate_testbench_top_contract(
        tb,
        kernel_name,
        hls_name,
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
