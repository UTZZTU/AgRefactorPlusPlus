from __future__ import annotations

from hashlib import sha256
import unittest

from agrefactor.product.refactor_eligibility import (
    EligibilityStatus,
    OriginalCsynthEvidence,
    analyze_source_boundary,
    assess_refactor_eligibility,
)


EXPLICIT_IO = r'''
extern "C" int top(int x, int *output) {
    output[0] = x + 1;
    return 0;
}
'''

PRIVATE_GLOBAL = r'''
static int state = 0;
extern "C" int top(int x) {
    state += x;
    return state;
}
'''

HELPER_GLOBAL = r'''
static int state = 0;
int helper(int x) {
    state += x;
    return state;
}
extern "C" int top(int x) {
    return helper(x);
}
'''

CONST_GLOBAL = r'''
static const int scale = 4;
extern "C" int top(int x) {
    return x * scale;
}
'''


def csynth_evidence(
    source: str,
    *,
    top: str = "top",
    status: str = "passed",
    complete: bool = True,
    source_sha256: str | None = None,
) -> OriginalCsynthEvidence:
    return OriginalCsynthEvidence(
        source_sha256=(
            source_sha256
            or sha256(source.encode("utf-8")).hexdigest()
        ),
        top_function=top,
        status=status,
        tool_launched=True,
        csynth_launched=True,
        returncode=0 if status == "passed" else 1,
        timed_out=False,
        evidence_sha256=("e" * 64 if complete else None),
        evidence_ref="tests/original_csynth.json",
    )


