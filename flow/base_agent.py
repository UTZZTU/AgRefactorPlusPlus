import logging, copy, yaml, importlib
from functools import wraps
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Any, Union, List
from autogen import LLMConfig, UserProxyAgent, ConversableAgent, UpdateSystemMessage # type: ignore
from autogen.agentchat import initiate_group_chat # type: ignore
from autogen.agentchat.group import ContextVariables # type: ignore
from autogen.agentchat.group.patterns import AutoPattern # type: ignore

from agrefactor.runtime.prompt_evidence import record_model_prompt_call
from agrefactor.models.call_policy import pop_internal_call_evidence

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


def _agrefactorpp_nonnegative_int(
    value: Any,
    default: int = 0,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        converted = int(
            default if value is None else value
        )
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, converted)


def _agrefactorpp_cost_decimal(
    value: Any,
) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        converted = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError):
        return None
    if not converted.is_finite() or converted < 0:
        return None
    return converted


def _agrefactorpp_decimal_text(
    value: Decimal,
) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _agrefactorpp_cost_observation(
    amount: Decimal | None,
    *,
    source: str,
    complete: bool,
) -> Dict[str, Any]:
    if amount is None:
        return {
            "kind": "unavailable",
            "amount": None,
            "currency": None,
            "quality": "unavailable",
            "source": source,
            "ledger_eligible": False,
            "complete": False,
            "assumptions": [
                (
                    "No framework cost amount was reported "
                    "with an explicit currency."
                )
            ],
        }

    return {
        "kind": "framework_reported",
        "amount": _agrefactorpp_decimal_text(
            amount
        ),
        "currency": None,
        "quality": (
            "reported_unverified_currency"
        ),
        "source": source,
        "ledger_eligible": False,
        "complete": bool(complete),
        "assumptions": [
            (
                "The AG2 framework did not provide an "
                "explicit currency. This amount is retained "
                "for audit only and is not entered into the "
                "native-currency Budget ledger."
            ),
            "Cost estimates are not invoices.",
        ],
    }


def _agrefactorpp_empty_usage_summary(
    agent_count: int,
) -> Dict[str, Any]:
    unavailable = _agrefactorpp_cost_observation(
        None,
        source="none",
        complete=False,
    )
    return {
        "agents": agent_count,
        "models": {},
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "source": "none",
        "framework_reported_cost": unavailable,
        "estimated_cost": None,
        "costs_by_currency": {},
        "cost_usd": None,
        "total_cost": None,
        "cost_complete": False,
    }


def _agrefactorpp_new_model_bucket(
) -> Dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "framework_reported_cost": (
            _agrefactorpp_cost_observation(
                None,
                source="none",
                complete=False,
            )
        ),
        "estimated_cost": None,
        "costs_by_currency": {},
        "cost_usd": None,
        "cost": None,
        "cost_complete": False,
    }


def _agrefactorpp_accumulate_model_usage(
    summary: Dict[str, Any],
    cost_states: Dict[str, Dict[str, Any]],
    *,
    model_name: str,
    data: Mapping[str, Any],
    source: str,
) -> None:
    prompt_tokens = _agrefactorpp_nonnegative_int(
        data.get("prompt_tokens")
    )
    completion_tokens = (
        _agrefactorpp_nonnegative_int(
            data.get("completion_tokens")
        )
    )
    total_tokens = _agrefactorpp_nonnegative_int(
        data.get(
            "total_tokens",
            prompt_tokens + completion_tokens,
        ),
        prompt_tokens + completion_tokens,
    )

    model_key = str(model_name)
    bucket = summary["models"].setdefault(
        model_key,
        _agrefactorpp_new_model_bucket(),
    )
    bucket["prompt_tokens"] += prompt_tokens
    bucket[
        "completion_tokens"
    ] += completion_tokens
    bucket["total_tokens"] += total_tokens

    summary["prompt_tokens"] += prompt_tokens
    summary[
        "completion_tokens"
    ] += completion_tokens
    summary["total_tokens"] += total_tokens

    raw_cost = data.get(
        "cost",
        data.get("total_cost"),
    )
    amount = _agrefactorpp_cost_decimal(raw_cost)

    state = cost_states.setdefault(
        model_key,
        {
            "amount": Decimal("0"),
            "observed": False,
            "complete": True,
            "source": source,
        },
    )
    if amount is None:
        state["complete"] = False
    else:
        state["amount"] += amount
        state["observed"] = True


