import unittest

from agrefactor.evidence.stage4_case_attribution import (
    _adjudicate,
    _attempt_component_change,
    _comparable,
    normalize_stage,
)


def digest(char):
    return char * 64


def contract(stage, code_hash):
    return {
        "schema_version": 1,
        "business_stage": stage,
        "validation_state": stage,
        "handler": f"handler.{stage}",
        "handler_version": 1,
        "semantics_version": 1,
        "orchestrator_version": 1,
        "public_suites": [{"suite_id": "public", "suite_version": "1", "content_sha256": digest("a")}],
        "target_profile": {"toolchain": "vitis", "version": "2023.2"},
        "source_package": {"target": "kernel.cpp", "extra_sources": []},
        "inputs": {"candidate_code": {"sha256": code_hash}, "top": "top"},
    }


def evidence(evidence_id, sequence, stage, verdict, code_hash, *, attempt_id=None):
    return {
        "evidence_id": evidence_id,
        "root_run_id": "run-1",
        "validation_id": f"validation-{evidence_id}",
        "attempt_id": attempt_id,
        "stage": stage,
        "evidence_verdict": verdict,
        "_sequence": sequence,
        "_eligible": True,
        "_stage_contract": contract(stage, code_hash),
        "_input_snapshot": {
            "input_contents": [{"role": "candidate_code", "logical_name": "candidate_code", "sha256": code_hash}],
            "source_package_files": [],
            "complete": True,
        },
        "actual_consumed_inputs": [],
        "input_snapshot_sha256": digest("f") if verdict == "passed" else digest("e"),
        "environment_observation": {"verified": True, "versions": ["2023.2"]},
    }


def event(event_id, sequence, stage, validation_id):
    return {
        "sequence": sequence,
        "payload": {
            "event_id": event_id,
            "run_id": "run-1",
            "validation_id": validation_id,
            "stage": stage,
            "evidence_complete": True,
            "physical_tool_launched": True,
            "evidence_view": "agent_safe",
            "visibility": "public",
            "split": "public",
            "hidden_content_persisted": False,
        },
        "relation": {"state": "unassigned", "case_ids": []},
    }


