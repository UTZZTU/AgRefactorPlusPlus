"""Model-backed Structural hypothesis and complete-source integration for S3.4.

This module connects the provider-neutral model registry to the injected S3.3
state machine without enabling product ``optimize``/``full``.  It performs two
bounded model operations:

1. strict Structural hypothesis JSON generation;
2. strict complete C++ source generation for the selected hypothesis.

Correctness, synthesis, Hidden evaluation, and PPA remain delegated to an
explicit qualification adapter.  A valid model response is never treated as a
qualified or improved candidate by itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol, runtime_checkable

from agrefactor.config import TaskSpec
from agrefactor.models import (
    EffectiveModelConfig,
    ModelProvider,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    TokenUsage,
    estimate_model_cost,
)
from agrefactor.models.candidate_adapter import (
    CandidateResponseContract,
    CandidateResponseError,
    candidate_response_reason_codes,
)
from agrefactor.prompts.optimization import (
    StructuralHypothesisPromptRequest,
    StructuralOptimizationPromptBuilder,
    StructuralRewritePromptRequest,
)
from agrefactor.runtime.budget import BudgetManager

from .execution import (
    CandidateExecutionRequest,
    CandidateExecutionResult,
    CandidateExecutor,
    CandidateGenerationAbstained,
)
from .policy import BudgetIncrement
from .provider import (
    HypothesisGenerationAbstained,
    HypothesisProvider,
    HypothesisRequest,
)
from .qualification import CandidateQualificationResult
from .state import HypothesisRecord, HypothesisRisk, OptimizationLevel


STRUCTURAL_MODEL_SCHEMA_VERSION = 1
MODEL_CALL_ARTIFACT_SCHEMA_VERSION = 2
MODEL_CALL_ARTIFACT_SUPPORTED_READ_VERSIONS = frozenset({1, 2})
STRUCTURAL_HYPOTHESIS_RESPONSE_SCHEMA_VERSION = 1
STRUCTURAL_MODEL_CALL_KIND_HYPOTHESIS = "structural_hypothesis"
STRUCTURAL_MODEL_CALL_KIND_REWRITE = "structural_rewrite"
_MODEL_CALL_KIND_HYPOTHESIS = STRUCTURAL_MODEL_CALL_KIND_HYPOTHESIS
_MODEL_CALL_KIND_REWRITE = STRUCTURAL_MODEL_CALL_KIND_REWRITE
_JSON_FENCE_RE = re.compile(r"^```[ \t]*json[ \t]*\r?\n(?P<body>.*)```$", re.DOTALL | re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StructuralModelContractError(ValueError):
    """Raised when a real or fake model violates an S3.4 contract."""


@dataclass(frozen=True, slots=True)
class StructuralModelCallRecord:
    sequence: int
    call_kind: str
    logical_model_name: str
    provider_name: str
    model_id: str
    prompt_identity_sha256: str
    prompt_manifest: Mapping[str, Any]
    response_sha256: str | None
    response_valid: bool
    error_code: str | None
    error_reason_codes: tuple[str, ...]
    usage: Mapping[str, Any]
    finish_reason: str | None
    timestamp_utc: str

    schema_version = MODEL_CALL_ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("sequence must be positive")
        for name in (
            "call_kind",
            "logical_model_name",
            "provider_name",
            "model_id",
            "prompt_identity_sha256",
            "timestamp_utc",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if _SHA256_RE.fullmatch(self.prompt_identity_sha256) is None:
            raise ValueError("prompt_identity_sha256 must be lowercase SHA-256")
        if self.response_sha256 is not None and _SHA256_RE.fullmatch(self.response_sha256) is None:
            raise ValueError("response_sha256 must be lowercase SHA-256 or null")
        if not isinstance(self.response_valid, bool):
            raise TypeError("response_valid must be boolean")
        reasons = tuple(self.error_reason_codes)
        if not all(
            isinstance(code, str)
            and re.fullmatch(r"[a-z][a-z0-9_]*", code)
            for code in reasons
        ):
            raise ValueError("error_reason_codes must contain safe tokens")
        if self.response_valid and reasons:
            raise ValueError("valid model calls must not carry error_reason_codes")
        object.__setattr__(self, "error_reason_codes", reasons)
        object.__setattr__(self, "prompt_manifest", _safe_json_object(self.prompt_manifest, "prompt_manifest"))
        object.__setattr__(self, "usage", _safe_json_object(self.usage, "usage"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "call_kind": self.call_kind,
            "logical_model_name": self.logical_model_name,
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "prompt_identity_sha256": self.prompt_identity_sha256,
            "prompt_manifest": dict(self.prompt_manifest),
            "response_sha256": self.response_sha256,
            "response_valid": self.response_valid,
            "error_code": self.error_code,
            "error_reason_codes": list(self.error_reason_codes),
            "usage": dict(self.usage),
            "finish_reason": self.finish_reason,
            "timestamp_utc": self.timestamp_utc,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuralModelCallRecord":
        """Read current v2 and both historical v1 model-call artifact shapes.

        Writers emit v2. Historical v1 records are accepted either without
        ``error_reason_codes`` or with the backward-compatible optional
        extension that S3.7 v8/v9 wrote before the schema bump.
        """

        if not isinstance(value, Mapping):
            raise TypeError("model call record must be a mapping")
        payload = dict(value)
        version = payload.get("schema_version")
        if (
            isinstance(version, bool)
            or version not in MODEL_CALL_ARTIFACT_SUPPORTED_READ_VERSIONS
        ):
            raise ValueError("unsupported model call artifact schema_version")

        common_fields = {
            "schema_version",
            "sequence",
            "call_kind",
            "logical_model_name",
            "provider_name",
            "model_id",
            "prompt_identity_sha256",
            "prompt_manifest",
            "response_sha256",
            "response_valid",
            "error_code",
            "usage",
            "finish_reason",
            "timestamp_utc",
        }
        if version == 1:
            required_fields = common_fields
            allowed_fields = common_fields | {"error_reason_codes"}
        else:
            required_fields = common_fields | {"error_reason_codes"}
            allowed_fields = required_fields

        missing = required_fields - set(payload)
        unknown = set(payload) - allowed_fields
        if missing or unknown:
            raise ValueError(
                "model call record fields do not match schema "
                f"(missing={sorted(missing)}, unknown={sorted(unknown)})"
            )

        return cls(
            sequence=payload["sequence"],
            call_kind=payload["call_kind"],
            logical_model_name=payload["logical_model_name"],
            provider_name=payload["provider_name"],
            model_id=payload["model_id"],
            prompt_identity_sha256=payload["prompt_identity_sha256"],
            prompt_manifest=payload["prompt_manifest"],
            response_sha256=payload["response_sha256"],
            response_valid=payload["response_valid"],
            error_code=payload["error_code"],
            error_reason_codes=tuple(payload.get("error_reason_codes", ())),
            usage=payload["usage"],
            finish_reason=payload["finish_reason"],
            timestamp_utc=payload["timestamp_utc"],
        )


class StructuralModelArtifactWriter:
    """Append agent-safe model-call metadata without raw prompts or responses."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        path = Path(root)
        if path.exists() and path.is_symlink():
            raise ValueError("model artifact root must not be a symbolic link")
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise ValueError("model artifact root must be a directory")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._root = path.resolve()
        self._path = self._root / "model_calls.jsonl"
        self._clock = clock
        self._next_sequence = self._read_next_sequence()

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        call_kind: str,
        effective_config: EffectiveModelConfig,
        prompt_manifest: Mapping[str, Any],
        response: ModelResponse | None,
        response_valid: bool,
        error_code: str | None,
        error_reason_codes: Sequence[str] = (),
    ) -> StructuralModelCallRecord:
        manifest = _safe_json_object(prompt_manifest, "prompt_manifest")
        prompt_sha = manifest.get("prompt_identity_sha256")
        if not isinstance(prompt_sha, str):
            raise ValueError("prompt manifest is missing prompt_identity_sha256")
        timestamp = self._clock()
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ValueError("model artifact clock must return aware datetime")
        record = StructuralModelCallRecord(
            sequence=self._next_sequence,
            call_kind=call_kind,
            logical_model_name=effective_config.logical_model_name,
            provider_name=effective_config.provider_name,
            model_id=effective_config.model_id,
            prompt_identity_sha256=prompt_sha,
            prompt_manifest=manifest,
            response_sha256=(None if response is None else sha256(response.text.encode("utf-8")).hexdigest()),
            response_valid=response_valid,
            error_code=error_code,
            error_reason_codes=tuple(error_reason_codes),
            usage=({} if response is None else response.usage.to_dict()),
            finish_reason=None if response is None else response.finish_reason,
            timestamp_utc=timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        if self._path.exists() and (self._path.is_symlink() or not self._path.is_file()):
            raise ValueError("model_calls.jsonl must be a regular file")
        line = json.dumps(record.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._next_sequence += 1
        return record

    def _read_next_sequence(self) -> int:
        if not self._path.exists():
            return 1
        if self._path.is_symlink() or not self._path.is_file():
            raise ValueError("model_calls.jsonl must be a regular file")
        expected = 1
        for line_number, raw in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"model_calls.jsonl line {line_number} is invalid JSON") from exc
            if not isinstance(value, Mapping) or value.get("sequence") != expected:
                raise ValueError("model call sequences must be contiguous")
            expected += 1
        return expected


@dataclass(frozen=True, slots=True)
class StructuralHypothesisDraft:
    claim: str
    expected_benefit: Mapping[str, Any]
    risk: HypothesisRisk
    modification_scope: tuple[str, ...]
    verification_plan: tuple[str, ...]

    def __post_init__(self) -> None:
        claim = _required_text(self.claim, "claim")
        benefit = _safe_json_object(self.expected_benefit, "expected_benefit")
        if benefit != {"direction": "decrease", "metric": "latency"}:
            raise StructuralModelContractError(
                "expected_benefit must be exactly latency/decrease"
            )
        risk = self.risk if isinstance(self.risk, HypothesisRisk) else HypothesisRisk(self.risk)
        scope = _text_tuple(self.modification_scope, "modification_scope")
        plan = _text_tuple(self.verification_plan, "verification_plan")
        if plan != ("preflight", "public", "csynth", "hidden"):
            raise StructuralModelContractError(
                "verification_plan must be preflight, public, csynth, hidden"
            )
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "expected_benefit", benefit)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "modification_scope", scope)
        object.__setattr__(self, "verification_plan", plan)


