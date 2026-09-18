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
    CalibrationAcceptancePolicy,
    CalibrationCertificate,
    CalibrationProtocol,
    CalibrationReport,
    CalibrationVerification,
    ProviderBackedShadowDiagnosticAdvisor,
    ShadowAccounting,
    ShadowAuditArtifact,
    ShadowEquivalenceResult,
    ShadowInputRejected,
    ShadowOutputRejected,
    ShadowReserve,
    build_shadow_request,
    compare_shadow_equivalence,
    certify_calibration,
    diagnostic_event_from_dict,
    evaluate_calibration,
    freeze_calibration_protocol,
    run_shadow_diagnostics,
    verify_calibrated_advisory,
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
    "CalibrationAcceptancePolicy",
    "CalibrationCertificate",
    "CalibrationProtocol",
    "CalibrationReport",
    "CalibrationVerification",
    "ProviderBackedShadowDiagnosticAdvisor",
    "ShadowAccounting",
    "ShadowAuditArtifact",
    "ShadowEquivalenceResult",
    "ShadowInputRejected",
    "ShadowOutputRejected",
    "ShadowReserve",
    "build_shadow_request",
    "compare_shadow_equivalence",
    "certify_calibration",
    "diagnostic_event_from_dict",
    "evaluate_calibration",
    "freeze_calibration_protocol",
    "run_shadow_diagnostics",
    "verify_calibrated_advisory",
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

# R5_CONTINUAL_MEMORY_EXPORTS
from .episode_ledger import (
    AppendOnlyEpisodeLedger,
    EpisodeLedgerError,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
    canonical_sha256,
)
from .pattern_lifecycle import (
    Lifecycle,
    LifecycleReduction,
    R5LifecyclePolicy,
    R5LifecycleReducer,
    R5PatternRevision,
)
from .r5_authorization import (
    R5AuthorizationError,
    R5AuthorizationMode,
    R5ResearchAuthorization,
)
from .r5_budget import (
    R5BudgetError,
    R5BudgetLedger,
    R5BudgetReservation,
    load_budget_ledger,
    write_budget_ledger,
)
from .r5_memory_payload import (
    MemoryPayloadError,
    R5MemoryPayload,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    memory_payload_manifest_sha256,
    render_candidate_memory_snippets,
)
from .r5_snapshot_builder import (
    R5MemorySnapshot,
    R5SnapshotBuilder,
    SnapshotBoundaryError,
)
__all__.extend([
    "AppendOnlyEpisodeLedger", "EpisodeLedgerError", "R5EpisodeEnvelope",
    "R5EpisodeOutcome", "canonical_sha256", "Lifecycle", "LifecycleReduction",
    "R5LifecyclePolicy", "R5LifecycleReducer", "R5PatternRevision",
    "R5AuthorizationError", "R5AuthorizationMode", "R5ResearchAuthorization",
    "R5BudgetError", "R5BudgetLedger", "R5BudgetReservation",
    "load_budget_ledger", "write_budget_ledger", "MemoryPayloadError",
    "R5MemoryPayload", "R5_MEMORY_PAYLOAD_POLICY_SHA256",
    "memory_payload_manifest_sha256",
    "render_candidate_memory_snippets", "R5MemorySnapshot",
    "R5SnapshotBuilder", "SnapshotBoundaryError",
])

# R4_GATED_CANDIDATE_REPAIR_EXPORTS
from .gated_candidate_repair import (
    R4CanaryManifest,
    R4CandidateRepairAuthorization,
    R4CandidateRepairController,
    R4ContractError,
    R4ExecutionInput,
    R4KillSwitchState,
    R4MutationFailure,
    R4Outcome,
    R4RevisionSafetyRecord,
    R4RunResult,
    R4_CONTROLLER_CONTRACT_SHA256,
    R5CandidateRepairAuthorization,
    R5ExecutionInput,
)
__all__.extend([
    "R4CanaryManifest", "R4CandidateRepairAuthorization",
    "R4CandidateRepairController", "R4ContractError", "R4ExecutionInput",
    "R4KillSwitchState", "R4MutationFailure", "R4Outcome",
    "R4RevisionSafetyRecord", "R4RunResult",
    "R4_CONTROLLER_CONTRACT_SHA256", "R5CandidateRepairAuthorization",
    "R5ExecutionInput",
])


# R4_EPISODE_FOUNDATION_EXPORTS
from .r4_budget import (
    R4BudgetReservation,
    R4ReservePlan,
    build_r4_budget_actual,
    build_r4_reserve_plan,
    build_r4_reserve_plan_from_handlers,
    record_r4_budget_reservation,
)
from .r4_episode import R4RepairEpisode, R4RepairEpisodeReader
from .r4_provenance import (
    R4ProvenanceResult,
    canonical_artifact_sha256,
    validate_r4_provenance,
)
__all__.extend([
    "R4BudgetReservation", "R4ReservePlan", "build_r4_budget_actual",
    "build_r4_reserve_plan", "build_r4_reserve_plan_from_handlers",
    "record_r4_budget_reservation", "R4RepairEpisode",
    "R4RepairEpisodeReader", "R4ProvenanceResult",
    "canonical_artifact_sha256", "validate_r4_provenance",
])
