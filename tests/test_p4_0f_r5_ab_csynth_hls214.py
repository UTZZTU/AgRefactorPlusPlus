from __future__ import annotations

import unittest

from agrefactor.evaluation import (
    CsynthDiagnosticParser,
    FeedbackRouteAction,
    FeedbackRouter,
)
from agrefactor.evidence import FeedbackCategory, FeedbackOwner


class R5ABHls214Tests(unittest.TestCase):
    def setUp(self):
        self.parser = CsynthDiagnosticParser()

    def parse(self, line):
        return self.parser.parse_text(
            line,
            report_id="r5-ab-hls214",
            evidence_ref="solution.log",
            owner=FeedbackOwner.CANDIDATE,
        )

    def test_hls_214_133_is_typed_candidate_failure(self):
        for symbol in ("epsilon", "threshold_2"):
            with self.subTest(symbol=symbol):
                report = self.parse(
                    "ERROR: [HLS 214-133] Global variable "
                    f"'{symbol}' must have definition"
                )
                item = report.items[0]
                self.assertEqual(
                    item.category,
                    FeedbackCategory.UNSUPPORTED_CONSTRUCT,
                )
                self.assertEqual(item.owner, FeedbackOwner.CANDIDATE)
                self.assertEqual(
                    item.metadata["parser_rule"],
                    "global_variable_requires_definition",
                )
                self.assertEqual(
                    item.metadata["affected_symbol"], symbol
                )

    def test_recognized_item_routes_to_candidate_repair(self):
        report = self.parse(
            "ERROR: [HLS 214-133] Global variable "
            "'epsilon' must have definition"
        )
        decision = FeedbackRouter().route(
            report,
            decision_id="r5-ab-route",
        )
        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )

    def test_same_words_without_exact_message_id_remain_unknown(self):
        report = self.parse(
            "top.cpp:3:1: error: Global variable "
            "'epsilon' must have definition"
        )
        item = report.items[0]
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)

    def test_other_hls_214_code_remains_unknown(self):
        report = self.parse(
            "ERROR: [HLS 214-999] Global variable "
            "'epsilon' must have definition"
        )
        item = report.items[0]
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
