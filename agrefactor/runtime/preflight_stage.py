"""Execute real testbench preflight as a validation-stage handler."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from agrefactor.evaluation.preflight_feedback import (
    TestbenchPreflightFeedbackAdapter,
)
from agrefactor.evaluation.preflight_feedback_view import (
    TestbenchPreflightFeedbackViewAdapter,
)
from agrefactor.evaluation.testbench_preflight import (
    TestbenchPreflight,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)

from .budget import BudgetExceededError
from .runner import RunContext


@dataclass(frozen=True, slots=True)
class PreflightStageInputs:
    """Source inputs required by one real preflight execution."""

    work_dir: str | os.PathLike[str]
    testbench_code: str
    original_code: str
    candidate_code: str

    def __post_init__(self) -> None:
        try:
            raw_work_dir = os.fspath(self.work_dir)
        except TypeError as exc:
            raise TypeError(
                "work_dir must be a path-like value"
            ) from exc
        if not isinstance(raw_work_dir, str):
            raise TypeError(
                "work_dir must resolve to a string path"
            )
        if not raw_work_dir.strip():
            raise ValueError("work_dir must not be empty")

        object.__setattr__(
            self,
            "work_dir",
            Path(raw_work_dir).expanduser(),
        )
        for field_name in (
            "testbench_code",
            "original_code",
            "candidate_code",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string"
                )
            if not value.strip():
                raise ValueError(
                    f"{field_name} must not be empty"
                )


class PreflightValidationStageHandler:
    """Run preflight and return agent-safe normalized feedback."""

    handler_version = 1
    source = "testbench_preflight"

    def __init__(
        self,
        inputs: PreflightStageInputs,
        *,
        preflight: TestbenchPreflight | None = None,
    ) -> None:
        if not isinstance(inputs, PreflightStageInputs):
            raise TypeError(
                "inputs must be PreflightStageInputs"
            )
        if (
            preflight is not None
            and not isinstance(preflight, TestbenchPreflight)
        ):
            raise TypeError(
                "preflight must be TestbenchPreflight or null"
            )

        self._inputs = inputs
        self._preflight = preflight or TestbenchPreflight()
        self._operator_adapter = (
            TestbenchPreflightFeedbackAdapter()
        )
        self._view_adapter = (
            TestbenchPreflightFeedbackViewAdapter()
        )

    @property
    def inputs(self) -> PreflightStageInputs:
        return self._inputs

    def __call__(
        self,
        context: RunContext,
    ) -> FeedbackReport:
        if not isinstance(context, RunContext):
            raise TypeError(
                "context must be a RunContext"
            )

        operator_id = (
            f"{context.run_id}.preflight.operator"
        )
        agent_id = (
            f"{context.run_id}.preflight.agent"
        )

        try:
            result = self._preflight.compile_and_link(
                work_dir=self._inputs.work_dir,
                testbench_code=self._inputs.testbench_code,
                original_code=self._inputs.original_code,
                candidate_code=self._inputs.candidate_code,
                budget=context.budget,
            )
        except BudgetExceededError as exc:
            return self._budget_report(
                exc,
                report_id=agent_id,
            )

        operator_report = (
            self._operator_adapter.to_operator_report(
                result,
                report_id=operator_id,
            )
        )
        safe_report = self._view_adapter.to_agent_report(
            operator_report,
            report_id=agent_id,
        )

        metadata = dict(safe_report.metadata)
        metadata.update(
            {
                "stage_handler_version": (
                    self.handler_version
                ),
                "physical_execution": True,
                "shared_budget": True,
                "operator_invocation_available": (
                    self._invocation_path().is_file()
                ),
            }
        )
        return FeedbackReport(
            report_id=safe_report.report_id,
            source=safe_report.source,
            items=safe_report.items,
            source_evidence=safe_report.source_evidence,
            metadata=metadata,
        )

    def _budget_report(
        self,
        exc: BudgetExceededError,
        *,
        report_id: str,
    ) -> FeedbackReport:
        item = FeedbackItem(
            feedback_id=f"{report_id}.budget.1",
            stage=FeedbackStage.CONFIGURATION,
            category=FeedbackCategory.BUDGET_EXHAUSTED,
            severity=FeedbackSeverity.ERROR,
            owner=FeedbackOwner.EVALUATOR,
            summary=(
                "Testbench preflight blocked by "
                f"{exc.resource} budget"
            ),
            detail=None,
            source=self.source,
            evidence_ref=None,
            metadata={
                "resource": exc.resource,
                "limit": exc.limit,
                "attempted": exc.attempted,
                "checkpoint": "before_compile_launch",
            },
        )
        invocation_available = (
            self._invocation_path().is_file()
        )
        return FeedbackReport(
            report_id=report_id,
            source=self.source,
            items=(item,),
            source_evidence={
                "redacted": True,
                "budget": {
                    "resource": exc.resource,
                    "limit": exc.limit,
                    "attempted": exc.attempted,
                    "checkpoint": (
                        "before_compile_launch"
                    ),
                },
                "operator_invocation_available": (
                    invocation_available
                ),
            },
            metadata={
                "stage_handler_version": (
                    self.handler_version
                ),
                "evidence_view": "agent_safe",
                "preflight_status": "blocked",
                "preflight_stage": "compile_link",
                "failure_kind": "budget_exhausted",
                "failure_owner": "evaluator",
                "next_action": (
                    "stop_budget_exhausted"
                ),
                "physical_execution": False,
                "shared_budget": True,
                "operator_invocation_available": (
                    invocation_available
                ),
                "category_counts": {
                    FeedbackCategory.BUDGET_EXHAUSTED.value: 1
                },
                "severity_counts": {
                    FeedbackSeverity.ERROR.value: 1
                },
                "owner_counts": {
                    FeedbackOwner.EVALUATOR.value: 1
                },
            },
        )

    def _invocation_path(self) -> Path:
        return (
            Path(self._inputs.work_dir)
            / "testbench_preflight_invocation.json"
        )


def read_preflight_invocation_summary(
    work_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    """Read only non-sensitive preflight execution fields."""

    path = (
        Path(work_dir)
        / "testbench_preflight_invocation.json"
    )
    if not path.is_file():
        return None
    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None

    budget = value.get("budget")
    execution = value.get("execution")
    return {
        "budget_status": (
            budget.get("status")
            if isinstance(budget, dict)
            else None
        ),
        "budget_checkpoint": (
            budget.get("checkpoint")
            if isinstance(budget, dict)
            else None
        ),
        "execution_status": (
            execution.get("status")
            if isinstance(execution, dict)
            else None
        ),
        "execution_returncode": (
            execution.get("returncode")
            if isinstance(execution, dict)
            else None
        ),
        "execution_timeout": (
            execution.get("timeout")
            if isinstance(execution, dict)
            else None
        ),
    }
