import unittest

from agrefactor.evaluation import (
    CsynthDiagnosticParser,
    CsynthFeedbackAdapter,
    CsynthFeedbackComposer,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


def completed_invocation(returncode: int = 0) -> dict:
    return {
        "phase": "csynth",
        "top_kernel": "generic_top",
        "target_profile": {
            "name": "default",
            "device": "xcu200-fsgd2104-2-e",
        },
        "toolchain_version_verification": {
            "status": "matched",
            "requested": "2023.2",
            "actual": "2023.2",
        },
        "budget": {
            "status": "consumed",
            "checkpoint": "before_csynth_launch",
        },
        "execution": {
            "status": "completed",
            "returncode": returncode,
            "timeout": False,
        },
    }


class CsynthFeedbackComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = CsynthFeedbackAdapter()
        self.parser = CsynthDiagnosticParser()
        self.composer = CsynthFeedbackComposer()

    def invocation_report(
        self,
        *,
        status="succeeded",
        returncode=0,
        error_msg="",
        report_id="invocation",
    ) -> FeedbackReport:
        return self.adapter.to_operator_report(
            invocation=completed_invocation(returncode),
            report_id=report_id,
            legacy_status=status,
            error_msg=error_msg,
            evidence_ref="/tmp/csynth_invocation.json",
        )

    def diagnostic_report(
        self,
        text: str,
        *,
        report_id="diagnostic",
    ) -> FeedbackReport:
        return self.parser.parse_text(
            text,
            report_id=report_id,
            evidence_ref="/tmp/solution.log",
            owner=FeedbackOwner.CANDIDATE,
        )

    def test_success_without_diagnostics_is_empty(self) -> None:
        report = self.composer.compose(
            invocation_report=self.invocation_report(),
            diagnostic_report=self.diagnostic_report(""),
            report_id="combined",
        )

        self.assertEqual(report.items, ())
        self.assertFalse(report.blocking)
        self.assertEqual(
            report.metadata["composed_item_count"],
            0,
        )

    def test_success_with_pipeline_warning_keeps_warning(
        self,
    ) -> None:
        diagnostics = self.diagnostic_report(
            "WARNING: [HLS 200-880] The II Violation in "
            "module 'top_L1' (loop 'L1'): Unable to enforce "
            "a carried dependence constraint "
            "(II = 2, distance = 1, offset = 0) between "
            "a store operation and a load operation."
        )

        report = self.composer.compose(
            invocation_report=self.invocation_report(),
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.PIPELINE_DEPENDENCY,
        )
        self.assertFalse(report.blocking)
        self.assertEqual(
            report.metadata[
                "suppressed_generic_invocation_count"
            ],
            0,
        )

    def test_specific_error_suppresses_generic_failure(
        self,
    ) -> None:
        invocation = self.invocation_report(
            status="csynth_failed",
            returncode=1,
            error_msg="generic synthesis failure",
        )
        diagnostics = self.diagnostic_report(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'N' (top.cpp:4:2)"
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.UNDECLARED_SYMBOL,
        )
        self.assertEqual(
            report.metadata[
                "suppressed_generic_invocation_count"
            ],
            1,
        )
        self.assertEqual(
            len(
                report.source_evidence[
                    "suppressed_invocation_items"
                ]
            ),
            1,
        )

    def test_unknown_blocking_diagnostic_suppresses_generic(
        self,
    ) -> None:
        invocation = self.invocation_report(
            status="csynth_failed",
            returncode=1,
        )
        diagnostics = self.diagnostic_report(
            "ERROR: [HLS 999-123] new future failure"
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.UNKNOWN,
        )
        self.assertEqual(
            report.items[0].source,
            "csynth_diagnostic",
        )
        self.assertTrue(report.blocking)

    def test_warning_does_not_suppress_failed_invocation(
        self,
    ) -> None:
        invocation = self.invocation_report(
            status="csynth_failed",
            returncode=1,
        )
        diagnostics = self.diagnostic_report(
            "WARNING: [HLS 200-878] Unable to schedule the "
            "loop exit test ('icmp' operation) in the first "
            "pipeline iteration (II = 2 cycles)."
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(len(report.items), 2)
        self.assertTrue(report.blocking)
        self.assertEqual(
            report.metadata[
                "suppressed_generic_invocation_count"
            ],
            0,
        )
        self.assertEqual(
            {item.severity for item in report.items},
            {
                FeedbackSeverity.ERROR,
                FeedbackSeverity.WARNING,
            },
        )

    def test_timeout_is_not_suppressed(self) -> None:
        invocation_payload = completed_invocation()
        invocation_payload["execution"] = {
            "status": "completed",
            "returncode": None,
            "timeout": True,
        }
        invocation = self.adapter.to_operator_report(
            invocation=invocation_payload,
            report_id="timeout-invocation",
            legacy_status="timeout",
        )
        diagnostics = self.diagnostic_report(
            "ERROR: [HLS 999-1] partial log failure"
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(len(report.items), 2)
        self.assertIn(
            FeedbackCategory.TIMEOUT,
            {item.category for item in report.items},
        )
        self.assertEqual(
            report.metadata[
                "suppressed_generic_invocation_count"
            ],
            0,
        )

    def test_budget_failure_is_preserved(self) -> None:
        invocation_payload = completed_invocation()
        invocation_payload["budget"] = {
            "status": "blocked",
            "resource": "csynth_calls",
            "checkpoint": "before_version_probe",
        }
        invocation_payload["execution"] = {
            "status": "blocked_by_budget",
            "returncode": None,
            "timeout": False,
        }
        invocation = self.adapter.to_operator_report(
            invocation=invocation_payload,
            report_id="budget-invocation",
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=self.diagnostic_report(""),
            report_id="combined",
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.EVALUATOR,
        )

    def test_item_ids_are_rebased_and_provenance_preserved(
        self,
    ) -> None:
        invocation = self.invocation_report(
            status="csynth_failed",
            returncode=1,
        )
        diagnostics = self.diagnostic_report(
            "\n".join(
                [
                    (
                        "ERROR: [HLS 207-7] expected ')' "
                        "(top.cpp:1:1)"
                    ),
                    (
                        "WARNING: [HLS 200-880] The II Violation "
                        "in module 'L1' (loop 'L1'): Unable to "
                        "enforce a carried dependence constraint "
                        "(II = 2, distance = 1, offset = 0) "
                        "between a store and a load."
                    ),
                ]
            )
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(
            [item.feedback_id for item in report.items],
            [
                "combined.diagnostic.1",
                "combined.diagnostic.2",
            ],
        )
        for item in report.items:
            self.assertEqual(
                item.metadata["component"],
                "diagnostic",
            )
            self.assertIn(
                "component_feedback_id",
                item.metadata,
            )

    def test_component_reports_are_preserved_completely(
        self,
    ) -> None:
        invocation = self.invocation_report(
            status="csynth_failed",
            returncode=1,
            error_msg="raw invocation error",
        )
        diagnostics = self.diagnostic_report(
            "ERROR: [HLS 207-7] expected ')' (top.cpp:1:1)"
        )

        report = self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(
            report.source_evidence["invocation_report"],
            invocation.to_dict(),
        )
        self.assertEqual(
            report.source_evidence["diagnostic_report"],
            diagnostics.to_dict(),
        )

    def test_composition_does_not_mutate_components(self) -> None:
        invocation = self.invocation_report(
            status="csynth_failed",
            returncode=1,
        )
        diagnostics = self.diagnostic_report(
            "ERROR: [HLS 207-7] expected ')' (top.cpp:1:1)"
        )
        invocation_before = invocation.to_dict()
        diagnostics_before = diagnostics.to_dict()

        self.composer.compose(
            invocation_report=invocation,
            diagnostic_report=diagnostics,
            report_id="combined",
        )

        self.assertEqual(invocation.to_dict(), invocation_before)
        self.assertEqual(diagnostics.to_dict(), diagnostics_before)

    def test_report_round_trip(self) -> None:
        original = self.composer.compose(
            invocation_report=self.invocation_report(
                status="csynth_failed",
                returncode=1,
            ),
            diagnostic_report=self.diagnostic_report(
                "ERROR: [HLS 207-7] expected ')' "
                "(top.cpp:1:1)"
            ),
            report_id="combined",
        )
        restored = FeedbackReport.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_rejects_wrong_invocation_source(self) -> None:
        wrong = FeedbackReport(
            report_id="wrong",
            source="other",
            metadata={"evidence_view": "operator_full"},
        )

        with self.assertRaises(ValueError):
            self.composer.compose(
                invocation_report=wrong,
                diagnostic_report=self.diagnostic_report(""),
                report_id="combined",
            )

    def test_rejects_wrong_diagnostic_source(self) -> None:
        wrong = FeedbackReport(
            report_id="wrong",
            source="other",
            metadata={"evidence_view": "operator_full"},
        )

        with self.assertRaises(ValueError):
            self.composer.compose(
                invocation_report=self.invocation_report(),
                diagnostic_report=wrong,
                report_id="combined",
            )

    def test_rejects_non_operator_component(self) -> None:
        wrong = FeedbackReport(
            report_id="wrong",
            source="csynth_diagnostic",
            metadata={"evidence_view": "agent_safe"},
        )

        with self.assertRaises(ValueError):
            self.composer.compose(
                invocation_report=self.invocation_report(),
                diagnostic_report=wrong,
                report_id="combined",
            )

    def test_rejects_non_report_component(self) -> None:
        with self.assertRaises(TypeError):
            self.composer.compose(
                invocation_report={"items": []},
                diagnostic_report=self.diagnostic_report(""),
                report_id="combined",
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
            diagnostics = self.parser.parse_text(
                (
                    "ERROR: [HLS 207-3776] use of undeclared "
                    f"identifier '{family}_bound' "
                    f"({family}.cpp:1:1)"
                ),
                report_id=f"{family}-diagnostic",
                owner=FeedbackOwner.CANDIDATE,
            )
            reports.append(
                self.composer.compose(
                    invocation_report=self.invocation_report(
                        status="csynth_failed",
                        returncode=1,
                        report_id=f"{family}-invocation",
                    ),
                    diagnostic_report=diagnostics,
                    report_id=f"{family}-combined",
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
