
import copy
from dataclasses import FrozenInstanceError
import inspect
import json
import socket
import subprocess
import unittest
from unittest.mock import patch

from agrefactor.config import (
    EvaluationSplit,
    TargetProfile,
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
import agrefactor.prompts.candidate_repair as candidate_repair_module
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    ModificationScope,
    PromptArtifact,
    PromptOutputContract,
    PromptPurpose,
    SharedLayeredPromptBuilder,
    build_candidate_compile_repair_prompt,
    build_candidate_csynth_repair_prompt,
    build_candidate_public_csim_repair_prompt,
)


ORIGINAL = (
    'extern "C" int original_top(int x) '
    "{ return x + 1; }\n"
)
CANDIDATE = (
    'extern "C" int candidate_top(int x) '
    "{ return x; }\n"
)
PUBLIC_TESTBENCH = (
    'extern "C" int original_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    "int main() {\n"
    "  return original_top(3) == candidate_top(3) ? 0 : 1;\n"
    "}\n"
)


def make_task() -> TaskSpec:
    return TaskSpec(
        task_id="candidate-repair-task",
        kernel_path="/private/work/candidate.cpp",
        kernel_name="candidate_top",
        target=TargetProfile(
            name="effective-target",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
            device="xcu200-fsgd2104-2-e",
            clock_period_ns=4.0,
            compile_flags=("-D XILINX",),
        ),
        test_suites=(
            TestSuiteSpec(
                suite_id="public-a",
                split=EvaluationSplit.PUBLIC,
                testbench_path="/private/work/public_tb.cpp",
            ),
        ),
    )


def make_feedback(
    *,
    stage: FeedbackStage,
    owner: FeedbackOwner = FeedbackOwner.CANDIDATE,
    category: FeedbackCategory = FeedbackCategory.SYNTAX_ERROR,
    severity: FeedbackSeverity = FeedbackSeverity.ERROR,
    view: str = "agent_safe",
    split: EvaluationSplit | None = None,
    visible: bool | None = None,
    report_id: str = "candidate-repair-report",
    source: str = "synthetic",
    extra_metadata: dict | None = None,
) -> FeedbackReport:
    metadata = {"evidence_view": view}
    if split is not None:
        metadata["evaluation_split"] = split.value
    if visible is not None:
        metadata["feedback_visible_to_agent"] = visible
    if extra_metadata is not None:
        metadata.update(extra_metadata)

    return FeedbackReport(
        report_id=report_id,
        source=source,
        items=(
            FeedbackItem(
                feedback_id=f"{report_id}.item",
                stage=stage,
                category=category,
                severity=severity,
                owner=owner,
                summary=(
                    "candidate failed at "
                    "/private/work/candidate.cpp"
                ),
                detail=(
                    "see C:\\secret\\candidate.cpp and "
                    "/private/log/build.log"
                ),
                source="deterministic-test",
                evidence_ref="/private/evidence/raw.json",
                metadata={
                    "absolute_path": "/private/work/candidate.cpp",
                    "secret": "operator-item-secret",
                },
            ),
        ),
        source_evidence={
            "raw_command": "tool /private/work/candidate.cpp",
            "secret": "operator-report-secret",
        },
        metadata=metadata,
    )


def make_inputs(
    feedback: FeedbackReport,
    *,
    include_public_testbench: bool = True,
) -> CandidateRepairPromptInputs:
    return CandidateRepairPromptInputs(
        task=make_task(),
        feedback=feedback,
        candidate_code=CANDIDATE,
        original_code=ORIGINAL,
        public_testbench_code=(
            PUBLIC_TESTBENCH
            if include_public_testbench
            else None
        ),
        attempt=1,
        max_attempts=3,
        family_instruction=(
            "Reason internally and emit only the final artifact."
        ),
        prior_attempt_summaries=(
            "The previous attempt preserved the error.",
        ),
        approved_memory_snippets=(
            "Use only caller-approved evidence.",
        ),
    )


class RecordingBuilder(SharedLayeredPromptBuilder):
    def __init__(self) -> None:
        self.requests = []

    def build(self, request):
        self.requests.append(request)
        return super().build(request)


