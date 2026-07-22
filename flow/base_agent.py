import logging, copy, yaml, importlib
from pathlib import Path
from typing import Dict, Any, Union, List
from autogen import LLMConfig, UserProxyAgent, ConversableAgent, UpdateSystemMessage # type: ignore
from autogen.agentchat import initiate_group_chat # type: ignore
from autogen.agentchat.group import ContextVariables # type: ignore
from autogen.agentchat.group.patterns import AutoPattern # type: ignore

def yaml_load_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def import_from_string(import_string: str) -> Any:
    module_path, name = import_string.rsplit('.', 1)
    module = importlib.import_module(module_path)
    return getattr(module, name)


AGREFPP_USAGE_AGENTS: List[Any] = []


def reset_agrefactorpp_usage_registry() -> None:
    # Reset the per-run agent registry used for token/cost accounting.
    AGREFPP_USAGE_AGENTS.clear()


def register_agrefactorpp_usage_agent(agent: Any) -> None:
    # Register a newly created agent for usage accounting.
    if not any(id(agent) == id(existing) for existing in AGREFPP_USAGE_AGENTS):
        AGREFPP_USAGE_AGENTS.append(agent)


def _agrefactorpp_price_per_1k(model_name: str) -> tuple[float, float] | None:
    # Return default USD price per 1K tokens for known OpenAI-compatible models.
    model_l = str(model_name or "").lower()
    if "deepseek-v4-pro" in model_l:
        return (0.000435, 0.00087)
    if "deepseek-v4-flash" in model_l or model_l in {"deepseek-chat", "deepseek-reasoner"}:
        return (0.00014, 0.00028)
    return None


def _agrefactorpp_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def get_agrefactorpp_usage_summary() -> Dict[str, Any]:
    # Collect token/cost usage for all agents created in the current run.
    agents = [agent for agent in AGREFPP_USAGE_AGENTS if agent is not None]
    summary: Dict[str, Any] = {
        "agents": len(agents),
        "models": {},
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "source": "none",
    }

    if not agents:
        return summary

    try:
        from autogen import gather_usage_summary  # type: ignore

        usage = gather_usage_summary(agents)
        usage_data = (
            usage.get("usage_including_cached_inference")
            or usage.get("usage_excluding_cached_inference")
            or {}
        )
        summary["source"] = "autogen.gather_usage_summary"

        total_cost_from_ag2 = usage_data.get("total_cost")
        if total_cost_from_ag2 is not None:
            summary["total_cost"] = _agrefactorpp_number(total_cost_from_ag2)

        for model_name, data in usage_data.items():
            if model_name == "total_cost" or not isinstance(data, dict):
                continue

            prompt_tokens = int(_agrefactorpp_number(data.get("prompt_tokens"), 0))
            completion_tokens = int(_agrefactorpp_number(data.get("completion_tokens"), 0))
            total_tokens = int(_agrefactorpp_number(data.get("total_tokens"), prompt_tokens + completion_tokens))

            cost = data.get("cost", data.get("total_cost"))
            if cost is None:
                price = _agrefactorpp_price_per_1k(str(model_name))
                if price is not None:
                    cost = (prompt_tokens / 1000.0) * price[0] + (completion_tokens / 1000.0) * price[1]
            cost_f = _agrefactorpp_number(cost, 0.0)

            summary["models"][str(model_name)] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost": cost_f,
            }
            summary["prompt_tokens"] += prompt_tokens
            summary["completion_tokens"] += completion_tokens
            summary["total_tokens"] += total_tokens

            if total_cost_from_ag2 is None:
                summary["total_cost"] += cost_f

        return summary
    except Exception as exc:
        summary["source"] = f"fallback_per_agent: {type(exc).__name__}: {exc}"

    for agent in agents:
        for method_name in ("get_actual_usage", "get_total_usage"):
            method = getattr(agent, method_name, None)
            if not callable(method):
                continue
            try:
                usage = method()
            except Exception:
                usage = None
            if not isinstance(usage, dict):
                continue

            for model_name, data in usage.items():
                if model_name == "total_cost" or not isinstance(data, dict):
                    continue
                prompt_tokens = int(_agrefactorpp_number(data.get("prompt_tokens"), 0))
                completion_tokens = int(_agrefactorpp_number(data.get("completion_tokens"), 0))
                total_tokens = int(_agrefactorpp_number(data.get("total_tokens"), prompt_tokens + completion_tokens))
                cost = data.get("cost", data.get("total_cost"))
                if cost is None:
                    price = _agrefactorpp_price_per_1k(str(model_name))
                    if price is not None:
                        cost = (prompt_tokens / 1000.0) * price[0] + (completion_tokens / 1000.0) * price[1]
                cost_f = _agrefactorpp_number(cost, 0.0)

                model_key = str(model_name)
                bucket = summary["models"].setdefault(
                    model_key,
                    {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0},
                )
                bucket["prompt_tokens"] += prompt_tokens
                bucket["completion_tokens"] += completion_tokens
                bucket["total_tokens"] += total_tokens
                bucket["cost"] += cost_f
                summary["prompt_tokens"] += prompt_tokens
                summary["completion_tokens"] += completion_tokens
                summary["total_tokens"] += total_tokens
                summary["total_cost"] += cost_f
            break

    return summary


