"""Model-backed Bottleneck evidence, classification, and source integration.

S3.5 consumes only typed, agent-safe PPA evidence from an accepted parent
candidate.  A model may classify likely bottlenecks and propose evidence-linked
hypotheses, but every classification remains a recorded model inference rather
than an authoritative tool fact.  Missing or insufficient evidence must be
represented as ``unknown``; this module deliberately contains no source-string,
pragma-count, warning-regex, or other heuristic blocking gate.

Correctness, synthesis, Hidden evaluation, and PPA comparison remain delegated
to an explicit qualification adapter.  A valid classification, hypothesis, or
complete-source response is never treated as a qualified candidate by itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Protocol, runtime_checkable

from agrefactor.config import TaskSpec
from agrefactor.models import (
    EffectiveModelConfig,
    ModelProvider,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    ModelCallRole,
    estimate_model_cost,
)
from agrefactor.models.candidate_adapter import (
    CandidateResponseContract,
    CandidateResponseError,
    candidate_response_reason_codes,
)
from agrefactor.prompts.optimization import (
    BOTTLENECK_ALLOWED_SIGNAL_FIELDS,
    BottleneckAnalysisPromptRequest,
    BottleneckOptimizationPromptBuilder,
    BottleneckRewritePromptRequest,
)
from agrefactor.runtime.budget import BudgetManager
from agrefactor.models.call_policy import (
    parameterize_effective_config_call,
)
from agrefactor.runtime.prompt_evidence import (
    record_model_prompt_call,
)

from .execution import (
    CandidateExecutionRequest,
    CandidateExecutionResult,
    CandidateGenerationAbstained,
)
from .policy import BudgetIncrement
from .ppa import PpaEvidence
from .provider import HypothesisGenerationAbstained, HypothesisRequest
from .qualification import CandidateQualificationResult
from .state import (
    CandidateRecord,
    CandidateStatus,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
)
from .structural_model import StructuralModelArtifactWriter


BOTTLENECK_MODEL_SCHEMA_VERSION = 1
BOTTLENECK_RESPONSE_SCHEMA_VERSION = 1
BOTTLENECK_MODEL_CALL_KIND_ANALYSIS = "bottleneck_analysis"
BOTTLENECK_MODEL_CALL_KIND_REWRITE = "bottleneck_rewrite"
_MODEL_CALL_KIND_ANALYSIS = BOTTLENECK_MODEL_CALL_KIND_ANALYSIS
_MODEL_CALL_KIND_REWRITE = BOTTLENECK_MODEL_CALL_KIND_REWRITE
_JSON_FENCE_RE = re.compile(
    r"^```[ \t]*json[ \t]*\r?\n(?P<body>.*)```$",
    re.DOTALL | re.IGNORECASE,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_ALLOWED_SIGNAL_FIELDS = frozenset(BOTTLENECK_ALLOWED_SIGNAL_FIELDS)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BottleneckModelContractError(ValueError):
    """Raised when evidence or a model response violates the S3.5 contract."""


class BottleneckKind(str, Enum):
    INITIATION_INTERVAL = "initiation_interval"
    LOOP_CARRIED_DEPENDENCY = "loop_carried_dependency"
    MEMORY_PORT_CONTENTION = "memory_port_contention"
    CRITICAL_PATH = "critical_path"
    RESOURCE_BOTTLENECK = "resource_bottleneck"
    UNKNOWN_LOOP_BOUND = "unknown_loop_bound"
    DATAFLOW_STALL_RISK = "dataflow_stall_risk"
    LATENCY_STRUCTURE = "latency_structure"
    OBJECTIVE_CONSTRAINT = "objective_constraint"
    UNKNOWN = "unknown"


class BottleneckConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class BottleneckEvidenceView:
    """Agent-safe projection of one accepted candidate's typed PPA evidence."""

    candidate_id: str
    evidence_id: str
    parser_profile: str
    report_format: str
    report_sha256: str
    comparison_context_identity_sha256: str
    latency_cycles_min: int | None
    latency_cycles_max: int
    initiation_interval_min: int | None
    initiation_interval_max: int | None
    target_clock_period_ns: float | None
    achieved_clock_period_ns: float | None
    resources_used: Mapping[str, int | None]
    resources_available: Mapping[str, int | None]
    max_resource_utilization_ratio: float | None
    objective_feasible: bool | None
    constraint_violations: tuple[str, ...] = ()
    parser_warnings: tuple[str, ...] = ()

    schema_version = BOTTLENECK_MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_id(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "evidence_id", _required_id(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "parser_profile", _required_id(self.parser_profile, "parser_profile"))
        if self.report_format not in {"xml", "text"}:
            raise ValueError("report_format must be xml or text")
        _sha256(self.report_sha256, "report_sha256")
        _sha256(
            self.comparison_context_identity_sha256,
            "comparison_context_identity_sha256",
        )
        _optional_non_negative_int(
            self.latency_cycles_min, "latency_cycles_min"
        )
        _non_negative_int(self.latency_cycles_max, "latency_cycles_max")
        if (
            self.latency_cycles_min is not None
            and self.latency_cycles_min > self.latency_cycles_max
        ):
            raise ValueError(
                "latency_cycles_min must not exceed latency_cycles_max"
            )
        _optional_non_negative_int(
            self.initiation_interval_min, "initiation_interval_min"
        )
        _optional_non_negative_int(
            self.initiation_interval_max, "initiation_interval_max"
        )
        if (
            self.initiation_interval_min is not None
            and self.initiation_interval_max is not None
            and self.initiation_interval_min > self.initiation_interval_max
        ):
            raise ValueError(
                "initiation_interval_min must not exceed initiation_interval_max"
            )
        _optional_positive_number(
            self.target_clock_period_ns, "target_clock_period_ns"
        )
        _optional_positive_number(
            self.achieved_clock_period_ns, "achieved_clock_period_ns"
        )
        _optional_non_negative_number(
            self.max_resource_utilization_ratio,
            "max_resource_utilization_ratio",
        )
        object.__setattr__(
            self,
            "resources_used",
            _resource_mapping(self.resources_used, "resources_used"),
        )
        object.__setattr__(
            self,
            "resources_available",
            _resource_mapping(self.resources_available, "resources_available"),
        )
        if self.objective_feasible is not None and not isinstance(
            self.objective_feasible, bool
        ):
            raise TypeError("objective_feasible must be boolean or null")
        object.__setattr__(
            self,
            "constraint_violations",
            _safe_text_tuple(self.constraint_violations, "constraint_violations"),
        )
        object.__setattr__(
            self,
            "parser_warnings",
            _safe_text_tuple(self.parser_warnings, "parser_warnings"),
        )

    @classmethod
    def from_candidate(cls, candidate: CandidateRecord) -> "BottleneckEvidenceView":
        if not isinstance(candidate, CandidateRecord):
            raise TypeError("candidate must be CandidateRecord")
        if candidate.status is not CandidateStatus.ACCEPTED:
            raise BottleneckModelContractError(
                "Bottleneck evidence requires an accepted parent candidate"
            )
        if not candidate.ppa:
            raise BottleneckModelContractError(
                "Bottleneck evidence requires typed parent PPA"
            )
        try:
            ppa = PpaEvidence.from_dict(candidate.ppa)
        except Exception as exc:
            raise BottleneckModelContractError(
                "parent candidate PPA is not valid typed evidence"
            ) from exc
        return cls(
            candidate_id=candidate.candidate_id,
            evidence_id=ppa.evidence_id,
            parser_profile=ppa.parser_profile,
            report_format=ppa.report_format.value,
            report_sha256=ppa.report_sha256,
            comparison_context_identity_sha256=(
                ppa.comparison_context_identity_sha256
            ),
            latency_cycles_min=ppa.latency_cycles_min,
            latency_cycles_max=ppa.latency_cycles_max,
            initiation_interval_min=ppa.initiation_interval_min,
            initiation_interval_max=ppa.initiation_interval_max,
            target_clock_period_ns=ppa.target_clock_period_ns,
            achieved_clock_period_ns=ppa.achieved_clock_period_ns,
            resources_used=ppa.resources_used.to_dict(),
            resources_available=ppa.resources_available.to_dict(),
            max_resource_utilization_ratio=ppa.max_resource_utilization_ratio,
            objective_feasible=ppa.objective_feasible,
            constraint_violations=ppa.constraint_violations,
            parser_warnings=ppa.parser_warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "evidence_id": self.evidence_id,
            "report_identity": {
                "parser_profile": self.parser_profile,
                "report_format": self.report_format,
                "report_sha256": self.report_sha256,
                "comparison_context_identity_sha256": (
                    self.comparison_context_identity_sha256
                ),
            },
            "metrics": {
                "latency_cycles_min": self.latency_cycles_min,
                "latency_cycles_max": self.latency_cycles_max,
                "initiation_interval_min": self.initiation_interval_min,
                "initiation_interval_max": self.initiation_interval_max,
                "target_clock_period_ns": self.target_clock_period_ns,
                "achieved_clock_period_ns": self.achieved_clock_period_ns,
                "max_resource_utilization_ratio": (
                    self.max_resource_utilization_ratio
                ),
                "objective_feasible": self.objective_feasible,
            },
            "resources_used": dict(self.resources_used),
            "resources_available": dict(self.resources_available),
            "constraint_violations": list(self.constraint_violations),
            "parser_warnings": list(self.parser_warnings),
            "raw_report_included": False,
            "hidden_evidence_included": False,
        }

    @property
    def identity_sha256(self) -> str:
        return _mapping_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class BottleneckClassificationDraft:
    kind: BottleneckKind
    claim: str
    confidence: BottleneckConfidence
    supporting_evidence_ids: tuple[str, ...]
    signal_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, BottleneckKind) else BottleneckKind(self.kind)
        confidence = (
            self.confidence
            if isinstance(self.confidence, BottleneckConfidence)
            else BottleneckConfidence(self.confidence)
        )
        claim = _agent_safe_text(self.claim, "claim")
        evidence = _id_tuple(
            self.supporting_evidence_ids,
            "supporting_evidence_ids",
            allow_empty=(kind is BottleneckKind.UNKNOWN),
        )
        signals = _signal_tuple(
            self.signal_fields,
            allow_empty=(kind is BottleneckKind.UNKNOWN),
        )
        if kind is BottleneckKind.UNKNOWN:
            if confidence is not BottleneckConfidence.LOW:
                raise BottleneckModelContractError(
                    "unknown classification confidence must be low"
                )
        elif not evidence:
            raise BottleneckModelContractError(
                "non-unknown classification requires evidence"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "supporting_evidence_ids", evidence)
        object.__setattr__(self, "signal_fields", signals)