class StructuralHypothesisResponseContract:
    """Strict JSON parser for one model hypothesis response."""

    def __init__(self, *, max_hypotheses: int) -> None:
        if isinstance(max_hypotheses, bool) or not 1 <= max_hypotheses <= 3:
            raise ValueError("max_hypotheses must be between 1 and 3")
        self._max_hypotheses = max_hypotheses

    def parse(self, response_text: str) -> tuple[StructuralHypothesisDraft, ...]:
        payload = _parse_strict_json_object(response_text)
        if set(payload) != {"schema_version", "hypotheses"}:
            raise StructuralModelContractError(
                "hypothesis response must contain exactly schema_version and hypotheses"
            )
        if payload["schema_version"] != STRUCTURAL_HYPOTHESIS_RESPONSE_SCHEMA_VERSION:
            raise StructuralModelContractError("unsupported hypothesis response schema_version")
        values = payload["hypotheses"]
        if not isinstance(values, list):
            raise StructuralModelContractError("hypotheses must be an array")
        if len(values) > self._max_hypotheses:
            raise StructuralModelContractError("model returned too many hypotheses")
        drafts: list[StructuralHypothesisDraft] = []
        required = {
            "claim",
            "expected_benefit",
            "risk",
            "modification_scope",
            "verification_plan",
        }
        for index, value in enumerate(values, start=1):
            if not isinstance(value, Mapping) or set(value) != required:
                raise StructuralModelContractError(
                    f"hypothesis {index} must contain exactly the frozen fields"
                )
            try:
                draft = StructuralHypothesisDraft(
                    claim=value["claim"],
                    expected_benefit=value["expected_benefit"],
                    risk=value["risk"],
                    modification_scope=tuple(value["modification_scope"]),
                    verification_plan=tuple(value["verification_plan"]),
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise StructuralModelContractError(
                    f"hypothesis {index} violates the frozen schema"
                ) from exc
            drafts.append(draft)
        return tuple(drafts)


@dataclass(frozen=True, slots=True)
class StructuralCandidateGenerationResult:
    candidate_code: str
    source: bytes
    prompt_manifest: Mapping[str, Any]
    response: ModelResponse
    response_contract: CandidateResponseContract

    schema_version = STRUCTURAL_MODEL_SCHEMA_VERSION

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
        object.__setattr__(self, "candidate_code", code)
        object.__setattr__(self, "prompt_manifest", _safe_json_object(self.prompt_manifest, "prompt_manifest"))


class _StructuralModelEndpoint:
    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        budget: BudgetManager,
        artifacts: StructuralModelArtifactWriter,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("registry must be ModelRegistry")
        if not isinstance(effective_config, EffectiveModelConfig):
            raise TypeError("effective_config must be EffectiveModelConfig")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be BudgetManager")
        if not isinstance(artifacts, StructuralModelArtifactWriter):
            raise TypeError("artifacts must be StructuralModelArtifactWriter")
        model = effective_config.to_model_spec()
        provider = registry.get_provider(effective_config.provider_name)
        self._registry = registry
        self._effective_config = effective_config
        self._model = model
        self._provider = provider
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
            response = self._provider.generate(
                self._model,
                ModelRequest(
                    messages=prompt.messages,
                    parameters=self._effective_config.parameters,
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


class StructuralModelHypothesisProvider(_StructuralModelEndpoint):
    """Real-model HypothesisProvider for the Structural level only."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        task: TaskSpec,
        budget: BudgetManager,
        artifacts: StructuralModelArtifactWriter,
        prompt_builder: StructuralOptimizationPromptBuilder | None = None,
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
        self._builder = prompt_builder or StructuralOptimizationPromptBuilder()
        if not isinstance(self._builder, StructuralOptimizationPromptBuilder):
            raise TypeError("prompt_builder must be StructuralOptimizationPromptBuilder")
        self._name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"structural-model-hypothesis:{effective_config.logical_model_name}"
        )
        self._requests: list[HypothesisRequest] = []
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
    def responses(self) -> tuple[ModelResponse, ...]:
        return tuple(self._responses)

    def propose(self, request: HypothesisRequest) -> Sequence[HypothesisRecord]:
        if not isinstance(request, HypothesisRequest):
            raise TypeError("request must be HypothesisRequest")
        if request.level is not OptimizationLevel.STRUCTURAL:
            raise ValueError("S3.4 model provider supports Structural only")
        if not request.parent_source:
            raise ValueError("Structural model provider requires parent_source")
        try:
            source = request.parent_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parent_source must be UTF-8") from exc
        prompt = self._builder.build_hypothesis(
            StructuralHypothesisPromptRequest(
                task=self._task,
                parent_candidate_id=request.parent_candidate.candidate_id,
                parent_source=source,
                round_number=request.round_number,
                max_hypotheses=request.max_hypotheses,
                supporting_evidence_ids=request.supporting_evidence_ids,
                safe_context=request.safe_context,
                family_instruction=self._effective_config.family_instruction,
            )
        )
        self._requests.append(request)
        response = self._call(prompt=prompt, call_kind=_MODEL_CALL_KIND_HYPOTHESIS)
        self._responses.append(response)
        try:
            drafts = StructuralHypothesisResponseContract(
                max_hypotheses=request.max_hypotheses
            ).parse(response.text)
            prompt_sha = prompt.manifest["prompt_identity_sha256"]
            records = tuple(
                HypothesisRecord(
                    hypothesis_id=f"hyp-structural-r{request.round_number}-{index}",
                    level=OptimizationLevel.STRUCTURAL,
                    parent_candidate_id=request.parent_candidate.candidate_id,
                    claim=draft.claim,
                    supporting_evidence_ids=request.supporting_evidence_ids,
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
                    },
                    prompt_identity_sha256=prompt_sha,
                )
                for index, draft in enumerate(drafts, start=1)
            )
        except StructuralModelContractError as exc:
            self._record_invalid(
                prompt=prompt,
                call_kind=_MODEL_CALL_KIND_HYPOTHESIS,
                response=response,
                error=exc,
                error_reason_codes=("analysis_response_contract_invalid",),
            )
            raise HypothesisGenerationAbstained(
                reason_code="hypothesis_response_contract_abstention",
                error_code=type(exc).__name__,
                detail_codes=("analysis_response_contract_invalid",),
            ) from exc
        self._record_valid(
            prompt=prompt,
            call_kind=_MODEL_CALL_KIND_HYPOTHESIS,
            response=response,
        )
        return records


class StructuralModelCandidateGenerator(_StructuralModelEndpoint):
    """Generate and validate one complete Structural candidate source."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        effective_config: EffectiveModelConfig,
        task: TaskSpec,
        budget: BudgetManager,
        artifacts: StructuralModelArtifactWriter,
        prompt_builder: StructuralOptimizationPromptBuilder | None = None,
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
        self._builder = prompt_builder or StructuralOptimizationPromptBuilder()
        if not isinstance(self._builder, StructuralOptimizationPromptBuilder):
            raise TypeError("prompt_builder must be StructuralOptimizationPromptBuilder")
        self._name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"structural-model-generator:{effective_config.logical_model_name}"
        )
        self._requests: list[CandidateExecutionRequest] = []
        self._results: list[StructuralCandidateGenerationResult] = []

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
    def results(self) -> tuple[StructuralCandidateGenerationResult, ...]:
        return tuple(self._results)

    def generate(self, request: CandidateExecutionRequest) -> StructuralCandidateGenerationResult:
        if not isinstance(request, CandidateExecutionRequest):
            raise TypeError("request must be CandidateExecutionRequest")
        if request.level is not OptimizationLevel.STRUCTURAL:
            raise ValueError("S3.4 model generator supports Structural only")
        try:
            parent_source = request.parent_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parent_source must be UTF-8") from exc
        prompt = self._builder.build_rewrite(
            StructuralRewritePromptRequest(
                task=self._task,
                candidate_id=request.candidate_id,
                parent_candidate_id=request.parent_candidate.candidate_id,
                parent_source=parent_source,
                hypothesis=request.hypothesis,
                safe_context={
                    "policy": request.hypothesis.model_identity.get("policy", "safe-v1"),
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
            result = StructuralCandidateGenerationResult(
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
class StructuralQualificationAdapter(Protocol):
    """Explicit correctness/synthesis/PPA consumer for generated source."""

    @property
    def name(self) -> str: ...

    @property
    def uses_vitis(self) -> bool: ...

    def qualify(
        self,
        request: CandidateExecutionRequest,
        source: bytes,
    ) -> CandidateQualificationResult: ...


class StructuralModelCandidateExecutor:
    """CandidateExecutor combining real model generation with explicit qualification."""

    def __init__(
        self,
        *,
        generator: StructuralModelCandidateGenerator,
        qualifier: StructuralQualificationAdapter,
        name: str = "structural-model-candidate-executor",
    ) -> None:
        if not isinstance(generator, StructuralModelCandidateGenerator):
            raise TypeError("generator must be StructuralModelCandidateGenerator")
        if not hasattr(qualifier, "qualify") or not hasattr(qualifier, "uses_vitis"):
            raise TypeError("qualifier does not satisfy StructuralQualificationAdapter")
        self._generator = generator
        self._qualifier = qualifier
        self._name = _required_text(name, "name")
        self._requests: list[CandidateExecutionRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        # Tool/compile/CSIM/CSYNTH usage is accounted by the injected qualifier.
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
        if request.level is not OptimizationLevel.STRUCTURAL:
            raise ValueError("S3.4 executor supports Structural only")
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
        raise StructuralModelContractError(
            "JSON response must be raw JSON or exactly one json fence"
        )
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise StructuralModelContractError("model response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise StructuralModelContractError("model response must be a JSON object")
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
        if legacy_cost is not None and abs(Decimal(str(legacy_cost)) - estimated.amount) > Decimal("1e-12"):
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


def _safe_json_object(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        copied = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite JSON data") from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    _reject_unsafe_keys(copied, name)
    return copied


def _reject_unsafe_keys(value: Any, path: str) -> None:
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
            if normalized == "hidden_test_source_isolation":
                _reject_unsafe_keys(item, f"{path}.{key}")
                continue
            if normalized in forbidden or normalized.startswith("hidden_"):
                raise ValueError(f"{path} contains unsafe key: {key}")
            _reject_unsafe_keys(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _reject_unsafe_keys(item, f"{path}[{index}]")


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in cleaned:
        raise ValueError(f"{name} must not contain NUL")
    return cleaned


def _text_tuple(value: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(_required_text(item, name) for item in value)
    if not result:
        raise StructuralModelContractError(f"{name} must not be empty")
    return result