def print_agrefactorpp_usage_summary() -> None:
    # Print a human-readable usage summary to the current run log.
    summary = get_agrefactorpp_usage_summary()

    print("=============== Token / Cost Summary ===============")
    print(f"Usage source: {summary.get('source')}")
    print(f"Registered agents: {summary.get('agents', 0)}")

    models = summary.get("models", {})
    if not models:
        print("No token usage was reported by the current AG2 client.")
        print("====================================================")
        return

    print(f"Prompt tokens: {int(summary.get('prompt_tokens', 0)):,}")
    print(f"Completion tokens: {int(summary.get('completion_tokens', 0)):,}")
    print(f"Total tokens: {int(summary.get('total_tokens', 0)):,}")
    print(f"Estimated cost: ${float(summary.get('total_cost', 0.0)):.6f}")

    for model_name, info in models.items():
        print(f"--- {model_name} ---")
        print(f"  Prompt tokens: {int(info.get('prompt_tokens', 0)):,}")
        print(f"  Completion tokens: {int(info.get('completion_tokens', 0)):,}")
        print(f"  Total tokens: {int(info.get('total_tokens', 0)):,}")
        print(f"  Estimated cost: ${float(info.get('cost', 0.0)):.6f}")

    print("====================================================")


def is_termination_msg(x: dict[str, Any]) -> bool:
    content = x.get("content", "")
    mark_identified = (
        "==== EXIT ====" in content
    )
    return (content is not None) and mark_identified

class HLSUserProxyAgent(UserProxyAgent):
    """A custom proxy agent for the user with redefined default descriptions for HLS context."""
    DEFAULT_USER_PROXY_AGENT_DESCRIPTIONS = {
        "ALWAYS": "An attentive HUMAN user who can answer questions about the task and provide feedback.",
        "TERMINATE": "A user that can run Python code and report back the execution results.",
        "NEVER": "A computer terminal that performs no other action than running Python scripts (provided to it quoted in ```python code blocks).",
    }