def _agrefactorpp_finalize_usage_costs(
    summary: Dict[str, Any],
    cost_states: Mapping[str, Mapping[str, Any]],
    *,
    source: str,
    aggregate_amount: Decimal | None = None,
) -> None:
    for model_name, state in cost_states.items():
        observed = bool(state.get("observed"))
        amount = (
            state.get("amount")
            if observed
            else None
        )
        observation = _agrefactorpp_cost_observation(
            amount,
            source=str(state.get("source", source)),
            complete=(
                observed
                and bool(state.get("complete"))
            ),
        )
        bucket = summary["models"][model_name]
        bucket[
            "framework_reported_cost"
        ] = observation
        bucket["cost_complete"] = False

    if aggregate_amount is not None:
        aggregate_observation = (
            _agrefactorpp_cost_observation(
                aggregate_amount,
                source=f"{source}:aggregate",
                complete=True,
            )
        )
    else:
        observed_states = [
            state
            for state in cost_states.values()
            if bool(state.get("observed"))
        ]
        if observed_states:
            aggregate_observation = (
                _agrefactorpp_cost_observation(
                    sum(
                        (
                            state["amount"]
                            for state
                            in observed_states
                        ),
                        Decimal("0"),
                    ),
                    source=f"{source}:model_sum",
                    complete=(
                        len(observed_states)
                        == len(cost_states)
                        and all(
                            bool(
                                state.get(
                                    "complete"
                                )
                            )
                            for state
                            in observed_states
                        )
                    ),
                )
            )
        else:
            aggregate_observation = (
                _agrefactorpp_cost_observation(
                    None,
                    source=f"{source}:unavailable",
                    complete=False,
                )
            )

    summary[
        "framework_reported_cost"
    ] = aggregate_observation
    summary["cost_complete"] = False


def _agrefactorpp_usage_mapping(
    usage: Any,
) -> Mapping[str, Any]:
    if not isinstance(usage, Mapping):
        return {}
    including = usage.get(
        "usage_including_cached_inference"
    )
    excluding = usage.get(
        "usage_excluding_cached_inference"
    )
    if isinstance(including, Mapping):
        return including
    if isinstance(excluding, Mapping):
        return excluding
    return {}


def get_agrefactorpp_usage_summary() -> Dict[str, Any]:
    """Return token observations and currency-safe cost provenance."""

    agents = [
        agent
        for agent in AGREFPP_USAGE_AGENTS
        if agent is not None
    ]
    summary = _agrefactorpp_empty_usage_summary(
        len(agents)
    )
    if not agents:
        return summary

    try:
        from autogen import gather_usage_summary  # type: ignore

        usage_data = _agrefactorpp_usage_mapping(
            gather_usage_summary(agents)
        )
        source = "autogen.gather_usage_summary"
        summary["source"] = source
        cost_states: Dict[
            str,
            Dict[str, Any],
        ] = {}

        for model_name, data in usage_data.items():
            if (
                model_name == "total_cost"
                or not isinstance(data, Mapping)
            ):
                continue
            _agrefactorpp_accumulate_model_usage(
                summary,
                cost_states,
                model_name=str(model_name),
                data=data,
                source=source,
            )

        aggregate_amount = (
            _agrefactorpp_cost_decimal(
                usage_data.get("total_cost")
            )
        )
        _agrefactorpp_finalize_usage_costs(
            summary,
            cost_states,
            source=source,
            aggregate_amount=aggregate_amount,
        )
        return summary
    except Exception as exc:
        source = (
            "fallback_per_agent: "
            f"{type(exc).__name__}: {exc}"
        )
        summary = (
            _agrefactorpp_empty_usage_summary(
                len(agents)
            )
        )
        summary["source"] = source

    cost_states: Dict[
        str,
        Dict[str, Any],
    ] = {}
    for agent in agents:
        for method_name in (
            "get_actual_usage",
            "get_total_usage",
        ):
            method = getattr(
                agent,
                method_name,
                None,
            )
            if not callable(method):
                continue
            try:
                usage = method()
            except Exception:
                usage = None
            if not isinstance(usage, Mapping):
                continue

            for model_name, data in usage.items():
                if (
                    model_name == "total_cost"
                    or not isinstance(
                        data,
                        Mapping,
                    )
                ):
                    continue
                _agrefactorpp_accumulate_model_usage(
                    summary,
                    cost_states,
                    model_name=str(model_name),
                    data=data,
                    source=source,
                )
            break

    _agrefactorpp_finalize_usage_costs(
        summary,
        cost_states,
        source=source,
    )
    return summary


def _agrefactorpp_cost_text(
    observation: Any,
) -> str:
    if not isinstance(observation, Mapping):
        return "unavailable"
    amount = observation.get("amount")
    if amount is None:
        return "unavailable"
    currency = observation.get("currency")
    if isinstance(currency, str) and currency:
        return f"{amount} {currency}"
    return (
        f"{amount} "
        "(currency unspecified; audit only)"
    )


