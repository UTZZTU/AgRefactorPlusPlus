import hashlib
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


def artifact_identity(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "produced_by_current_invocation": True,
    }


class CsynthArtifactFeedbackEvaluatorTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_invocation(self, payload=None, *, source_role=None) -> None:
        value = (
            invocation_payload()
            if payload is None
            else payload
        )
        value["work_dir"] = str(self.root.resolve())
        execution = value["execution"]
        if (execution["status"] == "completed" and execution["returncode"] == 0
                and not execution["timeout"]):
            report = self.root / "csynth/solution/syn/report/top_hls_csynth.rpt"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("current synthesis report\n", encoding="utf-8")
            value["expected_report"] = artifact_identity(report)
        if source_role is not None:
            source = self.root / "top_hls.cpp"
            source.write_text("void top_hls() {}\n", encoding="utf-8")
            value["source_files"] = [source.name]
            value["compile_units"] = [source.name]
            value["source_provenance"] = {
                source.name: {
                    "path": str(source.resolve()),
                    "role": source_role,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            }
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
        invocation_path = self.root / "csynth_invocation.json"
        value = json.loads(invocation_path.read_text(encoding="utf-8"))
        value["diagnostic_log"] = artifact_identity(path)
        invocation_path.write_text(json.dumps(value), encoding="utf-8")
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

    def test_uncompleted_invocation_downgrades_diagnostic_owner(self) -> None:
        payload = invocation_payload(returncode=None, execution_status="launch_error")
        self.write_invocation(payload)
        self.write_log(
            "ERROR: [HLS 207-3776] use of undeclared "
            "identifier 'N' (top_hls.cpp:4:2)"
        )

        report = self.evaluate(status="csynth_failed", owner=FeedbackOwner.CANDIDATE)

        diagnostic = next(
            item
            for item in report.items
            if item.category is FeedbackCategory.UNDECLARED_SYMBOL
        )
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(report.metadata["command_completion_proven"])
        self.assertEqual(
            report.metadata["failure_reason"],
            "command_completion_not_proven",
        )
        self.assertEqual(
            diagnostic.evidence_ref,
            str((self.root / "csynth" / "solution" / "solution.log").resolve()),
        )

    def test_completed_recursive_diagnostic_without_provenance_has_unknown_owner(self) -> None:
        self.write_invocation(invocation_payload(returncode=1))
        self.write_log(
            "ERROR: [HLS 214-139] Recursive function calls are not supported "
            "(top_hls.cpp:10:1)"
        )

        report = self.evaluate(status="csynth_failed", owner=FeedbackOwner.CANDIDATE)
        diagnostic = next(
            item
            for item in report.items
            if item.metadata.get("parser_rule") == "unsupported_recursive_function"
        )

        self.assertEqual(diagnostic.category, FeedbackCategory.UNSUPPORTED_CONSTRUCT)
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertTrue(diagnostic.metadata["command_completion_proven"])

    def test_completed_recursive_diagnostic_with_valid_candidate_provenance_is_repairable(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="candidate")
        self.write_log(
            "ERROR: [HLS 214-139] Recursive function calls are not supported "
            "(top_hls.cpp:10:1)"
        )
        report = self.evaluate(status="csynth_failed", owner=FeedbackOwner.TOOLCHAIN)
        diagnostic = report.items[0]
        self.assertEqual(diagnostic.category, FeedbackCategory.UNSUPPORTED_CONSTRUCT)
        self.assertEqual(diagnostic.owner, FeedbackOwner.CANDIDATE)
        self.assertTrue(diagnostic.metadata["repair_eligible"])
        self.assertTrue(diagnostic.metadata["evidence_complete"])
        self.assertEqual(diagnostic.metadata["owner_authority"], "source_compile_unit")

    def test_conflicting_legacy_success_downgrades_diagnostic_owner(self) -> None:
        self.write_invocation(invocation_payload(returncode=1))
        self.write_log(
            "ERROR: [HLS 214-139] Recursive function calls are not supported "
            "(top_hls.cpp:10:1)"
        )

        report = self.evaluate(status="succeeded", owner=FeedbackOwner.CANDIDATE)
        diagnostic = next(
            item
            for item in report.items
            if item.metadata.get("parser_rule") == "unsupported_recursive_function"
        )

        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(diagnostic.metadata["evidence_consistent"])
        self.assertEqual(
            report.metadata["failure_reason"],
            "inconsistent_execution_evidence",
        )

    def test_timed_out_recursive_diagnostic_has_unknown_owner(self) -> None:
        self.write_invocation(
            invocation_payload(returncode=None, timeout=True)
        )
        self.write_log(
            "ERROR: [HLS 214-139] Recursive function calls are not supported "
            "(top_hls.cpp:10:1)"
        )

        report = self.evaluate(status="timeout", owner=FeedbackOwner.CANDIDATE)
        diagnostic = next(
            item
            for item in report.items
            if item.metadata.get("parser_rule") == "unsupported_recursive_function"
        )

        self.assertEqual(diagnostic.category, FeedbackCategory.UNSUPPORTED_CONSTRUCT)
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(diagnostic.metadata["command_completion_proven"])

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

    def test_caller_owner_without_provenance_is_not_forwarded(self) -> None:
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
            FeedbackOwner.UNKNOWN,
        )

    def test_verified_original_manifest_overrides_candidate_caller(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="original")
        self.write_log("top_hls.cpp:4:2: error: use of undeclared identifier 'N'")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.ORIGINAL)
        self.assertFalse(diagnostic.metadata["repair_eligible"])

    def test_source_changed_after_invocation_cannot_be_owned(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="candidate")
        self.write_log("top_hls.cpp:4:2: error: use of undeclared identifier 'N'")
        (self.root / "top_hls.cpp").write_text("void different_source() {}\n", encoding="utf-8")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(diagnostic.metadata["repair_eligible"])

    def test_log_changed_after_invocation_cannot_be_owned(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="candidate")
        log = self.write_log("top_hls.cpp:4:2: error: use of undeclared identifier 'N'")
        log.write_text(log.read_text(encoding="utf-8") + "\nINFO: another invocation\n", encoding="utf-8")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertEqual(diagnostic.metadata["failure_reason"], "diagnostic_identity_not_proven")

    def test_compiled_sources_and_provenance_must_agree(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="candidate")
        self.write_log("top_hls.cpp:4:2: error: use of undeclared identifier 'N'")
        path = self.root / "csynth_invocation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["compile_units"] = ["some_other.cpp"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(diagnostic.metadata["evidence_complete"])

    def test_unknown_error_without_source_remains_unknown_with_candidate_manifest(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="candidate")
        self.write_log("ERROR: [HLS 999-123] new synthesis failure")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertEqual(diagnostic.category, FeedbackCategory.UNKNOWN)
        self.assertTrue(diagnostic.blocking)

    def test_zero_exit_with_error_log_and_missing_report_cannot_prove_candidate_failure(self) -> None:
        self.write_invocation(source_role="candidate")
        report_path = self.root / "csynth/solution/syn/report/top_hls_csynth.rpt"
        report_path.unlink()
        self.write_log("top_hls.cpp:4:2: error: use of undeclared identifier 'N'")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(diagnostic.metadata["repair_eligible"])

    def test_same_basename_in_unlisted_dependency_cannot_prove_candidate_owner(self) -> None:
        self.write_invocation(invocation_payload(returncode=1), source_role="candidate")
        dependency = self.root / "dependency/top_hls.cpp"
        dependency.parent.mkdir()
        dependency.write_text("int dependency_value;\n", encoding="utf-8")
        self.write_log("dependency/top_hls.cpp:4:2: error: use of undeclared identifier 'N'")
        diagnostic = self.evaluate(status="csynth_failed").items[0]
        self.assertEqual(diagnostic.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(diagnostic.metadata["repair_eligible"])

    def test_success_requires_report_file_and_current_report_identity(self) -> None:
        for tamper in ("missing", "changed", "stale"):
            with self.subTest(tamper=tamper):
                self.write_invocation()
                path = self.root / "csynth_invocation.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                report_path = Path(payload["expected_report"]["path"])
                if tamper == "missing":
                    report_path.unlink()
                elif tamper == "changed":
                    report_path.write_text("other invocation report\n", encoding="utf-8")
                else:
                    payload["expected_report"]["produced_by_current_invocation"] = False
                    path.write_text(json.dumps(payload), encoding="utf-8")
                report = self.evaluate()
                self.assertTrue(report.blocking)
                self.assertFalse(report.metadata["report_identity_proven"])

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
