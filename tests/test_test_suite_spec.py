import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TestSuiteSpec,
)


class EvaluationSplitTests(unittest.TestCase):
    def test_public_feedback_is_visible(self) -> None:
        self.assertTrue(
            EvaluationSplit.PUBLIC.feedback_visible_to_agent
        )

    def test_hidden_feedback_is_not_visible(self) -> None:
        self.assertFalse(
            EvaluationSplit.HIDDEN.feedback_visible_to_agent
        )


class TestSuiteSpecTests(unittest.TestCase):
    def test_create_public_suite(self) -> None:
        suite = TestSuiteSpec(
            suite_id="array-map-public",
            suite_version="1",
            split=EvaluationSplit.PUBLIC,
            case_count=8,
            testbench_path="tests/array_map_public.cpp",
        )

        self.assertEqual(suite.split, EvaluationSplit.PUBLIC)
        self.assertTrue(suite.feedback_visible_to_agent)
        self.assertEqual(suite.case_count, 8)

    def test_create_hidden_suite(self) -> None:
        suite = TestSuiteSpec(
            suite_id="reduction-hidden",
            split=EvaluationSplit.HIDDEN,
        )

        self.assertEqual(suite.split, EvaluationSplit.HIDDEN)
        self.assertFalse(suite.feedback_visible_to_agent)

    def test_accept_split_string(self) -> None:
        suite = TestSuiteSpec(
            suite_id="stencil-public",
            split="public",
        )

        self.assertEqual(suite.split, EvaluationSplit.PUBLIC)

    def test_defaults_to_public_without_execution_changes(self) -> None:
        suite = TestSuiteSpec(suite_id="legacy-default")

        self.assertEqual(suite.split, EvaluationSplit.PUBLIC)
        self.assertTrue(suite.feedback_visible_to_agent)

    def test_cleans_text_fields(self) -> None:
        suite = TestSuiteSpec(
            suite_id="  stream-public  ",
            suite_version="  v1  ",
            testbench_path="  tests/stream.cpp  ",
        )

        self.assertEqual(suite.suite_id, "stream-public")
        self.assertEqual(suite.suite_version, "v1")
        self.assertEqual(
            suite.testbench_path,
            "tests/stream.cpp",
        )

    def test_blank_optional_text_becomes_none(self) -> None:
        suite = TestSuiteSpec(
            suite_id="stateful-public",
            suite_version="   ",
            testbench_path="   ",
        )

        self.assertIsNone(suite.suite_version)
        self.assertIsNone(suite.testbench_path)

    def test_round_trip_dict(self) -> None:
        original = TestSuiteSpec(
            suite_id="multi-output-hidden",
            suite_version="2026-07",
            split=EvaluationSplit.HIDDEN,
            case_count=20,
            testbench_path="tests/multi_output_hidden.cpp",
        )

        restored = TestSuiteSpec.from_dict(original.to_dict())

        self.assertEqual(restored, original)

    def test_to_dict_is_json_serializable(self) -> None:
        suite = TestSuiteSpec(
            suite_id="ap-int-public",
            split=EvaluationSplit.PUBLIC,
            case_count=6,
        )

        encoded = json.dumps(suite.to_dict())

        self.assertIn('"split": "public"', encoded)
        self.assertIn(
            '"feedback_visible_to_agent": true',
            encoded,
        )

    def test_reject_empty_suite_id(self) -> None:
        with self.assertRaises(ValueError):
            TestSuiteSpec(suite_id="   ")

    def test_reject_unknown_split(self) -> None:
        with self.assertRaises(ValueError):
            TestSuiteSpec(
                suite_id="invalid",
                split="training",
            )

    def test_reject_non_integer_case_count(self) -> None:
        with self.assertRaises(TypeError):
            TestSuiteSpec(
                suite_id="invalid",
                case_count=2.5,
            )

    def test_reject_boolean_case_count(self) -> None:
        with self.assertRaises(TypeError):
            TestSuiteSpec(
                suite_id="invalid",
                case_count=True,
            )

    def test_reject_non_positive_case_count(self) -> None:
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TestSuiteSpec(
                        suite_id="invalid",
                        case_count=value,
                    )

    def test_reject_unknown_mapping_field(self) -> None:
        with self.assertRaises(ValueError):
            TestSuiteSpec.from_dict(
                {
                    "suite_id": "invalid",
                    "kernel_family": "dfs",
                }
            )

    def test_reject_hidden_visible_conflict(self) -> None:
        with self.assertRaises(ValueError):
            TestSuiteSpec.from_dict(
                {
                    "suite_id": "hidden",
                    "split": "hidden",
                    "feedback_visible_to_agent": True,
                }
            )

    def test_reject_public_hidden_conflict(self) -> None:
        with self.assertRaises(ValueError):
            TestSuiteSpec.from_dict(
                {
                    "suite_id": "public",
                    "split": "public",
                    "feedback_visible_to_agent": False,
                }
            )

    def test_reject_non_boolean_visibility(self) -> None:
        with self.assertRaises(TypeError):
            TestSuiteSpec.from_dict(
                {
                    "suite_id": "public",
                    "feedback_visible_to_agent": "yes",
                }
            )

    def test_schema_is_kernel_agnostic(self) -> None:
        suites = [
            TestSuiteSpec(suite_id="array-map"),
            TestSuiteSpec(suite_id="reduction"),
            TestSuiteSpec(suite_id="stencil"),
            TestSuiteSpec(suite_id="stream"),
            TestSuiteSpec(suite_id="stateful"),
        ]

        self.assertEqual(len(suites), 5)
        self.assertTrue(
            all(
                suite.split is EvaluationSplit.PUBLIC
                for suite in suites
            )
        )


if __name__ == "__main__":
    unittest.main()
