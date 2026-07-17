import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import (
    TestEvaluationFeedbackComposer,
    ValidationFeedbackCoordinator,
    ValidationState,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


def item(
    feedback_id,
    *,
    owner=FeedbackOwner.CANDIDATE,
    category=FeedbackCategory.FUNCTIONAL_MISMATCH,
    severity=FeedbackSeverity.ERROR,
    detail="safe detail",
):
    return FeedbackItem(
        feedback_id=feedback_id,
        stage=FeedbackStage.CSIM,
        category=category,
        severity=severity,
        owner=owner,
        summary="suite failure",
        detail=detail,
        source="test_evaluation",
        evidence_ref=None,
        metadata={"original_key": "preserved"},
    )


def report(
    suite_id,
    *,
    split=EvaluationSplit.PUBLIC,
    report_id=None,
    items=(),
    source="test_evaluation",
    secret=None,
):
    view = (
        "agent_safe"
        if split is EvaluationSplit.PUBLIC
        else "operator_full"
    )
    visible = (
        split is EvaluationSplit.PUBLIC
    )
    return FeedbackReport(
        report_id=(
            report_id or f"{suite_id}.report"
        ),
        source=source,
        items=tuple(items),
        source_evidence={
            "suite_id": suite_id,
            "secret": secret,
        },
        metadata={
            "evidence_view": view,
            "suite_id": suite_id,
            "evaluation_split": split.value,
            "feedback_visible_to_agent": visible,
        },
    )


class TestEvaluationFeedbackComposerTests(
    unittest.TestCase
):
    def setUp(self):
        self.composer = (
            TestEvaluationFeedbackComposer()
        )

    def test_public_reports_preserve_order(self):
        first = report(
            "public-a",
            items=(
                item("a.1"),
                item(
                    "a.2",
                    severity=FeedbackSeverity.WARNING,
                ),
            ),
        )
        second = report(
            "public-b",
            items=(item("b.1"),),
        )

        result = self.composer.compose(
            reports=(first, second),
            report_id="public.composed",
            split=EvaluationSplit.PUBLIC,
        )

        self.assertEqual(
            result.metadata["suite_ids"],
            ["public-a", "public-b"],
        )
        self.assertEqual(
            [entry.feedback_id for entry in result.items],
            [
                "public.composed.suite.1.item.1",
                "public.composed.suite.1.item.2",
                "public.composed.suite.2.item.1",
            ],
        )
        self.assertEqual(
            result.items[0].metadata[
                "component_feedback_id"
            ],
            "a.1",
        )
        self.assertEqual(
            result.items[2].metadata["suite_id"],
            "public-b",
        )
        self.assertTrue(
            result.metadata[
                "component_order_preserved"
            ]
        )

    def test_public_candidate_failure_routes_repair(self):
        task = TaskSpec(
            task_id="public-compose",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=(
                TestSuiteSpec(
                    suite_id="public-a",
                    split=EvaluationSplit.PUBLIC,
                ),
                TestSuiteSpec(
                    suite_id="public-b",
                    split=EvaluationSplit.PUBLIC,
                ),
            ),
        )
        composed = self.composer.compose(
            reports=(
                report("public-a"),
                report(
                    "public-b",
                    items=(item("public-b.item"),),
                ),
            ),
            report_id="public.composed",
            split=EvaluationSplit.PUBLIC,
        )

        coordinated = ValidationFeedbackCoordinator(
            task
        ).coordinate(
            composed,
            ValidationState.PUBLIC_EVALUATION,
            coordination_id="public-step",
        )

        self.assertEqual(
            coordinated.route_action.value,
            "repair_candidate",
        )
        self.assertEqual(
            coordinated.transition.next_state,
            ValidationState.REPAIR_PENDING,
        )
        self.assertEqual(
            len(
                coordinated.selected_feedback_items
            ),
            1,
        )

    def test_hidden_composition_is_operator_only(self):
        secret = "HIDDEN_COMPOSER_SECRET"
        task = TaskSpec(
            task_id="hidden-compose",
            kernel_path="kernel.cpp",
            kernel_name="top",
            test_suites=(
                TestSuiteSpec(
                    suite_id="hidden-a",
                    split=EvaluationSplit.HIDDEN,
                ),
                TestSuiteSpec(
                    suite_id="hidden-b",
                    split=EvaluationSplit.HIDDEN,
                ),
            ),
        )
        composed = self.composer.compose(
            reports=(
                report(
                    "hidden-a",
                    split=EvaluationSplit.HIDDEN,
                    secret=secret,
                ),
                report(
                    "hidden-b",
                    split=EvaluationSplit.HIDDEN,
                    items=(
                        item(
                            "hidden-b.item",
                            detail=secret,
                        ),
                    ),
                    secret=secret,
                ),
            ),
            report_id="hidden.composed",
            split=EvaluationSplit.HIDDEN,
        )

        self.assertEqual(
            composed.metadata["evidence_view"],
            "operator_full",
        )
        self.assertIn(
            secret,
            json.dumps(composed.to_dict()),
        )

        coordinated = ValidationFeedbackCoordinator(
            task
        ).coordinate(
            composed,
            ValidationState.HIDDEN_EVALUATION,
            coordination_id="hidden-step",
        )
        safe = json.dumps(
            coordinated.to_dict(),
            sort_keys=True,
        )

        self.assertEqual(
            coordinated.transition.next_state,
            ValidationState.REJECTED,
        )
        self.assertEqual(
            coordinated.source_report_id,
            "hidden-redacted",
        )
        self.assertEqual(
            coordinated.selected_feedback_items,
            (),
        )
        self.assertNotIn(secret, safe)
        self.assertNotIn(
            "hidden-b.item",
            safe,
        )

    def test_rejects_public_operator_report(self):
        component = report("public-a")
        bad = FeedbackReport(
            report_id=component.report_id,
            source=component.source,
            items=component.items,
            source_evidence=component.source_evidence,
            metadata={
                **component.metadata,
                "evidence_view": "operator_full",
            },
        )
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(bad,),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_rejects_hidden_agent_report(self):
        component = report(
            "hidden-a",
            split=EvaluationSplit.HIDDEN,
        )
        bad = FeedbackReport(
            report_id=component.report_id,
            source=component.source,
            items=component.items,
            source_evidence=component.source_evidence,
            metadata={
                **component.metadata,
                "evidence_view": "agent_safe",
            },
        )
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(bad,),
                report_id="composed",
                split=EvaluationSplit.HIDDEN,
            )

    def test_rejects_mixed_splits(self):
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(
                    report("public-a"),
                    report(
                        "hidden-a",
                        split=EvaluationSplit.HIDDEN,
                    ),
                ),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_rejects_visibility_conflict(self):
        component = report("public-a")
        bad = FeedbackReport(
            report_id=component.report_id,
            source=component.source,
            items=component.items,
            source_evidence=component.source_evidence,
            metadata={
                **component.metadata,
                "feedback_visible_to_agent": False,
            },
        )
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(bad,),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_rejects_duplicate_suite_ids(self):
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(
                    report(
                        "public-a",
                        report_id="report-a",
                    ),
                    report(
                        "public-a",
                        report_id="report-b",
                    ),
                ),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_rejects_duplicate_report_ids(self):
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(
                    report(
                        "public-a",
                        report_id="same",
                    ),
                    report(
                        "public-b",
                        report_id="same",
                    ),
                ),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_rejects_wrong_source(self):
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(
                    report(
                        "public-a",
                        source="csynth",
                    ),
                ),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_rejects_empty_and_nonsequence(self):
        with self.assertRaises(ValueError):
            self.composer.compose(
                reports=(),
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )
        with self.assertRaises(TypeError):
            self.composer.compose(
                reports={"not": "a sequence"},
                report_id="composed",
                split=EvaluationSplit.PUBLIC,
            )

    def test_counts_are_deterministic(self):
        result = self.composer.compose(
            reports=(
                report(
                    "public-a",
                    items=(
                        item("a.1"),
                        item(
                            "a.2",
                            owner=FeedbackOwner.TOOLCHAIN,
                            category=(
                                FeedbackCategory.TIMEOUT
                            ),
                            severity=(
                                FeedbackSeverity.FATAL
                            ),
                        ),
                    ),
                ),
                report("public-b"),
            ),
            report_id="composed",
            split="public",
        )

        self.assertEqual(
            result.metadata["suite_count"],
            2,
        )
        self.assertEqual(
            result.metadata["blocking_suite_ids"],
            ["public-a"],
        )
        self.assertEqual(
            result.metadata["composed_item_count"],
            2,
        )
        self.assertEqual(
            result.metadata["owner_counts"],
            {"candidate": 1, "toolchain": 1},
        )

    def test_component_reports_are_preserved(self):
        first = report("public-a")
        second = report("public-b")
        result = self.composer.compose(
            reports=(first, second),
            report_id="composed",
            split=EvaluationSplit.PUBLIC,
        )

        components = result.source_evidence[
            "component_reports"
        ]
        self.assertEqual(
            components,
            [first.to_dict(), second.to_dict()],
        )

    def test_does_not_mutate_components(self):
        component = report(
            "public-a",
            items=(item("a.1"),),
        )
        before = component.to_dict()

        self.composer.compose(
            reports=(component,),
            report_id="composed",
            split=EvaluationSplit.PUBLIC,
        )

        self.assertEqual(
            component.to_dict(),
            before,
        )

    def test_report_round_trip(self):
        result = self.composer.compose(
            reports=(report("public-a"),),
            report_id="composed",
            split=EvaluationSplit.PUBLIC,
        )

        restored = FeedbackReport.from_dict(
            result.to_dict()
        )
        self.assertEqual(restored, result)


if __name__ == "__main__":
    unittest.main()
