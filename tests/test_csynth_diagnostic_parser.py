import unittest

from agrefactor.evaluation import CsynthDiagnosticParser
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


class CsynthDiagnosticParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CsynthDiagnosticParser()

    def parse(
        self,
        text: str,
        *,
        owner=FeedbackOwner.CANDIDATE,
        report_id="csynth-log",
    ) -> FeedbackReport:
        return self.parser.parse_text(
            text,
            report_id=report_id,
            evidence_ref="/tmp/solution.log",
            owner=owner,
        )

    def test_ignores_info_messages(self) -> None:
        report = self.parse(
            "\n".join(
                [
                    "INFO: [SCHED 204-11] Starting scheduling ...",
                    "INFO: [SCHED 204-11] Finished scheduling.",
                    "INFO: [HLS 200-10] Checking synthesizability ...",
                ]
            )
        )

        self.assertEqual(report.items, ())
        self.assertEqual(
            report.metadata["parsed_diagnostic_count"],
            0,
        )

    def test_ordinary_warnings_are_not_emitted(self) -> None:
        report = self.parse(
            "\n".join(
                [
                    (
                        "WARNING: [RTGEN 206-101] Setting dangling "
                        "out port 'x' to 0."
                    ),
                    (
                        "WARNING: [RTGEN 206-101] Design contains "
                        "AXI ports. Reset is fixed."
                    ),
                ]
            )
        )

        self.assertEqual(report.items, ())
        self.assertEqual(
            report.metadata["ignored_warning_count"],
            2,
        )
        self.assertEqual(
            len(report.source_evidence["diagnostics"]),
            2,
        )

    def test_parses_undeclared_identifier(self) -> None:
        report = self.parse(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'N' (process_top_hls.cpp:42:7)"
        )
        item = report.items[0]

        self.assertEqual(item.stage, FeedbackStage.CSYNTH)
        self.assertEqual(
            item.category,
            FeedbackCategory.UNDECLARED_SYMBOL,
        )
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)
        self.assertEqual(item.owner, FeedbackOwner.CANDIDATE)
        self.assertEqual(item.metadata["message_id"], "HLS 207-3776")
        self.assertEqual(item.metadata["file"], "process_top_hls.cpp")
        self.assertEqual(item.metadata["line"], 42)
        self.assertEqual(item.metadata["column"], 7)

    def test_deduplicates_hls_and_compiler_echo(self) -> None:
        report = self.parse(
            "\n".join(
                [
                    (
                        "process_top_hls.cpp:42:7: error: use of "
                        "undeclared identifier 'N'"
                    ),
                    (
                        "ERROR: [HLS 207-3776] use of undeclared "
                        "identifier 'N' "
                        "(process_top_hls.cpp:42:7)"
                    ),
                ]
            )
        )

        self.assertEqual(len(report.items), 1)
        item = report.items[0]
        self.assertEqual(item.metadata["occurrence_count"], 2)
        self.assertEqual(item.metadata["message_id"], "HLS 207-3776")
        self.assertEqual(
            len(report.source_evidence["duplicates"]),
            1,
        )

    def test_expected_token_is_syntax_error(self) -> None:
        item = self.parse(
            "ERROR: [HLS 207-7] expected ')' "
            "(process_top_hls.cpp:8:12)"
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.SYNTAX_ERROR,
        )
        self.assertEqual(
            item.metadata["parser_rule"],
            "expected_token",
        )

    def test_invalid_goto_is_syntax_error(self) -> None:
        item = self.parse(
            "ERROR: [HLS 207-2686] cannot jump from this "
            "goto statement to its label "
            "(process_top_hls.cpp:20:3)"
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.SYNTAX_ERROR,
        )
        self.assertEqual(
            item.metadata["parser_rule"],
            "invalid_goto_control_flow",
        )

    def test_s_axilite_bundle_mismatch_is_configuration(
        self,
    ) -> None:
        item = self.parse(
            "ERROR: [HLS 214-219] Vitis kernel mode requires "
            "that all s_axilite ports must be bundled into one "
            "bundle. The following ports have different bundle "
            "names. (top_hls.cpp:5:1)"
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.INVALID_CONFIGURATION,
        )
        self.assertEqual(
            item.metadata["parser_rule"],
            "s_axilite_bundle_mismatch",
        )

    def test_pipeline_carried_dependence_is_warning(self) -> None:
        item = self.parse(
            "WARNING: [HLS 200-880] The II Violation in "
            "module 'top_Pipeline_L1' (loop 'L1'): Unable to "
            "enforce a carried dependence constraint "
            "(II = 2, distance = 1, offset = 0) between a "
            "store operation and a load operation."
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.PIPELINE_DEPENDENCY,
        )
        self.assertEqual(
            item.severity,
            FeedbackSeverity.WARNING,
        )
        self.assertFalse(item.blocking)
        self.assertEqual(item.owner, FeedbackOwner.CANDIDATE)

    def test_loop_exit_scheduling_remains_unknown_warning(
        self,
    ) -> None:
        item = self.parse(
            "WARNING: [HLS 200-878] Unable to schedule the "
            "loop exit test ('icmp' operation) in the first "
            "pipeline iteration (II = 2 cycles)."
        ).items[0]

        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.severity, FeedbackSeverity.WARNING)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)
        self.assertEqual(
            item.metadata["classification_confidence"],
            "partial",
        )

    def test_unknown_error_is_blocking_unknown(self) -> None:
        item = self.parse(
            "ERROR: [HLS 999-123] A future synthesis "
            "diagnostic that the parser has never seen."
        ).items[0]

        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)
        self.assertTrue(item.blocking)
        self.assertEqual(
            item.metadata["parser_rule"],
            "unknown_fallback",
        )
        self.assertIn("HLS 999-123", item.detail)

    def test_aggregate_error_is_suppressed_with_specific_error(
        self,
    ) -> None:
        report = self.parse(
            "\n".join(
                [
                    (
                        "ERROR: [HLS 207-3776] use of undeclared "
                        "identifier 'N' "
                        "(process_top_hls.cpp:42:7)"
                    ),
                    (
                        "ERROR: [HLS 200-1715] Encountered "
                        "problem during source synthesis"
                    ),
                ]
            )
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.UNDECLARED_SYMBOL,
        )
        self.assertEqual(
            report.metadata["suppressed_aggregate_count"],
            1,
        )

    def test_aggregate_error_alone_is_unknown(self) -> None:
        item = self.parse(
            "ERROR: [HLS 200-1715] Encountered problem "
            "during source synthesis"
        ).items[0]

        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertTrue(item.blocking)
        self.assertEqual(
            item.metadata["parser_rule"],
            "aggregate_source_synthesis",
        )

    def test_unittest_error_heading_is_rejected(self) -> None:
        report = self.parse(
            "ERROR: test_limit_one_allows_first_and_blocks_second"
        )

        self.assertEqual(report.items, ())
        self.assertEqual(
            report.metadata["rejected_severity_line_count"],
            1,
        )

    def test_generic_source_diagnostic_is_parsed(self) -> None:
        item = self.parse(
            "process_top_hls.cpp:14:9: error: expected ';'"
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.SYNTAX_ERROR,
        )
        self.assertIsNone(item.metadata["message_id"])
        self.assertEqual(item.metadata["line"], 14)
        self.assertEqual(item.metadata["column"], 9)

    def test_error_then_source_location_form_is_parsed(
        self,
    ) -> None:
        item = self.parse(
            "error: top_hls.cpp:3:2: Vitis kernel mode "
            "requires that all s_axilite ports must be "
            "bundled into one bundle."
        ).items[0]

        self.assertEqual(
            item.category,
            FeedbackCategory.INVALID_CONFIGURATION,
        )
        self.assertEqual(item.metadata["file"], "top_hls.cpp")

    def test_default_owner_is_unknown(self) -> None:
        report = self.parser.parse_text(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'N' (top.cpp:1:1)",
            report_id="default-owner",
        )

        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.UNKNOWN,
        )

    def test_accepts_owner_string(self) -> None:
        report = self.parser.parse_text(
            "ERROR: [HLS 207-7] expected ')' (top.cpp:1:1)",
            report_id="owner-string",
            owner="original",
        )

        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.ORIGINAL,
        )

    def test_unknown_diagnostic_does_not_inherit_owner(
        self,
    ) -> None:
        item = self.parse(
            "ERROR: [HLS 999-1] unknown future failure"
        ).items[0]

        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)

    def test_rejects_unknown_owner(self) -> None:
        with self.assertRaises(ValueError):
            self.parser.parse_text(
                "ERROR: [HLS 999-1] failure",
                report_id="bad-owner",
                owner="source_code",
            )

    def test_rejects_non_string_text(self) -> None:
        with self.assertRaises(TypeError):
            self.parser.parse_text(
                {"log": "ERROR"},
                report_id="invalid",
            )

    def test_report_round_trip(self) -> None:
        original = self.parse(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'MAX_N' (top.cpp:9:4)"
        )
        restored = FeedbackReport.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_parser_is_kernel_agnostic(self) -> None:
        families = (
            "array_map",
            "reduction",
            "stencil",
            "multi_output",
            "stream",
            "stateful",
        )

        reports = [
            self.parser.parse_text(
                (
                    "ERROR: [HLS 207-3776] use of undeclared "
                    f"identifier '{family}_bound' "
                    f"({family}.cpp:1:1)"
                ),
                report_id=f"{family}-report",
                owner=FeedbackOwner.CANDIDATE,
            )
            for family in families
        ]

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
