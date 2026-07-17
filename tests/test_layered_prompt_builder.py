import copy
import json
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.prompts import (
    LayeredPromptRequest,
    ModificationScope,
    PromptArtifact,
    PromptOutputContract,
    PromptPurpose,
    SharedLayeredPromptBuilder,
)


ORIGINAL = (
    'extern "C" int original_top(int x) '
    "{ return x + 1; }\n"
)
CANDIDATE = (
    'extern "C" int candidate_top(int x) '
    "{ return x; }\n"
)


def make_task():
    return TaskSpec(
        task_id="prompt-task",
        kernel_path="/private/work/candidate.cpp",
        kernel_name="candidate_top",
        testbench_path="/private/work/testbench.cpp",
        test_suites=(
            TestSuiteSpec(
                suite_id="public-a",
                split=EvaluationSplit.PUBLIC,
                testbench_path=(
                    "/private/work/public_tb.cpp"
                ),
            ),
        ),
    )


def make_feedback(
    *,
    owner=FeedbackOwner.CANDIDATE,
    stage=FeedbackStage.COMPILE,
    split=None,
    view="agent_safe",
    visible=None,
):
    metadata = {
        "evidence_view": view,
    }
    if split is not None:
        metadata["evaluation_split"] = split
    if visible is not None:
        metadata["feedback_visible_to_agent"] = visible

    return FeedbackReport(
        report_id="operator-path-report",
        source="synthetic",
        items=(
            FeedbackItem(
                feedback_id="item-1",
                stage=stage,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=owner,
                summary=(
                    "candidate compile failed at "
                    "/private/work/candidate.cpp"
                ),
                detail=(
                    "see C:\\secret\\candidate.cpp "
                    "and /private/log/build.log"
                ),
                source="compiler",
                evidence_ref="/private/evidence/raw.json",
                metadata={
                    "absolute_path": (
                        "/private/work/candidate.cpp"
                    ),
                    "secret": "operator-secret",
                },
            ),
        ),
        source_evidence={
            "raw_command": "g++ /private/work/candidate.cpp",
            "secret": "operator-secret",
        },
        metadata=metadata,
    )


def make_request(
    *,
    purpose=PromptPurpose.CANDIDATE_COMPILE_REPAIR,
    feedback=None,
    family_instruction=None,
    prior=(),
    memory=(),
):
    return LayeredPromptRequest(
        purpose=purpose,
        task=make_task(),
        feedback=feedback or make_feedback(),
        objective=(
            "Repair the candidate using only the "
            "structured compile evidence."
        ),
        artifacts=(
            PromptArtifact(
                name="candidate",
                content=CANDIDATE,
            ),
            PromptArtifact(
                name="original",
                content=ORIGINAL,
            ),
        ),
        modification_scope=ModificationScope(
            editable_artifacts=("candidate",),
            read_only_artifacts=("original",),
            forbidden_actions=(
                "Do not modify the original program.",
                "Do not weaken validation.",
            ),
        ),
        output_contract=PromptOutputContract(
            artifact_name="candidate",
            additional_requirements=(
                "Return exactly one complete C++ artifact.",
            ),
        ),
        attempt=1,
        max_attempts=3,
        family_instruction=family_instruction,
        prior_attempt_summaries=prior,
        approved_memory_snippets=memory,
    )


