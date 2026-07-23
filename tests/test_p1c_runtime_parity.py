from __future__ import annotations

from decimal import Decimal
import inspect
import json
import unittest

from agrefactor.compat import (
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import TaskSpec
from agrefactor.models import (
    CandidateModelAdapter,
    EffectiveModelConfig,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    ModelPricingSnapshot,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    OpenAICompatibleProvider,
    PricingRate,
    PricingVerificationStatus,
    TokenUsage,
)
from agrefactor.runtime import BudgetManager
from agrefactor.testing import ModelTestbenchRepairer

import agrefactor.compat.legacy_refactor as legacy_module
import agrefactor.testing.factory as repair_factory_module
import flow.base_agent as base_agent_module


class FakeOpenAICompatibleProvider(ModelProvider):
    @property
    def name(self):
        return "openai-compatible"

    def generate(self, model, request):
        raise AssertionError(
            "P1-C4 parity tests must not call a model provider"
        )


def make_snapshot(currency: str) -> ModelPricingSnapshot:
    return ModelPricingSnapshot(
        provider="openai-compatible",
        model_id="parity-model",
        official_source_identity=f"p1c4-{currency.lower()}",
        official_source_url="https://example.invalid/pricing",
        retrieved_at="2026-07-23T00:00:00Z",
        verification_status=(
            PricingVerificationStatus.OFFICIAL_VERIFIED
        ),
        currency=currency,
        billing_unit_tokens=1000,
        rates=(
            PricingRate(
                token_category="input",
                amount_per_billing_unit="1",
            ),
            PricingRate(
                token_category="output",
                amount_per_billing_unit="2",
            ),
        ),
        source_content_sha256="b" * 64,
    )


def make_config(
    *,
    currency: str | None = None,
    model_id: str = "parity-model",
) -> EffectiveModelConfig:
    snapshot = (
        None
        if currency is None
        else make_snapshot(currency)
    )
    return EffectiveModelConfig(
        logical_model_name="p1c4-parity",
        provider_name="openai-compatible",
        model_id=model_id,
        family_profile=(
            GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
        ),
        effective_parameters={
            "temperature": 0,
            "max_tokens": 4096,
        },
        requested_family_name="generic-openai-compatible",
        base_url="https://example.invalid/v1",
        api_key_env="P1C4_FAKE_API_KEY",
        pricing_snapshot=snapshot,
    )


def make_registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_provider(
        FakeOpenAICompatibleProvider()
    )
    return registry


def make_response() -> ModelResponse:
    return ModelResponse(
        text="```cpp\nint main() { return 0; }\n```",
        model="parity-model",
        usage=TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
        ),
        finish_reason="stop",
    )


def make_summary(
    *,
    model_id: str = "parity-model",
    total_tokens: int = 150,
):
    unavailable = {
        "kind": "unavailable",
        "amount": None,
        "currency": None,
        "quality": "unavailable",
        "source": "p1c4",
        "ledger_eligible": False,
        "complete": False,
        "assumptions": [],
    }
    return {
        "agents": 1,
        "models": {
            model_id: {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "framework_reported_cost": unavailable,
                "estimated_cost": None,
                "costs_by_currency": {},
                "cost_usd": None,
                "cost": None,
                "cost_complete": False,
            }
        },
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": total_tokens,
        "source": "p1c4-summary",
        "framework_reported_cost": unavailable,
        "estimated_cost": None,
        "costs_by_currency": {},
        "cost_usd": None,
        "total_cost": None,
        "cost_complete": False,
    }


def make_consumers(config):
    registry = make_registry()
    candidate = CandidateModelAdapter(
        registry=registry,
        effective_config=config,
    )
    repairer = ModelTestbenchRepairer(
        registry=registry,
        effective_config=config,
    )
    return candidate, repairer


def enrich_all(config):
    candidate, repairer = make_consumers(config)
    response = make_response()
    candidate_response = (
        candidate._with_estimated_cost(response)
    )
    repair_response = repairer._with_estimated_cost(
        response
    )
    _, legacy_usages, residual = (
        legacy_module._normalize_usage(
            make_summary(),
            effective_config=config,
        )
    )
    return (
        candidate_response.usage,
        repair_response.usage,
        legacy_usages[0],
        residual,
    )


