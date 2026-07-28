"""Safe optimizer state and checkpoint foundations."""

from .checkpoint import (
    OptimizerCheckpointSnapshot,
    OptimizerCheckpointWriter,
)
from .state import (
    SCHEMA_VERSION,
    CandidateRecord,
    CandidateStatus,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
    OptimizerState,
    OptimizerTerminalStatus,
    candidate_index_from_dict,
    candidate_index_to_dict,
    normalize_candidate_index,
)

__all__ = [
    "SCHEMA_VERSION",
    "CandidateRecord",
    "CandidateStatus",
    "HypothesisRecord",
    "HypothesisRisk",
    "OptimizationLevel",
    "OptimizerCheckpointSnapshot",
    "OptimizerCheckpointWriter",
    "OptimizerState",
    "OptimizerTerminalStatus",
    "candidate_index_from_dict",
    "candidate_index_to_dict",
    "normalize_candidate_index",
]
