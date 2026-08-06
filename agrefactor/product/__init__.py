
"""Normal-user product entrypoints built above internal task contracts."""

from .refactor_eligibility import (
    EligibilityStatus,
    OriginalCsynthEvidence,
    RefactorEligibilityReport,
    SourceBoundaryEvidence,
    analyze_source_boundary,
    assess_refactor_eligibility,
    load_original_csynth_evidence,
)
from .run_output import (
    PRODUCT_RUN_SUMMARY_SCHEMA_VERSION,
    CapturedProductStreams,
    ProductOutputMode,
    build_product_summary,
    build_rejection_summary,
    capture_product_streams,
    finalize_product_artifacts,
    render_product_output,
    resolve_output_mode,
    write_rejection_support_artifacts,
)
from .stage3_optimizer import (
    AcceptedOptimizationMaterial,
    ProductOptimizerRequest,
    Stage3ProductOptimizationPhase,
    build_direct_optimization_material,
    write_direct_optimize_execution_identity,
)
from .source_bootstrap import (
    SourceBootstrapPhase,
    SourceBootstrapRequest,
    SourceBootstrapRunResult,
    SourceCommandRejected,
    SourceRunLayout,
    build_test_source_plan,
    run_source_command,
)

__all__ = [
    "PRODUCT_RUN_SUMMARY_SCHEMA_VERSION",
    "AcceptedOptimizationMaterial",
    "ProductOptimizerRequest",
    "Stage3ProductOptimizationPhase",
    "CapturedProductStreams",
    "EligibilityStatus",
    "OriginalCsynthEvidence",
    "ProductOutputMode",
    "RefactorEligibilityReport",
    "SourceBoundaryEvidence",
    "SourceBootstrapPhase",
    "SourceBootstrapRequest",
    "SourceBootstrapRunResult",
    "SourceCommandRejected",
    "SourceRunLayout",
    "analyze_source_boundary",
    "assess_refactor_eligibility",
    "load_original_csynth_evidence",
    "build_direct_optimization_material",
    "build_product_summary",
    "build_rejection_summary",
    "build_test_source_plan",
    "capture_product_streams",
    "finalize_product_artifacts",
    "render_product_output",
    "resolve_output_mode",
    "run_source_command",
    "write_direct_optimize_execution_identity",
    "write_rejection_support_artifacts",
]
