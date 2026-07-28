from dataclasses import replace
from hashlib import sha256
import json
import unittest

from agrefactor.optimization import (
    CandidateRecord,
    CandidateStatus,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
    OptimizerState,
    OptimizerTerminalStatus,
    candidate_index_from_dict,
    candidate_index_to_dict,
)


SOURCE = b"void top() {}\n"
SOURCE_SHA = sha256(SOURCE).hexdigest()
PROMPT_SHA = sha256(b"prompt").hexdigest()
CREATED = "2026-07-28T00:00:00Z"


def make_hypothesis(level="structural", evidence=()):
    return HypothesisRecord(
        hypothesis_id="hyp-0001",
        level=level,
        parent_candidate_id="baseline",
        claim="Reorder the loop nest to reduce latency.",
        supporting_evidence_ids=tuple(evidence),
        expected_benefit={
            "metric": "latency_cycles",
            "direction": "decrease",
        },
        risk="low",
        modification_scope=("top.loop_i",),
        verification_plan=("preflight", "public", "csynth", "hidden"),
        model_identity={"logical_model": "fake-model"},
        prompt_identity_sha256=PROMPT_SHA,
    )


def make_baseline(status=CandidateStatus.GENERATED):
    return CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=SOURCE_SHA,
        source_artifact="candidates/baseline/source.cpp",
        status=status,
        created_at_utc=CREATED,
    )


def accepted_baseline():
    return make_baseline().transition_to(
        CandidateStatus.VALIDATING
    ).transition_to(
        CandidateStatus.ACCEPTED,
        correctness={"qualified": True},
        synthesis={"qualified": True},
        decision={"decision": "initialize_best_correct"},
    )


def make_candidate(sequence=1, status=CandidateStatus.GENERATED):
    candidate_id = f"cand-{sequence:04d}"
    return CandidateRecord(
        candidate_id=candidate_id,
        sequence=sequence,
        parent_candidate_id="baseline",
        hypothesis_id=f"hyp-{sequence:04d}",
        level=OptimizationLevel.STRUCTURAL,
        source_sha256=SOURCE_SHA,
        source_artifact=f"candidates/{candidate_id}/source.cpp",
        status=status,
        budget_before={"tool_calls": 0},
        created_at_utc=CREATED,
    )


class HypothesisRecordTests(unittest.TestCase):
    def test_structural_round_trip(self):
        original = make_hypothesis()
        self.assertEqual(
            HypothesisRecord.from_dict(original.to_dict()),
            original,
        )

    def test_bottleneck_requires_evidence(self):
        with self.assertRaises(ValueError):
            make_hypothesis(level="bottleneck")
        record = make_hypothesis(
            level="bottleneck",
            evidence=("evidence-csynth-1",),
        )
        self.assertEqual(record.level, OptimizationLevel.BOTTLENECK)

    def test_pragma_is_valid(self):
        self.assertEqual(
            make_hypothesis(level="pragma").level,
            OptimizationLevel.PRAGMA,
        )

    def test_parent_is_required(self):
        with self.assertRaises(ValueError):
            replace(make_hypothesis(), parent_candidate_id="")

    def test_scope_is_required(self):
        with self.assertRaises(ValueError):
            replace(make_hypothesis(), modification_scope=())

    def test_verification_plan_is_required_and_ordered(self):
        with self.assertRaises(ValueError):
            replace(make_hypothesis(), verification_plan=())
        with self.assertRaises(ValueError):
            replace(
                make_hypothesis(),
                verification_plan=("csynth", "public"),
            )

    def test_invalid_level_and_risk_are_rejected(self):
        with self.assertRaises(ValueError):
            replace(make_hypothesis(), level="unknown")
        with self.assertRaises(ValueError):
            replace(make_hypothesis(), risk="extreme")

    def test_schema_and_unknown_fields_are_rejected(self):
        payload = make_hypothesis().to_dict()
        payload["schema_version"] = 2
        with self.assertRaises(ValueError):
            HypothesisRecord.from_dict(payload)
        payload = make_hypothesis().to_dict()
        payload["unexpected"] = True
        with self.assertRaises(ValueError):
            HypothesisRecord.from_dict(payload)

    def test_json_serialization_is_deterministic(self):
        record = make_hypothesis()
        self.assertEqual(record.to_json(), record.to_json())
        self.assertEqual(
            HypothesisRecord.from_json(record.to_json()),
            record,
        )
        self.assertTrue(record.to_json().endswith("\n"))

    def test_hidden_or_operator_full_material_is_rejected(self):
        with self.assertRaises(ValueError):
            replace(
                make_hypothesis(),
                claim="Use HIDDEN_PLAINTEXT_SECRET from the private suite.",
            )
        with self.assertRaises(ValueError):
            replace(
                make_hypothesis(),
                model_identity={"evidence_view": "operator_full"},
            )

    def test_model_identity_rejects_secrets(self):
        with self.assertRaises(ValueError):
            replace(
                make_hypothesis(),
                model_identity={"api_key": "not-allowed"},
            )


