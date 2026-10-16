from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from agrefactor.evidence import CaseRecorder, CaseStore, capture_candidate_recovery
from agrefactor.evidence.case_capture import (
    _digest,
    _patch,
    _stable_id,
    _validation_contract,
)
from agrefactor.runtime.repair_phase import _experience_root_for_artifact


class CaseCaptureTests(unittest.TestCase):
    def test_experience_store_is_scoped_to_configured_run_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "runs"
            artifact_root = run_root / "task" / "refactor"
            outside_root = root / "tests" / "refactor"

            self.assertEqual(
                _experience_root_for_artifact(artifact_root, str(run_root)),
                run_root / "diagnostic_experiences",
            )
            self.assertIsNone(
                _experience_root_for_artifact(outside_root, str(run_root))
            )
            self.assertIsNone(
                _experience_root_for_artifact(artifact_root, None)
            )

    def test_recorder_persists_safe_events_and_blocks_duplicate_dispatch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "cases"
            initial = self.event("diagnostic-initial", "validation.initial")
            recorder = CaseRecorder(root, "validation")
            self.assertTrue(recorder.start_case(initial))
            self.assertTrue(
                recorder.record_candidate_dispatch(1, "candidate-before")
            )
            safe_next = self.event(
                "diagnostic-next", "validation.candidate.1"
            )
            self.assertTrue(recorder.record_event(safe_next))
            hidden = self.event(
                "diagnostic-hidden",
                "validation.hidden",
                stage="hidden_evaluation",
            )
            self.assertFalse(recorder.record_event(hidden))
            hidden_split = self.event("diagnostic-hidden-split", "validation.hidden-split")
            hidden_split["split"] = "hidden"
            self.assertFalse(recorder.record_event(hidden_split))
            hidden_visibility = self.event(
                "diagnostic-hidden-visibility", "validation.hidden-visibility"
            )
            hidden_visibility["visibility"] = "hidden_generation"
            self.assertFalse(recorder.record_event(hidden_visibility))

            resumed = CaseRecorder(root, "validation")
            self.assertTrue(resumed.start_case(initial))
            self.assertFalse(
                resumed.record_candidate_dispatch(1, "candidate-before")
            )
            case_id = CaseStore(root).open_cases()[0].case_id
            case = CaseStore(root).read_case(case_id)
            self.assertEqual(
                [attempt.result for attempt in case.attempts],
                ["dispatch_incomplete"],
            )
            self.assertEqual(
                set(CaseStore(root).read_events(case_id)),
                {"diagnostic-initial", "diagnostic-next"},
            )
            stored_events = CaseStore(root).read_events(case_id)
            self.assertEqual(stored_events["diagnostic-initial"]["visibility"], "public")
            self.assertEqual(stored_events["diagnostic-next"]["visibility"], "public")

    def event(self, event_id, validation_id, *, owner="candidate", stage="csynth"):
        return {
            "event_id": event_id,
            "run_id": "root-run",
            "validation_id": validation_id,
            "stage": stage,
            "owner": owner,
            "route_action": "repair_candidate",
            "repair_scope": "candidate",
            "evidence_refs": [f"report:{validation_id}"],
            "evidence_view": "agent_safe",
            "accepted": False,
            "created_at": "2026-10-15T12:00:00Z",
        }

    def attempt(self, index, before, after, *, passed, validation_id, stage="csynth"):
        validation = SimpleNamespace(
            passed=passed,
            metadata={"orchestration_validation_id": validation_id},
            completed_stages=(stage,),
        )
        return SimpleNamespace(
            attempt=index,
            status="validated" if passed else "validation_failed",
            input_candidate=before,
            proposal=after,
            model_response=object(),
            model_result=None,
            validation_result=validation,
        )

    def result(
        self,
        validation_id,
        events,
        attempts,
        *,
        accepted,
        case_change_observations=(),
    ):
        return SimpleNamespace(
            validation_id=validation_id,
            status="accepted" if accepted else "validation_terminal",
            accepted=accepted,
            repair_result=SimpleNamespace(attempts=tuple(attempts)),
            metadata={"diagnostic_events": events},
            case_change_observations=tuple(case_change_observations),
        )

    def test_candidate_chain_derives_action_level_experiences(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            events = [
                {
                    **self.event("diagnostic-initial", "validation.initial"),
                    "diagnostic_terms": ["interface_contract_failure"],
                },
                {
                    **self.event("diagnostic-failure", "validation.candidate.1"),
                    "diagnostic_terms": ["interface_contract_failure"],
                    "validation_contract": _validation_contract(
                        "csynth", "candidate"
                    ),
                    "failure_attributable_to_change": True,
                    "failure_code": "interface_contract_violation",
                },
            ]
            result = self.result(
                "validation",
                events,
                [
                    self.attempt(1, "int top() { return 0; }", "int top() { return 1; }", passed=False, validation_id="validation.candidate.1"),
                    self.attempt(2, "int top() { return 1; }", "int top() { return 2; }", passed=True, validation_id="validation.candidate.2"),
                ],
                accepted=True,
            )

            captured = capture_candidate_recovery(root=root / "cases", result=result)
            case = CaseStore(root / "cases").read_case(captured["case_ids"][0])
            experiences = list((root / "diagnostic_experiences" / "records").glob("*.json"))

            self.assertEqual(captured["case_count"], 1)
            self.assertEqual(captured["experience_count"], 2)
            self.assertEqual(len(case.attempts), 2)
            self.assertEqual(len(experiences), 2)
            self.assertEqual(case.attempts[0].result, "validation_failed")
            self.assertEqual(case.attempts[1].result, "validated")
            self.assertEqual(
                case.attempts[0].pre_action_event_refs,
                ("diagnostic-initial",),
            )
            self.assertEqual(
                case.attempts[1].pre_action_event_refs,
                ("diagnostic-failure",),
            )

    def test_unknown_unresolved_case_is_saved_without_experience(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            result = self.result(
                "validation",
                [self.event("diagnostic-initial", "validation.initial", owner="unknown")],
                [],
                accepted=False,
            )
            captured = capture_candidate_recovery(root=root / "cases", result=result)
            case = CaseStore(root / "cases").read_case(captured["case_ids"][0])
            self.assertEqual(case.outcome.value, "unresolved")
            self.assertEqual(case.owner_assessments[0].owner, "unknown")
            self.assertEqual(captured["experience_count"], 0)

    def test_candidate_case_can_be_saved_without_deriving_experiences(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            result = self.result(
                "validation",
                [self.event("diagnostic-initial", "validation.initial")],
                [],
                accepted=False,
            )
            captured = capture_candidate_recovery(
                root=root / "cases",
                result=result,
                derive_experience_records=False,
            )

            self.assertEqual(captured["case_count"], 1)
            self.assertEqual(captured["experience_count"], 0)
            self.assertTrue(
                CaseStore(root / "cases").read_case(captured["case_ids"][0])
            )
            self.assertFalse((root / "diagnostic_experiences").exists())

    def test_hidden_validation_result_is_not_projected_to_case_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            result = self.result(
                "validation",
                [self.event("diagnostic-initial", "validation.initial")],
                [
                    self.attempt(
                        1,
                        "int top() { return 0; }",
                        "int top() { return 1; }",
                        passed=False,
                        validation_id="validation.hidden",
                        stage="hidden_evaluation",
                    )
                ],
                accepted=False,
            )
            captured = capture_candidate_recovery(root=root / "cases", result=result)
            case_id = captured["case_ids"][0]
            stored_events = CaseStore(root / "cases").read_events(case_id)
            self.assertEqual(set(stored_events), {"diagnostic-initial"})
            self.assertEqual(captured["experience_count"], 0)

    def test_successful_run_without_diagnostics_creates_no_case(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            result = self.result("validation", [], [], accepted=True)
            self.assertEqual(
                capture_candidate_recovery(root=root / "cases", result=result),
                {"case_count": 0, "experience_count": 0, "case_ids": []},
            )

    def test_public_testbench_attempt_uses_observed_diff_and_public_quota(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            before = "int main() { return 1; }\n"
            after = "int main() { return 0; }\n"
            observation = {
                "attempt_key": "run:suite:1:1",
                "component": "testbench",
                "visibility": "public",
                "result": "validated",
                "before_sha256": _digest(before),
                "after_sha256": _digest(after),
                "patch": _patch(before, after, name="testbench.cpp"),
                "repair_record_ref": "repair-attempt",
                "validation": {
                    "validation_id": "run.public-validation",
                    "stage": "public_evaluation",
                    "accepted": True,
                    "diagnostic_event_ref": "diagnostic-after",
                },
            }
            post_event = self.event(
                "diagnostic-after",
                "run.public-validation",
                owner="testbench",
                stage="public_evaluation",
            )
            post_event["accepted"] = True
            post_event["diagnostic_terms"] = ["public_functional_mismatch"]
            post_event["validation_contract"] = _validation_contract(
                "public_evaluation", "testbench"
            )
            initial_event = self.event(
                "diagnostic-initial",
                "validation.initial",
                owner="testbench",
            )
            initial_event["diagnostic_terms"] = ["public_functional_mismatch"]
            result = self.result(
                "validation",
                [initial_event, post_event],
                [],
                accepted=True,
                case_change_observations=(observation,),
            )

            captured = capture_candidate_recovery(root=root / "cases", result=result)
            case = CaseStore(root / "cases").read_case(captured["case_ids"][0])

            self.assertEqual(captured["experience_count"], 1)
            self.assertEqual(case.attempts[0].quota_buckets, ("testbench_public",))
            self.assertEqual(case.attempts[0].changed, True)
            self.assertEqual(
                case.attempts[0].post_validation_event_refs,
                ("diagnostic-after",),
            )

    def test_final_capture_preserves_runtime_dispatch_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "refactor"
            case_root = root / "cases"
            initial = self.event(
                "diagnostic-initial", "validation.initial", owner="testbench"
            )
            after_testbench = self.event(
                "diagnostic-after-tb",
                "validation.after-tb",
                owner="candidate",
            )
            recorder = CaseRecorder(case_root, "validation")
            recorder.start_case(initial)
            recorder.record_testbench_dispatch(
                attempt_key="run:suite:1:1",
                repair_record_ref="run:testbench:suite:1:1",
            )
            recorder.record_event(after_testbench)
            recorder.record_candidate_dispatch(1, "int top() { return 0; }")
            result = self.result(
                "validation",
                [initial, after_testbench],
                [
                    self.attempt(
                        1,
                        "int top() { return 0; }",
                        "int top() { return 1; }",
                        passed=False,
                        validation_id="validation.candidate.1",
                    )
                ],
                accepted=False,
            )

            captured = capture_candidate_recovery(
                root=case_root, result=result
            )
            case = CaseStore(case_root).read_case(captured["case_ids"][0])

            self.assertEqual(len(case.attempts), 2)
            self.assertEqual(case.attempts[0].quota_buckets, ("testbench_public",))
            self.assertEqual(
                case.attempts[0].pre_action_event_refs,
                ("diagnostic-initial",),
            )
            self.assertEqual(case.attempts[1].quota_buckets, ("candidate",))
            self.assertEqual(
                case.attempts[1].pre_action_event_refs,
                ("diagnostic-after-tb",),
            )
            candidate_eligibility = next(
                item
                for item in case.eligibility_snapshots
                if item.decision_ref
                == _stable_id(
                    "eligibility",
                    case.case_id,
                    case.attempts[1].attempt_id,
                )
            )
            self.assertEqual(
                candidate_eligibility.authorization_ref,
                None,
            )


if __name__ == "__main__":
    unittest.main()
