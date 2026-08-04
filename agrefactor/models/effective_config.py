"""Immutable effective model configuration resolved before provider execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from typing import Any

from .base import ModelSpec
from .family import ModelFamilyProfile
from .pricing import ModelPricingSnapshot
from .call_policy import (
    ModelCallRole,
    normalize_requested_reasoning_effort,
    parameterize_model_call,
)


_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|bearer|credential|"
    r"password|refresh[_-]?token|secret|access[_-]?token)",
    re.IGNORECASE,
)


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


def _copy_json_object(
    name: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must contain finite JSON-serializable data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _SECRET_KEY_RE.search(key_text):
                raise ValueError(
                    "effective model parameters must not contain "
                    f"credential-like key: {child_path}"
                )
            _reject_secret_keys(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {
                str(key): _freeze_json(child)
                for key, child in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(child)
            for key, child in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class EffectiveModelConfig:
    """One resolved, provider-ready model configuration without credentials."""

    logical_model_name: str
    provider_name: str
    model_id: str
    family_profile: ModelFamilyProfile
    effective_parameters: Mapping[str, Any] = field(
        default_factory=dict
    )
    requested_family_name: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    pricing_snapshot: ModelPricingSnapshot | None = None
    allow_approximate_cost: bool = False
    requested_reasoning_effort: str = "auto"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "logical_model_name",
            _clean_required(
                "logical_model_name",
                self.logical_model_name,
            ),
        )
        object.__setattr__(
            self,
            "provider_name",
            _clean_required(
                "provider_name",
                self.provider_name,
            ),
        )
        object.__setattr__(
            self,
            "model_id",
            _clean_required("model_id", self.model_id),
        )
        object.__setattr__(
            self,
            "requested_family_name",
            _clean_optional(
                "requested_family_name",
                self.requested_family_name,
            ),
        )
        object.__setattr__(
            self,
            "base_url",
            _clean_optional("base_url", self.base_url),
        )
        object.__setattr__(
            self,
            "api_key_env",
            _clean_optional(
                "api_key_env",
                self.api_key_env,
            ),
        )

        if not isinstance(
            self.family_profile,
            ModelFamilyProfile,
        ):
            raise TypeError(
                "family_profile must be a ModelFamilyProfile"
            )

        parameters = _copy_json_object(
            "effective_parameters",
            self.effective_parameters,
        )
        _reject_secret_keys(
            parameters,
            "effective_parameters",
        )
        object.__setattr__(
            self,
            "effective_parameters",
            _freeze_json(parameters),
        )

        snapshot = self.pricing_snapshot
        if snapshot is not None:
            if not isinstance(
                snapshot,
                ModelPricingSnapshot,
            ):
                raise TypeError(
                    "pricing_snapshot must be a "
                    "ModelPricingSnapshot or None"
                )
            if snapshot.model_id != self.model_id:
                raise ValueError(
                    "pricing_snapshot.model_id must match model_id"
                )

        if not isinstance(
            self.allow_approximate_cost,
            bool,
        ):
            raise TypeError(
                "allow_approximate_cost must be boolean"
            )
        object.__setattr__(
            self,
            "requested_reasoning_effort",
            normalize_requested_reasoning_effort(
                self.requested_reasoning_effort
            ),
        )

    @property
    def family_profile_name(self) -> str:
        return self.family_profile.name

    @property
    def family_instruction(self) -> str | None:
        return self.family_profile.render_instruction()

    @property
    def pricing_snapshot_sha256(self) -> str | None:
        if self.pricing_snapshot is None:
            return None
        return self.pricing_snapshot.pricing_snapshot_sha256

    @property
    def parameters(self) -> dict[str, Any]:
        return _thaw_json(self.effective_parameters)

    def parameterize_call(
        self,
        role: str | ModelCallRole,
    ):
        return parameterize_model_call(
            base_parameters=self.parameters,
            model_id=self.model_id,
            provider=self.provider_name,
            family_profile=self.family_profile,
            requested_reasoning_effort=(
                self.requested_reasoning_effort
            ),
            role=role,
        )

    def parameters_for_call(
        self,
        role: str | ModelCallRole,
    ) -> dict[str, Any]:
        return self.parameterize_call(role)[0]

    def call_policy_evidence(
        self,
        role: str | ModelCallRole,
    ) -> dict[str, Any]:
        return self.parameterize_call(role)[1].to_dict()

    def to_model_spec(self) -> ModelSpec:
        """Create the transport-facing spec after defaults are resolved."""

        return ModelSpec(
            name=self.logical_model_name,
            provider=self.provider_name,
            model=self.model_id,
            family=self.requested_family_name,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            default_parameters={},
        )

    def to_manifest(self) -> dict[str, Any]:
        snapshot = self.pricing_snapshot
        pricing_identity = (
            None
            if snapshot is None
            else {
                "pricing_snapshot_sha256": (
                    snapshot.pricing_snapshot_sha256
                ),
                "provider": snapshot.provider,
                "model_id": snapshot.model_id,
                "model_version": snapshot.model_version,
                "currency": snapshot.currency,
                "verification_status": (
                    snapshot.verification_status.value
                ),
            }
        )
        return {
            "logical_model_name": self.logical_model_name,
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "requested_family_name": (
                self.requested_family_name
            ),
            "family_profile_name": (
                self.family_profile_name
            ),
            "family_profile": (
                self.family_profile.to_manifest()
            ),
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "effective_parameters": self.parameters,
            "pricing_snapshot": pricing_identity,
            "allow_approximate_cost": (
                self.allow_approximate_cost
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_manifest()
