from __future__ import annotations

from argparse import Namespace
from decimal import Decimal
from types import SimpleNamespace
import unittest

from agrefactor.product.source_bootstrap import _budget_from_cli


def _args(*, cost_budget=None):
    return Namespace(
        max_llm_calls=None,
        max_tool_calls=None,
        max_compile_calls=None,
        max_csim_calls=None,
        max_csynth_calls=None,
        max_wall_time_s=None,
        token_budget=None,
        cost_budget=cost_budget,
    )


def _selection(currency):
    pricing = (
        None
        if currency is None
        else SimpleNamespace(currency=currency)
    )
    return SimpleNamespace(
        effective_config=SimpleNamespace(
            pricing_snapshot=pricing,
        )
    )


class P0CostBudgetCurrencyCorrectionTests(unittest.TestCase):
    def test_priced_model_without_cost_budget_has_no_currency(self):
        budget = _budget_from_cli(
            _args(cost_budget=None),
            _selection("CNY"),
        )
        self.assertIsNone(budget.cost_budget)
        self.assertIsNone(budget.cost_budget_currency)
        self.assertEqual(
            budget.budget_source_per_field["max_llm_calls"],
            "system_default",
        )

    def test_explicit_cost_budget_uses_pricing_currency(self):
        budget = _budget_from_cli(
            _args(cost_budget="1.00"),
            _selection("CNY"),
        )
        self.assertEqual(budget.cost_budget, Decimal("1.00"))
        self.assertEqual(budget.cost_budget_currency, "CNY")

    def test_unpriced_model_without_cost_budget_remains_valid(self):
        budget = _budget_from_cli(
            _args(cost_budget=None),
            _selection(None),
        )
        self.assertIsNone(budget.cost_budget)
        self.assertIsNone(budget.cost_budget_currency)


if __name__ == "__main__":
    unittest.main()
