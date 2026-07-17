"""Execute local Vitis CSYNTH as a validation-stage handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from autogen.agentchat.group import ContextVariables

from agrefactor.evaluation.csynth_artifact_feedback import (
    CsynthArtifactFeedbackEvaluator,
)
from agrefactor.evaluation.csynth_feedback_view import (
    CsynthFeedbackViewAdapter,
)
from agrefactor.evidence import (
    FeedbackOwner,
    FeedbackReport,
)

from .runner import RunContext


CsynthExecutor = Callable[..., tuple[str, str]]


@dataclass(frozen=True, slots=True)
class CsynthStageInputs:
    """Candidate inputs required by one local CSYNTH execution."""

    work_dir: str | os.PathLike[str]
    candidate_code: str
    timelimit: int = 300

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
        if not isinstance(self.candidate_code, str):
            raise TypeError(
                "candidate_code must be a string"
            )
        if not self.candidate_code.strip():
            raise ValueError(
                "candidate_code must not be empty"
            )
        if (
            isinstance(self.timelimit, bool)
            or not isinstance(self.timelimit, int)
        ):
            raise TypeError(
                "timelimit must be an integer"
            )
        if self.timelimit <= 0:
            raise ValueError(
                "timelimit must be positive"
            )

        object.__setattr__(
            self,
            "work_dir",
            Path(raw_work_dir).expanduser(),
        )


class CsynthValidationStageHandler:
    """Run local CSYNTH and return agent-safe feedback."""

    handler_version = 1
    source = "csynth"

    def __init__(
        self,
        inputs: CsynthStageInputs,
        *,
        executor: CsynthExecutor | None = None,
        artifact_evaluator: (
            CsynthArtifactFeedbackEvaluator | None
        ) = None,
        view_adapter: CsynthFeedbackViewAdapter | None = None,
        owner: FeedbackOwner | str = FeedbackOwner.CANDIDATE,
    ) -> None:
        if not isinstance(inputs, CsynthStageInputs):
            raise TypeError(
                "inputs must be CsynthStageInputs"
            )
        if executor is not None and not callable(executor):
            raise TypeError(
                "executor must be callable or null"
            )
        if (
            artifact_evaluator is not None
            and not isinstance(
                artifact_evaluator,
                CsynthArtifactFeedbackEvaluator,
            )
        ):
            raise TypeError(
                "artifact_evaluator must be "
                "CsynthArtifactFeedbackEvaluator or null"
            )
        if (
            view_adapter is not None
            and not isinstance(
                view_adapter,
                CsynthFeedbackViewAdapter,
            )
        ):
            raise TypeError(
                "view_adapter must be "
                "CsynthFeedbackViewAdapter or null"
            )
        try:
            normalized_owner = (
                owner
                if isinstance(owner, FeedbackOwner)
                else FeedbackOwner(str(owner))
            )
        except ValueError as exc:
            raise ValueError(
                f"unsupported CSYNTH feedback owner: {owner!r}"
            ) from exc

        self._inputs = inputs
        self._executor = executor
        self._artifact_evaluator = (
            artifact_evaluator
            or CsynthArtifactFeedbackEvaluator()
        )
        self._view_adapter = (
            view_adapter
            or CsynthFeedbackViewAdapter()
        )
        self._owner = normalized_owner

    @property
    def inputs(self) -> CsynthStageInputs:
        return self._inputs

    def __call__(
        self,
        context: RunContext,
    ) -> FeedbackReport:
        if not isinstance(context, RunContext):
            raise TypeError(
                "context must be a RunContext"
            )

        work_dir = Path(self._inputs.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        variables = ContextVariables(
            data={
                "curr_code": self._inputs.candidate_code,
                "new_kernel_name": (
                    context.task.kernel_name
                ),
                "target_profile": (
                    context.task.target.to_dict()
                ),
            }
        )
        executor = (
            self._executor
            or self._load_default_executor()
        )

        legacy_status: str | None = None
        error_msg = ""
        execution_exception_type: str | None = None

        try:
            result = executor(
                str(work_dir),
                variables,
                self._inputs.timelimit,
                budget=context.budget,
            )
        except Exception as exc:
            execution_exception_type = type(exc).__name__
            if not self._invocation_path().is_file():
                raise
        else:
            legacy_status, error_msg = (
                self._validate_legacy_result(result)
            )

        operator_id = (
            f"{context.run_id}.csynth.operator"
        )
        agent_id = f"{context.run_id}.csynth.agent"

        operator_report = (
            self._artifact_evaluator.evaluate(
                work_dir,
                report_id=operator_id,
                legacy_status=legacy_status,
                error_msg=error_msg,
                owner=self._owner,
            )
        )
        safe_report = self._view_adapter.to_agent_report(
            operator_report,
            report_id=agent_id,
        )
        summary = read_csynth_invocation_summary(
            work_dir
        )

        metadata = dict(safe_report.metadata)
        metadata.update(
            {
                "stage_handler_version": (
                    self.handler_version
                ),
                "shared_budget": True,
                "legacy_status": legacy_status,
                "execution_exception_type": (
                    execution_exception_type
                ),
                "operator_invocation_available": (
                    self._invocation_path().is_file()
                ),
                "physical_execution": (
                    summary is not None
                    and summary.get("execution_status")
                    == "completed"
                ),
                "tool_attempt_counted": (
                    summary is not None
                    and summary.get("budget_status")
                    == "consumed"
                ),
                "target_profile_name": (
                    context.task.target.name
                ),
                "requested_toolchain_version": (
                    context.task.target.toolchain_version
                ),
            }
        )

        source_evidence = dict(
            safe_report.source_evidence
        )
        source_evidence.update(
            {
                "stage_handler": {
                    "version": self.handler_version,
                    "shared_budget": True,
                    "legacy_status": legacy_status,
                    "execution_exception_type": (
                        execution_exception_type
                    ),
                    "physical_execution": (
                        metadata["physical_execution"]
                    ),
                    "tool_attempt_counted": (
                        metadata["tool_attempt_counted"]
                    ),
                }
            }
        )

        return FeedbackReport(
            report_id=safe_report.report_id,
            source=safe_report.source,
            items=safe_report.items,
            source_evidence=source_evidence,
            metadata=metadata,
        )

    def _invocation_path(self) -> Path:
        return (
            Path(self._inputs.work_dir)
            / "csynth_invocation.json"
        )

    @staticmethod
    def _load_default_executor() -> CsynthExecutor:
        from flow.tools.csynth import run_csynth

        return run_csynth

    @staticmethod
    def _validate_legacy_result(
        value: Any,
    ) -> tuple[str, str]:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
        ):
            raise TypeError(
                "CSYNTH executor must return "
                "(status, error_msg)"
            )
        status, error_msg = value
        if not isinstance(status, str):
            raise TypeError(
                "CSYNTH status must be a string"
            )
        if not status.strip():
            raise ValueError(
                "CSYNTH status must not be empty"
            )
        if not isinstance(error_msg, str):
            raise TypeError(
                "CSYNTH error_msg must be a string"
            )
        return status.strip(), error_msg.strip()


def read_csynth_invocation_summary(
    work_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    """Read only non-sensitive CSYNTH invocation fields."""

    path = Path(work_dir) / "csynth_invocation.json"
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
    verification = value.get(
        "toolchain_version_verification"
    )
    target = value.get("target_profile")

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
        "budget_resource": (
            budget.get("resource")
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
        "verification_status": (
            verification.get("status")
            if isinstance(verification, dict)
            else None
        ),
        "requested_version": (
            verification.get("requested")
            if isinstance(verification, dict)
            else None
        ),
        "actual_version": (
            verification.get("actual")
            if isinstance(verification, dict)
            else None
        ),
        "target_profile_name": (
            target.get("name")
            if isinstance(target, dict)
            else None
        ),
        "target_device": (
            target.get("device")
            if isinstance(target, dict)
            else None
        ),
    }
