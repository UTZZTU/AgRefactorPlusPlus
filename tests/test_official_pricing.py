import json
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import agrefactor.models.official_pricing as module
from agrefactor.models import (
    OFFICIAL_MODEL_PRICING_SNAPSHOTS,
    OFFICIAL_PRICING_MANIFEST_FILE_SHA256,
    OFFICIAL_PRICING_SOURCE_RECORDS,
    OfficialPricingSourceRecord,
    PricingVerificationStatus,
    find_official_model_pricing_snapshots,
    find_official_pricing_sources,
    official_pricing_manifest,
)


EXPECTED_SOURCE_HASHES = {
    "deepseek_pricing_zh": (
        "de70ea3a08da1e0ad1c5859779b99b6c2eb95b530146bed"
        "351b473abb89c88c8"
    ),
    "kimi_k26_pricing": (
        "3f0db1e9743ab0f15f7e2520912a229eed4ec62bd37c0f7"
        "e0f3606d0b0902a40"
    ),
    "minimax_paygo": (
        "1d187c925e4bf608acde607b5bbf1076221c2a9a7ab25bc"
        "df9dd6e90cc6bc21c"
    ),
    "qwen_model_pricing": (
        "61e5aef7451196d49f0411ffdbe61e31c20da51c261f402"
        "d5bf29eb47735e67d"
    ),
    "glm_pricing": (
        "c988273f16307238bfd94e0c9fe8d4531a3193b88a53724"
        "c3c5f436bd3443994"
    ),
}


def snapshot(provider, model_id, *, tier=None):
    values = find_official_model_pricing_snapshots(
        provider=provider,
        model_id=model_id,
        service_tier=tier,
    )
    if len(values) != 1:
        raise AssertionError(
            f"expected one snapshot, got {len(values)}"
        )
    return values[0]


def rate_map(value):
    return {
        (
            rate.token_category,
            rate.input_token_min_exclusive,
            rate.input_token_max_inclusive,
        ): rate.amount_per_billing_unit
        for rate in value.rates
    }


