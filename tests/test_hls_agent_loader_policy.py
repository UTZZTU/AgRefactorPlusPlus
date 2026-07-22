from __future__ import annotations

import copy
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

import flow.base_agent as base_agent_module


class FakeLLMConfig:
    def __init__(self, *configs):
        self.configs = copy.deepcopy(configs)


class FakeContextVariables:
    def __init__(self, **values):
        self.values = copy.deepcopy(values)


class FakeAgent:
    def __init__(self, **kwargs):
        self.kwargs = copy.deepcopy(kwargs)


class HLSAgentLoaderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.llm_patch = patch.object(
            base_agent_module,
            "LLMConfig",
            FakeLLMConfig,
        )
        self.context_patch = patch.object(
            base_agent_module,
            "ContextVariables",
            FakeContextVariables,
        )
        self.llm_patch.start()
        self.context_patch.start()

    def tearDown(self):
        self.context_patch.stop()
        self.llm_patch.stop()
        self.tempdir.cleanup()

    def write_config(self, data):
        path = self.root / "agents.yaml"
        path.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def make_loader(
        self,
        *,
        global_llm=None,
        runtime_llm=None,
        agents=None,
        context_variables=None,
    ):
        data = {
            "agents": agents
            or {
                "worker": {
                    "system_message": "Worker system message",
                }
            },
        }
        if global_llm is not None:
            data["llm_config"] = global_llm
        if context_variables is not None:
            data["context_variables"] = context_variables
        return base_agent_module.HLSAgentLoader(
            self.write_config(data),
            llm_config_override=runtime_llm,
        )

    def prepare(self, loader, name="worker"):
        return loader._prepare_agent_config(
            name,
            loader.config_data["agents"][name],
        )

    def single_llm_dict(self, prepared):
        value = prepared["llm_config"]
        self.assertIsInstance(value, FakeLLMConfig)
        self.assertEqual(len(value.configs), 1)
        self.assertIsInstance(value.configs[0], dict)
        return value.configs[0]

    def test_loader_source_has_no_vendor_detection(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        )
        for forbidden in (
            "is_deepseek",
            "'deepseek' in model_l",
            "'deepseek' in base_l",
        ):
            self.assertNotIn(forbidden, source)

    def test_loader_source_has_no_vendor_patch_helper(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        )
        self.assertNotIn(
            "_patch_one_llm_entry",
            source,
        )
        self.assertNotIn(
            "_append_system_message_suffix",
            source,
        )

    def test_loader_source_has_no_price_injection(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        )
        self.assertNotIn(
            "entry['price']",
            source,
        )
        self.assertNotIn(
            "[0.000435, 0.00087]",
            source,
        )
        self.assertNotIn(
            "[0.00014, 0.00028]",
            source,
        )

    def test_loader_source_has_no_max_token_injection(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        )
        self.assertNotIn(
            "setdefault('max_tokens', 8192)",
            source,
        )

    def test_loader_source_has_no_json_output_suffix(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        )
        self.assertNotIn(
            "IMPORTANT OUTPUT FORMAT",
            source,
        )

    def test_usage_fallback_price_helper_is_preserved(self):
        source = inspect.getsource(
            base_agent_module
        )
        self.assertIn(
            "def _agrefactorpp_price_per_1k(",
            source,
        )
        self.assertGreaterEqual(
            source.count("_agrefactorpp_price_per_1k("),
            3,
        )

    def test_runtime_override_wins_over_global_and_agent(self):
        loader = self.make_loader(
            global_llm={
                "model": "global-model",
                "temperature": 0.2,
            },
            runtime_llm={
                "model": "runtime-model",
                "temperature": 0,
                "reasoning_effort": "high",
            },
            agents={
                "worker": {
                    "system_message": "Worker",
                    "llm_config": {
                        "model": "agent-model",
                        "temperature": 0.8,
                        "max_tokens": 1024,
                    },
                }
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertEqual(config["model"], "runtime-model")
        self.assertEqual(config["temperature"], 0)
        self.assertEqual(config["reasoning_effort"], "high")
        self.assertEqual(config["max_tokens"], 1024)

    def test_global_config_wins_over_agent_config(self):
        loader = self.make_loader(
            global_llm={
                "temperature": 0.2,
                "top_p": 0.9,
            },
            agents={
                "worker": {
                    "system_message": "Worker",
                    "llm_config": {
                        "model": "agent-model",
                        "temperature": 0.8,
                    },
                }
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertEqual(config["model"], "agent-model")
        self.assertEqual(config["temperature"], 0.2)
        self.assertEqual(config["top_p"], 0.9)

    def test_agent_config_is_used_without_higher_layers(self):
        loader = self.make_loader(
            agents={
                "worker": {
                    "system_message": "Worker",
                    "llm_config": {
                        "model": "agent-model",
                        "temperature": 0.8,
                    },
                }
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertEqual(
            config,
            {
                "model": "agent-model",
                "temperature": 0.8,
            },
        )

    def test_agent_without_local_config_receives_global(self):
        loader = self.make_loader(
            global_llm={
                "model": "global-model",
                "temperature": 0.2,
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertEqual(config["model"], "global-model")
        self.assertEqual(config["temperature"], 0.2)

    def test_agent_without_local_config_receives_runtime(self):
        loader = self.make_loader(
            runtime_llm={
                "model": "runtime-model",
                "api_type": "openai",
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertEqual(config["model"], "runtime-model")
        self.assertEqual(config["api_type"], "openai")

    def test_deepseek_spelling_does_not_infer_api_type(self):
        loader = self.make_loader(
            runtime_llm={
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com",
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertNotIn("api_type", config)

    def test_deepseek_spelling_does_not_inject_price(self):
        loader = self.make_loader(
            runtime_llm={
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com",
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertNotIn("price", config)

    def test_deepseek_spelling_does_not_inject_max_tokens(self):
        loader = self.make_loader(
            runtime_llm={
                "model": "deepseek-v4-flash",
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertNotIn("max_tokens", config)

    def test_deepseek_spelling_does_not_inject_response_format(self):
        loader = self.make_loader(
            runtime_llm={
                "model": "deepseek-v4-flash",
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertNotIn("response_format", config)

    def test_explicit_generic_fields_are_preserved(self):
        explicit = {
            "model": "typed-model",
            "api_type": "openai",
            "price": [1.0, 2.0],
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }
        loader = self.make_loader(runtime_llm=explicit)
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertEqual(config, explicit)

    def test_system_message_is_not_mutated(self):
        original = {
            "sys_start": "start",
            "sys_middle": "middle",
            "sys_end": "end",
        }
        loader = self.make_loader(
            runtime_llm={
                "model": "deepseek-v4-flash",
                "response_format": "builtins.dict",
            },
            agents={
                "worker": {
                    "system_message": copy.deepcopy(original),
                }
            },
        )
        prepared, _ = self.prepare(loader)
        self.assertEqual(
            prepared["system_message"],
            "startmiddleend",
        )
        self.assertEqual(
            loader.config_data["agents"]["worker"][
                "system_message"
            ],
            original,
        )

    def test_runtime_override_is_not_mutated(self):
        runtime = {
            "model": "runtime-model",
            "nested": {"items": [1, 2]},
        }
        loader = self.make_loader(runtime_llm=runtime)
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        config["nested"]["items"][0] = 99
        self.assertEqual(runtime["nested"]["items"], [1, 2])

    def test_yaml_global_config_is_not_mutated(self):
        global_llm = {
            "model": "global-model",
            "nested": {"items": [1, 2]},
        }
        loader = self.make_loader(
            global_llm=global_llm,
            runtime_llm={"temperature": 0},
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        config["nested"]["items"][0] = 99
        self.assertEqual(
            loader.config_data["llm_config"]["nested"][
                "items"
            ],
            [1, 2],
        )

    def test_agent_source_config_is_not_mutated(self):
        loader = self.make_loader(
            runtime_llm={"temperature": 0},
            agents={
                "worker": {
                    "system_message": "Worker",
                    "llm_config": {
                        "model": "agent-model",
                        "nested": {"items": [1, 2]},
                    },
                }
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        config["nested"]["items"][0] = 99
        self.assertEqual(
            loader.config_data["agents"]["worker"][
                "llm_config"
            ]["nested"]["items"],
            [1, 2],
        )

    def test_runtime_dict_overlays_each_list_entry(self):
        loader = self.make_loader(
            runtime_llm={
                "temperature": 0,
                "api_type": "openai",
            },
            agents={
                "worker": {
                    "system_message": "Worker",
                    "llm_config": [
                        {"model": "model-a", "temperature": 0.7},
                        {"model": "model-b", "temperature": 0.8},
                    ],
                }
            },
        )
        value = self.prepare(loader)[0]["llm_config"]
        self.assertIsInstance(value, FakeLLMConfig)
        self.assertEqual(len(value.configs), 2)
        self.assertEqual(
            value.configs[0],
            {
                "model": "model-a",
                "temperature": 0,
                "api_type": "openai",
            },
        )
        self.assertEqual(
            value.configs[1],
            {
                "model": "model-b",
                "temperature": 0,
                "api_type": "openai",
            },
        )

    def test_runtime_list_is_authoritative(self):
        runtime = [
            {"model": "runtime-a"},
            {"model": "runtime-b"},
        ]
        loader = self.make_loader(
            global_llm={"model": "global-model"},
            runtime_llm=runtime,
            agents={
                "worker": {
                    "system_message": "Worker",
                    "llm_config": {"model": "agent-model"},
                }
            },
        )
        value = self.prepare(loader)[0]["llm_config"]
        self.assertIsInstance(value, FakeLLMConfig)
        self.assertEqual(
            value.configs,
            (
                {"model": "runtime-a"},
                {"model": "runtime-b"},
            ),
        )

    def test_prebuilt_runtime_llm_config_is_atomic(self):
        runtime = FakeLLMConfig(
            {"model": "prebuilt-model"}
        )
        loader = self.make_loader(
            global_llm={"model": "global-model"},
            runtime_llm=runtime,
        )
        value = self.prepare(loader)[0]["llm_config"]
        self.assertIsInstance(value, FakeLLMConfig)
        self.assertEqual(
            value.configs,
            ({"model": "prebuilt-model"},),
        )
        self.assertIsNot(value, runtime)

    def test_dict_constructs_one_llm_config_entry(self):
        loader = self.make_loader(
            runtime_llm={"model": "runtime-model"},
        )
        value = self.prepare(loader)[0]["llm_config"]
        self.assertEqual(
            value.configs,
            ({"model": "runtime-model"},),
        )

    def test_list_constructs_multiple_llm_config_entries(self):
        loader = self.make_loader(
            runtime_llm=[
                {"model": "runtime-a"},
                {"model": "runtime-b"},
            ],
        )
        value = self.prepare(loader)[0]["llm_config"]
        self.assertEqual(len(value.configs), 2)

    def test_context_variables_conversion_is_preserved(self):
        loader = self.make_loader(
            context_variables={
                "kernel_name": "top",
                "model_configuration_source": (
                    "effective_model_config"
                ),
            },
        )
        prepared, _ = self.prepare(loader)
        context = prepared["context_variables"]
        self.assertIsInstance(
            context,
            FakeContextVariables,
        )
        self.assertEqual(
            context.values["kernel_name"],
            "top",
        )

    def test_missing_system_message_still_fails(self):
        loader = self.make_loader(
            agents={
                "worker": {
                    "llm_config": {
                        "model": "agent-model",
                    }
                }
            },
        )
        with self.assertRaisesRegex(
            ValueError,
            "system_message is required",
        ):
            self.prepare(loader)

    def test_human_agent_without_system_message_remains_valid(self):
        loader = self.make_loader(
            agents={
                "human": {
                    "llm_config": {
                        "model": "agent-model",
                    }
                }
            },
        )
        prepared, _ = self.prepare(loader, "human")
        self.assertEqual(prepared["name"], "human")

    def test_load_agent_uses_generic_prepared_config(self):
        registered = []
        loader = self.make_loader(
            runtime_llm={
                "model": "runtime-model",
                "api_type": "openai",
            },
        )
        with (
            patch.object(
                base_agent_module,
                "ConversableAgent",
                FakeAgent,
            ),
            patch.object(
                base_agent_module,
                "register_agrefactorpp_usage_agent",
                side_effect=registered.append,
            ),
        ):
            agent = loader.load_agent("worker")
        self.assertIsInstance(agent, FakeAgent)
        self.assertEqual(
            agent.kwargs["llm_config"].configs[0][
                "model"
            ],
            "runtime-model",
        )
        self.assertEqual(registered, [agent])

    def test_response_format_import_resolves_before_llm_config_construction(self):
        loader = self.make_loader(
            runtime_llm={
                "model": "runtime-model",
                "response_format": "builtins.dict",
            },
        )
        config = self.single_llm_dict(
            self.prepare(loader)[0]
        )
        self.assertIs(
            config["response_format"],
            dict,
        )

    def test_import_resolution_remains_generic(self):
        loader = self.make_loader()
        resolved = loader._resolve_imports(
            {
                "llm_config": {
                    "response_format": "builtins.dict",
                }
            }
        )
        self.assertIs(
            resolved["llm_config"]["response_format"],
            dict,
        )


if __name__ == "__main__":
    unittest.main()
