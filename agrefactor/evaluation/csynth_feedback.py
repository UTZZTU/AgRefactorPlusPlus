"""Map csynth invocation evidence to generic operator feedback."""

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
)


class CsynthFeedbackAdapter:
    """Normalize invocation-level csynth outcomes.

    This adapter intentionally handles only deterministic invocation
    evidence: budget gating, toolchain verification, launch failures,
    timeout, legacy status, and return code. It does not parse Vitis
    synthesis diagnostics into source-level causes such as unknown
    bounds, dependencies, timing, or resource pressure.

    The report preserves complete source evidence and is therefore an
    operator/internal representation. It is not inserted into an agent
    prompt by this commit.
    """

    source = "csynth_invocation"
    adapter_version = 1

    def to_operator_report(
        self,
        *,
        invocation: Mapping[str, Any],
        report_id: str,
        legacy_status: str | None = None,
        error_msg: str = "",
        evidence_ref: str | None = None,
    ) -> FeedbackReport:
        """Convert one csynth invocation without changing execution."""

        if not isinstance(invocation, Mapping):
            raise TypeError("invocation must be a mapping")

        normalized_status = self._clean_optional(
            legacy_status,
            "legacy_status",
        )
        normalized_error = self._clean_text(
            error_msg,
            "error_msg",
        )
        normalized_ref = self._clean_optional(
            evidence_ref,
            "evidence_ref",
        )

        invocation_copy = dict(invocation)
        item = self._build_item(
            invocation_copy,
            report_id=report_id,
            legacy_status=normalized_status,
            error_msg=normalized_error,
            evidence_ref=normalized_ref,
        )
        items = () if item is None else (item,)

        execution = self._mapping(
            invocation_copy.get("execution")
        )
        verification = self._mapping(
            invocation_copy.get(
                "toolchain_version_verification"
            )
        )
        budget = self._mapping(
            invocation_copy.get("budget")
        )
        target = self._mapping(
            invocation_copy.get("target_profile")
        )

        return FeedbackReport(
            report_id=report_id,
            source=self.source,
            items=items,
            source_evidence={
                "legacy_status": normalized_status,
                "error_msg": normalized_error,
                "invocation": invocation_copy,
            },
            metadata={
                "adapter_version": self.adapter_version,
                "evidence_view": "operator_full",
                "legacy_status": normalized_status,
                "execution_status": execution.get("status"),
                "toolchain_verification_status": (
                    verification.get("status")
                ),
                "budget_status": budget.get("status"),
                "return_code": execution.get("returncode"),
                "timed_out": execution.get("timeout") is True,
                "top_kernel": invocation_copy.get("top_kernel"),
                "target_profile_name": target.get("name"),
                "target_device": target.get("device"),
                "requested_toolchain_version": (
                    invocation_copy.get(
                        "requested_toolchain_version"
                    )
                ),
                "evidence_ref": normalized_ref,
            },
        )

    def _build_item(
        self,
        invocation: Mapping[str, Any],
        *,
        report_id: str,
        legacy_status: str | None,
        error_msg: str,
        evidence_ref: str | None,
    ) -> FeedbackItem | None:
        execution = self._mapping(invocation.get("execution"))
        verification = self._mapping(
            invocation.get("toolchain_version_verification")
        )
        budget = self._mapping(invocation.get("budget"))

        if (
            legacy_status == "succeeded"
            and execution.get("status") == "completed"
            and execution.get("timeout") is not True
        ):
            return None

        stage, category, severity, owner, summary = (
            self._classify(
                legacy_status=legacy_status,
                execution=execution,
                verification=verification,
                budget=budget,
            )
        )

        detail = error_msg or self._fallback_detail(
            invocation=invocation,
            execution=execution,
            verification=verification,
            budget=budget,
        )

        metadata = {
            "legacy_status": legacy_status,
            "execution_status": execution.get("status"),
            "execution_returncode": execution.get("returncode"),
            "execution_timeout": execution.get("timeout"),
            "toolchain_verification_status": (
                verification.get("status")
            ),
            "toolchain_requested_version": (
                verification.get("requested")
            ),
            "toolchain_actual_version": verification.get("actual"),
            "budget_status": budget.get("status"),
            "budget_checkpoint": budget.get("checkpoint"),
            "budget_resource": budget.get("resource"),
            "top_kernel": invocation.get("top_kernel"),
        }

        return FeedbackItem(
            feedback_id=f"{report_id}.result.1",
            stage=stage,
            category=category,
            severity=severity,
            owner=owner,
            summary=summary,
            detail=detail or None,
            source=self.source,
            evidence_ref=evidence_ref,
            metadata=metadata,
        )

    @classmethod
    def _classify(
        cls,
        *,
        legacy_status: str | None,
        execution: Mapping[str, Any],
        verification: Mapping[str, Any],
        budget: Mapping[str, Any],
    ) -> tuple[
        FeedbackStage,
        FeedbackCategory,
        FeedbackSeverity,
        FeedbackOwner,
        str,
    ]:
        execution_status = execution.get("status")
        verification_status = verification.get("status")
        budget_status = budget.get("status")

        if (
            budget_status == "blocked"
            or execution_status == "blocked_by_budget"
        ):
            return (
                FeedbackStage.CONFIGURATION,
                FeedbackCategory.BUDGET_EXHAUSTED,
                FeedbackSeverity.ERROR,
                FeedbackOwner.EVALUATOR,
                "CSYNTH execution was blocked by the configured budget",
            )

        if verification_status == "mismatch":
            return (
                FeedbackStage.TOOLCHAIN,
                FeedbackCategory.INVALID_CONFIGURATION,
                FeedbackSeverity.FATAL,
                FeedbackOwner.CONFIGURATION,
                "Requested and detected Vitis versions do not match",
            )

        if verification_status == "probe_timeout":
            return (
                FeedbackStage.TOOLCHAIN,
                FeedbackCategory.TIMEOUT,
                FeedbackSeverity.FATAL,
                FeedbackOwner.TOOLCHAIN,
                "Vitis version verification timed out",
            )

        if verification_status in {
            "executable_not_found",
            "probe_failed",
            "unparseable",
        }:
            return (
                FeedbackStage.TOOLCHAIN,
                FeedbackCategory.TOOLCHAIN_FAILURE,
                FeedbackSeverity.FATAL,
                FeedbackOwner.TOOLCHAIN,
                "Vitis toolchain verification failed",
            )

        if execution_status == "blocked_before_csynth":
            return (
                FeedbackStage.TOOLCHAIN,
                FeedbackCategory.TOOLCHAIN_FAILURE,
                FeedbackSeverity.FATAL,
                FeedbackOwner.TOOLCHAIN,
                "CSYNTH was blocked before synthesis launch",
            )

        if execution_status == "launch_error":
            return (
                FeedbackStage.TOOLCHAIN,
                FeedbackCategory.TOOLCHAIN_FAILURE,
                FeedbackSeverity.FATAL,
                FeedbackOwner.TOOLCHAIN,
                "CSYNTH process could not be launched",
            )

        if (
            execution.get("timeout") is True
            or legacy_status == "timeout"
        ):
            return (
                FeedbackStage.CSYNTH,
                FeedbackCategory.TIMEOUT,
                FeedbackSeverity.FATAL,
                FeedbackOwner.UNKNOWN,
                "CSYNTH execution timed out",
            )

        if legacy_status == "csynth_failed":
            return (
                FeedbackStage.CSYNTH,
                FeedbackCategory.UNKNOWN,
                FeedbackSeverity.ERROR,
                FeedbackOwner.UNKNOWN,
                "CSYNTH execution failed",
            )

        return_code = execution.get("returncode")
        if (
            isinstance(return_code, int)
            and not isinstance(return_code, bool)
            and return_code != 0
        ):
            return (
                FeedbackStage.CSYNTH,
                FeedbackCategory.UNKNOWN,
                FeedbackSeverity.ERROR,
                FeedbackOwner.UNKNOWN,
                "CSYNTH returned a non-zero exit code",
            )

        return (
            FeedbackStage.CSYNTH,
            FeedbackCategory.UNKNOWN,
            FeedbackSeverity.ERROR,
            FeedbackOwner.UNKNOWN,
            "CSYNTH outcome could not be classified",
        )

    @classmethod
    def _fallback_detail(
        cls,
        *,
        invocation: Mapping[str, Any],
        execution: Mapping[str, Any],
        verification: Mapping[str, Any],
        budget: Mapping[str, Any],
    ) -> str:
        for value in (
            execution.get("error"),
            verification.get("stderr"),
            verification.get("stdout"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()

        parts = []
        for label, value in (
            ("execution_status", execution.get("status")),
            ("returncode", execution.get("returncode")),
            ("verification_status", verification.get("status")),
            ("budget_status", budget.get("status")),
            ("phase", invocation.get("phase")),
        ):
            if value is not None:
                parts.append(f"{label}={value}")

        return ", ".join(parts)

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _clean_text(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        return value.strip()

    @staticmethod
    def _clean_optional(
        value: str | None,
        field_name: str,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(
                f"{field_name} must be a string or null"
            )
        cleaned = value.strip()
        return cleaned or None
