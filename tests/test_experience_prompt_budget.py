from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agrefactor.evidence.case_record import ExperiencePolarity, ExperienceRecord
from agrefactor.evidence.case_store import ExperienceSnapshot, StoredChange, StoredExperience
from agrefactor.evidence.experience_retrieval import CurrentCaseQuery, retrieve_experiences
from agrefactor.models.model_token_counting import (
    ModelTokenCounter,
    TokenCountConfig,
    select_experience_context,
)


class WhitespaceTokenizer:
    chat_template = "registered"

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        text = " ".join(f"{item['role']} {item['content']}" for item in messages)
        return text.split()


def item(name, *, polarity=ExperiencePolarity.POSITIVE, patch_words=3):
    attempt_id = f"attempt-{name}"
    patch = "\n".join(f"+ token_{index}" for index in range(patch_words)) + "\n"
    fingerprint = sha256(patch.encode()).hexdigest()
    change = StoredChange(
        attempt_id=attempt_id,
        components=("candidate",),
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
        applicability_conditions=("stage.csynth", "owner.candidate"),
        action_summary="candidate interface update",
        validation_scope="csynth",
        validation_refs=(f"validation-{name}",),
        evidence_refs=(f"change:{attempt_id}",),
        source_version_hash="a" * 64,
        change_fingerprint=fingerprint,
        schema_version=2,
        stage="csynth",
        owner="candidate",
        components=("candidate",),
        visibility="public",
        diagnostic_terms=("hls_214-298", "interface", "pointer"),
        validation_contract="csynth_contract_v1",
        failure_code="failed_validation" if polarity is ExperiencePolarity.NEGATIVE else None,
    )
    return StoredExperience(record, change)


class ExperiencePromptBudgetTests(unittest.TestCase):
    def build_counter(self, context=10000):
        config = TokenCountConfig(
            provider="deepseek",
            model_id="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            request_mode="chat_completions",
            tokenizer_dir=Path("."),
            tokenizer_hashes={},
            context_window=context,
            safe_margin_tokens=4,
            verification_status="verified",
        )
        return ModelTokenCounter(config, WhitespaceTokenizer())

    def retrieval(self, positives=(), negatives=(), owner="candidate"):
        query = CurrentCaseQuery(
            stage="csynth",
            visibility="public",
            owner=owner,
            diagnostic_codes=("hls_214-298",),
            diagnostic_terms=("interface", "pointer"),
            components=() if owner == "unknown" else ("candidate",),
        )
        return retrieve_experiences(
            query,
            ExperienceSnapshot("revision", tuple((*positives, *negatives)), ()),
        )

    def render(self, base_text="current evidence"):
        def build(snippets):
            content = base_text
            if snippets:
                content += "\nHistorical references:\n" + "\n".join(snippets)
            return ({"role": "system", "content": "Repair under local rules."}, {"role": "user", "content": content})
        return build

    def run_select(self, result, counter, render=None):
        return select_experience_context(
            result,
            counter=counter,
            provider="deepseek",
            model_id="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            request_mode="chat_completions",
            call_parameters={"max_tokens": 8},
            render_messages=render or self.render(),
        )

    def test_three_positive_and_two_negative_caps_are_enforced(self):
        positives = tuple(item(f"p{i}") for i in range(4))
        negatives = tuple(item(f"n{i}", polarity=ExperiencePolarity.NEGATIVE) for i in range(3))
        selected, budget = self.run_select(self.retrieval(positives, negatives), self.build_counter())
        self.assertEqual(sum("[positive experience" in value for value in budget.selected_snippets), 3)
        self.assertEqual(sum("[negative experience" in value for value in budget.selected_snippets), 2)
        self.assertLessEqual(budget.final_tokens - budget.base_tokens, budget.experience_budget)
        self.assertLessEqual(budget.final_tokens + budget.output_reserve + budget.safe_margin, budget.context_window)

    def test_oversized_entry_is_skipped_whole_and_later_small_item_can_fit(self):
        large = item("large", patch_words=700)
        small = item("small", patch_words=1)
        result = self.retrieval((large, small))
        _, budget = self.run_select(result, self.build_counter(context=1200))
        self.assertIn(("experience-large", "experience_budget_exceeded"), budget.skipped)
        self.assertIn("experience-small", budget.selected_ids)
        self.assertNotIn("token_0\ntoken_1", "\n".join(budget.selected_snippets))

    def test_unknown_owner_context_contains_summary_but_no_change(self):
        result = self.retrieval((item("public"),), owner="unknown")
        _, budget = self.run_select(result, self.build_counter())
        self.assertEqual(len(budget.selected_ids), 1)
        self.assertNotIn("Verified change:", budget.selected_snippets[0])
        self.assertNotIn("+ token_0", budget.selected_snippets[0])

    def test_long_base_prompt_or_output_reserve_can_leave_empty_injection(self):
        result = self.retrieval((item("fit"),))
        _, short = self.run_select(result, self.build_counter(context=1000))
        _, long = self.run_select(result, self.build_counter(context=1000), self.render("base " * 995))
        self.assertTrue(short.selected_ids)
        self.assertFalse(long.selected_ids)
        self.assertEqual(long.experience_budget, 0)


if __name__ == "__main__":
    unittest.main()