@dataclass(frozen=True, slots=True)
class BottleneckHypothesisDraft:
    classification_index: int
    claim: str
    supporting_evidence_ids: tuple[str, ...]
    expected_benefit: Mapping[str, Any]
    risk: HypothesisRisk
    modification_scope: tuple[str, ...]
    verification_plan: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.classification_index, bool) or self.classification_index < 1:
            raise ValueError("classification_index must be positive")
        claim = _agent_safe_text(self.claim, "claim")
        evidence = _id_tuple(
            self.supporting_evidence_ids,
            "supporting_evidence_ids",
            allow_empty=False,
        )
        benefit = _safe_json_object(self.expected_benefit, "expected_benefit")
        if benefit != {"direction": "decrease", "metric": "latency"}:
            raise BottleneckModelContractError(
                "expected_benefit must be exactly latency/decrease"
            )
        risk = self.risk if isinstance(self.risk, HypothesisRisk) else HypothesisRisk(self.risk)
        scope = _safe_text_tuple(self.modification_scope, "modification_scope")
        plan = _safe_text_tuple(self.verification_plan, "verification_plan")
        if plan != ("preflight", "public", "csynth", "hidden"):
            raise BottleneckModelContractError(
                "verification_plan must be preflight, public, csynth, hidden"
            )
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "supporting_evidence_ids", evidence)
        object.__setattr__(self, "expected_benefit", benefit)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "modification_scope", scope)
        object.__setattr__(self, "verification_plan", plan)


