import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.evidence import (
    CaseRecorder,
    CaseStore,
    DiagnosticEventProjector,
    FeedbackOwner,
)
from agrefactor.evaluation import CsynthArtifactFeedbackEvaluator, CsynthFeedbackViewAdapter
from agrefactor.evaluation.catalog_diagnostic_checks import evaluate_catalog_check


CHECK_ID = "csynth_top_argument_pointer_member_contract"
DIAGNOSTIC = (
    "ERROR: [HLS 214-298] Struct type with pointer type inside on top function "
    "argument is not supported\n"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CatalogDiagnosticCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def evaluate(self, source, *, diagnostic=DIAGNOSTIC, parse_context=True):
        source_path = self.root / "top_hls.cpp"
        source_path.write_text(source, encoding="utf-8")
        invocation = {
            "schema_version": 1,
            "phase": "csynth",
            "work_dir": str(self.root),
            "top_kernel": "top_hls",
            "source_files": [source_path.name],
            "compile_units": [source_path.name],
            "source_provenance": {
                source_path.name: {
                    "path": str(source_path),
                    "role": "candidate",
                    "sha256": sha256(source_path),
                }
            },
            "execution": {
                "status": "completed",
                "returncode": 1,
                "timeout": False,
            },
        }
        if parse_context:
            invocation["catalog_parse_context"] = {
                "schema_version": 1,
                "mode": "libclang_default",
                "compile_flags": [],
                "include_roots": [str(self.root)],
                "source_files": [source_path.name],
            }
        log = self.root / "csynth" / "solution" / "solution.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(diagnostic, encoding="utf-8")
        invocation["diagnostic_log"] = {
            "path": str(log),
            "size_bytes": log.stat().st_size,
            "sha256": sha256(log),
            "produced_by_current_invocation": True,
        }
        (self.root / "csynth_invocation.json").write_text(
            json.dumps(invocation), encoding="utf-8"
        )
        return CsynthArtifactFeedbackEvaluator().evaluate(
            self.root,
            report_id="validation.csynth.operator",
            legacy_status="csynth_failed",
        )

    def persist_case(self, report, name):
        safe = CsynthFeedbackViewAdapter().to_agent_report(
            report, report_id=f"{name}.agent"
        )
        event = DiagnosticEventProjector().from_feedback(
            safe,
            run_id=f"run-{name}",
            validation_id=f"validation-{name}",
            validation_state="csynth",
            route_action="review_unknown",
            target={},
        ).to_dict()
        cases_root = self.root / f"cases-{name}"
        recorder = CaseRecorder(cases_root, f"validation-{name}")
        self.assertTrue(recorder.start_case(event))
        case_id = CaseStore(cases_root).open_cases()[0].case_id
        return CaseStore(cases_root).read_case(case_id), event

    def test_satisfied_check_reaches_safe_event_and_case_without_granting_owner(self):
        report = self.evaluate(
            "struct Payload { int *data; };\n"
            "struct Packet { Payload payload; };\n"
            "void top_hls(Packet packet) {}\n"
        )
        item = report.items[0]
        self.assertEqual(item.metadata["diagnostic_check"]["result"], "satisfied")
        self.assertIn("packet.payload.data", item.metadata["diagnostic_check"]["summary"])
        self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(item.metadata["repair_eligible"])
        self.assertTrue(item.blocking)

        case, event = self.persist_case(report, "satisfied")
        self.assertEqual(
            report.items[0].metadata["diagnostic_check"]["result"], "satisfied"
        )
        hidden_event = {
            **event,
            "event_id": "diagnostic-hidden",
            "stage": "hidden_evaluation",
            "split": "hidden",
        }
        cases_root = self.root / "cases-satisfied"
        recorder = CaseRecorder(cases_root, "validation-satisfied")
        public_id = CaseStore(cases_root).open_cases()[0].case_id
        self.assertTrue(recorder.start_case(event))
        self.assertFalse(recorder.record_event(hidden_event))
        case = CaseStore(cases_root).read_case(public_id)
        self.assertEqual(len(case.evidence_checks), 1)
        self.assertEqual(case.evidence_checks[0].check_id, CHECK_ID)
        self.assertEqual(case.evidence_checks[0].result.value, "satisfied")
        self.assertEqual(case.evidence_checks[0].evidence_refs, (event["event_id"],))

    def test_complete_contract_without_pointer_member_is_contradicted(self):
        report = self.evaluate(
            "struct Packet { int value; };\n"
            "void top_hls(Packet packet) {}\n"
        )
        check = report.items[0].metadata["diagnostic_check"]
        self.assertEqual(check["result"], "contradicted")
        self.assertEqual(report.items[0].owner, FeedbackOwner.UNKNOWN)
        self.assertFalse(report.items[0].metadata["repair_eligible"])
        case, _event = self.persist_case(report, "contradicted")
        self.assertEqual(case.evidence_checks[0].result.value, "contradicted")

    def test_missing_parse_context_fails_closed(self):
        report = self.evaluate(
            "struct Packet { int *data; };\n"
            "void top_hls(Packet packet) {}\n",
            parse_context=False,
        )
        self.assertEqual(report.items[0].metadata["diagnostic_check"]["result"], "missing")
        case, _event = self.persist_case(report, "missing")
        self.assertEqual(case.evidence_checks[0].result.value, "missing")

    def test_unverified_source_and_unreviewed_flags_return_missing(self):
        report = self.evaluate(
            "struct Packet { int *data; };\n"
            "void top_hls(Packet packet) {}\n"
        )
        invocation_path = self.root / "csynth_invocation.json"
        invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
        item = report.items[0]
        invocation["source_provenance"]["top_hls.cpp"]["sha256"] = "0" * 64
        result = evaluate_catalog_check(self.root, invocation, item)
        self.assertEqual(result["result"], "missing")
        invocation["source_provenance"]["top_hls.cpp"]["sha256"] = sha256(
            self.root / "top_hls.cpp"
        )
        invocation["catalog_parse_context"]["compile_flags"] = [
            "-include", "/tmp/unregistered.h"
        ]
        result = evaluate_catalog_check(self.root, invocation, item)
        self.assertEqual(result["result"], "missing")

    def test_nonmatching_diagnostic_does_not_run_pilot_check(self):
        report = self.evaluate(
            "struct Packet { int *data; };\n"
            "void top_hls(Packet packet) {}\n",
            diagnostic="ERROR: [HLS 214-298] unrelated diagnostic text\n",
        )
        self.assertNotIn("diagnostic_check", report.items[0].metadata)

    def test_hidden_and_unmatched_catalog_records_do_not_select_the_check(self):
        report = self.evaluate(
            "struct Packet { int *data; };\n"
            "void top_hls(Packet packet) {}\n"
        )
        item = report.items[0]
        from agrefactor.evaluation.catalog_diagnostic_checks import evaluate_catalog_check

        invocation_path = self.root / "csynth_invocation.json"
        invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
        invocation["evaluation_split"] = "hidden"
        self.assertIsNone(evaluate_catalog_check(self.root, invocation, item))
        metadata = dict(item.metadata)
        metadata["diagnostic_id"] = "another_rule"
        item = type(item)(
            feedback_id=item.feedback_id,
            stage=item.stage,
            category=item.category,
            severity=item.severity,
            owner=item.owner,
            summary=item.summary,
            detail=item.detail,
            source=item.source,
            evidence_ref=item.evidence_ref,
            metadata=metadata,
        )
        invocation.pop("evaluation_split")
        self.assertIsNone(evaluate_catalog_check(self.root, invocation, item))


if __name__ == "__main__":
    unittest.main()
