import json
import unittest

from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


class FeedbackEnumTests(unittest.TestCase):
    def test_stage_vocabulary_covers_evaluation_pipeline(self) -> None:
        self.assertEqual(
            {item.value for item in FeedbackStage},
            {
                "input",
                "configuration",
                "static_check",
                "compile",
                "link",
                "test",
                "csim",
                "csynth",
                "cosim",
                "toolchain",
            },
        )

    def test_category_vocabulary_covers_stage2_failures(
        self,
    ) -> None:
        required = {
            "invalid_input",
            "invalid_configuration",
            "functional_mismatch",
            "runtime_crash",
            "timeout",
            "unsupported_construct",
            "unknown_bound",
            "pipeline_dependency",
            "memory_port_contention",
            "timing_violation",
            "resource_limit",
            "toolchain_failure",
            "budget_exhausted",
            "unknown",
        }

        self.assertTrue(
            required.issubset(
                {item.value for item in FeedbackCategory}
            )
        )

    def test_only_error_and_fatal_are_blocking(self) -> None:
        self.assertFalse(FeedbackSeverity.INFO.blocking)
        self.assertFalse(FeedbackSeverity.WARNING.blocking)
        self.assertTrue(FeedbackSeverity.ERROR.blocking)
        self.assertTrue(FeedbackSeverity.FATAL.blocking)


class FeedbackItemTests(unittest.TestCase):
    def test_create_item_with_enum_values(self) -> None:
        item = FeedbackItem(
            feedback_id="compile.syntax.1",
            stage=FeedbackStage.COMPILE,
            category=FeedbackCategory.SYNTAX_ERROR,
            severity=FeedbackSeverity.ERROR,
            owner=FeedbackOwner.CANDIDATE,
            summary="Candidate compilation failed",
            detail="expected ';' after expression",
            source="testbench_preflight",
            evidence_ref="preflight.json",
            metadata={"line": 18},
        )

        self.assertTrue(item.blocking)
        self.assertEqual(item.owner, FeedbackOwner.CANDIDATE)
        self.assertEqual(item.metadata["line"], 18)

    def test_accept_string_enum_values(self) -> None:
        item = FeedbackItem(
            feedback_id="csim.timeout",
            stage="csim",
            category="timeout",
            severity="fatal",
            owner="toolchain",
            summary="CSIM timed out",
        )

        self.assertEqual(item.stage, FeedbackStage.CSIM)
        self.assertEqual(item.category, FeedbackCategory.TIMEOUT)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)
        self.assertEqual(item.owner, FeedbackOwner.TOOLCHAIN)

    def test_optional_text_is_normalized(self) -> None:
        item = FeedbackItem(
            feedback_id="  id  ",
            stage="test",
            category="functional_mismatch",
            severity="error",
            summary="  mismatch  ",
            detail="   ",
            source="  csim  ",
            evidence_ref="  evidence.json  ",
        )

        self.assertEqual(item.feedback_id, "id")
        self.assertEqual(item.summary, "mismatch")
        self.assertIsNone(item.detail)
        self.assertEqual(item.source, "csim")
        self.assertEqual(item.evidence_ref, "evidence.json")

    def test_round_trip_dict(self) -> None:
        original = FeedbackItem(
            feedback_id="csynth.resource",
            stage=FeedbackStage.CSYNTH,
            category=FeedbackCategory.RESOURCE_LIMIT,
            severity=FeedbackSeverity.ERROR,
            owner=FeedbackOwner.CANDIDATE,
            summary="Resource limit exceeded",
            metadata={
                "resource": "LUT",
                "required": 120,
                "available": 100,
            },
        )

        restored = FeedbackItem.from_dict(original.to_dict())

        self.assertEqual(restored, original)

    def test_to_dict_is_json_serializable(self) -> None:
        item = FeedbackItem(
            feedback_id="timing.warning",
            stage="csynth",
            category="timing_violation",
            severity="warning",
            summary="Estimated timing missed",
        )

        encoded = json.dumps(item.to_dict())

        self.assertIn('"stage": "csynth"', encoded)
        self.assertIn('"blocking": false', encoded)

    def test_metadata_is_deep_copied(self) -> None:
        metadata = {"nested": {"values": [1, 2]}}
        item = FeedbackItem(
            feedback_id="copy",
            stage="input",
            category="invalid_input",
            severity="error",
            summary="Invalid input",
            metadata=metadata,
        )

        metadata["nested"]["values"].append(3)

        self.assertEqual(
            item.metadata,
            {"nested": {"values": [1, 2]}},
        )

    def test_reject_non_finite_metadata(self) -> None:
        with self.assertRaises(ValueError):
            FeedbackItem(
                feedback_id="nan",
                stage="csynth",
                category="timing_violation",
                severity="warning",
                summary="Invalid metric",
                metadata={"slack": float("nan")},
            )

    def test_reject_unknown_enum_value(self) -> None:
        with self.assertRaises(ValueError):
            FeedbackItem(
                feedback_id="bad-stage",
                stage="rtl_cosim",
                category="unknown",
                severity="error",
                summary="Unsupported stage",
            )

    def test_reject_unknown_mapping_field(self) -> None:
        with self.assertRaises(ValueError):
            FeedbackItem.from_dict(
                {
                    "feedback_id": "unknown-field",
                    "stage": "compile",
                    "category": "syntax_error",
                    "severity": "error",
                    "summary": "Compile failed",
                    "repair_prompt": "change code",
                }
            )

    def test_reject_derived_blocking_conflict(self) -> None:
        with self.assertRaises(ValueError):
            FeedbackItem.from_dict(
                {
                    "feedback_id": "conflict",
                    "stage": "compile",
                    "category": "syntax_error",
                    "severity": "error",
                    "summary": "Compile failed",
                    "blocking": False,
                }
            )


