import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from agrefactor.evidence import (
    CaseRecorder,
    CaseStatus,
    CaseStore,
    RootRunRecordStore,
    capture_candidate_recovery,
)
from agrefactor.runtime import (
    PhaseResult,
    PhaseStatus,
    RunPhase,
    RunStatus,
    UnifiedRunner,
)
from agrefactor.config import RunMode, TargetProfile, TaskSpec


class RootRunRecordTests(unittest.TestCase):
    def event(self, event_id="event-1"):
        return {
            "event_id": event_id,
            "run_id": "run-1",
            "validation_id": "run-1.validation",
            "stage": "csynth",
            "event_type": "blocking_diagnostic",
            "evidence_view": "agent_safe",
            "visibility": "public",
            "accepted": False,
        }

    def test_prepare_is_not_attempt_until_dispatch_and_resume_keeps_order(self):
        with TemporaryDirectory() as directory:
            root = RootRunRecordStore(directory, "run-1")
            root.register_case("case-a")
            root.register_case("case-b")
            root.record_event(self.event())
            attempt = {
                "attempt_id": "attempt-1",
                "run_id": "run-1",
                "action_input_sha256": "a" * 64,
                "evidence_view": "agent_safe",
            }
            root.prepare_attempt("attempt-1", attempt)
            root.set_case_relation(
                "event",
                "event-1",
                state="candidate",
                case_ids=("case-a", "case-b"),
                basis="fixture",
            )
            root.set_case_relation(
                "attempt",
                "attempt-1",
                state="candidate",
                case_ids=("case-a", "case-b"),
                basis="fixture",
            )
            self.assertEqual(root.case_items("case-a")["attempts"], ())
            self.assertEqual(root.case_items("case-b")["attempts"], ())
            self.assertEqual(
                root.timeline(),
                ({"sequence": 1, "kind": "event", "id": "event-1"},),
            )
            self.assertEqual(root.attempt_record("attempt-1")["dispatch_state"], "prepared")

            self.assertTrue(root.mark_attempt_dispatched("attempt-1"))
            self.assertFalse(root.mark_attempt_dispatched("attempt-1"))
            self.assertEqual(root.case_items("case-a")["attempts"], ("attempt-1",))
            self.assertEqual(root.case_items("case-b")["attempts"], ("attempt-1",))
            root.update_attempt("attempt-1", {"result": "provider_error"})

            resumed = RootRunRecordStore(directory, "run-1")
            self.assertEqual(
                [item["id"] for item in resumed.timeline()],
                ["event-1", "attempt-1"],
            )
            self.assertEqual(resumed.attempt_record("attempt-1")["sequence"], 2)
            self.assertEqual(
                resumed.record_relations("attempt", "attempt-1")["case_ids"],
                ["case-a", "case-b"],
            )

    def test_cancelled_pre_registration_is_not_in_attempt_timeline(self):
        with TemporaryDirectory() as directory:
            root = RootRunRecordStore(directory, "run-1")
            root.prepare_attempt(
                "attempt-cancelled",
                {"attempt_id": "attempt-cancelled", "action": "repair"},
            )
            self.assertTrue(
                root.cancel_prepared_attempt(
                    "attempt-cancelled", "provider_call_not_started"
                )
            )
            self.assertEqual(root.timeline(), ())
            self.assertEqual(
                root.attempt_record("attempt-cancelled")["dispatch_state"],
                "not_dispatched",
            )

    def test_identity_conflicts_and_relation_updates_preserve_timeline(self):
        with TemporaryDirectory() as directory:
            root = RootRunRecordStore(directory, "run-1")
            root.register_case("case-a")
            root.register_case("case-b")
            event = self.event()
            self.assertTrue(root.record_event(event))
            self.assertFalse(root.record_event(event))
            with self.assertRaisesRegex(ValueError, "conflicting root event identity"):
                root.record_event({**event, "accepted": True})

            attempt = {
                "attempt_id": "attempt-1",
                "run_id": "run-1",
                "action": "repair",
            }
            self.assertTrue(root.prepare_attempt("attempt-1", attempt))
            self.assertFalse(root.prepare_attempt("attempt-1", attempt))
            with self.assertRaisesRegex(ValueError, "conflicting root Attempt identity"):
                root.prepare_attempt(
                    "attempt-1", {**attempt, "action": "different repair"}
                )
            root.set_case_relation(
                "attempt",
                "attempt-1",
                state="candidate",
                case_ids=("case-a", "case-b"),
                basis="initial_evidence",
            )
            root.mark_attempt_dispatched("attempt-1")
            before = root.timeline()
            root.set_case_relation(
                "attempt",
                "attempt-1",
                state="confirmed",
                case_ids=("case-b",),
                basis="new_evidence",
            )
            self.assertEqual(root.timeline(), before)
            self.assertEqual(root.case_items("case-a")["attempts"], ())
            self.assertEqual(root.case_items("case-b")["attempts"], ("attempt-1",))

    def test_case_recorder_shares_root_facts_and_keeps_cancelled_attempt_out(self):
        with TemporaryDirectory() as directory:
            root = RootRunRecordStore(Path(directory) / "root", "run-1")
            recorder = CaseRecorder(
                Path(directory) / "cases",
                "run-1.validation",
                root_record=root,
            )
            self.assertTrue(recorder.start_case(self.event()))
            self.assertTrue(recorder.record_candidate_dispatch(1, "int top;"))
            prepared = next(iter(root.snapshot()["attempts"].items()))
            self.assertEqual(prepared[1]["dispatch_state"], "prepared")
            recorder.dispatch_cancelled(1)
            self.assertEqual(
                root.timeline(),
                ({"sequence": 1, "kind": "event", "id": "event-1"},),
            )
            self.assertEqual(root.case_items(recorder.active_case_id)["attempts"], ())
            self.assertEqual(
                CaseStore(Path(directory) / "cases").open_cases()[0].attempts,
                (),
            )

            self.assertTrue(recorder.record_candidate_dispatch(2, "int top;"))
            self.assertTrue(recorder.dispatch_started(2))
            self.assertFalse(recorder.dispatch_started(2))
            recorder.dispatch_cancelled(2)
            attempt_id = next(iter(root.snapshot()["attempts"]))
            self.assertEqual(
                root.attempt_record(attempt_id)["dispatch_state"],
                "dispatched",
            )
            self.assertEqual(
                root.case_items(recorder.active_case_id)["attempts"],
                (attempt_id,),
            )
            self.assertEqual(
                [item["kind"] for item in root.timeline()], ["event", "attempt"]
            )
            case = CaseStore(Path(directory) / "cases").open_cases()[0]
            self.assertEqual(case.status, CaseStatus.OPEN)

    def test_formal_recovery_capture_stays_open_without_experience(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = RootRunRecordStore(base / "root", "run-1")
            recorder = CaseRecorder(
                base / "cases", "run-1.validation", root_record=root
            )
            event = self.event()
            self.assertTrue(recorder.start_case(event))
            result = SimpleNamespace(
                validation_id="run-1.validation",
                status="accepted",
                accepted=True,
                repair_result=None,
                metadata={"diagnostic_events": [event]},
                case_change_observations=(),
            )
            captured = capture_candidate_recovery(
                root=base / "cases",
                result=result,
                experience_root=base / "experiences",
                root_record=root,
            )
            case = CaseStore(base / "cases").read_case(captured["case_ids"][0])
            self.assertEqual(case.status, CaseStatus.OPEN)
            self.assertIsNone(case.outcome)
            self.assertEqual(captured["experience_count"], 0)
            self.assertFalse((base / "experiences").exists())

    def test_testbench_attempt_result_is_recorded_as_it_completes(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = RootRunRecordStore(base / "root", "run-1")
            recorder = CaseRecorder(
                base / "cases", "run-1.validation", root_record=root
            )
            self.assertTrue(recorder.start_case(self.event()))
            self.assertTrue(
                recorder.record_testbench_dispatch(
                    attempt_key="tb-attempt-1",
                    repair_record_ref="repair-tb-1",
                )
            )
            self.assertTrue(recorder.dispatch_started("tb-attempt-1"))
            attempt = SimpleNamespace(
                index=1,
                action="repair_and_preflight",
                case_before_sha256="a" * 64,
                case_after_sha256="b" * 64,
                case_change_patch="@@ -1 +1 @@\n-old\n+new\n",
                preflight=SimpleNamespace(
                    succeeded=True,
                    stage=SimpleNamespace(value="preflight"),
                ),
            )
            recorder.record_testbench_attempt_result(
                "tb-attempt-1",
                attempt,
                validation_id="run-1.validation.testbench.1.preflight",
            )
            snapshot = root.snapshot()
            saved = next(iter(snapshot["attempts"].values()))
            self.assertEqual(saved["details"]["result"], "validated")
            self.assertTrue(saved["details"]["validation"]["passed"])
            self.assertEqual(len(snapshot["events"]), 2)

    def test_unified_runner_persists_root_lifecycle(self):
        with TemporaryDirectory() as directory:
            task = TaskSpec(
                task_id="task-1",
                kernel_path="kernel.cpp",
                kernel_name="top",
                target=TargetProfile(
                    name="target",
                    toolchain="vitis_hls",
                    toolchain_version="2023.2",
                ),
                mode=RunMode.REFACTOR,
            )
            runner = UnifiedRunner(
                {
                    RunPhase.REFACTOR: lambda context: PhaseResult(
                        phase=RunPhase.REFACTOR,
                        status=PhaseStatus.SUCCEEDED,
                    )
                }
            )
            result = runner.run(
                task,
                run_id="run-1",
                artifact_root=directory,
            )
            root_record = json.loads(
                (Path(directory) / "root_run" / "root_run_record.json").read_text()
            )
            self.assertEqual(result.status, RunStatus.SUCCEEDED)
            self.assertEqual(root_record["run_id"], "run-1")
            self.assertEqual(root_record["lifecycle"]["status"], "succeeded")
            self.assertEqual(root_record["cases"], {})


if __name__ == "__main__":
    unittest.main()
