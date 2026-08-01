import copy
import inspect
import json
import unittest

from agrefactor.config import TargetProfile, TaskSpec
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    CandidateResponseContract,
    CandidateResponseError,
    ChatMessage,
    ModelFamilyProfile,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
import agrefactor.models.candidate_adapter as candidate_adapter_module
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    LayeredPrompt,
    build_candidate_compile_repair_prompt,
)


TASK = TaskSpec(
    task_id="candidate-model-adapter-task",
    kernel_path="/operator/private/candidate.cpp",
    kernel_name="candidate_top",
    target=TargetProfile(
        name="adapter-target",
        toolchain="vitis_hls",
        toolchain_version="2023.2",
        device="xcu200-fsgd2104-2-e",
        clock_period_ns=4.0,
        compile_flags=("-D ADAPTER_TEST",),
    ),
)

ORIGINAL = (
    'extern "C" int original_top(const int *input, int output[4]) '
    "{ output[0] = input[0] + 1; return 0; }\n"
)

CURRENT = r'''
#include <stdint.h>
extern "C" int candidate_top(
    const int *input,
    int output[4]
) {
    output[0] = input[0];
    return 0;
}
'''

REPAIRED = r'''
#include <stdint.h>
extern "C" int candidate_top(
    const int *input,
    int output[4]
) {
    output[0] = input[0] + 1;
    return 0;
}
'''


def make_feedback() -> FeedbackReport:
    return FeedbackReport(
        report_id="candidate-model-adapter-feedback",
        source="deterministic-test",
        items=(
            FeedbackItem(
                feedback_id="candidate-model-adapter-feedback.item",
                stage=FeedbackStage.COMPILE,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate compile repair required",
            ),
        ),
        metadata={"evidence_view": "agent_safe"},
    )


def make_prompt() -> LayeredPrompt:
    return build_candidate_compile_repair_prompt(
        CandidateRepairPromptInputs(
            task=TASK,
            feedback=make_feedback(),
            candidate_code=CURRENT,
            original_code=ORIGINAL,
            attempt=1,
            max_attempts=2,
        )
    )


def make_request(prompt=None, candidate=CURRENT) -> CandidateModelRequest:
    return CandidateModelRequest(
        prompt=prompt or make_prompt(),
        task=TASK,
        current_candidate=candidate,
    )


class FakeProvider(ModelProvider):
    def __init__(self, response_text, *, wrong_type=False, error=None):
        self.response_text = response_text
        self.wrong_type = wrong_type
        self.error = error
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        if self.error is not None:
            raise self.error
        if self.wrong_type:
            return {"text": self.response_text}
        return ModelResponse(
            text=self.response_text,
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=120,
                completion_tokens=80,
                cost_usd=0.002,
            ),
            finish_reason="stop",
            metadata={"request_id": "fake-request"},
        )


