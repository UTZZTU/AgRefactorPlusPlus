import unittest

from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BudgetManagerTests(unittest.TestCase):
    def test_consume_updates_usage(self) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_llm_calls=2,
                max_tool_calls=3,
                max_csynth_calls=2,
                max_tokens=100,
                max_cost_usd=1.0,
            )
        )

        usage = manager.consume(
            llm_calls=1,
            tool_calls=2,
            csynth_calls=1,
            tokens=40,
            cost_usd=0.25,
        )

        self.assertEqual(usage.llm_calls, 1)
        self.assertEqual(usage.tool_calls, 2)
        self.assertEqual(usage.csynth_calls, 1)
        self.assertEqual(usage.tokens, 40)
        self.assertAlmostEqual(usage.cost_usd, 0.25)

    def test_rejects_excess_without_partial_update(self) -> None:
        manager = BudgetManager(BudgetLimits(max_tokens=10))
        manager.consume(tokens=6)

        with self.assertRaises(BudgetExceededError):
            manager.consume(
                tokens=5,
                tool_calls=1,
                csynth_calls=1,
            )

        usage = manager.snapshot()
        self.assertEqual(usage.tokens, 6)
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.csynth_calls, 0)

    def test_rejects_negative_increment(self) -> None:
        manager = BudgetManager()

        with self.assertRaises(ValueError):
            manager.consume(llm_calls=-1)

        with self.assertRaises(ValueError):
            manager.ensure_available(csynth_calls=-1)

    def test_detects_wall_time_limit(self) -> None:
        clock = FakeClock()
        manager = BudgetManager(
            BudgetLimits(max_wall_time_s=5.0),
            clock=clock,
        )
        clock.advance(6.0)

        with self.assertRaises(BudgetExceededError):
            manager.snapshot()

    def test_reports_exhausted_at_exact_limit(self) -> None:
        manager = BudgetManager(
            BudgetLimits(max_csynth_calls=1)
        )
        manager.consume(csynth_calls=1)

        self.assertTrue(manager.exhausted())

    def test_csynth_limit_is_independent_from_total_tools(self) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_csynth_calls=0,
            )
        )

        with self.assertRaises(BudgetExceededError) as caught:
            manager.ensure_available(
                tool_calls=1,
                csynth_calls=1,
            )

        self.assertEqual(caught.exception.resource, "csynth_calls")
        usage = manager.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.csynth_calls, 0)

    def test_total_tool_limit_can_block_csynth(self) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_tool_calls=0,
                max_csynth_calls=10,
            )
        )

        with self.assertRaises(BudgetExceededError) as caught:
            manager.ensure_available(
                tool_calls=1,
                csynth_calls=1,
            )

        self.assertEqual(caught.exception.resource, "tool_calls")


if __name__ == "__main__":
    unittest.main()
