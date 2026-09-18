"""Bounded campaign execution with typed progress and heartbeat evidence."""

from .runner import (
    CampaignCase,
    CampaignInvariantError,
    CampaignManifest,
    CampaignResult,
    CampaignRunner,
    load_campaign_manifest,
)

__all__ = [
    "CampaignCase",
    "CampaignInvariantError",
    "CampaignManifest",
    "CampaignResult",
    "CampaignRunner",
    "load_campaign_manifest",
]

from .r5_protocol import (
    ARM_SEMANTICS,
    R5Arm,
    R5CampaignManifest,
    R5ProtocolError,
    estimate_upper_bound,
    validate_arm_diff,
)
from .r5_reducer import (
    R5ArmObservation,
    R5CampaignReduction,
    R5ReductionError,
    reduce_observations,
)
from .r5_runner import (
    R5CampaignError,
    R5CampaignExecutor,
    R5CampaignRun,
    R5CampaignRunner,
    R5CaseSpec,
)
__all__.extend([
    "ARM_SEMANTICS", "R5Arm", "R5CampaignManifest", "R5ProtocolError",
    "estimate_upper_bound", "validate_arm_diff", "R5ArmObservation",
    "R5CampaignReduction", "R5ReductionError", "reduce_observations",
    "R5CampaignError", "R5CampaignExecutor", "R5CampaignRun",
    "R5CampaignRunner", "R5CaseSpec",
])
