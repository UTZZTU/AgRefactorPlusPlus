"""Map testbench preflight evidence to generic operator feedback."""

from __future__ import annotations

from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
    TestbenchDiagnostic,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)


_KIND_TO_CATEGORY = {
    TestbenchFailureKind.FORBIDDEN_INTERNAL_DEPENDENCY: (
        FeedbackCategory.FORBIDDEN_DEPENDENCY
    ),
    TestbenchFailureKind.UNDECLARED_TYPE: (
        FeedbackCategory.UNDECLARED_TYPE
    ),
    TestbenchFailureKind.UNDECLARED_SYMBOL: (
        FeedbackCategory.UNDECLARED_SYMBOL
    ),
    TestbenchFailureKind.SYNTAX_ERROR: (
        FeedbackCategory.SYNTAX_ERROR
    ),
    TestbenchFailureKind.LINK_ERROR: FeedbackCategory.LINK_ERROR,
    TestbenchFailureKind.LINKAGE_MISMATCH: (
        FeedbackCategory.LINKAGE_MISMATCH
    ),
    TestbenchFailureKind.COMPILE_TIMEOUT: FeedbackCategory.TIMEOUT,
    TestbenchFailureKind.COMPILER_NOT_FOUND: (
        FeedbackCategory.TOOLCHAIN_FAILURE
    ),
    TestbenchFailureKind.RUNTIME_ERROR: (
        FeedbackCategory.RUNTIME_CRASH
    ),
    TestbenchFailureKind.RUN_TIMEOUT: FeedbackCategory.TIMEOUT,
    TestbenchFailureKind.OUTPUT_MISMATCH: (
        FeedbackCategory.FUNCTIONAL_MISMATCH
    ),
    TestbenchFailureKind.FALLBACK_MISMATCH: (
        FeedbackCategory.FUNCTIONAL_MISMATCH
    ),
    TestbenchFailureKind.UNKNOWN: FeedbackCategory.UNKNOWN,
    TestbenchFailureKind.NONE: FeedbackCategory.UNKNOWN,
}

_OWNER_MAP = {
    TestbenchFailureOwner.NONE: FeedbackOwner.NONE,
    TestbenchFailureOwner.TESTBENCH: FeedbackOwner.TESTBENCH,
    TestbenchFailureOwner.ORIGINAL: FeedbackOwner.ORIGINAL,
    TestbenchFailureOwner.CANDIDATE: FeedbackOwner.CANDIDATE,
    TestbenchFailureOwner.TOOLCHAIN: FeedbackOwner.TOOLCHAIN,
    TestbenchFailureOwner.UNKNOWN: FeedbackOwner.UNKNOWN,
}


class TestbenchPreflightFeedbackAdapter:
    """Create an operator-oriented report from preflight evidence.

    The returned report preserves complete source evidence, including
    stdout, stderr, diagnostics, commands, and artifact paths. It is
    therefore an operator/internal representation and must not be
    inserted into an agent prompt without a later audience-safe
    projection.
    """

    source = "testbench_preflight"
    adapter_version = 1

    def to_operator_report(
        self,
        result: TestbenchPreflightResult,
        *,
        report_id: str,
    ) -> FeedbackReport:
        """Convert a preflight result without changing the source result."""

        if not isinstance(result, TestbenchPreflightResult):
            raise TypeError(
                "result must be a TestbenchPreflightResult"
            )

        items = self._build_items(result, report_id=report_id)

        return FeedbackReport(
            report_id=report_id,
            source=self.source,
            items=items,
            source_evidence=result.to_dict(),
            metadata={
                "adapter_version": self.adapter_version,
                "evidence_view": "operator_full",
                "preflight_status": result.status.value,
                "preflight_stage": result.stage.value,
                "failure_kind": result.failure_kind.value,
                "failure_owner": result.failure_owner.value,
                "next_action": result.next_action,
                "duration_s": result.duration_s,
            },
        )

    def _build_items(
        self,
        result: TestbenchPreflightResult,
        *,
        report_id: str,
    ) -> tuple[FeedbackItem, ...]:
        if result.status is TestbenchPreflightStatus.PASSED:
            return ()

        severity = self._severity(result.status)
        owner = _OWNER_MAP[result.failure_owner]

        if result.diagnostics:
            return tuple(
                self._from_diagnostic(
                    diagnostic,
                    result=result,
                    report_id=report_id,
                    index=index,
                    severity=severity,
                    owner=owner,
                )
                for index, diagnostic in enumerate(
                    result.diagnostics,
                    start=1,
                )
            )

        return (
            FeedbackItem(
                feedback_id=f"{report_id}.result.1",
                stage=self._stage(
                    result.stage,
                    result.failure_kind,
                ),
                category=_KIND_TO_CATEGORY[
                    result.failure_kind
                ],
                severity=severity,
                owner=owner,
                summary=self._fallback_summary(result),
                detail=result.stderr or result.stdout or None,
                source=self.source,
                metadata={
                    "diagnostic_kind": (
                        result.failure_kind.value
                    ),
                    "preflight_status": result.status.value,
                    "next_action": result.next_action,
                    "fallback_item": True,
                },
            ),
        )

    def _from_diagnostic(
        self,
        diagnostic: TestbenchDiagnostic,
        *,
        result: TestbenchPreflightResult,
        report_id: str,
        index: int,
        severity: FeedbackSeverity,
        owner: FeedbackOwner,
    ) -> FeedbackItem:
        kind = diagnostic.kind
        return FeedbackItem(
            feedback_id=f"{report_id}.diagnostic.{index}",
            stage=self._stage(result.stage, kind),
            category=_KIND_TO_CATEGORY[kind],
            severity=severity,
            owner=owner,
            summary=diagnostic.message,
            detail=diagnostic.raw,
            source=self.source,
            metadata={
                "diagnostic_kind": kind.value,
                "file": diagnostic.file,
                "line": diagnostic.line,
                "column": diagnostic.column,
                "preflight_status": result.status.value,
                "next_action": result.next_action,
            },
        )

    @staticmethod
    def _severity(
        status: TestbenchPreflightStatus,
    ) -> FeedbackSeverity:
        if status is TestbenchPreflightStatus.ERROR:
            return FeedbackSeverity.FATAL
        return FeedbackSeverity.ERROR

    @staticmethod
    def _stage(
        stage: TestbenchStage,
        kind: TestbenchFailureKind,
    ) -> FeedbackStage:
        if stage is TestbenchStage.STATIC_CHECK:
            return FeedbackStage.STATIC_CHECK
        if stage is TestbenchStage.RUN:
            return FeedbackStage.TEST
        if kind in {
            TestbenchFailureKind.LINK_ERROR,
            TestbenchFailureKind.LINKAGE_MISMATCH,
        }:
            return FeedbackStage.LINK
        return FeedbackStage.COMPILE

    @staticmethod
    def _fallback_summary(
        result: TestbenchPreflightResult,
    ) -> str:
        return (
            "Testbench preflight "
            f"{result.status.value}: "
            f"{result.failure_kind.value}"
        )
