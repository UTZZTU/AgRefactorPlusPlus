import json
from types import SimpleNamespace
import unittest

from agrefactor.recovery.diagnostic_assistant import (
    DiagnosticAssistant,
    DiagnosticAssistantOutputRejected,
    DiagnosticAssistantRequest,
)


class FakeAdapter:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def generate_auxiliary_text(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(text=self.text)


class DiagnosticAssistantTests(unittest.TestCase):
    def request(self, **overrides):
        values = {
            "stage": "csynth",
            "evidence": (
                {
                    "evidence_id": "feedback-1",
                    "category": "synthesis",
                    "summary": "HLS 214-298 interface diagnostic",
                },
            ),
            "experiences": ("Historical reference only.",),
            "check_ids": (),
        }
        values.update(overrides)
        return DiagnosticAssistantRequest(**values)

    def test_suggestion_only_cites_current_evidence_and_uses_run_budget(self):
        adapter = FakeAdapter(
            json.dumps(
                {
                    "possible_owner": "candidate",
                    "summary": "The interface declaration may be incompatible.",
                    "evidence_refs": ["feedback-1"],
                    "check_ids": [],
                    "abstain": False,
                }
            )
        )
        result = DiagnosticAssistant(adapter, object()).diagnose(self.request())
        self.assertEqual(result.possible_owner, "candidate")
        self.assertEqual(result.evidence_refs, ("feedback-1",))
        self.assertFalse(result.to_dict()["authorization_changed"])
        self.assertFalse(result.to_dict()["owner_changed"])
        self.assertEqual(len(adapter.calls), 1)
        messages, call = adapter.calls[0]
        self.assertIn("Historical reference only.", messages[1].content)
        self.assertEqual(call["evidence"]["evidence_ids"], ["feedback-1"])

    def test_out_of_scope_reference_or_unregistered_check_is_rejected(self):
        payload = {
            "possible_owner": "candidate",
            "summary": "Possible interface mismatch.",
            "evidence_refs": ["private-evidence"],
            "check_ids": ["run-shell-command"],
            "abstain": False,
        }
        with self.assertRaises(DiagnosticAssistantOutputRejected):
            DiagnosticAssistant.parse(self.request(), json.dumps(payload))

    def test_commands_paths_and_extra_authority_fields_are_rejected(self):
        for summary in ("Run source /tmp/collect.sh", "sudo inspect this path"):
            payload = {
                "possible_owner": "unknown",
                "summary": summary,
                "evidence_refs": ["feedback-1"],
                "check_ids": [],
                "abstain": False,
            }
            with self.subTest(summary=summary), self.assertRaises(
                DiagnosticAssistantOutputRejected
            ):
                DiagnosticAssistant.parse(self.request(), json.dumps(payload))
        payload["summary"] = "Possible cause is uncertain."
        payload["repair_eligible"] = True
        with self.assertRaises(DiagnosticAssistantOutputRejected):
            DiagnosticAssistant.parse(self.request(), json.dumps(payload))

    def test_abstention_clears_owner_and_references(self):
        payload = {
            "possible_owner": "candidate",
            "summary": "Evidence does not distinguish ownership.",
            "evidence_refs": ["feedback-1"],
            "check_ids": [],
            "abstain": True,
        }
        result = DiagnosticAssistant.parse(self.request(), json.dumps(payload))
        self.assertEqual(result.possible_owner, "unknown")
        self.assertEqual(result.evidence_refs, ())
        self.assertTrue(result.abstain)


if __name__ == "__main__":
    unittest.main()