class HLSAgentLoader:
    def __init__(
        self,
        config_path: Union[str, Path],
        llm_config_override: Union[Dict[str, Any], LLMConfig, None] = None,
    ):
        self.config_path = Path(config_path)
        self.config_data = yaml_load_file(self.config_path)
        self.agents: Dict[str, ConversableAgent] = {}
        self.agent_configs: Dict[str, Dict[str, Any]] = {}
        self._runtime_llm_config = llm_config_override
        self._global_llm_config = None
        self._context_variables = None
        self._process_global_config()
        
    @staticmethod
    def _overlay_llm_config(
        base: Any,
        overlay: Any,
    ) -> Any:
        'Overlay one generic AG2 LLM configuration layer.'

        if overlay is None:
            return copy.deepcopy(base)
        if base is None:
            return copy.deepcopy(overlay)

        if isinstance(overlay, LLMConfig):
            return copy.deepcopy(overlay)
        if isinstance(base, LLMConfig):
            return copy.deepcopy(overlay)

        if isinstance(base, dict) and isinstance(
            overlay,
            dict,
        ):
            merged = copy.deepcopy(base)
            merged.update(copy.deepcopy(overlay))
            return merged

        if isinstance(base, list) and isinstance(
            overlay,
            dict,
        ):
            merged_entries = []
            for entry in base:
                if not isinstance(entry, dict):
                    merged_entries.append(
                        copy.deepcopy(entry)
                    )
                    continue
                merged = copy.deepcopy(entry)
                merged.update(copy.deepcopy(overlay))
                merged_entries.append(merged)
            return merged_entries

        return copy.deepcopy(overlay)

    @classmethod
    def _merge_llm_config_layers(
        cls,
        *layers: Any,
    ) -> Any:
        merged = None
        for layer in layers:
            if layer is None:
                continue
            merged = cls._overlay_llm_config(
                merged,
                layer,
            )
        return merged

    @staticmethod
    def _resolve_llm_config_imports(
        value: Any,
    ) -> Any:
        if isinstance(value, dict):
            resolved = copy.deepcopy(value)
            response_format = resolved.get(
                'response_format'
            )
            if isinstance(response_format, str):
                resolved['response_format'] = (
                    import_from_string(
                        response_format
                    )
                )
            return resolved
        if isinstance(value, list):
            return [
                HLSAgentLoader._resolve_llm_config_imports(
                    entry
                )
                for entry in value
            ]
        return copy.deepcopy(value)

    def _process_global_config(self):
        global_llm_config = self.config_data.get(
            'llm_config'
        )
        self._global_llm_config = copy.deepcopy(
            global_llm_config
        )
        if 'context_variables' in self.config_data:
            cv_data = self.config_data[
                'context_variables'
            ]
            self._context_variables = (
                ContextVariables(**cv_data)
                if isinstance(cv_data, dict)
                else copy.deepcopy(cv_data)
            )

    def _resolve_imports(self, config: Dict[str, Any]) -> Dict[str, Any]:
        resolved_config = copy.deepcopy(config)

        if 'llm_config' in resolved_config:
            resolved_config['llm_config'] = (
                self._resolve_llm_config_imports(
                    resolved_config['llm_config']
                )
            )
        
        if 'functions' in resolved_config:
            functions = resolved_config['functions']
            if isinstance(functions, list):
                resolved_functions = []
                for func in functions:
                    if isinstance(func, str):
                        resolved_functions.append(import_from_string(func))
                    else:
                        resolved_functions.append(func)
                resolved_config['functions'] = resolved_functions
            elif isinstance(functions, str):
                resolved_config['functions'] = [import_from_string(functions)]
                
        if 'is_termination_msg' in resolved_config:
            term_msg = resolved_config['is_termination_msg']
            if isinstance(term_msg, str):
                resolved_config['is_termination_msg'] = import_from_string(term_msg)
            
        if 'update_agent_state_before_reply' in resolved_config:
            update_funcs = resolved_config['update_agent_state_before_reply']
            if isinstance(update_funcs, list):
                resolved_funcs = []
                for func in update_funcs:
                    if isinstance(func, str):
                        if func == 'UpdateSystemMessage':
                            template = resolved_config.get('system_message', '')
                            resolved_funcs.append(UpdateSystemMessage(template))
                        else:
                            resolved_funcs.append(import_from_string(func))
                    else:
                        resolved_funcs.append(func)
                resolved_config['update_agent_state_before_reply'] = resolved_funcs
            elif isinstance(update_funcs, str):
                if update_funcs == 'UpdateSystemMessage':
                    template = resolved_config.get('system_message', '')
                    resolved_config['update_agent_state_before_reply'] = [UpdateSystemMessage(template)]
                else:
                    resolved_config['update_agent_state_before_reply'] = [import_from_string(update_funcs)]
                
        if 'handoffs' in resolved_config:
            handoffs_data = resolved_config['handoffs']
            if isinstance(handoffs_data, str):
                resolved_config['handoffs'] = import_from_string(handoffs_data)
            elif isinstance(handoffs_data, dict):
                pass
                
        return resolved_config
        
    def _prepare_agent_config(
        self,
        agent_name: str,
        agent_config: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        config = copy.deepcopy(agent_config)

        if 'name' not in config:
            config['name'] = agent_name

        merged_llm_config = self._merge_llm_config_layers(
            config.get('llm_config'),
            self._global_llm_config,
            self._runtime_llm_config,
        )
        merged_llm_config = (
            self._resolve_llm_config_imports(
                merged_llm_config
            )
        )
        if merged_llm_config is not None:
            config['llm_config'] = merged_llm_config

        if (
            'llm_config' in config
            and config['llm_config'] is not None
            and not isinstance(
                config['llm_config'],
                LLMConfig,
            )
        ):
            llm_config_obj = copy.deepcopy(
                config['llm_config']
            )
            if isinstance(llm_config_obj, dict):
                config['llm_config'] = LLMConfig(
                    llm_config_obj
                )
            elif isinstance(llm_config_obj, list):
                config['llm_config'] = LLMConfig(
                    *llm_config_obj
                )
            else:
                config['llm_config'] = LLMConfig(
                    llm_config_obj
                )

        if (
            'context_variables' not in config
            and self._context_variables
        ):
            config['context_variables'] = (
                self._context_variables
            )
        elif (
            'context_variables' in config
            and isinstance(
                config['context_variables'],
                dict,
            )
        ):
            config['context_variables'] = (
                ContextVariables(
                    **config['context_variables']
                )
            )

        if (
            'system_message' not in config
            and config['name'] != 'human'
        ):
            raise ValueError(
                "system_message is required"
            )

        if 'system_message' in config:
            if isinstance(
                config['system_message'],
                dict,
            ):
                config['system_message'] = (
                    config['system_message'][
                        'sys_start'
                    ]
                    + config['system_message'][
                        'sys_middle'
                    ]
                    + config['system_message'][
                        'sys_end'
                    ]
                )

        config.setdefault(
            'human_input_mode',
            'TERMINATE',
        )
        config.setdefault(
            'code_execution_config',
            False,
        )
        config.setdefault(
            'default_auto_reply',
            "",
        )

        agent_params = {
            'name',
            'system_message',
            'is_termination_msg',
            'max_consecutive_auto_reply',
            'human_input_mode',
            'function_map',
            'code_execution_config',
            'llm_config',
            'default_auto_reply',
            'description',
            'chat_messages',
            'silent',
            'context_variables',
            'functions',
            'update_agent_state_before_reply',
            'handoffs',
        }
        filtered_config = {
            key: value
            for key, value in config.items()
            if key in agent_params
        }
        extra_config = {
            key: value
            for key, value in config.items()
            if key not in agent_params
        }
        filtered_config = self._resolve_imports(
            filtered_config
        )

        return filtered_config, extra_config

    def load_agent(self, agent_name: str, **overrides) -> ConversableAgent:
        if 'agents' not in self.config_data:
            raise ValueError("No 'agents' section found in configuration")
            
        if agent_name not in self.config_data['agents']:
            available_agents = list(self.config_data['agents'].keys())
            raise ValueError(f"Agent '{agent_name}' not found. Available agents: {available_agents}")
            
        agent_config = self.config_data['agents'][agent_name]
        
        config, extra_config = self._prepare_agent_config(agent_name, agent_config)
        
        config.update(overrides)
        
        agent = ConversableAgent(**config)
        register_agrefactorpp_usage_agent(agent)
        
        self.agents[agent_name] = agent
        self.agent_configs[agent_name] = {
            'config': config,
            'extra_config': extra_config
        }
        
        logger = logging.getLogger(agent_name)
        logger.info(f"Created agent '{agent_name}' with configuration:")
        for key, value in config.items():
            if key not in ['llm_config', 'functions', 'function_map']:
                logger.info(f"  {key}: {value}")
                
        return agent
        
    def load_all_agents(self, **global_overrides) -> Dict[str, ConversableAgent]:
        if 'agents' not in self.config_data:
            raise ValueError("No 'agents' section found in configuration")
            
        agents = {}
        for agent_name in self.config_data['agents'].keys():
            agents[agent_name] = self.load_agent(agent_name, **global_overrides)
            
        return agents
        
    def get_agent(self, agent_name: str) -> ConversableAgent:
        if agent_name not in self.agents:
            raise ValueError(f"Agent '{agent_name}' not loaded. Call load_agent() first.")
        return self.agents[agent_name]
        
    def list_available_agents(self) -> List[str]:
        if 'agents' not in self.config_data:
            return []
        return list(self.config_data['agents'].keys())
        
    def get_agent_config(self, agent_name: str) -> Dict[str, Any]:
        if agent_name not in self.agent_configs:
            raise ValueError(f"Agent '{agent_name}' not loaded.")
        return self.agent_configs[agent_name]
        
    def print_all_agents_model_selections(self):
        if 'agents' not in self.config_data:
            print("No agents found in configuration.")
            return
        agents = self.config_data['agents']
        print("Agent model selections:")
        for agent_name, agent_cfg in agents.items():
            model = None
            if isinstance(agent_cfg, dict):
                if 'llm_config' in agent_cfg and isinstance(agent_cfg['llm_config'], dict):
                    model = agent_cfg['llm_config'].get('model', None)
                elif 'model' in agent_cfg:
                    model = agent_cfg.get('model', None)
            print(f"  - {agent_name}: {model if model else '[model not specified]'}")

    def initiate_chat_pattern(
        self,
        agents: list[ConversableAgent],
        context_variables: ContextVariables,
        group_manager_args: dict[str, Any],
    ) -> AutoPattern:
        return AutoPattern(
            initial_agent=agents[0],
            agents=agents,
            context_variables=context_variables,
            group_manager_args=group_manager_args
        )
    
    def initiate_group_chat(
        self,
        agents: list[ConversableAgent],
        context_variables: ContextVariables,
        group_manager_args: dict[str, Any] = None,
        messages: str = "Your turn!",
        max_rounds: int = 20,
    ) -> tuple[list[dict[str, Any]], ContextVariables, list[dict[str, Any]]]:
        default_group_manager_args = {
            "llm_config": self._global_llm_config,
            "is_termination_msg": is_termination_msg
        }
        if group_manager_args is not None:
            default_group_manager_args.update(group_manager_args)

        pattern = self.initiate_chat_pattern(agents, context_variables, default_group_manager_args)
        return initiate_group_chat(pattern, messages, max_rounds)
