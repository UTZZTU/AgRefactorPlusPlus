import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.evaluation import (
    CsynthArtifactFeedbackEvaluator,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
)


def invocation_payload(
    *,
    returncode=0,
    timeout=False,
    execution_status="completed",
) -> dict:
    return {
        "schema_version": 1,
        "phase": "csynth",
        "top_kernel": "top_hls",
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
            "status": execution_status,
            "returncode": returncode,
            "timeout": timeout,
        },
    }


class CsynthArtifactFeedbackEvaluatorTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_invocation(self, payload=None) -> None:
        value = (
            invocation_payload()
            if payload is None
            else payload
        )
        (self.root / "csynth_invocation.json").write_text(
            json.dumps(value),
            encoding="utf-8",
        )

    def write_log(self, text: str) -> Path:
        path = (
            self.root
            / "csynth"
            / "solution"
            / "solution.log"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def evaluate(
        self,
        *,
        status="succeeded",
        error_msg="",
        owner=FeedbackOwner.CANDIDATE,
        evaluator=None,
    ) -> FeedbackReport:
        active = (
            CsynthArtifactFeedbackEvaluator()
            if evaluator is None
            else evaluator
        )
        return active.evaluate(
            self.root,
            report_id="artifact",
            legacy_status=status,
            error_msg=error_msg,
            owner=owner,
        )

    def test_success_without_log_is_empty(self) -> None:
        self.write_invocation()

        report = self.evaluate()

        self.assertEqual(report.items, ())
        self.assertFalse(report.blocking)
        self.assertFalse(
            report.metadata["diagnostic_exists"]
        )
        self.assertEqual(
            report.metadata["diagnostic_bytes_read"],
            0,
        )

    def test_specific_log_error_suppresses_generic_failure(
        self,
    ) -> None:
        self.write_invocation(
            invocation_payload(returncode=1)
        )
        self.write_log(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'N' (top_hls.cpp:4:2)"
        )

        report = self.evaluate(
            status="csynth_failed",
            error_msg="generic failure",
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

    def test_unknown_error_remains_blocking(self) -> None:
        self.write_invocation(
            invocation_payload(returncode=1)
        )
        self.write_log(
            "ERROR: [HLS 999-123] future synthesis failure"
        )

        report = self.evaluate(status="csynth_failed")

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.UNKNOWN,
        )
        self.assertEqual(
            report.items[0].severity,
            FeedbackSeverity.ERROR,
        )
        self.assertTrue(report.blocking)

    def test_warning_only_does_not_hide_failed_invocation(
        self,
    ) -> None:
        self.write_invocation(
            invocation_payload(returncode=1)
        )
        self.write_log(
            "WARNING: [HLS 200-878] Unable to schedule "
            "the loop exit test ('icmp' operation) in the "
            "first pipeline iteration (II = 2 cycles)."
        )

        report = self.evaluate(status="csynth_failed")

        self.assertEqual(len(report.items), 2)
        self.assertTrue(report.blocking)
        self.assertEqual(
            {
                item.severity
                for item in report.items
            },
            {
                FeedbackSeverity.ERROR,
                FeedbackSeverity.WARNING,
            },
        )

    def test_pipeline_warning_on_success_is_preserved(
        self,
    ) -> None:
        self.write_invocation()
        self.write_log(
            "WARNING: [HLS 200-880] The II Violation in "
            "module 'top_L1' (loop 'L1'): Unable to enforce "
            "a carried dependence constraint "
            "(II = 2, distance = 1, offset = 0) between "
            "a store operation and a load operation."
        )

        report = self.evaluate()

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.PIPELINE_DEPENDENCY,
        )
        self.assertFalse(report.blocking)

    def test_budget_block_works_without_log(self) -> None:
        payload = invocation_payload(
            returncode=None,
            execution_status="blocked_by_budget",
        )
        payload["budget"] = {
            "status": "blocked",
            "resource": "csynth_calls",
            "checkpoint": "before_version_probe",
        }
        self.write_invocation(payload)

        report = self.evaluate(status=None)

        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.BUDGET_EXHAUSTED,
        )

    def test_tail_read_parses_error_at_end(self) -> None:
        self.write_invocation(
            invocation_payload(returncode=1)
        )
        self.write_log(
            ("INFO: filler line\n" * 100)
            + (
                "ERROR: [HLS 207-7] expected ')' "
                "(top_hls.cpp:9:3)\n"
            )
        )

        evaluator = CsynthArtifactFeedbackEvaluator(
            max_diagnostic_bytes=160,
        )
        report = self.evaluate(
            status="csynth_failed",
            evaluator=evaluator,
        )

        self.assertTrue(
            report.metadata["diagnostic_truncated"]
        )
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.SYNTAX_ERROR,
        )

    def test_complete_artifact_loading_is_preserved(
        self,
    ) -> None:
        self.write_invocation()
        log_path = self.write_log("INFO: [HLS 200-10] done")

        report = self.evaluate()
        loading = report.source_evidence[
            "artifact_loading"
        ]

        self.assertEqual(
            loading["work_dir"],
            str(self.root.resolve()),
        )
        self.assertEqual(
            loading["diagnostic_path"],
            str(log_path.resolve()),
        )
        self.assertTrue(loading["exists"])

    def test_owner_context_is_forwarded(self) -> None:
        self.write_invocation(
            invocation_payload(returncode=1)
        )
        self.write_log(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'N' (top_hls.cpp:4:2)"
        )

        report = self.evaluate(
            status="csynth_failed",
            owner=FeedbackOwner.ORIGINAL,
        )

        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.ORIGINAL,
        )

    def test_malformed_invocation_json_is_rejected(
        self,
    ) -> None:
        (self.root / "csynth_invocation.json").write_text(
            "{broken",
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            self.evaluate()

    def test_non_object_invocation_json_is_rejected(
        self,
    ) -> None:
        (self.root / "csynth_invocation.json").write_text(
            "[]",
            encoding="utf-8",
        )

        with self.assertRaises(TypeError):
            self.evaluate()

    def test_missing_invocation_is_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.evaluate()

    def test_invalid_max_bytes_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CsynthArtifactFeedbackEvaluator(
                max_diagnostic_bytes=0,
            )

        with self.assertRaises(TypeError):
            CsynthArtifactFeedbackEvaluator(
                max_diagnostic_bytes=True,
            )

    def test_report_round_trip(self) -> None:
        self.write_invocation(
            invocation_payload(returncode=1)
        )
        self.write_log(
            "ERROR: [HLS 207-7] expected ')' "
            "(top_hls.cpp:9:3)"
        )

        original = self.evaluate(
            status="csynth_failed"
        )
        restored = FeedbackReport.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_evaluator_is_kernel_agnostic(self) -> None:
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
            subdir = self.root / family
            subdir.mkdir()
            payload = invocation_payload(returncode=1)
            payload["top_kernel"] = f"{family}_top"
            (
                subdir / "csynth_invocation.json"
            ).write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            log_path = (
                subdir
                / "csynth"
                / "solution"
                / "solution.log"
            )
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                (
                    "ERROR: [HLS 207-3776] use of "
                    f"undeclared identifier '{family}_n' "
                    f"({family}.cpp:1:1)"
                ),
                encoding="utf-8",
            )
            reports.append(
                CsynthArtifactFeedbackEvaluator().evaluate(
                    subdir,
                    report_id=f"{family}-report",
                    legacy_status="csynth_failed",
                    owner=FeedbackOwner.CANDIDATE,
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