def print_agrefactorpp_usage_summary() -> None:
    """Print token observations without inventing currency semantics."""

    summary = get_agrefactorpp_usage_summary()

    print(
        "=============== Token / Cost Summary "
        "==============="
    )
    print(f"Usage source: {summary.get('source')}")
    print(
        "Registered agents: "
        f"{summary.get('agents', 0)}"
    )

    models = summary.get("models", {})
    if not models:
        print(
            "No token usage was reported by the "
            "current AG2 client."
        )
        print(
            "Cost: "
            + _agrefactorpp_cost_text(
                summary.get(
                    "framework_reported_cost"
                )
            )
        )
        print(
            "===================================================="
        )
        return

    print(
        "Prompt tokens: "
        f"{int(summary.get('prompt_tokens', 0)):,}"
    )
    print(
        "Completion tokens: "
        f"{int(summary.get('completion_tokens', 0)):,}"
    )
    print(
        "Total tokens: "
        f"{int(summary.get('total_tokens', 0)):,}"
    )
    print(
        "Framework-reported cost: "
        + _agrefactorpp_cost_text(
            summary.get(
                "framework_reported_cost"
            )
        )
    )

    for model_name, info in models.items():
        print(f"--- {model_name} ---")
        print(
            "  Prompt tokens: "
            f"{int(info.get('prompt_tokens', 0)):,}"
        )
        print(
            "  Completion tokens: "
            f"{int(info.get('completion_tokens', 0)):,}"
        )
        print(
            "  Total tokens: "
            f"{int(info.get('total_tokens', 0)):,}"
        )
        print(
            "  Framework-reported cost: "
            + _agrefactorpp_cost_text(
                info.get(
                    "framework_reported_cost"
                )
            )
        )

    print(
        "===================================================="
    )

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
        budget: Any = None,
    ):
        self.config_path = Path(config_path)
        self.config_data = yaml_load_file(self.config_path)
        self.agents: Dict[str, ConversableAgent] = {}
        self.agent_configs: Dict[str, Dict[str, Any]] = {}
        self._runtime_llm_config = llm_config_override
        self._global_llm_config = None
        self._context_variables = None
        if budget is not None and not callable(getattr(budget, 'consume', None)):
            raise TypeError('budget must provide consume() or be None')
        self._budget = budget
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
        call_evidence = None
        transport_evidence = None
        if isinstance(merged_llm_config, dict):
            merged_llm_config, call_evidence = (
                pop_internal_call_evidence(merged_llm_config)
            )
            transport_evidence = merged_llm_config.pop(
                '_agrefactor_legacy_transport_evidence',
                None,
            )
            if (
                transport_evidence is not None
                and not isinstance(transport_evidence, Mapping)
            ):
                raise TypeError(
                    'legacy AG2 transport evidence must be a mapping'
                )
        if call_evidence is not None:
            config['_agrefactor_call_evidence'] = call_evidence
        if transport_evidence is not None:
            config['_agrefactor_legacy_transport_evidence'] = (
                copy.deepcopy(dict(transport_evidence))
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

    def _attach_budgeted_run(
        self,
        agent: ConversableAgent,
        call_evidence=None,
        transport_evidence=None,
    ) -> ConversableAgent:
        if getattr(agent, '_agrefactorpp_prompt_recorded_run', False):
            return agent
        original_run = getattr(agent, 'run', None)
        if not callable(original_run):
            return agent

        @wraps(original_run)
        def budgeted_run(*args, **kwargs):
            if self._budget is not None:
                self._budget.consume(llm_calls=1)
            system_message = getattr(agent, 'system_message', None)
            if system_message is not None and not isinstance(system_message, str):
                system_message = str(system_message)
            record_model_prompt_call(
                template_id=(
                    f"ag2:{self.config_path.name}:"
                    f"{getattr(agent, 'name', 'agent')}"
                ),
                template_version=1,
                system_message=system_message,
                invocation={
                    "args": args,
                    "kwargs": kwargs,
                },
                provider_call_observed=True,
                metadata={
                    "agent_name": str(getattr(agent, 'name', 'agent')),
                    "config_file": self.config_path.name,
                    "source": "ag2_agent_run",
                    **(
                        dict(call_evidence)
                        if isinstance(call_evidence, Mapping)
                        else {}
                    ),
                    **(
                        {
                            "legacy_ag2_transport": dict(
                                transport_evidence
                            )
                        }
                        if isinstance(transport_evidence, Mapping)
                        else {}
                    ),
                },
            )
            return original_run(*args, **kwargs)

        agent.run = budgeted_run
        setattr(agent, '_agrefactorpp_budgeted_run', self._budget is not None)
        setattr(agent, '_agrefactorpp_prompt_recorded_run', True)
        return agent

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
        agent = self._attach_budgeted_run(
            agent,
            extra_config.get("_agrefactor_call_evidence"),
            extra_config.get(
                "_agrefactor_legacy_transport_evidence"
            ),
        )
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
