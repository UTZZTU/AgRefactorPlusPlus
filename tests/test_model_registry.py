import unittest

from agrefactor.models import (
    ChatMessage,
    ModelProvider,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    TokenUsage,
    UnknownModelError,
    UnknownProviderError,
)


class DummyProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "openai-compatible"

    def generate(
        self,
        model: ModelSpec,
        request: ModelRequest,
    ) -> ModelResponse:
        return ModelResponse(
            text=request.messages[-1].content.upper(),
            model=model.model,
            usage=TokenUsage(prompt_tokens=3, completion_tokens=2),
            finish_reason="stop",
        )


class ModelRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ModelRegistry()
        self.spec = ModelSpec(
            name="deepseek-v4-flash",
            provider="openai-compatible",
            model="deepseek-v4-flash",
            family="deepseek",
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            default_parameters={"reasoning_effort": "low"},
        )

    def test_register_and_resolve_model(self) -> None:
        provider = DummyProvider()
        self.registry.register_provider(provider)
        self.registry.register_model(self.spec)

        spec, resolved_provider = self.registry.resolve(
            "deepseek-v4-flash"
        )

        self.assertEqual(spec, self.spec)
        self.assertIs(resolved_provider, provider)

    def test_provider_generates_normalized_response(self) -> None:
        provider = DummyProvider()
        request = ModelRequest(
            messages=(ChatMessage(role="user", content="hello"),)
        )

        response = provider.generate(self.spec, request)

        self.assertEqual(response.text, "HELLO")
        self.assertEqual(response.usage.total_tokens, 5)

    def test_rejects_duplicate_model(self) -> None:
        self.registry.register_model(self.spec)

        with self.assertRaises(ValueError):
            self.registry.register_model(self.spec)

    def test_reports_unknown_model(self) -> None:
        with self.assertRaises(UnknownModelError):
            self.registry.get_model("missing")

    def test_reports_missing_provider_during_resolve(self) -> None:
        self.registry.register_model(self.spec)

        with self.assertRaises(UnknownProviderError):
            self.registry.resolve("deepseek-v4-flash")

    def test_rejects_non_serializable_defaults(self) -> None:
        with self.assertRaises(TypeError):
            ModelSpec(
                name="bad",
                provider="dummy",
                model="bad",
                default_parameters={"value": object()},
            )


if __name__ == "__main__":
    unittest.main()
