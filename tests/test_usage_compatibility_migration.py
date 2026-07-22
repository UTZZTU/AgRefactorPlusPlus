import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from agrefactor.models import (
    ChatMessage,
    CostEstimate,
    CostEstimationQuality,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    OpenAICompatibleProvider,
    OpenAICompatibleResponseError,
    TokenUsage,
    TokenUsageBreakdown,
)
from agrefactor.models.candidate_adapter import (
    CandidateModelResult,
    CandidateResponseContract,
)
from agrefactor.repair.protocol import model_response_to_safe_dict


def make_model():
    return ModelSpec(
        name="usage-migration-model",
        provider="openai-compatible",
        model="provider-model-v1",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_API_KEY",
    )


def make_request():
    return ModelRequest(
        messages=(
            ChatMessage(
                role="user",
                content="Return a short answer.",
            ),
        ),
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


def make_provider(response):
    return OpenAICompatibleProvider(
        client_factory=lambda **kwargs: FakeClient(response),
        environment={"TEST_API_KEY": "secret"},
    )


def make_response(usage, *, mapping=False):
    if mapping:
        return {
            "id": "mapping-response",
            "model": "provider-model-v1",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "normalized text"},
                }
            ],
            "usage": usage,
        }
    return SimpleNamespace(
        id="object-response",
        model="provider-model-v1",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="normalized text",
                    reasoning_content="private reasoning",
                ),
            )
        ],
        usage=usage,
    )


def priced_usage():
    return TokenUsage(
        prompt_tokens=100,
        completion_tokens=20,
        breakdown=TokenUsageBreakdown(
            cache_hit_input_tokens=40,
            cache_miss_input_tokens=60,
            thinking_output_tokens=5,
        ),
        estimated_cost=CostEstimate(
            quality=CostEstimationQuality.VERIFIED,
            amount=Decimal("0.00125"),
            currency="CNY",
            pricing_snapshot_sha256="b" * 64,
        ),
    )


def candidate_result(response):
    return CandidateModelResult(
        candidate_code='extern "C" void top() {}',
        logical_model_name="logical-model",
        provider_name="openai-compatible",
        response=response,
        response_contract=CandidateResponseContract(
            top_function_name="top",
            interface_header='extern "C" void top()',
            current_candidate_semantic_sha256="a" * 64,
        ),
    )