class CandidateRepairPromptPolicyTests(unittest.TestCase):
    def test_compile_policy_purpose_scope_and_output_contract(self):
        builder = RecordingBuilder()
        result = build_candidate_compile_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.COMPILE)
            ),
            builder=builder,
        )
        request = builder.requests[0]

        self.assertTrue(
            all(
                isinstance(artifact, PromptArtifact)
                for artifact in request.artifacts
            )
        )
        self.assertIsInstance(
            request.modification_scope,
            ModificationScope,
        )
        self.assertIsInstance(
            request.output_contract,
            PromptOutputContract,
        )
        self.assertEqual(
            result.manifest["purpose"],
            PromptPurpose.CANDIDATE_COMPILE_REPAIR.value,
        )
        self.assertEqual(
            result.manifest["editable_artifacts"],
            ["candidate_kernel"],
        )
        self.assertEqual(
            result.manifest["read_only_artifacts"],
            ["original_program", "public_testbench"],
        )
        self.assertEqual(
            {artifact.name for artifact in request.artifacts},
            {
                "candidate_kernel",
                "original_program",
                "public_testbench",
            },
        )
        self.assertEqual(
            set(request.modification_scope.editable_artifacts)
            | set(request.modification_scope.read_only_artifacts),
            {artifact.name for artifact in request.artifacts},
        )

        contract = result.manifest["output_contract"]
        self.assertEqual(contract["artifact_name"], "candidate_kernel")
        self.assertEqual(contract["language"], "cpp")
        self.assertTrue(contract["complete_replacement"])
        self.assertTrue(contract["fenced_code_block"])
        self.assertFalse(contract["commentary_allowed"])

        system_prompt = result.messages[0].content
        for required in (
            "Never modify or propose changes to the original program.",
            "Never modify or propose changes to the Public testbench.",
            "Never delete, rename, or change the candidate top-level",
            "Do not weaken, remove, bypass, or special-case validation",
            "Do not delete required functionality",
            "Do not infer, request, encode, or depend on Hidden",
            "Return no patch, diff, partial edit, explanation",
        ):
            self.assertIn(required, system_prompt)

    def test_compile_policy_allows_omitting_public_testbench(self):
        builder = RecordingBuilder()
        result = build_candidate_compile_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.LINK),
                include_public_testbench=False,
            ),
            builder=builder,
        )
        request = builder.requests[0]

        self.assertEqual(
            result.manifest["read_only_artifacts"],
            ["original_program"],
        )
        self.assertEqual(
            [artifact.name for artifact in request.artifacts],
            ["candidate_kernel", "original_program"],
        )
        self.assertNotIn(
            "public_testbench",
            result.messages[1].content,
        )

    def test_csynth_policy_includes_effective_target_profile(self):
        result = build_candidate_csynth_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.CSYNTH)
            )
        )
        combined = "\n".join(
            message.content for message in result.messages
        )

        self.assertEqual(
            result.manifest["purpose"],
            PromptPurpose.CANDIDATE_CSYNTH_REPAIR.value,
        )
        self.assertIn('"name": "effective-target"', combined)
        self.assertIn('"toolchain_version": "2023.2"', combined)
        self.assertIn('"device": "xcu200-fsgd2104-2-e"', combined)
        self.assertIn('"clock_period_ns": 4.0', combined)

    def test_csynth_policy_allows_omitting_public_testbench(self):
        builder = RecordingBuilder()
        result = build_candidate_csynth_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.CSYNTH),
                include_public_testbench=False,
            ),
            builder=builder,
        )
        request = builder.requests[0]

        self.assertEqual(
            result.manifest["read_only_artifacts"],
            ["original_program"],
        )
        self.assertEqual(
            [artifact.name for artifact in request.artifacts],
            ["candidate_kernel", "original_program"],
        )
        self.assertNotIn(
            "public_testbench",
            result.messages[1].content,
        )

    def test_public_csim_accepts_public_agent_visible_feedback(self):
        builder = RecordingBuilder()
        result = build_candidate_public_csim_repair_prompt(
            make_inputs(
                make_feedback(
                    stage=FeedbackStage.CSIM,
                    split=EvaluationSplit.PUBLIC,
                    visible=True,
                )
            ),
            builder=builder,
        )
        request = builder.requests[0]

        self.assertEqual(
            result.manifest["purpose"],
            PromptPurpose.CANDIDATE_PUBLIC_CSIM_REPAIR.value,
        )
        self.assertEqual(
            request.feedback.metadata["evaluation_split"],
            EvaluationSplit.PUBLIC.value,
        )
        self.assertIs(
            request.feedback.metadata["feedback_visible_to_agent"],
            True,
        )
        self.assertEqual(
            request.feedback.metadata["evidence_view"],
            "agent_safe",
        )
        self.assertIn(
            "Read-only artifact: public_testbench",
            result.messages[1].content,
        )

    def test_public_csim_requires_public_testbench(self):
        inputs = make_inputs(
            make_feedback(
                stage=FeedbackStage.TEST,
                split=EvaluationSplit.PUBLIC,
                visible=True,
            ),
            include_public_testbench=False,
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires public_testbench_code",
        ):
            build_candidate_public_csim_repair_prompt(inputs)

    def test_hidden_feedback_is_rejected(self):
        invalid_reports = (
            (
                "hidden split marked visible and agent-safe",
                make_feedback(
                    stage=FeedbackStage.CSIM,
                    split=EvaluationSplit.HIDDEN,
                    visible=True,
                    report_id="hidden-suite-secret-report",
                    source="hidden-suite-secret-source",
                    extra_metadata={
                        "hidden_identifier": "hidden-case-17",
                        "secret": "hidden-report-secret",
                        "absolute_path": "/hidden/private/case.json",
                    },
                ),
                "hidden",
            ),
            (
                "public split marked invisible",
                make_feedback(
                    stage=FeedbackStage.CSIM,
                    split=EvaluationSplit.PUBLIC,
                    visible=False,
                ),
                "invisible",
            ),
            (
                "missing explicit public split",
                make_feedback(
                    stage=FeedbackStage.CSIM,
                    visible=True,
                ),
                "public feedback split",
            ),
            (
                "missing explicit visibility",
                make_feedback(
                    stage=FeedbackStage.CSIM,
                    split=EvaluationSplit.PUBLIC,
                ),
                "explicitly visible",
            ),
        )

        for label, report, pattern in invalid_reports:
            with self.subTest(label=label):
                builder = SharedLayeredPromptBuilder()
                with patch.object(
                    builder,
                    "_build_system_prompt",
                    side_effect=AssertionError("renderer was reached"),
                ), patch.object(
                    builder,
                    "_build_user_prompt",
                    side_effect=AssertionError("renderer was reached"),
                ):
                    with self.assertRaisesRegex(ValueError, pattern):
                        build_candidate_public_csim_repair_prompt(
                            make_inputs(report),
                            builder=builder,
                        )

    def test_operator_full_feedback_is_rejected(self):
        inputs = make_inputs(
            make_feedback(
                stage=FeedbackStage.COMPILE,
                view="operator_full",
            )
        )

        with self.assertRaisesRegex(ValueError, "agent_safe"):
            build_candidate_compile_repair_prompt(inputs)

    def test_wrong_owner_is_rejected(self):
        compile_cases = (
            (
                "testbench linkage mismatch",
                FeedbackStage.LINK,
                FeedbackCategory.LINKAGE_MISMATCH,
                FeedbackOwner.TESTBENCH,
            ),
            (
                "compiler missing",
                FeedbackStage.COMPILE,
                FeedbackCategory.TOOLCHAIN_FAILURE,
                FeedbackOwner.TOOLCHAIN,
            ),
            (
                "compile timeout",
                FeedbackStage.COMPILE,
                FeedbackCategory.TIMEOUT,
                FeedbackOwner.TOOLCHAIN,
            ),
            (
                "budget block",
                FeedbackStage.COMPILE,
                FeedbackCategory.BUDGET_EXHAUSTED,
                FeedbackOwner.EVALUATOR,
            ),
            (
                "unknown compile owner",
                FeedbackStage.COMPILE,
                FeedbackCategory.UNKNOWN,
                FeedbackOwner.UNKNOWN,
            ),
        )
        for label, stage, category, owner in compile_cases:
            with self.subTest(policy="compile", case=label):
                inputs = make_inputs(
                    make_feedback(
                        stage=stage,
                        category=category,
                        owner=owner,
                        report_id=(
                            f"preflight-{owner.value}-report"
                        ),
                        source="preflight-handler",
                    )
                )
                with self.assertRaisesRegex(ValueError, "owner"):
                    build_candidate_compile_repair_prompt(inputs)

        rejected_csynth_owners = (
            FeedbackOwner.TOOLCHAIN,
            FeedbackOwner.CONFIGURATION,
            FeedbackOwner.EVALUATOR,
            FeedbackOwner.TASK_INPUT,
            FeedbackOwner.TESTBENCH,
            FeedbackOwner.ORIGINAL,
            FeedbackOwner.UNKNOWN,
            FeedbackOwner.NONE,
        )
        for owner in rejected_csynth_owners:
            with self.subTest(policy="csynth", owner=owner.value):
                inputs = make_inputs(
                    make_feedback(
                        stage=FeedbackStage.CSYNTH,
                        owner=owner,
                        report_id=(
                            f"csynth-{owner.value}-report"
                        ),
                        source="csynth-handler",
                    ),
                    include_public_testbench=False,
                )
                with self.assertRaisesRegex(ValueError, "owner"):
                    build_candidate_csynth_repair_prompt(inputs)

        nonblocking = make_inputs(
            make_feedback(
                stage=FeedbackStage.COMPILE,
                owner=FeedbackOwner.CANDIDATE,
                severity=FeedbackSeverity.WARNING,
            )
        )
        with self.assertRaisesRegex(ValueError, "blocking"):
            build_candidate_compile_repair_prompt(nonblocking)

    def test_compile_wrong_stage_is_rejected(self):
        inputs = make_inputs(
            make_feedback(stage=FeedbackStage.CSYNTH)
        )

        with self.assertRaisesRegex(ValueError, "stage"):
            build_candidate_compile_repair_prompt(inputs)

    def test_csynth_wrong_stage_is_rejected(self):
        inputs = make_inputs(
            make_feedback(stage=FeedbackStage.COMPILE)
        )

        with self.assertRaisesRegex(ValueError, "stage"):
            build_candidate_csynth_repair_prompt(inputs)

    def test_public_csim_wrong_stage_is_rejected(self):
        inputs = make_inputs(
            make_feedback(
                stage=FeedbackStage.COMPILE,
                split=EvaluationSplit.PUBLIC,
                visible=True,
            )
        )

        with self.assertRaisesRegex(ValueError, "stage"):
            build_candidate_public_csim_repair_prompt(inputs)

    def test_original_and_public_testbench_are_read_only(self):
        builders = (
            build_candidate_compile_repair_prompt,
            build_candidate_csynth_repair_prompt,
            build_candidate_public_csim_repair_prompt,
        )
        feedbacks = (
            make_feedback(stage=FeedbackStage.COMPILE),
            make_feedback(stage=FeedbackStage.CSYNTH),
            make_feedback(
                stage=FeedbackStage.CSIM,
                split=EvaluationSplit.PUBLIC,
                visible=True,
            ),
        )

        for build, feedback in zip(builders, feedbacks):
            with self.subTest(build=build.__name__):
                recorder = RecordingBuilder()
                result = build(make_inputs(feedback), builder=recorder)
                request = recorder.requests[0]
                self.assertEqual(
                    result.manifest["read_only_artifacts"],
                    ["original_program", "public_testbench"],
                )
                self.assertEqual(
                    set(request.modification_scope.read_only_artifacts),
                    {"original_program", "public_testbench"},
                )

    def test_candidate_is_the_only_editable_artifact(self):
        builders = (
            build_candidate_compile_repair_prompt,
            build_candidate_csynth_repair_prompt,
            build_candidate_public_csim_repair_prompt,
        )
        feedbacks = (
            make_feedback(stage=FeedbackStage.STATIC_CHECK),
            make_feedback(stage=FeedbackStage.CSYNTH),
            make_feedback(
                stage=FeedbackStage.TEST,
                split=EvaluationSplit.PUBLIC,
                visible=True,
            ),
        )

        for build, feedback in zip(builders, feedbacks):
            with self.subTest(build=build.__name__):
                result = build(make_inputs(feedback))
                self.assertEqual(
                    result.manifest["editable_artifacts"],
                    ["candidate_kernel"],
                )

    def test_sensitive_feedback_fields_and_paths_do_not_leak(self):
        result = build_candidate_compile_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.COMPILE)
            )
        )
        encoded = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )

        for marker in (
            "operator-item-secret",
            "operator-report-secret",
            "raw_command",
            "evidence_ref",
            "absolute_path",
            "/private/work/candidate.cpp",
            "/private/log/build.log",
            "/private/evidence/raw.json",
        ):
            self.assertNotIn(marker, encoded)
        self.assertIn("<absolute-path>", encoded)

    def test_policy_does_not_call_model_or_tools(self):
        compile_inputs = make_inputs(
            make_feedback(stage=FeedbackStage.COMPILE)
        )
        csynth_inputs = make_inputs(
            make_feedback(stage=FeedbackStage.CSYNTH),
            include_public_testbench=False,
        )
        csim_inputs = make_inputs(
            make_feedback(
                stage=FeedbackStage.CSIM,
                split=EvaluationSplit.PUBLIC,
                visible=True,
            )
        )

        with patch.object(
            subprocess,
            "run",
            side_effect=AssertionError("process launch forbidden"),
        ), patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("process launch forbidden"),
        ), patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network access forbidden"),
        ):
            build_candidate_compile_repair_prompt(compile_inputs)
            build_candidate_csynth_repair_prompt(csynth_inputs)
            build_candidate_public_csim_repair_prompt(csim_inputs)

        source = inspect.getsource(candidate_repair_module)
        for forbidden in (
            "agrefactor.models",
            "ModelRegistry",
            "provider",
            "subprocess",
            "socket",
            "requests",
            "urllib",
            "http.client",
            "Path(",
            ".read_text(",
            "os.environ",
            "run_dir",
            "retrieve_memory",
            "applicability_score",
        ):
            self.assertNotIn(forbidden, source)

    def test_result_is_json_serializable(self):
        result = build_candidate_csynth_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.CSYNTH),
                include_public_testbench=False,
            )
        )

        encoded = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        self.assertIn('"schema_version": 1', encoded)

    def test_inputs_are_not_mutated(self):
        task = make_task()
        feedback = make_feedback(stage=FeedbackStage.COMPILE)
        candidate_code = "\n  " + CANDIDATE + "  \n"
        original_code = "\n  " + ORIGINAL + "  \n"
        public_testbench_code = "\n" + PUBLIC_TESTBENCH + "\n"
        prior = ("  caller prior summary  ",)
        memory = ("  caller-approved memory  ",)
        inputs = CandidateRepairPromptInputs(
            task=task,
            feedback=feedback,
            candidate_code=candidate_code,
            original_code=original_code,
            public_testbench_code=public_testbench_code,
            prior_attempt_summaries=prior,
            approved_memory_snippets=memory,
        )

        self.assertIs(inputs.task, task)
        self.assertIs(inputs.feedback, feedback)
        self.assertEqual(inputs.candidate_code, candidate_code)
        self.assertEqual(inputs.original_code, original_code)
        self.assertEqual(
            inputs.public_testbench_code,
            public_testbench_code,
        )
        self.assertEqual(inputs.prior_attempt_summaries, prior)
        self.assertEqual(inputs.approved_memory_snippets, memory)

        before = {
            "task": copy.deepcopy(inputs.task.to_dict()),
            "feedback": copy.deepcopy(inputs.feedback.to_dict()),
            "candidate_code": inputs.candidate_code,
            "original_code": inputs.original_code,
            "public_testbench_code": inputs.public_testbench_code,
            "prior_attempt_summaries": inputs.prior_attempt_summaries,
            "approved_memory_snippets": inputs.approved_memory_snippets,
        }
        result = build_candidate_compile_repair_prompt(inputs)
        after = {
            "task": inputs.task.to_dict(),
            "feedback": inputs.feedback.to_dict(),
            "candidate_code": inputs.candidate_code,
            "original_code": inputs.original_code,
            "public_testbench_code": inputs.public_testbench_code,
            "prior_attempt_summaries": inputs.prior_attempt_summaries,
            "approved_memory_snippets": inputs.approved_memory_snippets,
        }
        self.assertEqual(after, before)
        self.assertEqual(
            result.manifest["approved_memory_count"],
            1,
        )
        self.assertIn(
            "caller-approved memory",
            result.messages[1].content,
        )
        with self.assertRaises(FrozenInstanceError):
            inputs.attempt = 2

    def test_three_policies_share_one_renderer(self):
        builder = RecordingBuilder()

        build_candidate_compile_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.COMPILE)
            ),
            builder=builder,
        )
        build_candidate_csynth_repair_prompt(
            make_inputs(
                make_feedback(stage=FeedbackStage.CSYNTH),
                include_public_testbench=False,
            ),
            builder=builder,
        )
        build_candidate_public_csim_repair_prompt(
            make_inputs(
                make_feedback(
                    stage=FeedbackStage.CSIM,
                    split=EvaluationSplit.PUBLIC,
                    visible=True,
                )
            ),
            builder=builder,
        )

        self.assertEqual(len(builder.requests), 3)
        self.assertEqual(
            [request.purpose for request in builder.requests],
            [
                PromptPurpose.CANDIDATE_COMPILE_REPAIR,
                PromptPurpose.CANDIDATE_CSYNTH_REPAIR,
                PromptPurpose.CANDIDATE_PUBLIC_CSIM_REPAIR,
            ],
        )
        source = inspect.getsource(candidate_repair_module)
        self.assertEqual(
            source.count("def _build_candidate_repair_prompt("),
            1,
        )
        self.assertEqual(source.count("builder.build(request)"), 1)

    def test_generic_naming_guard(self):
        source = inspect.getsource(
            candidate_repair_module
        ).lower()
        for forbidden in (
            "fpt" + "26",
            "competi" + "tion",
            "track" + "_a",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
