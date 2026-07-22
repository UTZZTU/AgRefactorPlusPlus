"""Provider-neutral conversion from token usage to native-currency cost."""

from __future__ import annotations

from decimal import Decimal

from .base import TokenUsage
from .pricing import (
    CostEstimate,
    CostEstimationQuality,
    ModelPricingSnapshot,
    PricingRate,
    PricingVerificationStatus,
    TokenUsageBreakdown,
)


_SUPPORTED_TOKEN_CATEGORIES = frozenset(
    {
        "input",
        "output",
        "cache_hit_input",
        "cache_miss_input",
        "cache_read",
        "cache_write",
        "thinking_output",
    }
)
_CACHE_PARTITION_CATEGORIES = frozenset(
    {
        "cache_hit_input",
        "cache_miss_input",
    }
)


def _unavailable(
    snapshot: ModelPricingSnapshot,
    categories,
    *,
    assumptions: tuple[str, ...] = (),
) -> CostEstimate:
    normalized = tuple(
        sorted(
            {
                str(category).strip()
                for category in categories
                if str(category).strip()
            }
        )
    )
    if not normalized:
        normalized = ("pricing_data",)
    return CostEstimate(
        quality=CostEstimationQuality.UNAVAILABLE,
        currency=snapshot.currency,
        pricing_snapshot_sha256=(
            snapshot.pricing_snapshot_sha256
        ),
        assumptions=assumptions,
        unpriced_token_categories=normalized,
    )


def _rate_matches(
    rate: PricingRate,
    input_tokens: int,
) -> bool:
    lower = rate.input_token_min_exclusive
    upper = rate.input_token_max_inclusive
    return (
        (lower is None or input_tokens > lower)
        and (upper is None or input_tokens <= upper)
    )


