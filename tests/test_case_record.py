import unittest

from agrefactor.evidence import (
    CaseAttempt,
    CaseOutcome,
    CaseStatus,
    DiagnosticCase,
    EvidenceCheckResult,
    EvidenceCheckState,
    ExperiencePolarity,
    ExperienceRecord,
    OwnerAssessment,
    RepairEligibilitySnapshot,
)


class CaseRecordTests(unittest.TestCase):
    def make_attempt(self, attempt_id, sequence, before, after, result):
        return CaseAttempt(
            attempt_id=attempt_id,
            sequence_index=sequence,
            result=result,
            pre_action_event_refs=(before,),
            post_validation_event_refs=(after,),
            authorization_ref=f"policy-{sequence}",
            quota_buckets=("candidate",),
            changed=True,
            ledger_event_ref=f"ledger-{sequence}",
            repair_record_ref=f"repair-{sequence}",
        )

    def test_case_round_trip_preserves_unknown_and_event_timeline(self):
        case = DiagnosticCase(
            case_id="case-1",
            created_at="2026-10-12T12:00:00Z",
            status=CaseStatus.OPEN,
            diagnostic_event_refs=("event-before", "event-after"),
            attempts=(
                self.make_attempt(
                    "attempt-1", 0, "event-before", "event-after", "validation_failed"
                ),
            ),
            owner_assessments=(
                OwnerAssessment(
                    diagnostic_event_ref="event-before",
                    owner="unknown",
                    candidate_clues=("candidate",),
                    evidence_refs=("owner-evidence-1",),
                ),
            ),
            eligibility_snapshots=(
                RepairEligibilitySnapshot(
                    decision_ref="decision-1",
                    eligible=False,
                    authorization_ref=None,
                    evidence_refs=("eligibility-evidence-1",),
                ),
            ),
            evidence_checks=(
                EvidenceCheckResult(
                    check_id="check-1",
                    result=EvidenceCheckState.MISSING,
                    evidence_refs=("collector-result-1",),
                    summary="type contract was unavailable",
                ),
            ),
        )

        restored = DiagnosticCase.from_dict(case.to_dict())

        self.assertEqual(restored, case)
        self.assertEqual(restored.owner_assessments[0].owner, "unknown")
        self.assertEqual(restored.owner_assessments[0].candidate_clues, ("candidate",))
        self.assertIsNone(restored.outcome)

    def test_attempt_failure_does_not_set_case_outcome(self):
        attempt = self.make_attempt(
            "attempt-1", 0, "event-before", "event-after", "validation_failed"
        )
        case = DiagnosticCase(
            case_id="case-2",
            created_at="2026-10-12T12:00:00+08:00",
            status="open",
            diagnostic_event_refs=("event-before", "event-after"),
            attempts=(attempt,),
        )

        self.assertEqual(case.attempts[0].result, "validation_failed")
        self.assertEqual(case.status, CaseStatus.OPEN)
        self.assertIsNone(case.outcome)

    def test_dispatched_attempt_without_file_difference_is_recordable(self):
        attempt = CaseAttempt(
            attempt_id="attempt-no-diff",
            sequence_index=0,
            result="validation_failed",
            pre_action_event_refs=("event-before",),
            post_validation_event_refs=("event-after",),
            authorization_ref="policy-0",
            quota_buckets=("candidate",),
            changed=False,
            ledger_event_ref="ledger-0",
            repair_record_ref="repair-0",
        )
        case = DiagnosticCase(
            case_id="case-no-diff",
            created_at="2026-10-12T12:00:00Z",
            status="open",
            diagnostic_event_refs=("event-before", "event-after"),
            attempts=(attempt,),
        )

        restored = DiagnosticCase.from_dict(case.to_dict())

        self.assertFalse(restored.attempts[0].changed)
        self.assertEqual(restored.attempts[0].repair_record_ref, "repair-0")

    def test_experience_must_link_to_a_resolved_case_attempt(self):
        failed = self.make_attempt(
            "attempt-failed", 0, "event-0", "event-1", "validation_failed"
        )
        resolved_case = DiagnosticCase(
            case_id="case-resolved",
            created_at="2026-10-12T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="validated",
            diagnostic_event_refs=("event-0", "event-1", "event-2"),
            attempts=(
                failed,
                self.make_attempt(
                    "attempt-success", 1, "event-1", "event-2", "validated"
                ),
            ),
        )
        common = {
            "experience_id": "experience-linked",
            "source_attempt_ids": ("attempt-failed",),
            "applicability_conditions": ("stage.csynth",),
            "action_summary": "declaration-only edit failed validation",
            "validation_scope": "public_csynth",
            "evidence_refs": ("evidence-1",),
            "source_version_hash": "a" * 64,
        }

        with self.assertRaisesRegex(ValueError, "link to source attempts"):
            ExperienceRecord.from_case(
                resolved_case,
                polarity="negative",
                validation_refs=("event-2",),
                change_fingerprint="b" * 64,
                **common,
            )

        with self.assertRaisesRegex(ValueError, "must belong to the source case"):
            selected = {
                key: value
                for key, value in common.items()
                if key != "source_attempt_ids"
            }
            ExperienceRecord.from_case(
                resolved_case,
                source_attempt_ids=("attempt-missing",),
                polarity="negative",
                validation_refs=("event-1",),
                change_fingerprint="b" * 64,
                **selected,
            )

    def test_negative_experience_requires_explicit_failed_changed_attempt(self):
        unchanged = CaseAttempt(
            attempt_id="attempt-no-diff",
            sequence_index=0,
            result="validation_failed",
            pre_action_event_refs=("event-0",),
            post_validation_event_refs=("event-1",),
            authorization_ref="policy-0",
            quota_buckets=("candidate",),
            changed=False,
        )
        resolved_case = DiagnosticCase(
            case_id="case-resolved-no-diff",
            created_at="2026-10-12T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="later_attempt_validated",
            diagnostic_event_refs=("event-0", "event-1", "event-2"),
            attempts=(
                unchanged,
                self.make_attempt(
                    "attempt-success", 1, "event-1", "event-2", "validated"
                ),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "changed attempts with explicit validation failure",
        ):
            ExperienceRecord.from_case(
                resolved_case,
                experience_id="experience-no-diff",
                source_attempt_ids=("attempt-no-diff",),
                polarity="negative",
                applicability_conditions=("stage.csynth",),
                action_summary="no source change was produced",
                validation_scope="public_csynth",
                validation_refs=("event-1",),
                evidence_refs=("evidence-1",),
                source_version_hash="a" * 64,
                change_fingerprint="b" * 64,
            )

    def test_positive_experience_requires_validated_change(self):
        failed = self.make_attempt(
            "attempt-failed", 0, "event-0", "event-1", "validation_failed"
        )
        resolved_case = DiagnosticCase(
            case_id="case-positive-needs-success",
            created_at="2026-10-12T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="later_manual_fix",
            diagnostic_event_refs=("event-0", "event-1"),
            attempts=(failed,),
        )

        with self.assertRaisesRegex(
            ValueError,
            "positive experiences require a validated change",
        ):
            ExperienceRecord.from_case(
                resolved_case,
                experience_id="experience-not-positive",
                source_attempt_ids=("attempt-failed",),
                polarity="positive",
                applicability_conditions=("stage.csynth",),
                action_summary="failed edit",
                validation_scope="public_csynth",
                validation_refs=("event-1",),
                evidence_refs=("evidence-1",),
                source_version_hash="a" * 64,
            )

    def test_negative_experience_requires_change_fingerprint(self):
        failed = self.make_attempt(
            "attempt-failed", 0, "event-0", "event-1", "validation_failed"
        )
        resolved_case = DiagnosticCase(
            case_id="case-negative-fingerprint",
            created_at="2026-10-12T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="later_attempt_validated",
            diagnostic_event_refs=("event-0", "event-1"),
            attempts=(failed,),
        )

        with self.assertRaisesRegex(ValueError, "change fingerprint"):
            ExperienceRecord.from_case(
                resolved_case,
                experience_id="experience-no-fingerprint",
                source_attempt_ids=("attempt-failed",),
                polarity="negative",
                applicability_conditions=("stage.csynth",),
                action_summary="declaration-only edit failed validation",
                validation_scope="public_csynth",
                validation_refs=("event-1",),
                evidence_refs=("evidence-1",),
                source_version_hash="a" * 64,
            )

    def test_resolved_case_can_contain_failed_attempt_and_unknown_owner(self):
        case = DiagnosticCase(
            case_id="case-3",
            created_at="2026-10-12T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="validated",
            diagnostic_event_refs=("event-0", "event-1", "event-2"),
            attempts=(
                self.make_attempt(
                    "attempt-1", 0, "event-0", "event-1", "validation_failed"
                ),
                self.make_attempt(
                    "attempt-2", 1, "event-1", "event-2", "validated"
                ),
            ),
            owner_assessments=(
                OwnerAssessment(
                    diagnostic_event_ref="event-1",
                    owner="unknown",
                    candidate_clues=("candidate",),
                ),
            ),
        )

        self.assertEqual(case.outcome, CaseOutcome.RESOLVED)
        self.assertEqual(case.attempts[0].result, "validation_failed")
        self.assertEqual(case.owner_assessments[0].owner, "unknown")

    def test_evidence_check_accepts_only_three_states(self):
        for value in ("satisfied", "missing", "contradicted"):
            with self.subTest(value=value):
                result = EvidenceCheckResult(
                    check_id="check-1",
                    result=value,
                    evidence_refs=("evidence-1",),
                    summary="check completed",
                )
                self.assertEqual(result.result.value, value)
        with self.assertRaises(ValueError):
            EvidenceCheckResult(
                check_id="check-2",
                result="unknown",
                evidence_refs=("evidence-2",),
                summary="check completed",
            )

    def test_schema_versions_must_be_integer_one(self):
        for version in (True, 1.0, "1", 2):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    DiagnosticCase(
                        case_id="case-schema",
                        created_at="2026-10-12T12:00:00Z",
                        status="open",
                        diagnostic_event_refs=("event-1",),
                        schema_version=version,
                    )

    def test_duplicate_attempt_ids_and_unlinked_events_are_rejected(self):
        first = self.make_attempt(
            "attempt-1", 0, "event-before", "event-after", "validation_failed"
        )
        duplicate = self.make_attempt(
            "attempt-1", 1, "event-after", "event-final", "validated"
        )
        with self.assertRaises(ValueError):
            DiagnosticCase(
                case_id="case-4",
                created_at="2026-10-12T12:00:00Z",
                status="open",
                diagnostic_event_refs=("event-before", "event-after", "event-final"),
                attempts=(first, duplicate),
            )
        with self.assertRaises(ValueError):
            DiagnosticCase(
                case_id="case-5",
                created_at="2026-10-12T12:00:00Z",
                status="open",
                diagnostic_event_refs=("event-before",),
                attempts=(first,),
            )

    def test_unresolved_case_cannot_produce_retrievable_experience(self):
        with self.assertRaisesRegex(ValueError, "unresolved cases"):
            ExperienceRecord(
                experience_id="experience-1",
                source_case_id="case-unresolved",
                source_case_outcome=CaseOutcome.UNRESOLVED,
                source_attempt_ids=("attempt-1",),
                polarity=ExperiencePolarity.NEGATIVE,
                applicability_conditions=("stage.csynth",),
                action_summary="updated candidate type declaration",
                validation_scope="public_csynth",
                validation_refs=("validation-1",),
                evidence_refs=("evidence-1",),
                source_version_hash="a" * 64,
            )

    def test_experience_round_trip_requires_resolved_source_and_validation(self):
        record = ExperienceRecord(
            experience_id="experience-2",
            source_case_id="case-resolved",
            source_case_outcome="resolved",
            source_attempt_ids=("attempt-1", "attempt-2"),
            polarity="negative",
            applicability_conditions=("stage.csynth", "owner.candidate"),
            action_summary="first declaration-only edit failed validation",
            validation_scope="public_csynth",
            validation_refs=("validation-1",),
            evidence_refs=("evidence-1", "evidence-2"),
            source_version_hash="b" * 64,
            change_fingerprint="c" * 64,
        )

        self.assertEqual(ExperienceRecord.from_dict(record.to_dict()), record)
        self.assertNotIn("model_response", record.to_dict())
        self.assertNotIn("source_code", record.to_dict())
        for version in (True, 1.0, "1", 3):
            with self.subTest(experience_schema_version=version):
                payload = record.to_dict()
                payload["schema_version"] = version
                with self.assertRaises(ValueError):
                    ExperienceRecord.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
