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
from .runner import (
    PhaseHandler,
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
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
    "CsimStageInputs",
    "CsimValidationStageHandler",
    "CsynthStageInputs",
    "CsynthValidationStageHandler",
    "PhaseHandler",
    "PhaseResult",
    "PhaseStatus",
    "PreflightStageInputs",
    "PreflightValidationStageHandler",
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
    "UnifiedRunner",
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
