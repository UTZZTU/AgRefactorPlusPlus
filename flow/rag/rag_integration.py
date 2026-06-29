from typing import Dict, List, Any, Optional
from autogen.agentchat.group import ContextVariables  # type: ignore
import json
from flow.base_agent import HLSAgentLoader
from flow.rag.knowledge_db import KnowledgeDB
from typing import Annotated
from pydantic import BaseModel

class AnalyzerResult(BaseModel):
    non_synthesizable_items: Annotated[list[str], "a list of mistakes in refactoring, each item as a string less than 12 words"]

class RAGIntegration:
    def __init__(
        self,
        knowledge_db: KnowledgeDB,
        debug: int = 0,
        llm_config: Optional[Dict[str, Any]] = None
    ):
        self.knowledge_db = knowledge_db
        self.debug = debug
        self.llm_config = llm_config
    
    def enhance_identification_prompt(
        self,
        original_system_message: str,
        code: str,
        agent_type: str = "general"
    ) -> str:
        common_missing_items = self.knowledge_db.get_common_missing_items(
            code=code,
            n_results=5,
            threshold=1.5
        )
        if not common_missing_items:
            if self.debug >= 1:
                print(f"No common missing items found for {agent_type} identifier")
            return original_system_message
        
        # relevant_missing_items = self._filter_missing_items_by_agent_type(
        #     common_missing_items, agent_type
        # )
        relevant_missing_items = common_missing_items
        
        if not relevant_missing_items:
            return original_system_message
        
        enhancement = self._create_identification_enhancement(relevant_missing_items)
        enhanced_message = original_system_message + "\n\n" + enhancement
        
        if self.debug >= 1:
            print(f"Enhanced {agent_type} identifier with {len(relevant_missing_items)} historical insights")
            print(f"Current system message:\n {enhanced_message}")
        
        return enhanced_message
    
    def _filter_missing_items_by_agent_type(
        self,
        missing_items: List[str],
        agent_type: str
    ) -> List[str]:
        agent_keywords = {
            "recursion": ["recursive", "recursion", "call", "function call"],
            "heap_based": ["malloc", "free", "new", "delete", "vector", "string", "map", "heap", "dynamic"],
            "stack_based": ["alloca", "variable length", "flexible array", "stack", "runtime size"],
            "pointer": ["pointer", "double pointer", "multi-access", "pointer arithmetic", "casting"],
            "others": ["long double", "complex", "lambda", "bitfield", "pragma", "constructor", "destructor"]
        }
        if agent_type not in agent_keywords:
            return missing_items
        
        keywords = agent_keywords[agent_type]
        relevant_items = []
        for item in missing_items:
            item_lower = item.lower()
            if any(keyword in item_lower for keyword in keywords):
                relevant_items.append(item)
        
        return relevant_items
    
    def _create_identification_enhancement(self, missing_items: List[str]) -> str:
        enhancement = "## Historical Knowledge - Common Missing Items\n"
        enhancement += "Based on analysis of similar code in the past, pay special attention to these commonly missed non-synthesizable constructs (they may or may not belong to your category, ignore them if not):\n\n"
        for i, item in enumerate(missing_items, 1):
            enhancement += f"{i}. {item}\n"
        enhancement += "\nMake sure to carefully check for these patterns in the current code."
        return enhancement
    
    def enhance_planning_with_similar_plans(
        self,
        original_system_message: str,
        code: str,
        identified_items: List[str],
        n_similar_plans: int = 1,
        plan_type: str = "main"
    ) -> str:
        relevant_plans = self.knowledge_db.retrieve_relevant_plans(
            code=code,
            identified_items=identified_items,
            n_results=n_similar_plans,
            code_weight=0.6,
            items_weight=0.4,
            threshold=1.5,
            plan_type=plan_type
        )
        if not relevant_plans:
            if self.debug >= 1:
                print("No relevant similar plans found for planning enhancement")
            return original_system_message
        
        enhancement = self._create_planning_enhancement(relevant_plans)
        enhanced_message = original_system_message + "\n\n" + enhancement
        
        if self.debug >= 1:
            print(f"Enhanced planner with {len(relevant_plans)} similar successful plans")
            print(f"Current system message:\n {enhanced_message}")
        return enhanced_message
    
    def enhance_planning_with_similar_plans_heterorefactor(
        self,
        original_system_message: str,
        code: str,
        identified_items: List[str],
        n_similar_plans: int = 3
    ) -> str:
        relevant_plans = self.knowledge_db.retrieve_relevant_plans(
            code=code,
            identified_items=identified_items,
            n_results=n_similar_plans,
            code_weight=0.6,
            items_weight=0.4,
            threshold=1.5,
            plan_type="hetero"
        )
        if not relevant_plans:
            if self.debug >= 1:
                print("No relevant similar heterorefactor plans found for planning enhancement")
            return original_system_message
        enhancement = self._create_planning_enhancement_heterorefactor(relevant_plans)
        enhanced_message = original_system_message + "\n\n" + enhancement
        if self.debug >= 1:
            print(f"Enhanced planner_heterorefactor with {len(relevant_plans)} similar successful plans")
        return enhanced_message

    def _create_planning_enhancement(
        self,
        relevant_plans: List[tuple]
    ) -> str:
        enhancement = "## Historical Knowledge - Similar Successful Plans\n"
        enhancement += "Here are successful refactoring plans from similar code patterns. Use these as guidance:\n\n"
        for i, (trial, distance) in enumerate(relevant_plans, 1):
            enhancement += f"### Similar Plan {i} (similarity: {1.0 - distance:.2f})\n"
            enhancement += f"**Identified Items:** {', '.join(trial.identified_items)}\n"
            enhancement += f"**Plan:**\n {trial.plan}\n"
            enhancement += "\n"
        enhancement += "Use these examples to inform your planning, but adapt the steps to the specific constructs and context of the current code."
        return enhancement

    def enhance_heterorefactor_filter_prompt(
        self,
        original_system_message: str,
        code: str,
        identified_items: List[str],
        n_similar_plans: int = 3
    ) -> str:
        relevant_plans = self.knowledge_db.retrieve_relevant_plans(
            code=code,
            identified_items=identified_items,
            n_results=n_similar_plans,
            code_weight=0.6,
            items_weight=0.4,
            threshold=1.5,
            plan_type="hetero"
        )

        likely_hr_supported = []
        if relevant_plans:
            for trial, _ in relevant_plans:
                for itm in trial.identified_items:
                    if itm not in trial.plan_hetero:
                        likely_hr_supported.append(itm)

        enhancement = "## Historical Knowledge - HeteroRefactor Capabilities and Blockers\n"
        if likely_hr_supported:
            enhancement += "Items frequently auto-handled by HeteroRefactor in similar successes:\n"
            for i, itm in enumerate(likely_hr_supported, 1):
                enhancement += f"{i}. {itm}\n"
            enhancement += "\n"

        if self.debug >= 1:
            print(
                f"Enhanced heterorefactor_filter with {len(likely_hr_supported)} likely-supported constructs examples."
            )

        return original_system_message + ("\n\n" + enhancement if enhancement else "")
    
    def _create_planning_enhancement_heterorefactor(
        self,
        relevant_plans: List[tuple]
    ) -> str:
        enhancement = "## Historical Knowledge - Similar Successful Heterorefactor Plans\n"
        enhancement += "Here are successful heterorefactor plans from similar code patterns. Use these as guidance:\n\n"
        for i, (trial, distance) in enumerate(relevant_plans, 1):
            enhancement += f"### Similar Heterorefactor Plan {i} (similarity: {1.0 - distance:.2f})\n"
            enhancement += f"**Identified Items:** {', '.join(trial.identified_items)}\n"
            plan_to_show = trial.plan_hetero if trial.plan_hetero else trial.plan
            enhancement += f"**Plan (heterorefactor):**\n {plan_to_show}\n\n"
        enhancement += "Use these examples to inform your planning, but adapt the steps to the specific constructs and context of the current code."
        return enhancement
    
    def store_trial_result(
        self,
        context_variables: ContextVariables,
        synthesis_success: bool,
        missing_items: Optional[List[str]] = None
    ) -> str:
        original_code = context_variables.get("orig_code", "")
        identified_items = context_variables.get("identified_items", [])
        
        if synthesis_success:
            plan = context_variables.get("plan", {})
            plan_hetero = context_variables.get("plan_hetero", {})
            plan_type = "hetero" if context_variables.get("csynth_csim_history", []) and context_variables["csynth_csim_history"][-1].get("status") == "succeeded by hetero" else "main"
            trial_id = self.knowledge_db.add_successful_trial(
                original_code=original_code,
                identified_items=identified_items,
                plan=plan,
                plan_hetero=plan_hetero,
                plan_type=plan_type
            )
            if self.debug >= 1:
                print(f"Stored successful trial: {trial_id}")
        else:
            synthesis_error = context_variables["csynth_csim_history"][-1].get("error_msg", "Unknown error")
            if missing_items is None:
                missing_items = []
            trial_id = self.knowledge_db.add_failed_trial(
                original_code=original_code,
                identified_items=identified_items,
                missing_items=missing_items,
                synthesis_error=synthesis_error
            )
            if self.debug >= 1:
                print(f"Stored failed trial: {trial_id}")
        return trial_id
    
    def analyze_synthesis_failure(
        self,
        context_variables: ContextVariables
    ) -> List[str]:
        analyzer_loader = HLSAgentLoader("flow/agents/fixing.yaml", llm_config_override=self.llm_config)
        analyzer = analyzer_loader.load_agent("csynth_error_analyzer")
        msg = context_variables["csynth_csim_history"][-1].get("error_msg", "Unknown error") + "\n\n" + context_variables["curr_code"] + f"\n\nThe device top-level kernel names are: {context_variables['new_kernel_name']}."
        response = analyzer.run(message=msg, max_turns=1)
        response.process()
        return json.loads(response.messages[1]["content"])["non_synthesizable_items"]

