import json
import unittest

from agrefactor.evaluation import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
    FeedbackRouter,
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
    suffix: str,
    *,
    stage=FeedbackStage.CSYNTH,
    category=FeedbackCategory.UNKNOWN,
    severity=FeedbackSeverity.ERROR,
    owner=FeedbackOwner.UNKNOWN,
) -> FeedbackItem:
    return FeedbackItem(
        feedback_id=f"report.{suffix}",
        stage=stage,
        category=category,
        severity=severity,
        owner=owner,
        summary=f"feedback {suffix}",
        detail="safe diagnostic",
        source="test",
    )


def report(
    *items: FeedbackItem,
    view="agent_safe",
) -> FeedbackReport:
    return FeedbackReport(
        report_id="report",
        source="combined",
        items=items,
        metadata={"evidence_view": view},
    )


class FeedbackRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = FeedbackRouter()

    def route(
        self,
        source: FeedbackReport,
    ) -> FeedbackRouteDecision:
        return self.router.route(
            source,
            decision_id="decision",
        )

    def test_empty_report_continues_validation(self) -> None:
        decision = self.route(report())

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            decision.blocking_feedback_ids,
            (),
        )

    def test_warning_only_continues_validation(self) -> None:
        warning = item(
            "warning",
            category=FeedbackCategory.PIPELINE_DEPENDENCY,
            severity=FeedbackSeverity.WARNING,
            owner=FeedbackOwner.CANDIDATE,
        )

        decision = self.route(report(warning))

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.CONTINUE_VALIDATION,
        )
        self.assertEqual(
            decision.advisory_feedback_ids,
            ("report.warning",),
        )

    def test_budget_exhaustion_stops(self) -> None:
        budget = item(
            "budget",
            stage=FeedbackStage.CONFIGURATION,
            category=FeedbackCategory.BUDGET_EXHAUSTED,
            owner=FeedbackOwner.EVALUATOR,
        )

        decision = self.route(report(budget))

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            decision.selected_feedback_ids,
            ("report.budget",),
        )

    def test_budget_stop_precedes_other_blockers(self) -> None:
        budget = item(
            "budget",
            stage=FeedbackStage.CONFIGURATION,
            category=FeedbackCategory.BUDGET_EXHAUSTED,
            owner=FeedbackOwner.EVALUATOR,
        )
        candidate = item(
            "candidate",
            category=FeedbackCategory.SYNTAX_ERROR,
            owner=FeedbackOwner.CANDIDATE,
        )

        decision = self.route(
            report(budget, candidate)
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            decision.selected_feedback_ids,
            ("report.budget",),
        )
        self.assertEqual(
            decision.metadata[
                "deferred_blocking_feedback_ids"
            ],
            ["report.candidate"],
        )

    def test_toolchain_owner_routes_to_toolchain(self) -> None:
        decision = self.route(
            report(
                item(
                    "tool",
                    stage=FeedbackStage.TOOLCHAIN,
                    category=(
                        FeedbackCategory.TOOLCHAIN_FAILURE
                    ),
                    severity=FeedbackSeverity.FATAL,
                    owner=FeedbackOwner.TOOLCHAIN,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.FIX_TOOLCHAIN,
        )

    def test_toolchain_category_fallback(self) -> None:
        decision = self.route(
            report(
                item(
                    "tool",
                    category=(
                        FeedbackCategory.TOOLCHAIN_FAILURE
                    ),
                    severity=FeedbackSeverity.FATAL,
                    owner=FeedbackOwner.UNKNOWN,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.FIX_TOOLCHAIN,
        )

    def test_toolchain_timeout_fallback(self) -> None:
        decision = self.route(
            report(
                item(
                    "timeout",
                    stage=FeedbackStage.TOOLCHAIN,
                    category=FeedbackCategory.TIMEOUT,
                    severity=FeedbackSeverity.FATAL,
                    owner=FeedbackOwner.UNKNOWN,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.FIX_TOOLCHAIN,
        )

    def test_configuration_owner_routes_configuration(
        self,
    ) -> None:
        decision = self.route(
            report(
                item(
                    "config",
                    category=(
                        FeedbackCategory.INVALID_CONFIGURATION
                    ),
                    owner=FeedbackOwner.CONFIGURATION,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.FIX_CONFIGURATION,
        )

    def test_candidate_owned_invalid_configuration_repairs_candidate(
        self,
    ) -> None:
        decision = self.route(
            report(
                item(
                    "candidate-config",
                    category=(
                        FeedbackCategory.INVALID_CONFIGURATION
                    ),
                    owner=FeedbackOwner.CANDIDATE,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )

    def test_task_input_owner_routes_task_input(self) -> None:
        decision = self.route(
            report(
                item(
                    "input",
                    stage=FeedbackStage.INPUT,
                    category=FeedbackCategory.INVALID_INPUT,
                    owner=FeedbackOwner.TASK_INPUT,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.FIX_TASK_INPUT,
        )

    def test_testbench_owner_routes_testbench(self) -> None:
        decision = self.route(
            report(
                item(
                    "testbench",
                    stage=FeedbackStage.COMPILE,
                    category=FeedbackCategory.UNDECLARED_TYPE,
                    owner=FeedbackOwner.TESTBENCH,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_TESTBENCH,
        )

    def test_candidate_owner_routes_candidate(self) -> None:
        decision = self.route(
            report(
                item(
                    "candidate",
                    category=FeedbackCategory.SYNTAX_ERROR,
                    owner=FeedbackOwner.CANDIDATE,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )

    def test_original_owner_routes_original(self) -> None:
        decision = self.route(
            report(
                item(
                    "original",
                    category=(
                        FeedbackCategory.UNSUPPORTED_CONSTRUCT
                    ),
                    owner=FeedbackOwner.ORIGINAL,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_ORIGINAL,
        )

    def test_unknown_error_requires_review(self) -> None:
        decision = self.route(
            report(
                item(
                    "unknown",
                    category=FeedbackCategory.UNKNOWN,
                    owner=FeedbackOwner.UNKNOWN,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REVIEW_UNKNOWN,
        )

    def test_known_category_unknown_owner_requires_review(
        self,
    ) -> None:
        decision = self.route(
            report(
                item(
                    "syntax",
                    category=FeedbackCategory.SYNTAX_ERROR,
                    owner=FeedbackOwner.UNKNOWN,
                )
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REVIEW_UNKNOWN,
        )

    def test_mixed_candidate_and_testbench_requires_review(
        self,
    ) -> None:
        decision = self.route(
            report(
                item(
                    "candidate",
                    category=FeedbackCategory.SYNTAX_ERROR,
                    owner=FeedbackOwner.CANDIDATE,
                ),
                item(
                    "testbench",
                    stage=FeedbackStage.COMPILE,
                    category=FeedbackCategory.UNDECLARED_TYPE,
                    owner=FeedbackOwner.TESTBENCH,
                ),
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REVIEW_MIXED,
        )
        self.assertEqual(
            set(decision.selected_feedback_ids),
            {
                "report.candidate",
                "report.testbench",
            },
        )

    def test_same_route_multiple_items_is_not_mixed(self) -> None:
        decision = self.route(
            report(
                item(
                    "syntax",
                    category=FeedbackCategory.SYNTAX_ERROR,
                    owner=FeedbackOwner.CANDIDATE,
                ),
                item(
                    "symbol",
                    category=FeedbackCategory.UNDECLARED_SYMBOL,
                    owner=FeedbackOwner.CANDIDATE,
                ),
            )
        )

        self.assertEqual(
            decision.action,
            FeedbackRouteAction.REPAIR_CANDIDATE,
        )
        self.assertEqual(
            set(decision.selected_feedback_ids),
            {
                "report.syntax",
                "report.symbol",
            },
        )

    def test_router_uses_no_detail_or_source_evidence(
        self,
    ) -> None:
        secret = "/private/hidden/path"
        source = FeedbackReport(
            report_id="report",
            source="csynth",
            items=(
                FeedbackItem(
                    feedback_id="report.unknown",
                    stage=FeedbackStage.CSYNTH,
                    category=FeedbackCategory.UNKNOWN,
                    severity=FeedbackSeverity.ERROR,
                    owner=FeedbackOwner.UNKNOWN,
                    summary="unknown failure",
                    detail=f"failure at {secret}",
                    source="csynth_diagnostic",
                ),
            ),
            source_evidence={
                "secret": secret,
            },
            metadata={
                "evidence_view": "operator_full",
                "secret": secret,
            },
        )

        decision = self.route(source)
        serialized = json.dumps(
            decision.to_dict(),
            sort_keys=True,
        )

        self.assertNotIn(secret, serialized)
        self.assertNotIn("source_evidence", serialized)

    def test_decision_round_trip(self) -> None:
        original = self.route(
            report(
                item(
                    "candidate",
                    category=FeedbackCategory.SYNTAX_ERROR,
                    owner=FeedbackOwner.CANDIDATE,
                )
            )
        )
        restored = FeedbackRouteDecision.from_dict(
            original.to_dict()
        )

        self.assertEqual(restored, original)

    def test_decision_rejects_unknown_field(self) -> None:
        payload = self.route(report()).to_dict()
        payload["unexpected"] = True

        with self.assertRaises(ValueError):
            FeedbackRouteDecision.from_dict(payload)

    def test_decision_rejects_selected_not_blocking(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            FeedbackRouteDecision(
                decision_id="decision",
                action=FeedbackRouteAction.REVIEW_UNKNOWN,
                reason="reason",
                source_report_id="report",
                blocking_feedback_ids=("report.a",),
                selected_feedback_ids=("report.b",),
            )

    def test_rejects_non_report(self) -> None:
        with self.assertRaises(TypeError):
            self.router.route(
                {"items": []},
                decision_id="decision",
            )

    def test_rejects_empty_decision_id(self) -> None:
        with self.assertRaises(ValueError):
            self.router.route(
                report(),
                decision_id=" ",
            )

    def test_router_is_source_and_kernel_agnostic(
        self,
    ) -> None:
        sources = (
            "preflight",
            "test_evaluation",
            "csynth",
            "future_evaluator",
        )
        decisions = []

        for index, source_name in enumerate(
            sources,
            start=1,
        ):
            source = FeedbackReport(
                report_id=f"report-{index}",
                source=source_name,
                items=(
                    FeedbackItem(
                        feedback_id=f"report-{index}.item",
                        stage=FeedbackStage.CSYNTH,
                        category=(
                            FeedbackCategory.UNDECLARED_SYMBOL
                        ),
                        severity=FeedbackSeverity.ERROR,
                        owner=FeedbackOwner.CANDIDATE,
                        summary="undeclared identifier",
                    ),
                ),
                metadata={
                    "evidence_view": "agent_safe",
                },
            )
            decisions.append(
                self.router.route(
                    source,
                    decision_id=f"decision-{index}",
                )
            )

        self.assertTrue(
            all(
                decision.action
                is FeedbackRouteAction.REPAIR_CANDIDATE
                for decision in decisions
            )
        )


if __name__ == "__main__":
    unittest.main()