class R5CEligibilityTests(unittest.TestCase):
    def test_explicit_io_auto_is_execution_allowed(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="top",
            public_test_mode="auto",
        )
        self.assertEqual(
            report.execution_status,
            EligibilityStatus.ALLOWED,
        )
        self.assertTrue(report.execution_allowed)
        self.assertEqual(
            report.primary_sample_status,
            EligibilityStatus.NOT_EVALUATED,
        )

    def test_authoritative_csynth_promotes_complete_explicit_io_primary(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(EXPLICIT_IO),
        )
        self.assertTrue(report.primary_sample_eligible)
        self.assertIn("primary_sample_eligible", report.reason_codes)
        self.assertTrue(report.original_csynth_passed)

    def test_bare_boolean_cannot_promote_primary_sample(self):
        with self.assertRaises(TypeError):
            assess_refactor_eligibility(
                source_code=EXPLICIT_IO,
                top_function="top",
                public_test_mode="auto",
                original_csynth_evidence=True,
            )

    def test_csynth_identity_mismatch_requires_review(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(
                EXPLICIT_IO,
                source_sha256="a" * 64,
            ),
        )
        self.assertEqual(
            report.primary_sample_status,
            EligibilityStatus.REVIEW_REQUIRED,
        )
        self.assertIn(
            "original_csynth_identity_mismatch",
            report.reason_codes,
        )

    def test_claimed_pass_without_immutable_evidence_requires_review(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(
                EXPLICIT_IO,
                complete=False,
            ),
        )
        self.assertEqual(
            report.primary_sample_status,
            EligibilityStatus.REVIEW_REQUIRED,
        )
        self.assertIn(
            "original_csynth_evidence_incomplete",
            report.reason_codes,
        )

    def test_evidence_round_trip_recomputes_authority(self):
        evidence = csynth_evidence(EXPLICIT_IO)
        restored = OriginalCsynthEvidence.from_dict(evidence.to_dict())
        self.assertTrue(restored.authoritative_pass)
        self.assertEqual(restored.source_sha256, evidence.source_sha256)

    def test_original_csynth_alone_does_not_override_private_global(self):
        report = assess_refactor_eligibility(
            source_code=PRIVATE_GLOBAL,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(PRIVATE_GLOBAL),
        )
        self.assertEqual(
            report.execution_status,
            EligibilityStatus.REJECTED,
        )
        self.assertFalse(report.primary_sample_eligible)
        self.assertIn(
            "original_csynth_alone_not_sufficient",
            report.reason_codes,
        )
        self.assertEqual(
            report.boundary.private_global_dependencies,
            ("state",),
        )

    def test_reachable_helper_private_global_is_detected(self):
        report = assess_refactor_eligibility(
            source_code=HELPER_GLOBAL,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(HELPER_GLOBAL),
        )
        self.assertEqual(
            report.boundary.reachable_functions,
            ("helper", "top"),
        )
        self.assertEqual(
            report.boundary.private_global_dependencies,
            ("state",),
        )
        self.assertFalse(report.execution_allowed)

    def test_const_file_scope_value_is_not_mutable_state(self):
        report = assess_refactor_eligibility(
            source_code=CONST_GLOBAL,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(CONST_GLOBAL),
        )
        self.assertTrue(report.execution_allowed)
        self.assertEqual(
            report.boundary.mutable_file_scope_objects,
            (),
        )

    def test_provided_public_tests_allow_stateful_execution_boundary(self):
        report = assess_refactor_eligibility(
            source_code=PRIVATE_GLOBAL,
            top_function="top",
            public_test_mode="provided",
            original_csynth_evidence=csynth_evidence(PRIVATE_GLOBAL),
        )
        self.assertTrue(report.execution_allowed)
        self.assertTrue(report.primary_sample_eligible)
        self.assertIn(
            "operator_provided_public_tests",
            report.reason_codes,
        )

    def test_original_csynth_failure_blocks_primary_sample(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(
                EXPLICIT_IO,
                status="failed",
            ),
        )
        self.assertTrue(report.execution_allowed)
        self.assertEqual(
            report.primary_sample_status,
            EligibilityStatus.REJECTED,
        )
        self.assertIn("original_csynth_failed", report.reason_codes)

    def test_missing_top_is_review_required_for_auto(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="other_top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(
                EXPLICIT_IO,
                top="other_top",
            ),
        )
        self.assertEqual(
            report.execution_status,
            EligibilityStatus.REVIEW_REQUIRED,
        )
        self.assertFalse(report.boundary.top_function_found)
        self.assertIn(
            "top_function_definition_not_found",
            report.boundary.ambiguity_codes,
        )

    def test_public_none_is_rejected(self):
        report = assess_refactor_eligibility(
            source_code=EXPLICIT_IO,
            top_function="top",
            public_test_mode="none",
        )
        self.assertEqual(
            report.execution_status,
            EligibilityStatus.REJECTED,
        )
        self.assertIn(
            "public_test_source_required",
            report.reason_codes,
        )

    def test_local_state_is_not_file_scope_state(self):
        source = r'''
extern "C" int top(int x) {
    int state = x;
    return state + 1;
}
'''
        evidence = analyze_source_boundary(
            source_code=source,
            top_function="top",
        )
        self.assertEqual(evidence.mutable_file_scope_objects, ())
        self.assertEqual(evidence.private_global_dependencies, ())
        self.assertTrue(evidence.analysis_complete)

    def test_extern_c_block_is_transparent(self):
        source = r'''
extern "C" {
int top(int x) {
    return x + 1;
}
}
'''
        evidence = analyze_source_boundary(
            source_code=source,
            top_function="top",
        )
        self.assertTrue(evidence.top_function_found)
        self.assertTrue(evidence.analysis_complete)

    def test_macro_boundary_is_review_required(self):
        source = r'''
#define DECLARE_STATE(name) static int name = 0
DECLARE_STATE(state);
extern "C" int top(int x) {
    return state + x;
}
'''
        report = assess_refactor_eligibility(
            source_code=source,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(source),
        )
        self.assertEqual(
            report.execution_status,
            EligibilityStatus.REVIEW_REQUIRED,
        )
        self.assertIn(
            "preprocessor_boundary_semantics:define",
            report.boundary.ambiguity_codes,
        )

    def test_extern_c_block_mutable_global_is_detected(self):
        source = r'''
extern "C" {
static int state = 0;
int top(int x) {
    state += x;
    return state;
}
}
'''
        report = assess_refactor_eligibility(
            source_code=source,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(source),
        )
        self.assertEqual(
            report.boundary.private_global_dependencies,
            ("state",),
        )
        self.assertFalse(report.execution_allowed)

    def test_file_scope_function_pointer_is_unknown_safe(self):
        source = r'''
static int (*state_callback)(int);
extern "C" int top(int x) {
    return state_callback(x);
}
'''
        report = assess_refactor_eligibility(
            source_code=source,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(source),
        )
        self.assertEqual(
            report.execution_status,
            EligibilityStatus.REVIEW_REQUIRED,
        )
        self.assertIn(
            "unresolved_file_scope_callable_or_pointer_declaration",
            report.boundary.ambiguity_codes,
        )

    def test_rejection_is_agent_safe_and_no_provider_call(self):
        report = assess_refactor_eligibility(
            source_code=PRIVATE_GLOBAL,
            top_function="top",
            public_test_mode="auto",
            original_csynth_evidence=csynth_evidence(PRIVATE_GLOBAL),
        )
        rejection = report.to_rejection()
        self.assertFalse(rejection["provider_call_observed"])
        self.assertFalse(rejection["credential_value_persisted"])
        self.assertFalse(rejection["hidden_evidence_exposed"])
        self.assertNotIn(PRIVATE_GLOBAL, str(rejection))


if __name__ == "__main__":
    unittest.main()