def make_registry(provider):
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_family_profile(
        ModelFamilyProfile(name="reasoning")
    )
    registry.register_model(
        ModelSpec(
            name="candidate-repair-model",
            provider="fake",
            model="fake-candidate-1",
            family="reasoning",
            default_parameters={
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        )
    )
    return registry


class CandidateResponseContractTests(unittest.TestCase):
    def test_accepts_one_complete_cpp_replacement(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        proposed = contract.extract_and_validate(
            f"```cpp\n{REPAIRED}\n```"
        )
        self.assertEqual(proposed.strip(), REPAIRED.strip())


    def test_accepts_raw_complete_cpp_replacement(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        proposed = contract.extract_and_validate(REPAIRED)
        self.assertEqual(proposed.strip(), REPAIRED.strip())

    def test_explicit_abstention_has_stable_reason_code(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with self.assertRaises(CandidateResponseError) as captured:
            contract.extract_and_validate("AGREFACTOR_ABSTAIN")
        self.assertEqual(captured.exception.reason_codes, ("explicit_abstention",))

    def test_contract_exposes_safe_reason_codes(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        changed = REPAIRED.replace("const int *input", "int *input")
        with self.assertRaises(CandidateResponseError) as captured:
            contract.extract_and_validate(f"```cpp\n{changed}\n```")
        self.assertEqual(captured.exception.reason_codes, ("top_interface_changed",))
        self.assertNotIn("candidate_top", repr(captured.exception.reason_codes))

    def test_rejects_leading_think_block(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with self.assertRaises(CandidateResponseError):
            contract.extract_and_validate(
                "<think>internal model reasoning</think>\n"
                f"```c++\n{REPAIRED}\n```"
            )

    def test_rejects_empty_response(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with self.assertRaises(CandidateResponseError):
            contract.extract_and_validate("   ")

    def test_rejects_commentary_outside_block(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with self.assertRaises(CandidateResponseError):
            contract.extract_and_validate(
                "Here is the fix:\n"
                f"```cpp\n{REPAIRED}\n```"
            )

    def test_rejects_multiple_code_blocks(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with self.assertRaises(CandidateResponseError):
            contract.extract_and_validate(
                f"```cpp\n{REPAIRED}\n```\n"
                "```cpp\nint helper() { return 0; }\n```"
            )

    def test_rejects_unlabeled_or_wrong_language_block(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        for text in (
            f"```\n{REPAIRED}\n```",
            f"```python\n{REPAIRED}\n```",
        ):
            with self.subTest(text=text[:20]):
                with self.assertRaises(CandidateResponseError):
                    contract.extract_and_validate(text)

    def test_rejects_empty_cpp_block(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with self.assertRaises(CandidateResponseError):
            contract.extract_and_validate("```cpp\n   \n```")

    def test_rejects_patch_or_diff_content(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        for marker in (
            "diff --git a/candidate.cpp b/candidate.cpp",
            "@@ -1,2 +1,2 @@",
            "*** Begin Patch",
        ):
            with self.subTest(marker=marker):
                with self.assertRaises(CandidateResponseError):
                    contract.extract_and_validate(
                        f"```cpp\n{marker}\n{REPAIRED}\n```"
                    )

    def test_rejects_missing_or_renamed_top_function(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        for proposed in (
            "int helper() { return 0; }",
            REPAIRED.replace("candidate_top", "renamed_top"),
        ):
            with self.subTest(proposed=proposed[:30]):
                with self.assertRaises(CandidateResponseError):
                    contract.extract_and_validate(
                        f"```cpp\n{proposed}\n```"
                    )

    def test_rejects_changed_top_interface(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        changed = REPAIRED.replace(
            "const int *input",
            "int *input",
        )
        with self.assertRaisesRegex(
            CandidateResponseError,
            "interface was changed",
        ):
            contract.extract_and_validate(f"```cpp\n{changed}\n```")

    def test_rejects_duplicate_top_definition(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        duplicate = REPAIRED + "\n" + REPAIRED
        with self.assertRaisesRegex(
            CandidateResponseError,
            "multiple definitions",
        ):
            contract.extract_and_validate(
                f"```cpp\n{duplicate}\n```"
            )

    def test_rejects_candidate_main_definition(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        with_main = REPAIRED + "\nint main() { return 0; }\n"
        with self.assertRaisesRegex(
            CandidateResponseError,
            "must not define main",
        ):
            contract.extract_and_validate(
                f"```cpp\n{with_main}\n```"
            )

    def test_rejects_unchanged_or_whitespace_only_candidate(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        variants = (
            CURRENT,
            "\n\n" + CURRENT.replace("    ", "        ") + "\n",
            CURRENT.replace(
                "output[0] = input[0];",
                "// no semantic change\n    output[0] = input[0];",
            ),
        )
        for proposed in variants:
            with self.subTest(size=len(proposed)):
                with self.assertRaisesRegex(
                    CandidateResponseError,
                    "semantically unchanged",
                ):
                    contract.extract_and_validate(
                        f"```cpp\n{proposed}\n```"
                    )

    def test_multiline_interface_is_preserved(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        formatted = REPAIRED.replace(
            "extern \"C\" int candidate_top(\n"
            "    const int *input,\n"
            "    int output[4]\n"
            ")",
            'extern   "C"   int candidate_top(const int* input, int output [ 4 ])',
        )
        proposed = contract.extract_and_validate(
            f"```cxx\n{formatted}\n```"
        )
        self.assertIn("input[0] + 1", proposed)

    def test_contract_is_json_serializable(self):
        contract = CandidateResponseContract.from_candidate(TASK, CURRENT)
        encoded = json.dumps(contract.to_dict(), sort_keys=True)
        self.assertIn("candidate_top", encoded)
        self.assertEqual(
            len(contract.current_candidate_semantic_sha256),
            64,
        )


class CandidateModelRequestTests(unittest.TestCase):
    def test_request_accepts_candidate_prompt_and_preserves_inputs(self):
        prompt = make_prompt()
        task_before = copy.deepcopy(TASK.to_dict())
        prompt_before = copy.deepcopy(prompt.to_dict())
        candidate_before = CURRENT

        request = CandidateModelRequest(
            prompt=prompt,
            task=TASK,
            current_candidate=CURRENT,
        )

        self.assertIs(request.prompt, prompt)
        self.assertIs(request.task, TASK)
        self.assertEqual(request.current_candidate, candidate_before)
        self.assertEqual(TASK.to_dict(), task_before)
        self.assertEqual(prompt.to_dict(), prompt_before)

    def test_request_rejects_non_candidate_prompt(self):
        prompt = LayeredPrompt(
            messages=(
                ChatMessage(role="system", content="system"),
                ChatMessage(role="user", content="user"),
            ),
            manifest={
                "purpose": "testbench_repair",
                "task_id": TASK.task_id,
                "kernel_name": TASK.kernel_name,
                "target_profile": TASK.target.name,
                "editable_artifacts": ["testbench"],
                "output_contract": {
                    "artifact_name": "testbench",
                    "language": "cpp",
                    "complete_replacement": True,
                    "fenced_code_block": True,
                    "commentary_allowed": False,
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "candidate repair prompt"):
            make_request(prompt=prompt)

    def test_request_rejects_manifest_mismatch_or_bad_output_contract(self):
        valid = make_prompt()
        cases = []
        wrong_task = copy.deepcopy(dict(valid.manifest))
        wrong_task["task_id"] = "different-task"
        cases.append(wrong_task)
        wrong_editable = copy.deepcopy(dict(valid.manifest))
        wrong_editable["editable_artifacts"] = [
            "candidate_kernel",
            "original_program",
        ]
        cases.append(wrong_editable)
        wrong_contract = copy.deepcopy(dict(valid.manifest))
        wrong_contract["output_contract"]["commentary_allowed"] = True
        cases.append(wrong_contract)

        for manifest in cases:
            prompt = LayeredPrompt(
                messages=valid.messages,
                manifest=manifest,
            )
            with self.subTest(manifest=manifest):
                with self.assertRaises((TypeError, ValueError)):
                    make_request(prompt=prompt)


class CandidateModelAdapterTests(unittest.TestCase):
    def test_adapter_uses_registry_and_merges_parameters(self):
        provider = FakeProvider(f"```cpp\n{REPAIRED}\n```")
        adapter = CandidateModelAdapter(
            registry=make_registry(provider),
            model_name="candidate-repair-model",
            parameters={"temperature": 0},
        )

        result = adapter.generate(make_request())

        self.assertEqual(result.candidate_code.strip(), REPAIRED.strip())
        self.assertEqual(len(provider.calls), 1)
        model, model_request = provider.calls[0]
        self.assertEqual(model.name, "candidate-repair-model")
        self.assertEqual(model_request.parameters["temperature"], 0)
        self.assertEqual(model_request.parameters["max_tokens"], 4096)
        self.assertEqual(model_request.messages, make_prompt().messages)
        self.assertIs(adapter.model_spec, model)

    def test_adapter_records_prompt_response_result_and_usage(self):
        provider = FakeProvider(
            f"```cpp\n{REPAIRED}\n```"
        )
        adapter = CandidateModelAdapter(
            registry=make_registry(provider),
            model_name="candidate-repair-model",
        )
        request = make_request()

        result = adapter.generate(request)
        payload = result.to_dict()

        self.assertEqual(adapter.prompts, (request.prompt,))
        self.assertIs(adapter.last_prompt, request.prompt)
        self.assertEqual(adapter.responses, (result.response,))
        self.assertIs(adapter.last_response, result.response)
        self.assertEqual(adapter.results, (result,))
        self.assertIs(adapter.last_result, result)
        self.assertEqual(payload["response"]["usage"]["total_tokens"], 200)
        self.assertEqual(payload["response"]["usage"]["cost_usd"], 0.002)
        self.assertEqual(
            payload["prompt_manifest"]["purpose"],
            "candidate_compile_repair",
        )
        json.dumps(payload, sort_keys=True)

    def test_adapter_records_response_before_contract_rejection(self):
        provider = FakeProvider(f"```cpp\n{CURRENT}\n```")
        adapter = CandidateModelAdapter(
            registry=make_registry(provider),
            model_name="candidate-repair-model",
        )

        with self.assertRaises(CandidateResponseError):
            adapter.generate(make_request())

        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(len(adapter.responses), 1)
        self.assertEqual(adapter.results, ())

    def test_adapter_rejects_non_model_response(self):
        provider = FakeProvider(
            f"```cpp\n{REPAIRED}\n```",
            wrong_type=True,
        )
        adapter = CandidateModelAdapter(
            registry=make_registry(provider),
            model_name="candidate-repair-model",
        )

        with self.assertRaisesRegex(TypeError, "ModelResponse"):
            adapter.generate(make_request())

        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(adapter.responses, ())
        self.assertEqual(adapter.results, ())

    def test_provider_exception_records_prompt_only(self):
        provider = FakeProvider(
            "unused",
            error=RuntimeError("provider unavailable"),
        )
        adapter = CandidateModelAdapter(
            registry=make_registry(provider),
            model_name="candidate-repair-model",
        )

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            adapter.generate(make_request())

        self.assertEqual(len(adapter.prompts), 1)
        self.assertEqual(adapter.responses, ())
        self.assertEqual(adapter.results, ())

    def test_fixed_model_generic_naming_and_no_tool_dependencies(self):
        source = inspect.getsource(candidate_adapter_module)
        disallowed = (
            "fpt" + "26",
            "competi" + "tion",
            "track" + "_a",
        )
        for term in disallowed:
            self.assertNotIn(term, source.lower())
        for forbidden_import in (
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "pathlib",
            "BudgetManager",
            "ValidationOrchestrator",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertNotIn("auto_model", source)
        self.assertNotIn("repair_loop", source)


if __name__ == "__main__":
    unittest.main()
