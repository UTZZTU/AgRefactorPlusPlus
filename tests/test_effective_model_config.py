from __future__ import annotations

import inspect
import json
import os
import unittest
from decimal import Decimal

from agrefactor.models import (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    EffectiveModelConfig,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    KIMI_MODEL_FAMILY_PROFILE,
    ModelFamilyProfile,
    ModelParameterAliasConflictError,
    ModelPricingSnapshot,
    ModelProvider,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
    RejectedModelParameterError,
    UnsupportedReasoningLevelError,
    TokenUsage,
    UnknownModelError,
    UnknownModelFamilyProfileError,
    UnknownProviderError,
)
import agrefactor.models.registry as registry_module


SOURCE_HASH = "a" * 64


class GuardProvider(ModelProvider):
    def __init__(self) -> None:
        self.generate_calls = 0

    @property
    def name(self) -> str:
        return "openai-compatible"

    def generate(
        self,
        model: ModelSpec,
        request: ModelRequest,
    ) -> ModelResponse:
        self.generate_calls += 1
        raise AssertionError(
            "effective configuration resolution must not call Provider"
        )


def pricing_snapshot(
    *,
    model_id: str = "deepseek-v4-flash",
) -> ModelPricingSnapshot:
    return ModelPricingSnapshot(
        provider="deepseek",
        model_id=model_id,
        official_source_identity="Official pricing",
        official_source_url=(
            "https://example.invalid/official-pricing"
        ),
        retrieved_at="2026-07-22T00:00:00+00:00",
        verification_status=(
            PricingVerificationStatus.OFFICIAL_VERIFIED
        ),
        applicability=PricingApplicability(
            region="global",
            billing_mode="real-time",
        ),
        currency="CNY",
        billing_unit_tokens=1_000_000,
        rates=(
            PricingRate(
                token_category="input",
                amount_per_billing_unit=Decimal("1"),
            ),
            PricingRate(
                token_category="output",
                amount_per_billing_unit=Decimal("2"),
            ),
        ),
        effective_date="2026-07-22",
        source_content_sha256=SOURCE_HASH,
    )


def registry_for(
    *,
    family: str | None = "deepseek",
    model_defaults=None,
) -> tuple[ModelRegistry, GuardProvider]:
    provider = GuardProvider()
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="logical-model",
            provider=provider.name,
            model="deepseek-v4-flash",
            family=family,
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            default_parameters=(
                {
                    "temperature": 0.2,
                    "reasoning_effort": "low",
                    "nested": {"value": 1, "items": [1, 2]},
                }
                if model_defaults is None
                else model_defaults
            ),
        )
    )
    return registry, provider


