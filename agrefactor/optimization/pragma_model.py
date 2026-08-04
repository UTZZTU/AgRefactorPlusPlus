"""Model-backed Pragma applicability, typed action, and source integration.

S3.6 is the final optimization level. It consumes only typed, agent-safe PPA
projection from an accepted parent candidate and asks a model for a narrow,
evidence-linked pragma action. Every action is a proposal rather than an
authoritative statement that a target exists or that a directive is legal or
beneficial. Source-string scans, pragma counts, warning regexes, and similar
heuristics are deliberately not used as applicability or correctness gates.

Correctness, synthesis, Hidden evaluation, and PPA comparison remain delegated
to an explicit qualification adapter. A valid action, hypothesis, or complete
source response is never treated as a qualified or improved candidate itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
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
    PRAGMA_ALLOWED_SIGNAL_FIELDS,
    PragmaAnalysisPromptRequest,
    PragmaOptimizationPromptBuilder,
    PragmaRewritePromptRequest,
)
from agrefactor.runtime.budget import BudgetManager
from agrefactor.models.call_policy import (
    parameterize_effective_config_call,
)
from agrefactor.runtime.prompt_evidence import (
    record_model_prompt_call,
)

from .bottleneck_model import BottleneckEvidenceView
from .execution import (
    CandidateExecutionRequest,
    CandidateExecutionResult,
    CandidateGenerationAbstained,
)
from .policy import BudgetIncrement
from .provider import HypothesisGenerationAbstained, HypothesisRequest
from .qualification import CandidateQualificationResult
from .state import HypothesisRecord, HypothesisRisk, OptimizationLevel
from .structural_model import StructuralModelArtifactWriter


PRAGMA_MODEL_SCHEMA_VERSION = 1
PRAGMA_RESPONSE_SCHEMA_VERSION = 1
PRAGMA_MODEL_CALL_KIND_ANALYSIS = "pragma_analysis"
PRAGMA_MODEL_CALL_KIND_REWRITE = "pragma_rewrite"
_MODEL_CALL_KIND_ANALYSIS = PRAGMA_MODEL_CALL_KIND_ANALYSIS
_MODEL_CALL_KIND_REWRITE = PRAGMA_MODEL_CALL_KIND_REWRITE
_JSON_FENCE_RE = re.compile(
    r"^```[ \t]*json[ \t]*\r?\n(?P<body>.*)```$",
    re.DOTALL | re.IGNORECASE,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_ALLOWED_SIGNAL_FIELDS = frozenset(PRAGMA_ALLOWED_SIGNAL_FIELDS)

_BIND_STORAGE_TYPES = {
    "ram_1p",
    "ram_1wnr",
    "ram_2p",
    "ram_s2p",
    "ram_t2p",
    "rom_1p",
    "rom_2p",
    "rom_np",
    "fifo",
}
_BIND_STORAGE_IMPLS = {
    "auto",
    "bram",
    "bram_ecc",
    "lutram",
    "uram",
    "uram_ecc",
    "memory",
    "srl",
}
_BIND_OPS = {
    "add",
    "sub",
    "mul",
    "div",
    "rem",
    "fadd",
    "fsub",
    "fmul",
    "fdiv",
    "dadd",
    "dsub",
    "dmul",
    "ddiv",
}
_BIND_OP_IMPLS = {
    "fabric",
    "dsp",
    "maxdsp",
    "fulldsp",
    "meddsp",
    "primitivedsp",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PragmaModelContractError(ValueError):
    """Raised when evidence or a model response violates the S3.6 contract."""


class PragmaKind(str, Enum):
    PIPELINE = "pipeline"
    UNROLL = "unroll"
    ARRAY_PARTITION = "array_partition"
    DATAFLOW = "dataflow"
    INLINE = "inline"
    BIND_STORAGE = "bind_storage"
    BIND_OP = "bind_op"
    UNKNOWN = "unknown"


class PragmaTargetKind(str, Enum):
    FUNCTION = "function"
    LOOP = "loop"
    ARRAY = "array"
    OPERATION = "operation"
    REGION = "region"
    UNKNOWN = "unknown"


class PragmaConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class PragmaActionDraft:
    kind: PragmaKind
    target_kind: PragmaTargetKind
    target_ref: str | None
    parameters: Mapping[str, Any]
    claim: str
    confidence: PragmaConfidence
    supporting_evidence_ids: tuple[str, ...]
    signal_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, PragmaKind) else PragmaKind(self.kind)
        target_kind = (
            self.target_kind
            if isinstance(self.target_kind, PragmaTargetKind)
            else PragmaTargetKind(self.target_kind)
        )
        confidence = (
            self.confidence
            if isinstance(self.confidence, PragmaConfidence)
            else PragmaConfidence(self.confidence)
        )
        claim = _agent_safe_text(self.claim, "claim")
        if kind is PragmaKind.UNKNOWN:
            if target_kind is not PragmaTargetKind.UNKNOWN:
                raise PragmaModelContractError(
                    "unknown action target_kind must be unknown"
                )
            if self.target_ref is not None:
                raise PragmaModelContractError("unknown action target_ref must be null")
            parameters = _pragma_parameters(kind, self.parameters)
            evidence = _id_tuple(
                self.supporting_evidence_ids,
                "supporting_evidence_ids",
                allow_empty=True,
            )
            signals = _signal_tuple(self.signal_fields, allow_empty=True)
            if evidence or signals:
                raise PragmaModelContractError(
                    "unknown action must not claim evidence or signal fields"
                )
            if confidence is not PragmaConfidence.LOW:
                raise PragmaModelContractError(
                    "unknown action confidence must be low"
                )
            target_ref = None
        else:
            if target_kind is PragmaTargetKind.UNKNOWN:
                raise PragmaModelContractError(
                    "non-unknown action target_kind must be specific"
                )
            target_ref = _agent_safe_text(self.target_ref, "target_ref")
            _validate_target_compatibility(kind, target_kind)
            parameters = _pragma_parameters(kind, self.parameters)
            evidence = _id_tuple(
                self.supporting_evidence_ids,
                "supporting_evidence_ids",
                allow_empty=False,
            )
            signals = _signal_tuple(self.signal_fields, allow_empty=False)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "target_kind", target_kind)
        object.__setattr__(self, "target_ref", target_ref)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "supporting_evidence_ids", evidence)
        object.__setattr__(self, "signal_fields", signals)


@dataclass(frozen=True, slots=True)
class PragmaHypothesisDraft:
    action_index: int
    claim: str
    supporting_evidence_ids: tuple[str, ...]
    expected_benefit: Mapping[str, Any]
    risk: HypothesisRisk
    modification_scope: tuple[str, ...]
    verification_plan: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.action_index, bool) or self.action_index < 1:
            raise ValueError("action_index must be positive")
        claim = _agent_safe_text(self.claim, "claim")
        evidence = _id_tuple(
            self.supporting_evidence_ids,
            "supporting_evidence_ids",
            allow_empty=False,
        )
        benefit = _safe_json_object(self.expected_benefit, "expected_benefit")
        if benefit != {"direction": "decrease", "metric": "latency"}:
            raise PragmaModelContractError(
                "expected_benefit must be exactly latency/decrease"
            )
        risk = self.risk if isinstance(self.risk, HypothesisRisk) else HypothesisRisk(self.risk)
        scope = _safe_text_tuple(self.modification_scope, "modification_scope")
        if not scope:
            raise ValueError("modification_scope must not be empty")
        plan = _safe_text_tuple(self.verification_plan, "verification_plan")
        if plan != ("preflight", "public", "csynth", "hidden"):
            raise PragmaModelContractError(
                "verification_plan must be preflight, public, csynth, hidden"
            )
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "supporting_evidence_ids", evidence)
        object.__setattr__(self, "expected_benefit", benefit)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "modification_scope", scope)
        object.__setattr__(self, "verification_plan", plan)


@dataclass(frozen=True, slots=True)
class PragmaActionRecord:
    action_id: str
    parent_candidate_id: str
    kind: PragmaKind
    target_kind: PragmaTargetKind
    target_ref: str | None
    parameters: Mapping[str, Any]
    claim: str
    confidence: PragmaConfidence
    supporting_evidence_ids: tuple[str, ...]
    signal_fields: tuple[str, ...]
    model_identity: Mapping[str, Any]
    prompt_identity_sha256: str
    authoritative: bool = False

    schema_version = PRAGMA_MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _required_id(self.action_id, "action_id"))
        object.__setattr__(
            self,
            "parent_candidate_id",
            _required_id(self.parent_candidate_id, "parent_candidate_id"),
        )
        draft = PragmaActionDraft(
            kind=self.kind,
            target_kind=self.target_kind,
            target_ref=self.target_ref,
            parameters=self.parameters,
            claim=self.claim,
            confidence=self.confidence,
            supporting_evidence_ids=self.supporting_evidence_ids,
            signal_fields=self.signal_fields,
        )
        object.__setattr__(self, "kind", draft.kind)
        object.__setattr__(self, "target_kind", draft.target_kind)
        object.__setattr__(self, "target_ref", draft.target_ref)
        object.__setattr__(self, "parameters", draft.parameters)
        object.__setattr__(self, "claim", draft.claim)
        object.__setattr__(self, "confidence", draft.confidence)
        object.__setattr__(
            self, "supporting_evidence_ids", draft.supporting_evidence_ids
        )
        object.__setattr__(self, "signal_fields", draft.signal_fields)
        object.__setattr__(
            self,
            "model_identity",
            _safe_json_object(self.model_identity, "model_identity"),
        )
        _sha256(self.prompt_identity_sha256, "prompt_identity_sha256")
        if self.authoritative is not False:
            raise ValueError("model pragma action must remain non-authoritative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "parent_candidate_id": self.parent_candidate_id,
            "kind": self.kind.value,
            "target_kind": self.target_kind.value,
            "target_ref": self.target_ref,
            "parameters": dict(self.parameters),
            "claim": self.claim,
            "confidence": self.confidence.value,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "signal_fields": list(self.signal_fields),
            "model_identity": dict(self.model_identity),
            "prompt_identity_sha256": self.prompt_identity_sha256,
            "authoritative": False,
            "action_source": "model_proposal",
        }


@dataclass(frozen=True, slots=True)
class PragmaAnalysisResult:
    actions: tuple[PragmaActionDraft, ...]
    hypotheses: tuple[PragmaHypothesisDraft, ...]


class PragmaAnalysisResponseContract:
    """Strict JSON parser linking typed actions, evidence, and hypotheses."""

    def __init__(
        self,
        *,
        max_actions: int,
        max_hypotheses: int,
        allowed_evidence_ids: Sequence[str],
    ) -> None:
        for name, value in (
            ("max_actions", max_actions),
            ("max_hypotheses", max_hypotheses),
        ):
            if isinstance(value, bool) or not 1 <= value <= 3:
                raise ValueError(f"{name} must be between 1 and 3")
        allowed = _id_tuple(
            allowed_evidence_ids,
            "allowed_evidence_ids",
            allow_empty=False,
        )
        self._max_actions = max_actions
        self._max_hypotheses = max_hypotheses
        self._allowed_evidence_ids = frozenset(allowed)

    def parse(self, response_text: str) -> PragmaAnalysisResult:
        payload = _parse_strict_json_object(response_text)
        if set(payload) != {"schema_version", "actions", "hypotheses"}:
            raise PragmaModelContractError(
                "response must contain exactly schema_version, actions, hypotheses"
            )
        if payload["schema_version"] != PRAGMA_RESPONSE_SCHEMA_VERSION:
            raise PragmaModelContractError(
                "unsupported Pragma response schema_version"
            )
        raw_actions = payload["actions"]
        raw_hypotheses = payload["hypotheses"]
        if not isinstance(raw_actions, list) or not isinstance(raw_hypotheses, list):
            raise PragmaModelContractError("actions and hypotheses must be arrays")
        if not 1 <= len(raw_actions) <= self._max_actions:
            raise PragmaModelContractError("actions count is outside the frozen bound")
        if len(raw_hypotheses) > self._max_hypotheses:
            raise PragmaModelContractError(
                "hypotheses count is outside the frozen bound"
            )
        actions: list[PragmaActionDraft] = []
        for index, item in enumerate(raw_actions, start=1):
            if not isinstance(item, Mapping) or set(item) != {
                "kind",
                "target_kind",
                "target_ref",
                "parameters",
                "claim",
                "confidence",
                "supporting_evidence_ids",
                "signal_fields",
            }:
                raise PragmaModelContractError(
                    f"action {index} must contain exactly the frozen keys"
                )
            try:
                action = PragmaActionDraft(
                    kind=item["kind"],
                    target_kind=item["target_kind"],
                    target_ref=item["target_ref"],
                    parameters=item["parameters"],
                    claim=item["claim"],
                    confidence=item["confidence"],
                    supporting_evidence_ids=tuple(item["supporting_evidence_ids"]),
                    signal_fields=tuple(item["signal_fields"]),
                )
            except Exception as exc:
                raise PragmaModelContractError(
                    f"action {index} violates the frozen schema"
                ) from exc
            if not set(action.supporting_evidence_ids) <= self._allowed_evidence_ids:
                raise PragmaModelContractError(
                    f"action {index} cites unsupported evidence"
                )
            actions.append(action)
        hypotheses: list[PragmaHypothesisDraft] = []
        for index, item in enumerate(raw_hypotheses, start=1):
            if not isinstance(item, Mapping) or set(item) != {
                "action_index",
                "claim",
                "supporting_evidence_ids",
                "expected_benefit",
                "risk",
                "modification_scope",
                "verification_plan",
            }:
                raise PragmaModelContractError(
                    f"hypothesis {index} must contain exactly the frozen keys"
                )
            try:
                hypothesis = PragmaHypothesisDraft(
                    action_index=item["action_index"],
                    claim=item["claim"],
                    supporting_evidence_ids=tuple(item["supporting_evidence_ids"]),
                    expected_benefit=item["expected_benefit"],
                    risk=item["risk"],
                    modification_scope=tuple(item["modification_scope"]),
                    verification_plan=tuple(item["verification_plan"]),
                )
            except Exception as exc:
                raise PragmaModelContractError(
                    f"hypothesis {index} violates the frozen schema"
                ) from exc
            if hypothesis.action_index > len(actions):
                raise PragmaModelContractError(
                    f"hypothesis {index} references a missing action"
                )
            action = actions[hypothesis.action_index - 1]
            if action.kind is PragmaKind.UNKNOWN:
                raise PragmaModelContractError(
                    f"hypothesis {index} references an unknown action"
                )
            if not set(hypothesis.supporting_evidence_ids) <= set(
                action.supporting_evidence_ids
            ):
                raise PragmaModelContractError(
                    f"hypothesis {index} evidence must be a subset of its action"
                )
            hypotheses.append(hypothesis)
        return PragmaAnalysisResult(tuple(actions), tuple(hypotheses))


class PragmaModelArtifactWriter(StructuralModelArtifactWriter):
    """Reuse safe model-call audit and add immutable pragma action artifacts."""

    @property
    def root(self) -> Path:
        return self.path.parent

    def write_action(self, action: PragmaActionRecord) -> Path:
        if not isinstance(action, PragmaActionRecord):
            raise TypeError("action must be PragmaActionRecord")
        directory = self.root / "pragma_actions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{action.action_id}.json"
        data = _json_bytes(action.to_dict())
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("pragma action artifact must be a regular file")
            if path.read_bytes() != data:
                raise FileExistsError(
                    "pragma action artifact exists with different content"
                )
            return path
        _atomic_write(path, data)
        return path


@dataclass(frozen=True, slots=True)
class PragmaCandidateGenerationResult:
    candidate_code: str
    source: bytes
    prompt_manifest: Mapping[str, Any]
    response: ModelResponse
    response_contract: CandidateResponseContract

    schema_version = PRAGMA_MODEL_SCHEMA_VERSION

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


class _PragmaModelEndpoint:
    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        budget: BudgetManager,
        artifacts: PragmaModelArtifactWriter,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("registry must be ModelRegistry")
        if not isinstance(effective_config, EffectiveModelConfig):
            raise TypeError("effective_config must be EffectiveModelConfig")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be BudgetManager")
        if not isinstance(artifacts, PragmaModelArtifactWriter):
            raise TypeError("artifacts must be PragmaModelArtifactWriter")
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
                ModelCallRole.OPTIMIZATION_ACTION_SELECTION
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
                template_id=f"pragma-{call_kind}",
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


class PragmaModelHypothesisProvider(_PragmaModelEndpoint):
    """Real-model provider for typed Pragma actions and hypotheses."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        task: TaskSpec,
        budget: BudgetManager,
        artifacts: PragmaModelArtifactWriter,
        prompt_builder: PragmaOptimizationPromptBuilder | None = None,
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
        self._builder = prompt_builder or PragmaOptimizationPromptBuilder()
        if not isinstance(self._builder, PragmaOptimizationPromptBuilder):
            raise TypeError("prompt_builder must be PragmaOptimizationPromptBuilder")
        self._name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"pragma-model-hypothesis:{effective_config.logical_model_name}"
        )
        self._requests: list[HypothesisRequest] = []
        self._actions: list[PragmaActionRecord] = []
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
    def actions(self) -> tuple[PragmaActionRecord, ...]:
        return tuple(self._actions)

    @property
    def responses(self) -> tuple[ModelResponse, ...]:
        return tuple(self._responses)

    def propose(self, request: HypothesisRequest) -> tuple[HypothesisRecord, ...]:
        if not isinstance(request, HypothesisRequest):
            raise TypeError("request must be HypothesisRequest")
        if request.level is not OptimizationLevel.PRAGMA:
            raise ValueError("S3.6 model provider supports Pragma only")
        if not request.parent_source:
            raise ValueError("Pragma model request requires parent_source")
        evidence = BottleneckEvidenceView.from_candidate(request.parent_candidate)
        if tuple(request.supporting_evidence_ids) != (evidence.evidence_id,):
            raise PragmaModelContractError(
                "request evidence IDs must exactly match the typed parent PPA evidence"
            )
        try:
            parent_source = request.parent_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parent_source must be UTF-8") from exc
        prompt = self._builder.build_analysis(
            PragmaAnalysisPromptRequest(
                task=self._task,
                parent_candidate_id=request.parent_candidate.candidate_id,
                parent_source=parent_source,
                round_number=request.round_number,
                max_actions=request.max_hypotheses,
                max_hypotheses=request.max_hypotheses,
                evidence=evidence.to_dict(),
                safe_context=request.safe_context,
                family_instruction=self._effective_config.family_instruction,
            )
        )
        self._requests.append(request)
        response = self._call(prompt=prompt, call_kind=_MODEL_CALL_KIND_ANALYSIS)
        self._responses.append(response)
        contract = PragmaAnalysisResponseContract(
            max_actions=request.max_hypotheses,
            max_hypotheses=request.max_hypotheses,
            allowed_evidence_ids=(evidence.evidence_id,),
        )
        try:
            parsed = contract.parse(response.text)
            prompt_sha = prompt.manifest["prompt_identity_sha256"]
            actions = tuple(
                PragmaActionRecord(
                    action_id=(
                        f"pragma-{request.parent_candidate.candidate_id}-"
                        f"r{request.round_number}-{index}"
                    ),
                    parent_candidate_id=request.parent_candidate.candidate_id,
                    kind=draft.kind,
                    target_kind=draft.target_kind,
                    target_ref=draft.target_ref,
                    parameters=draft.parameters,
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
                for index, draft in enumerate(parsed.actions, start=1)
            )
            hypotheses = tuple(
                HypothesisRecord(
                    hypothesis_id=f"hyp-pragma-r{request.round_number}-{index}",
                    level=OptimizationLevel.PRAGMA,
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
                        "pragma_action": actions[draft.action_index - 1].to_dict(),
                    },
                    prompt_identity_sha256=prompt_sha,
                )
                for index, draft in enumerate(parsed.hypotheses, start=1)
            )
        except PragmaModelContractError as exc:
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
        for action in actions:
            self._artifacts.write_action(action)
        self._record_valid(
            prompt=prompt,
            call_kind=_MODEL_CALL_KIND_ANALYSIS,
            response=response,
        )
        self._actions.extend(actions)
        return hypotheses