class FeedbackReportTests(unittest.TestCase):
    def make_warning(self) -> FeedbackItem:
        return FeedbackItem(
            feedback_id="csynth.timing.warning",
            stage=FeedbackStage.CSYNTH,
            category=FeedbackCategory.TIMING_VIOLATION,
            severity=FeedbackSeverity.WARNING,
            owner=FeedbackOwner.CANDIDATE,
            summary="Estimated timing is close to the target",
        )

    def make_error(self) -> FeedbackItem:
        return FeedbackItem(
            feedback_id="csim.functional.error",
            stage=FeedbackStage.CSIM,
            category=FeedbackCategory.FUNCTIONAL_MISMATCH,
            severity=FeedbackSeverity.ERROR,
            owner=FeedbackOwner.CANDIDATE,
            summary="Candidate output mismatched",
        )

    def test_empty_report_is_non_blocking(self) -> None:
        report = FeedbackReport(
            report_id="empty",
            source="input_validator",
        )

        self.assertFalse(report.blocking)
        self.assertIsNone(report.highest_severity)
        self.assertEqual(report.items, ())

    def test_report_derives_highest_severity(self) -> None:
        report = FeedbackReport(
            report_id="mixed",
            source="evaluation_pipeline",
            items=(self.make_warning(), self.make_error()),
        )

        self.assertTrue(report.blocking)
        self.assertEqual(
            report.highest_severity,
            FeedbackSeverity.ERROR,
        )

    def test_report_preserves_source_evidence(self) -> None:
        report = FeedbackReport(
            report_id="preflight",
            source="testbench_preflight",
            items=(self.make_error(),),
            source_evidence={
                "status": "failed",
                "failure_owner": "candidate",
                "diagnostics": [
                    {"kind": "output_mismatch"}
                ],
            },
            metadata={"adapter_version": 1},
        )

        payload = report.to_dict()

        self.assertEqual(
            payload["source_evidence"]["failure_owner"],
            "candidate",
        )
        self.assertEqual(payload["metadata"]["adapter_version"], 1)

    def test_report_round_trip_dict(self) -> None:
        original = FeedbackReport(
            report_id="round-trip",
            source="csim_suite",
            items=(self.make_warning(), self.make_error()),
            source_evidence={"status": "failed"},
            metadata={"suite_id": "generic-public"},
        )

        restored = FeedbackReport.from_dict(original.to_dict())

        self.assertEqual(restored, original)

    def test_report_rejects_duplicate_feedback_ids(self) -> None:
        item = self.make_error()

        with self.assertRaises(ValueError):
            FeedbackReport(
                report_id="duplicates",
                source="csim_suite",
                items=(item, item),
            )

    def test_report_rejects_non_item_entry(self) -> None:
        with self.assertRaises(TypeError):
            FeedbackReport(
                report_id="invalid",
                source="parser",
                items=({"feedback_id": "mapping"},),
            )

    def test_report_from_dict_accepts_item_mappings(self) -> None:
        report = FeedbackReport.from_dict(
            {
                "report_id": "mapped",
                "source": "compile_parser",
                "items": [
                    {
                        "feedback_id": "compile.syntax",
                        "stage": "compile",
                        "category": "syntax_error",
                        "severity": "error",
                        "owner": "candidate",
                        "summary": "Compilation failed",
                    }
                ],
            }
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.SYNTAX_ERROR,
        )

    def test_report_rejects_summary_conflicts(self) -> None:
        payload = FeedbackReport(
            report_id="conflict",
            source="parser",
            items=(self.make_error(),),
        ).to_dict()
        payload["highest_severity"] = "warning"

        with self.assertRaises(ValueError):
            FeedbackReport.from_dict(payload)

    def test_report_is_source_and_kernel_agnostic(self) -> None:
        sources = (
            "input_validator",
            "testbench_preflight",
            "public_test",
            "csim_suite",
            "csynth_report",
            "tool_launcher",
        )
        families = (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        )

        reports = [
            FeedbackReport(
                report_id=f"{source}-{family}",
                source=source,
                metadata={"kernel_family": family},
            )
            for source, family in zip(sources, families)
        ]

        self.assertEqual(len(reports), len(families))


if __name__ == "__main__":
    unittest.main()
