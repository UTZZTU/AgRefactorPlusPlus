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
            "event-failed": {"event_id": "event-failed", "stage": "csynth", "accepted": False},
            "event-passed": {"event_id": "event-passed", "stage": "csynth", "accepted": True},
        }

        first = derive_experiences(
            case,
            changes={
                "attempt-fail": make_change("attempt-fail", "int x;", "int x = 0;"),
                "attempt-pass": make_change("attempt-pass", "int x = 0;", "int x = 1;"),
            },
            events=events,
        )
        second = derive_experiences(
            case,
            changes={item.change.attempt_id: item.change for item in first},
            events=events,
        )

        self.assertEqual([item.record.polarity for item in first], [ExperiencePolarity.NEGATIVE, ExperiencePolarity.POSITIVE])
        self.assertEqual([item.record.experience_id for item in first], [item.record.experience_id for item in second])
        self.assertEqual(first[0].record.source_attempt_ids, ("attempt-fail",))
        self.assertEqual(first[1].record.source_attempt_ids, ("attempt-pass",))

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
