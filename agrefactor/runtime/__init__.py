'''Runtime services such as budgets, traces, and orchestration.'''

from __future__ import annotations

from importlib import import_module
from typing import Any

from .budget import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    BudgetUsage,
)
from .budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
    HARD_BUDGET_FIELDS,
    EffectiveRunBudget,
    RunBudgetProfile,
)
from .execution_identity import (
    EXECUTION_IDENTITY_SCHEMA_VERSION,
    build_execution_identity_bundle,
    canonical_json_sha256,
    execution_identity_summary,
    file_sha256,
    finalize_execution_identity_bundle,
    validate_execution_identity_bundle,
    write_execution_identity_bundle,
)
from .runner import (
    PhaseHandler,
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
    RunArtifactFile,
    RunArtifactWriteResult,
    RunArtifactWriter,
    RunResult,
    RunStatus,
    UnifiedRunner,
)
from .trace import (
    TraceEvent,
    TraceEvidenceView,
    TraceRecorder,
)


_LAZY_EXPORTS = {
    "PreflightStageInputs": (
        ".preflight_stage",
        "PreflightStageInputs",
    ),
    "PreflightValidationStageHandler": (
        ".preflight_stage",
        "PreflightValidationStageHandler",
    ),
    "read_preflight_invocation_summary": (
        ".preflight_stage",
        "read_preflight_invocation_summary",
    ),
    "CsynthStageInputs": (
        ".csynth_stage",
        "CsynthStageInputs",
    ),
    "CsynthValidationStageHandler": (
        ".csynth_stage",
        "CsynthValidationStageHandler",
    ),
    "read_csynth_invocation_summary": (
        ".csynth_stage",
        "read_csynth_invocation_summary",
    ),
    "CsimStageInputs": (
        ".csim_stage",
        "CsimStageInputs",
    ),
    "CsimValidationStageHandler": (
        ".csim_stage",
        "CsimValidationStageHandler",
    ),
    "read_csim_invocation_summary": (
        ".csim_stage",
        "read_csim_invocation_summary",
    ),
    "CandidateRepairOrchestrationRequest": (
        ".candidate_repair_integration",
        "CandidateRepairOrchestrationRequest",
    ),
    "CandidateRepairOrchestrationResult": (
        ".candidate_repair_integration",
        "CandidateRepairOrchestrationResult",
    ),
    "CandidateRepairOrchestrationStatus": (
        ".candidate_repair_integration",
        "CandidateRepairOrchestrationStatus",
    ),
    "CandidateRepairValidationOrchestrator": (
        ".candidate_repair_integration",
        "CandidateRepairValidationOrchestrator",
    ),
    "CandidateValidationHandlerFactory": (
        ".candidate_repair_integration",
        "CandidateValidationHandlerFactory",
    ),
    "CandidateValidationPlanRequest": (
        ".candidate_repair_integration",
        "CandidateValidationPlanRequest",
    ),
    "LocalCandidateValidationHandlerFactory": (
        ".candidate_repair_integration",
        "LocalCandidateValidationHandlerFactory",
    ),
    "CandidateRepairPhase": (
        ".repair_phase",
        "CandidateRepairPhase",
    ),
    "CandidateRepairPhaseArtifactWriteResult": (
        ".repair_phase",
        "CandidateRepairPhaseArtifactWriteResult",
    ),
    "CandidateRepairPhaseArtifactWriter": (
        ".repair_phase",
        "CandidateRepairPhaseArtifactWriter",
    ),
    "CandidateRepairPhaseConfig": (
        ".repair_phase",
        "CandidateRepairPhaseConfig",
    ),
    "build_candidate_repair_phase": (
        ".repair_phase",
        "build_candidate_repair_phase",
    ),
    "ValidationExecutionOutcome": (
        ".validation_orchestrator",
        "ValidationExecutionOutcome",
    ),
    "ValidationOrchestrationResult": (
        ".validation_orchestrator",
        "ValidationOrchestrationResult",
    ),
    "ValidationOrchestrator": (
        ".validation_orchestrator",
        "ValidationOrchestrator",
    ),
    "ValidationStageHandler": (
        ".validation_orchestrator",
        "ValidationStageHandler",
    ),
    "ValidationStepRecord": (
        ".validation_orchestrator",
        "ValidationStepRecord",
    ),
}


__all__ = [
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetManager",
    "BudgetUsage",
    "DEFAULT_SOURCE_RUN_BUDGET_PROFILE",
    "EffectiveRunBudget",
    "HARD_BUDGET_FIELDS",
    "RunBudgetProfile",
    "EXECUTION_IDENTITY_SCHEMA_VERSION",
    "build_execution_identity_bundle",
    "canonical_json_sha256",
    "execution_identity_summary",
    "file_sha256",
    "finalize_execution_identity_bundle",
    "validate_execution_identity_bundle",
    "write_execution_identity_bundle",
    "CandidateRepairOrchestrationRequest",
    "CandidateRepairOrchestrationResult",
    "CandidateRepairOrchestrationStatus",
    "CandidateRepairValidationOrchestrator",
    "CandidateValidationHandlerFactory",
    "CandidateValidationPlanRequest",
    "CandidateRepairPhase",
    "CandidateRepairPhaseArtifactWriteResult",
    "CandidateRepairPhaseArtifactWriter",
    "CandidateRepairPhaseConfig",
    "LocalCandidateValidationHandlerFactory",
    "CsimStageInputs",
    "CsimValidationStageHandler",
    "CsynthStageInputs",
    "CsynthValidationStageHandler",
    "PhaseHandler",
    "PhaseResult",
    "PhaseStatus",
    "PreflightStageInputs",
    "PreflightValidationStageHandler",
    "RunArtifactFile",
    "RunArtifactWriteResult",
    "RunArtifactWriter",
    "RunContext",
    "RunPhase",
    "RunResult",
    "RunStatus",
    "TraceEvent",
    "TraceEvidenceView",
    "TraceRecorder",
    "read_csim_invocation_summary",
    "read_csynth_invocation_summary",
    "read_preflight_invocation_summary",
    "build_candidate_repair_phase",
    "UnifiedRunner",
    "ValidationExecutionOutcome",
    "ValidationOrchestrationResult",
    "ValidationOrchestrator",
    "ValidationStageHandler",
    "ValidationStepRecord",
]


def __getattr__(name: str) -> Any:
    '''Resolve high-level runtime integrations only when requested.'''

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    module_name, attribute_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    '''Return eager and lazy public runtime names.'''

    return sorted(set(globals()) | set(__all__))