@dataclass(frozen=True, slots=True)
class BottleneckClassificationRecord:
    classification_id: str
    parent_candidate_id: str
    kind: BottleneckKind
    claim: str
    confidence: BottleneckConfidence
    supporting_evidence_ids: tuple[str, ...]
    signal_fields: tuple[str, ...]
    model_identity: Mapping[str, Any]
    prompt_identity_sha256: str
    authoritative: bool = False

    schema_version = BOTTLENECK_MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "classification_id",
            _required_id(self.classification_id, "classification_id"),
        )
        object.__setattr__(
            self,
            "parent_candidate_id",
            _required_id(self.parent_candidate_id, "parent_candidate_id"),
        )
        object.__setattr__(
            self,
            "kind",
            self.kind if isinstance(self.kind, BottleneckKind) else BottleneckKind(self.kind),
        )
        object.__setattr__(self, "claim", _agent_safe_text(self.claim, "claim"))
        object.__setattr__(
            self,
            "confidence",
            self.confidence
            if isinstance(self.confidence, BottleneckConfidence)
            else BottleneckConfidence(self.confidence),
        )
        object.__setattr__(
            self,
            "supporting_evidence_ids",
            _id_tuple(
                self.supporting_evidence_ids,
                "supporting_evidence_ids",
                allow_empty=self.kind is BottleneckKind.UNKNOWN,
            ),
        )
        object.__setattr__(
            self,
            "signal_fields",
            _signal_tuple(
                self.signal_fields,
                allow_empty=self.kind is BottleneckKind.UNKNOWN,
            ),
        )
        object.__setattr__(
            self,
            "model_identity",
            _safe_json_object(self.model_identity, "model_identity"),
        )
        _sha256(self.prompt_identity_sha256, "prompt_identity_sha256")
        if (
            self.kind is BottleneckKind.UNKNOWN
            and self.confidence is not BottleneckConfidence.LOW
        ):
            raise ValueError("unknown classification must use low confidence")
        if self.authoritative is not False:
            raise ValueError("model classification must remain non-authoritative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "classification_id": self.classification_id,
            "parent_candidate_id": self.parent_candidate_id,
            "kind": self.kind.value,
            "claim": self.claim,
            "confidence": self.confidence.value,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "signal_fields": list(self.signal_fields),
            "model_identity": dict(self.model_identity),
            "prompt_identity_sha256": self.prompt_identity_sha256,
            "authoritative": False,
            "classification_source": "model_inference",
        }


@dataclass(frozen=True, slots=True)
class BottleneckAnalysisResult:
    classifications: tuple[BottleneckClassificationDraft, ...]
    hypotheses: tuple[BottleneckHypothesisDraft, ...]