class EffectiveModelConfigTests(unittest.TestCase):
    def test_resolver_returns_typed_effective_config(self):
        registry, _ = registry_for()

        config = registry.resolve_effective_config(
            "logical-model"
        )

        self.assertIsInstance(config, EffectiveModelConfig)
        self.assertEqual(
            config.logical_model_name,
            "logical-model",
        )
        self.assertEqual(
            config.provider_name,
            "openai-compatible",
        )
        self.assertEqual(
            config.model_id,
            "deepseek-v4-flash",
        )

    def test_parameter_precedence_is_family_model_call(self):
        registry, _ = registry_for(
            model_defaults={
                "temperature": 0.2,
                "top_p": 0.8,
                "reasoning_effort": "low",
            }
        )

        config = registry.resolve_effective_config(
            "logical-model",
            parameters={
                "temperature": 0,
                "top_p": 0.5,
            },
        )

        self.assertEqual(config.parameters["temperature"], 0)
        self.assertEqual(config.parameters["top_p"], 0.5)
        self.assertEqual(
            config.parameters["reasoning_effort"],
            "high",
        )

    def test_model_defaults_are_not_mutated(self):
        defaults = {
            "temperature": 0.2,
            "reasoning_effort": "low",
            "nested": {"value": 1},
        }
        registry, _ = registry_for(
            model_defaults=defaults
        )

        registry.resolve_effective_config(
            "logical-model",
            parameters={"temperature": 0},
        )

        self.assertEqual(
            defaults,
            {
                "temperature": 0.2,
                "reasoning_effort": "low",
                "nested": {"value": 1},
            },
        )

    def test_call_overrides_are_not_mutated(self):
        registry, _ = registry_for()
        overrides = {
            "temperature": 0,
            "nested": {"value": [1, 2]},
        }

        registry.resolve_effective_config(
            "logical-model",
            parameters=overrides,
        )

        self.assertEqual(
            overrides,
            {
                "temperature": 0,
                "nested": {"value": [1, 2]},
            },
        )

    def test_effective_parameters_are_deeply_immutable(self):
        registry, _ = registry_for()
        config = registry.resolve_effective_config(
            "logical-model"
        )

        with self.assertRaises(TypeError):
            config.effective_parameters["temperature"] = 9
        with self.assertRaises(TypeError):
            config.effective_parameters["nested"]["value"] = 9
        with self.assertRaises(TypeError):
            config.effective_parameters["nested"]["items"][0] = 9

    def test_parameters_property_returns_detached_copy(self):
        registry, _ = registry_for()
        config = registry.resolve_effective_config(
            "logical-model"
        )

        first = config.parameters
        first["nested"]["items"][0] = 99

        self.assertEqual(
            config.parameters["nested"]["items"],
            [1, 2],
        )

    def test_manifest_returns_detached_copy(self):
        registry, _ = registry_for()
        config = registry.resolve_effective_config(
            "logical-model"
        )

        first = config.to_manifest()
        first["effective_parameters"]["nested"]["items"][0] = 99

        second = config.to_manifest()
        self.assertEqual(
            second["effective_parameters"]["nested"]["items"],
            [1, 2],
        )

    def test_manifest_is_json_serializable(self):
        registry, _ = registry_for()
        config = registry.resolve_effective_config(
            "logical-model"
        )

        payload = config.to_manifest()
        json.dumps(payload, sort_keys=True)

    def test_manifest_contains_transport_identity_without_secret(self):
        registry, _ = registry_for()
        os.environ["DEEPSEEK_API_KEY"] = "must-not-leak"
        try:
            config = registry.resolve_effective_config(
                "logical-model"
            )
            payload = config.to_manifest()
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)

        encoded = json.dumps(payload, sort_keys=True)
        self.assertEqual(
            payload["api_key_env"],
            "DEEPSEEK_API_KEY",
        )
        self.assertEqual(
            payload["base_url"],
            "https://api.deepseek.com",
        )
        self.assertNotIn("must-not-leak", encoded)

    def test_direct_config_rejects_top_level_secret_parameter(self):
        with self.assertRaisesRegex(
            ValueError,
            "credential-like",
        ):
            EffectiveModelConfig(
                logical_model_name="logical",
                provider_name="provider",
                model_id="model",
                family_profile=(
                    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
                ),
                effective_parameters={
                    "api_key": "must-not-be-here"
                },
            )

    def test_direct_config_rejects_nested_secret_parameter(self):
        with self.assertRaisesRegex(
            ValueError,
            "credential-like",
        ):
            EffectiveModelConfig(
                logical_model_name="logical",
                provider_name="provider",
                model_id="model",
                family_profile=(
                    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
                ),
                effective_parameters={
                    "transport": {
                        "access_token": "must-not-be-here"
                    }
                },
            )

    def test_resolution_never_calls_provider(self):
        registry, provider = registry_for()

        registry.resolve_effective_config(
            "logical-model"
        )

        self.assertEqual(provider.generate_calls, 0)

    def test_unknown_model_is_rejected(self):
        registry, _ = registry_for()

        with self.assertRaises(UnknownModelError):
            registry.resolve_effective_config("missing")

    def test_missing_provider_is_rejected(self):
        registry = ModelRegistry()
        registry.register_model(
            ModelSpec(
                name="missing-provider",
                provider="missing",
                model="provider-model",
            )
        )

        with self.assertRaises(UnknownProviderError):
            registry.resolve_effective_config(
                "missing-provider"
            )

    def test_explicit_unknown_family_is_rejected(self):
        provider = GuardProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelSpec(
                name="unknown-family",
                provider=provider.name,
                model="provider-model",
                family="not-registered",
            )
        )

        with self.assertRaises(
            UnknownModelFamilyProfileError
        ):
            registry.resolve_effective_config(
                "unknown-family"
            )
        self.assertEqual(provider.generate_calls, 0)

    def test_absent_family_uses_neutral_profile(self):
        registry, _ = registry_for(
            family=None,
            model_defaults={"temperature": 0.2},
        )

        config = registry.resolve_effective_config(
            "logical-model"
        )

        self.assertEqual(config.family_profile_name, "default")
        self.assertIsNone(config.requested_family_name)
        self.assertIsNone(config.family_instruction)

    def test_family_alias_resolves_to_canonical_profile(self):
        registry, _ = registry_for(
            family="openai",
            model_defaults={"temperature": 0.2},
        )

        config = registry.resolve_effective_config(
            "logical-model"
        )

        self.assertEqual(
            config.requested_family_name,
            "openai",
        )
        self.assertEqual(
            config.family_profile_name,
            "generic-openai-compatible",
        )

    def test_deepseek_reasoning_is_mapped(self):
        registry, _ = registry_for()

        config = registry.resolve_effective_config(
            "logical-model",
            parameters={"reasoning_effort": "medium"},
        )

        self.assertEqual(
            config.parameters["reasoning_effort"],
            "high",
        )

    def test_kimi_reasoning_is_omitted(self):
        provider = GuardProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelSpec(
                name="kimi-model",
                provider=provider.name,
                model="kimi-k2",
                family="kimi",
                default_parameters={
                    "reasoning_effort": "high",
                    "temperature": 0.2,
                },
            )
        )

        config = registry.resolve_effective_config(
            "kimi-model"
        )

        self.assertNotIn(
            "reasoning_effort",
            config.parameters,
        )
        self.assertEqual(
            config.family_profile,
            KIMI_MODEL_FAMILY_PROFILE,
        )

    def test_neutral_reasoning_is_rejected(self):
        registry, _ = registry_for(
            family=None,
            model_defaults={"temperature": 0.2},
        )

        with self.assertRaises(
            UnsupportedReasoningLevelError
        ):
            registry.resolve_effective_config(
                "logical-model",
                parameters={"reasoning_effort": "high"},
            )

    def test_parameter_alias_is_normalized_once(self):
        provider = GuardProvider()
        registry = ModelRegistry(
            include_known_family_profiles=False
        )
        registry.register_provider(provider)
        registry.register_family_profile(
            ModelFamilyProfile(
                name="alias-profile",
                parameter_aliases={
                    "max_completion_tokens": "max_tokens"
                },
            )
        )
        registry.register_model(
            ModelSpec(
                name="alias-model",
                provider=provider.name,
                model="provider-model",
                family="alias-profile",
            )
        )

        config = registry.resolve_effective_config(
            "alias-model",
            parameters={"max_completion_tokens": 4096},
        )

        self.assertEqual(
            config.parameters,
            {"max_tokens": 4096},
        )

    def test_parameter_alias_conflict_is_rejected(self):
        provider = GuardProvider()
        registry = ModelRegistry(
            include_known_family_profiles=False
        )
        registry.register_provider(provider)
        registry.register_family_profile(
            ModelFamilyProfile(
                name="alias-profile",
                parameter_aliases={
                    "max_completion_tokens": "max_tokens"
                },
            )
        )
        registry.register_model(
            ModelSpec(
                name="alias-model",
                provider=provider.name,
                model="provider-model",
                family="alias-profile",
            )
        )

        with self.assertRaises(
            ModelParameterAliasConflictError
        ):
            registry.resolve_effective_config(
                "alias-model",
                parameters={
                    "max_completion_tokens": 4096,
                    "max_tokens": 2048,
                },
            )

    def test_rejected_parameter_is_rejected(self):
        provider = GuardProvider()
        registry = ModelRegistry(
            include_known_family_profiles=False
        )
        registry.register_provider(provider)
        registry.register_family_profile(
            ModelFamilyProfile(
                name="reject-profile",
                rejected_parameters=frozenset(
                    {"logprobs"}
                ),
            )
        )
        registry.register_model(
            ModelSpec(
                name="reject-model",
                provider=provider.name,
                model="provider-model",
                family="reject-profile",
            )
        )

        with self.assertRaises(
            RejectedModelParameterError
        ):
            registry.resolve_effective_config(
                "reject-model",
                parameters={"logprobs": True},
            )

    def test_explicit_pricing_snapshot_identity_is_preserved(self):
        registry, _ = registry_for()
        snapshot = pricing_snapshot()

        config = registry.resolve_effective_config(
            "logical-model",
            pricing_snapshot=snapshot,
        )

        self.assertIs(config.pricing_snapshot, snapshot)
        self.assertEqual(
            config.pricing_snapshot_sha256,
            snapshot.pricing_snapshot_sha256,
        )
        self.assertEqual(
            config.to_manifest()["pricing_snapshot"][
                "pricing_snapshot_sha256"
            ],
            snapshot.pricing_snapshot_sha256,
        )

    def test_pricing_snapshot_model_mismatch_is_rejected(self):
        registry, _ = registry_for()

        with self.assertRaisesRegex(
            ValueError,
            "model_id",
        ):
            registry.resolve_effective_config(
                "logical-model",
                pricing_snapshot=pricing_snapshot(
                    model_id="different-model"
                ),
            )

    def test_no_snapshot_remains_explicitly_none(self):
        registry, _ = registry_for()

        config = registry.resolve_effective_config(
            "logical-model"
        )

        self.assertIsNone(config.pricing_snapshot)
        self.assertIsNone(config.pricing_snapshot_sha256)
        self.assertIsNone(
            config.to_manifest()["pricing_snapshot"]
        )

    def test_approximate_cost_flag_must_be_boolean(self):
        registry, _ = registry_for()

        with self.assertRaises(TypeError):
            registry.resolve_effective_config(
                "logical-model",
                allow_approximate_cost=1,
            )

    def test_approximate_cost_flag_is_manifested(self):
        registry, _ = registry_for()

        config = registry.resolve_effective_config(
            "logical-model",
            allow_approximate_cost=True,
        )

        self.assertTrue(config.allow_approximate_cost)
        self.assertTrue(
            config.to_manifest()[
                "allow_approximate_cost"
            ]
        )

    def test_to_model_spec_preserves_execution_identity(self):
        registry, _ = registry_for()
        config = registry.resolve_effective_config(
            "logical-model"
        )

        spec = config.to_model_spec()

        self.assertEqual(spec.name, "logical-model")
        self.assertEqual(
            spec.provider,
            "openai-compatible",
        )
        self.assertEqual(
            spec.model,
            "deepseek-v4-flash",
        )
        self.assertEqual(spec.family, "deepseek")
        self.assertEqual(
            spec.base_url,
            "https://api.deepseek.com",
        )
        self.assertEqual(
            spec.api_key_env,
            "DEEPSEEK_API_KEY",
        )
        self.assertEqual(spec.default_parameters, {})

    def test_to_model_spec_returns_fresh_value(self):
        registry, _ = registry_for()
        config = registry.resolve_effective_config(
            "logical-model"
        )

        first = config.to_model_spec()
        second = config.to_model_spec()

        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_family_manifest_uses_canonical_profile(self):
        registry, _ = registry_for()

        config = registry.resolve_effective_config(
            "logical-model"
        )
        manifest = config.to_manifest()

        self.assertEqual(
            manifest["family_profile_name"],
            "deepseek",
        )
        self.assertEqual(
            manifest["family_profile"]["name"],
            "deepseek",
        )
        self.assertEqual(
            config.family_profile,
            DEEPSEEK_MODEL_FAMILY_PROFILE,
        )

    def test_direct_config_rejects_wrong_family_profile_type(self):
        with self.assertRaises(TypeError):
            EffectiveModelConfig(
                logical_model_name="logical",
                provider_name="provider",
                model_id="model",
                family_profile="deepseek",
            )

    def test_direct_config_rejects_non_mapping_parameters(self):
        with self.assertRaises(TypeError):
            EffectiveModelConfig(
                logical_model_name="logical",
                provider_name="provider",
                model_id="model",
                family_profile=(
                    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
                ),
                effective_parameters=[],
            )

    def test_direct_config_rejects_non_snapshot_pricing_value(self):
        with self.assertRaises(TypeError):
            EffectiveModelConfig(
                logical_model_name="logical",
                provider_name="provider",
                model_id="model",
                family_profile=(
                    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
                ),
                pricing_snapshot={},
            )

    def test_registry_source_has_no_automatic_pricing_lookup(self):
        source = inspect.getsource(registry_module)

        self.assertNotIn(
            "find_official_model_pricing_snapshots",
            source,
        )
        self.assertNotIn(
            "OFFICIAL_MODEL_PRICING_SNAPSHOTS",
            source,
        )

    def test_registry_source_does_not_call_provider_generate(self):
        source = inspect.getsource(
            ModelRegistry.resolve_effective_config
        )

        self.assertNotIn(".generate(", source)

    def test_effective_config_is_exported_from_models_package(self):
        from agrefactor import models

        self.assertIs(
            models.EffectiveModelConfig,
            EffectiveModelConfig,
        )


if __name__ == "__main__":
    unittest.main()