class KnowledgeManager:
    def __init__(
        self,
        db_path: str = "./tmp/hls_knowledge_db",
        embedding_model: str = "all-MiniLM-L6-v2",
        reset_db: bool = False,
        debug: int = 0,
        llm_config: Optional[Dict[str, Any]] = None
    ):
        self.knowledge_db = KnowledgeDB(
            db_path=db_path,
            embedding_model=embedding_model,
            reset_db=reset_db,
            debug=debug
        )
        self.rag_integration = RAGIntegration(
            knowledge_db=self.knowledge_db,
            debug=debug,
            llm_config=llm_config
        )
        self.debug = debug
    
    def enhance_identifier_agent(
        self,
        agent_config: Dict[str, Any],
        code: str,
        agent_name: str
    ) -> Dict[str, Any]:
        enhanced_config = agent_config.copy()
        agent_type = agent_name.replace("_identifier", "")
        original_message = agent_config.get("system_message", "")
        enhanced_message = self.rag_integration.enhance_identification_prompt(
            original_system_message=original_message,
            code=code,
            agent_type=agent_type
        )
        enhanced_message = enhanced_message.replace("{", "{{").replace("}", "}}")
        enhanced_config["system_message"] = enhanced_message
        return enhanced_config
    
    def enhance_planner_agent(
        self,
        agent_config: Dict[str, Any],
        code: str,
        identified_items: List[str]
    ) -> Dict[str, Any]:
        enhanced_config = agent_config.copy()
        original_message = agent_config.get("system_message", "")
        enhanced_message = self.rag_integration.enhance_planning_with_similar_plans(
            original_system_message=original_message,
            code=code,
            identified_items=identified_items,
            plan_type="main"
        )
        enhanced_message = enhanced_message.replace("{", "{{").replace("}", "}}")
        enhanced_config["system_message"] = enhanced_message
        return enhanced_config
    
    def enhance_planner_agent_heterorefactor(
        self,
        agent_config: Dict[str, Any],
        code: str,
        identified_items: List[str]
    ) -> Dict[str, Any]:
        enhanced_config = agent_config.copy()
        original_message = agent_config.get("system_message", "")
        enhanced_message = self.rag_integration.enhance_planning_with_similar_plans_heterorefactor(
            original_system_message=original_message,
            code=code,
            identified_items=identified_items
        )
        enhanced_message = enhanced_message.replace("{", "{{").replace("}", "}}")
        enhanced_config["system_message"] = enhanced_message
        return enhanced_config

    def enhance_heterorefactor_filter_agent(
        self,
        agent_config: Dict[str, Any],
        code: str,
        identified_items: List[str]
    ) -> Dict[str, Any]:
        enhanced_config = agent_config.copy()
        original_message = agent_config.get("system_message", "")
        enhanced_message = self.rag_integration.enhance_heterorefactor_filter_prompt(
            original_system_message=original_message,
            code=code,
            identified_items=identified_items
        )
        enhanced_message = enhanced_message.replace("{", "{{").replace("}", "}}")
        enhanced_config["system_message"] = enhanced_message
        return enhanced_config
    
    def record_trial_outcome(
        self,
        context_variables: ContextVariables,
        synthesis_status: str
    ) -> str:
        if synthesis_status == "succeeded" or synthesis_status == "succeeded by hetero":
            return self.rag_integration.store_trial_result(
                context_variables=context_variables,
                synthesis_success=True
            )
        else:
            if synthesis_status == "timeout" or synthesis_status == "csynth_failed":
                missing_items = self.rag_integration.analyze_synthesis_failure(
                    context_variables=context_variables
                )
            else:
                missing_items = None
            return self.rag_integration.store_trial_result(
                context_variables=context_variables,
                synthesis_success=False,
                missing_items=missing_items
            )
    
    def get_database_stats(self) -> Dict[str, Any]:
        return self.knowledge_db.get_stats()