class BottleneckAnalysisResponseContract:
    """Strict JSON parser linking classifications, evidence, and hypotheses."""

    def __init__(
        self,
        *,
        max_classifications: int,
        max_hypotheses: int,
        allowed_evidence_ids: Sequence[str],
    ) -> None:
        for name, value in (
            ("max_classifications", max_classifications),
            ("max_hypotheses", max_hypotheses),
        ):
            if isinstance(value, bool) or not 1 <= value <= 3:
                raise ValueError(f"{name} must be between 1 and 3")
        allowed = _id_tuple(
            allowed_evidence_ids,
            "allowed_evidence_ids",
            allow_empty=False,
        )
        self._max_classifications = max_classifications
        self._max_hypotheses = max_hypotheses
        self._allowed_evidence_ids = frozenset(allowed)

    def parse(self, response_text: str) -> BottleneckAnalysisResult:
        payload = _parse_strict_json_object(response_text)
        if set(payload) != {"schema_version", "classifications", "hypotheses"}:
            raise BottleneckModelContractError(
                "response must contain exactly schema_version, classifications, hypotheses"
            )
        if payload["schema_version"] != BOTTLENECK_RESPONSE_SCHEMA_VERSION:
            raise BottleneckModelContractError(
                "unsupported Bottleneck response schema_version"
            )
        raw_classifications = payload["classifications"]
        raw_hypotheses = payload["hypotheses"]
        if not isinstance(raw_classifications, list) or not isinstance(raw_hypotheses, list):
            raise BottleneckModelContractError(
                "classifications and hypotheses must be arrays"
            )
        if not raw_classifications:
            raise BottleneckModelContractError(
                "at least one classification is required; use unknown when evidence is insufficient"
            )
        if len(raw_classifications) > self._max_classifications:
            raise BottleneckModelContractError("too many classifications")
        if len(raw_hypotheses) > self._max_hypotheses:
            raise BottleneckModelContractError("too many hypotheses")

        classifications: list[BottleneckClassificationDraft] = []
        classification_keys = {
            "kind",
            "claim",
            "confidence",
            "supporting_evidence_ids",
            "signal_fields",
        }
        for index, raw in enumerate(raw_classifications, start=1):
            if not isinstance(raw, Mapping) or set(raw) != classification_keys:
                raise BottleneckModelContractError(
                    f"classification {index} must contain exactly the frozen fields"
                )
            try:
                draft = BottleneckClassificationDraft(
                    kind=raw["kind"],
                    claim=raw["claim"],
                    confidence=raw["confidence"],
                    supporting_evidence_ids=tuple(raw["supporting_evidence_ids"]),
                    signal_fields=tuple(raw["signal_fields"]),
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise BottleneckModelContractError(
                    f"classification {index} violates the frozen schema"
                ) from exc
            unknown_ids = set(draft.supporting_evidence_ids) - self._allowed_evidence_ids
            if unknown_ids:
                raise BottleneckModelContractError(
                    f"classification {index} references unknown evidence"
                )
            classifications.append(draft)

        hypotheses: list[BottleneckHypothesisDraft] = []
        hypothesis_keys = {
            "classification_index",
            "claim",
            "supporting_evidence_ids",
            "expected_benefit",
            "risk",
            "modification_scope",
            "verification_plan",
        }
        for index, raw in enumerate(raw_hypotheses, start=1):
            if not isinstance(raw, Mapping) or set(raw) != hypothesis_keys:
                raise BottleneckModelContractError(
                    f"hypothesis {index} must contain exactly the frozen fields"
                )
            try:
                draft = BottleneckHypothesisDraft(
                    classification_index=raw["classification_index"],
                    claim=raw["claim"],
                    supporting_evidence_ids=tuple(raw["supporting_evidence_ids"]),
                    expected_benefit=raw["expected_benefit"],
                    risk=raw["risk"],
                    modification_scope=tuple(raw["modification_scope"]),
                    verification_plan=tuple(raw["verification_plan"]),
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise BottleneckModelContractError(
                    f"hypothesis {index} violates the frozen schema"
                ) from exc
            if draft.classification_index > len(classifications):
                raise BottleneckModelContractError(
                    f"hypothesis {index} references a missing classification"
                )
            classification = classifications[draft.classification_index - 1]
            if classification.kind is BottleneckKind.UNKNOWN:
                raise BottleneckModelContractError(
                    "unknown classification cannot produce an executable hypothesis"
                )
            if not set(draft.supporting_evidence_ids).issubset(
                set(classification.supporting_evidence_ids)
            ):
                raise BottleneckModelContractError(
                    "hypothesis evidence must be a subset of its classification evidence"
                )
            if not set(draft.supporting_evidence_ids).issubset(
                self._allowed_evidence_ids
            ):
                raise BottleneckModelContractError(
                    f"hypothesis {index} references unknown evidence"
                )
            hypotheses.append(draft)
        return BottleneckAnalysisResult(
            classifications=tuple(classifications),
            hypotheses=tuple(hypotheses),
        )


class BottleneckModelArtifactWriter(StructuralModelArtifactWriter):
    """Reuse safe model-call audit and add immutable classification artifacts."""

    @property
    def root(self) -> Path:
        return self.path.parent

    def write_classification(
        self,
        classification: BottleneckClassificationRecord,
    ) -> Path:
        if not isinstance(classification, BottleneckClassificationRecord):
            raise TypeError("classification must be BottleneckClassificationRecord")
        directory = self.root / "bottlenecks"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{classification.classification_id}.json"
        data = _json_bytes(classification.to_dict())
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("classification artifact must be a regular file")
            if path.read_bytes() != data:
                raise FileExistsError(
                    "classification artifact exists with different content"
                )
            return path
        _atomic_write(path, data)
        return path


@dataclass(frozen=True, slots=True)
class BottleneckCandidateGenerationResult:
    candidate_code: str
    source: bytes
    prompt_manifest: Mapping[str, Any]
    response: ModelResponse
    response_contract: CandidateResponseContract

    schema_version = BOTTLENECK_MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        code = _required_text(self.candidate_code, "candidate_code")
        if not isinstance(self.source, bytes) or not self.source:
            raise ValueError("source must be non-empty bytes")
        if self.source != code.encode("utf-8"):
            raise ValueError("source bytes must encode candidate_code")
        if not isinstance(self.response, ModelResponse):
            raise TypeError("response must be ModelResponse")
        if not isinstance(self.response_contract, CandidateResponseContract):
            raise TypeError("response_contract must be CandidateResponseContract")
        object.__setattr__(
            self,
            "prompt_manifest",
            _safe_json_object(self.prompt_manifest, "prompt_manifest"),
        )


class _BottleneckModelEndpoint:
    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        budget: BudgetManager,
        artifacts: BottleneckModelArtifactWriter,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("registry must be ModelRegistry")
        if not isinstance(effective_config, EffectiveModelConfig):
            raise TypeError("effective_config must be EffectiveModelConfig")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be BudgetManager")
        if not isinstance(artifacts, BottleneckModelArtifactWriter):
            raise TypeError("artifacts must be BottleneckModelArtifactWriter")
        self._effective_config = effective_config
        self._model = effective_config.to_model_spec()
        self._provider = registry.get_provider(effective_config.provider_name)
        self._budget = budget
        self._artifacts = artifacts

    @property
    def effective_config(self) -> EffectiveModelConfig:
        return self._effective_config

    @property
    def model_spec(self) -> ModelSpec:
        return self._model

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    def _call(self, *, prompt, call_kind: str) -> ModelResponse:
        response: ModelResponse | None = None
        try:
            call_role = (
                ModelCallRole.BOTTLENECK_DIAGNOSIS
                if call_kind == _MODEL_CALL_KIND_ANALYSIS
                else ModelCallRole.OPTIMIZATION_CANDIDATE_GENERATION
            )
            call_parameters, call_evidence = (
                parameterize_effective_config_call(
                    self._effective_config,
                    call_role,
                )
            )
            record_model_prompt_call(
                template_id=f"bottleneck-{call_kind}",
                template_version=1,
                system_message=None,
                invocation=prompt.manifest,
                provider_call_observed=True,
                metadata=(call_evidence or {}),
            )
            response = self._provider.generate(
                self._model,
                ModelRequest(
                    messages=prompt.messages,
                    parameters=call_parameters,
                    metadata=(
                        {}
                        if call_evidence is None
                        else {"model_call_policy": call_evidence}
                    ),
                ),
            )
            if not isinstance(response, ModelResponse):
                raise TypeError("model provider must return ModelResponse")
            response = _with_estimated_cost(response, self._effective_config)
            self._budget.record_model_usage(response.usage)
            return response
        except Exception as exc:
            self._artifacts.append(
                call_kind=call_kind,
                effective_config=self._effective_config,
                prompt_manifest=prompt.manifest,
                response=response,
                response_valid=False,
                error_code=type(exc).__name__,
                error_reason_codes=candidate_response_reason_codes(exc),
            )
            raise

    def _record_valid(self, *, prompt, call_kind: str, response: ModelResponse) -> None:
        self._artifacts.append(
            call_kind=call_kind,
            effective_config=self._effective_config,
            prompt_manifest=prompt.manifest,
            response=response,
            response_valid=True,
            error_code=None,
        )

    def _record_invalid(
        self,
        *,
        prompt,
        call_kind: str,
        response: ModelResponse,
        error: Exception,
        error_reason_codes: tuple[str, ...] | None = None,
    ) -> None:
        self._artifacts.append(
            call_kind=call_kind,
            effective_config=self._effective_config,
            prompt_manifest=prompt.manifest,
            response=response,
            response_valid=False,
            error_code=type(error).__name__,
            error_reason_codes=(
                candidate_response_reason_codes(error)
                if error_reason_codes is None
                else error_reason_codes
            ),
        )


class BottleneckModelHypothesisProvider(_BottleneckModelEndpoint):
    """Real-model provider for typed Bottleneck classification and hypotheses."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        task: TaskSpec,
        budget: BudgetManager,
        artifacts: BottleneckModelArtifactWriter,
        prompt_builder: BottleneckOptimizationPromptBuilder | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(
            registry=registry,
            effective_config=effective_config,
            budget=budget,
            artifacts=artifacts,
        )
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be TaskSpec")
        self._task = task
        self._builder = prompt_builder or BottleneckOptimizationPromptBuilder()
        if not isinstance(self._builder, BottleneckOptimizationPromptBuilder):
            raise TypeError(
                "prompt_builder must be BottleneckOptimizationPromptBuilder"
            )
        self._name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"bottleneck-model-hypothesis:{effective_config.logical_model_name}"
        )
        self._requests: list[HypothesisRequest] = []
        self._classifications: list[BottleneckClassificationRecord] = []
        self._responses: list[ModelResponse] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return BudgetIncrement(llm_calls=1)

    @property
    def uses_network(self) -> bool:
        return True

    @property
    def requests(self) -> tuple[HypothesisRequest, ...]:
        return tuple(self._requests)

    @property
    def classifications(self) -> tuple[BottleneckClassificationRecord, ...]:
        return tuple(self._classifications)

    @property
    def responses(self) -> tuple[ModelResponse, ...]:
        return tuple(self._responses)

    def propose(self, request: HypothesisRequest) -> tuple[HypothesisRecord, ...]:
        if not isinstance(request, HypothesisRequest):
            raise TypeError("request must be HypothesisRequest")
        if request.level is not OptimizationLevel.BOTTLENECK:
            raise ValueError("S3.5 model provider supports Bottleneck only")
        if not request.parent_source:
            raise ValueError("Bottleneck model request requires parent_source")
        evidence = BottleneckEvidenceView.from_candidate(request.parent_candidate)
        if tuple(request.supporting_evidence_ids) != (evidence.evidence_id,):
            raise BottleneckModelContractError(
                "request evidence IDs must exactly match the typed parent PPA evidence"
            )
        try:
            parent_source = request.parent_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parent_source must be UTF-8") from exc
        prompt = self._builder.build_analysis(
            BottleneckAnalysisPromptRequest(
                task=self._task,
                parent_candidate_id=request.parent_candidate.candidate_id,
                parent_source=parent_source,
                round_number=request.round_number,
                max_classifications=request.max_hypotheses,
                max_hypotheses=request.max_hypotheses,
                evidence=evidence.to_dict(),
                safe_context=request.safe_context,
                family_instruction=self._effective_config.family_instruction,
            )
        )
        self._requests.append(request)
        response = self._call(prompt=prompt, call_kind=_MODEL_CALL_KIND_ANALYSIS)
        self._responses.append(response)
        contract = BottleneckAnalysisResponseContract(
            max_classifications=request.max_hypotheses,
            max_hypotheses=request.max_hypotheses,
            allowed_evidence_ids=(evidence.evidence_id,),
        )
        try:
            parsed = contract.parse(response.text)
            prompt_sha = prompt.manifest["prompt_identity_sha256"]
            classifications = tuple(
                BottleneckClassificationRecord(
                    classification_id=(
                        f"btl-{request.parent_candidate.candidate_id}-"
                        f"r{request.round_number}-{index}"
                    ),
                    parent_candidate_id=request.parent_candidate.candidate_id,
                    kind=draft.kind,
                    claim=draft.claim,
                    confidence=draft.confidence,
                    supporting_evidence_ids=draft.supporting_evidence_ids,
                    signal_fields=draft.signal_fields,
                    model_identity={
                        "provider": self._effective_config.provider_name,
                        "logical_model": self._effective_config.logical_model_name,
                        "model_id": self._effective_config.model_id,
                        "family": self._effective_config.family_profile.name,
                        "network": True,
                        "fixture": False,
                    },
                    prompt_identity_sha256=prompt_sha,
                )
                for index, draft in enumerate(parsed.classifications, start=1)
            )
            hypotheses = tuple(
                HypothesisRecord(
                    hypothesis_id=(
                        f"hyp-bottleneck-r{request.round_number}-{index}"
                    ),
                    level=OptimizationLevel.BOTTLENECK,
                    parent_candidate_id=request.parent_candidate.candidate_id,
                    claim=draft.claim,
                    supporting_evidence_ids=draft.supporting_evidence_ids,
                    expected_benefit=draft.expected_benefit,
                    risk=draft.risk,
                    modification_scope=draft.modification_scope,
                    verification_plan=draft.verification_plan,
                    model_identity={
                        "provider": self._effective_config.provider_name,
                        "logical_model": self._effective_config.logical_model_name,
                        "model_id": self._effective_config.model_id,
                        "family": self._effective_config.family_profile.name,
                        "network": True,
                        "fixture": False,
                        "classification": classifications[
                            draft.classification_index - 1
                        ].to_dict(),
                    },
                    prompt_identity_sha256=prompt_sha,
                )
                for index, draft in enumerate(parsed.hypotheses, start=1)
            )
        except BottleneckModelContractError as exc:
            self._record_invalid(
                prompt=prompt,
                call_kind=_MODEL_CALL_KIND_ANALYSIS,
                response=response,
                error=exc,
                error_reason_codes=("analysis_response_contract_invalid",),
            )
            raise HypothesisGenerationAbstained(
                reason_code="hypothesis_response_contract_abstention",
                error_code=type(exc).__name__,
                detail_codes=("analysis_response_contract_invalid",),
            ) from exc
        for classification in classifications:
            self._artifacts.write_classification(classification)
        self._record_valid(
            prompt=prompt,
            call_kind=_MODEL_CALL_KIND_ANALYSIS,
            response=response,
        )
        self._classifications.extend(classifications)
        return hypotheses


class BottleneckModelCandidateGenerator(_BottleneckModelEndpoint):
    """Generate one complete source implementing a selected Bottleneck hypothesis."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        task: TaskSpec,
        budget: BudgetManager,
        artifacts: BottleneckModelArtifactWriter,
        prompt_builder: BottleneckOptimizationPromptBuilder | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(
            registry=registry,
            effective_config=effective_config,
            budget=budget,
            artifacts=artifacts,
        )
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be TaskSpec")
        self._task = task
        self._builder = prompt_builder or BottleneckOptimizationPromptBuilder()
        if not isinstance(self._builder, BottleneckOptimizationPromptBuilder):
            raise TypeError(
                "prompt_builder must be BottleneckOptimizationPromptBuilder"
            )
        self._name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"bottleneck-model-generator:{effective_config.logical_model_name}"
        )
        self._requests: list[CandidateExecutionRequest] = []
        self._results: list[BottleneckCandidateGenerationResult] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return BudgetIncrement(llm_calls=1)

    @property
    def uses_network(self) -> bool:
        return True

    @property
    def requests(self) -> tuple[CandidateExecutionRequest, ...]:
        return tuple(self._requests)

    @property
    def results(self) -> tuple[BottleneckCandidateGenerationResult, ...]:
        return tuple(self._results)

    def generate(
        self,
        request: CandidateExecutionRequest,
    ) -> BottleneckCandidateGenerationResult:
        if not isinstance(request, CandidateExecutionRequest):
            raise TypeError("request must be CandidateExecutionRequest")
        if request.level is not OptimizationLevel.BOTTLENECK:
            raise ValueError("S3.5 model generator supports Bottleneck only")
        try:
            parent_source = request.parent_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parent_source must be UTF-8") from exc
        prompt = self._builder.build_rewrite(
            BottleneckRewritePromptRequest(
                task=self._task,
                candidate_id=request.candidate_id,
                parent_candidate_id=request.parent_candidate.candidate_id,
                parent_source=parent_source,
                hypothesis=request.hypothesis,
                safe_context={
                    "policy": request.hypothesis.model_identity.get(
                        "policy", "safe-v1"
                    ),
                    "objective": "latency",
                    "round_number": request.round_number,
                },
                family_instruction=self._effective_config.family_instruction,
            )
        )
        self._requests.append(request)
        response = self._call(prompt=prompt, call_kind=_MODEL_CALL_KIND_REWRITE)
        contract = CandidateResponseContract.from_candidate(self._task, parent_source)
        try:
            candidate_code = contract.extract_and_validate(response.text)
            result = BottleneckCandidateGenerationResult(
                candidate_code=candidate_code,
                source=candidate_code.encode("utf-8"),
                prompt_manifest=prompt.manifest,
                response=response,
                response_contract=contract,
            )
        except Exception as exc:
            self._record_invalid(
                prompt=prompt,
                call_kind=_MODEL_CALL_KIND_REWRITE,
                response=response,
                error=exc,
            )
            raise
        self._record_valid(
            prompt=prompt,
            call_kind=_MODEL_CALL_KIND_REWRITE,
            response=response,
        )
        self._results.append(result)
        return result


@runtime_checkable
class BottleneckQualificationAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def uses_vitis(self) -> bool: ...

    def qualify(
        self,
        request: CandidateExecutionRequest,
        source: bytes,
    ) -> CandidateQualificationResult: ...


class BottleneckModelCandidateExecutor:
    """Candidate executor combining Bottleneck generation and qualification."""

    def __init__(
        self,
        *,
        generator: BottleneckModelCandidateGenerator,
        qualifier: BottleneckQualificationAdapter,
        name: str = "bottleneck-model-candidate-executor",
    ) -> None:
        if not isinstance(generator, BottleneckModelCandidateGenerator):
            raise TypeError("generator must be BottleneckModelCandidateGenerator")
        if not hasattr(qualifier, "qualify") or not hasattr(qualifier, "uses_vitis"):
            raise TypeError("qualifier does not satisfy BottleneckQualificationAdapter")
        self._generator = generator
        self._qualifier = qualifier
        self._name = _required_text(name, "name")
        self._requests: list[CandidateExecutionRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._generator.budget_increment

    @property
    def uses_network(self) -> bool:
        return True

    @property
    def uses_vitis(self) -> bool:
        return bool(self._qualifier.uses_vitis)

    @property
    def requests(self) -> tuple[CandidateExecutionRequest, ...]:
        return tuple(self._requests)

    def execute(self, request: CandidateExecutionRequest) -> CandidateExecutionResult:
        if not isinstance(request, CandidateExecutionRequest):
            raise TypeError("request must be CandidateExecutionRequest")
        if request.level is not OptimizationLevel.BOTTLENECK:
            raise ValueError("S3.5 executor supports Bottleneck only")
        self._requests.append(request)
        try:
            generated = self._generator.generate(request)
        except CandidateResponseError as exc:
            raise CandidateGenerationAbstained(
                reason_code="candidate_response_contract_abstention",
                error_code=type(exc).__name__,
                detail_codes=candidate_response_reason_codes(exc),
            ) from exc
        qualification = self._qualifier.qualify(request, generated.source)
        if not isinstance(qualification, CandidateQualificationResult):
            raise TypeError("qualifier must return CandidateQualificationResult")
        return CandidateExecutionResult(
            source=generated.source,
            qualification=qualification,
        )


def _parse_strict_json_object(text: str) -> dict[str, Any]:
    cleaned = _required_text(text, "model response")
    fence = _JSON_FENCE_RE.fullmatch(cleaned)
    if fence is not None:
        cleaned = fence.group("body").strip()
    elif "```" in cleaned:
        raise BottleneckModelContractError(
            "JSON response must be raw JSON or exactly one json fence"
        )
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise BottleneckModelContractError("model response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BottleneckModelContractError("model response must be a JSON object")
    return value


def _with_estimated_cost(
    response: ModelResponse,
    config: EffectiveModelConfig,
) -> ModelResponse:
    snapshot = config.pricing_snapshot
    if snapshot is None:
        return response
    estimated = estimate_model_cost(
        snapshot,
        response.usage,
        allow_approximate=config.allow_approximate_cost,
    )
    legacy_cost = response.usage.cost_usd
    if legacy_cost is not None and estimated.currency not in (None, "USD"):
        raise ValueError("non-USD estimate cannot be combined with cost_usd")
    if estimated.amount is not None and estimated.currency == "USD":
        if legacy_cost is not None and abs(
            Decimal(str(legacy_cost)) - estimated.amount
        ) > Decimal("1e-12"):
            raise ValueError("provider cost_usd conflicts with pricing snapshot")
        legacy_cost = float(estimated.amount)
    usage = replace(
        response.usage,
        cost_usd=legacy_cost,
        estimated_cost=estimated,
    )
    metadata = dict(response.metadata)
    metadata.update(
        {
            "pricing_estimation_attempted": True,
            "pricing_estimation_quality": estimated.quality.value,
            "pricing_snapshot_sha256": snapshot.pricing_snapshot_sha256,
            "pricing_currency": estimated.currency,
            "pricing_amount_available": estimated.amount is not None,
        }
    )
    return replace(response, usage=usage, metadata=metadata)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in cleaned:
        raise ValueError(f"{name} must not contain NUL")
    return cleaned


def _agent_safe_text(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    lowered = cleaned.lower()
    forbidden = (
        "hidden testbench",
        "hidden diagnostic",
        "hidden report",
        "operator_full",
        "operator-full",
        "private testbench",
        "api key",
        "authorization bearer",
    )
    if any(item in lowered for item in forbidden):
        raise BottleneckModelContractError(f"{name} contains agent-unsafe text")
    return cleaned


def _required_id(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if _SAFE_ID_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} contains unsupported characters")
    return cleaned


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _id_tuple(value: Any, name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(_required_id(item, name) for item in value)
    if not allow_empty and not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} values must be unique")
    return result


def _safe_text_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(_agent_safe_text(item, name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} values must be unique")
    return result


def _signal_tuple(value: Any, *, allow_empty: bool) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("signal_fields must be a sequence")
    result = tuple(_required_text(item, "signal_fields") for item in value)
    if not allow_empty and not result:
        raise ValueError("signal_fields must not be empty")
    if len(result) != len(set(result)):
        raise ValueError("signal_fields values must be unique")
    unknown = set(result) - _ALLOWED_SIGNAL_FIELDS
    if unknown:
        raise BottleneckModelContractError(
            "unsupported signal_fields: " + ", ".join(sorted(unknown))
        )
    return result


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _optional_non_negative_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value, name)


def _optional_positive_number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a positive finite number or null")
    return numeric


def _optional_non_negative_number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a non-negative finite number or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a non-negative finite number or null")
    return numeric


def _resource_mapping(value: Mapping[str, Any], name: str) -> dict[str, int | None]:
    allowed = {"bram_18k", "dsp", "ff", "lut", "uram"}
    if not isinstance(value, Mapping) or set(value) != allowed:
        raise ValueError(f"{name} must contain the five frozen resource fields")
    result: dict[str, int | None] = {}
    for key in sorted(allowed):
        item = value[key]
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise ValueError(f"{name}.{key} must be non-negative integer or null")
        result[key] = item
    return result


def _safe_json_object(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        copied = json.loads(
            json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite JSON data") from exc
    _reject_agent_unsafe(copied, name)
    return copied


def _reject_agent_unsafe(value: Any, path: str) -> None:
    forbidden = {
        "hidden",
        "hidden_diagnostic",
        "hidden_report",
        "hidden_testbench",
        "operator_full",
        "private_testbench",
        "secret",
        "api_key",
        "access_token",
        "password",
        "authorization",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized == "hidden_test_source_isolation" and item == "verified":
                continue
            if normalized == "hidden_evidence_included" and item is False:
                continue
            if normalized in forbidden or normalized.startswith("hidden_"):
                raise ValueError(f"{path} contains agent-unsafe key: {key}")
            _reject_agent_unsafe(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _reject_agent_unsafe(item, f"{path}[{index}]")
    elif isinstance(value, str):
        _agent_safe_text(value, path)


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    if path.parent.is_symlink():
        raise ValueError("artifact parent must not be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        os.unlink(temporary)
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