class UsageCompatibilityMigrationTests(unittest.TestCase):
    def test_token_usage_to_dict_preserves_legacy_keys(self):
        payload = TokenUsage(
            prompt_tokens=3,
            completion_tokens=7,
            cost_usd=0.25,
        ).to_dict()
        self.assertEqual(payload["prompt_tokens"], 3)
        self.assertEqual(payload["completion_tokens"], 7)
        self.assertEqual(payload["total_tokens"], 10)
        self.assertEqual(payload["cost_usd"], 0.25)
        self.assertIsNone(payload["breakdown"])
        self.assertIsNone(payload["estimated_cost"])

    def test_token_usage_to_dict_serializes_extensions(self):
        payload = priced_usage().to_dict()
        self.assertEqual(
            payload["breakdown"]["cache_hit_input_tokens"],
            40,
        )
        self.assertEqual(
            payload["estimated_cost"]["amount"],
            "0.00125",
        )
        json.dumps(payload, sort_keys=True)

    def test_model_response_to_dict_uses_usage_serializer(self):
        response = ModelResponse(
            text="response text",
            model="provider-model-v1",
            usage=priced_usage(),
            finish_reason="stop",
            metadata={"request_id": "request-1"},
        )
        payload = response.to_dict()
        self.assertEqual(payload["usage"], response.usage.to_dict())
        json.dumps(payload, sort_keys=True)

    def test_candidate_result_uses_shared_response_shape(self):
        response = ModelResponse(
            text="response text",
            model="provider-model-v1",
            usage=priced_usage(),
        )
        self.assertEqual(
            candidate_result(response).to_dict()["response"],
            response.to_dict(),
        )

    def test_repair_protocol_uses_shared_response_shape(self):
        response = ModelResponse(
            text="response text",
            model="provider-model-v1",
            usage=priced_usage(),
        )
        self.assertEqual(
            model_response_to_safe_dict(response),
            response.to_dict(),
        )

    def test_candidate_and_repair_serializers_match(self):
        response = ModelResponse(
            text="response text",
            model="provider-model-v1",
            usage=priced_usage(),
        )
        self.assertEqual(
            candidate_result(response).to_dict()["response"],
            model_response_to_safe_dict(response),
        )

    def test_provider_absent_breakdown_remains_none(self):
        response = make_provider(
            make_response(
                SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=2,
                )
            )
        ).generate(make_model(), make_request())
        self.assertIsNone(response.usage.breakdown)
        self.assertFalse(
            response.metadata["usage_breakdown_observed"]
        )

    def test_deepseek_explicit_cache_and_reasoning(self):
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 40,
            "completion_tokens_details": {
                "reasoning_tokens": 5,
            },
        }
        response = make_provider(
            make_response(usage, mapping=True)
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.usage.breakdown,
            TokenUsageBreakdown(
                cache_hit_input_tokens=60,
                cache_miss_input_tokens=40,
                thinking_output_tokens=5,
            ),
        )

    def test_openai_nested_cached_derives_cache_miss(self):
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 25},
            "completion_tokens_details": {
                "reasoning_tokens": 6,
            },
        }
        response = make_provider(
            make_response(usage, mapping=True)
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.usage.breakdown.cache_hit_input_tokens,
            25,
        )
        self.assertEqual(
            response.usage.breakdown.cache_miss_input_tokens,
            75,
        )
        self.assertEqual(
            response.usage.breakdown.thinking_output_tokens,
            6,
        )

    def test_kimi_top_level_cached_tokens_is_supported(self):
        response = make_provider(
            make_response(
                {
                    "prompt_tokens": 19,
                    "completion_tokens": 21,
                    "cached_tokens": 10,
                },
                mapping=True,
            )
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.usage.breakdown.cache_hit_input_tokens,
            10,
        )
        self.assertEqual(
            response.usage.breakdown.cache_miss_input_tokens,
            9,
        )

    def test_input_output_token_aliases_are_supported(self):
        response = make_provider(
            make_response(
                {
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "input_tokens_details": {
                        "cached_tokens": 8,
                    },
                    "output_tokens_details": {
                        "reasoning_tokens": 3,
                    },
                },
                mapping=True,
            )
        ).generate(make_model(), make_request())
        self.assertEqual(response.usage.prompt_tokens, 30)
        self.assertEqual(response.usage.completion_tokens, 12)
        self.assertEqual(
            response.usage.breakdown.cache_miss_input_tokens,
            22,
        )
        self.assertEqual(
            response.usage.breakdown.thinking_output_tokens,
            3,
        )

    def test_direct_cache_read_and_write_are_supported(self):
        response = make_provider(
            make_response(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 12,
                },
                mapping=True,
            )
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.usage.breakdown.cache_read_tokens,
            40,
        )
        self.assertEqual(
            response.usage.breakdown.cache_write_tokens,
            12,
        )

    def test_nested_cache_creation_is_supported(self):
        response = make_provider(
            make_response(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {
                        "cached_tokens": 20,
                        "cache_creation": {
                            "cache_creation_input_tokens": 15,
                        },
                    },
                },
                mapping=True,
            )
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.usage.breakdown.cache_write_tokens,
            15,
        )

    def test_zero_cached_tokens_is_observed(self):
        response = make_provider(
            make_response(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {
                        "cached_tokens": 0,
                    },
                },
                mapping=True,
            )
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.usage.breakdown.cache_hit_input_tokens,
            0,
        )
        self.assertEqual(
            response.usage.breakdown.cache_miss_input_tokens,
            100,
        )

    def test_conflicting_cached_aliases_are_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "conflicting",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "cached_tokens": 20,
                        "prompt_tokens_details": {
                            "cached_tokens": 30,
                        },
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_explicit_cache_partition_mismatch_is_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "partition",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "prompt_cache_hit_tokens": 40,
                        "prompt_cache_miss_tokens": 50,
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_cached_tokens_above_prompt_are_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "partition",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "cached_tokens": 11,
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_reasoning_tokens_above_completion_are_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "reasoning",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "completion_tokens_details": {
                            "reasoning_tokens": 3,
                        },
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_negative_optional_usage_is_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "cached",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "cached_tokens": -1,
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_boolean_optional_usage_is_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "reasoning",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "completion_tokens_details": {
                            "reasoning_tokens": True,
                        },
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_negative_cache_read_is_rejected(self):
        with self.assertRaisesRegex(
            OpenAICompatibleResponseError,
            "cache_read",
        ):
            make_provider(
                make_response(
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "cache_read_input_tokens": -1,
                    },
                    mapping=True,
                )
            ).generate(make_model(), make_request())

    def test_legacy_total_tokens_remains_unchanged(self):
        response = make_provider(
            make_response(
                {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "prompt_tokens_details": {
                        "cached_tokens": 20,
                    },
                },
                mapping=True,
            )
        ).generate(make_model(), make_request())
        self.assertEqual(response.usage.total_tokens, 150)

    def test_metadata_records_categories_without_reasoning_text(self):
        response = make_provider(
            make_response(
                {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {
                        "cached_tokens": 5,
                    },
                    "completion_tokens_details": {
                        "reasoning_tokens": 4,
                    },
                }
            )
        ).generate(make_model(), make_request())
        self.assertEqual(
            response.metadata["usage_breakdown_categories"],
            [
                "cache_hit_input_tokens",
                "cache_miss_input_tokens",
                "thinking_output_tokens",
            ],
        )
        self.assertNotIn(
            "private reasoning",
            json.dumps(response.metadata),
        )


if __name__ == "__main__":
    unittest.main()
