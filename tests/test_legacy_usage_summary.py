from __future__ import annotations

from contextlib import redirect_stdout
import inspect
import io
import json
import unittest
from unittest.mock import patch

import flow.base_agent as base_agent_module


class UsageAgent:
    def __init__(self, *, actual=None, total=None):
        self.actual = actual
        self.total = total
        self.actual_calls = 0
        self.total_calls = 0

    def get_actual_usage(self):
        self.actual_calls += 1
        return self.actual

    def get_total_usage(self):
        self.total_calls += 1
        return self.total


class LegacyUsageSummaryTests(unittest.TestCase):
    def setUp(self):
        base_agent_module.reset_agrefactorpp_usage_registry()

    def tearDown(self):
        base_agent_module.reset_agrefactorpp_usage_registry()

    def register(self, *agents):
        for agent in agents:
            base_agent_module.register_agrefactorpp_usage_agent(
                agent
            )

    def gather(self, usage):
        self.register(UsageAgent())
        with patch(
            "autogen.gather_usage_summary",
            return_value=usage,
            create=True,
        ):
            return (
                base_agent_module
                .get_agrefactorpp_usage_summary()
            )

    def fallback(self, *agents, error=None):
        self.register(*agents)
        with patch(
            "autogen.gather_usage_summary",
            side_effect=error or RuntimeError("gather failed"),
            create=True,
        ):
            return (
                base_agent_module
                .get_agrefactorpp_usage_summary()
            )

    @staticmethod
    def usage_data(
        *,
        model="model-a",
        prompt=10,
        completion=5,
        total=None,
        cost_marker="missing",
        total_cost_marker="missing",
    ):
        model_data = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": (
                prompt + completion
                if total is None
                else total
            ),
        }
        if cost_marker != "missing":
            model_data["cost"] = cost_marker

        data = {
            model: model_data,
        }
        if total_cost_marker != "missing":
            data["total_cost"] = total_cost_marker
        return {
            "usage_including_cached_inference": data,
        }

    def test_empty_summary_has_unavailable_cost(self):
        summary = (
            base_agent_module
            .get_agrefactorpp_usage_summary()
        )
        observation = summary[
            "framework_reported_cost"
        ]
        self.assertEqual(
            observation["quality"],
            "unavailable",
        )
        self.assertIsNone(observation["amount"])

    def test_empty_summary_keeps_legacy_cost_none(self):
        summary = (
            base_agent_module
            .get_agrefactorpp_usage_summary()
        )
        self.assertIsNone(summary["total_cost"])
        self.assertIsNone(summary["cost_usd"])

    def test_empty_summary_has_empty_currency_ledger(self):
        summary = (
            base_agent_module
            .get_agrefactorpp_usage_summary()
        )
        self.assertEqual(
            summary["costs_by_currency"],
            {},
        )

    def test_gather_preserves_tokens(self):
        summary = self.gather(
            self.usage_data(
                prompt=12,
                completion=8,
                total=20,
            )
        )
        self.assertEqual(summary["prompt_tokens"], 12)
        self.assertEqual(
            summary["completion_tokens"],
            8,
        )
        self.assertEqual(summary["total_tokens"], 20)

    def test_gather_quarantines_model_cost(self):
        summary = self.gather(
            self.usage_data(cost_marker=0.25)
        )
        model = summary["models"]["model-a"]
        self.assertIsNone(model["cost"])
        self.assertIsNone(model["cost_usd"])
        self.assertEqual(
            model["framework_reported_cost"]["amount"],
            "0.25",
        )
        self.assertFalse(
            model["framework_reported_cost"][
                "ledger_eligible"
            ]
        )

    def test_gather_quarantines_total_cost(self):
        summary = self.gather(
            self.usage_data(
                cost_marker=0.25,
                total_cost_marker=0.5,
            )
        )
        self.assertIsNone(summary["total_cost"])
        self.assertEqual(
            summary["framework_reported_cost"]["amount"],
            "0.5",
        )
        self.assertFalse(
            summary["framework_reported_cost"][
                "ledger_eligible"
            ]
        )

    def test_gather_does_not_populate_legacy_cost_fields(self):
        summary = self.gather(
            self.usage_data(
                cost_marker=0.25,
                total_cost_marker=0.5,
            )
        )
        self.assertIsNone(summary["total_cost"])
        self.assertIsNone(summary["cost_usd"])
        self.assertEqual(
            summary["costs_by_currency"],
            {},
        )

    def test_gather_zero_cost_is_observed_not_unavailable(self):
        summary = self.gather(
            self.usage_data(
                cost_marker=0,
                total_cost_marker=0,
            )
        )
        observation = summary[
            "framework_reported_cost"
        ]
        self.assertEqual(observation["amount"], "0")
        self.assertEqual(
            observation["quality"],
            "reported_unverified_currency",
        )

    def test_gather_missing_cost_is_unavailable(self):
        summary = self.gather(self.usage_data())
        observation = summary["models"]["model-a"][
            "framework_reported_cost"
        ]
        self.assertIsNone(observation["amount"])
        self.assertEqual(
            observation["quality"],
            "unavailable",
        )

    def test_gather_invalid_cost_is_unavailable(self):
        summary = self.gather(
            self.usage_data(cost_marker="not-a-number")
        )
        observation = summary["models"]["model-a"][
            "framework_reported_cost"
        ]
        self.assertIsNone(observation["amount"])

    def test_gather_negative_cost_is_unavailable(self):
        summary = self.gather(
            self.usage_data(cost_marker=-0.5)
        )
        observation = summary["models"]["model-a"][
            "framework_reported_cost"
        ]
        self.assertIsNone(observation["amount"])

    def test_gather_boolean_cost_is_unavailable(self):
        summary = self.gather(
            self.usage_data(cost_marker=True)
        )
        observation = summary["models"]["model-a"][
            "framework_reported_cost"
        ]
        self.assertIsNone(observation["amount"])

    def test_gather_sums_model_costs_when_total_missing(self):
        usage = {
            "usage_including_cached_inference": {
                "model-a": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": 0.1,
                },
                "model-b": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                    "cost": 0.2,
                },
            }
        }
        summary = self.gather(usage)
        observation = summary[
            "framework_reported_cost"
        ]
        self.assertEqual(observation["amount"], "0.3")
        self.assertTrue(observation["complete"])
        self.assertEqual(
            observation["source"],
            "autogen.gather_usage_summary:model_sum",
        )

    def test_gather_marks_partial_model_cost_sum_incomplete(self):
        usage = {
            "usage_including_cached_inference": {
                "model-a": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "cost": 0.1,
                },
                "model-b": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                },
            }
        }
        summary = self.gather(usage)
        observation = summary[
            "framework_reported_cost"
        ]
        self.assertEqual(observation["amount"], "0.1")
        self.assertFalse(observation["complete"])

    def test_gather_total_cost_is_authoritative_for_reported_observation(self):
        usage = {
            "usage_including_cached_inference": {
                "total_cost": 0.9,
                "model-a": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "cost": 0.1,
                },
                "model-b": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                },
            }
        }
        summary = self.gather(usage)
        observation = summary[
            "framework_reported_cost"
        ]
        self.assertEqual(observation["amount"], "0.9")
        self.assertTrue(observation["complete"])
        self.assertEqual(
            observation["source"],
            "autogen.gather_usage_summary:aggregate",
        )

    def test_gather_model_observation_schema_is_stable(self):
        summary = self.gather(
            self.usage_data(cost_marker=0.25)
        )
        observation = summary["models"]["model-a"][
            "framework_reported_cost"
        ]
        self.assertEqual(
            set(observation),
            {
                "kind",
                "amount",
                "currency",
                "quality",
                "source",
                "ledger_eligible",
                "complete",
                "assumptions",
            },
        )

    def test_gather_summary_is_json_serializable(self):
        summary = self.gather(
            self.usage_data(
                cost_marker=0.25,
                total_cost_marker=0.5,
            )
        )
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
        )

    def test_fallback_preserves_tokens(self):
        summary = self.fallback(
            UsageAgent(
                actual={
                    "model-a": {
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_tokens": 20,
                    }
                }
            )
        )
        self.assertEqual(summary["total_tokens"], 20)
        self.assertEqual(
            summary["models"]["model-a"][
                "prompt_tokens"
            ],
            12,
        )

    def test_fallback_quarantines_cost(self):
        summary = self.fallback(
            UsageAgent(
                actual={
                    "model-a": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "cost": 0.25,
                    }
                }
            )
        )
        model = summary["models"]["model-a"]
        self.assertIsNone(model["cost"])
        self.assertEqual(
            model["framework_reported_cost"]["amount"],
            "0.25",
        )
        self.assertEqual(
            summary["costs_by_currency"],
            {},
        )

    def test_fallback_uses_actual_usage_before_total_usage(self):
        agent = UsageAgent(
            actual={
                "model-a": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                }
            },
            total={
                "model-a": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                }
            },
        )
        summary = self.fallback(agent)
        self.assertEqual(summary["total_tokens"], 15)
        self.assertEqual(agent.actual_calls, 1)
        self.assertEqual(agent.total_calls, 0)

    def test_fallback_uses_total_usage_when_actual_missing(self):
        agent = UsageAgent(
            actual=None,
            total={
                "model-a": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                }
            },
        )
        summary = self.fallback(agent)
        self.assertEqual(summary["total_tokens"], 25)
        self.assertEqual(agent.actual_calls, 1)
        self.assertEqual(agent.total_calls, 1)

    def test_fallback_sums_same_model_across_agents(self):
        summary = self.fallback(
            UsageAgent(
                actual={
                    "model-a": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "cost": 0.1,
                    }
                }
            ),
            UsageAgent(
                actual={
                    "model-a": {
                        "prompt_tokens": 20,
                        "completion_tokens": 5,
                        "cost": 0.2,
                    }
                }
            ),
        )
        model = summary["models"]["model-a"]
        self.assertEqual(model["total_tokens"], 40)
        self.assertEqual(
            model["framework_reported_cost"]["amount"],
            "0.3",
        )

    def test_fallback_marks_missing_cost_incomplete(self):
        summary = self.fallback(
            UsageAgent(
                actual={
                    "model-a": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "cost": 0.1,
                    }
                }
            ),
            UsageAgent(
                actual={
                    "model-a": {
                        "prompt_tokens": 20,
                        "completion_tokens": 5,
                    }
                }
            ),
        )
        observation = summary["models"]["model-a"][
            "framework_reported_cost"
        ]
        self.assertFalse(observation["complete"])

    def test_fallback_source_records_gather_failure(self):
        summary = self.fallback(
            UsageAgent(actual={}),
            error=ValueError("bad summary"),
        )
        self.assertIn(
            "fallback_per_agent: ValueError: bad summary",
            summary["source"],
        )

    def test_fallback_ignores_invalid_usage_mapping(self):
        summary = self.fallback(
            UsageAgent(actual="invalid", total=None)
        )
        self.assertEqual(summary["models"], {})
        self.assertEqual(summary["total_tokens"], 0)

    def test_printer_reports_unavailable_without_dollar(self):
        summary = (
            base_agent_module
            .get_agrefactorpp_usage_summary()
        )
        stream = io.StringIO()
        with (
            patch.object(
                base_agent_module,
                "get_agrefactorpp_usage_summary",
                return_value=summary,
            ),
            redirect_stdout(stream),
        ):
            base_agent_module.print_agrefactorpp_usage_summary()
        output = stream.getvalue()
        self.assertIn("Cost: unavailable", output)
        self.assertNotIn("$", output)

    def test_printer_reports_currency_unspecified_without_dollar(self):
        summary = self.gather(
            self.usage_data(
                cost_marker=0.25,
                total_cost_marker=0.5,
            )
        )
        stream = io.StringIO()
        with (
            patch.object(
                base_agent_module,
                "get_agrefactorpp_usage_summary",
                return_value=summary,
            ),
            redirect_stdout(stream),
        ):
            base_agent_module.print_agrefactorpp_usage_summary()
        output = stream.getvalue()
        self.assertIn(
            "Framework-reported cost: 0.5 "
            "(currency unspecified; audit only)",
            output,
        )
        self.assertNotIn("$", output)

    def test_printer_reports_per_model_observation(self):
        summary = self.gather(
            self.usage_data(cost_marker=0.25)
        )
        stream = io.StringIO()
        with (
            patch.object(
                base_agent_module,
                "get_agrefactorpp_usage_summary",
                return_value=summary,
            ),
            redirect_stdout(stream),
        ):
            base_agent_module.print_agrefactorpp_usage_summary()
        output = stream.getvalue()
        self.assertIn("--- model-a ---", output)
        self.assertIn(
            "Framework-reported cost: 0.25",
            output,
        )

    def test_source_has_no_hardcoded_price_helper(self):
        source = inspect.getsource(base_agent_module)
        self.assertNotIn(
            "def _agrefactorpp_price_per_1k(",
            source,
        )
        self.assertNotIn(
            "_agrefactorpp_price_per_1k(",
            source,
        )

    def test_source_has_no_deepseek_price_literals(self):
        source = inspect.getsource(base_agent_module)
        self.assertNotIn("0.000435", source)
        self.assertNotIn("0.00087", source)
        self.assertNotIn("0.00014", source)
        self.assertNotIn("0.00028", source)

    def test_source_has_no_dollar_cost_format(self):
        source = inspect.getsource(
            base_agent_module
            .print_agrefactorpp_usage_summary
        )
        self.assertNotIn("$", source)

    def test_source_has_typed_provenance_keys(self):
        source = inspect.getsource(
            base_agent_module
        )
        for key in (
            "framework_reported_cost",
            "estimated_cost",
            "costs_by_currency",
            "cost_usd",
            "cost_complete",
        ):
            self.assertIn(key, source)
        helper_source = inspect.getsource(
            base_agent_module
            ._agrefactorpp_empty_usage_summary
        )
        for key in (
            "framework_reported_cost",
            "estimated_cost",
            "costs_by_currency",
            "cost_usd",
            "cost_complete",
        ):
            self.assertIn(key, helper_source)

    def test_registered_agent_count_preserved(self):
        first = UsageAgent()
        second = UsageAgent()
        self.register(first, second)
        with patch(
            "autogen.gather_usage_summary",
            return_value={
                "usage_including_cached_inference": {}
            },
            create=True,
        ):
            summary = (
                base_agent_module
                .get_agrefactorpp_usage_summary()
            )
        self.assertEqual(summary["agents"], 2)

    def test_registry_deduplicates_same_agent(self):
        agent = UsageAgent()
        self.register(agent, agent)
        with patch(
            "autogen.gather_usage_summary",
            return_value={
                "usage_including_cached_inference": {}
            },
            create=True,
        ):
            summary = (
                base_agent_module
                .get_agrefactorpp_usage_summary()
            )
        self.assertEqual(summary["agents"], 1)

    def test_framework_reported_cost_never_ledger_eligible(self):
        summary = self.gather(
            self.usage_data(
                cost_marker=0.25,
                total_cost_marker=0.5,
            )
        )
        self.assertFalse(
            summary["framework_reported_cost"][
                "ledger_eligible"
            ]
        )
        self.assertFalse(summary["cost_complete"])

    def test_estimated_cost_remains_none_in_c3c1(self):
        summary = self.gather(
            self.usage_data(cost_marker=0.25)
        )
        self.assertIsNone(summary["estimated_cost"])
        self.assertIsNone(
            summary["models"]["model-a"][
                "estimated_cost"
            ]
        )


if __name__ == "__main__":
    unittest.main()
