"""Bounded Optimize Candidate recovery without replacing the safe-v1 engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
import json

from agrefactor.config import TaskSpec
from agrefactor.evaluation import FeedbackRouteAction, FeedbackRouteDecision
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackReport,
    FeedbackStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    CandidateModelResult,
    CandidateResponseContract,
    CandidateResponseError,
    ModelResponse,
)
from agrefactor.models.candidate_adapter import candidate_response_reason_codes
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    build_candidate_compile_repair_prompt,
    build_candidate_csynth_repair_prompt,
)
from agrefactor.repair import (
    CandidateRepairPayload,
    RepairArtifactRole,
    RepairArtifactWriter,
    RepairAttemptRecord,
    RepairModelObservation,
    RepairObservedUsage,
    RepairRunRecord,
    RepairTerminalStatus,
    repair_attempt_id,
    repair_proposal_id,
)
from agrefactor.runtime.budget import BudgetExceededError, BudgetManager, BudgetUsage

from .execution import (
    CandidateExecutionRequest,
    CandidateExecutionResult,
    CandidateGenerationAbstained,
)
from .policy import BudgetIncrement
from .qualification import CandidateQualificationResult, QualificationStatus
from .state import CandidateRecord, CandidateStatus, HypothesisRecord
from .state_machine import DeterministicOptimizerStateMachine


OPTIMIZE_RECOVERY_SCHEMA_VERSION = 1
MAX_OPTIMIZE_RECOVERIES_PER_ROOT_CANDIDATE = 1

_PREFLIGHT_REASON_CODES = frozenset(
    {"candidate_compile_failed", "candidate_top_missing", "interface_mismatch"}
)
_PREFLIGHT_STAGES = frozenset(
    {FeedbackStage.STATIC_CHECK, FeedbackStage.COMPILE, FeedbackStage.LINK}
)
_CSYNTH_LEGALITY_CATEGORIES = frozenset(
    {
        FeedbackCategory.UNDECLARED_TYPE,
        FeedbackCategory.UNDECLARED_SYMBOL,
        FeedbackCategory.SYNTAX_ERROR,
        FeedbackCategory.LINK_ERROR,
        FeedbackCategory.LINKAGE_MISMATCH,
        FeedbackCategory.UNSUPPORTED_CONSTRUCT,
        FeedbackCategory.UNKNOWN_BOUND,
    }
)


class OptimizeRecoveryStage(str, Enum):
    PREFLIGHT = "preflight"
    CSYNTH = "csynth"


class OptimizeRecoveryStatus(str, Enum):
    INELIGIBLE = "ineligible"
    BUDGET_BLOCKED = "budget_blocked"
    PROVIDER_ERROR = "provider_error"
    RESPONSE_REJECTED = "response_rejected"
    VALIDATOR_ERROR = "validator_error"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"


@dataclass(frozen=True, slots=True)
class OptimizeRecoveryEvidence:
    """Agent-safe, Candidate-owned evidence eligible for one recovery."""

    stage: OptimizeRecoveryStage
    feedback: FeedbackReport
    route_decision: FeedbackRouteDecision
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        stage = (
            self.stage
            if isinstance(self.stage, OptimizeRecoveryStage)
            else OptimizeRecoveryStage(str(self.stage))
        )
        if not isinstance(self.feedback, FeedbackReport):
            raise TypeError("feedback must be a FeedbackReport")
        if not isinstance(self.route_decision, FeedbackRouteDecision):
            raise TypeError("route_decision must be a FeedbackRouteDecision")
        reasons = _safe_reason_tuple(self.reason_codes)
        feedback = self.feedback
        route = self.route_decision
        if feedback.metadata.get("evidence_view") != "agent_safe":
            raise ValueError("Optimize recovery requires agent_safe feedback")
        if route.metadata.get("evidence_view") != "agent_safe":
            raise ValueError("Optimize recovery route must be agent_safe")
        if route.action is not FeedbackRouteAction.REPAIR_CANDIDATE:
            raise ValueError("Optimize recovery requires route=repair_candidate")
        if route.source_report_id != feedback.report_id:
            raise ValueError("route source_report_id must match feedback")
        if not feedback.blocking:
            raise ValueError("Optimize recovery requires blocking feedback")
        selected = set(route.selected_feedback_ids)
        blocking = {item.feedback_id for item in feedback.items if item.blocking}
        if not selected or selected != blocking:
            raise ValueError("Optimize recovery must select all blocking feedback")
        items = {item.feedback_id: item for item in feedback.items}
        for feedback_id in selected:
            item = items.get(feedback_id)
            if item is None:
                raise ValueError("route selected an unknown feedback item")
            if item.owner is not FeedbackOwner.CANDIDATE:
                raise ValueError("Optimize recovery requires Candidate ownership")
        if stage is OptimizeRecoveryStage.PREFLIGHT:
            if not set(reasons).issubset(_PREFLIGHT_REASON_CODES):
                raise ValueError("Preflight recovery reason is not eligible")
            if any(items[item_id].stage not in _PREFLIGHT_STAGES for item_id in selected):
                raise ValueError("Preflight recovery received a non-Preflight item")
        else:
            if any(items[item_id].stage is not FeedbackStage.CSYNTH for item_id in selected):
                raise ValueError("CSYNTH recovery requires CSYNTH feedback")
            if any(
                items[item_id].category not in _CSYNTH_LEGALITY_CATEGORIES
                for item_id in selected
            ):
                raise ValueError("CSYNTH recovery is limited to legality failures")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "reason_codes", reasons)


@dataclass(frozen=True, slots=True)
class OptimizeRecoveryValidationRequest:
    candidate_id: str
    sequence: int
    source_candidate: CandidateRecord
    hypothesis: HypothesisRecord
    source: bytes
    budget_before: Mapping[str, Any]
    created_at_utc: str

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be positive")
        if self.candidate_id != f"cand-{self.sequence}":
            raise ValueError("candidate_id must match recovery sequence")
        if not isinstance(self.source_candidate, CandidateRecord):
            raise TypeError("source_candidate must be a CandidateRecord")
        if self.source_candidate.status is not CandidateStatus.REJECTED:
            raise ValueError("source_candidate must already be rejected")
        if not isinstance(self.hypothesis, HypothesisRecord):
            raise TypeError("hypothesis must be a HypothesisRecord")
        if self.source_candidate.hypothesis_id != self.hypothesis.hypothesis_id:
            raise ValueError("recovery hypothesis must match source Candidate")
        if not isinstance(self.source, bytes) or not self.source:
            raise ValueError("recovery source must be non-empty bytes")
        object.__setattr__(self, "budget_before", _json_mapping(self.budget_before, "budget_before"))


@runtime_checkable
class OptimizeRecoveryValidator(Protocol):
    def validate_recovery(
        self, request: OptimizeRecoveryValidationRequest
    ) -> CandidateExecutionResult: ...


@dataclass(frozen=True, slots=True)
class OptimizeCandidateRecoveryRequest:
    run_id: str
    source_candidate: CandidateRecord
    source: bytes
    interface_source: bytes
    source_qualification: CandidateQualificationResult
    hypothesis: HypothesisRecord
    recovery_candidate_id: str
    recovery_sequence: int
    budget_before: Mapping[str, Any]
    created_at_utc: str

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        if not isinstance(self.source_candidate, CandidateRecord):
            raise TypeError("source_candidate must be a CandidateRecord")
        if self.source_candidate.status is not CandidateStatus.REJECTED:
            raise ValueError("source_candidate must be rejected")
        if not isinstance(self.source, bytes) or not self.source:
            raise ValueError("source must be non-empty bytes")
        if sha256(self.source).hexdigest() != self.source_candidate.source_sha256:
            raise ValueError("source bytes do not match source Candidate")
        if not isinstance(self.interface_source, bytes) or not self.interface_source:
            raise ValueError("interface_source must be non-empty bytes")
        if not isinstance(self.source_qualification, CandidateQualificationResult):
            raise TypeError("source_qualification must be typed")
        if self.source_qualification.candidate_id != self.source_candidate.candidate_id:
            raise ValueError("source qualification linkage mismatch")
        if self.source_qualification.status is not QualificationStatus.REJECTED:
            raise ValueError("only rejected qualifications can recover")
        if not isinstance(self.hypothesis, HypothesisRecord):
            raise TypeError("hypothesis must be a HypothesisRecord")
        if self.source_candidate.hypothesis_id != self.hypothesis.hypothesis_id:
            raise ValueError("source Candidate hypothesis mismatch")
        if (
            isinstance(self.recovery_sequence, bool)
            or not isinstance(self.recovery_sequence, int)
            or self.recovery_sequence != self.source_candidate.sequence + 1
        ):
            raise ValueError("recovery_sequence must immediately follow source")
        if self.recovery_candidate_id != f"cand-{self.recovery_sequence}":
            raise ValueError("recovery_candidate_id must match sequence")
        object.__setattr__(self, "budget_before", _json_mapping(self.budget_before, "budget_before"))


@dataclass(frozen=True, slots=True)
class OptimizeCandidateRecoveryResult:
    status: OptimizeRecoveryStatus
    source_candidate_id: str
    recovery_candidate_id: str | None
    stage: OptimizeRecoveryStage | None
    reason_codes: tuple[str, ...]
    source: bytes | None
    qualification: CandidateQualificationResult | None
    budget_before: Mapping[str, Any]
    budget_after: Mapping[str, Any]
    artifact_paths: Mapping[str, Any] = field(default_factory=dict)
    prompt_manifest: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None

    schema_version = OPTIMIZE_RECOVERY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        status = self.status if isinstance(self.status, OptimizeRecoveryStatus) else OptimizeRecoveryStatus(str(self.status))
        stage = None if self.stage is None else (
            self.stage if isinstance(self.stage, OptimizeRecoveryStage) else OptimizeRecoveryStage(str(self.stage))
        )
        _required_text(self.source_candidate_id, "source_candidate_id")
        if self.recovery_candidate_id is not None:
            _required_text(self.recovery_candidate_id, "recovery_candidate_id")
        reasons = _safe_reason_tuple(
            self.reason_codes, allow_empty=status is OptimizeRecoveryStatus.INELIGIBLE
        )
        if self.source is not None and (not isinstance(self.source, bytes) or not self.source):
            raise ValueError("source must be non-empty bytes or null")
        if self.qualification is not None and not isinstance(self.qualification, CandidateQualificationResult):
            raise TypeError("qualification must be typed or null")
        if (self.source is None) != (self.qualification is None):
            raise ValueError("source and qualification must appear together")
        if self.qualification is not None:
            if self.recovery_candidate_id is None:
                raise ValueError("qualified recovery requires candidate ID")
            if self.qualification.candidate_id != self.recovery_candidate_id:
                raise ValueError("recovery qualification linkage mismatch")
        if status is OptimizeRecoveryStatus.VALIDATED and (
            self.qualification is None or not self.qualification.accepted
        ):
            raise ValueError("validated recovery requires accepted qualification")
        if status is OptimizeRecoveryStatus.VALIDATION_FAILED and (
            self.qualification is None or self.qualification.accepted
        ):
            raise ValueError("validation_failed requires failed qualification")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "budget_before", _json_mapping(self.budget_before, "budget_before"))
        object.__setattr__(self, "budget_after", _json_mapping(self.budget_after, "budget_after"))
        object.__setattr__(self, "artifact_paths", _json_mapping(self.artifact_paths, "artifact_paths"))
        object.__setattr__(self, "prompt_manifest", _json_mapping(self.prompt_manifest, "prompt_manifest"))

    @property
    def descendant_created(self) -> bool:
        return self.source is not None and self.qualification is not None

    def lineage_metadata(self) -> dict[str, Any]:
        return {
            "recovery_schema_version": self.schema_version,
            "recovery_status": self.status.value,
            "recovery_of": self.source_candidate_id,
            "recovery_candidate_id": self.recovery_candidate_id,
            "recovery_attempt": 0 if self.status is OptimizeRecoveryStatus.INELIGIBLE else 1,
            "recovery_stage": None if self.stage is None else self.stage.value,
            "recovery_reason_codes": list(self.reason_codes),
            "recovery_artifacts": dict(self.artifact_paths),
            "recovery_error_type": self.error_type,
        }


@runtime_checkable
class OptimizeCandidateRecoveryCoordinator(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def uses_network(self) -> bool: ...
    @property
    def uses_vitis(self) -> bool: ...
    def recover(self, request: OptimizeCandidateRecoveryRequest) -> OptimizeCandidateRecoveryResult: ...
    def summary(self) -> Mapping[str, Any]: ...


class BoundedOptimizeCandidateRecoveryCoordinator:
    """One total Candidate-only recovery per root Optimize Candidate."""

    name = "p4-0b-r-bounded-optimize-candidate-recovery"
    uses_network = True
    uses_vitis = True

    def __init__(
        self,
        *,
        model_adapter: CandidateModelAdapter,
        validator: OptimizeRecoveryValidator,
        evidence_provider: Callable[
            [str, CandidateQualificationResult], OptimizeRecoveryEvidence | None
        ],
        task: TaskSpec,
        original_code: str,
        budget: BudgetManager,
        validation_increment: BudgetIncrement,
        artifact_root: str | Path,
    ) -> None:
        if not isinstance(model_adapter, CandidateModelAdapter):
            raise TypeError("model_adapter must be CandidateModelAdapter")
        if not callable(getattr(validator, "validate_recovery", None)):
            raise TypeError("validator must implement validate_recovery")
        if not callable(evidence_provider):
            raise TypeError("evidence_provider must be callable")
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        _required_text(original_code, "original_code")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")
        if not isinstance(validation_increment, BudgetIncrement):
            raise TypeError("validation_increment must be BudgetIncrement")
        self._model_adapter = model_adapter
        self._validator = validator
        self._evidence_provider = evidence_provider
        self._task = task
        self._original_code = original_code
        self._budget = budget
        self._validation_increment = validation_increment
        self._artifact_root = Path(artifact_root)
        self._counts = {
            "eligible": 0,
            "attempted": 0,
            "validated": 0,
            "validation_failed": 0,
            "budget_blocked": 0,
            "provider_error": 0,
            "response_rejected": 0,
            "validator_error": 0,
            "ineligible": 0,
        }

    def summary(self) -> Mapping[str, Any]:
        return {
            "schema_version": OPTIMIZE_RECOVERY_SCHEMA_VERSION,
            "policy": self.name,
            "max_recoveries_per_root_candidate": 1,
            "repairable_stages": ["preflight", "csynth"],
            "public_csim_repair": False,
            "hidden_repair": False,
            "ppa_repair": False,
            "validation_restart_order": [
                "source",
                "preflight",
                "public",
                "csynth",
                "hidden",
                "ppa",
                "feasibility",
            ],
            **dict(self._counts),
        }

    def recover(
        self, request: OptimizeCandidateRecoveryRequest
    ) -> OptimizeCandidateRecoveryResult:
        if not isinstance(request, OptimizeCandidateRecoveryRequest):
            raise TypeError("request must be OptimizeCandidateRecoveryRequest")
        before = self._safe_snapshot()
        evidence = self._evidence_provider(
            request.source_candidate.candidate_id,
            request.source_qualification,
        )
        if evidence is None or "recovery_of" in request.source_candidate.decision:
            self._counts["ineligible"] += 1
            return self._result(
                OptimizeRecoveryStatus.INELIGIBLE,
                request,
                evidence=None,
                budget_before=before,
            )

        self._counts["eligible"] += 1
        prospective = self._validation_increment.to_kwargs()
        prospective["llm_calls"] += 1
        try:
            self._budget.ensure_available(**prospective)
        except BudgetExceededError as exc:
            self._counts["budget_blocked"] += 1
            result = self._result(
                OptimizeRecoveryStatus.BUDGET_BLOCKED,
                request,
                evidence=evidence,
                budget_before=before,
                error_type=type(exc).__name__,
            )
            return self._write_attempt(
                result,
                request=request,
                evidence=evidence,
                model_response=None,
                model_result=None,
                budget_before=before,
            )

        self._counts["attempted"] += 1
        prompt = self._build_prompt(request, evidence)
        contract = self._response_contract(request, evidence)
        model_request = CandidateModelRequest(
            prompt=prompt,
            task=self._task,
            current_candidate=request.source.decode("utf-8"),
            response_contract=contract,
        )
        response_count = len(self._model_adapter.responses)
        model_response: ModelResponse | None = None
        model_result: CandidateModelResult | None = None
        try:
            model_result = self._model_adapter.generate(
                model_request,
                before_provider_call=self._consume_llm,
                after_provider_response=self._record_model_usage,
            )
            model_response = model_result.response
        except CandidateResponseError as exc:
            model_response = self._new_response(response_count)
            self._counts["response_rejected"] += 1
            result = self._result(
                OptimizeRecoveryStatus.RESPONSE_REJECTED,
                request,
                evidence=evidence,
                budget_before=before,
                prompt_manifest=prompt.manifest,
                error_type=type(exc).__name__,
                extra_reason_codes=candidate_response_reason_codes(exc),
            )
            return self._write_attempt(
                result,
                request=request,
                evidence=evidence,
                model_response=model_response,
                model_result=None,
                budget_before=before,
            )
        except BudgetExceededError as exc:
            self._counts["budget_blocked"] += 1
            result = self._result(
                OptimizeRecoveryStatus.BUDGET_BLOCKED,
                request,
                evidence=evidence,
                budget_before=before,
                prompt_manifest=prompt.manifest,
                error_type=type(exc).__name__,
            )
            return self._write_attempt(
                result,
                request=request,
                evidence=evidence,
                model_response=model_response,
                model_result=None,
                budget_before=before,
            )
        except Exception as exc:
            model_response = self._new_response(response_count)
            self._counts["provider_error"] += 1
            result = self._result(
                OptimizeRecoveryStatus.PROVIDER_ERROR,
                request,
                evidence=evidence,
                budget_before=before,
                prompt_manifest=prompt.manifest,
                error_type=type(exc).__name__,
            )
            return self._write_attempt(
                result,
                request=request,
                evidence=evidence,
                model_response=model_response,
                model_result=None,
                budget_before=before,
            )

        proposed = model_result.candidate_code
        parent_contract = CandidateResponseContract.from_candidate(
            self._task,
            request.interface_source.decode("utf-8"),
        )
        parent_reason_codes = (
            parent_contract.validate_replacement_reason_codes(
                proposed
            )
        )
        if (
            "semantic_unchanged" in parent_reason_codes
            or _source_sha(proposed) == _source_sha(
                request.interface_source.decode("utf-8")
            )
        ):
            self._counts["response_rejected"] += 1
            result = self._result(
                OptimizeRecoveryStatus.RESPONSE_REJECTED,
                request,
                evidence=evidence,
                budget_before=before,
                prompt_manifest=prompt.manifest,
                error_type="ParentSourceFallbackRejected",
                extra_reason_codes=("parent_source_fallback",),
            )
            return self._write_attempt(
                result,
                request=request,
                evidence=evidence,
                model_response=model_response,
                model_result=model_result,
                budget_before=before,
            )

        validation_request = OptimizeRecoveryValidationRequest(
            candidate_id=request.recovery_candidate_id,
            sequence=request.recovery_sequence,
            source_candidate=request.source_candidate,
            hypothesis=request.hypothesis,
            source=proposed.encode("utf-8"),
            budget_before=self._safe_snapshot().to_dict(),
            created_at_utc=request.created_at_utc,
        )
        try:
            execution = self._validator.validate_recovery(validation_request)
            if not isinstance(execution, CandidateExecutionResult):
                raise TypeError("validator must return CandidateExecutionResult")
            if execution.qualification.candidate_id != request.recovery_candidate_id:
                raise ValueError("validator recovery candidate linkage mismatch")
            if execution.source != validation_request.source:
                raise ValueError("validator changed the recovery source")
        except Exception as exc:
            self._counts["validator_error"] += 1
            result = self._result(
                OptimizeRecoveryStatus.VALIDATOR_ERROR,
                request,
                evidence=evidence,
                budget_before=before,
                prompt_manifest=prompt.manifest,
                error_type=type(exc).__name__,
            )
            return self._write_attempt(
                result,
                request=request,
                evidence=evidence,
                model_response=model_response,
                model_result=model_result,
                budget_before=before,
            )

        if execution.qualification.accepted:
            status = OptimizeRecoveryStatus.VALIDATED
            self._counts["validated"] += 1
        else:
            status = OptimizeRecoveryStatus.VALIDATION_FAILED
            self._counts["validation_failed"] += 1
        result = self._result(
            status,
            request,
            evidence=evidence,
            budget_before=before,
            source=execution.source,
            qualification=execution.qualification,
            prompt_manifest=prompt.manifest,
        )
        return self._write_attempt(
            result,
            request=request,
            evidence=evidence,
            model_response=model_response,
            model_result=model_result,
            budget_before=before,
        )

    def _build_prompt(
        self,
        request: OptimizeCandidateRecoveryRequest,
        evidence: OptimizeRecoveryEvidence,
    ):
        hypothesis = request.hypothesis
        intent = (
            "Preserve the originating optimization hypothesis "
            f"{hypothesis.hypothesis_id}: {hypothesis.claim}. "
            "Preserve its declared modification scope: "
            + ", ".join(hypothesis.modification_scope)
            + ". Do not silently revert to the accepted parent source."
        )
        inputs = CandidateRepairPromptInputs(
            task=self._task,
            feedback=evidence.feedback,
            candidate_code=request.source.decode("utf-8"),
            original_code=self._original_code,
            attempt=1,
            max_attempts=1,
            family_instruction=self._model_adapter.family_instruction,
            family_profile=self._model_adapter.family_profile,
            approved_memory_snippets=(intent,),
        )
        if evidence.stage is OptimizeRecoveryStage.PREFLIGHT:
            return build_candidate_compile_repair_prompt(inputs)
        return build_candidate_csynth_repair_prompt(inputs)

    def _response_contract(
        self,
        request: OptimizeCandidateRecoveryRequest,
        evidence: OptimizeRecoveryEvidence,
    ) -> CandidateResponseContract:
        failed = request.source.decode("utf-8")
        interface = request.interface_source.decode("utf-8")
        if (
            evidence.stage is OptimizeRecoveryStage.PREFLIGHT
            and set(evidence.reason_codes) & {"candidate_top_missing", "interface_mismatch"}
        ):
            return CandidateResponseContract.from_candidate(self._task, interface)
        try:
            return CandidateResponseContract.from_candidate(self._task, failed)
        except CandidateResponseError:
            return CandidateResponseContract.from_candidate(self._task, interface)

    def _result(
        self,
        status: OptimizeRecoveryStatus,
        request: OptimizeCandidateRecoveryRequest,
        *,
        evidence: OptimizeRecoveryEvidence | None,
        budget_before: BudgetUsage,
        source: bytes | None = None,
        qualification: CandidateQualificationResult | None = None,
        prompt_manifest: Mapping[str, Any] | None = None,
        error_type: str | None = None,
        extra_reason_codes: tuple[str, ...] = (),
    ) -> OptimizeCandidateRecoveryResult:
        reasons = () if evidence is None else evidence.reason_codes
        if extra_reason_codes:
            reasons = tuple(dict.fromkeys((*reasons, *extra_reason_codes)))
        return OptimizeCandidateRecoveryResult(
            status=status,
            source_candidate_id=request.source_candidate.candidate_id,
            recovery_candidate_id=(
                request.recovery_candidate_id
                if status is not OptimizeRecoveryStatus.INELIGIBLE
                else None
            ),
            stage=None if evidence is None else evidence.stage,
            reason_codes=reasons,
            source=source,
            qualification=qualification,
            budget_before=budget_before.to_dict(),
            budget_after=self._safe_snapshot().to_dict(),
            prompt_manifest={} if prompt_manifest is None else prompt_manifest,
            error_type=error_type,
        )

    def _write_attempt(
        self,
        result: OptimizeCandidateRecoveryResult,
        *,
        request: OptimizeCandidateRecoveryRequest,
        evidence: OptimizeRecoveryEvidence,
        model_response: ModelResponse | None,
        model_result: CandidateModelResult | None,
        budget_before: BudgetUsage,
    ) -> OptimizeCandidateRecoveryResult:
        root = self._artifact_root / request.source_candidate.candidate_id
        run_id = (
            f"{request.run_id}.optimize-recovery."
            f"{request.source_candidate.candidate_id}"
        )
        attempt_id = repair_attempt_id(run_id, 1)
        terminal = {
            OptimizeRecoveryStatus.VALIDATED: RepairTerminalStatus.SUCCEEDED,
            OptimizeRecoveryStatus.VALIDATION_FAILED: RepairTerminalStatus.FAILED,
            OptimizeRecoveryStatus.BUDGET_BLOCKED: RepairTerminalStatus.BLOCKED,
            OptimizeRecoveryStatus.PROVIDER_ERROR: RepairTerminalStatus.ERROR,
            OptimizeRecoveryStatus.RESPONSE_REJECTED: RepairTerminalStatus.ERROR,
            OptimizeRecoveryStatus.VALIDATOR_ERROR: RepairTerminalStatus.ERROR,
        }[result.status]
        summary = {
            "status": None if result.qualification is None else result.qualification.status.value,
            "candidate_id": result.recovery_candidate_id,
            "restart_order": [
                "source",
                "preflight",
                "public",
                "csynth",
                "hidden",
                "ppa",
                "feasibility",
            ],
            "hidden_feedback_visible_to_model": False,
            "best_correct_changed_by_repair_call": False,
        }
        observation = RepairModelObservation.from_response(
            prompt_manifest=result.prompt_manifest,
            response=model_response,
            model_call_observed=(
                result.status is not OptimizeRecoveryStatus.BUDGET_BLOCKED
            ),
        )
        attempt = RepairAttemptRecord(
            attempt_id=attempt_id,
            proposal_id=None if model_result is None else repair_proposal_id(attempt_id),
            artifact_role=RepairArtifactRole.CANDIDATE,
            sequence_index=1,
            action=result.status.value,
            status=result.status.value,
            changed=model_result is not None,
            model_observation=observation,
            observed_usage=RepairObservedUsage.from_observations(
                budget_before,
                self._safe_snapshot(),
                observation,
            ),
            payload=CandidateRepairPayload(
                validation_summary=summary,
                model_result_available=model_result is not None,
            ),
            stop_reason=result.status.value,
            terminal_status=terminal,
            evidence_view="agent_safe",
            operator_artifact_available=result.qualification is not None,
            error_type=result.error_type,
            error_message=None,
            metadata={
                "source_candidate_id": request.source_candidate.candidate_id,
                "recovery_candidate_id": request.recovery_candidate_id,
                "hypothesis_id": request.hypothesis.hypothesis_id,
                "recovery_stage": evidence.stage.value,
                "recovery_reason_codes": list(result.reason_codes),
                "source_sha256": request.source_candidate.source_sha256,
                "repaired_sha256": (
                    None if result.source is None else sha256(result.source).hexdigest()
                ),
                "prompt_identity_sha256": result.prompt_manifest.get(
                    "message_sequence_sha256"
                ),
                "model_identity": (
                    None
                    if model_result is None
                    else {
                        "logical_model_name": (
                            model_result.logical_model_name
                        ),
                        "provider_name": (
                            model_result.provider_name
                        ),
                        "provider_model": (
                            model_result.response.model
                        ),
                    }
                ),
                "feedback_identity": {
                    "report_id": evidence.feedback.report_id,
                    "route_decision_id": (
                        evidence.route_decision.decision_id
                    ),
                    "selected_feedback_ids": list(
                        evidence.route_decision.selected_feedback_ids
                    ),
                },
                "hidden_evidence_exposed": False,
                "public_csim_repair": False,
                "ppa_repair": False,
            },
        )
        run = RepairRunRecord(
            run_id=run_id,
            artifact_role=RepairArtifactRole.CANDIDATE,
            terminal_status=terminal,
            stop_reason=result.status.value,
            attempts=(attempt,),
            metadata={
                "schema_version": OPTIMIZE_RECOVERY_SCHEMA_VERSION,
                "max_attempts": 1,
                "source_candidate_id": request.source_candidate.candidate_id,
                "recovery_candidate_id": request.recovery_candidate_id,
                "hypothesis_id": request.hypothesis.hypothesis_id,
            },
        )
        written = RepairArtifactWriter(root).write(run)
        return replace(result, artifact_paths=written.to_dict())

    def _consume_llm(self) -> None:
        self._budget.consume(llm_calls=1)

    def _record_model_usage(self, response: ModelResponse) -> None:
        self._budget.record_model_usage(response.usage)

    def _new_response(self, previous_count: int) -> ModelResponse | None:
        responses = self._model_adapter.responses
        if len(responses) == previous_count + 1:
            return responses[-1]
        return None

    def _safe_snapshot(self) -> BudgetUsage:
        try:
            return self._budget.snapshot()
        except BudgetExceededError:
            return self._budget.record_observed()


class BoundedRecoveryOptimizerStateMachine(DeterministicOptimizerStateMachine):
    """safe-v1 plus one injected, lineage-preserving recovery descendant."""

    def __init__(
        self,
        *,
        recovery_coordinator: OptimizeCandidateRecoveryCoordinator | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if recovery_coordinator is not None and not callable(
            getattr(recovery_coordinator, "recover", None)
        ):
            raise TypeError("recovery_coordinator must implement recover")
        self._recovery_coordinator = recovery_coordinator

    def _execute_selected(
        self,
        parent: CandidateRecord,
        hypothesis: HypothesisRecord,
    ) -> None:
        level = self._state.current_level
        round_number = self._state.current_round
        sequence = self._state.executed_candidate_count + 1
        candidate_id = f"cand-{sequence}"
        budget_before = self._budget_snapshot()

        if not self._preflight_invocation(
            self._executor.budget_increment, "candidate_executor"
        ):
            return
        parent_source = self._read_candidate_source(parent)
        request = CandidateExecutionRequest(
            run_id=self._state.run_id,
            sequence=sequence,
            candidate_id=candidate_id,
            level=level,
            round_number=round_number,
            parent_candidate=parent,
            parent_source=parent_source,
            hypothesis=hypothesis,
            budget_before=budget_before,
        )
        try:
            execution = self._executor.execute(request)
            if not isinstance(execution, CandidateExecutionResult):
                raise TypeError("executor returned an invalid result type")
        except CandidateGenerationAbstained as exc:
            self._consume_invocation(self._executor.budget_increment)
            self._counters = self._counters.increment(
                executor_calls=1,
                candidate_generation_abstentions=1,
            )
            self._record_decision(
                event="candidate_generation_abstained",
                action="advance_level_without_candidate",
                reason=exc.reason_code,
                hypothesis_id=hypothesis.hypothesis_id,
                metadata={
                    "candidate_id_reserved_but_not_created": candidate_id,
                    "error_code": exc.error_code,
                    "detail_codes": list(exc.detail_codes),
                    "best_correct_candidate_id": self._state.best_correct_candidate_id,
                    "automatic_retry": False,
                    "candidate_created": False,
                    "qualification_started": False,
                },
            )
            self._advance_level("candidate_generation_abstained")
            return
        except Exception as exc:
            self._consume_invocation(self._executor.budget_increment)
            self._counters = self._counters.increment(executor_calls=1)
            self._terminal_error(
                "candidate_executor_error",
                type(exc).__name__,
                hypothesis_id=hypothesis.hypothesis_id,
            )
            return

        self._consume_invocation(self._executor.budget_increment)
        self._counters = self._counters.increment(executor_calls=1)
        budget_after = self._budget_snapshot()
        if execution.qualification.candidate_id != candidate_id:
            self._terminal_error(
                "qualification_linkage_error",
                "executor qualification candidate_id mismatch",
                hypothesis_id=hypothesis.hypothesis_id,
            )
            return

        generated = CandidateRecord(
            candidate_id=candidate_id,
            sequence=sequence,
            parent_candidate_id=parent.candidate_id,
            hypothesis_id=hypothesis.hypothesis_id,
            level=level,
            source_sha256=sha256(execution.source).hexdigest(),
            source_artifact=f"candidates/{candidate_id}/source.cpp",
            status=CandidateStatus.GENERATED,
            budget_before=budget_before,
            created_at_utc=self._timestamp(),
        )
        self._writer.write_candidate_source(generated, execution.source)
        qualification = replace(
            execution.qualification,
            budget_before=budget_before,
            budget_after=budget_after,
        )
        terminal_candidate = qualification.apply_to_candidate(generated)
        source_updates, source_decision = self._decide_candidate(
            terminal_candidate,
            qualification.status,
            level=level,
            round_number=round_number,
        )

        recovery = None
        can_recover = (
            qualification.status is QualificationStatus.REJECTED
            and self._recovery_coordinator is not None
            and sequence < self._policy.max_executed_candidates
        )
        if can_recover:
            source_for_recovery = replace(
                terminal_candidate,
                decision={
                    **dict(terminal_candidate.decision),
                    **source_decision,
                },
            )
            recovery = self._recovery_coordinator.recover(
                OptimizeCandidateRecoveryRequest(
                    run_id=self._state.run_id,
                    source_candidate=source_for_recovery,
                    source=execution.source,
                    interface_source=parent_source,
                    source_qualification=qualification,
                    hypothesis=hypothesis,
                    recovery_candidate_id=f"cand-{sequence + 1}",
                    recovery_sequence=sequence + 1,
                    budget_before=self._budget_snapshot(),
                    created_at_utc=self._timestamp(),
                )
            )

        source_recovery_metadata = (
            {
                "recovery_status": (
                    "not_configured"
                    if self._recovery_coordinator is None
                    else (
                        "not_attempted_candidate_limit"
                        if (
                            qualification.status is QualificationStatus.REJECTED
                            and sequence >= self._policy.max_executed_candidates
                        )
                        else "ineligible"
                    )
                )
            }
            if recovery is None
            else recovery.lineage_metadata()
        )
        terminal_candidate = replace(
            terminal_candidate,
            decision={
                **dict(terminal_candidate.decision),
                **source_decision,
                **source_recovery_metadata,
            },
        )
        self._candidates[candidate_id] = terminal_candidate

        final_id = candidate_id
        final_candidate = terminal_candidate
        final_qualification = qualification
        final_decision = source_decision

        if recovery is not None and recovery.descendant_created:
            assert recovery.recovery_candidate_id is not None
            assert recovery.source is not None
            assert recovery.qualification is not None
            recovery_sequence = sequence + 1
            recovery_id = recovery.recovery_candidate_id
            repaired = CandidateRecord(
                candidate_id=recovery_id,
                sequence=recovery_sequence,
                parent_candidate_id=candidate_id,
                hypothesis_id=hypothesis.hypothesis_id,
                level=level,
                source_sha256=sha256(recovery.source).hexdigest(),
                source_artifact=f"candidates/{recovery_id}/source.cpp",
                status=CandidateStatus.GENERATED,
                budget_before=recovery.qualification.budget_before,
                created_at_utc=self._timestamp(),
            )
            self._writer.write_candidate_source(repaired, recovery.source)
            repaired_terminal = recovery.qualification.apply_to_candidate(repaired)
            repaired_updates, repaired_decision = self._decide_candidate(
                repaired_terminal,
                recovery.qualification.status,
                level=level,
                round_number=round_number,
            )
            repaired_terminal = replace(
                repaired_terminal,
                decision={
                    **dict(repaired_terminal.decision),
                    **repaired_decision,
                    **recovery.lineage_metadata(),
                    "recovery_of": candidate_id,
                    "recovery_attempt": 1,
                    "recovery_source_sha256": terminal_candidate.source_sha256,
                    "recovery_repaired_sha256": repaired.source_sha256,
                    "recovery_hypothesis_preserved": (
                        repaired.hypothesis_id == terminal_candidate.hypothesis_id
                    ),
                },
            )
            self._candidates[recovery_id] = repaired_terminal
            self._state = replace(
                self._state,
                executed_candidate_count=recovery_sequence,
                **repaired_updates,
            )
            final_id = recovery_id
            final_candidate = repaired_terminal
            final_qualification = recovery.qualification
            final_decision = repaired_decision
        else:
            self._state = replace(
                self._state,
                executed_candidate_count=sequence,
                **source_updates,
            )

        if self._state.terminal_status is None:
            self._advance_after_executed_round()
        self._checkpoint()

        self._record_decision(
            event="candidate_terminal",
            action=source_decision["optimizer_action"],
            reason=source_decision["optimizer_reason"],
            candidate_id=candidate_id,
            hypothesis_id=hypothesis.hypothesis_id,
            level=level,
            round_number=round_number,
            metadata={
                "qualification_status": qualification.status.value,
                "candidate_status": terminal_candidate.status.value,
                **source_recovery_metadata,
            },
        )
        if final_id != candidate_id:
            self._record_decision(
                event="candidate_recovery_terminal",
                action=final_decision["optimizer_action"],
                reason=final_decision["optimizer_reason"],
                candidate_id=final_id,
                hypothesis_id=hypothesis.hypothesis_id,
                level=level,
                round_number=round_number,
                metadata={
                    "qualification_status": final_qualification.status.value,
                    "candidate_status": final_candidate.status.value,
                    "source_candidate_id": candidate_id,
                    "best_correct_candidate_id": self._state.best_correct_candidate_id,
                    "best_ppa_candidate_id": self._state.best_ppa_candidate_id,
                    "next_level": self._state.current_level.value,
                    "next_round": self._state.current_round,
                    "terminal_status": (
                        None
                        if self._state.terminal_status is None
                        else self._state.terminal_status.value
                    ),
                },
            )

    def _uses_network(self) -> bool:
        return bool(
            super()._uses_network()
            or (
                self._recovery_coordinator is not None
                and getattr(self._recovery_coordinator, "uses_network", False)
            )
        )

    def _uses_vitis(self) -> bool:
        return bool(
            super()._uses_vitis()
            or (
                self._recovery_coordinator is not None
                and getattr(self._recovery_coordinator, "uses_vitis", False)
            )
        )


def empty_optimize_recovery_summary() -> dict[str, Any]:
    return {
        "schema_version": OPTIMIZE_RECOVERY_SCHEMA_VERSION,
        "policy": "p4-0b-r-bounded-optimize-candidate-recovery",
        "max_recoveries_per_root_candidate": 1,
        "repairable_stages": ["preflight", "csynth"],
        "public_csim_repair": False,
        "hidden_repair": False,
        "ppa_repair": False,
        "eligible": 0,
        "attempted": 0,
        "validated": 0,
        "validation_failed": 0,
        "budget_blocked": 0,
        "provider_error": 0,
        "response_rejected": 0,
        "validator_error": 0,
        "ineligible": 0,
    }


def _safe_reason_tuple(value, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("reason_codes must be a sequence")
    result = tuple(dict.fromkeys(str(item).strip() for item in value))
    if not result and not allow_empty:
        raise ValueError("reason_codes must not be empty")
    for item in result:
        if not re_safe_reason(item):
            raise ValueError("reason_codes must contain safe tokens")
    return result


def re_safe_reason(value: str) -> bool:
    return bool(value) and value[0].islower() and all(
        character.islower() or character.isdigit() or character == "_"
        for character in value
    )


def _json_mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    copied = json.loads(
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True)
    )
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def _required_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _source_sha(value: str) -> str:
    normalized = "\n".join(line.rstrip() for line in value.strip().splitlines())
    return sha256(normalized.encode("utf-8")).hexdigest()