class CandidateRecordTests(unittest.TestCase):
    def test_baseline_record_round_trip(self):
        record = make_baseline()
        self.assertTrue(record.is_baseline)
        self.assertEqual(CandidateRecord.from_json(record.to_json()), record)

    def test_generated_candidate_round_trip_and_lineage(self):
        record = make_candidate()
        restored = CandidateRecord.from_dict(record.to_dict())
        self.assertEqual(restored, record)
        self.assertEqual(restored.parent_candidate_id, "baseline")
        self.assertEqual(restored.hypothesis_id, "hyp-0001")

    def test_source_sha_and_artifact_path_are_validated(self):
        with self.assertRaises(ValueError):
            replace(make_candidate(), source_sha256="bad")
        with self.assertRaises(ValueError):
            replace(
                make_candidate(),
                source_artifact="../outside/source.cpp",
            )
        with self.assertRaises(ValueError):
            replace(
                make_candidate(),
                source_artifact="candidates/cand-9999/source.cpp",
            )

    def test_baseline_and_generated_semantics_are_distinct(self):
        with self.assertRaises(ValueError):
            replace(make_baseline(), sequence=1)
        with self.assertRaises(TypeError):
            replace(make_candidate(), parent_candidate_id=None)
        with self.assertRaises(ValueError):
            replace(make_candidate(), level=None)

    def test_sequence_must_match_candidate_id(self):
        with self.assertRaises(ValueError):
            replace(make_candidate(), sequence=2)

    def test_generated_to_validating_to_accepted(self):
        validating = make_candidate().transition_to("validating")
        accepted = validating.transition_to(
            "accepted",
            correctness={"passed": True},
            synthesis={"passed": True},
            decision={"decision": "keep_best"},
        )
        self.assertEqual(accepted.status, CandidateStatus.ACCEPTED)

    def test_generated_to_validating_to_rejected(self):
        rejected = make_candidate().transition_to(
            "validating"
        ).transition_to(
            "rejected",
            correctness={"passed": False},
            decision={"decision": "reject"},
        )
        self.assertEqual(rejected.status, CandidateStatus.REJECTED)

    def test_rejected_cannot_return_to_accepted(self):
        rejected = make_candidate().transition_to(
            "rejected",
            decision={"decision": "reject"},
        )
        with self.assertRaises(ValueError):
            rejected.transition_to(
                "accepted",
                correctness={"passed": True},
                synthesis={"passed": True},
                decision={"decision": "update_best"},
            )

    def test_terminal_failure_requires_decision(self):
        with self.assertRaises(ValueError):
            replace(make_candidate(), status="rejected")

    def test_accepted_generated_candidate_requires_evidence(self):
        with self.assertRaises(ValueError):
            replace(make_candidate(), status="accepted")

    def test_created_at_must_be_utc(self):
        with self.assertRaises(ValueError):
            replace(make_candidate(), created_at_utc="2026-07-28T08:00:00+08:00")

    def test_candidate_json_is_deterministic(self):
        record = make_candidate()
        self.assertEqual(record.to_json(), record.to_json())
        self.assertEqual(json.loads(record.to_json()), record.to_dict())


