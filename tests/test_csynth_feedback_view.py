import json
import unittest

from agrefactor.evaluation import (
    CsynthFeedbackViewAdapter,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


SECRET_ROOT = "/home/private-user/projects/secret-run"
SECRET_COMMAND = "/opt/Xilinx/Vitis_HLS/2023.2/bin/vitis_hls"
SECRET_TOKEN = "TOP_SECRET_OPERATOR_ONLY"


def operator_report(
    *,
    items: tuple[FeedbackItem, ...],
) -> FeedbackReport:
    return FeedbackReport(
        report_id="operator-csynth",
        source="csynth",
        items=items,
        source_evidence={
            "invocation_report": {
                "source_evidence": {
                    "invocation": {
                        "work_dir": SECRET_ROOT,
                        "command": [
                            SECRET_COMMAND,
                            "-f",
                            f"{SECRET_ROOT}/vitis.tcl",
                        ],
                        "environment_secret": SECRET_TOKEN,
                    },
                },
            },
            "diagnostic_report": {
                "source_evidence": {
                    "diagnostics": [
                        {
                            "raw_line": (
                                "ERROR at "
                                f"{SECRET_ROOT}/kernel.cpp"
                            ),
                            "operator_secret": SECRET_TOKEN,
                        },
                    ],
                },
            },
            "artifact_loading": {
                "work_dir": SECRET_ROOT,
                "invocation_path": (
                    f"{SECRET_ROOT}/csynth_invocation.json"
                ),
            },
        },
        metadata={
            "evidence_view": "operator_full",
            "work_dir": SECRET_ROOT,
            "invocation_path": (
                f"{SECRET_ROOT}/csynth_invocation.json"
            ),
            "diagnostic_path": (
                f"{SECRET_ROOT}/csynth/solution/solution.log"
            ),
        },
    )


def diagnostic_item(
    *,
    feedback_id: str = "operator-csynth.diagnostic.1",
    detail: str | None = None,
    category=FeedbackCategory.UNDECLARED_SYMBOL,
    severity=FeedbackSeverity.ERROR,
    owner=FeedbackOwner.CANDIDATE,
) -> FeedbackItem:
    return FeedbackItem(
        feedback_id=feedback_id,
        stage=FeedbackStage.CSYNTH,
        category=category,
        severity=severity,
        owner=owner,
        summary="HLS source uses an undeclared identifier",
        detail=(
            detail
            if detail is not None
            else (
                "ERROR: [HLS 207-3776] use of undeclared "
                "identifier 'N' "
                f"({SECRET_ROOT}/kernel.cpp:42:7)"
            )
        ),
        source="csynth_diagnostic",
        evidence_ref=(
            f"{SECRET_ROOT}/csynth/solution/solution.log"
        ),
        metadata={
            "raw_severity": "ERROR",
            "message_family": "HLS",
            "message_code": "207-3776",
            "message_id": "HLS 207-3776",
            "file": f"{SECRET_ROOT}/kernel.cpp",
            "line": 42,
            "column": 7,
            "input_line": 9001,
            "parser_rule": "undeclared_identifier",
            "classification_confidence": "high",
            "occurrence_count": 2,
            "component": "diagnostic",
            "component_report_id": "operator-diagnostic",
            "component_feedback_id": "raw-item-id",
            "operator_secret": SECRET_TOKEN,
        },
    )


class CsynthFeedbackViewAdapterTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.adapter = CsynthFeedbackViewAdapter()

    def safe(
        self,
        report: FeedbackReport,
    ) -> FeedbackReport:
        return self.adapter.to_agent_report(
            report,
            report_id="agent-csynth",
        )

    def test_preserves_actionable_semantics(self) -> None:
        safe = self.safe(
            operator_report(
                items=(diagnostic_item(),)
            )
        )
        item = safe.items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.UNDECLARED_SYMBOL,
        )
        self.assertEqual(
            item.severity,
            FeedbackSeverity.ERROR,
        )
        self.assertEqual(
            item.owner,
            FeedbackOwner.CANDIDATE,
        )
        self.assertEqual(
            item.metadata["message_id"],
            "HLS 207-3776",
        )
        self.assertEqual(
            item.metadata["file"],
            "kernel.cpp",
        )
        self.assertEqual(item.metadata["line"], 42)
        self.assertEqual(item.metadata["column"], 7)
        self.assertTrue(safe.blocking)

    def test_removes_operator_source_evidence(self) -> None:
        safe = self.safe(
            operator_report(
                items=(diagnostic_item(),)
            )
        )
        serialized = json.dumps(
            safe.to_dict(),
            sort_keys=True,
        )

        self.assertNotIn(SECRET_ROOT, serialized)
        self.assertNotIn(SECRET_COMMAND, serialized)
        self.assertNotIn(SECRET_TOKEN, serialized)
        self.assertNotIn(
            "invocation_report",
            serialized,
        )
        self.assertNotIn(
            "diagnostic_report",
            serialized,
        )
        self.assertNotIn(
            "artifact_loading",
            serialized,
        )

    def test_removes_evidence_refs(self) -> None:
        safe = self.safe(
            operator_report(
                items=(diagnostic_item(),)
            )
        )

        self.assertIsNone(safe.items[0].evidence_ref)

    def test_sanitizes_absolute_paths_in_detail(self) -> None:
        safe = self.safe(
            operator_report(
                items=(diagnostic_item(),)
            )
        )
        detail = safe.items[0].detail

        self.assertIsNotNone(detail)
        self.assertNotIn(SECRET_ROOT, detail)
        self.assertIn("<PATH>", detail)
        self.assertIn(
            "use of undeclared identifier 'N'",
            detail,
        )

    def test_preserves_unknown_diagnostic_text(self) -> None:
        item = diagnostic_item(
            detail=(
                "ERROR: [HLS 999-123] future failure while "
                f"reading {SECRET_ROOT}/future.cpp"
            ),
            category=FeedbackCategory.UNKNOWN,
            owner=FeedbackOwner.UNKNOWN,
        )

        safe = self.safe(
            operator_report(items=(item,))
        )

        self.assertEqual(
            safe.items[0].category,
            FeedbackCategory.UNKNOWN,
        )
        self.assertIn(
            "future failure",
            safe.items[0].detail,
        )
        self.assertNotIn(
            SECRET_ROOT,
            safe.items[0].detail,
        )
        self.assertTrue(safe.items[0].blocking)

    def test_invocation_item_uses_metadata_allowlist(
        self,
    ) -> None:
        item = FeedbackItem(
            feedback_id="operator-csynth.invocation.1",
            stage=FeedbackStage.TOOLCHAIN,
            category=FeedbackCategory.TOOLCHAIN_FAILURE,
            severity=FeedbackSeverity.FATAL,
            owner=FeedbackOwner.TOOLCHAIN,
            summary="Vitis toolchain verification failed",
            detail=(
                "failed to launch "
                f"{SECRET_COMMAND} from {SECRET_ROOT}"
            ),
            source="csynth_invocation",
            evidence_ref=(
                f"{SECRET_ROOT}/csynth_invocation.json"
            ),
            metadata={
                "legacy_status": None,
                "execution_status": "launch_error",
                "execution_returncode": None,
                "execution_timeout": False,
                "toolchain_verification_status": (
                    "executable_not_found"
                ),
                "toolchain_requested_version": "2023.2",
                "toolchain_actual_version": None,
                "budget_status": "available",
                "budget_checkpoint": "before_version_probe",
                "budget_resource": None,
                "component": "invocation",
                "command": SECRET_COMMAND,
                "work_dir": SECRET_ROOT,
            },
        )

        safe = self.safe(
            operator_report(items=(item,))
        )
        safe_item = safe.items[0]

        self.assertEqual(
            safe_item.category,
            FeedbackCategory.TOOLCHAIN_FAILURE,
        )
        self.assertEqual(
            safe_item.metadata[
                "toolchain_verification_status"
            ],
            "executable_not_found",
        )
        self.assertNotIn(
            "command",
            safe_item.metadata,
        )
        self.assertNotIn(
            "work_dir",
            safe_item.metadata,
        )

    def test_empty_report_remains_empty(self) -> None:
        safe = self.safe(
            operator_report(items=())
        )

        self.assertEqual(safe.items, ())
        self.assertFalse(safe.blocking)
        self.assertEqual(
            safe.metadata["item_count"],
            0,
        )

    def test_report_is_marked_agent_safe(self) -> None:
        safe = self.safe(
            operator_report(
                items=(diagnostic_item(),)
            )
        )

        self.assertEqual(
            safe.metadata["evidence_view"],
            "agent_safe",
        )
        self.assertTrue(
            safe.metadata["source_redacted"]
        )
        self.assertTrue(
            safe.source_evidence["redacted"]
        )

    def test_category_severity_owner_counts(self) -> None:
        warning = diagnostic_item(
            feedback_id="operator-csynth.diagnostic.2",
            detail=(
                "WARNING: [HLS 200-880] "
                "Unable to enforce a carried dependence"
            ),
            category=FeedbackCategory.PIPELINE_DEPENDENCY,
            severity=FeedbackSeverity.WARNING,
        )
        safe = self.safe(
            operator_report(
                items=(diagnostic_item(), warning)
            )
        )

        self.assertEqual(
            safe.metadata["category_counts"],
            {
                "undeclared_symbol": 1,
                "pipeline_dependency": 1,
            },
        )
        self.assertEqual(
            safe.metadata["severity_counts"],
            {"error": 1, "warning": 1},
        )
        self.assertEqual(
            safe.metadata["owner_counts"],
            {"candidate": 2},
        )

    def test_does_not_mutate_operator_report(self) -> None:
        original = operator_report(
            items=(diagnostic_item(),)
        )
        before = original.to_dict()

        self.safe(original)

        self.assertEqual(
            original.to_dict(),
            before,
        )

    def test_agent_report_round_trip(self) -> None:
        original = self.safe(
            operator_report(
                items=(diagnostic_item(),)
            )
        )
        restored = FeedbackReport.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_rejects_non_csynth_report(self) -> None:
        wrong = FeedbackReport(
            report_id="wrong",
            source="test_evaluation",
            metadata={
                "evidence_view": "operator_full",
            },
        )

        with self.assertRaises(ValueError):
            self.safe(wrong)

    def test_rejects_agent_safe_input(self) -> None:
        wrong = FeedbackReport(
            report_id="wrong",
            source="csynth",
            metadata={
                "evidence_view": "agent_safe",
            },
        )

        with self.assertRaises(ValueError):
            self.safe(wrong)

    def test_rejects_non_report_input(self) -> None:
        with self.assertRaises(TypeError):
            self.adapter.to_agent_report(
                {"source": "csynth"},
                report_id="agent-csynth",
            )

    def test_rejects_empty_report_id(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.to_agent_report(
                operator_report(items=()),
                report_id=" ",
            )

    def test_is_kernel_agnostic(self) -> None:
        families = (
            "array_map",
            "reduction",
            "stencil",
            "multi_output",
            "stream",
            "stateful",
        )

        reports = []
        for family in families:
            item = FeedbackItem(
                feedback_id=f"{family}.item",
                stage=FeedbackStage.CSYNTH,
                category=FeedbackCategory.UNDECLARED_SYMBOL,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="undeclared identifier",
                detail=(
                    "ERROR: [HLS 207-3776] use of "
                    f"undeclared identifier '{family}_n' "
                    f"(/tmp/{family}/{family}.cpp:1:1)"
                ),
                source="csynth_diagnostic",
                evidence_ref=(
                    f"/tmp/{family}/solution.log"
                ),
                metadata={
                    "file": f"/tmp/{family}/{family}.cpp",
                    "line": 1,
                    "column": 1,
                    "message_id": "HLS 207-3776",
                },
            )
            reports.append(
                self.adapter.to_agent_report(
                    operator_report(items=(item,)),
                    report_id=f"{family}-safe",
                )
            )

        self.assertEqual(len(reports), len(families))
        self.assertTrue(
            all(
                report.items[0].category
                is FeedbackCategory.UNDECLARED_SYMBOL
                for report in reports
            )
        )


if __name__ == "__main__":
    unittest.main()
