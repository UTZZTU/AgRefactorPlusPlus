import unittest

from agrefactor.testing import (
    ModelTestbenchRepairer,
    build_openai_compatible_testbench_repairer,
    infer_model_family,
)


class TestbenchRepairFactoryTests(unittest.TestCase):
    def test_infers_common_model_families(self) -> None:
        self.assertEqual(
            infer_model_family("deepseek-chat"),
            "deepseek",
        )
        self.assertEqual(
            infer_model_family("qwen3-coder"),
            "qwen",
        )
        self.assertEqual(
            infer_model_family("gpt-5"),
            "openai",
        )
        self.assertIsNone(
            infer_model_family("custom-model"),
        )

    def test_builds_provider_neutral_repairer(self) -> None:
        repairer = build_openai_compatible_testbench_repairer(
            model="deepseek-chat",
            base_url="https://example.invalid/v1",
            api_key_env="DEEPSEEK_API_KEY",
        )
        self.assertIsInstance(
            repairer,
            ModelTestbenchRepairer,
        )
        self.assertEqual(repairer.responses, ())

    def test_rejects_empty_model(self) -> None:
        with self.assertRaises(ValueError):
            build_openai_compatible_testbench_repairer(
                model="  ",
            )

    def test_rejects_empty_api_key_environment_name(self) -> None:
        with self.assertRaises(ValueError):
            build_openai_compatible_testbench_repairer(
                model="deepseek-chat",
                api_key_env=" ",
            )


if __name__ == "__main__":
    unittest.main()
