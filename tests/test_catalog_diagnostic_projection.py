from dataclasses import replace
import unittest

from agrefactor.evaluation.csynth_diagnostics import CsynthDiagnosticParser
from agrefactor.evaluation.csynth_feedback_view import CsynthFeedbackViewAdapter
from agrefactor.evidence.diagnostic_event import DiagnosticEventProjector


class CatalogDiagnosticProjectionTests(unittest.TestCase):
    def safe_report(self, text):
        parsed = CsynthDiagnosticParser().parse_text(text, report_id="raw")
        operator = replace(parsed, source="csynth")
        return CsynthFeedbackViewAdapter().to_agent_report(operator, report_id="safe")

    def test_new_errors_retain_identity_in_existing_event(self):
        for text in (
            "ERROR: new failure",
            "ERROR: [HLS 999-123] new failure (/private/build/input.cpp:9:2)",
        ):
            with self.subTest(text=text):
                safe = self.safe_report(text)
                event = DiagnosticEventProjector().from_feedback(
                    safe, run_id="run", validation_id="validation",
                    validation_state="csynth", route_action="review_unknown",
                )
                diagnostic = event.diagnostic_items[0]
                self.assertEqual(diagnostic["stage"], "csynth")
                self.assertEqual(diagnostic["category_id"], "unknown")
                self.assertEqual(diagnostic["diagnostic_id"], "unknown_fallback")
                self.assertEqual(len(diagnostic["evidence_fingerprint"]), 64)
                self.assertEqual(event.metadata["owner_authority"], "unknown")
                self.assertFalse(event.evidence_complete)
                self.assertFalse(event.physical_tool_launched)
                self.assertNotIn("/private", str(event.to_dict()))

    def test_same_basename_in_distinct_units_is_not_deduplicated(self):
        parsed = CsynthDiagnosticParser().parse_text(
            "left/input.cpp:4:2: error: expected ';'\n"
            "right/input.cpp:4:2: error: expected ';'",
            report_id="units",
        )
        self.assertEqual(len(parsed.items), 2)
        fingerprints = {item.metadata["evidence_fingerprint"] for item in parsed.items}
        self.assertEqual(len(fingerprints), 2)

    def test_hidden_final_evidence_cannot_be_projected_to_agent_event(self):
        with self.assertRaises(ValueError):
            DiagnosticEventProjector().from_feedback(
                self.safe_report("ERROR: new failure"), run_id="run",
                validation_id="validation", validation_state="hidden_evaluation",
                route_action="repair_candidate",
            )


if __name__ == "__main__":
    unittest.main()
