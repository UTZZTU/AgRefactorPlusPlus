import json
import unittest
from decimal import Decimal

from agrefactor.models import (
    CostEstimate,
    CostEstimationQuality,
    ModelPricingSnapshot,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
    TokenUsage,
    TokenUsageBreakdown,
)


SOURCE_A = "a" * 64
SOURCE_B = "b" * 64


def make_rate(**overrides):
    values = {
        "token_category": "input",
        "amount_per_billing_unit": "1.25",
    }
    values.update(overrides)
    return PricingRate(**values)


def make_snapshot(**overrides):
    values = {
        "provider": "provider",
        "model_id": "model-v1",
        "official_source_identity": "Official pricing",
        "official_source_url": (
            "https://example.invalid/pricing"
        ),
        "retrieved_at": (
            "2026-07-22T00:00:00+00:00"
        ),
        "verification_status": (
            PricingVerificationStatus.OFFICIAL_VERIFIED
        ),
        "applicability": PricingApplicability(
            region="global",
            billing_mode="real-time",
        ),
        "currency": "usd",
        "billing_unit_tokens": 1_000_000,
        "rates": (
            make_rate(),
            make_rate(
                token_category="output",
                amount_per_billing_unit="2.50",
            ),
        ),
        "effective_date": "2026-07-22",
        "source_content_sha256": SOURCE_A,
    }
    values.update(overrides)
    return ModelPricingSnapshot(**values)


def make_estimate(
    *,
    currency="USD",
    quality=CostEstimationQuality.VERIFIED,
):
    return CostEstimate(
        quality=quality,
        amount="0.125",
        currency=currency,
        pricing_snapshot_sha256=SOURCE_A,
    )


