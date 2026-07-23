from __future__ import annotations

import inspect
from pathlib import Path
import unittest
import yaml

import flow.base_agent as base_agent_module


class P0PortableIdentifyingJsonContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(
            Path("flow/agents/identifying.yaml").read_text(
                encoding="utf-8"
            )
        )

    def test_identifier_response_format_is_portable_json_object(self):
        self.assertEqual(
            self.config["identifier_config"]["response_format"],
            {"type": "json_object"},
        )

    def test_deduplicator_and_filter_use_portable_json_object(self):
        for name in (
            "deduplicator_config",
            "filter_config",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.config[name]["response_format"],
                    {"type": "json_object"},
                )

    def test_identifier_prompts_declare_exact_json_shape(self):
        for name in (
            "system_identifier",
            "recursion_identifier",
            "heap_based_identifier",
            "stack_based_identifier",
            "pointer_identifier",
            "others_identifier",
        ):
            with self.subTest(name=name):
                prompt = self.config["agents"][name][
                    "system_message"
                ]
                self.assertIn("JSON object", prompt)
                self.assertIn('"identified_items"', prompt)

    def test_loader_remains_vendor_neutral(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        ).lower()
        for forbidden in (
            "deepseek",
            "qwen",
            "minimax",
            "moonshot",
            "is_deepseek",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