def _matching_rate(
    snapshot: ModelPricingSnapshot,
    category: str,
    input_tokens: int,
) -> PricingRate | None:
    matches = tuple(
        rate
        for rate in snapshot.rates
        if rate.token_category == category
        and _rate_matches(rate, input_tokens)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _breakdown_or_empty(
    usage: TokenUsage,
) -> TokenUsageBreakdown:
    return usage.breakdown or TokenUsageBreakdown()


def estimate_model_cost(
    snapshot: ModelPricingSnapshot,
    usage: TokenUsage,
    *,
    allow_approximate: bool = False,
) -> CostEstimate:
    """Estimate one response cost using an explicit pricing snapshot.

    ``prompt_tokens`` and ``completion_tokens`` are authoritative totals.
    Breakdown fields are optional partitions of those totals. No provider
    lookup, currency conversion, Budget update, or TokenUsage mutation occurs.
    """

    if not isinstance(snapshot, ModelPricingSnapshot):
        raise TypeError(
            "snapshot must be a ModelPricingSnapshot"
        )
    if not isinstance(usage, TokenUsage):
        raise TypeError("usage must be a TokenUsage")
    if not isinstance(allow_approximate, bool):
        raise TypeError(
            "allow_approximate must be a bool"
        )

    assumptions: list[str] = []
    if (
        snapshot.verification_status
        is PricingVerificationStatus.STALE
    ):
        if not allow_approximate:
            return _unavailable(
                snapshot,
                ("pricing_snapshot",),
            )
        assumptions.append(
            "pricing_snapshot_status=stale"
        )
    elif (
        snapshot.verification_status
        is not PricingVerificationStatus.OFFICIAL_VERIFIED
    ):
        return _unavailable(
            snapshot,
            ("pricing_snapshot",),
        )

    if (
        snapshot.currency is None
        or snapshot.billing_unit_tokens is None
        or snapshot.billing_unit_tokens <= 0
        or not snapshot.rates
    ):
        return _unavailable(
            snapshot,
            ("pricing_snapshot",),
            assumptions=tuple(assumptions),
        )

    categories = {
        rate.token_category
        for rate in snapshot.rates
    }
    unsupported = categories - _SUPPORTED_TOKEN_CATEGORIES
    if unsupported:
        return _unavailable(
            snapshot,
            unsupported,
            assumptions=tuple(assumptions),
        )

    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    breakdown = _breakdown_or_empty(usage)
    counts: dict[str, int] = {}
    unresolved: set[str] = set()

    has_cache_partition = bool(
        categories & _CACHE_PARTITION_CATEGORIES
    )
    if has_cache_partition and (
        "input" in categories
        or "cache_read" in categories
    ):
        return _unavailable(
            snapshot,
            ("pricing_category_mix",),
            assumptions=tuple(assumptions),
        )

    if has_cache_partition:
        hit = breakdown.cache_hit_input_tokens
        miss = breakdown.cache_miss_input_tokens

        if prompt_tokens == 0:
            if (hit not in (None, 0)) or (
                miss not in (None, 0)
            ):
                unresolved.add("input_token_partition")
            hit = 0
            miss = 0
        elif hit is None and miss is None:
            if allow_approximate:
                hit = 0
                miss = prompt_tokens
                assumptions.append(
                    "cache_hit_input_tokens=0;"
                    "cache_miss_input_tokens=prompt_tokens"
                )
            else:
                unresolved.update(
                    (
                        "cache_hit_input",
                        "cache_miss_input",
                    )
                )
        elif hit is None:
            if miss is None or miss > prompt_tokens:
                unresolved.add("input_token_partition")
            else:
                hit = prompt_tokens - miss
        elif miss is None:
            if hit > prompt_tokens:
                unresolved.add("input_token_partition")
            else:
                miss = prompt_tokens - hit
        elif hit + miss != prompt_tokens:
            unresolved.add("input_token_partition")

        if not unresolved:
            counts["cache_hit_input"] = int(hit or 0)
            counts["cache_miss_input"] = int(miss or 0)
    else:
        cache_read = 0
        if "cache_read" in categories:
            observed_cache_read = (
                breakdown.cache_read_tokens
            )
            if prompt_tokens == 0:
                if observed_cache_read not in (None, 0):
                    unresolved.add(
                        "input_token_partition"
                    )
            elif observed_cache_read is None:
                if allow_approximate:
                    assumptions.append(
                        "cache_read_tokens=0"
                    )
                else:
                    unresolved.add("cache_read")
            elif observed_cache_read > prompt_tokens:
                unresolved.add("input_token_partition")
            else:
                cache_read = observed_cache_read

            counts["cache_read"] = cache_read

        remaining_input = prompt_tokens - cache_read
        if remaining_input < 0:
            unresolved.add("input_token_partition")
        elif "input" in categories:
            counts["input"] = remaining_input
        elif remaining_input > 0:
            unresolved.add("input")

    if "cache_write" in categories:
        cache_write = breakdown.cache_write_tokens
        if cache_write is None:
            if allow_approximate:
                cache_write = 0
                assumptions.append(
                    "cache_write_tokens=0"
                )
            else:
                unresolved.add("cache_write")
        if cache_write is not None:
            counts["cache_write"] = cache_write

    if "thinking_output" in categories:
        thinking = breakdown.thinking_output_tokens
        if completion_tokens == 0:
            if thinking not in (None, 0):
                unresolved.add(
                    "output_token_partition"
                )
            thinking = 0
        elif thinking is None:
            if allow_approximate:
                thinking = 0
                assumptions.append(
                    "thinking_output_tokens=0"
                )
            else:
                unresolved.add("thinking_output")
        elif thinking > completion_tokens:
            unresolved.add("output_token_partition")

        if thinking is not None:
            counts["thinking_output"] = thinking
            non_thinking = completion_tokens - thinking
            if non_thinking < 0:
                unresolved.add(
                    "output_token_partition"
                )
            elif "output" in categories:
                counts["output"] = non_thinking
            elif non_thinking > 0:
                unresolved.add("output")
    elif "output" in categories:
        counts["output"] = completion_tokens
    elif completion_tokens > 0:
        unresolved.add("output")

    if unresolved:
        return _unavailable(
            snapshot,
            unresolved,
            assumptions=tuple(assumptions),
        )

    total = Decimal("0")
    missing_rates: set[str] = set()
    billing_unit = Decimal(
        snapshot.billing_unit_tokens
    )

    for category, count in counts.items():
        if count == 0:
            continue
        rate = _matching_rate(
            snapshot,
            category,
            prompt_tokens,
        )
        if rate is None:
            missing_rates.add(category)
            continue
        total += (
            Decimal(count)
            * rate.amount_per_billing_unit
            / billing_unit
        )

    if missing_rates:
        return _unavailable(
            snapshot,
            missing_rates,
            assumptions=tuple(assumptions),
        )

    quality = (
        CostEstimationQuality.APPROXIMATE
        if assumptions
        else CostEstimationQuality.VERIFIED
    )
    return CostEstimate(
        quality=quality,
        amount=total,
        currency=snapshot.currency,
        pricing_snapshot_sha256=(
            snapshot.pricing_snapshot_sha256
        ),
        assumptions=tuple(assumptions),
    )
