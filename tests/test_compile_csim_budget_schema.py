import unittest

from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
)


class CompileCsimBudgetSchemaTests(unittest.TestCase):
    def test_omitted_limits_are_unlimited(self) -> None:
        limits = BudgetLimits()

        self.assertIsNone(limits.max_compile_calls)
        self.assertIsNone(limits.max_csim_calls)

        manager = BudgetManager(limits)
        usage = manager.consume(
            tool_calls=4,
            compile_calls=2,
            csim_calls=2,
        )

        self.assertEqual(usage.tool_calls, 4)
        self.assertEqual(usage.compile_calls, 2)
        self.assertEqual(usage.csim_calls, 2)

    def test_compile_limit_is_independent(self) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_compile_calls=0,
                max_csim_calls=10,
            )
        )

        with self.assertRaises(BudgetExceededError) as caught:
            manager.ensure_available(
                tool_calls=1,
                compile_calls=1,
            )

        self.assertEqual(
            caught.exception.resource,
            "compile_calls",
        )
        usage = manager.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.compile_calls, 0)

    def test_csim_limit_is_independent(self) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_compile_calls=10,
                max_csim_calls=0,
            )
        )

        with self.assertRaises(BudgetExceededError) as caught:
            manager.ensure_available(
                tool_calls=1,
                csim_calls=1,
            )

        self.assertEqual(
            caught.exception.resource,
            "csim_calls",
        )
        usage = manager.snapshot()
        self.assertEqual(usage.tool_calls, 0)
        self.assertEqual(usage.csim_calls, 0)

    def test_total_tool_limit_can_block_full_csim_plan(
        self,
    ) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_tool_calls=1,
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )

        with self.assertRaises(BudgetExceededError) as caught:
            manager.ensure_available(
                tool_calls=2,
                compile_calls=1,
                csim_calls=1,
            )

        self.assertEqual(
            caught.exception.resource,
            "tool_calls",
        )

    def test_failed_multi_resource_consume_is_atomic(
        self,
    ) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_tool_calls=10,
                max_compile_calls=10,
                max_csim_calls=0,
            )
        )
        manager.consume(
            tool_calls=1,
            compile_calls=1,
        )

        with self.assertRaises(BudgetExceededError):
            manager.consume(
                tool_calls=1,
                csim_calls=1,
            )

        usage = manager.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)
        self.assertEqual(usage.csim_calls, 0)

    def test_exact_compile_and_csim_limits_are_exhausted(
        self,
    ) -> None:
        manager = BudgetManager(
            BudgetLimits(
                max_compile_calls=1,
                max_csim_calls=1,
            )
        )
        manager.consume(
            compile_calls=1,
            csim_calls=1,
        )

        self.assertTrue(manager.exhausted())


if __name__ == "__main__":
    unittest.main()
