"""Execute public or hidden CSIM suites as one validation stage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from autogen.agentchat.group import ContextVariables

from agrefactor.config import EvaluationSplit
from agrefactor.evaluation.csim_suite import (
    CsimSuiteEvaluationResult,
    CsimSuiteEvaluator,
)
from agrefactor.evaluation.test_evaluation_feedback import (
    TestEvaluationFeedbackAdapter,
)
from agrefactor.evaluation.test_evaluation_feedback_composer import (
    TestEvaluationFeedbackComposer,
)
from agrefactor.evaluation.testbench_preflight import (
    classify_compile_failure,
    infer_failure_owner,
    parse_compiler_diagnostics,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
    TestbenchFailureKind,
)

from .runner import RunContext


_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:[^/\s]+/)+[^/\s]*"
)
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\(?:[^\\\s]+\\)+[^\\\s]*"
)

_KIND_TO_CATEGORY = {
    TestbenchFailureKind.UNDECLARED_TYPE: (
        FeedbackCategory.UNDECLARED_TYPE
    ),
    TestbenchFailureKind.UNDECLARED_SYMBOL: (
        FeedbackCategory.UNDECLARED_SYMBOL
    ),
    TestbenchFailureKind.SYNTAX_ERROR: (
        FeedbackCategory.SYNTAX_ERROR
    ),
    TestbenchFailureKind.LINK_ERROR: (
        FeedbackCategory.LINK_ERROR
    ),
    TestbenchFailureKind.LINKAGE_MISMATCH: (
        FeedbackCategory.LINKAGE_MISMATCH
    ),
}

_TERMINAL_PUBLIC_CATEGORIES = frozenset(
    {
        FeedbackCategory.BUDGET_EXHAUSTED,
        FeedbackCategory.TOOLCHAIN_FAILURE,
        FeedbackCategory.TIMEOUT,
        FeedbackCategory.INVALID_CONFIGURATION,
    }
)


@dataclass(frozen=True, slots=True)
class CsimStageInputs:
    """Explicit source inputs for public and hidden CSIM handlers."""

    work_dir: str | os.PathLike[str]
    original_code: str
    candidate_code: str
    suite_testbench_codes: Mapping[str, str]
    timelimit: int = 60

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

        for field_name in (
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

        mapping = self.suite_testbench_codes
        if not isinstance(mapping, Mapping):
            raise TypeError(
                "suite_testbench_codes must be a mapping"
            )
        normalized: dict[str, str] = {}
        for raw_suite_id, raw_code in mapping.items():
            if not isinstance(raw_suite_id, str):
                raise TypeError(
                    "suite_testbench_codes keys must be strings"
                )
            suite_id = raw_suite_id.strip()
            if not suite_id:
                raise ValueError(
                    "suite_testbench_codes keys must not be empty"
                )
            if not isinstance(raw_code, str):
                raise TypeError(
                    "suite testbench code must be a string"
                )
            if not raw_code.strip():
                raise ValueError(
                    f"suite testbench code is empty: {suite_id}"
                )
            normalized[suite_id] = raw_code

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
        object.__setattr__(
            self,
            "suite_testbench_codes",
            normalized,
        )


class CsimValidationStageHandler:
    """Execute all suites belonging to one evaluation split.

    Public evaluation collects candidate/testbench feedback from every
    declared public suite until an infrastructure, timeout, or budget
    blocker occurs. Hidden evaluation stops at the first blocking result.
    Both paths reuse the exact ``RunContext.budget`` instance.

    Hidden reports remain operator-full and are suppressed later by the
    validation coordinator/orchestrator. Public reports are projected to a
    path-safe agent view before composition.
    """

    handler_version = 1
    semantics_version = 1
    source = "test_evaluation"

    def __init__(
        self,
        inputs: CsimStageInputs,
        *,
        split: EvaluationSplit | str,
        evaluator: CsimSuiteEvaluator | None = None,
        feedback_adapter: (
            TestEvaluationFeedbackAdapter | None
        ) = None,
        composer: (
            TestEvaluationFeedbackComposer | None
        ) = None,
    ) -> None:
        if not isinstance(inputs, CsimStageInputs):
            raise TypeError(
                "inputs must be CsimStageInputs"
            )
        try:
            normalized_split = (
                split
                if isinstance(split, EvaluationSplit)
                else EvaluationSplit(str(split))
            )
        except ValueError as exc:
            raise ValueError(
                f"unsupported CSIM split: {split!r}"
            ) from exc

        if (
            evaluator is not None
            and not isinstance(evaluator, CsimSuiteEvaluator)
        ):
            raise TypeError(
                "evaluator must be CsimSuiteEvaluator or null"
            )
        if (
            feedback_adapter is not None
            and not isinstance(
                feedback_adapter,
                TestEvaluationFeedbackAdapter,
            )
        ):
            raise TypeError(
                "feedback_adapter must be "
                "TestEvaluationFeedbackAdapter or null"
            )
        if (
            composer is not None
            and not isinstance(
                composer,
                TestEvaluationFeedbackComposer,
            )
        ):
            raise TypeError(
                "composer must be "
                "TestEvaluationFeedbackComposer or null"
            )

        self._inputs = inputs
        self._split = normalized_split
        self._evaluator = evaluator or CsimSuiteEvaluator()
        self._feedback_adapter = (
            feedback_adapter
            or TestEvaluationFeedbackAdapter()
        )
        self._composer = (
            composer
            or TestEvaluationFeedbackComposer()
        )

    @property
    def split(self) -> EvaluationSplit:
        return self._split

    @property
    def inputs(self) -> CsimStageInputs:
        return self._inputs

    def __call__(
        self,
        context: RunContext,
    ) -> FeedbackReport:
        if not isinstance(context, RunContext):
            raise TypeError(
                "context must be a RunContext"
            )

        suites = tuple(
            suite
            for suite in context.task.test_suites
            if suite.split is self._split
        )
        if not suites:
            raise ValueError(
                f"task has no {self._split.value} suites"
            )

        missing_codes = tuple(
            suite.suite_id
            for suite in suites
            if suite.suite_id
            not in self._inputs.suite_testbench_codes
        )
        if missing_codes:
            raise ValueError(
                "missing testbench code for suites: "
                + ", ".join(missing_codes)
            )

        component_reports: list[FeedbackReport] = []
        attempted_suite_ids: list[str] = []
        stop_reason: str | None = None

        for index, suite in enumerate(suites, start=1):
            attempted_suite_ids.append(suite.suite_id)
            suite_work_dir = self._suite_work_dir(index)
            suite_work_dir.mkdir(
                parents=True,
                exist_ok=True,
            )
            variables = ContextVariables(
                data={
                    "orig_code": self._inputs.original_code,
                    "curr_code": self._inputs.candidate_code,
                    "testbench": (
                        self._inputs.suite_testbench_codes[
                            suite.suite_id
                        ]
                    ),
                }
            )
            component_id = (
                f"{context.run_id}.{self._split.value}."
                f"suite.{index}"
            )

            try:
                result = self._evaluator.evaluate(
                    work_dir=suite_work_dir,
                    context_variables=variables,
                    suite=suite,
                    timelimit=self._inputs.timelimit,
                    budget=context.budget,
                    trace=None,
                )
            except Exception as exc:
                invocation = self._read_invocation(
                    suite_work_dir
                    / "csim_invocation.json"
                )
                if invocation is None:
                    raise
                report = self._exception_report(
                    invocation=invocation,
                    suite=suite,
                    report_id=component_id,
                    exception_type=type(exc).__name__,
                )
            else:
                report = self._result_report(
                    result,
                    report_id=component_id,
                )

            component_reports.append(report)
            stop_reason = self._stop_reason(report)
            if stop_reason is not None:
                break

        composed = self._composer.compose(
            reports=tuple(component_reports),
            report_id=(
                f"{context.run_id}.{self._split.value}."
                "evaluation"
            ),
            split=self._split,
        )

        metadata = dict(composed.metadata)
        metadata.update(
            {
                "stage_handler_version": (
                    self.handler_version
                ),
                "semantics_version": (
                    self.semantics_version
                ),
                "shared_budget": True,
                "declared_suite_ids": [
                    suite.suite_id for suite in suites
                ],
                "attempted_suite_ids": (
                    attempted_suite_ids
                ),
                "declared_suite_count": len(suites),
                "attempted_suite_count": len(
                    attempted_suite_ids
                ),
                "stopped_early": (
                    len(attempted_suite_ids) < len(suites)
                ),
                "stop_reason": stop_reason,
                "execution_policy": (
                    "collect_public_until_terminal"
                    if self._split
                    is EvaluationSplit.PUBLIC
                    else "hidden_fail_fast"
                ),
                "suite_work_dir_layout": (
                    f"{self._split.value}/suite_NNN"
                ),
            }
        )

        source_evidence = dict(
            composed.source_evidence
        )
        source_evidence["stage_handler"] = {
            "version": self.handler_version,
            "semantics_version": self.semantics_version,
            "shared_budget": True,
            "split": self._split.value,
            "declared_suite_count": len(suites),
            "attempted_suite_count": len(
                attempted_suite_ids
            ),
            "stopped_early": (
                len(attempted_suite_ids) < len(suites)
            ),
            "stop_reason": stop_reason,
        }

        return FeedbackReport(
            report_id=composed.report_id,
            source=composed.source,
            items=composed.items,
            source_evidence=source_evidence,
            metadata=metadata,
        )

    def _result_report(
        self,
        result: CsimSuiteEvaluationResult,
        *,
        report_id: str,
    ) -> FeedbackReport:
        if not isinstance(
            result,
            CsimSuiteEvaluationResult,
        ):
            raise TypeError(
                "CSIM evaluator returned an invalid result"
            )

        if self._split is EvaluationSplit.PUBLIC:
            base = self._feedback_adapter.to_agent_report(
                result.evidence,
                report_id=f"{report_id}.agent",
            )
        else:
            base = self._feedback_adapter.to_operator_report(
                result.evidence,
                report_id=f"{report_id}.operator",
            )

        normalized = self._apply_result_semantics(
            base,
            result,
        )
        if self._split is EvaluationSplit.PUBLIC:
            return self._sanitize_public_report(
                normalized
            )
        return normalized

    def _apply_result_semantics(
        self,
        report: FeedbackReport,
        result: CsimSuiteEvaluationResult,
    ) -> FeedbackReport:
        if not report.items:
            return report

        category: FeedbackCategory | None = None
        owner: FeedbackOwner | None = None
        stage: FeedbackStage | None = None
        metadata_update: dict[str, Any] = {
            "runtime_semantics_version": (
                self.semantics_version
            ),
            "legacy_status": result.legacy_status,
        }

        if result.legacy_status == "csim_failed":
            category = FeedbackCategory.FUNCTIONAL_MISMATCH
            owner = FeedbackOwner.CANDIDATE
            stage = FeedbackStage.CSIM
        elif result.legacy_status == "tb_compile_failed":
            default_kind = classify_compile_failure(
                result.diagnostic
            )
            diagnostics = parse_compiler_diagnostics(
                result.diagnostic,
                default_kind=default_kind,
            )
            inferred_owner = infer_failure_owner(
                diagnostics
            )
            category = _KIND_TO_CATEGORY.get(
                default_kind,
                FeedbackCategory.UNKNOWN,
            )
            try:
                owner = FeedbackOwner(
                    inferred_owner.value
                )
            except ValueError:
                owner = FeedbackOwner.UNKNOWN
            stage = FeedbackStage.COMPILE
            metadata_update["compiler_diagnostics"] = [
                {
                    "kind": item.kind.value,
                    "message": item.message,
                    "file": (
                        Path(item.file).name
                        if item.file
                        else None
                    ),
                    "line": item.line,
                    "column": item.column,
                }
                for item in diagnostics
            ]
        elif result.evidence.timed_out:
            category = FeedbackCategory.TIMEOUT
            owner = FeedbackOwner.UNKNOWN
        elif any(
            item.category
            is FeedbackCategory.TOOLCHAIN_FAILURE
            for item in report.items
        ):
            owner = FeedbackOwner.TOOLCHAIN

        items = tuple(
            self._copy_item(
                item,
                category=category,
                owner=owner,
                stage=stage,
                metadata_update=metadata_update,
            )
            for item in report.items
        )

        metadata = dict(report.metadata)
        metadata.update(
            {
                "runtime_semantics_version": (
                    self.semantics_version
                ),
                "legacy_status": result.legacy_status,
            }
        )
        return FeedbackReport(
            report_id=report.report_id,
            source=report.source,
            items=items,
            source_evidence=report.source_evidence,
            metadata=metadata,
        )

    def _exception_report(
        self,
        *,
        invocation: Mapping[str, Any],
        suite: Any,
        report_id: str,
        exception_type: str,
    ) -> FeedbackReport:
        budget = invocation.get("budget")
        compile_execution = self._execution_summary(
            invocation.get("compile_execution")
        )
        simulation_execution = self._execution_summary(
            invocation.get("simulation_execution")
        )

        category = FeedbackCategory.UNKNOWN
        owner = FeedbackOwner.UNKNOWN
        stage = FeedbackStage.TEST
        summary = "CSIM executor raised an evidenced error"

        if (
            isinstance(budget, Mapping)
            and budget.get("status") == "blocked"
        ):
            category = FeedbackCategory.BUDGET_EXHAUSTED
            owner = FeedbackOwner.EVALUATOR
            stage = FeedbackStage.CONFIGURATION
            summary = "CSIM evaluation blocked by budget"
        elif compile_execution.get("status") == "launch_error":
            category = FeedbackCategory.TOOLCHAIN_FAILURE
            owner = FeedbackOwner.TOOLCHAIN
            stage = FeedbackStage.COMPILE
            summary = "CSIM compilation could not be launched"
        elif simulation_execution.get("status") == "launch_error":
            category = FeedbackCategory.TOOLCHAIN_FAILURE
            owner = FeedbackOwner.TOOLCHAIN
            stage = FeedbackStage.CSIM
            summary = "CSIM simulation could not be launched"

        evidence_view = (
            "agent_safe"
            if self._split is EvaluationSplit.PUBLIC
            else "operator_full"
        )
        item = FeedbackItem(
            feedback_id=f"{report_id}.exception.1",
            stage=stage,
            category=category,
            severity=FeedbackSeverity.FATAL,
            owner=owner,
            summary=summary,
            detail=None,
            source=self.source,
            evidence_ref=None,
            metadata={
                "suite_id": suite.suite_id,
                "evaluation_split": (
                    self._split.value
                ),
                "exception_type": exception_type,
                "compile_execution": compile_execution,
                "simulation_execution": (
                    simulation_execution
                ),
                "budget": self._budget_summary(
                    budget
                ),
            },
        )

        if self._split is EvaluationSplit.PUBLIC:
            source_evidence: Mapping[str, Any] = {
                "suite": {
                    "suite_id": suite.suite_id,
                    "suite_version": suite.suite_version,
                    "split": self._split.value,
                    "case_count": suite.case_count,
                    "feedback_visible_to_agent": True,
                },
                "exception_type": exception_type,
                "compile_execution": compile_execution,
                "simulation_execution": (
                    simulation_execution
                ),
                "budget": self._budget_summary(
                    budget
                ),
                "redacted": False,
            }
        else:
            source_evidence = {
                "suite": suite.to_dict(),
                "invocation": dict(invocation),
                "exception_type": exception_type,
                "redacted": False,
            }

        return FeedbackReport(
            report_id=(
                f"{report_id}.agent"
                if self._split
                is EvaluationSplit.PUBLIC
                else f"{report_id}.operator"
            ),
            source=self.source,
            items=(item,),
            source_evidence=source_evidence,
            metadata={
                "evidence_view": evidence_view,
                "suite_id": suite.suite_id,
                "suite_version": suite.suite_version,
                "evaluation_split": (
                    self._split.value
                ),
                "feedback_visible_to_agent": (
                    self._split
                    is EvaluationSplit.PUBLIC
                ),
                "evaluation_status": "error",
                "passed_cases": 0,
                "failed_cases": 0,
                "evaluated_cases": 0,
                "timed_out": False,
                "return_code": None,
                "source_redacted": False,
                "operator_paths_redacted": (
                    self._split
                    is EvaluationSplit.PUBLIC
                ),
                "runtime_semantics_version": (
                    self.semantics_version
                ),
            },
        )

    @classmethod
    def _sanitize_public_report(
        cls,
        report: FeedbackReport,
    ) -> FeedbackReport:
        items = tuple(
            FeedbackItem(
                feedback_id=item.feedback_id,
                stage=item.stage,
                category=item.category,
                severity=item.severity,
                owner=item.owner,
                summary=cls._sanitize_text(
                    item.summary
                ),
                detail=(
                    cls._sanitize_text(item.detail)
                    if item.detail is not None
                    else None
                ),
                source=item.source,
                evidence_ref=None,
                metadata=cls._sanitize_value(
                    item.metadata
                ),
            )
            for item in report.items
        )

        source_evidence = cls._sanitize_value(
            report.source_evidence
        )
        if isinstance(source_evidence, dict):
            cls._remove_sensitive_keys(
                source_evidence
            )

        metadata = cls._sanitize_value(
            report.metadata
        )
        if not isinstance(metadata, dict):
            raise TypeError(
                "feedback metadata did not normalize to an object"
            )
        metadata["operator_paths_redacted"] = True

        return FeedbackReport(
            report_id=report.report_id,
            source=report.source,
            items=items,
            source_evidence=source_evidence,
            metadata=metadata,
        )

    @classmethod
    def _remove_sensitive_keys(
        cls,
        value: dict[str, Any],
    ) -> None:
        forbidden = {
            "artifacts",
            "testbench_path",
            "work_dir",
            "command",
            "compile_command",
            "simulation_command",
            "resolved_executable",
        }
        for key in tuple(value):
            if key in forbidden:
                value.pop(key, None)
                continue
            item = value[key]
            if isinstance(item, dict):
                cls._remove_sensitive_keys(item)
            elif isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict):
                        cls._remove_sensitive_keys(
                            entry
                        )

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._sanitize_text(value)
        if isinstance(value, list):
            return [
                cls._sanitize_value(item)
                for item in value
            ]
        if isinstance(value, tuple):
            return [
                cls._sanitize_value(item)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_value(item)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _sanitize_text(value: str) -> str:
        text = _WINDOWS_PATH_RE.sub(
            "<PATH>",
            value,
        )
        return _POSIX_PATH_RE.sub(
            "<PATH>",
            text,
        )

    @staticmethod
    def _copy_item(
        item: FeedbackItem,
        *,
        category: FeedbackCategory | None,
        owner: FeedbackOwner | None,
        stage: FeedbackStage | None,
        metadata_update: Mapping[str, Any],
    ) -> FeedbackItem:
        metadata = dict(item.metadata)
        metadata.update(metadata_update)
        return FeedbackItem(
            feedback_id=item.feedback_id,
            stage=stage or item.stage,
            category=category or item.category,
            severity=item.severity,
            owner=owner or item.owner,
            summary=item.summary,
            detail=item.detail,
            source=item.source,
            evidence_ref=item.evidence_ref,
            metadata=metadata,
        )

    def _stop_reason(
        self,
        report: FeedbackReport,
    ) -> str | None:
        if not report.blocking:
            return None
        if self._split is EvaluationSplit.HIDDEN:
            return "hidden_blocking_result"

        categories = {
            item.category for item in report.items
        }
        if FeedbackCategory.BUDGET_EXHAUSTED in categories:
            return "budget_exhausted"
        if categories & _TERMINAL_PUBLIC_CATEGORIES:
            return "terminal_infrastructure_failure"
        return None

    def _suite_work_dir(self, index: int) -> Path:
        return (
            Path(self._inputs.work_dir)
            / self._split.value
            / f"suite_{index:03d}"
        )

    @staticmethod
    def _read_invocation(
        path: Path,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(
                path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _execution_summary(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {
            field: value.get(field)
            for field in (
                "status",
                "returncode",
                "timeout",
                "error_type",
            )
            if value.get(field) is not None
        }

    @staticmethod
    def _budget_summary(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {
            field: value.get(field)
            for field in (
                "status",
                "checkpoint",
                "resource",
                "limit",
                "attempted",
            )
            if value.get(field) is not None
        }


def read_csim_invocation_summary(
    work_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    """Read only non-sensitive CSIM invocation fields."""

    path = Path(work_dir) / "csim_invocation.json"
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

    return {
        "budget": (
            CsimValidationStageHandler._budget_summary(
                value.get("budget")
            )
        ),
        "compile_execution": (
            CsimValidationStageHandler._execution_summary(
                value.get("compile_execution")
            )
        ),
        "simulation_execution": (
            CsimValidationStageHandler._execution_summary(
                value.get("simulation_execution")
            )
        ),
    }
