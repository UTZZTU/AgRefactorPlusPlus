import unittest
from types import SimpleNamespace

from agrefactor.models import (
    ChatMessage,
    ModelRequest,
    ModelSpec,
    OpenAICompatibleProvider,
    OpenAICompatibleResponseError,
)
from agrefactor.models.candidate_adapter import (
    candidate_response_reason_codes,
)


class FakeCompletions:
    def __init__(self, response):
        self.response = response

    def create(self, **kwargs):
        return self.response


class FakeClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response)
        )


class Factory:
    def __init__(self, response):
        self.response = response

    def __call__(self, **kwargs):
        return FakeClient(self.response)


def model():
    return ModelSpec(
        name="deepseek-v4-flash",
        provider="openai-compatible",
        model="deepseek-v4-flash",
        family="deepseek",
        base_url="https://example.invalid",
        api_key_env="DEEPSEEK_API_KEY",
    )


def request():
    return ModelRequest(
        messages=(
            ChatMessage(role="system", content="Return JSON."),
            ChatMessage(role="user", content="Analyze typed evidence."),
        ),
        parameters={},
    )


def provider(response):
    return OpenAICompatibleProvider(
        client_factory=Factory(response),
        environment={"DEEPSEEK_API_KEY": "test-secret"},
    )


def assert_error(testcase, response, expected_code):
    with testcase.assertRaises(OpenAICompatibleResponseError) as ctx:
        provider(response).generate(model(), request())
    testcase.assertEqual(ctx.exception.reason_codes, (expected_code,))
    return ctx.exception


class ProviderReasonCodeTests(unittest.TestCase):
    def test_no_choices(self):
        exc = assert_error(
            self,
            SimpleNamespace(
                id="r-no-choices",
                choices=[],
                usage=None,
            ),
            "provider_no_choices",
        )
        self.assertEqual(exc.diagnostics["choices_count"], 0)

    def test_missing_message(self):
        exc = assert_error(
            self,
            SimpleNamespace(
                id="r-no-message",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=None,
                    )
                ],
                usage=None,
            ),
            "provider_missing_message",
        )
        self.assertFalse(exc.diagnostics["message_present"])

    def test_empty_final_content(self):
        exc = assert_error(
            self,
            SimpleNamespace(
                id="r-empty",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content="   ",
                            reasoning_content="private",
                        ),
                    )
                ],
                usage=None,
            ),
            "provider_empty_final_content",
        )
        self.assertEqual(exc.diagnostics["content_chars"], 3)
        self.assertEqual(
            exc.diagnostics["reasoning_content_chars"],
            len("private"),
        )
        self.assertNotIn("private", str(exc.diagnostics))

    def test_finish_length(self):
        assert_error(
            self,
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(content=""),
                    )
                ],
                usage=None,
            ),
            "provider_finish_length",
        )

    def test_content_filtered(self):
        assert_error(
            self,
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="content_filter",
                        message=SimpleNamespace(content=None),
                    )
                ],
                usage=None,
            ),
            "provider_content_filtered",
        )

    def test_insufficient_system_resource(self):
        assert_error(
            self,
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="insufficient_system_resource",
                        message=SimpleNamespace(content=None),
                    )
                ],
                usage=None,
            ),
            "provider_insufficient_system_resource",
        )

    def test_unsupported_content_shape(self):
        exc = assert_error(
            self,
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content={"text": "raw"}),
                    )
                ],
                usage=None,
            ),
            "provider_unsupported_content_shape",
        )
        self.assertEqual(
            exc.diagnostics["content_shape"],
            "unsupported",
        )

    def test_usage_field_invalid(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="valid"),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=-1,
                completion_tokens=2,
            ),
        )
        exc = assert_error(
            self,
            response,
            "provider_usage_field_invalid",
        )
        self.assertTrue(exc.diagnostics["usage_present"])

    def test_usage_field_conflict(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="valid"),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=3,
                input_tokens=4,
                completion_tokens=2,
            ),
        )
        assert_error(
            self,
            response,
            "provider_usage_field_conflict",
        )

    def test_safe_extractor_accepts_provider_codes(self):
        error = OpenAICompatibleResponseError(
            "safe",
            reason_code="provider_no_choices",
        )
        self.assertEqual(
            candidate_response_reason_codes(error),
            ("provider_no_choices",),
        )

    def test_unknown_diagnostic_key_is_rejected(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleResponseError(
                "safe",
                diagnostics={"raw_content": "must not be stored"},
            )

    def test_diagnostics_reject_complex_values(self):
        with self.assertRaises(TypeError):
            OpenAICompatibleResponseError(
                "safe",
                diagnostics={"response_id": {"raw": "value"}},
            )

    def test_bottleneck_call_artifact_records_provider_reason_codes(self):
        from agrefactor.optimization.bottleneck_model import (
            _BottleneckModelEndpoint,
        )

        class RaisingProvider:
            def __init__(self):
                self.calls = 0

            def generate(self, model_spec, model_request):
                self.calls += 1
                raise OpenAICompatibleResponseError(
                    "no choices",
                    reason_code="provider_no_choices",
                )

        class RecordingArtifacts:
            def __init__(self):
                self.calls = []

            def append(self, **kwargs):
                self.calls.append(kwargs)

        endpoint = object.__new__(_BottleneckModelEndpoint)
        raising_provider = RaisingProvider()
        endpoint._provider = raising_provider
        endpoint._model = model()
        endpoint._effective_config = SimpleNamespace(parameters={})
        endpoint._budget = SimpleNamespace(
            record_model_usage=lambda usage: None
        )
        artifacts = RecordingArtifacts()
        endpoint._artifacts = artifacts
        prompt = SimpleNamespace(
            messages=request().messages,
            manifest={"safe": True},
        )

        with self.assertRaises(OpenAICompatibleResponseError):
            endpoint._call(
                prompt=prompt,
                call_kind="bottleneck_analysis",
            )

        self.assertEqual(raising_provider.calls, 1)
        self.assertEqual(len(artifacts.calls), 1)
        self.assertEqual(
            artifacts.calls[0]["error_reason_codes"],
            ("provider_no_choices",),
        )


if __name__ == "__main__":
    unittest.main()