class OptimizerStateTests(unittest.TestCase):
    def test_initial_state_has_no_best_before_qualification(self):
        state = OptimizerState.initial(run_id="run-1")
        baseline = make_baseline()
        state.validate_against_candidates({"baseline": baseline})
        self.assertIsNone(state.best_correct_candidate_id)
        self.assertIsNone(state.best_ppa_candidate_id)

    def test_qualified_baseline_becomes_initial_best_correct(self):
        baseline = accepted_baseline()
        state = OptimizerState.initial(run_id="run-1").with_qualified_baseline(
            baseline
        )
        state.validate_against_candidates({"baseline": baseline})
        self.assertEqual(state.best_correct_candidate_id, "baseline")
        self.assertIsNone(state.best_ppa_candidate_id)

    def test_accepted_baseline_without_best_correct_is_invalid(self):
        with self.assertRaises(ValueError):
            OptimizerState.initial(run_id="run-1").validate_against_candidates(
                {"baseline": accepted_baseline()}
            )

    def test_candidate_pointers_must_exist(self):
        baseline = accepted_baseline()
        state = OptimizerState.initial(run_id="run-1").with_qualified_baseline(
            baseline
        )
        with self.assertRaises(ValueError):
            replace(
                state,
                current_candidate_id="cand-0001",
            ).validate_against_candidates({"baseline": baseline})

    def test_executed_count_and_sequences_must_match_index(self):
        baseline = accepted_baseline()
        state = OptimizerState.initial(run_id="run-1").with_qualified_baseline(
            baseline
        )
        with self.assertRaises(ValueError):
            state.validate_against_candidates(
                {"baseline": baseline, "cand-0001": make_candidate()}
            )
        with self.assertRaises(ValueError):
            replace(
                state,
                executed_candidate_count=1,
            ).validate_against_candidates(
                {"baseline": baseline, "cand-0002": make_candidate(2)}
            )

    def test_parent_must_precede_child(self):
        baseline = accepted_baseline()
        parent = replace(
            make_candidate(1),
            parent_candidate_id="cand-0002",
        )
        child = make_candidate(2)
        state = replace(
            OptimizerState.initial(run_id="run-1").with_qualified_baseline(
                baseline
            ),
            executed_candidate_count=2,
        )
        with self.assertRaises(ValueError):
            state.validate_against_candidates(
                {
                    "baseline": baseline,
                    "cand-0001": parent,
                    "cand-0002": child,
                }
            )

    def test_checkpoint_sequence_advances_exactly_one(self):
        state = OptimizerState.initial(run_id="run-1")
        self.assertEqual(state.with_checkpoint_sequence(1).checkpoint_sequence, 1)
        with self.assertRaises(ValueError):
            state.with_checkpoint_sequence(2)

    def test_state_round_trip(self):
        state = OptimizerState.initial(run_id="run-1")
        self.assertEqual(OptimizerState.from_json(state.to_json()), state)

    def test_invalid_level_round_objective_and_schema_are_rejected(self):
        with self.assertRaises(ValueError):
            OptimizerState(run_id="run", current_level="unknown")
        with self.assertRaises(ValueError):
            OptimizerState(run_id="run", current_round=0)
        with self.assertRaises(ValueError):
            OptimizerState(run_id="run", objective="throughput")
        payload = OptimizerState.initial(run_id="run").to_dict()
        payload["schema_version"] = 2
        with self.assertRaises(ValueError):
            OptimizerState.from_dict(payload)

    def test_terminal_status_requiring_best_correct_is_enforced(self):
        with self.assertRaises(ValueError):
            OptimizerState(
                run_id="run",
                terminal_status=(
                    OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT
                ),
            )


    def test_duplicate_candidate_ids_are_rejected_on_load(self):
        baseline = make_baseline()
        payload = {
            "schema_version": 1,
            "candidates": [baseline.to_dict(), baseline.to_dict()],
        }
        with self.assertRaises(ValueError):
            candidate_index_from_dict(payload)

    def test_candidate_index_serialization_is_deterministic(self):
        baseline = make_baseline()
        candidate = make_candidate()
        payload = candidate_index_to_dict(
            {"cand-0001": candidate, "baseline": baseline}
        )
        restored = candidate_index_from_dict(payload)
        self.assertEqual(list(restored), ["baseline", "cand-0001"])
        self.assertEqual(restored["baseline"], baseline)


if __name__ == "__main__":
    unittest.main()