class OfficialPricingSnapshotTests(unittest.TestCase):
    def test_source_record_count(self):
        self.assertEqual(
            len(OFFICIAL_PRICING_SOURCE_RECORDS),
            5,
        )

    def test_verified_snapshot_count(self):
        self.assertEqual(
            len(OFFICIAL_MODEL_PRICING_SNAPSHOTS),
            6,
        )

    def test_source_ids_are_unique(self):
        ids = [
            value.source_id
            for value in OFFICIAL_PRICING_SOURCE_RECORDS
        ]
        self.assertEqual(len(ids), len(set(ids)))

    def test_source_hashes_match_frozen_p1b0_evidence(self):
        observed = {
            value.source_id: value.source_content_sha256
            for value in OFFICIAL_PRICING_SOURCE_RECORDS
        }
        self.assertEqual(observed, EXPECTED_SOURCE_HASHES)

    def test_source_records_are_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            OFFICIAL_PRICING_SOURCE_RECORDS[0].provider = "x"

    def test_source_urls_are_official_domains(self):
        allowed = (
            "https://api-docs.deepseek.com/",
            "https://platform.kimi.com/",
            "https://platform.minimaxi.com/",
            "https://help.aliyun.com/",
            "https://open.bigmodel.cn/",
        )
        for source in OFFICIAL_PRICING_SOURCE_RECORDS:
            with self.subTest(source=source.source_id):
                self.assertTrue(
                    source.source_url.startswith(allowed),
                    source.source_url,
                )

    def test_glm_is_unreadable_and_has_no_verified_snapshot(self):
        records = find_official_pricing_sources(
            provider="glm"
        )
        self.assertEqual(len(records), 1)
        self.assertIs(
            records[0].verification_status,
            PricingVerificationStatus
            .OFFICIAL_PAGE_UNREADABLE,
        )
        self.assertEqual(
            find_official_model_pricing_snapshots(
                provider="glm"
            ),
            (),
        )

    def test_all_verified_sources_have_expected_models(self):
        for source in OFFICIAL_PRICING_SOURCE_RECORDS:
            if (
                source.verification_status
                is PricingVerificationStatus
                .OFFICIAL_VERIFIED
            ):
                self.assertTrue(source.expected_model_ids)

    def test_all_snapshots_use_one_million_token_unit(self):
        for value in OFFICIAL_MODEL_PRICING_SNAPSHOTS:
            self.assertEqual(
                value.billing_unit_tokens,
                1_000_000,
            )

    def test_all_snapshots_are_official_verified(self):
        for value in OFFICIAL_MODEL_PRICING_SNAPSHOTS:
            self.assertIs(
                value.verification_status,
                PricingVerificationStatus
                .OFFICIAL_VERIFIED,
            )

    def test_snapshot_applicability_keys_are_unique(self):
        keys = [
            (
                value.provider,
                value.model_id,
                tuple(value.applicability.to_dict().items()),
            )
            for value in OFFICIAL_MODEL_PRICING_SNAPSHOTS
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_snapshot_semantic_hashes_are_unique(self):
        hashes = [
            value.pricing_snapshot_sha256
            for value in OFFICIAL_MODEL_PRICING_SNAPSHOTS
        ]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_deepseek_flash_rates(self):
        value = snapshot(
            "deepseek",
            "deepseek-v4-flash",
        )
        self.assertEqual(
            rate_map(value),
            {
                ("cache_hit_input", None, None): Decimal("0.02"),
                ("cache_miss_input", None, None): Decimal("1"),
                ("output", None, None): Decimal("2"),
            },
        )

    def test_deepseek_pro_rates(self):
        value = snapshot(
            "deepseek",
            "deepseek-v4-pro",
        )
        self.assertEqual(
            rate_map(value),
            {
                ("cache_hit_input", None, None): Decimal("0.025"),
                ("cache_miss_input", None, None): Decimal("3"),
                ("output", None, None): Decimal("6"),
            },
        )

    def test_deepseek_snapshots_share_official_source(self):
        values = find_official_model_pricing_snapshots(
            provider="deepseek"
        )
        self.assertEqual(len(values), 2)
        self.assertEqual(
            len({
                value.source_content_sha256
                for value in values
            }),
            1,
        )

    def test_kimi_k26_cny_rates(self):
        value = snapshot("kimi", "kimi-k2.6")
        self.assertEqual(value.currency, "CNY")
        self.assertEqual(
            rate_map(value),
            {
                ("cache_hit_input", None, None): Decimal("1.10"),
                ("cache_miss_input", None, None): Decimal("6.50"),
                ("output", None, None): Decimal("27.00"),
            },
        )

    def test_kimi_snapshot_is_china_endpoint_specific(self):
        value = snapshot("kimi", "kimi-k2.6")
        self.assertEqual(
            value.applicability.deployment_scope,
            "api.moonshot.cn/v1",
        )

    def test_minimax_standard_rates_and_tiers(self):
        value = snapshot(
            "minimax",
            "MiniMax-M3",
            tier="standard",
        )
        self.assertEqual(
            rate_map(value),
            {
                ("cache_read", None, 512000): Decimal("0.42"),
                ("cache_read", 512000, None): Decimal("0.84"),
                ("input", None, 512000): Decimal("2.10"),
                ("input", 512000, None): Decimal("4.20"),
                ("output", None, 512000): Decimal("8.40"),
                ("output", 512000, None): Decimal("16.80"),
            },
        )

    def test_minimax_priority_rates_and_tiers(self):
        value = snapshot(
            "minimax",
            "MiniMax-M3",
            tier="priority",
        )
        self.assertEqual(
            rate_map(value),
            {
                ("cache_read", None, 512000): Decimal("0.63"),
                ("cache_read", 512000, None): Decimal("1.26"),
                ("input", None, 512000): Decimal("3.15"),
                ("input", 512000, None): Decimal("6.30"),
                ("output", None, 512000): Decimal("12.60"),
                ("output", 512000, None): Decimal("25.20"),
            },
        )

    def test_minimax_service_tier_filter(self):
        values = find_official_model_pricing_snapshots(
            provider="minimax",
            model_id="MiniMax-M3",
            service_tier="priority",
        )
        self.assertEqual(len(values), 1)
        self.assertEqual(
            values[0].applicability.service_tier,
            "priority",
        )

    def test_qwen_coder_has_four_input_tiers(self):
        value = snapshot(
            "qwen",
            "qwen3-coder-plus-2025-09-23",
        )
        input_rates = [
            rate
            for rate in value.rates
            if rate.token_category == "input"
        ]
        self.assertEqual(len(input_rates), 4)

    def test_qwen_coder_exact_tier_rates(self):
        value = snapshot(
            "qwen",
            "qwen3-coder-plus-2025-09-23",
        )
        self.assertEqual(
            rate_map(value),
            {
                ("input", None, 32768): Decimal("4"),
                ("input", 32768, 131072): Decimal("6"),
                ("input", 131072, 262144): Decimal("10"),
                ("input", 262144, 1000000): Decimal("20"),
                ("output", None, 32768): Decimal("16"),
                ("output", 32768, 131072): Decimal("24"),
                ("output", 131072, 262144): Decimal("40"),
                ("output", 262144, 1000000): Decimal("200"),
            },
        )

    def test_qwen_snapshot_is_global_scope(self):
        value = snapshot(
            "qwen",
            "qwen3-coder-plus-2025-09-23",
        )
        self.assertEqual(
            value.applicability.deployment_scope,
            "global",
        )

    def test_provider_filter_is_case_insensitive(self):
        values = find_official_model_pricing_snapshots(
            provider="DeepSeek"
        )
        self.assertEqual(len(values), 2)

    def test_model_filter_is_exact(self):
        self.assertEqual(
            find_official_model_pricing_snapshots(
                model_id="DEEPSEEK-V4-FLASH"
            ),
            (),
        )

    def test_unknown_provider_returns_empty_tuple(self):
        self.assertEqual(
            find_official_model_pricing_snapshots(
                provider="unknown"
            ),
            (),
        )

    def test_source_lookup_by_id(self):
        values = find_official_pricing_sources(
            source_id="minimax_paygo"
        )
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].provider, "minimax")

    def test_manifest_file_hash_matches_repository_file(self):
        path = (
            Path(module.__file__).with_name("pricing_sources")
            / "official_pricing_sources_20260722.json"
        )
        import hashlib

        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            OFFICIAL_PRICING_MANIFEST_FILE_SHA256,
        )

    def test_manifest_is_json_serializable(self):
        encoded = json.dumps(
            official_pricing_manifest(),
            sort_keys=True,
        )
        self.assertIn("pricing_snapshot_sha256", encoded)
        self.assertIn("source_content_sha256", encoded)

    def test_module_does_not_implement_cost_estimation(self):
        text = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("CostEstimate", text)
        self.assertNotIn("TokenUsage", text)
        self.assertNotIn("def estimate", text)


if __name__ == "__main__":
    unittest.main()