class CaseAttributionTests(unittest.TestCase):
    def test_stage_normalization_preserves_validation_order(self):
        self.assertEqual(normalize_stage("public_evaluation"), "csim")
        self.assertEqual(normalize_stage("CSYNTH"), "csynth")
        self.assertEqual(normalize_stage("public_rtl_cosim"), "cosim")
        self.assertIsNone(normalize_stage("hidden_evaluation"))

    def test_first_failure_starts_a_formal_case(self):
        failure = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failure["validation_id"] = "validation-a"
        snapshot = {
            "run_id": "run-1",
            "cases": {},
            "events": {"event-a": event("event-a", 3, "public_evaluation", "validation-a")},
            "attempts": {},
        }
        decisions, event_relations, _, created = _adjudicate(snapshot, [failure])
        self.assertEqual(decisions[0]["reason"], "first_eligible_formal_failure_starts_case")
        self.assertEqual(event_relations["event-a"]["state"], "confirmed")
        self.assertEqual(len(created), 1)

    def test_ineligible_failure_evidence_does_not_create_a_case(self):
        failure = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failure["validation_id"] = "validation-a"
        failure["_eligible"] = False
        snapshot = {
            "run_id": "run-1",
            "cases": {},
            "events": {"event-a": event("event-a", 3, "public_evaluation", "validation-a")},
            "attempts": {},
        }

        decisions, relations, _, created = _adjudicate(snapshot, [failure])

        self.assertEqual(decisions, [])
        self.assertEqual(relations["event-a"]["state"], "unassigned")
        self.assertEqual(created, set())

    def test_empty_root_creates_no_formal_case(self):
        decisions, relations, _, created = _adjudicate(
            {"run_id": "run-1", "cases": {}, "events": {}, "attempts": {}},
            [],
        )

        self.assertEqual(decisions, [])
        self.assertEqual(relations, {})
        self.assertEqual(created, set())

    def test_same_or_earlier_stage_joins_the_prior_case(self):
        failed_a = evidence("failure-a", 2, "csynth", "confirmed_failed", digest("a"))
        failed_a["validation_id"] = "validation-a"
        failed_b = evidence("failure-b", 5, "csim", "confirmed_failed", digest("a"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "csynth", "validation-a"),
                "event-b": event("event-b", 6, "public_evaluation", "validation-b"),
            },
            "attempts": {},
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "candidate",
            "case_ids": ["case-a"],
            "basis": "formal_capture_pending_adjudication",
        }
        decisions, relations, _, _ = _adjudicate(snapshot, [failed_a, failed_b])
        self.assertEqual(relations["event-a"]["state"], "confirmed")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])
        self.assertEqual(decisions[1]["reason"], "same_or_earlier_stage_joins_prior_chain")

    def test_later_stage_failure_uses_latest_pass_and_connected_attempt(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("b"))
        failed_a["validation_id"] = "validation-a"
        older_baseline = evidence("older-baseline", 4, "csynth", "passed", digest("b"))
        baseline = evidence("baseline", 5, "csynth", "passed", digest("b"))
        ineligible_baseline = evidence("ineligible-baseline", 6, "csynth", "passed", digest("b"))
        ineligible_baseline["_eligible"] = False
        attempt_result = evidence("attempt-result", 8, "preflight", "passed", digest("c"), attempt_id="attempt-1")
        failed_b = evidence("failure-b", 10, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 11, "csynth", "validation-b"),
            },
            "attempts": {
                "attempt-1": {
                    "attempt_id": "attempt-1",
                    "sequence": 7,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {
                        "candidate_input_sha256": digest("b"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("b"),
                            "after_sha256": digest("c"),
                        },
                    },
                }
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "candidate",
            "case_ids": ["case-a"],
            "basis": "formal_capture_pending_adjudication",
        }
        attempt_state = {**snapshot["attempts"]["attempt-1"], "attempt_id": "attempt-1"}
        self.assertEqual(
            _attempt_component_change(
                attempt_state,
                {"candidate": digest("b")},
                [failed_a, older_baseline, baseline, ineligible_baseline, attempt_result, failed_b],
                10,
            ),
            (True, True),
        )
        decisions, relations, attempt_relations, _ = _adjudicate(
            snapshot, [failed_a, older_baseline, baseline, ineligible_baseline, attempt_result, failed_b]
        )
        decision_b = decisions[1]
        self.assertEqual(decision_b["baseline_evidence_id"], "baseline")
        self.assertEqual(decision_b["interval_attempt_ids"], ["attempt-1"])
        self.assertEqual(relations["event-b"]["state"], "confirmed", decision_b)
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])
        self.assertEqual(attempt_relations["attempt-1"]["state"], "confirmed")

    def test_later_stage_pass_can_be_baseline_for_earlier_stage_failure(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failed_a["validation_id"] = "validation-a"
        later_baseline = evidence("baseline", 5, "cosim", "passed", digest("b"))
        attempt_result = evidence("attempt-result", 8, "preflight", "passed", digest("c"), attempt_id="attempt-1")
        failed_b = evidence("failure-b", 10, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 11, "csynth", "validation-b"),
            },
            "attempts": {
                "attempt-1": {
                    "attempt_id": "attempt-1",
                    "sequence": 6,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {
                        "candidate_input_sha256": digest("b"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("b"),
                            "after_sha256": digest("c"),
                        },
                    },
                }
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }

        decisions, relations, _, _ = _adjudicate(
            snapshot, [failed_a, later_baseline, attempt_result, failed_b]
        )

        self.assertEqual(decisions[-1]["baseline_evidence_id"], "baseline")
        self.assertEqual(decisions[-1]["interval_attempt_ids"], ["attempt-1"])
        self.assertEqual(relations["event-b"]["state"], "confirmed")

    def test_multiple_case_sources_remain_one_candidate_event(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("b"))
        failed_a["validation_id"] = "validation-a"
        baseline = evidence("baseline", 5, "csynth", "passed", digest("b"))
        result_1 = evidence("result-1", 8, "preflight", "passed", digest("c"), attempt_id="attempt-1")
        result_2 = evidence("result-2", 11, "preflight", "passed", digest("d"), attempt_id="attempt-2")
        failed_b = evidence("failure-b", 13, "csynth", "confirmed_failed", digest("d"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}, "case-c": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 4, "csim", "validation-a"),
                "event-c": event("event-c", 1, "public_testbench_generation", "ignored"),
                "event-b": event("event-b", 14, "csynth", "validation-b"),
            },
            "attempts": {
                "attempt-1": {
                    "attempt_id": "attempt-1", "sequence": 6, "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {"candidate_input_sha256": digest("b"), "change": {"component": "candidate", "before_sha256": digest("b"), "after_sha256": digest("c")}},
                },
                "attempt-2": {
                    "attempt_id": "attempt-2", "sequence": 9, "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-c"]},
                    "details": {"candidate_input_sha256": digest("c"), "change": {"component": "candidate", "before_sha256": digest("c"), "after_sha256": digest("d")}},
                },
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "candidate",
            "case_ids": ["case-a"],
            "basis": "formal_capture_pending_adjudication",
        }
        snapshot["events"]["event-c"]["relation"] = {"state": "confirmed", "case_ids": ["case-c"]}
        decisions, relations, _, _ = _adjudicate(
            snapshot, [failed_a, baseline, result_1, result_2, failed_b]
        )
        self.assertEqual(relations["event-b"]["state"], "candidate")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a", "case-c"])
        self.assertEqual(decisions[-1]["interval_attempt_ids"], ["attempt-1", "attempt-2"])

    def test_no_baseline_retains_existing_cases_and_possible_new_root(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("b"))
        failed_a["validation_id"] = "validation-a"
        failed_b = evidence("failure-b", 5, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        failed_c = evidence("failure-c", 7, "csim", "confirmed_failed", digest("d"))
        failed_c["validation_id"] = "validation-c"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "csim", "validation-a"),
                "event-b": event("event-b", 6, "csynth", "validation-b"),
                "event-c": event("event-c", 8, "csim", "validation-c"),
            },
            "attempts": {},
        }
        snapshot["events"]["event-a"]["relation"] = {"state": "candidate", "case_ids": ["case-a"]}
        decisions, relations, _, _ = _adjudicate(snapshot, [failed_a, failed_b, failed_c])
        self.assertEqual(relations["event-b"]["state"], "candidate")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])
        self.assertEqual(decisions[1]["interval_attempt_ids"], [])
        self.assertTrue(decisions[1]["possible_new_case_root"])
        self.assertEqual(relations["event-c"]["case_ids"], ["case-a"])
        self.assertTrue(decisions[-1]["possible_new_case_root"])

    def test_earlier_stage_pass_cannot_be_baseline_for_later_failure(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failed_a["validation_id"] = "validation-a"
        earlier_pass = evidence("earlier-pass", 4, "csim", "passed", digest("a"))
        failed_b = evidence("failure-b", 6, "csynth", "confirmed_failed", digest("a"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 7, "csynth", "validation-b"),
            },
            "attempts": {},
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }

        decisions, relations, _, _ = _adjudicate(
            snapshot, [failed_a, earlier_pass, failed_b]
        )

        self.assertIsNone(decisions[-1]["baseline_evidence_id"])
        self.assertTrue(decisions[-1]["possible_new_case_root"])
        self.assertEqual(relations["event-b"]["state"], "candidate")

    def test_baseline_to_failure_without_dispatched_attempt_is_incomplete(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failed_a["validation_id"] = "validation-a"
        baseline = evidence("baseline", 5, "csynth", "passed", digest("b"))
        failed_b = evidence("failure-b", 8, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 9, "csynth", "validation-b"),
            },
            "attempts": {},
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }

        decisions, relations, _, _ = _adjudicate(snapshot, [failed_a, baseline, failed_b])

        self.assertEqual(decisions[-1]["baseline_evidence_id"], "baseline")
        self.assertEqual(decisions[-1]["reason"], "baseline_to_failure_missing_attempt_or_unrecorded_change")
        self.assertFalse(decisions[-1]["possible_new_case_root"])
        self.assertEqual(relations["event-b"]["state"], "candidate")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])

    def test_new_pass_to_failure_interval_is_adjudicated_separately(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failed_a["validation_id"] = "validation-a"
        baseline_b = evidence("baseline-b", 5, "csynth", "passed", digest("b"))
        result_b = evidence("attempt-b-result", 8, "preflight", "passed", digest("c"), attempt_id="attempt-b")
        failed_b = evidence("failure-b", 10, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        baseline_c = evidence("baseline-c", 12, "cosim", "passed", digest("c"))
        result_c = evidence("attempt-c-result", 15, "preflight", "passed", digest("d"), attempt_id="attempt-c")
        failed_c = evidence("failure-c", 17, "cosim", "confirmed_failed", digest("d"))
        failed_c["validation_id"] = "validation-c"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 11, "csynth", "validation-b"),
                "event-c": event("event-c", 18, "cosim", "validation-c"),
            },
            "attempts": {
                "attempt-b": {
                    "attempt_id": "attempt-b",
                    "sequence": 6,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {
                        "candidate_input_sha256": digest("b"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("b"),
                            "after_sha256": digest("c"),
                        },
                    },
                },
                "attempt-c": {
                    "attempt_id": "attempt-c",
                    "sequence": 13,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-b"]},
                    "details": {
                        "candidate_input_sha256": digest("c"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("c"),
                            "after_sha256": digest("d"),
                        },
                    },
                },
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }
        all_evidence = [
            failed_a,
            baseline_b,
            result_b,
            failed_b,
            baseline_c,
            result_c,
            failed_c,
        ]

        decisions, relations, _, _ = _adjudicate(snapshot, all_evidence)

        self.assertEqual(decisions[1]["baseline_evidence_id"], "baseline-b")
        self.assertEqual(decisions[1]["interval_attempt_ids"], ["attempt-b"])
        self.assertEqual(decisions[2]["baseline_evidence_id"], "baseline-c")
        self.assertEqual(decisions[2]["interval_attempt_ids"], ["attempt-c"])
        self.assertEqual(relations["event-c"]["state"], "confirmed")
        self.assertEqual(relations["event-c"]["case_ids"], ["case-a"])

    def test_check_contract_mismatch_is_not_comparable(self):
        passed = evidence("passed", 1, "csynth", "passed", digest("a"))
        failed = evidence("failed", 2, "csynth", "confirmed_failed", digest("a"))
        self.assertTrue(_comparable(passed, failed))

        failed["_stage_contract"]["public_suites"][0]["suite_id"] = "other-suite"

        self.assertFalse(_comparable(passed, failed))

        failed = evidence("failed-flags", 2, "csynth", "confirmed_failed", digest("a"))
        passed["_stage_contract"]["inputs"]["compiler_flags"] = ["-O2"]
        failed["_stage_contract"]["inputs"]["compiler_flags"] = ["-O3"]
        self.assertFalse(_comparable(passed, failed))

    def test_single_candidate_relation_is_not_promoted_without_capture_basis(self):
        failed = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failed["validation_id"] = "validation-a"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
            },
            "attempts": {},
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "candidate",
            "case_ids": ["case-a"],
            "basis": "earlier_uncertain_attribution",
        }

        _, relations, _, _ = _adjudicate(snapshot, [failed])

        self.assertEqual(relations["event-a"]["state"], "candidate")
        self.assertEqual(relations["event-a"]["case_ids"], ["case-a"])

    def test_candidate_attempt_source_does_not_confirm_later_failure(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("b"))
        failed_a["validation_id"] = "validation-a"
        baseline = evidence("baseline", 5, "csynth", "passed", digest("b"))
        result = evidence("attempt-result", 8, "preflight", "passed", digest("c"), attempt_id="attempt-1")
        failed_b = evidence("failure-b", 10, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 11, "csynth", "validation-b"),
            },
            "attempts": {
                "attempt-1": {
                    "attempt_id": "attempt-1",
                    "sequence": 6,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {
                        "candidate_input_sha256": digest("b"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("b"),
                            "after_sha256": digest("c"),
                        },
                    },
                }
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "candidate",
            "case_ids": ["case-a"],
            "basis": "earlier_uncertain_attribution",
        }

        decisions, relations, attempt_relations, _ = _adjudicate(
            snapshot, [failed_a, baseline, result, failed_b]
        )

        self.assertEqual(relations["event-b"]["state"], "candidate")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])
        self.assertEqual(attempt_relations["attempt-1"]["state"], "candidate")

    def test_unverified_dispatched_attempt_prevents_single_case_confirmation(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("b"))
        failed_a["validation_id"] = "validation-a"
        baseline = evidence("baseline", 5, "csynth", "passed", digest("b"))
        result = evidence("attempt-result", 8, "preflight", "passed", digest("c"), attempt_id="attempt-1")
        failed_b = evidence("failure-b", 13, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {
                "case-a": {"case_type": "formal"},
                "case-b": {"case_type": "formal"},
            },
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b-source": event("event-b-source", 4, "preflight", "other-validation"),
                "event-b": event("event-b", 14, "csynth", "validation-b"),
            },
            "attempts": {
                "attempt-1": {
                    "attempt_id": "attempt-1",
                    "sequence": 6,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {
                        "candidate_input_sha256": digest("b"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("b"),
                            "after_sha256": digest("c"),
                        },
                    },
                },
                "attempt-2": {
                    "attempt_id": "attempt-2",
                    "sequence": 9,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-b-source"]},
                    "details": {"result": "validated"},
                },
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }
        snapshot["events"]["event-b-source"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-b"],
            "basis": "test",
        }

        decisions, relations, _, _ = _adjudicate(
            snapshot, [failed_a, baseline, result, failed_b]
        )

        self.assertEqual(relations["event-b"]["state"], "candidate")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a", "case-b"])
        self.assertEqual(decisions[-1]["interval_attempt_ids"], ["attempt-1", "attempt-2"])

    def test_explicit_no_change_attempt_does_not_pollute_interval_sources(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("b"))
        failed_a["validation_id"] = "validation-a"
        baseline = evidence("baseline", 5, "csynth", "passed", digest("b"))
        result = evidence("attempt-result", 8, "preflight", "passed", digest("c"), attempt_id="attempt-1")
        failed_b = evidence("failure-b", 13, "csynth", "confirmed_failed", digest("c"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {
                "case-a": {"case_type": "formal"},
                "case-b": {"case_type": "formal"},
            },
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b-source": event("event-b-source", 4, "preflight", "other-validation"),
                "event-b": event("event-b", 14, "csynth", "validation-b"),
            },
            "attempts": {
                "attempt-1": {
                    "attempt_id": "attempt-1",
                    "sequence": 6,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-a"]},
                    "details": {
                        "candidate_input_sha256": digest("b"),
                        "change": {
                            "component": "candidate",
                            "before_sha256": digest("b"),
                            "after_sha256": digest("c"),
                        },
                    },
                },
                "attempt-2": {
                    "attempt_id": "attempt-2",
                    "sequence": 9,
                    "dispatch_state": "dispatched",
                    "payload": {"pre_action_event_refs": ["event-b-source"]},
                    "details": {"result": "response_rejected", "change": None},
                },
            },
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }
        snapshot["events"]["event-b-source"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-b"],
            "basis": "test",
        }

        decisions, relations, _, _ = _adjudicate(
            snapshot, [failed_a, baseline, result, failed_b]
        )

        self.assertEqual(relations["event-b"]["state"], "confirmed")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])
        self.assertEqual(decisions[-1]["interval_attempt_ids"], ["attempt-1"])

    def test_same_contract_same_input_pass_failure_conflict_stays_unassigned(self):
        failed_a = evidence("failure-a", 2, "csim", "confirmed_failed", digest("a"))
        failed_a["validation_id"] = "validation-a"
        baseline = evidence("baseline", 5, "csynth", "passed", digest("a"))
        failed_b = evidence("failure-b", 8, "csynth", "confirmed_failed", digest("a"))
        failed_b["validation_id"] = "validation-b"
        snapshot = {
            "run_id": "run-1",
            "cases": {"case-a": {"case_type": "formal"}},
            "events": {
                "event-a": event("event-a", 3, "public_evaluation", "validation-a"),
                "event-b": event("event-b", 9, "csynth", "validation-b"),
            },
            "attempts": {},
        }
        snapshot["events"]["event-a"]["relation"] = {
            "state": "confirmed",
            "case_ids": ["case-a"],
            "basis": "test",
        }

        decisions, relations, _, _ = _adjudicate(
            snapshot, [failed_a, baseline, failed_b]
        )

        self.assertEqual(relations["event-b"]["state"], "candidate")
        self.assertEqual(relations["event-b"]["case_ids"], ["case-a"])
        self.assertEqual(
            decisions[-1]["reason"],
            "conflicting_pass_and_failure_for_same_effective_inputs",
        )


if __name__ == "__main__":
    unittest.main()
