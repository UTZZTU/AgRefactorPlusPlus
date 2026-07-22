import json
import unittest
from decimal import Decimal

from agrefactor.models import (
    CostEstimationQuality,
    ModelPricingSnapshot,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
    TokenUsage,
    TokenUsageBreakdown,
    estimate_model_cost,
)


SOURCE_HASH = "a" * 64


def rate(
    category,
    amount,
    *,
    lower=None,
    upper=None,
):
    return PricingRate(
        token_category=category,
        amount_per_billing_unit=amount,
        input_token_min_exclusive=lower,
        input_token_max_inclusive=upper,
    )


def snapshot(
    *rates,
    status=PricingVerificationStatus.OFFICIAL_VERIFIED,
    currency="CNY",
    billing_unit=1_000_000,
):
    return ModelPricingSnapshot(
        provider="provider",
        model_id="model-v1",
        official_source_identity="Official pricing",
        official_source_url=(
            "https://example.invalid/pricing"
        ),
        retrieved_at=(
            "2026-07-22T00:00:00+00:00"
        ),
        verification_status=status,
        applicability=PricingApplicability(
            region="global",
            billing_mode="real-time",
        ),
        currency=currency,
        billing_unit_tokens=billing_unit,
        rates=tuple(rates),
        effective_date="2026-07-22",
        source_content_sha256=SOURCE_HASH,
    )


def deepseek_snapshot():
    return snapshot(
        rate("cache_hit_input", "0.02"),
        rate("cache_miss_input", "1"),
        rate("output", "2"),
    )


def minimax_snapshot(*, priority=False):
    if priority:
        values = (
            ("input", "3.15", None, 512000),
            ("input", "6.30", 512000, None),
            ("output", "12.60", None, 512000),
            ("output", "25.20", 512000, None),
            ("cache_read", "0.63", None, 512000),
            ("cache_read", "1.26", 512000, None),
        )
    else:
        values = (
            ("input", "2.10", None, 512000),
            ("input", "4.20", 512000, None),
            ("output", "8.40", None, 512000),
            ("output", "16.80", 512000, None),
            ("cache_read", "0.42", None, 512000),
            ("cache_read", "0.84", 512000, None),
        )
    return snapshot(
        *(
            rate(
                category,
                amount,
                lower=lower,
                upper=upper,
            )
            for category, amount, lower, upper
            in values
        )
    )


def qwen_snapshot():
    values = (
        ("input", "4", None, 32768),
        ("input", "6", 32768, 131072),
        ("input", "10", 131072, 262144),
        ("input", "20", 262144, 1000000),
        ("output", "16", None, 32768),
        ("output", "24", 32768, 131072),
        ("output", "40", 131072, 262144),
        ("output", "200", 262144, 1000000),
    )
    return snapshot(
        *(
            rate(
                category,
                amount,
                lower=lower,
                upper=upper,
            )
            for category, amount, lower, upper
            in values
        )
    )


