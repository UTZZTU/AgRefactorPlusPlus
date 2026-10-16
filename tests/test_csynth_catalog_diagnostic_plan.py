import unittest

from agrefactor.evaluation import CsynthFeedbackViewAdapter
from agrefactor.evaluation.feedback_routing import (
    FeedbackRouteAction,
    FeedbackRouter,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


class CsynthCatalogDiagnosticPlanTests(unittest.TestCase):
    def report(self, *, diagnostic_check=None):
        metadata = {
            "diagnostic_id": "hls_214_298_struct_pointer_argument",
            "diagnostic_checks": [
                "csynth_top_argument_pointer_member_contract"
            ],
            "execution_evidence_complete": True,
            "evidence_complete": False,
            "repair_eligible": False,
        }
        detail = "HLS 214-298 unsupported top argument."
        if diagnostic_check is not None:
            metadata["diagnostic_check"] = diagnostic_check
            detail += (
                " Catalog diagnostic check: "
                f"{diagnostic_check['result']}; {diagnostic_check['summary']}"
            )
        item = FeedbackItem(
            feedback_id="csynth-item-1",
            stage=FeedbackStage.CSYNTH,
            category=FeedbackCategory.UNSUPPORTED_CONSTRUCT,
            severity=FeedbackSeverity.ERROR,
            owner=FeedbackOwner.UNKNOWN,
            summary="Unsupported top argument type.",
            detail=detail,
            source="csynth_diagnostic",
            metadata=metadata,
        )
        return FeedbackReport(
            report_id="csynth-operator",
            source="csynth",
            items=(item,),
            metadata={
                "evidence_view": "operator_full",
                "evaluation_split": "public",
            },
        )

    def test_all_check_states_preserve_existing_unknown_route(self):
        baseline = CsynthFeedbackViewAdapter().to_agent_report(
            self.report(), report_id="csynth-agent"
        )
        baseline_decision = FeedbackRouter().route(
            baseline, decision_id="same-decision"
        )
        self.assertEqual(
            baseline_decision.action,
            FeedbackRouteAction.REVIEW_UNKNOWN,
        )

        for result in ("satisfied", "missing", "contradicted"):
            with self.subTest(result=result):
                report = self.report(diagnostic_check={
                    "check_id": "csynth_top_argument_pointer_member_contract",
                    "result": result,
                    "reason_code": "fixture_result",
                    "summary": "bounded local check summary",
                })
                safe = CsynthFeedbackViewAdapter().to_agent_report(
                    report, report_id="csynth-agent"
                )
                decision = FeedbackRouter().route(
                    safe, decision_id="same-decision"
                )
                self.assertEqual(decision.action, baseline_decision.action)
                self.assertEqual(
                    decision.selected_feedback_ids,
                    baseline_decision.selected_feedback_ids,
                )
                self.assertEqual(len(safe.items), 1)
                self.assertTrue(safe.items[0].blocking)
                self.assertEqual(safe.items[0].owner, FeedbackOwner.UNKNOWN)
                self.assertFalse(safe.items[0].metadata["repair_eligible"])
                self.assertEqual(
                    safe.items[0].metadata["diagnostic_check"]["result"],
                    result,
                )


if __name__ == "__main__":
    unittest.main()
