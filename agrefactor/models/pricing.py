"""Typed pricing provenance and native-currency cost structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _clean_required(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _clean_optional(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    cleaned = value.strip()
    return cleaned or None


def _clean_currency(value: str | None) -> str | None:
    cleaned = _clean_optional("currency", value)
    if cleaned is None:
        return None
    normalized = cleaned.upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise ValueError(
            "currency must be a three-letter alphabetic code"
        )
    return normalized


def _clean_sha256(name: str, value: str | None) -> str | None:
    cleaned = _clean_optional(name, value)
    if cleaned is None:
        return None
    normalized = cleaned.lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(
            f"{name} must be a 64-character SHA-256 hex digest"
        )
    return normalized


def _clean_decimal(
    name: str,
    value: Decimal | int | str | float | None,
    *,
    optional: bool = False,
) -> Decimal | None:
    if value is None:
        if optional:
            return None
        raise ValueError(f"{name} must not be None")
    if isinstance(value, bool):
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        )
    try:
        converted = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        ) from exc
    if not converted.is_finite() or converted < 0:
        raise ValueError(
            f"{name} must be a finite non-negative decimal"
        )
    return converted


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _clean_optional_nonnegative_int(
    name: str,
    value: int | None,
) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise ValueError(
            f"{name} must be a non-negative integer or None"
        )
    return value


def _clean_string_tuple(name: str, values) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(
            f"{name} must be an iterable of strings"
        )
    try:
        raw = tuple(values)
    except TypeError as exc:
        raise TypeError(
            f"{name} must be an iterable of strings"
        ) from exc
    cleaned = tuple(
        _clean_required(f"{name} item", item)
        for item in raw
    )
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(
            f"{name} must not contain duplicates"
        )
    return cleaned


class PricingVerificationStatus(str, Enum):
    OFFICIAL_VERIFIED = "official_verified"
    OFFICIAL_PAGE_UNREADABLE = "official_page_unreadable"
    NOT_PUBLISHED = "not_published"
    STALE = "stale"
    UNKNOWN = "unknown"


class CostEstimationQuality(str, Enum):
    VERIFIED = "verified"
    APPROXIMATE = "approximate"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PricingApplicability:
    region: str | None = None
    deployment_scope: str | None = None
    billing_mode: str | None = None
    thinking_mode: str | None = None
    cache_mode: str | None = None
    service_tier: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "region",
            "deployment_scope",
            "billing_mode",
            "thinking_mode",
            "cache_mode",
            "service_tier",
        ):
            object.__setattr__(
                self,
                name,
                _clean_optional(
                    name,
                    getattr(self, name),
                ),
            )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "region": self.region,
            "deployment_scope": self.deployment_scope,
            "billing_mode": self.billing_mode,
            "thinking_mode": self.thinking_mode,
            "cache_mode": self.cache_mode,
            "service_tier": self.service_tier,
        }


@dataclass(frozen=True, slots=True)
class PricingRate:
    token_category: str
    amount_per_billing_unit: Decimal | int | str | float
    input_token_min_exclusive: int | None = None
    input_token_max_inclusive: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "token_category",
            _clean_required(
                "token_category",
                self.token_category,
            ),
        )
        object.__setattr__(
            self,
            "amount_per_billing_unit",
            _clean_decimal(
                "amount_per_billing_unit",
                self.amount_per_billing_unit,
            ),
        )
        lower = _clean_optional_nonnegative_int(
            "input_token_min_exclusive",
            self.input_token_min_exclusive,
        )
        upper = _clean_optional_nonnegative_int(
            "input_token_max_inclusive",
            self.input_token_max_inclusive,
        )
        if (
            lower is not None
            and upper is not None
            and upper <= lower
        ):
            raise ValueError(
                "input_token_max_inclusive must be greater than "
                "input_token_min_exclusive"
            )
        object.__setattr__(
            self,
            "input_token_min_exclusive",
            lower,
        )
        object.__setattr__(
            self,
            "input_token_max_inclusive",
            upper,
        )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "token_category": self.token_category,
            "amount_per_billing_unit": _decimal_text(
                self.amount_per_billing_unit
            ),
            "input_token_min_exclusive": (
                self.input_token_min_exclusive
            ),
            "input_token_max_inclusive": (
                self.input_token_max_inclusive
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()


@dataclass(frozen=True, slots=True)
class ModelPricingSnapshot:
    provider: str
    model_id: str
    official_source_identity: str
    official_source_url: str
    retrieved_at: str
    verification_status: PricingVerificationStatus
    applicability: PricingApplicability = field(
        default_factory=PricingApplicability
    )
    model_version: str | None = None
    currency: str | None = None
    billing_unit_tokens: int | None = None
    rates: tuple[PricingRate, ...] = ()
    effective_date: str | None = None
    source_content_sha256: str | None = None
    pricing_snapshot_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            _clean_required("provider", self.provider),
        )
        object.__setattr__(
            self,
            "model_id",
            _clean_required("model_id", self.model_id),
        )
        object.__setattr__(
            self,
            "official_source_identity",
            _clean_required(
                "official_source_identity",
                self.official_source_identity,
            ),
        )
        object.__setattr__(
            self,
            "official_source_url",
            _clean_required(
                "official_source_url",
                self.official_source_url,
            ),
        )
        object.__setattr__(
            self,
            "retrieved_at",
            _clean_required(
                "retrieved_at",
                self.retrieved_at,
            ),
        )

        status = self.verification_status
        if not isinstance(
            status,
            PricingVerificationStatus,
        ):
            try:
                status = PricingVerificationStatus(str(status))
            except ValueError as exc:
                raise ValueError(
                    "unsupported pricing verification status: "
                    f"{self.verification_status!r}"
                ) from exc

        if not isinstance(
            self.applicability,
            PricingApplicability,
        ):
            raise TypeError(
                "applicability must be a PricingApplicability"
            )

        model_version = _clean_optional(
            "model_version",
            self.model_version,
        )
        currency = _clean_currency(self.currency)
        billing_unit = _clean_optional_nonnegative_int(
            "billing_unit_tokens",
            self.billing_unit_tokens,
        )
        if billing_unit == 0:
            raise ValueError(
                "billing_unit_tokens must be positive or None"
            )

        rates = tuple(self.rates)
        if not all(
            isinstance(rate, PricingRate)
            for rate in rates
        ):
            raise TypeError(
                "rates must contain only PricingRate values"
            )
        rate_keys = [
            (
                rate.token_category,
                rate.input_token_min_exclusive,
                rate.input_token_max_inclusive,
            )
            for rate in rates
        ]
        if len(set(rate_keys)) != len(rate_keys):
            raise ValueError(
                "rates must not contain duplicate pricing keys"
            )
        rates = tuple(
            sorted(
                rates,
                key=lambda rate: (
                    rate.token_category,
                    -1
                    if rate.input_token_min_exclusive is None
                    else rate.input_token_min_exclusive,
                    -1
                    if rate.input_token_max_inclusive is None
                    else rate.input_token_max_inclusive,
                ),
            )
        )

        effective_date = _clean_optional(
            "effective_date",
            self.effective_date,
        )
        source_hash = _clean_sha256(
            "source_content_sha256",
            self.source_content_sha256,
        )

        if (
            status
            is PricingVerificationStatus.OFFICIAL_VERIFIED
        ):
            if currency is None:
                raise ValueError(
                    "official_verified snapshot requires currency"
                )
            if billing_unit is None:
                raise ValueError(
                    "official_verified snapshot requires "
                    "billing_unit_tokens"
                )
            if not rates:
                raise ValueError(
                    "official_verified snapshot requires rates"
                )
            if source_hash is None:
                raise ValueError(
                    "official_verified snapshot requires "
                    "source_content_sha256"
                )

        object.__setattr__(
            self,
            "verification_status",
            status,
        )
        object.__setattr__(
            self,
            "model_version",
            model_version,
        )
        object.__setattr__(self, "currency", currency)
        object.__setattr__(
            self,
            "billing_unit_tokens",
            billing_unit,
        )
        object.__setattr__(self, "rates", rates)
        object.__setattr__(
            self,
            "effective_date",
            effective_date,
        )
        object.__setattr__(
            self,
            "source_content_sha256",
            source_hash,
        )

        canonical = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        object.__setattr__(
            self,
            "pricing_snapshot_sha256",
            hashlib.sha256(canonical).hexdigest(),
        )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "applicability": self.applicability.to_dict(),
            "currency": self.currency,
            "billing_unit_tokens": (
                self.billing_unit_tokens
            ),
            "rates": [
                rate.canonical_dict()
                for rate in self.rates
            ],
            "effective_date": self.effective_date,
            "verification_status": (
                self.verification_status.value
            ),
            "official_source_identity": (
                self.official_source_identity
            ),
            "official_source_url": (
                self.official_source_url
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "retrieved_at": self.retrieved_at,
            "source_content_sha256": (
                self.source_content_sha256
            ),
            "pricing_snapshot_sha256": (
                self.pricing_snapshot_sha256
            ),
        }


@dataclass(frozen=True, slots=True)
class TokenUsageBreakdown:
    cache_hit_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    thinking_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "cache_hit_input_tokens",
            "cache_miss_input_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "thinking_output_tokens",
        ):
            object.__setattr__(
                self,
                name,
                _clean_optional_nonnegative_int(
                    name,
                    getattr(self, name),
                ),
            )

    def to_dict(self) -> dict[str, int | None]:
        return {
            "cache_hit_input_tokens": (
                self.cache_hit_input_tokens
            ),
            "cache_miss_input_tokens": (
                self.cache_miss_input_tokens
            ),
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": (
                self.cache_write_tokens
            ),
            "thinking_output_tokens": (
                self.thinking_output_tokens
            ),
        }


@dataclass(frozen=True, slots=True)
class CostEstimate:
    quality: CostEstimationQuality
    amount: Decimal | int | str | float | None = None
    currency: str | None = None
    pricing_snapshot_sha256: str | None = None
    assumptions: tuple[str, ...] = ()
    unpriced_token_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        quality = self.quality
        if not isinstance(
            quality,
            CostEstimationQuality,
        ):
            try:
                quality = CostEstimationQuality(str(quality))
            except ValueError as exc:
                raise ValueError(
                    "unsupported cost estimation quality: "
                    f"{self.quality!r}"
                ) from exc

        amount = _clean_decimal(
            "amount",
            self.amount,
            optional=True,
        )
        currency = _clean_currency(self.currency)
        snapshot_hash = _clean_sha256(
            "pricing_snapshot_sha256",
            self.pricing_snapshot_sha256,
        )
        assumptions = _clean_string_tuple(
            "assumptions",
            self.assumptions,
        )
        unpriced = _clean_string_tuple(
            "unpriced_token_categories",
            self.unpriced_token_categories,
        )

        if quality in (
            CostEstimationQuality.VERIFIED,
            CostEstimationQuality.APPROXIMATE,
        ):
            if (
                amount is None
                or currency is None
                or snapshot_hash is None
            ):
                raise ValueError(
                    "verified/approximate cost estimate requires "
                    "amount, currency, and pricing snapshot identity"
                )
        elif (
            quality
            is CostEstimationQuality.UNAVAILABLE
        ):
            if amount is not None:
                raise ValueError(
                    "unavailable cost estimate must not "
                    "contain amount"
                )
            if not unpriced:
                raise ValueError(
                    "unavailable cost estimate must record "
                    "unpriced token categories"
                )

        object.__setattr__(self, "quality", quality)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(
            self,
            "pricing_snapshot_sha256",
            snapshot_hash,
        )
        object.__setattr__(
            self,
            "assumptions",
            assumptions,
        )
        object.__setattr__(
            self,
            "unpriced_token_categories",
            unpriced,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": (
                None
                if self.amount is None
                else _decimal_text(self.amount)
            ),
            "currency": self.currency,
            "quality": self.quality.value,
            "pricing_snapshot_sha256": (
                self.pricing_snapshot_sha256
            ),
            "assumptions": list(self.assumptions),
            "unpriced_token_categories": list(
                self.unpriced_token_categories
            ),
        }
