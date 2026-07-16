import unittest

from agrefactor.evaluation import (
    TestbenchPreflightFeedbackAdapter,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
    FeedbackSeverity,
    FeedbackStage,
    TestbenchDiagnostic,
    TestbenchFailureKind,
    TestbenchFailureOwner,
    TestbenchPreflightResult,
    TestbenchPreflightStatus,
    TestbenchStage,
)


class TestbenchPreflightFeedbackAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = TestbenchPreflightFeedbackAdapter()

    def make_result(
        self,
        *,
        status=TestbenchPreflightStatus.FAILED,
        stage=TestbenchStage.COMPILE_LINK,
        kind=TestbenchFailureKind.SYNTAX_ERROR,
        owner=TestbenchFailureOwner.CANDIDATE,
        diagnostics=(),
        stderr="",
        stdout="",
        return_code=1,
    ) -> TestbenchPreflightResult:
        return TestbenchPreflightResult(
            status=status,
            stage=stage,
            failure_kind=kind,
            failure_owner=owner,
            return_code=return_code,
            command=("g++", "testbench.cpp"),
            diagnostics=tuple(diagnostics),
            stdout=stdout,
            stderr=stderr,
            artifacts=("testbench.cpp", "result.json"),
            duration_s=0.25,
        )

    def test_passed_result_creates_empty_report(self) -> None:
        result = self.make_result(
            status=TestbenchPreflightStatus.PASSED,
            kind=TestbenchFailureKind.NONE,
            owner=TestbenchFailureOwner.NONE,
            return_code=0,
        )
        report = self.adapter.to_operator_report(
            result,
            report_id="preflight-pass",
        )
        self.assertEqual(report.items, ())
        self.assertFalse(report.blocking)
        self.assertIsNone(report.highest_severity)
        self.assertEqual(report.source_evidence, result.to_dict())
        self.assertEqual(
            report.metadata["next_action"],
            "continue_validation",
        )

    def test_static_forbidden_dependency_mapping(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=(
                TestbenchFailureKind
                .FORBIDDEN_INTERNAL_DEPENDENCY
            ),
            message="Private global dependency",
            file="testbench.cpp",
            line=8,
            raw="extern int private_state;",
        )
        result = self.make_result(
            stage=TestbenchStage.STATIC_CHECK,
            kind=(
                TestbenchFailureKind
                .FORBIDDEN_INTERNAL_DEPENDENCY
            ),
            owner=TestbenchFailureOwner.TESTBENCH,
            diagnostics=(diagnostic,),
            return_code=None,
        )
        item = self.adapter.to_operator_report(
            result,
            report_id="static",
        ).items[0]
        self.assertEqual(item.stage, FeedbackStage.STATIC_CHECK)
        self.assertEqual(
            item.category,
            FeedbackCategory.FORBIDDEN_DEPENDENCY,
        )
        self.assertEqual(item.owner, FeedbackOwner.TESTBENCH)
        self.assertEqual(item.severity, FeedbackSeverity.ERROR)

    def test_candidate_syntax_error_mapping(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.SYNTAX_ERROR,
            message="expected ';'",
            file="refactor_code.cpp",
            line=18,
            column=4,
            raw="refactor_code.cpp:18:4: error: expected ';'",
        )
        result = self.make_result(diagnostics=(diagnostic,))
        item = self.adapter.to_operator_report(
            result,
            report_id="syntax",
        ).items[0]
        self.assertEqual(item.stage, FeedbackStage.COMPILE)
        self.assertEqual(
            item.category,
            FeedbackCategory.SYNTAX_ERROR,
        )
        self.assertEqual(item.owner, FeedbackOwner.CANDIDATE)
        self.assertEqual(item.metadata["line"], 18)
        self.assertEqual(item.metadata["column"], 4)

    def test_linkage_mismatch_maps_to_link_stage(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.LINKAGE_MISMATCH,
            message="C/C++ linkage mismatch",
            file="testbench.cpp",
        )
        result = self.make_result(
            kind=TestbenchFailureKind.LINKAGE_MISMATCH,
            owner=TestbenchFailureOwner.TESTBENCH,
            diagnostics=(diagnostic,),
        )
        item = self.adapter.to_operator_report(
            result,
            report_id="linkage",
        ).items[0]
        self.assertEqual(item.stage, FeedbackStage.LINK)
        self.assertEqual(
            item.category,
            FeedbackCategory.LINKAGE_MISMATCH,
        )
        self.assertEqual(item.owner, FeedbackOwner.TESTBENCH)

    def test_compile_timeout_is_fatal_timeout(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.COMPILE_TIMEOUT,
            message="compile timed out",
        )
        result = self.make_result(
            status=TestbenchPreflightStatus.ERROR,
            kind=TestbenchFailureKind.COMPILE_TIMEOUT,
            owner=TestbenchFailureOwner.TOOLCHAIN,
            diagnostics=(diagnostic,),
            return_code=None,
        )
        item = self.adapter.to_operator_report(
            result,
            report_id="timeout",
        ).items[0]
        self.assertEqual(item.stage, FeedbackStage.COMPILE)
        self.assertEqual(item.category, FeedbackCategory.TIMEOUT)
        self.assertEqual(item.severity, FeedbackSeverity.FATAL)
        self.assertEqual(item.owner, FeedbackOwner.TOOLCHAIN)

    def test_compiler_not_found_maps_to_toolchain_failure(
        self,
    ) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.COMPILER_NOT_FOUND,
            message="compiler not found",
        )
        result = self.make_result(
            status=TestbenchPreflightStatus.ERROR,
            kind=TestbenchFailureKind.COMPILER_NOT_FOUND,
            owner=TestbenchFailureOwner.TOOLCHAIN,
            diagnostics=(diagnostic,),
            return_code=None,
        )
        item = self.adapter.to_operator_report(
            result,
            report_id="compiler",
        ).items[0]
        self.assertEqual(item.stage, FeedbackStage.COMPILE)
        self.assertEqual(
            item.category,
            FeedbackCategory.TOOLCHAIN_FAILURE,
        )

    def test_run_mismatch_maps_to_test_stage(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.OUTPUT_MISMATCH,
            message="candidate output mismatched",
            raw="expected=4 actual=5",
        )
        result = self.make_result(
            stage=TestbenchStage.RUN,
            kind=TestbenchFailureKind.OUTPUT_MISMATCH,
            diagnostics=(diagnostic,),
        )
        item = self.adapter.to_operator_report(
            result,
            report_id="runtime",
        ).items[0]
        self.assertEqual(item.stage, FeedbackStage.TEST)
        self.assertEqual(
            item.category,
            FeedbackCategory.FUNCTIONAL_MISMATCH,
        )

    def test_multiple_diagnostics_have_stable_unique_ids(
        self,
    ) -> None:
        diagnostics = (
            TestbenchDiagnostic(
                kind=TestbenchFailureKind.UNDECLARED_TYPE,
                message="unknown type A",
                file="testbench.cpp",
            ),
            TestbenchDiagnostic(
                kind=TestbenchFailureKind.UNDECLARED_SYMBOL,
                message="unknown symbol b",
                file="testbench.cpp",
            ),
        )
        result = self.make_result(
            kind=TestbenchFailureKind.UNDECLARED_TYPE,
            owner=TestbenchFailureOwner.TESTBENCH,
            diagnostics=diagnostics,
        )
        report = self.adapter.to_operator_report(
            result,
            report_id="multiple",
        )
        self.assertEqual(
            [item.feedback_id for item in report.items],
            [
                "multiple.diagnostic.1",
                "multiple.diagnostic.2",
            ],
        )
        self.assertEqual(
            [item.category for item in report.items],
            [
                FeedbackCategory.UNDECLARED_TYPE,
                FeedbackCategory.UNDECLARED_SYMBOL,
            ],
        )

    def test_missing_diagnostic_creates_fallback_item(self) -> None:
        result = self.make_result(
            kind=TestbenchFailureKind.UNKNOWN,
            owner=TestbenchFailureOwner.UNKNOWN,
            diagnostics=(),
            stderr="unstructured compiler output",
        )
        report = self.adapter.to_operator_report(
            result,
            report_id="fallback",
        )
        item = report.items[0]
        self.assertEqual(item.feedback_id, "fallback.result.1")
        self.assertEqual(item.category, FeedbackCategory.UNKNOWN)
        self.assertEqual(
            item.summary,
            "Testbench preflight failed: unknown",
        )
        self.assertEqual(
            item.detail,
            "unstructured compiler output",
        )
        self.assertTrue(item.metadata["fallback_item"])

    def test_complete_source_evidence_is_preserved(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.LINK_ERROR,
            message="undefined reference",
            raw="raw linker output",
        )
        result = self.make_result(
            kind=TestbenchFailureKind.LINK_ERROR,
            diagnostics=(diagnostic,),
            stdout="compiler stdout",
            stderr="compiler stderr",
        )
        report = self.adapter.to_operator_report(
            result,
            report_id="preserved",
        )
        self.assertEqual(report.source_evidence, result.to_dict())
        self.assertEqual(
            report.metadata["evidence_view"],
            "operator_full",
        )
        self.assertEqual(
            report.metadata["failure_kind"],
            "link_error",
        )

    def test_report_round_trip(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.UNDECLARED_SYMBOL,
            message="name was not declared",
        )
        result = self.make_result(
            kind=TestbenchFailureKind.UNDECLARED_SYMBOL,
            diagnostics=(diagnostic,),
        )
        original = self.adapter.to_operator_report(
            result,
            report_id="round-trip",
        )
        restored = type(original).from_dict(
            original.to_dict()
        )
        self.assertEqual(restored, original)

    def test_rejects_non_preflight_result(self) -> None:
        with self.assertRaises(TypeError):
            self.adapter.to_operator_report(
                {"status": "failed"},
                report_id="invalid",
            )

    def test_adapter_does_not_mutate_source_result(self) -> None:
        diagnostic = TestbenchDiagnostic(
            kind=TestbenchFailureKind.SYNTAX_ERROR,
            message="syntax error",
        )
        result = self.make_result(diagnostics=(diagnostic,))
        before = result.to_dict()
        self.adapter.to_operator_report(
            result,
            report_id="immutable",
        )
        self.assertEqual(result.to_dict(), before)

    def test_adapter_is_kernel_agnostic(self) -> None:
        families = (
            "array-map",
            "reduction",
            "stencil",
            "multi-output",
            "stream",
            "stateful",
        )
        result = self.make_result(
            status=TestbenchPreflightStatus.PASSED,
            kind=TestbenchFailureKind.NONE,
            owner=TestbenchFailureOwner.NONE,
            return_code=0,
        )
        reports = [
            self.adapter.to_operator_report(
                result,
                report_id=f"{family}-preflight",
            )
            for family in families
        ]
        self.assertEqual(len(reports), len(families))


if __name__ == "__main__":
    unittest.main()
