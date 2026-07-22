"""Static official concrete-model pricing snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .pricing import (
    ModelPricingSnapshot,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_PATH = (
    Path(__file__).with_name("pricing_sources")
    / "official_pricing_sources_20260722.json"
)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    cleaned = value.strip()
    return cleaned or None


def _sha256_text(name: str, value: object) -> str:
    cleaned = _required_text(name, value).lower()
    if not _SHA256_RE.fullmatch(cleaned):
        raise ValueError(
            f"{name} must be a 64-character SHA-256 hex digest"
        )
    return cleaned


def _string_tuple(name: str, value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of strings")
    try:
        raw = tuple(value)
    except TypeError as exc:
        raise TypeError(
            f"{name} must be a sequence of strings"
        ) from exc
    cleaned = tuple(
        _required_text(f"{name} item", item)
        for item in raw
    )
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{name} must not contain duplicates")
    return cleaned


@dataclass(frozen=True, slots=True)
class OfficialPricingSourceRecord:
    source_id: str
    provider: str
    source_identity: str
    source_url: str
    retrieved_at: str
    source_content_sha256: str
    verification_status: PricingVerificationStatus
    expected_model_ids: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            _required_text("source_id", self.source_id),
        )
        object.__setattr__(
            self,
            "provider",
            _required_text("provider", self.provider).casefold(),
        )
        object.__setattr__(
            self,
            "source_identity",
            _required_text(
                "source_identity",
                self.source_identity,
            ),
        )
        object.__setattr__(
            self,
            "source_url",
            _required_text("source_url", self.source_url),
        )
        object.__setattr__(
            self,
            "retrieved_at",
            _required_text("retrieved_at", self.retrieved_at),
        )
        object.__setattr__(
            self,
            "source_content_sha256",
            _sha256_text(
                "source_content_sha256",
                self.source_content_sha256,
            ),
        )

        status = self.verification_status
        if not isinstance(status, PricingVerificationStatus):
            try:
                status = PricingVerificationStatus(str(status))
            except ValueError as exc:
                raise ValueError(
                    "unsupported pricing verification status: "
                    f"{self.verification_status!r}"
                ) from exc
        object.__setattr__(
            self,
            "verification_status",
            status,
        )
        object.__setattr__(
            self,
            "expected_model_ids",
            _string_tuple(
                "expected_model_ids",
                self.expected_model_ids,
            ),
        )
        object.__setattr__(
            self,
            "note",
            _optional_text("note", self.note),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "source_identity": self.source_identity,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "source_content_sha256": (
                self.source_content_sha256
            ),
            "verification_status": (
                self.verification_status.value
            ),
            "expected_model_ids": list(
                self.expected_model_ids
            ),
            "note": self.note,
        }


def _load_manifest_payload() -> tuple[dict[str, Any], str]:
    raw = _MANIFEST_PATH.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            "official pricing manifest must contain an object"
        )
    if payload.get("schema_version") != 1:
        raise ValueError(
            "unsupported official pricing manifest schema"
        )
    return payload, hashlib.sha256(raw).hexdigest()


def _source_record(raw: object) -> OfficialPricingSourceRecord:
    if not isinstance(raw, dict):
        raise TypeError("source record must be an object")
    return OfficialPricingSourceRecord(
        source_id=raw.get("source_id"),
        provider=raw.get("provider"),
        source_identity=raw.get("source_identity"),
        source_url=raw.get("source_url"),
        retrieved_at=raw.get("retrieved_at"),
        source_content_sha256=raw.get(
            "source_content_sha256"
        ),
        verification_status=raw.get(
            "verification_status"
        ),
        expected_model_ids=tuple(
            raw.get("expected_model_ids", ())
        ),
        note=raw.get("note"),
    )


def _snapshot(
    raw: object,
    sources: dict[str, OfficialPricingSourceRecord],
) -> ModelPricingSnapshot:
    if not isinstance(raw, dict):
        raise TypeError("pricing snapshot must be an object")
    source_id = _required_text(
        "snapshot source_id",
        raw.get("source_id"),
    )
    try:
        source = sources[source_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown pricing source_id: {source_id}"
        ) from exc

    applicability_raw = raw.get("applicability", {})
    if not isinstance(applicability_raw, dict):
        raise TypeError(
            "snapshot applicability must be an object"
        )
    rates_raw = raw.get("rates", ())
    if isinstance(rates_raw, (str, bytes)):
        raise TypeError("snapshot rates must be a sequence")

    rates = []
    for rate in rates_raw:
        if not isinstance(rate, dict):
            raise TypeError("pricing rate must be an object")
        rates.append(
            PricingRate(
                token_category=rate.get("token_category"),
                amount_per_billing_unit=rate.get(
                    "amount_per_billing_unit"
                ),
                input_token_min_exclusive=rate.get(
                    "input_token_min_exclusive"
                ),
                input_token_max_inclusive=rate.get(
                    "input_token_max_inclusive"
                ),
            )
        )

    provider = _required_text(
        "snapshot provider",
        raw.get("provider"),
    ).casefold()
    model_id = _required_text(
        "snapshot model_id",
        raw.get("model_id"),
    )
    if provider != source.provider:
        raise ValueError(
            f"snapshot provider {provider!r} does not match "
            f"source provider {source.provider!r}"
        )
    if (
        source.verification_status
        is not PricingVerificationStatus.OFFICIAL_VERIFIED
    ):
        raise ValueError(
            "verified pricing snapshots require an "
            "official_verified source record"
        )
    if model_id not in source.expected_model_ids:
        raise ValueError(
            f"model {model_id!r} is not declared by source "
            f"{source.source_id!r}"
        )

    return ModelPricingSnapshot(
        provider=provider,
        model_id=model_id,
        model_version=raw.get("model_version"),
        official_source_identity=source.source_identity,
        official_source_url=source.source_url,
        retrieved_at=source.retrieved_at,
        verification_status=(
            PricingVerificationStatus.OFFICIAL_VERIFIED
        ),
        applicability=PricingApplicability(
            region=applicability_raw.get("region"),
            deployment_scope=applicability_raw.get(
                "deployment_scope"
            ),
            billing_mode=applicability_raw.get(
                "billing_mode"
            ),
            thinking_mode=applicability_raw.get(
                "thinking_mode"
            ),
            cache_mode=applicability_raw.get(
                "cache_mode"
            ),
            service_tier=applicability_raw.get(
                "service_tier"
            ),
        ),
        currency=raw.get("currency"),
        billing_unit_tokens=raw.get(
            "billing_unit_tokens"
        ),
        rates=tuple(rates),
        effective_date=raw.get("effective_date"),
        source_content_sha256=(
            source.source_content_sha256
        ),
    )


def _snapshot_key(
    snapshot: ModelPricingSnapshot,
) -> tuple[object, ...]:
    applicability = snapshot.applicability
    return (
        snapshot.provider.casefold(),
        snapshot.model_id,
        applicability.region,
        applicability.deployment_scope,
        applicability.billing_mode,
        applicability.thinking_mode,
        applicability.cache_mode,
        applicability.service_tier,
    )


_MANIFEST_PAYLOAD, OFFICIAL_PRICING_MANIFEST_FILE_SHA256 = (
    _load_manifest_payload()
)

OFFICIAL_PRICING_SOURCE_RECORDS = tuple(
    _source_record(raw)
    for raw in _MANIFEST_PAYLOAD.get("sources", ())
)
_SOURCE_BY_ID = {
    source.source_id: source
    for source in OFFICIAL_PRICING_SOURCE_RECORDS
}
if len(_SOURCE_BY_ID) != len(
    OFFICIAL_PRICING_SOURCE_RECORDS
):
    raise ValueError(
        "official pricing source IDs must be unique"
    )

OFFICIAL_MODEL_PRICING_SNAPSHOTS = tuple(
    _snapshot(raw, _SOURCE_BY_ID)
    for raw in _MANIFEST_PAYLOAD.get("snapshots", ())
)
_snapshot_keys = tuple(
    _snapshot_key(snapshot)
    for snapshot in OFFICIAL_MODEL_PRICING_SNAPSHOTS
)
if len(set(_snapshot_keys)) != len(_snapshot_keys):
    raise ValueError(
        "official pricing snapshot applicability keys "
        "must be unique"
    )

for source in OFFICIAL_PRICING_SOURCE_RECORDS:
    if (
        source.verification_status
        is PricingVerificationStatus.OFFICIAL_PAGE_UNREADABLE
        and any(
            snapshot.provider == source.provider
            and snapshot.source_content_sha256
            == source.source_content_sha256
            for snapshot in OFFICIAL_MODEL_PRICING_SNAPSHOTS
        )
    ):
        raise ValueError(
            "unreadable official source must not create "
            "verified pricing snapshots"
        )


def find_official_pricing_sources(
    *,
    provider: str | None = None,
    source_id: str | None = None,
) -> tuple[OfficialPricingSourceRecord, ...]:
    provider_key = (
        None
        if provider is None
        else _required_text("provider", provider).casefold()
    )
    source_key = (
        None
        if source_id is None
        else _required_text("source_id", source_id)
    )
    return tuple(
        source
        for source in OFFICIAL_PRICING_SOURCE_RECORDS
        if (
            provider_key is None
            or source.provider == provider_key
        )
        and (
            source_key is None
            or source.source_id == source_key
        )
    )


def find_official_model_pricing_snapshots(
    *,
    provider: str | None = None,
    model_id: str | None = None,
    deployment_scope: str | None = None,
    service_tier: str | None = None,
) -> tuple[ModelPricingSnapshot, ...]:
    provider_key = (
        None
        if provider is None
        else _required_text("provider", provider).casefold()
    )
    model_key = (
        None
        if model_id is None
        else _required_text("model_id", model_id)
    )
    deployment_key = (
        None
        if deployment_scope is None
        else _required_text(
            "deployment_scope",
            deployment_scope,
        )
    )
    tier_key = (
        None
        if service_tier is None
        else _required_text("service_tier", service_tier)
    )
    return tuple(
        snapshot
        for snapshot in OFFICIAL_MODEL_PRICING_SNAPSHOTS
        if (
            provider_key is None
            or snapshot.provider.casefold()
            == provider_key
        )
        and (
            model_key is None
            or snapshot.model_id == model_key
        )
        and (
            deployment_key is None
            or snapshot.applicability.deployment_scope
            == deployment_key
        )
        and (
            tier_key is None
            or snapshot.applicability.service_tier
            == tier_key
        )
    )


def official_pricing_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshot_date": _MANIFEST_PAYLOAD.get(
            "snapshot_date"
        ),
        "source_artifact_identity": (
            _MANIFEST_PAYLOAD.get(
                "source_artifact_identity"
            )
        ),
        "manifest_file_sha256": (
            OFFICIAL_PRICING_MANIFEST_FILE_SHA256
        ),
        "sources": [
            source.to_dict()
            for source in OFFICIAL_PRICING_SOURCE_RECORDS
        ],
        "snapshots": [
            snapshot.to_dict()
            for snapshot in OFFICIAL_MODEL_PRICING_SNAPSHOTS
        ],
    }
