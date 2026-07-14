import unittest
from types import SimpleNamespace

from agrefactor.models import (
    ChatMessage,
    MissingModelCredentialError,
    ModelRequest,
    ModelSpec,
    OpenAICompatibleProvider,
    OpenAICompatibleResponseError,
)


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response)
        )


class RecordingClientFactory:
    def __init__(self, response):
        self.response = response
        self.kwargs = []
        self.clients = []

    def __call__(self, **kwargs):
        self.kwargs.append(kwargs)
        client = FakeClient(self.response)
        self.clients.append(client)
        return client


def make_model(**overrides):
    values = {
        "name": "deepseek-repair",
        "provider": "openai-compatible",
        "model": "deepseek-chat",
        "family": "reasoning",
        "base_url": "https://example.invalid/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    }
    values.update(overrides)
    return ModelSpec(**values)


def make_request(parameters=None):
    return ModelRequest(
        messages=(
            ChatMessage(
                role="system",
                content="Repair only the testbench.",
            ),
            ChatMessage(
                role="user",
                content="Return one C++ block.",
            ),
        ),
        parameters=parameters or {},
    )


def make_response():
    return SimpleNamespace(
        id="chatcmpl-test",
        model="deepseek-chat",
        created=123456,
        system_fingerprint="fp-test",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="```cpp\nint main() { return 0; }\n```",
                    reasoning_content="private reasoning",
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
        ),
    )


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_builds_client_and_chat_completion_request(self) -> None:
        factory = RecordingClientFactory(make_response())
        provider = OpenAICompatibleProvider(
            client_factory=factory,
            environment={
                "DEEPSEEK_API_KEY": "secret-test-key",
            },
            timeout_s=45,
        )

        response = provider.generate(
            make_model(),
            make_request(
                {
                    "temperature": 0,
                    "max_tokens": 2048,
                }
            ),
        )

        self.assertEqual(
            factory.kwargs,
            [
                {
                    "api_key": "secret-test-key",
                    "base_url": "https://example.invalid/v1",
                    "timeout": 45,
                }
            ],
        )

        call = factory.clients[0].chat.completions.calls[0]
        self.assertEqual(call["model"], "deepseek-chat")
        self.assertEqual(call["temperature"], 0)
        self.assertEqual(call["max_tokens"], 2048)
        self.assertEqual(
            call["messages"][0],
            {
                "role": "system",
                "content": "Repair only the testbench.",
            },
        )
        self.assertEqual(
            response.text,
            "```cpp\nint main() { return 0; }\n```",
        )

    def test_normalizes_usage_and_safe_metadata(self) -> None:
        factory = RecordingClientFactory(make_response())
        provider = OpenAICompatibleProvider(
            client_factory=factory,
            environment={
                "DEEPSEEK_API_KEY": "secret-test-key",
            },
        )

        response = provider.generate(
            make_model(),
            make_request(),
        )

        self.assertEqual(response.usage.prompt_tokens, 120)
        self.assertEqual(response.usage.completion_tokens, 30)
        self.assertEqual(response.usage.total_tokens, 150)
        self.assertIsNone(response.usage.cost_usd)
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(
            response.metadata["response_id"],
            "chatcmpl-test",
        )
        self.assertTrue(
            response.metadata["has_reasoning_content"]
        )
        self.assertNotIn(
            "private reasoning",
            str(response.metadata),
        )

    def test_missing_api_key_fails_before_client_creation(self) -> None:
        factory = RecordingClientFactory(make_response())
        provider = OpenAICompatibleProvider(
            client_factory=factory,
            environment={},
        )

        with self.assertRaises(MissingModelCredentialError):
            provider.generate(
                make_model(),
                make_request(),
            )

        self.assertEqual(factory.kwargs, [])

    def test_uses_provider_defaults_when_model_omits_endpoint(self) -> None:
        factory = RecordingClientFactory(make_response())
        provider = OpenAICompatibleProvider(
            default_base_url="https://default.invalid/v1",
            default_api_key_env="CUSTOM_API_KEY",
            client_factory=factory,
            environment={
                "CUSTOM_API_KEY": "default-secret",
            },
        )

        provider.generate(
            make_model(
                base_url=None,
                api_key_env=None,
            ),
            make_request(),
        )

        self.assertEqual(
            factory.kwargs[0]["base_url"],
            "https://default.invalid/v1",
        )
        self.assertEqual(
            factory.kwargs[0]["api_key"],
            "default-secret",
        )

    def test_rejects_reserved_request_parameters(self) -> None:
        provider = OpenAICompatibleProvider(
            client_factory=RecordingClientFactory(
                make_response()
            ),
            environment={
                "DEEPSEEK_API_KEY": "secret-test-key",
            },
        )

        with self.assertRaises(ValueError):
            provider.generate(
                make_model(),
                make_request(
                    {
                        "model": "override-not-allowed",
                    }
                ),
            )

    def test_rejects_response_without_choices(self) -> None:
        empty_response = SimpleNamespace(
            choices=[],
            usage=None,
        )
        provider = OpenAICompatibleProvider(
            client_factory=RecordingClientFactory(
                empty_response
            ),
            environment={
                "DEEPSEEK_API_KEY": "secret-test-key",
            },
        )

        with self.assertRaises(OpenAICompatibleResponseError):
            provider.generate(
                make_model(),
                make_request(),
            )

    def test_supports_mapping_responses_and_content_blocks(self) -> None:
        mapping_response = {
            "id": "mapping-response",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "```cpp\n",
                            },
                            {
                                "type": "text",
                                "text": "int main() { return 0; }\n```",
                            },
                        ]
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 7,
            },
        }
        provider = OpenAICompatibleProvider(
            client_factory=RecordingClientFactory(
                mapping_response
            ),
            environment={
                "DEEPSEEK_API_KEY": "secret-test-key",
            },
        )

        response = provider.generate(
            make_model(),
            make_request(),
        )

        self.assertEqual(
            response.text,
            "```cpp\nint main() { return 0; }\n```",
        )
        self.assertEqual(response.usage.total_tokens, 10)


if __name__ == "__main__":
    unittest.main()