class PragmaModelCandidateGenerator(_PragmaModelEndpoint):
    """Generate one complete source implementing a selected Pragma hypothesis."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        task: TaskSpec,
        budget: BudgetManager,
        artifacts: PragmaModelArtifactWriter,
        prompt_builder: PragmaOptimizationPromptBuilder | None = None,
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
        self._builder = prompt_builder or PragmaOptimizationPromptBuilder()
        if not isinstance(self._builder, PragmaOptimizationPromptBuilder):
            raise TypeError("prompt_builder must be PragmaOptimizationPromptBuilder")
        self._name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"pragma-model-generator:{effective_config.logical_model_name}"
        )
        self._requests: list[CandidateExecutionRequest] = []
        self._results: list[PragmaCandidateGenerationResult] = []

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
    def results(self) -> tuple[PragmaCandidateGenerationResult, ...]:
        return tuple(self._results)

    def generate(self, request: CandidateExecutionRequest) -> PragmaCandidateGenerationResult:
        if not isinstance(request, CandidateExecutionRequest):
            raise TypeError("request must be CandidateExecutionRequest")
        if request.level is not OptimizationLevel.PRAGMA:
            raise ValueError("S3.6 model generator supports Pragma only")
        try:
            parent_source = request.parent_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parent_source must be UTF-8") from exc
        prompt = self._builder.build_rewrite(
            PragmaRewritePromptRequest(
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
            result = PragmaCandidateGenerationResult(
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
class PragmaQualificationAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def uses_vitis(self) -> bool: ...

    def qualify(
        self,
        request: CandidateExecutionRequest,
        source: bytes,
    ) -> CandidateQualificationResult: ...


class PragmaModelCandidateExecutor:
    """Candidate executor combining Pragma generation and qualification."""

    def __init__(
        self,
        *,
        generator: PragmaModelCandidateGenerator,
        qualifier: PragmaQualificationAdapter,
        name: str = "pragma-model-candidate-executor",
    ) -> None:
        if not isinstance(generator, PragmaModelCandidateGenerator):
            raise TypeError("generator must be PragmaModelCandidateGenerator")
        if not hasattr(qualifier, "qualify") or not hasattr(qualifier, "uses_vitis"):
            raise TypeError("qualifier does not satisfy PragmaQualificationAdapter")
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
        if request.level is not OptimizationLevel.PRAGMA:
            raise ValueError("S3.6 executor supports Pragma only")
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


def _validate_target_compatibility(kind: PragmaKind, target: PragmaTargetKind) -> None:
    allowed = {
        PragmaKind.PIPELINE: {PragmaTargetKind.LOOP, PragmaTargetKind.FUNCTION},
        PragmaKind.UNROLL: {PragmaTargetKind.LOOP},
        PragmaKind.ARRAY_PARTITION: {PragmaTargetKind.ARRAY},
        PragmaKind.DATAFLOW: {PragmaTargetKind.FUNCTION, PragmaTargetKind.REGION},
        PragmaKind.INLINE: {PragmaTargetKind.FUNCTION},
        PragmaKind.BIND_STORAGE: {PragmaTargetKind.ARRAY},
        PragmaKind.BIND_OP: {PragmaTargetKind.OPERATION},
    }
    if target not in allowed[kind]:
        raise PragmaModelContractError(
            f"{kind.value} does not support target_kind={target.value}"
        )


def _pragma_parameters(kind: PragmaKind, value: Mapping[str, Any]) -> dict[str, Any]:
    data = _safe_json_object(value, "parameters")
    if kind is PragmaKind.UNKNOWN:
        if data:
            raise PragmaModelContractError("unknown action parameters must be empty")
        return {}
    if kind is PragmaKind.PIPELINE:
        _exact_keys(data, {"ii", "rewind"})
        if "ii" in data:
            data["ii"] = _positive_int(data["ii"], "parameters.ii")
        if "rewind" in data and not isinstance(data["rewind"], bool):
            raise PragmaModelContractError("parameters.rewind must be boolean")
    elif kind is PragmaKind.UNROLL:
        _exact_keys(data, {"factor", "skip_exit_check"})
        if "factor" in data:
            data["factor"] = _positive_int(data["factor"], "parameters.factor")
        if "skip_exit_check" in data and not isinstance(
            data["skip_exit_check"], bool
        ):
            raise PragmaModelContractError(
                "parameters.skip_exit_check must be boolean"
            )
    elif kind is PragmaKind.ARRAY_PARTITION:
        _exact_keys(data, {"type", "factor", "dim"})
        partition_type = data.get("type")
        if partition_type not in {"complete", "block", "cyclic"}:
            raise PragmaModelContractError(
                "array_partition parameters.type is invalid"
            )
        if "dim" in data:
            data["dim"] = _positive_int(data["dim"], "parameters.dim")
        if partition_type == "complete":
            if "factor" in data:
                raise PragmaModelContractError(
                    "complete array_partition must not specify factor"
                )
        else:
            if "factor" not in data:
                raise PragmaModelContractError(
                    "block/cyclic array_partition requires factor"
                )
            data["factor"] = _positive_int(data["factor"], "parameters.factor")
    elif kind is PragmaKind.DATAFLOW:
        if data:
            raise PragmaModelContractError("dataflow parameters must be empty")
    elif kind is PragmaKind.INLINE:
        if not data:
            return {}
        if set(data) != {"mode"} or data["mode"] not in {
            "off",
            "recursive",
        }:
            raise PragmaModelContractError(
                "inline parameters must be empty or exactly mode=off|recursive"
            )
    elif kind is PragmaKind.BIND_STORAGE:
        _exact_keys(data, {"type", "impl", "latency"})
        if data.get("type") not in _BIND_STORAGE_TYPES:
            raise PragmaModelContractError("bind_storage parameters.type is invalid")
        if data.get("impl") not in _BIND_STORAGE_IMPLS:
            raise PragmaModelContractError("bind_storage parameters.impl is invalid")
        if "latency" in data:
            data["latency"] = _non_negative_int(
                data["latency"], "parameters.latency"
            )
    elif kind is PragmaKind.BIND_OP:
        _exact_keys(data, {"op", "impl", "latency"})
        if data.get("op") not in _BIND_OPS:
            raise PragmaModelContractError("bind_op parameters.op is invalid")
        if data.get("impl") not in _BIND_OP_IMPLS:
            raise PragmaModelContractError("bind_op parameters.impl is invalid")
        if "latency" in data:
            data["latency"] = _non_negative_int(
                data["latency"], "parameters.latency"
            )
    else:  # pragma: no cover - enum exhaustiveness
        raise PragmaModelContractError("unsupported pragma kind")
    return data


def _exact_keys(value: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise PragmaModelContractError(
            "unsupported pragma parameters: " + ", ".join(sorted(unknown))
        )


def _parse_strict_json_object(text: str) -> dict[str, Any]:
    cleaned = _required_text(text, "model response")
    fence = _JSON_FENCE_RE.fullmatch(cleaned)
    if fence is not None:
        cleaned = fence.group("body").strip()
    elif "```" in cleaned:
        raise PragmaModelContractError(
            "JSON response must be raw JSON or exactly one json fence"
        )
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise PragmaModelContractError("model response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PragmaModelContractError("model response must be a JSON object")
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
        raise PragmaModelContractError(f"{name} contains agent-unsafe text")
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
        raise PragmaModelContractError(
            "unsupported signal_fields: " + ", ".join(sorted(unknown))
        )
    return result


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PragmaModelContractError(f"{name} must be a positive integer")
    return value


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PragmaModelContractError(f"{name} must be a non-negative integer")
    return value


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
