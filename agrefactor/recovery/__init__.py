"""Typed recovery policy, timeout ownership, and advisory contracts."""

from .advisory import (
    AdvisoryConfidence,
    AdvisoryOwner,
    AdvisoryRepairScope,
    DiagnosticAdvisory,
    DiagnosticAdvisoryRequest,
    DiagnosticAdvisor,
    validate_advisory_result,
)
from .policy import (
    RecoveryAction,
    RecoveryAuthority,
    RecoveryBudgetBlockedError,
    RecoveryDecision,
    RecoveryDecisionStatus,
    RecoveryDeniedError,
    RecoveryLedger,
    RecoveryLedgerEvent,
    RecoveryLimits,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
    conservative_v1_policy,
    default_restart_reserve,
)
from .timeout import (
    TimeoutClass,
    TimeoutClassification,
    TimeoutOwner,
    classify_public_timeout,
)
from .quota import (
    EffectiveRepairQuotaSummary,
    build_effective_repair_quota_summary,
)
from .shadow_advisor import (
    CalibrationProtocol,
    CalibrationReport,
    ProviderBackedShadowDiagnosticAdvisor,
    ShadowAccounting,
    ShadowAuditArtifact,
    ShadowEquivalenceResult,
    ShadowInputRejected,
    ShadowOutputRejected,
    ShadowReserve,
    build_shadow_request,
    compare_shadow_equivalence,
    diagnostic_event_from_dict,
    evaluate_calibration,
    freeze_calibration_protocol,
    run_shadow_diagnostics,
)

__all__ = [
    "AdvisoryConfidence",
    "AdvisoryOwner",
    "AdvisoryRepairScope",
    "DiagnosticAdvisory",
    "DiagnosticAdvisoryRequest",
    "DiagnosticAdvisor",
    "RecoveryAction",
    "RecoveryAuthority",
    "RecoveryBudgetBlockedError",
    "RecoveryDecision",
    "RecoveryDecisionStatus",
    "RecoveryDeniedError",
    "RecoveryLedger",
    "RecoveryLedgerEvent",
    "RecoveryLimits",
    "RecoveryPolicy",
    "RecoveryRequest",
    "RecoveryRole",
    "RecoveryStage",
    "TimeoutClass",
    "TimeoutClassification",
    "TimeoutOwner",
    "classify_public_timeout",
    "conservative_v1_policy",
    "default_restart_reserve",
    "validate_advisory_result",
    "EffectiveRepairQuotaSummary",
    "build_effective_repair_quota_summary",
    "CalibrationProtocol",
    "CalibrationReport",
    "ProviderBackedShadowDiagnosticAdvisor",
    "ShadowAccounting",
    "ShadowAuditArtifact",
    "ShadowEquivalenceResult",
    "ShadowInputRejected",
    "ShadowOutputRejected",
    "ShadowReserve",
    "build_shadow_request",
    "compare_shadow_equivalence",
    "diagnostic_event_from_dict",
    "evaluate_calibration",
    "freeze_calibration_protocol",
    "run_shadow_diagnostics",
]

# R3_CONDITIONED_MEMORY_GATE_EXPORTS
from .memory_gate import (
    ApplicabilityGate, DiagnosticEpisode, EpisodeOutcome, EpisodeStore,
    GateDecision, GateResult, MemoryContractError, PatternLifecycle,
    RepairPatternRevision, classify_outcome,
)
__all__.extend([
    "ApplicabilityGate", "DiagnosticEpisode", "EpisodeOutcome",
    "EpisodeStore", "GateDecision", "GateResult",
    "MemoryContractError", "PatternLifecycle",
    "RepairPatternRevision", "classify_outcome",
])

# R4_GATED_CANDIDATE_REPAIR_EXPORTS
from .gated_candidate_repair import (
    R4CanaryManifest,
    R4CandidateRepairAuthorization,
    R4CandidateRepairController,
    R4ContractError,
    R4ExecutionInput,
    R4KillSwitchState,
    R4Outcome,
    R4RevisionSafetyRecord,
    R4RunResult,
)
__all__.extend([
    "R4CanaryManifest", "R4CandidateRepairAuthorization",
    "R4CandidateRepairController", "R4ContractError", "R4ExecutionInput",
    "R4KillSwitchState", "R4Outcome", "R4RevisionSafetyRecord", "R4RunResult",
])
