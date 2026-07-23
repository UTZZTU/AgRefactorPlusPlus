from flow.base_agent import HLSAgentLoader
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
from typing import Optional, Dict, Any

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
    fix_message = (
        "Original code:\n"
        f"```cpp\n{cv['curr_code']}\n```\n"
        "Top function name: "
        f"{cv['kernel_name']}\n"
    )
    resp = agent.run(message=fix_message, max_turns=1)
    resp.process()
    tb = tools.general.extract_code(tools.general.strip_thinking(resp.messages[1]["content"]))[0]
    comm_message = (
        "Given this testbench, create a **VERY SHORT** instruction for the future refactoring (at most 4 bullet points).\n"
        "You must include the new function signature and the MACROs that has to be passed to the refactoring flow.\n"
        "For others, include **ONLY** necessary constraints/information the new function needs to run properly with this testbench."
        "Do **NOT** speak anything about function equivalence or HLS synthesizability at this point."
    )
    resp = agent.run(message=comm_message, max_turns=1, clear_history=False)
    resp.process()
    inst = tools.general.strip_thinking(resp.messages[-1]["content"])
    return tb, inst, f"{cv['kernel_name']}_hls"