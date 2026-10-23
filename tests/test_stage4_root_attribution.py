import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agrefactor.evidence.case_capture import capture_candidate_recovery
from agrefactor.evidence.case_store import CaseStore
from agrefactor.evidence.root_run_record import RootRunRecordStore
from agrefactor.evidence.stage4_case_attribution import adjudicate_formal_root_run


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class RootAttributionTests(unittest.TestCase):
    def add_validation(
        self,
        root,
        validation_id,
        stage,
        verdict,
        source,
        *,
        attempt_id=None,
    ):
        source_ref = root.store_validation_artifact(source)
        receipt_ref = root.store_validation_artifact(b"receipt:" + validation_id.encode())
        report_ref = root.store_validation_artifact(b"report:" + validation_id.encode())
        root.record_validation_session(
            {
                "schema_version": 1,
                "validation_id": validation_id,
                "root_run_id": root.run_id,
                "validation_run_id": validation_id,
                "task_id": "task-1",
                "attempt_id": attempt_id,
                "planned_public_stages": [stage],
                "evidence_view": "agent_safe",
                "hidden_content_persisted": False,
            }
        )
        source_digest = source_ref["sha256"]
        contract = {
            "schema_version": 1,
            "business_stage": stage,
            "validation_state": stage,
            "handler": f"handler.{stage}",
            "handler_version": 1,
            "semantics_version": 1,
            "orchestrator_version": 1,
            "public_suites": [],
            "target_profile": {"toolchain": "vitis", "version": "2023.2"},
            "source_package": None,
            "inputs": {
                "candidate_code": {"sha256": source_digest, "size_bytes": len(source)},
                "candidate_top_function": "top",
            },
        }
        input_snapshot = {
            "schema_version": 1,
            "input_contents": [
                {"role": "candidate_code", "logical_name": "candidate_code", **source_ref}
            ],
            "files": [],
            "source_package_files": [],
            "upstream_artifacts": [],
            "source_package": None,
            "complete": True,
            "replay_eligible": True,
            "capture_errors": [],
        }
        stage_contract_sha = hashlib.sha256(canonical(contract)).hexdigest()
        input_snapshot_sha = hashlib.sha256(canonical(input_snapshot)).hexdigest()
        start_id = f"{validation_id}:{stage}:started"
        result_id = f"{validation_id}:{stage}:result"
        common = {
            "schema_version": 1,
            "root_run_id": root.run_id,
            "validation_id": validation_id,
            "validation_run_id": validation_id,
            "attempt_id": attempt_id,
            "state": stage,
            "stage": stage,
            "split": "public",
            "evidence_view": "agent_safe",
            "hidden_content_persisted": False,
        }
        root.record_validation_evidence(
            {
                **common,
                "evidence_id": start_id,
                "step_id": f"{validation_id}.{stage}",
                "execution_status": "started",
                "capture_status": "complete",
                "stage_contract": contract,
                "stage_contract_sha256": stage_contract_sha,
                "input_snapshot": input_snapshot,
                "input_snapshot_sha256": input_snapshot_sha,
            }
        )
        root.record_validation_evidence(
            {
                **common,
                "evidence_id": result_id,
                "step_id": f"{validation_id}.{stage}",
                "execution_status": "completed",
                "evidence_verdict": verdict,
                "capture_status": "complete",
                "result_source_complete": True,
                "started_evidence_id": start_id,
                "stage_contract_sha256": stage_contract_sha,
                "input_snapshot_sha256": input_snapshot_sha,
                "result_report_ref": report_ref,
                "result_report_sha256": report_ref["sha256"],
                "output_artifacts": [receipt_ref],
                "actual_consumed_inputs": [
                    {
                        "role": "actual_consumed_input",
                        "logical_path": "candidate.cpp",
                        "matches_declared_input": True,
                        **source_ref,
                    }
                ],
                "environment_observation": {
                    "verified": True,
                    "versions": ["2023.2"],
                },
            }
        )
        return result_id

    @staticmethod
    def event(event_id, validation_id, stage):
        return {
            "event_id": event_id,
            "run_id": "run-1",
            "validation_id": validation_id,
            "stage": stage,
            "event_type": "blocking_diagnostic",
            "evidence_complete": True,
            "physical_tool_launched": True,
            "evidence_view": "agent_safe",
            "visibility": "public",
            "split": "public",
            "hidden_content_persisted": False,
        }

    def test_root_relations_and_decisions_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = RootRunRecordStore(Path(directory), "run-1")
            root.register_case("case-a")
            failure_a = self.add_validation(
                root, "validation-a", "csim", "confirmed_failed", b"candidate-0"
            )
            event_a = self.event("event-a", "validation-a", "public_evaluation")
            root.record_event(event_a)
            root.set_case_relation(
                "event", "event-a", state="candidate", case_ids=("case-a",),
                basis="formal_capture_pending_adjudication",
            )
            self.add_validation(
                root, "baseline", "csynth", "passed", b"candidate-0"
            )
            root.prepare_attempt(
                "attempt-1",
                {
                    "attempt_id": "attempt-1",
                    "run_id": "run-1",
                    "pre_action_event_refs": ["event-a"],
                },
            )
            root.set_case_relation(
                "attempt", "attempt-1", state="candidate", case_ids=("case-a",),
                basis="single_case_capture_pending_adjudication",
            )
            root.mark_attempt_dispatched("attempt-1")
            root.update_attempt(
                "attempt-1",
                {
                    "candidate_input_sha256": hashlib.sha256(b"candidate-0").hexdigest(),
                    "change": {
                        "component": "candidate",
                        "before_sha256": hashlib.sha256(b"candidate-0").hexdigest(),
                        "after_sha256": hashlib.sha256(b"candidate-1").hexdigest(),
                    },
                },
            )
            self.add_validation(
                root,
                "attempt-validation",
                "preflight",
                "passed",
                b"candidate-1",
                attempt_id="attempt-1",
            )
            failure_b = self.add_validation(
                root,
                "validation-b",
                "csynth",
                "confirmed_failed",
                b"candidate-1",
                attempt_id="attempt-1",
            )
            root.record_event(self.event("event-b", "validation-b", "csynth"))

            before_timeline = root.timeline()
            first = adjudicate_formal_root_run(root)
            second = adjudicate_formal_root_run(root)
            self.assertEqual(first["decision_count"], 2)
            self.assertEqual(second["decision_count"], 2)
            self.assertEqual(root.timeline(), before_timeline)
            self.assertEqual(
                root.record_relations("event", "event-b")["state"], "confirmed"
            )
            self.assertEqual(
                root.record_relations("event", "event-b")["case_ids"], ["case-a"]
            )
            self.assertEqual(
                root.record_relations("attempt", "attempt-1")["state"], "confirmed"
            )
            self.assertEqual(
                [item["decision_id"] for item in root.attribution_decisions()],
                ["attribution:event-a", "attribution:event-b"],
            )

    def test_candidate_relations_are_reverse_queryable_and_generation_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = RootRunRecordStore(Path(directory), "run-1")
            root.register_case("case-a")
            root.register_case("case-b")
            root.register_case("generation-case", case_type="generation")

            self.add_validation(
                root, "validation-a", "csim", "confirmed_failed", b"candidate-0"
            )
            event_a = self.event("event-a", "validation-a", "public_evaluation")
            root.record_event(event_a)
            root.set_case_relation(
                "event", "event-a", state="candidate", case_ids=("case-a",),
                basis="formal_capture_pending_adjudication",
            )
            self.add_validation(
                root, "baseline", "csynth", "passed", b"candidate-0"
            )

            root.prepare_attempt(
                "attempt-a",
                {"attempt_id": "attempt-a", "run_id": "run-1", "pre_action_event_refs": ["event-a"]},
            )
            root.mark_attempt_dispatched("attempt-a")
            root.update_attempt(
                "attempt-a",
                {
                    "candidate_input_sha256": hashlib.sha256(b"candidate-0").hexdigest(),
                    "change": {
                        "component": "candidate",
                        "before_sha256": hashlib.sha256(b"candidate-0").hexdigest(),
                        "after_sha256": hashlib.sha256(b"candidate-1").hexdigest(),
                    },
                },
            )
            self.add_validation(
                root, "attempt-a-result", "preflight", "passed", b"candidate-1",
                attempt_id="attempt-a",
            )

            event_b_source = self.event(
                "event-b-source", "validation-b-source", "preflight"
            )
            root.record_event(event_b_source)
            root.set_case_relation(
                "event", "event-b-source", state="confirmed", case_ids=("case-b",),
                basis="test",
            )
            root.prepare_attempt(
                "attempt-b",
                {
                    "attempt_id": "attempt-b",
                    "run_id": "run-1",
                    "pre_action_event_refs": ["event-b-source"],
                },
            )
            root.mark_attempt_dispatched("attempt-b")
            root.update_attempt(
                "attempt-b",
                {
                    "candidate_input_sha256": hashlib.sha256(b"candidate-1").hexdigest(),
                    "change": {
                        "component": "candidate",
                        "before_sha256": hashlib.sha256(b"candidate-1").hexdigest(),
                        "after_sha256": hashlib.sha256(b"candidate-2").hexdigest(),
                    },
                },
            )
            self.add_validation(
                root, "attempt-b-result", "preflight", "passed", b"candidate-2",
                attempt_id="attempt-b",
            )
            self.add_validation(
                root, "validation-b", "csynth", "confirmed_failed", b"candidate-2",
                attempt_id="attempt-b",
            )
            root.record_event(self.event("event-b", "validation-b", "csynth"))

            generation_event = self.event(
                "generation-event", "generation-validation", "public_testbench_generation"
            )
            root.record_event(generation_event)
            root.set_case_relation(
                "event", "generation-event", state="confirmed",
                case_ids=("generation-case",), basis="test",
            )

            result = adjudicate_formal_root_run(root)

            self.assertEqual(result["case_ids"], ["case-a", "case-b"])
            self.assertEqual(
                root.record_relations("event", "event-b"),
                {
                    "state": "candidate",
                    "case_ids": ["case-a", "case-b"],
                    "basis": "attribution:event-b",
                },
            )
            self.assertIn("event-b", root.case_items("case-a")["events"])
            self.assertIn("event-b", root.case_items("case-b")["events"])
            self.assertNotIn("generation-case", result["case_ids"])
            self.assertEqual(root.case_items("case-a")["events"], ("event-a", "event-b"))
            self.assertEqual(root.case_items("case-a")["attempts"], ("attempt-a",))
            self.assertEqual(
                root.case_items("case-b")["events"], ("event-b-source", "event-b")
            )
            self.assertEqual(root.case_items("case-b")["attempts"], ("attempt-b",))

            captured = capture_candidate_recovery(
                root=Path(directory) / "case-projections",
                result=SimpleNamespace(
                    validation_id="run-1.validation",
                    status="unresolved",
                    accepted=False,
                    repair_result=None,
                    case_change_observations=(),
                    metadata={
                        "diagnostic_events": [
                            event_a,
                            event_b_source,
                            self.event("event-b", "validation-b", "csynth"),
                            generation_event,
                        ]
                    },
                ),
                root_record=root,
            )
            projection_store = CaseStore(Path(directory) / "case-projections")
            self.assertEqual(captured["case_count"], 2)
            self.assertIn("event-a", projection_store.read_events("case-a"))
            self.assertNotIn("event-b", projection_store.read_events("case-a"))
            self.assertIn("event-b-source", projection_store.read_events("case-b"))
            self.assertNotIn("event-b", projection_store.read_events("case-b"))


if __name__ == "__main__":
    unittest.main()
