import json, concurrent.futures
from typing import Annotated, Optional, Dict, Any
from pydantic import BaseModel
from autogen.agentchat.group import ContextVariables  # type: ignore
from flow.rag.rag_agent_loader import create_rag_agent_loader
import flow.tools as tools

IDENTIFIER_LIST = [
    "system_identifier",
    "recursion_identifier",
    "heap_based_identifier",
    "stack_based_identifier",
    "pointer_identifier",
    "others_identifier"
]

class IdentifierResult(BaseModel):
    identified_items: Annotated[list[str], "empty list ([]) if no items identified, otherwise each item should be like 'item_identified: a less than 10 words description'"]

class DeduplicatorResult(BaseModel):
    index_to_remove: Annotated[list[int], "empty list ([]) if there are no duplicated items, otherwise the index number of duplicated items"]
    other_necessary_items: Annotated[list[str], "empty list ([]) if there are no other necessary items, otherwise the other necessary items"]

class FilterResult(BaseModel):
    index_to_remove: Annotated[list[int], "empty list ([]) if there are no items can be handled by heterorefactor; otherwise indices of items auto-handled by HeteroRefactor; remove them"]

def identify_non_synthesizable_items(
    cv: ContextVariables,
    knowledge_db_path: str,
    embedding_model: str,
    enable_rag: bool,
    heterorefactor_enabled: bool,
    reset_knowledge_db: bool,
    executor: Optional[concurrent.futures.Executor] = None,
    debug: int = 0,
    llm_config: Optional[Dict[str, Any]] = None
) -> concurrent.futures.Future[list[str]]:
    identifying_loader = create_rag_agent_loader(
        config_path="flow/agents/identifying.yaml",
        knowledge_db_path=knowledge_db_path,
        embedding_model=embedding_model,
        enable_rag=enable_rag,
        reset_db=reset_knowledge_db,
        debug=debug,
        llm_config_override=llm_config
    )
    if enable_rag:
        identifying_agents = [identifying_loader.load_agent_with_rag(agent, cv) for agent in IDENTIFIER_LIST]
    else:
        identifying_agents = [identifying_loader.load_agent(agent) for agent in IDENTIFIER_LIST]
    def run_agent(agent):
        msg = cv["curr_code"] + f"\n\nThe device top-level kernel names are: {cv['kernel_name']}."
        response = agent.run(message=msg, max_turns=1)
        response.process()
        return response
    
    def run_identification_process():
        items = []
        if executor is None:
            with concurrent.futures.ThreadPoolExecutor() as local_executor:
                future_to_agent = {local_executor.submit(run_agent, agent): agent for agent in identifying_agents}
                for future in concurrent.futures.as_completed(future_to_agent):
                    response = future.result()
                    items.extend(json.loads(tools.general.strip_thinking(response.messages[1]["content"]))["identified_items"])
        else:
            future_to_agent = {executor.submit(run_agent, agent): agent for agent in identifying_agents}
            for future in concurrent.futures.as_completed(future_to_agent):
                response = future.result()
                items.extend(json.loads(tools.general.strip_thinking(response.messages[1]["content"]))["identified_items"])
        
        deduplicator = identifying_loader.load_agent("deduplicator")
        item_list = "\n".join([f"\t{idx}. {item}" for idx, item in enumerate(items)])
        response = deduplicator.run(
            message=cv["curr_code"] + f"\n\nThe identified non-synthesizable item list:\n{item_list}",
            max_turns=1,
        )
        response.process()
        content = json.loads(tools.general.strip_thinking(response.messages[1]["content"]))
        items = [item for idx, item in enumerate(items) if idx not in content["index_to_remove"]] + content["other_necessary_items"]

        items_hetero: list[str] = []
        if heterorefactor_enabled:
            if enable_rag:
                hr_filter_agent = identifying_loader.load_agent_with_rag("heterorefactor_filter", cv)
            else:
                hr_filter_agent = identifying_loader.load_agent("heterorefactor_filter")

                item_list = "\n".join([f"\t{idx}. {item}" for idx, item in enumerate(items)])
                msg = (
                    cv["curr_code"]
                    + f"\n\nThe device top-level kernel names are: {cv['kernel_name']}."
                    + f"\n\nHere is the deduplicated list of identified non-synthesizable constructs:\n{item_list}"
                )
                response = hr_filter_agent.run(message=msg, max_turns=1)
                response.process()
                filter_content = json.loads(tools.general.strip_thinking(response.messages[1]["content"]))
                idx_to_remove = set(filter_content.get("index_to_remove", []))
                items_hetero = [item for idx, item in enumerate(items) if idx not in idx_to_remove]
        return items, items_hetero
    
    if executor is None:
        with concurrent.futures.ThreadPoolExecutor() as local_executor:
            return local_executor.submit(run_identification_process)
    else:
        return executor.submit(run_identification_process)