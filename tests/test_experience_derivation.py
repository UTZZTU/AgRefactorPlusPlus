from hashlib import sha256
import unittest

from agrefactor.evidence import (
    CaseAttempt,
    DiagnosticCase,
    ExperiencePolarity,
    StoredChange,
    derive_experiences,
)


def make_change(attempt_id, before, after):
    patch = f"@@ -1 +1 @@\n-{before}\n+{after}\n"
    return StoredChange(
        attempt_id=attempt_id,
        components=("candidate",),
        before_sha256=sha256(before.encode()).hexdigest(),
        after_sha256=sha256(after.encode()).hexdigest(),
        change_fingerprint=sha256(patch.encode()).hexdigest(),
        patch=patch,
    )


class ExperienceDerivationTests(unittest.TestCase):
    def test_resolved_case_derives_attempt_level_positive_and_negative_records(self):
        failed = CaseAttempt(
            attempt_id="attempt-fail",
            sequence_index=0,
            result="validation_failed",
            pre_action_event_refs=("event-start",),
            post_validation_event_refs=("event-failed",),
            authorization_ref="event-start",
            quota_buckets=("candidate",),
            changed=True,
        )
        passed = CaseAttempt(
            attempt_id="attempt-pass",
            sequence_index=1,
            result="validated",
            pre_action_event_refs=("event-failed",),
            post_validation_event_refs=("event-passed",),
            authorization_ref="event-failed",
            quota_buckets=("candidate",),
            changed=True,
        )
        case = DiagnosticCase(
            case_id="case-resolved",
            created_at="2026-10-15T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="validated",
            diagnostic_event_refs=("event-start", "event-failed", "event-passed"),
            attempts=(failed, passed),
        )
        events = {
            "event-start": {
                "event_id": "event-start",
                "stage": "csynth",
                "owner": "candidate",
                "visibility": "public",
                "diagnostic_items": [{"diagnostic_code": "hls_214-298"}],
            },
            "event-failed": {
                "event_id": "event-failed",
                "stage": "csynth",
                "accepted": False,
                "failure_attributable_to_change": True,
                "failure_code": "interface_contract_mismatch",
                "validation_contract": "csynth_candidate_contract_v1",
            },
            "event-passed": {
                "event_id": "event-passed",
                "stage": "csynth",
                "accepted": True,
                "validation_contract": "csynth_candidate_contract_v1",
            },
        }

        first = derive_experiences(
            case,
            changes={
                "attempt-fail": make_change("attempt-fail", "int x;", "int x = 0;"),
                "attempt-pass": make_change("attempt-pass", "int x = 0;", "int x = 1;"),
            },
            events=events,
        )
        second = derive_experiences(case, changes={"attempt-fail": make_change("attempt-fail", "int x;", "int x = 0;")}, events=events)

        self.assertEqual([item.record.polarity for item in first], [ExperiencePolarity.NEGATIVE])
        self.assertEqual([item.record.experience_id for item in first], [item.record.experience_id for item in second])
        self.assertEqual(first[0].record.source_attempt_ids, ("attempt-fail",))

    def test_single_changed_attempt_derives_positive_v2_record(self):
        attempt = CaseAttempt(
            attempt_id="attempt-pass",
            sequence_index=0,
            result="validated",
            pre_action_event_refs=("event-start",),
            post_validation_event_refs=("event-passed",),
            authorization_ref="event-start",
            quota_buckets=("candidate",),
            changed=True,
        )
        case = DiagnosticCase(
            case_id="case-positive",
            created_at="2026-10-15T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="validated",
            diagnostic_event_refs=("event-start", "event-passed"),
            attempts=(attempt,),
        )
        result = derive_experiences(
            case,
            changes={"attempt-pass": make_change("attempt-pass", "int x;", "int x = 1;")},
            events={
                "event-start": {
                    "event_id": "event-start",
                    "stage": "csynth",
                    "owner": "candidate",
                    "split": "public",
                    "diagnostic_items": [{"diagnostic_code": "hls_214-298"}],
                },
                "event-passed": {
                    "event_id": "event-passed",
                    "accepted": True,
                    "validation_contract": "csynth_candidate_contract_v1",
                },
            },
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].record.polarity, ExperiencePolarity.POSITIVE)
        self.assertEqual(result[0].record.schema_version, 2)
        self.assertEqual(result[0].record.visibility, "public")

    def test_multi_attempt_success_uses_cumulative_change_and_initial_evidence(self):
        first = CaseAttempt(
            attempt_id="attempt-first",
            sequence_index=0,
            result="validation_failed",
            pre_action_event_refs=("event-start",),
            post_validation_event_refs=("event-first-failed",),
            authorization_ref="event-start",
            quota_buckets=("candidate",),
            changed=True,
        )
        final = CaseAttempt(
            attempt_id="attempt-final",
            sequence_index=1,
            result="validated",
            pre_action_event_refs=("event-first-failed",),
            post_validation_event_refs=("event-passed",),
            authorization_ref="event-first-failed",
            quota_buckets=("candidate",),
            changed=True,
        )
        case = DiagnosticCase(
            case_id="case-multi-step",
            created_at="2026-10-15T12:00:00Z",
            status="closed",
            outcome="resolved",
            closure_reason="validated",
            diagnostic_event_refs=("event-start", "event-first-failed", "event-passed"),
            attempts=(first, final),
        )
        before = "int top(int value) {\n  return value;\n}\n"
        after = "int top(int value) {\n  int adjusted = value + 1;\n  return adjusted;\n}\n"
        patch = "@@ cumulative\n-before\n+after\n"
        change = StoredChange(
            attempt_id="attempt-final",
            source_attempt_ids=("attempt-first", "attempt-final"),
            components=("candidate",),
            before_sha256=sha256(before.encode()).hexdigest(),
            after_sha256=sha256(after.encode()).hexdigest(),
            change_fingerprint=sha256(patch.encode()).hexdigest(),
            patch=patch,
        )
        events = {
            "event-start": {
                "event_id": "event-start",
                "stage": "csynth",
                "owner": "candidate",
                "visibility": "public",
                "diagnostic_items": [{"diagnostic_code": "hls_214-298"}],
            },
            "event-first-failed": {
                "event_id": "event-first-failed",
                "accepted": False,
                "validation_contract": "csynth_candidate_contract_v1",
            },
            "event-passed": {
                "event_id": "event-passed",
                "accepted": True,
                "validation_contract": "csynth_candidate_contract_v1",
            },
        }
        result = derive_experiences(
            case,
            changes={"attempt-final": change},
            events=events,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].record.polarity, ExperiencePolarity.POSITIVE)
        self.assertEqual(
            result[0].record.source_attempt_ids,
            ("attempt-first", "attempt-final"),
        )
        self.assertEqual(result[0].record.evidence_refs, ("change:attempt-final",))
        self.assertIn("2 repair attempts", result[0].record.action_summary)

    def test_unresolved_unknown_case_produces_no_experience(self):
        case = DiagnosticCase(
            case_id="case-unknown",
            created_at="2026-10-15T12:00:00Z",
            status="closed",
            outcome="unresolved",
            closure_reason="budget_exhausted",
            diagnostic_event_refs=("event-start",),
            attempts=(),
        )
        self.assertEqual(derive_experiences(case, changes={}, events={}), ())


if __name__ == "__main__":
    unittest.main()
