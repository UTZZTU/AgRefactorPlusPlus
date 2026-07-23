import concurrent.futures
from typing import Optional, Dict, Any
from autogen.agentchat.group import ContextVariables # type: ignore
from flow.rag.rag_agent_loader import create_rag_agent_loader
import flow.tools as tools

def generate_plan(
    cv: ContextVariables,
    knowledge_db_path: str,
    embedding_model: str,
    enable_rag: bool,
    hetero_enabled: bool,
    debug: int,
    llm_config: Optional[Dict[str, Any]] = None,
    budget: Any = None,
) -> tuple[str, str]:
    planning_loader = create_rag_agent_loader(
        config_path="flow/agents/planning.yaml",
        knowledge_db_path=knowledge_db_path,
        embedding_model=embedding_model,
        enable_rag=enable_rag,
        reset_db=False,
        debug=debug,
        llm_config_override=llm_config,
        budget=budget,
    )
    if hetero_enabled:
        planning_loader_heterorefactor = create_rag_agent_loader(
            config_path="flow/agents/planning.yaml",
            knowledge_db_path=knowledge_db_path,
            embedding_model=embedding_model,
            enable_rag=enable_rag,
            reset_db=False,
            debug=debug,
            llm_config_override=llm_config,
            budget=budget,
        )
    if enable_rag:
        planner = planning_loader.load_planner_with_rag(context_variables=cv)
        if hetero_enabled:
            planner_heterorefactor = planning_loader_heterorefactor.load_planner_heterorefactor_with_rag(context_variables=cv)
    else:
        planner = planning_loader.load_agent("planner")
    if hetero_enabled:
        planner_heterorefactor = planning_loader_heterorefactor.load_agent("planner_heterorefactor")
    msg = (
        f"Here is the code:\n{cv['curr_code']}\n\n"
        f"Here is the instruction aligned with the testbench:\n{cv['tb_aligned_instruction']}\n\n"
        f"Here is the list of identified non-synthesizable constructs:\n{cv['identified_items']}\n\n"
    )
    msg_hetero = (
        f"Here is the code:\n{cv['curr_code']}\n\n"
        f"Here is the instruction aligned with the testbench:\n{cv['tb_aligned_instruction']}\n\n"
        f"Here is the list of constructs requiring HeteroRefactor-specific refactoring only:\n{cv.get('items_hetero')}\n\n"
    )

    def run_agent(agent, msg):
        resp = agent.run(message=msg, max_turns=1)
        resp.process()
        return tools.general.strip_thinking(resp.messages[1]["content"])

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_plan = executor.submit(run_agent, planner, msg)
        future_plan_hetero = None
        if hetero_enabled:
            future_plan_hetero = executor.submit(run_agent, planner_heterorefactor, msg_hetero)
        plan = future_plan.result()
        plan_hetero = ""
        if hetero_enabled and future_plan_hetero is not None:
            plan_hetero = future_plan_hetero.result()

    return plan, (plan_hetero if hetero_enabled else "")