class PricingSchemaTests(unittest.TestCase):
    def test_currency_is_normalized(self):
        snapshot = make_snapshot(currency="cny")
        self.assertEqual(snapshot.currency, "CNY")

    def test_currency_rejects_invalid_code(self):
        with self.assertRaises(ValueError):
            make_snapshot(currency="US")

    def test_status_strings_are_normalized_to_enums(self):
        snapshot = make_snapshot(
            verification_status="official_verified"
        )
        estimate = CostEstimate(
            quality="verified",
            amount="1",
            currency="usd",
            pricing_snapshot_sha256=SOURCE_A,
        )
        self.assertIs(
            snapshot.verification_status,
            PricingVerificationStatus.OFFICIAL_VERIFIED,
        )
        self.assertIs(
            estimate.quality,
            CostEstimationQuality.VERIFIED,
        )

    def test_rate_converts_to_decimal(self):
        rate = make_rate(
            amount_per_billing_unit="0.000100"
        )
        self.assertEqual(
            rate.amount_per_billing_unit,
            Decimal("0.000100"),
        )
        self.assertEqual(
            rate.to_dict()["amount_per_billing_unit"],
            "0.0001",
        )

    def test_rate_rejects_negative_nan_and_infinity(self):
        for value in (
            "-1",
            "NaN",
            "Infinity",
            float("inf"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    make_rate(
                        amount_per_billing_unit=value
                    )

    def test_rate_validates_context_bounds(self):
        with self.assertRaises(ValueError):
            make_rate(
                input_token_min_exclusive=100,
                input_token_max_inclusive=100,
            )

    def test_snapshot_hash_is_deterministic_across_rate_order(self):
        first = make_snapshot()
        second = make_snapshot(
            rates=tuple(reversed(first.rates))
        )
        self.assertEqual(
            first.pricing_snapshot_sha256,
            second.pricing_snapshot_sha256,
        )

    def test_snapshot_hash_changes_with_semantic_price(self):
        first = make_snapshot()
        second = make_snapshot(
            rates=(
                make_rate(
                    amount_per_billing_unit="1.26"
                ),
                make_rate(
                    token_category="output",
                    amount_per_billing_unit="2.50",
                ),
            )
        )
        self.assertNotEqual(
            first.pricing_snapshot_sha256,
            second.pricing_snapshot_sha256,
        )

    def test_source_hash_and_retrieval_time_do_not_rewrite_semantic_hash(self):
        first = make_snapshot()
        second = make_snapshot(
            source_content_sha256=SOURCE_B,
            retrieved_at=(
                "2026-07-23T00:00:00+00:00"
            ),
        )
        self.assertNotEqual(
            first.source_content_sha256,
            second.source_content_sha256,
        )
        self.assertEqual(
            first.pricing_snapshot_sha256,
            second.pricing_snapshot_sha256,
        )

    def test_source_and_pricing_hashes_are_distinct_fields(self):
        snapshot = make_snapshot()
        self.assertEqual(
            snapshot.source_content_sha256,
            SOURCE_A,
        )
        self.assertRegex(
            snapshot.pricing_snapshot_sha256,
            r"^[0-9a-f]{64}$",
        )
        self.assertNotEqual(
            snapshot.source_content_sha256,
            snapshot.pricing_snapshot_sha256,
        )

    def test_official_verified_requires_currency(self):
        with self.assertRaises(ValueError):
            make_snapshot(currency=None)

    def test_official_verified_requires_billing_unit(self):
        with self.assertRaises(ValueError):
            make_snapshot(billing_unit_tokens=None)

    def test_official_verified_requires_rates(self):
        with self.assertRaises(ValueError):
            make_snapshot(rates=())

    def test_official_verified_requires_source_hash(self):
        with self.assertRaises(ValueError):
            make_snapshot(source_content_sha256=None)

    def test_unreadable_snapshot_can_preserve_source_without_prices(self):
        snapshot = make_snapshot(
            verification_status=(
                PricingVerificationStatus
                .OFFICIAL_PAGE_UNREADABLE
            ),
            currency=None,
            billing_unit_tokens=None,
            rates=(),
        )
        self.assertIsNone(snapshot.currency)
        self.assertEqual(snapshot.rates, ())

    def test_duplicate_rate_key_is_rejected(self):
        with self.assertRaises(ValueError):
            make_snapshot(
                rates=(make_rate(), make_rate())
            )

    def test_snapshot_dict_is_json_serializable(self):
        encoded = json.dumps(
            make_snapshot().to_dict(),
            sort_keys=True,
        )
        self.assertIn(
            "pricing_snapshot_sha256",
            encoded,
        )

    def test_cost_estimate_verified_normalizes_currency(self):
        estimate = CostEstimate(
            quality=CostEstimationQuality.VERIFIED,
            amount="0.1250",
            currency="usd",
            pricing_snapshot_sha256=SOURCE_A,
        )
        self.assertEqual(
            estimate.amount,
            Decimal("0.1250"),
        )
        self.assertEqual(estimate.currency, "USD")
        self.assertEqual(
            estimate.to_dict()["amount"],
            "0.125",
        )

    def test_cost_estimate_approximate_requires_identity(self):
        with self.assertRaises(ValueError):
            CostEstimate(
                quality=(
                    CostEstimationQuality.APPROXIMATE
                ),
                amount="1",
                currency="USD",
            )

    def test_cost_estimate_rejects_invalid_amount(self):
        for value in ("-0.1", "NaN", "Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    CostEstimate(
                        quality=(
                            CostEstimationQuality.VERIFIED
                        ),
                        amount=value,
                        currency="USD",
                        pricing_snapshot_sha256=SOURCE_A,
                    )

    def test_unavailable_estimate_has_no_amount(self):
        estimate = CostEstimate(
            quality=(
                CostEstimationQuality.UNAVAILABLE
            ),
            unpriced_token_categories=(
                "cache_hit_input",
            ),
        )
        self.assertIsNone(estimate.amount)
        self.assertIsNone(estimate.currency)

    def test_unavailable_estimate_rejects_amount(self):
        with self.assertRaises(ValueError):
            CostEstimate(
                quality=(
                    CostEstimationQuality.UNAVAILABLE
                ),
                amount="1",
                unpriced_token_categories=("input",),
            )

    def test_unavailable_estimate_requires_unpriced_categories(self):
        with self.assertRaises(ValueError):
            CostEstimate(
                quality=(
                    CostEstimationQuality.UNAVAILABLE
                ),
            )

    def test_breakdown_preserves_missing_categories(self):
        breakdown = TokenUsageBreakdown(
            cache_hit_input_tokens=12,
        )
        self.assertEqual(
            breakdown.cache_hit_input_tokens,
            12,
        )
        self.assertIsNone(
            breakdown.cache_miss_input_tokens
        )
        self.assertIsNone(
            breakdown.thinking_output_tokens
        )

    def test_breakdown_rejects_negative_or_boolean_counts(self):
        for value in (-1, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TokenUsageBreakdown(
                        cache_read_tokens=value
                    )

    def test_applicability_normalizes_optional_text(self):
        applicability = PricingApplicability(
            region="  cn-beijing  ",
            deployment_scope=" ",
        )
        self.assertEqual(
            applicability.region,
            "cn-beijing",
        )
        self.assertIsNone(
            applicability.deployment_scope
        )

    def test_old_token_usage_positional_constructor_remains_valid(self):
        usage = TokenUsage(12, 8, 0.25)
        self.assertEqual(usage.total_tokens, 20)
        self.assertEqual(usage.cost_usd, 0.25)
        self.assertIsNone(usage.breakdown)
        self.assertIsNone(usage.estimated_cost)

    def test_old_token_usage_keyword_constructor_remains_valid(self):
        usage = TokenUsage(
            prompt_tokens=12,
            completion_tokens=8,
            cost_usd=0.25,
        )
        self.assertEqual(usage.total_tokens, 20)
        self.assertIsNone(usage.breakdown)
        self.assertIsNone(usage.estimated_cost)

    def test_token_usage_accepts_usd_estimate_with_compatibility_cost(self):
        usage = TokenUsage(
            prompt_tokens=12,
            completion_tokens=8,
            cost_usd=0.125,
            breakdown=TokenUsageBreakdown(
                cache_read_tokens=2
            ),
            estimated_cost=make_estimate(),
        )
        self.assertEqual(
            usage.breakdown.cache_read_tokens,
            2,
        )
        self.assertEqual(
            usage.estimated_cost.currency,
            "USD",
        )

    def test_token_usage_rejects_non_usd_estimate_with_cost_usd(self):
        with self.assertRaises(ValueError):
            TokenUsage(
                prompt_tokens=12,
                completion_tokens=8,
                cost_usd=0.125,
                estimated_cost=make_estimate(
                    currency="CNY"
                ),
            )

    def test_token_usage_accepts_non_usd_estimate_when_cost_usd_is_none(self):
        usage = TokenUsage(
            prompt_tokens=12,
            completion_tokens=8,
            estimated_cost=make_estimate(
                currency="CNY"
            ),
        )
        self.assertIsNone(usage.cost_usd)
        self.assertEqual(
            usage.estimated_cost.currency,
            "CNY",
        )


if __name__ == "__main__":
    unittest.main()
