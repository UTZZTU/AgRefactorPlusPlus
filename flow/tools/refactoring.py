from flow.base_agent import HLSAgentLoader
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
import concurrent.futures
from typing import Optional, Dict, Any

def refactor_code(
    cv: ContextVariables,
    hetero_enabled: bool,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Any = None,
) -> tuple[str, str]:
    refactoring_loader = HLSAgentLoader(
        "flow/agents/refactoring.yaml",
        llm_config_override=llm_config,
        budget=budget,
    )
    refactoring_worker = refactoring_loader.load_agent("refactoring_worker")
    if hetero_enabled:
        refactoring_heterorefactor_worker = refactoring_loader.load_agent("refactoring_heterorefactor_worker")

    msg = (
        f"Here is the code to be refactored:\n{cv['curr_code']}\n\n"
        f"Here is the plan you can refer to:\n{cv['plan']}\n\n"
        f"Here is the instructions you must follow, they specify the signature and constraints of the new kernel:\n{cv['tb_aligned_instruction']}\n\n"
        f"{cv['new_kernel_name']} is the new kernel name you must use to replace the original kernel {cv['kernel_name']}\n\n"
    )
    if hetero_enabled:
        msg_heterorefactor = (
            f"Here is the code to be refactored:\n{cv['curr_code']}\n\n"
            f"Here is the plan you can refer to:\n{cv['plan_hetero']}\n\n"
            f"Here is the instructions you must follow, they specify the signature and constraints of the new kernel:\n{cv['tb_aligned_instruction']}\n\n"
            f"{cv['new_kernel_name']} is the new kernel name you must use to replace the original kernel {cv['kernel_name']}\n\n"
        )

    def run_worker(worker, message):
        resp = worker.run(message=message, max_turns=1)
        resp.process()
        return tools.general.extract_code(tools.general.strip_thinking(resp.messages[1]["content"]))[0]

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_standard = executor.submit(run_worker, refactoring_worker, msg)
        if hetero_enabled:
            future_hetero = executor.submit(run_worker, refactoring_heterorefactor_worker, msg_heterorefactor)
        standard_code = future_standard.result()
        if hetero_enabled:
            hetero_code = future_hetero.result()

    return standard_code, (hetero_code if hetero_enabled else "")