class P1CRuntimeParityTests(unittest.TestCase):
    def test_candidate_and_repair_share_config_identity(self):
        config = make_config(currency="USD")
        candidate, repairer = make_consumers(config)
        self.assertIs(candidate.effective_config, config)
        self.assertIs(repairer.effective_config, config)

    def test_candidate_and_repair_model_identity_match(self):
        config = make_config()
        candidate, repairer = make_consumers(config)
        self.assertEqual(
            candidate.model_spec.model,
            repairer._model.model,
        )
        self.assertEqual(
            candidate.model_spec.model,
            config.model_id,
        )

    def test_candidate_and_repair_parameters_match(self):
        config = make_config()
        candidate, repairer = make_consumers(config)
        self.assertEqual(
            candidate.effective_parameters,
            repairer.effective_parameters,
        )
        self.assertEqual(
            candidate.effective_parameters,
            config.parameters,
        )

    def test_candidate_and_repair_family_profile_match(self):
        config = make_config()
        candidate, repairer = make_consumers(config)
        self.assertEqual(
            candidate.family_profile.name,
            repairer.family_profile.name,
        )
        self.assertEqual(
            candidate.family_profile.name,
            config.family_profile.name,
        )

    def test_candidate_and_repair_snapshot_hash_match(self):
        config = make_config(currency="USD")
        candidate, repairer = make_consumers(config)
        self.assertEqual(
            candidate.pricing_snapshot.pricing_snapshot_sha256,
            config.pricing_snapshot_sha256,
        )
        self.assertEqual(
            repairer.effective_config.pricing_snapshot_sha256,
            config.pricing_snapshot_sha256,
        )

    def test_usd_estimate_amount_matches_all_consumers(self):
        usages = enrich_all(make_config(currency="USD"))
        amounts = [
            usage.estimated_cost.amount
            for usage in usages[:3]
        ]
        self.assertEqual(
            amounts,
            [Decimal("0.2")] * 3,
        )
        self.assertEqual(usages[3], 0)

    def test_usd_compatibility_view_matches_all_consumers(self):
        usages = enrich_all(make_config(currency="USD"))
        self.assertEqual(
            [usage.cost_usd for usage in usages[:3]],
            [0.2, 0.2, 0.2],
        )

    def test_non_usd_estimate_amount_matches_all_consumers(self):
        usages = enrich_all(make_config(currency="CNY"))
        self.assertEqual(
            [
                usage.estimated_cost.amount
                for usage in usages[:3]
            ],
            [Decimal("0.2")] * 3,
        )
        self.assertEqual(
            [
                usage.estimated_cost.currency
                for usage in usages[:3]
            ],
            ["CNY"] * 3,
        )

    def test_non_usd_never_populates_cost_usd(self):
        usages = enrich_all(make_config(currency="CNY"))
        self.assertEqual(
            [usage.cost_usd for usage in usages[:3]],
            [None, None, None],
        )

    def test_no_snapshot_remains_unpriced_everywhere(self):
        usages = enrich_all(make_config())
        self.assertEqual(
            [
                usage.estimated_cost
                for usage in usages[:3]
            ],
            [None, None, None],
        )
        self.assertEqual(
            [usage.cost_usd for usage in usages[:3]],
            [None, None, None],
        )

    def test_legacy_pricing_requires_exact_model_id(self):
        config = make_config(currency="USD")
        metadata, usages, _ = (
            legacy_module._normalize_usage(
                make_summary(model_id="other-model"),
                effective_config=config,
            )
        )
        self.assertIsNone(usages[0].estimated_cost)
        self.assertEqual(
            metadata["models"]["other-model"][
                "pricing_attribution"
            ],
            "model_id_mismatch",
        )

    def test_legacy_residual_tokens_remain_unpriced(self):
        config = make_config(currency="USD")
        metadata, usages, residual = (
            legacy_module._normalize_usage(
                make_summary(total_tokens=200),
                effective_config=config,
            )
        )
        self.assertEqual(len(usages), 1)
        self.assertEqual(residual, 50)
        self.assertEqual(
            metadata["unattributed_tokens"],
            50,
        )

    def test_legacy_kwargs_carry_same_config_and_manifest(self):
        config = make_config()
        kwargs = build_legacy_refactor_kwargs(
            TaskSpec(
                task_id="p1c4",
                kernel_path="candidate.cpp",
                kernel_name="top_hls",
            ),
            LegacyRefactorSettings(
                effective_model_config=config
            ),
        )
        self.assertIs(
            kwargs["effective_model_config"],
            config,
        )
        self.assertEqual(
            kwargs["effective_model_config_manifest"],
            config.to_manifest(),
        )

    def test_repair_inherits_main_effective_config(self):
        config = make_config()
        kwargs = build_legacy_refactor_kwargs(
            TaskSpec(
                task_id="p1c4-repair",
                kernel_path="candidate.cpp",
                kernel_name="top_hls",
            ),
            LegacyRefactorSettings(
                effective_model_config=config,
                enable_testbench_repair=True,
            ),
        )
        self.assertIs(
            kwargs["testbench_repair_effective_config"],
            config,
        )

    def test_dedicated_repair_config_is_preserved(self):
        main = make_config()
        repair = make_config(model_id="repair-model")
        kwargs = build_legacy_refactor_kwargs(
            TaskSpec(
                task_id="p1c4-dedicated",
                kernel_path="candidate.cpp",
                kernel_name="top_hls",
            ),
            LegacyRefactorSettings(
                effective_model_config=main,
                enable_testbench_repair=True,
                testbench_repair_model="repair-model",
                testbench_repair_effective_config=repair,
            ),
        )
        self.assertIs(
            kwargs["testbench_repair_effective_config"],
            repair,
        )

    def test_conflicting_repair_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(
                enable_testbench_repair=True,
                testbench_repair_model="other",
                testbench_repair_effective_config=(
                    make_config()
                ),
            )

    def test_candidate_parallel_authority_is_rejected(self):
        with self.assertRaises(ValueError):
            CandidateModelAdapter(
                registry=make_registry(),
                model_name="parallel",
                effective_config=make_config(),
            )

    def test_repair_parallel_authority_is_rejected(self):
        with self.assertRaises(ValueError):
            ModelTestbenchRepairer(
                registry=make_registry(),
                model_name="parallel",
                effective_config=make_config(),
            )

    def test_usd_budget_ledger_matches_all_consumers(self):
        usages = enrich_all(make_config(currency="USD"))
        snapshots = []
        for usage in usages[:3]:
            budget = BudgetManager()
            budget.record_model_usage(usage)
            snapshots.append(budget.snapshot())
        self.assertEqual(
            [item.tokens for item in snapshots],
            [150, 150, 150],
        )
        self.assertEqual(
            [item.cost_usd for item in snapshots],
            [0.2, 0.2, 0.2],
        )
        self.assertEqual(
            [
                item.to_dict()["costs_by_currency"]
                for item in snapshots
            ],
            [{"USD": "0.2"}] * 3,
        )

    def test_non_usd_budget_ledger_matches_all_consumers(self):
        usages = enrich_all(make_config(currency="CNY"))
        snapshots = []
        for usage in usages[:3]:
            budget = BudgetManager()
            budget.record_model_usage(usage)
            snapshots.append(budget.snapshot())
        self.assertEqual(
            [item.cost_usd for item in snapshots],
            [0.0, 0.0, 0.0],
        )
        self.assertEqual(
            [
                item.to_dict()["costs_by_currency"]
                for item in snapshots
            ],
            [{"CNY": "0.2"}] * 3,
        )

    def test_unknown_framework_cost_never_enters_ledger(self):
        summary = make_summary()
        summary["framework_reported_cost"] = {
            "kind": "framework_reported",
            "amount": "99",
            "currency": None,
            "quality": "reported_unverified_currency",
            "source": "p1c4",
            "ledger_eligible": False,
            "complete": True,
            "assumptions": [],
        }
        _, usages, _ = legacy_module._normalize_usage(
            summary,
            effective_config=make_config(),
        )
        budget = BudgetManager()
        budget.record_model_usage(usages[0])
        self.assertEqual(
            budget.snapshot().to_dict()[
                "costs_by_currency"
            ],
            {},
        )

    def test_repair_factory_active_path_has_no_family_inference(self):
        source = inspect.getsource(
            repair_factory_module
            .build_openai_compatible_testbench_repairer
        )
        self.assertNotIn(
            "infer_model_family(",
            source,
        )
        self.assertIn(
            "EffectiveModelConfig",
            source,
        )

    def test_loader_remains_vendor_neutral(self):
        source = inspect.getsource(
            base_agent_module.HLSAgentLoader
        ).lower()
        for forbidden in (
            "deepseek",
            "qwen",
            "minimax",
            "moonshot",
            "price_per_1k",
        ):
            self.assertNotIn(forbidden, source)

    def test_provider_transport_has_no_cost_authority(self):
        source = inspect.getsource(
            OpenAICompatibleProvider
        )
        self.assertNotIn(
            "estimate_model_cost",
            source,
        )
        self.assertNotIn(
            "ModelPricingSnapshot",
            source,
        )

    def test_parity_payload_is_json_serializable(self):
        usages = enrich_all(make_config(currency="CNY"))
        payload = {
            "candidate": usages[0].to_dict(),
            "repair": usages[1].to_dict(),
            "legacy": usages[2].to_dict(),
        }
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )


if __name__ == "__main__":
    unittest.main()
