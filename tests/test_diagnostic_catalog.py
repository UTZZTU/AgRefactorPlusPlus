import json
import copy
import tempfile
import unittest
from pathlib import Path

from agrefactor.evaluation.diagnostic_catalog import (
    DiagnosticCatalog,
    DiagnosticCatalogError,
    default_catalog_path,
    load_catalog,
)
from agrefactor.evidence import FeedbackCategory, FeedbackStage


class DiagnosticCatalogTests(unittest.TestCase):
    def payload(self):
        return json.loads(default_catalog_path().read_text(encoding="utf-8"))

    def test_new_semantic_category_and_rule_need_only_json(self):
        payload = self.payload()
        payload["categories"]["new_pointer_form"] = {
            "description": "A newly reviewed unsupported form",
            "feedback_category": "unsupported_construct",
            "evidence_requirements": ["tool_launched", "execution_completed"],
            "allowed_actions": ["review_unknown", "repair_candidate_if_proven"],
        }
        payload["rules"].append({
            "id": "new_code", "match": {"hls_codes": ["HLS 999-123"]},
            "stage": "csynth", "category": "new_pointer_form",
            "owner_policy": "resolve_from_evidence", "priority": 1000,
        })
        match = DiagnosticCatalog(payload).match("new failure", message_id="HLS 999-123")
        self.assertEqual(match.category_id, "new_pointer_form")
        self.assertEqual(match.category, FeedbackCategory.UNSUPPORTED_CONSTRUCT)

    def test_invalid_catalog_changes_are_rejected(self):
        mutations = [
            lambda p: p["rules"][0].update(stage="future_stage"),
            lambda p: p["rules"][0].update(category="not_defined"),
            lambda p: p["rules"][0].update(owner_policy="candidate"),
            lambda p: p["rules"][0].update(priority=True),
            lambda p: p["rules"][0].update(match={"patterns": [".*"]}),
            lambda p: p["rules"][0].update(match={"patterns": ["["]}),
            lambda p: p["rules"][0].update(match={}),
            lambda p: p["categories"]["unknown"].update(allowed_actions=["repair_candidate"]),
            lambda p: p["categories"]["unknown"].update(allowed_actions=["repair_candidate_if_proven"]),
            lambda p: p["rules"][0].update(evaluation_split="hidden"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = self.payload()
                mutation(payload)
                with self.assertRaises(DiagnosticCatalogError):
                    DiagnosticCatalog(payload)

    def test_conflicting_equal_priority_rules_fail_validation(self):
        payload = self.payload()
        other = copy.deepcopy(payload["rules"][0])
        other.update(id="conflict", category="syntax_error")
        payload["rules"].append(other)
        with self.assertRaises(DiagnosticCatalogError):
            DiagnosticCatalog(payload)

    def test_hidden_classification_retains_category_without_agent_actions(self):
        result = load_catalog().match("use of undeclared identifier 'x'", evaluation_split="hidden")
        self.assertEqual(result.category, FeedbackCategory.UNDECLARED_SYMBOL)
        self.assertEqual(result.allowed_actions, ("review_unknown",))

    def test_default_catalog_is_valid_and_classifies_without_owner(self):
        catalog = load_catalog()
        result = catalog.classify(
            {
                "message_id": "HLS 214-134",
                "message": "Pointer to pointer is not supported",
            }
        )

        self.assertIsNotNone(result)
        category, _summary, parser_rule, _confidence = result
        self.assertEqual(category, FeedbackCategory.UNSUPPORTED_CONSTRUCT.value)
        self.assertEqual(parser_rule, "unsupported_pointer_to_pointer")
        rule = next(item for item in catalog.rules if item.parser_rule == parser_rule)
        self.assertEqual(rule.stage, FeedbackStage.CSYNTH)
        self.assertEqual(rule.owner_policy, "resolve_from_evidence")

    def test_unknown_pointer_phi_is_catalog_classified_without_owner(self):
        catalog = load_catalog()
        result = catalog.match(
            "Pointer (phi) points to an unknown underlying object and therefore cannot be synthesized",
            message_id="HLS 214-390",
            stage=FeedbackStage.CSYNTH,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.category, FeedbackCategory.UNSUPPORTED_CONSTRUCT)
        self.assertEqual(result.parser_rule, "unsupported_unknown_pointer_phi")
        self.assertEqual(result.owner_policy, "resolve_from_evidence")

    def test_compile_and_link_categories_are_stage_scoped(self):
        catalog = load_catalog()
        link = catalog.match(
            "undefined reference to `helper'",
            stage=FeedbackStage.LINK,
        )
        self.assertIsNotNone(link)
        self.assertEqual(link.category, FeedbackCategory.LINK_ERROR)
        self.assertEqual(link.stage, FeedbackStage.LINK)
        self.assertEqual(link.owner_policy, "resolve_from_evidence")

        missing = catalog.match(
            "fatal error: helper.hpp: No such file or directory",
            stage=FeedbackStage.COMPILE,
        )
        self.assertIsNotNone(missing)
        self.assertEqual(missing.category, FeedbackCategory.INVALID_CONFIGURATION)
        self.assertEqual(missing.parser_rule, "missing_dependency")

    def test_catalog_rejects_direct_owner_mapping(self):
        payload = {
            "schema_version": 1,
            "categories": {
                "unknown": {
                    "description": "unknown",
                    "evidence_requirements": ["raw_diagnostic"],
                    "allowed_actions": ["review_unknown"],
                }
            },
            "rules": [
                {
                    "id": "bad",
                    "match": {"hls_codes": ["HLS 999-1"]},
                    "stage": "csynth",
                    "category": "unknown",
                    "owner": "toolchain",
                    "owner_policy": "resolve_from_evidence",
                    "priority": 1,
                }
            ],
        }
        with self.assertRaises(DiagnosticCatalogError):
            DiagnosticCatalog(payload)

    def test_catalog_file_is_human_editable_json(self):
        path = Path(default_catalog_path())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("categories", payload)
        self.assertIn("rules", payload)


if __name__ == "__main__":
    unittest.main()