class CostEstimatorTests(unittest.TestCase):
    def test_rejects_non_snapshot(self):
        with self.assertRaises(TypeError):
            estimate_model_cost(
                object(),
                TokenUsage(),
            )

    def test_rejects_non_usage(self):
        with self.assertRaises(TypeError):
            estimate_model_cost(
                deepseek_snapshot(),
                object(),
            )

    def test_rejects_non_boolean_approximation_flag(self):
        with self.assertRaises(TypeError):
            estimate_model_cost(
                deepseek_snapshot(),
                TokenUsage(),
                allow_approximate=1,
            )

    def test_deepseek_complete_partition_is_verified(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=200,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=400,
                    cache_miss_input_tokens=600,
                ),
            ),
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.VERIFIED,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.001008"),
        )

    def test_deepseek_derives_missing_cache_miss_exactly(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=0,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=400,
                ),
            ),
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.VERIFIED,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.000608"),
        )
        self.assertEqual(value.assumptions, ())

    def test_deepseek_derives_missing_cache_hit_exactly(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                breakdown=TokenUsageBreakdown(
                    cache_miss_input_tokens=600,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.000608"),
        )
        self.assertEqual(value.assumptions, ())

    def test_deepseek_missing_partition_is_unavailable_by_default(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=200,
            ),
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.UNAVAILABLE,
        )
        self.assertEqual(
            value.unpriced_token_categories,
            (
                "cache_hit_input",
                "cache_miss_input",
            ),
        )

    def test_deepseek_missing_partition_can_be_conservative_approximation(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=200,
            ),
            allow_approximate=True,
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.APPROXIMATE,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.0014"),
        )
        self.assertEqual(
            value.assumptions,
            (
                "cache_hit_input_tokens=0;"
                "cache_miss_input_tokens=prompt_tokens",
            ),
        )

    def test_cache_partition_mismatch_is_unavailable(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=300,
                    cache_miss_input_tokens=600,
                ),
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("input_token_partition",),
        )

    def test_cache_partition_count_above_prompt_is_unavailable(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=1001,
                ),
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("input_token_partition",),
        )

    def test_mixed_cache_partition_and_generic_input_is_unavailable(self):
        value = estimate_model_cost(
            snapshot(
                rate("cache_hit_input", "1"),
                rate("cache_miss_input", "1"),
                rate("input", "1"),
                rate("output", "1"),
            ),
            TokenUsage(
                prompt_tokens=10,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=5,
                    cache_miss_input_tokens=5,
                ),
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("pricing_category_mix",),
        )

    def test_minimax_standard_low_tier_is_verified(self):
        value = estimate_model_cost(
            minimax_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=100,
                breakdown=TokenUsageBreakdown(
                    cache_read_tokens=200,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.002604"),
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.VERIFIED,
        )

    def test_minimax_priority_high_tier_is_verified(self):
        value = estimate_model_cost(
            minimax_snapshot(priority=True),
            TokenUsage(
                prompt_tokens=600000,
                completion_tokens=1000,
                breakdown=TokenUsageBreakdown(
                    cache_read_tokens=100000,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("3.3012"),
        )

    def test_input_tier_upper_bound_is_inclusive(self):
        value = estimate_model_cost(
            minimax_snapshot(),
            TokenUsage(
                prompt_tokens=512000,
                completion_tokens=0,
                breakdown=TokenUsageBreakdown(
                    cache_read_tokens=0,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("1.0752"),
        )

    def test_input_tier_lower_bound_is_exclusive(self):
        value = estimate_model_cost(
            minimax_snapshot(),
            TokenUsage(
                prompt_tokens=512001,
                completion_tokens=0,
                breakdown=TokenUsageBreakdown(
                    cache_read_tokens=0,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("2.1504042"),
        )

    def test_minimax_missing_cache_read_is_unavailable_by_default(self):
        value = estimate_model_cost(
            minimax_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=100,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("cache_read",),
        )

    def test_minimax_missing_cache_read_can_be_approximate(self):
        value = estimate_model_cost(
            minimax_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=100,
            ),
            allow_approximate=True,
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.APPROXIMATE,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.00294"),
        )
        self.assertEqual(
            value.assumptions,
            ("cache_read_tokens=0",),
        )

    def test_cache_read_above_prompt_is_unavailable(self):
        value = estimate_model_cost(
            minimax_snapshot(),
            TokenUsage(
                prompt_tokens=100,
                breakdown=TokenUsageBreakdown(
                    cache_read_tokens=101,
                ),
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("input_token_partition",),
        )

    def test_qwen_first_tier(self):
        value = estimate_model_cost(
            qwen_snapshot(),
            TokenUsage(
                prompt_tokens=32768,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.131088"),
        )

    def test_qwen_second_tier(self):
        value = estimate_model_cost(
            qwen_snapshot(),
            TokenUsage(
                prompt_tokens=32769,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.196638"),
        )

    def test_qwen_third_tier(self):
        value = estimate_model_cost(
            qwen_snapshot(),
            TokenUsage(
                prompt_tokens=131073,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("1.31077"),
        )

    def test_qwen_fourth_tier(self):
        value = estimate_model_cost(
            qwen_snapshot(),
            TokenUsage(
                prompt_tokens=262145,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("5.2431"),
        )

    def test_rate_gap_is_unavailable(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1", upper=100),
                rate("input", "2", lower=200),
                rate("output", "1", upper=100),
                rate("output", "2", lower=200),
            ),
            TokenUsage(
                prompt_tokens=150,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("input", "output"),
        )

    def test_overlapping_rates_are_unavailable(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1", upper=100),
                rate(
                    "input",
                    "2",
                    lower=50,
                    upper=200,
                ),
                rate("output", "1"),
            ),
            TokenUsage(
                prompt_tokens=75,
                completion_tokens=0,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("input",),
        )

    def test_prompt_without_input_rate_is_unavailable(self):
        value = estimate_model_cost(
            snapshot(rate("output", "1")),
            TokenUsage(
                prompt_tokens=10,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("input",),
        )

    def test_completion_without_output_rate_is_unavailable(self):
        value = estimate_model_cost(
            snapshot(rate("input", "1")),
            TokenUsage(
                prompt_tokens=10,
                completion_tokens=1,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("output",),
        )

    def test_unknown_rate_category_is_unavailable(self):
        value = estimate_model_cost(
            snapshot(rate("audio", "1")),
            TokenUsage(),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("audio",),
        )

    def test_zero_usage_is_verified_without_breakdown(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(),
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.VERIFIED,
        )
        self.assertEqual(value.amount, Decimal("0"))

    def test_stale_snapshot_is_unavailable_by_default(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
                status=PricingVerificationStatus.STALE,
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=10,
            ),
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.UNAVAILABLE,
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("pricing_snapshot",),
        )

    def test_stale_snapshot_can_be_explicit_approximation(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
                status=PricingVerificationStatus.STALE,
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=10,
            ),
            allow_approximate=True,
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.APPROXIMATE,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.00012"),
        )
        self.assertEqual(
            value.assumptions,
            ("pricing_snapshot_status=stale",),
        )

    def test_unknown_snapshot_status_remains_unavailable(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
                status=PricingVerificationStatus.UNKNOWN,
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=10,
            ),
            allow_approximate=True,
        )
        self.assertIs(
            value.quality,
            CostEstimationQuality.UNAVAILABLE,
        )

    def test_separate_thinking_output_is_verified(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
                rate("thinking_output", "4"),
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                breakdown=TokenUsageBreakdown(
                    thinking_output_tokens=20,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.00024"),
        )

    def test_missing_thinking_partition_is_unavailable(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
                rate("thinking_output", "4"),
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("thinking_output",),
        )

    def test_missing_thinking_partition_can_be_approximate(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
                rate("thinking_output", "4"),
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
            ),
            allow_approximate=True,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.0002"),
        )
        self.assertEqual(
            value.assumptions,
            ("thinking_output_tokens=0",),
        )

    def test_thinking_is_not_double_counted_when_only_output_is_priced(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                breakdown=TokenUsageBreakdown(
                    thinking_output_tokens=20,
                ),
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.0002"),
        )

    def test_cache_write_rate_requires_breakdown(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "1"),
                rate("cache_write", "2"),
            ),
            TokenUsage(
                prompt_tokens=100,
            ),
        )
        self.assertEqual(
            value.unpriced_token_categories,
            ("cache_write",),
        )

    def test_cache_write_can_be_assumed_zero(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "1"),
                rate("cache_write", "2"),
            ),
            TokenUsage(
                prompt_tokens=100,
            ),
            allow_approximate=True,
        )
        self.assertEqual(
            value.amount,
            Decimal("0.0001"),
        )
        self.assertEqual(
            value.assumptions,
            ("cache_write_tokens=0",),
        )

    def test_existing_cost_usd_is_not_used(self):
        value = estimate_model_cost(
            snapshot(
                rate("input", "1"),
                rate("output", "2"),
            ),
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=10,
                cost_usd=999.0,
            ),
        )
        self.assertEqual(
            value.amount,
            Decimal("0.00012"),
        )
        self.assertEqual(value.currency, "CNY")

    def test_estimator_does_not_mutate_inputs(self):
        pricing = minimax_snapshot()
        usage = TokenUsage(
            prompt_tokens=1000,
            completion_tokens=100,
            breakdown=TokenUsageBreakdown(
                cache_read_tokens=200,
            ),
        )
        pricing_before = pricing.to_dict()
        usage_before = (
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.cost_usd,
            usage.breakdown.to_dict(),
            usage.estimated_cost,
        )

        estimate_model_cost(pricing, usage)

        self.assertEqual(pricing.to_dict(), pricing_before)
        self.assertEqual(
            (
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.cost_usd,
                usage.breakdown.to_dict(),
                usage.estimated_cost,
            ),
            usage_before,
        )

    def test_result_is_json_serializable(self):
        value = estimate_model_cost(
            deepseek_snapshot(),
            TokenUsage(
                prompt_tokens=1000,
                completion_tokens=200,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=400,
                    cache_miss_input_tokens=600,
                ),
            ),
        )
        encoded = json.dumps(
            value.to_dict(),
            sort_keys=True,
        )
        self.assertIn("pricing_snapshot_sha256", encoded)

    def test_result_uses_exact_snapshot_identity(self):
        pricing = deepseek_snapshot()
        value = estimate_model_cost(
            pricing,
            TokenUsage(
                prompt_tokens=1,
                breakdown=TokenUsageBreakdown(
                    cache_hit_input_tokens=0,
                    cache_miss_input_tokens=1,
                ),
            ),
        )
        self.assertEqual(
            value.pricing_snapshot_sha256,
            pricing.pricing_snapshot_sha256,
        )

    def test_native_currency_is_preserved_without_fx(self):
        pricing = snapshot(
            rate("input", "1"),
            rate("output", "1"),
            currency="JPY",
        )
        value = estimate_model_cost(
            pricing,
            TokenUsage(
                prompt_tokens=100,
                completion_tokens=100,
            ),
        )
        self.assertEqual(value.currency, "JPY")
        self.assertEqual(
            value.amount,
            Decimal("0.0002"),
        )


if __name__ == "__main__":
    unittest.main()
