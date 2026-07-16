"""Map test-suite evaluation evidence to generic feedback reports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
    TestEvaluationEvidence,
    TestEvaluationStatus,
)


class TestEvaluationFeedbackAdapter:
    """Convert test evidence into operator or agent-safe feedback.

    The operator view preserves complete source evidence. The agent view
    always starts from ``TestEvaluationEvidence.to_agent_dict()`` so a
    hidden suite cannot leak diagnostic details, testbench locations, or
    artifact paths through either report-level or item-level fields.
    """

    source = "test_evaluation"
    adapter_version = 1

    def to_operator_report(
        self,
        evidence: TestEvaluationEvidence,
        *,
        report_id: str,
    ) -> FeedbackReport:
        """Return complete operator-oriented feedback."""

        return self._to_report(
            evidence,
            report_id=report_id,
            agent_safe=False,
        )

    def to_agent_report(
        self,
        evidence: TestEvaluationEvidence,
        *,
        report_id: str,
    ) -> FeedbackReport:
        """Return feedback safe to expose to an agent."""

        return self._to_report(
            evidence,
            report_id=report_id,
            agent_safe=True,
        )

    def _to_report(
        self,
        evidence: TestEvaluationEvidence,
        *,
        report_id: str,
        agent_safe: bool,
    ) -> FeedbackReport:
        if not isinstance(evidence, TestEvaluationEvidence):
            raise TypeError(
                "evidence must be a TestEvaluationEvidence"
            )

        payload = (
            evidence.to_agent_dict()
            if agent_safe
            else evidence.to_dict()
        )
        evidence_view = (
            "agent_safe" if agent_safe else "operator_full"
        )

        items = self._build_items(
            payload,
            report_id=report_id,
        )
        suite = payload["suite"]

        return FeedbackReport(
            report_id=report_id,
            source=self.source,
            items=items,
            source_evidence=payload,
            metadata={
                "adapter_version": self.adapter_version,
                "evidence_view": evidence_view,
                "suite_id": suite["suite_id"],
                "suite_version": suite.get("suite_version"),
                "evaluation_split": suite["split"],
                "feedback_visible_to_agent": suite[
                    "feedback_visible_to_agent"
                ],
                "evaluation_status": payload["status"],
                "passed_cases": payload["passed_cases"],
                "failed_cases": payload["failed_cases"],
                "evaluated_cases": payload["evaluated_cases"],
                "timed_out": payload["timed_out"],
                "return_code": payload["return_code"],
                "source_redacted": payload["redacted"],
            },
        )

    def _build_items(
        self,
        payload: Mapping[str, Any],
        *,
        report_id: str,
    ) -> tuple[FeedbackItem, ...]:
        status = TestEvaluationStatus(payload["status"])
        if status is TestEvaluationStatus.PASSED:
            return ()

        details = payload.get("details", {})
        if not isinstance(details, Mapping):
            details = {}

        stage = self._stage(details)
        category = self._category(
            details,
            timed_out=payload["timed_out"],
        )
        severity = self._severity(
            status,
            timed_out=payload["timed_out"],
        )

        diagnostic = details.get("diagnostic")
        detail = (
            diagnostic.strip()
            if isinstance(diagnostic, str)
            and diagnostic.strip()
            else None
        )

        artifacts = payload.get("artifacts", ())
        evidence_ref = None
        if (
            isinstance(artifacts, list)
            and artifacts
            and isinstance(artifacts[0], str)
        ):
            evidence_ref = artifacts[0]

        item_metadata: dict[str, Any] = {
            "evaluation_status": payload["status"],
            "suite_id": payload["suite"]["suite_id"],
            "evaluation_split": payload["suite"]["split"],
            "passed_cases": payload["passed_cases"],
            "failed_cases": payload["failed_cases"],
            "evaluated_cases": payload["evaluated_cases"],
            "timed_out": payload["timed_out"],
            "return_code": payload["return_code"],
            "source_redacted": payload["redacted"],
        }

        for key in (
            "legacy_status",
            "case_counts_complete",
            "compile_execution",
            "simulation_execution",
        ):
            if key in details:
                item_metadata[key] = details[key]

        return (
            FeedbackItem(
                feedback_id=f"{report_id}.result.1",
                stage=stage,
                category=category,
                severity=severity,
                owner=FeedbackOwner.UNKNOWN,
                summary=payload["summary"],
                detail=detail,
                source=self.source,
                evidence_ref=evidence_ref,
                metadata=item_metadata,
            ),
        )

    @staticmethod
    def _stage(
        details: Mapping[str, Any],
    ) -> FeedbackStage:
        legacy_status = details.get("legacy_status")
        compile_execution = details.get("compile_execution", {})
        simulation_execution = details.get(
            "simulation_execution",
            {},
        )

        if legacy_status == "tb_compile_failed":
            return FeedbackStage.COMPILE

        if isinstance(compile_execution, Mapping):
            compile_status = compile_execution.get("status")
            compile_code = compile_execution.get("returncode")
            compile_timeout = compile_execution.get("timeout")
            if (
                compile_timeout is True
                or compile_status == "launch_error"
                or (
                    isinstance(compile_code, int)
                    and not isinstance(compile_code, bool)
                    and compile_code != 0
                )
            ):
                return FeedbackStage.COMPILE

        if isinstance(simulation_execution, Mapping):
            if simulation_execution:
                return FeedbackStage.CSIM

        return FeedbackStage.TEST

    @staticmethod
    def _category(
        details: Mapping[str, Any],
        *,
        timed_out: bool,
    ) -> FeedbackCategory:
        if timed_out:
            return FeedbackCategory.TIMEOUT

        for key in ("compile_execution", "simulation_execution"):
            execution = details.get(key)
            if (
                isinstance(execution, Mapping)
                and execution.get("status") == "launch_error"
            ):
                return FeedbackCategory.TOOLCHAIN_FAILURE

        explicit = details.get("feedback_category")
        if isinstance(explicit, str):
            try:
                return FeedbackCategory(explicit)
            except ValueError:
                pass

        return FeedbackCategory.UNKNOWN

    @staticmethod
    def _severity(
        status: TestEvaluationStatus,
        *,
        timed_out: bool,
    ) -> FeedbackSeverity:
        if timed_out or status is TestEvaluationStatus.ERROR:
            return FeedbackSeverity.FATAL
        return FeedbackSeverity.ERROR
