import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from agrefactor.evidence import (
    CaseStatus,
    DiagnosticCase,
    CaseStore,
    ExperiencePolarity,
    ExperienceRecord,
    ExperienceStore,
    StoredChange,
)


def make_change(attempt_id="attempt-1", patch="@@ -1 +1 @@\n-old\n+new\n"):
    return StoredChange(
        attempt_id=attempt_id,
        components=("candidate",),
        before_sha256=sha256(b"old").hexdigest(),
        after_sha256=sha256(b"new").hexdigest(),
        change_fingerprint=sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )


class CaseStoreTests(unittest.TestCase):
    def make_case(self, status=CaseStatus.OPEN, outcome=None):
        return DiagnosticCase(
            case_id="case-1",
            created_at="2026-10-15T12:00:00Z",
            status=status,
            diagnostic_event_refs=("event-1",),
            outcome=outcome,
            closure_reason="validated" if outcome else None,
        )

    def test_case_and_change_round_trip_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(directory)
            case = self.make_case()
            self.assertTrue(store.write_case(case))
            self.assertFalse(store.write_case(case))
            change = make_change()
            self.assertTrue(store.write_change(case.case_id, change))
            self.assertFalse(store.write_change(case.case_id, change))
            self.assertEqual(store.read_case(case.case_id), case)
            self.assertEqual(store.read_changes(case.case_id)["attempt-1"], change)
            self.assertEqual(tuple(item.case_id for item in store.open_cases()), ("case-1",))

    def test_conflicting_create_does_not_overwrite_existing_case(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(directory)
            case = self.make_case()
            store.write_case(case)
            changed = DiagnosticCase(
                case_id=case.case_id,
                created_at=case.created_at,
                status="closed",
                outcome="unresolved",
                closure_reason="budget_exhausted",
                diagnostic_event_refs=case.diagnostic_event_refs,
            )
            with self.assertRaises(FileExistsError):
                store.write_case(changed)
            self.assertEqual(store.read_case(case.case_id), case)

    def test_case_updates_are_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(directory)
            case = self.make_case()
            store.write_case(case)
            closed = DiagnosticCase(
                case_id=case.case_id,
                created_at=case.created_at,
                status="closed",
                outcome="unresolved",
                closure_reason="budget_exhausted",
                diagnostic_event_refs=case.diagnostic_event_refs,
            )
            self.assertTrue(store.update_case(closed))
            self.assertFalse(store.update_case(closed))
            self.assertEqual(store.open_cases(), ())
            self.assertEqual(store.read_case(case.case_id), closed)

    def test_concurrent_idempotent_case_create(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(directory)
            case = self.make_case()
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: store.write_case(case), range(24)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(store.read_case(case.case_id), case)

    def test_hidden_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(directory)
            with self.assertRaisesRegex(ValueError, "Hidden"):
                store.write_event(
                    "case-1",
                    "event-hidden",
                    {
                        "event_id": "event-hidden",
                        "stage": "hidden_evaluation",
                        "evidence_view": "agent_safe",
                    },
                )

    def test_experience_snapshot_preserves_cumulative_attempt_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            patch = "@@ -1 +1 @@\n-old\n+new\n"
            fingerprint = sha256(patch.encode("utf-8")).hexdigest()
            attempts = ("attempt-1", "attempt-2")
            record = ExperienceRecord(
                experience_id="experience-1",
                source_case_id="case-1",
                source_case_outcome="resolved",
                source_attempt_ids=attempts,
                polarity=ExperiencePolarity.POSITIVE,
                applicability_conditions=("stage.csynth", "owner.candidate"),
                action_summary="candidate cumulative change across 2 repair attempts",
                validation_scope="csynth",
                validation_refs=("validation-2",),
                evidence_refs=("change:attempt-2",),
                source_version_hash="a" * 64,
                change_fingerprint=fingerprint,
                schema_version=2,
                stage="csynth",
                owner="candidate",
                components=("candidate",),
                visibility="public",
                diagnostic_terms=("hls_214-298", "interface"),
                validation_contract="csynth_contract_v1",
            )
            change = StoredChange(
                attempt_id="attempt-2",
                source_attempt_ids=attempts,
                components=("candidate",),
                before_sha256="a" * 64,
                after_sha256="b" * 64,
                change_fingerprint=fingerprint,
                patch=patch,
            )
            store = ExperienceStore(directory)
            self.assertTrue(store.write(record, change))
            snapshot = store.read_snapshot()
            self.assertEqual(len(snapshot.experiences), 1)
            stored = snapshot.experiences[0]
            self.assertEqual(stored.change.attempt_id, "attempt-2")
            self.assertEqual(stored.change.source_attempt_ids, attempts)
            self.assertEqual(stored.record.source_attempt_ids, attempts)


if __name__ == "__main__":
    unittest.main()