class SharedLayeredPromptBuilderTests(
    unittest.TestCase
):
    def test_builds_system_and_user_messages(self):
        result = SharedLayeredPromptBuilder().build(
            make_request()
        )

        self.assertEqual(
            [message.role for message in result.messages],
            ["system", "user"],
        )
        self.assertIn(
            "System invariants:",
            result.messages[0].content,
        )
        self.assertIn(
            "candidate_compile_repair",
            result.messages[0].content,
        )
        self.assertIn(
            "Editable artifacts: candidate",
            result.messages[0].content,
        )
        self.assertIn(
            "Read-only artifacts: original",
            result.messages[0].content,
        )
        self.assertIn(
            "Output contract:",
            result.messages[0].content,
        )

    def test_task_projection_includes_target_not_paths(self):
        result = SharedLayeredPromptBuilder().build(
            make_request()
        )
        user = result.messages[1].content

        self.assertIn('"kernel_name": "candidate_top"', user)
        self.assertIn(
            '"toolchain_version": "2023.2"',
            user,
        )
        self.assertNotIn(
            "/private/work/candidate.cpp",
            user,
        )
        self.assertNotIn(
            "/private/work/testbench.cpp",
            user,
        )
        self.assertNotIn(
            "/private/work/public_tb.cpp",
            user,
        )

    def test_feedback_projection_omits_operator_evidence(self):
        result = SharedLayeredPromptBuilder().build(
            make_request()
        )
        combined = "\n".join(
            message.content
            for message in result.messages
        )

        self.assertNotIn("operator-secret", combined)
        self.assertNotIn("raw_command", combined)
        self.assertNotIn("evidence_ref", combined)
        self.assertNotIn("absolute_path", combined)
        self.assertIn("<absolute-path>", combined)

    def test_manifest_is_non_sensitive(self):
        result = SharedLayeredPromptBuilder().build(
            make_request()
        )
        serialized = json.dumps(result.manifest)

        self.assertEqual(
            result.manifest["feedback_projection"],
            "agent_safe_items_only",
        )
        self.assertNotIn("operator-secret", serialized)
        self.assertNotIn("/private/", serialized)
        self.assertEqual(
            result.manifest["editable_artifacts"],
            ["candidate"],
        )

    def test_operator_full_feedback_is_rejected(self):
        request = make_request(
            feedback=make_feedback(view="operator_full")
        )

        with self.assertRaisesRegex(
            ValueError,
            "agent_safe",
        ):
            SharedLayeredPromptBuilder().build(request)

    def test_hidden_feedback_is_rejected(self):
        request = make_request(
            feedback=make_feedback(
                split="hidden",
                visible=False,
            )
        )

        with self.assertRaisesRegex(
            ValueError,
            "invisible|hidden",
        ):
            SharedLayeredPromptBuilder().build(request)

    def test_public_csim_requires_explicit_visibility(self):
        feedback = make_feedback(
            stage=FeedbackStage.CSIM,
            split="public",
        )
        request = make_request(
            purpose=(
                PromptPurpose.
                CANDIDATE_PUBLIC_CSIM_REPAIR
            ),
            feedback=feedback,
        )

        with self.assertRaisesRegex(
            ValueError,
            "explicitly visible",
        ):
            SharedLayeredPromptBuilder().build(request)

    def test_public_csim_feedback_is_accepted(self):
        feedback = make_feedback(
            stage=FeedbackStage.CSIM,
            split="public",
            visible=True,
        )
        request = make_request(
            purpose=(
                PromptPurpose.
                CANDIDATE_PUBLIC_CSIM_REPAIR
            ),
            feedback=feedback,
        )

        result = SharedLayeredPromptBuilder().build(
            request
        )

        self.assertEqual(
            result.manifest["purpose"],
            "candidate_public_csim_repair",
        )

    def test_wrong_feedback_owner_is_rejected(self):
        feedback = make_feedback(
            owner=FeedbackOwner.TESTBENCH
        )

        with self.assertRaisesRegex(
            ValueError,
            "owner",
        ):
            SharedLayeredPromptBuilder().build(
                make_request(feedback=feedback)
            )

    def test_wrong_feedback_stage_is_rejected(self):
        feedback = make_feedback(
            stage=FeedbackStage.CSYNTH
        )

        with self.assertRaisesRegex(
            ValueError,
            "stage",
        ):
            SharedLayeredPromptBuilder().build(
                make_request(feedback=feedback)
            )

    def test_testbench_purpose_accepts_testbench_owner(self):
        feedback = make_feedback(
            owner=FeedbackOwner.TESTBENCH,
            stage=FeedbackStage.COMPILE,
        )
        request = LayeredPromptRequest(
            purpose=PromptPurpose.TESTBENCH_REPAIR,
            task=make_task(),
            feedback=feedback,
            objective="Repair only the testbench.",
            artifacts=(
                PromptArtifact(
                    name="testbench",
                    content="int main() { return 0; }\n",
                ),
                PromptArtifact(
                    name="candidate",
                    content=CANDIDATE,
                ),
            ),
            modification_scope=ModificationScope(
                editable_artifacts=("testbench",),
                read_only_artifacts=("candidate",),
            ),
            output_contract=PromptOutputContract(
                artifact_name="testbench",
            ),
        )

        result = SharedLayeredPromptBuilder().build(
            request
        )

        self.assertIn(
            "testbench_repair",
            result.messages[0].content,
        )

    def test_requires_blocking_feedback(self):
        feedback = FeedbackReport(
            report_id="warning",
            source="synthetic",
            items=(
                FeedbackItem(
                    feedback_id="warning-1",
                    stage=FeedbackStage.COMPILE,
                    category=FeedbackCategory.UNKNOWN,
                    severity=FeedbackSeverity.WARNING,
                    owner=FeedbackOwner.CANDIDATE,
                    summary="warning only",
                ),
            ),
            metadata={
                "evidence_view": "agent_safe",
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "blocking",
        ):
            SharedLayeredPromptBuilder().build(
                make_request(feedback=feedback)
            )

    def test_scope_requires_exact_artifact_set(self):
        with self.assertRaisesRegex(
            ValueError,
            "not declared",
        ):
            make_request().__class__(
                purpose=(
                    PromptPurpose.
                    CANDIDATE_COMPILE_REPAIR
                ),
                task=make_task(),
                feedback=make_feedback(),
                objective="repair",
                artifacts=(
                    PromptArtifact(
                        name="candidate",
                        content=CANDIDATE,
                    ),
                    PromptArtifact(
                        name="extra",
                        content=ORIGINAL,
                    ),
                ),
                modification_scope=ModificationScope(
                    editable_artifacts=("candidate",),
                ),
                output_contract=(
                    PromptOutputContract(
                        artifact_name="candidate",
                    )
                ),
            )

    def test_scope_rejects_overlap(self):
        with self.assertRaisesRegex(
            ValueError,
            "both editable and read-only",
        ):
            ModificationScope(
                editable_artifacts=("candidate",),
                read_only_artifacts=("candidate",),
            )

    def test_rejects_operator_only_artifact(self):
        with self.assertRaisesRegex(
            ValueError,
            "operator-only",
        ):
            LayeredPromptRequest(
                purpose=(
                    PromptPurpose.
                    CANDIDATE_COMPILE_REPAIR
                ),
                task=make_task(),
                feedback=make_feedback(),
                objective="repair",
                artifacts=(
                    PromptArtifact(
                        name="candidate",
                        content=CANDIDATE,
                        agent_safe=False,
                    ),
                ),
                modification_scope=ModificationScope(
                    editable_artifacts=("candidate",),
                ),
                output_contract=(
                    PromptOutputContract(
                        artifact_name="candidate",
                    )
                ),
            )

    def test_family_prior_and_memory_layers(self):
        result = SharedLayeredPromptBuilder().build(
            make_request(
                family_instruction=(
                    "Reason internally and emit only "
                    "the final artifact."
                ),
                prior=(
                    "Attempt 1 preserved the syntax error.",
                ),
                memory=(
                    "Use an explicit loop bound when valid.",
                ),
            )
        )
        combined = "\n".join(
            message.content
            for message in result.messages
        )

        self.assertIn(
            "Model-family instruction:",
            combined,
        )
        self.assertIn(
            "Attempt 1 preserved the syntax error.",
            combined,
        )
        self.assertIn(
            "Use an explicit loop bound when valid.",
            combined,
        )
        self.assertEqual(
            result.manifest["approved_memory_count"],
            1,
        )

    def test_memory_defaults_to_none(self):
        result = SharedLayeredPromptBuilder().build(
            make_request()
        )

        self.assertIn(
            "Approved memory snippets "
            "(already gated by the caller):\n- none",
            result.messages[1].content,
        )
        self.assertEqual(
            result.manifest["approved_memory_count"],
            0,
        )

    def test_build_does_not_mutate_feedback(self):
        feedback = make_feedback()
        before = copy.deepcopy(feedback.to_dict())

        SharedLayeredPromptBuilder().build(
            make_request(feedback=feedback)
        )

        self.assertEqual(feedback.to_dict(), before)

    def test_result_is_json_serializable(self):
        result = SharedLayeredPromptBuilder().build(
            make_request()
        )

        payload = result.to_dict()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )

        self.assertIn(
            '"schema_version": 1',
            encoded,
        )


if __name__ == "__main__":
    unittest.main()
