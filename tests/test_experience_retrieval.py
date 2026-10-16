from hashlib import sha256
import unittest

from agrefactor.evidence.case_record import ExperiencePolarity, ExperienceRecord
from agrefactor.evidence.case_store import (
    ExperienceSnapshot,
    StoredChange,
    StoredExperience,
)
from agrefactor.evidence.experience_retrieval import (
    CurrentCaseQuery,
    retrieve_experiences,
)
from agrefactor.prompts.experience_context import (
    current_case_query_from_feedback,
)


def stored_experience(
    name,
    *,
    owner="candidate",
    stage="csynth",
    visibility="public",
    terms=("hls_214-298", "interface", "pointer"),
    source_terms=("top_kernel", "input_t"),
    evidence_terms=("interface_report",),
    polarity=ExperiencePolarity.POSITIVE,
):
    attempt_id = f"attempt-{name}"
    patch = f"@@ {name}\n-old\n+new\n"
    fingerprint = sha256(patch.encode()).hexdigest()
    change = StoredChange(
        attempt_id=attempt_id,
        components=(owner,),
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        change_fingerprint=fingerprint,
        patch=patch,
    )
    record = ExperienceRecord(
        experience_id=f"experience-{name}",
        source_case_id=f"case-{name}",
        source_case_outcome="resolved",
        source_attempt_ids=(attempt_id,),
        polarity=polarity,
        applicability_conditions=(f"stage.{stage}", f"owner.{owner}"),
        action_summary=f"{owner} interface update",
        validation_scope=stage,
        validation_refs=(f"validation-{name}",),
        evidence_refs=(f"change:{attempt_id}",),
        source_version_hash="a" * 64,
        change_fingerprint=fingerprint,
        schema_version=2,
        stage=stage,
        owner=owner,
        components=(owner,),
        visibility=visibility,
        diagnostic_terms=terms,
        source_terms=source_terms,
        evidence_terms=evidence_terms,
        validation_contract="csynth_contract_v1",
        toolchain="vitis_2023_2",
        failure_code=("validation_failed" if polarity is ExperiencePolarity.NEGATIVE else None),
    )
    return StoredExperience(record=record, change=change)


class ExperienceRetrievalTests(unittest.TestCase):
    def snapshot(self, *items):
        return ExperienceSnapshot("revision-1", tuple(items), ())

    def query(self, **overrides):
        values = {
            "stage": "csynth",
            "visibility": "public",
            "owner": "candidate",
            "diagnostic_codes": ("hls_214-298",),
            "diagnostic_terms": ("interface", "pointer"),
            "components": ("candidate",),
            "validation_contract": "csynth_contract_v1",
            "toolchain": "vitis_2023_2",
        }
        values.update(overrides)
        return CurrentCaseQuery(**values)

    def test_unknown_owner_can_retrieve_diagnosis_without_change(self):
        result = retrieve_experiences(
            self.query(owner="unknown", components=()),
            self.snapshot(stored_experience("one")),
        )
        self.assertEqual(len(result.positive), 1)
        self.assertFalse(result.positive[0].change_available)

    def test_known_owner_component_and_contract_are_hard_filters(self):
        item = stored_experience("one")
        mismatch = retrieve_experiences(
            self.query(owner="testbench", components=("testbench",)),
            self.snapshot(item),
        )
        self.assertEqual(mismatch.positive, ())
        self.assertIn(("experience-one", "owner_mismatch"), mismatch.excluded)
        contract = retrieve_experiences(
            self.query(validation_contract="other_contract"),
            self.snapshot(item),
        )
        self.assertIn(("experience-one", "validation_contract_mismatch"), contract.excluded)

    def test_positive_and_negative_are_partitioned_and_order_is_stable(self):
        items = (
            stored_experience("same-stage"),
            stored_experience("cross-stage", stage="csim"),
            stored_experience("negative", polarity=ExperiencePolarity.NEGATIVE),
        )
        first = retrieve_experiences(self.query(), self.snapshot(*items))
        second = retrieve_experiences(self.query(), self.snapshot(*reversed(items)))
        self.assertEqual(
            tuple(item.item.record.experience_id for item in first.positive),
            tuple(item.item.record.experience_id for item in second.positive),
        )
        self.assertEqual(first.negative[0].polarity, ExperiencePolarity.NEGATIVE)
        self.assertEqual(first.positive[0].item.record.stage, "csynth")

    def test_relevance_precedes_stage_and_failed_action_is_not_filtered(self):
        strong_cross_stage = stored_experience(
            "strong-cross",
            stage="cosim",
            terms=("hls_214-298", "interface", "pointer", "top_kernel"),
            source_terms=("top_kernel", "input_t"),
        )
        query = self.query(attempted_change_fingerprints=(strong_cross_stage.change.change_fingerprint,))
        result = retrieve_experiences(query, self.snapshot(stored_experience("weak-same", terms=("hls_214-298",)), strong_cross_stage))
        self.assertEqual(result.positive[0].item.record.experience_id, "experience-strong-cross")
        self.assertTrue(result.positive[0].previously_attempted)

    def test_low_relevance_and_hidden_generation_projection_return_empty(self):
        unrelated = stored_experience("unrelated", terms=("syntax_error",))
        empty = retrieve_experiences(self.query(), self.snapshot(unrelated))
        self.assertEqual(empty.positive, ())
        public = stored_experience("public")
        hidden_safe = stored_experience("generation", visibility="public_generation")
        hidden = retrieve_experiences(
            self.query(visibility="hidden_generation", owner="unknown", components=()),
            self.snapshot(public, hidden_safe),
        )
        self.assertEqual([item.item.record.experience_id for item in hidden.positive], ["experience-generation"])

    def test_formal_hidden_query_is_rejected(self):
        with self.assertRaises(ValueError):
            self.query(visibility="hidden_evaluation")

    def test_feedback_query_uses_only_allowlisted_structured_features(self):
        from types import SimpleNamespace

        report = SimpleNamespace(
            metadata={"evidence_view": "agent_safe"},
            items=(
                SimpleNamespace(
                    metadata={
                        "message_id": "HLS 214-298",
                        "evidence_class": "interface_report",
                        "top_function": "top_kernel",
                        "type_name": "input_t",
                        "file": "/private/server/path/not_used.cpp",
                    },
                    category=SimpleNamespace(value="synthesis"),
                    owner=SimpleNamespace(value="candidate"),
                    summary="Pointer member in the top interface",
                    detail="candidate declaration rejected",
                ),
            ),
        )
        query = current_case_query_from_feedback(
            report,
            stage="csynth",
            visibility="public",
            owner="unknown",
            components=(),
            validation_contract="csynth_contract_v1",
        )
        self.assertIn("hls_214-298", query.diagnostic_codes)
        self.assertIn("interface_report", query.evidence_terms)
        self.assertIn("top_kernel", query.source_terms)
        self.assertIn("candidate", query.owner_clues)
        self.assertNotIn("private", query.source_terms)


if __name__ == "__main__":
    unittest.main()
