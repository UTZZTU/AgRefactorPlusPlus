import copy
from typing import Dict, Any, Union, Optional
from pathlib import Path
from autogen import ConversableAgent  # type: ignore
from autogen.agentchat.group import ContextVariables  # type: ignore
from flow.base_agent import HLSAgentLoader
from flow.rag.rag_integration import KnowledgeManager

class RAGAgentLoader(HLSAgentLoader):
    def __init__(
        self,
        config_path: Union[str, Path],
        knowledge_manager: Optional[KnowledgeManager] = None,
        enable_rag: bool = True,
        debug: int = 0,
        llm_config_override: Optional[Dict[str, Any]] = None,
        budget: Any = None,
    ):
        super().__init__(
            config_path,
            llm_config_override=llm_config_override,
            budget=budget,
        )
        
        self.enable_rag = enable_rag
        self.debug = debug
        if knowledge_manager is None and enable_rag:
            self.knowledge_manager = KnowledgeManager(debug=debug, llm_config=llm_config_override)
        else:
            self.knowledge_manager = knowledge_manager
        
        self.enhanced_agents: Dict[str, bool] = {}
    
    def load_agent_with_rag(
        self,
        agent_name: str,
        context_variables: Optional[ContextVariables] = None,
        **overrides
    ) -> ConversableAgent:
        if not self.enable_rag or self.knowledge_manager is None:
            return self.load_agent(agent_name, **overrides)
        
        if agent_name not in self.config_data['agents']:
            available_agents = list(self.config_data['agents'].keys())
            raise ValueError(f"Agent '{agent_name}' not found. Available agents: {available_agents}")
        
        base_config = copy.deepcopy(self.config_data['agents'][agent_name])
        
        enhanced_config = self._enhance_agent_config(
            agent_name=agent_name,
            base_config=base_config,
            context_variables=context_variables
        )
        
        original_config = self.config_data['agents'][agent_name]
        self.config_data['agents'][agent_name] = enhanced_config
        
        try:
            agent = self.load_agent(agent_name, **overrides)
            self.enhanced_agents[agent_name] = True
            if self.debug >= 1:
                print(f"Loaded agent '{agent_name}' with RAG enhancements")
            return agent
        finally:
            self.config_data['agents'][agent_name] = original_config
    
    def _enhance_agent_config(
        self,
        agent_name: str,
        base_config: Dict[str, Any],
        context_variables: Optional[ContextVariables]
    ) -> Dict[str, Any]:
        enhanced_config = copy.deepcopy(base_config)
        
        if context_variables is None:
            return enhanced_config
        
        code = context_variables.get("curr_code", "") or context_variables.get("orig_code", "")
        identified_items = context_variables.get("identified_items", [])
        
        if self._is_identifier_agent(agent_name):
            enhanced_config = self.knowledge_manager.enhance_identifier_agent(
                agent_config=enhanced_config,
                code=code,
                agent_name=agent_name
            )
        elif agent_name == "heterorefactor_filter":
            enhanced_config = self.knowledge_manager.enhance_heterorefactor_filter_agent(
                agent_config=enhanced_config,
                code=code,
                identified_items=identified_items
            )
        elif self._is_planner_agent(agent_name):
            if agent_name == "planner_heterorefactor":
                enhanced_config = self.knowledge_manager.enhance_planner_agent_heterorefactor(
                    agent_config=enhanced_config,
                    code=code,
                    identified_items=identified_items
                )
            else:
                enhanced_config = self.knowledge_manager.enhance_planner_agent(
                    agent_config=enhanced_config,
                    code=code,
                    identified_items=identified_items
                )
        
        return enhanced_config
    
    def _is_identifier_agent(self, agent_name: str) -> bool:
        identifier_agents = [
            "recursion_identifier",
            "heap_based_identifier", 
            "stack_based_identifier",
            "pointer_identifier",
            "others_identifier"
        ]
        return agent_name in identifier_agents
    
    def _is_planner_agent(self, agent_name: str) -> bool:
        planner_agents = ["planner", "planner_heterorefactor"]
        return agent_name in planner_agents
    
    def load_planner_with_rag(
        self,
        context_variables: ContextVariables,
        **overrides
    ) -> ConversableAgent:
        return self.load_agent_with_rag(
            agent_name="planner",
            context_variables=context_variables,
            **overrides
        )
    
    def load_planner_heterorefactor_with_rag(
        self,
        context_variables: ContextVariables,
        **overrides
    ) -> ConversableAgent:
        return self.load_agent_with_rag(
            agent_name="planner_heterorefactor",
            context_variables=context_variables,
            **overrides
        )
    
    def record_trial_outcome(
        self,
        context_variables: ContextVariables,
        synthesis_status: str
    ) -> Optional[str]:
        if not self.enable_rag or self.knowledge_manager is None:
            return None
        
        return self.knowledge_manager.record_trial_outcome(
            context_variables=context_variables,
            synthesis_status=synthesis_status
        )
    
    def get_knowledge_stats(self) -> Optional[Dict[str, Any]]:
        if not self.enable_rag or self.knowledge_manager is None:
            return None
        
        return self.knowledge_manager.get_database_stats()
    
    def reset_knowledge_database(self):
        if self.enable_rag and self.knowledge_manager is not None:
            self.knowledge_manager.knowledge_db._reset_database()
            if self.debug >= 1:
                print("Knowledge database reset")


def create_rag_agent_loader(
    config_path: Union[str, Path],
    knowledge_db_path: str = "./tmp/hls_knowledge_db",
    embedding_model: str = "all-MiniLM-L6-v2",
    enable_rag: bool = True,
    reset_db: bool = False,
    debug: int = 0,
    llm_config_override: Optional[Dict[str, Any]] = None,
    budget: Any = None,
) -> RAGAgentLoader:
    knowledge_manager = None
    if enable_rag:
        knowledge_manager = KnowledgeManager(
            db_path=knowledge_db_path,
            embedding_model=embedding_model,
            reset_db=reset_db,
            debug=debug,
            llm_config=llm_config_override
        )

    return RAGAgentLoader(
        config_path=config_path,
        knowledge_manager=knowledge_manager,
        enable_rag=enable_rag,
        debug=debug,
        llm_config_override=llm_config_override,
        budget=budget,
    )
