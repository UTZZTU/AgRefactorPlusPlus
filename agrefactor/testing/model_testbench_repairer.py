"""Provider-neutral LLM adapter for testbench-only repair."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from agrefactor.evaluation.preflight_feedback import (
    TestbenchPreflightFeedbackAdapter,
)
from agrefactor.evaluation.preflight_feedback_view import (
    TestbenchPreflightFeedbackViewAdapter,
)
from agrefactor.models import (
    ChatMessage,
    EffectiveModelConfig,
    ModelFamilyProfile,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    estimate_model_cost,
)
from agrefactor.runtime.budget import BudgetManager
from agrefactor.repair.protocol import (
    RepairModelObservation,
)
from agrefactor.prompts import (
    LayeredPrompt,
    LayeredPromptRequest,
    ModificationScope,
    PromptArtifact,
    PromptOutputContract,
    PromptPurpose,
    SharedLayeredPromptBuilder,
)
from agrefactor.testing.testbench_repair import TestbenchRepairRequest


class TestbenchRepairResponseError(ValueError):
    """Raised when a model response violates the repair contract."""


_THINK_RE = re.compile(
    r"<think>.*?</think>\s*",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_RE = re.compile(
    r"```(?:cpp|c\+\+|cxx)?\s*(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_FUNCTION_DECL_RE = re.compile(
    r'^\s*(?:extern\s+"C"\s+)?'
    r'(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]\s*|\s+))+'
    r'(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*;',
    re.MULTILINE,
)
_DEFINE_RE = re.compile(
    r"^\s*#define\s+[A-Za-z_]\w*[^\n]*$",
    re.MULTILINE,
)


def _normalize_fragment(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_complete_cpp_block(text: str) -> str:
    """Extract exactly one fenced C++ block and reject commentary."""

    if not isinstance(text, str) or not text.strip():
        raise TestbenchRepairResponseError(
            "model response must not be empty"
        )

    cleaned = _THINK_RE.sub("", text).strip()
    matches = list(_FENCE_RE.finditer(cleaned))

    if len(matches) != 1:
        raise TestbenchRepairResponseError(
            "model response must contain exactly one fenced C++ block"
        )

    match = matches[0]
    outside = (
        cleaned[: match.start()]
        + cleaned[match.end() :]
    ).strip()
    if outside:
        raise TestbenchRepairResponseError(
            "model response must not contain commentary outside the C++ block"
        )

    code = match.group(1).strip()
    if not code:
        raise TestbenchRepairResponseError(
            "model returned an empty C++ block"
        )
    return code


def _extract_declared_function_names(
    source: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            match.group("name")
            for match in _FUNCTION_DECL_RE.finditer(source)
        )
    )


def _extract_macros(source: str) -> tuple[str, ...]:
    return tuple(
        _normalize_fragment(match.group(0))
        for match in _DEFINE_RE.finditer(source)
    )


def _call_count(source: str, function_name: str) -> int:
    without_declarations = _FUNCTION_DECL_RE.sub("", source)
    return len(
        re.findall(
            rf"\b{re.escape(function_name)}\s*\(",
            without_declarations,
        )
    )


@dataclass(frozen=True, slots=True)
class TestbenchRepairContract:
    # Minimal structural obligations for a testbench repair.
    required_top_function_names: tuple[str, ...]

    @classmethod
    def from_request(
        cls,
        request: TestbenchRepairRequest,
    ) -> "TestbenchRepairContract":
        if not isinstance(request, TestbenchRepairRequest):
            raise TypeError("request must be a TestbenchRepairRequest")

        candidate_name = request.task.kernel_name.strip()
        names: list[str] = []
        if candidate_name.endswith("_hls") and candidate_name != "_hls":
            names.append(candidate_name[:-4])
        names.append(candidate_name)
        return cls(
            required_top_function_names=tuple(dict.fromkeys(names))
        )

    def validate(self, proposed: str) -> tuple[str, ...]:
        issues: list[str] = []
        if not re.search(r"\b(?:int|auto)\s+main\s*\(", proposed):
            issues.append("missing main(...) entry point")

        allowed = set(self.required_top_function_names)
        for function_name in self.required_top_function_names:
            if _call_count(proposed, function_name) < 1:
                issues.append(
                    "missing required public top-level call: "
                    + function_name
                )
            definition_pattern = re.compile(
                rf"^\s*(?:extern\s+\"C\"\s+)?"
                rf"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]\s*|\s+))+"
                rf"{re.escape(function_name)}\s*"
                rf"\([^;{{}}]*\)\s*\{{",
                re.MULTILINE,
            )
            if definition_pattern.search(proposed):
                issues.append(
                    "testbench must not define, stub, or wrap "
                    "public top-level function: "
                    + function_name
                    + "; it also must not alias or reimplement it"
                )

        unexpected = sorted(
            {
                name
                for name in _extract_declared_function_names(proposed)
                if name not in allowed
            }
        )
        if unexpected:
            issues.append(
                "testbench has external helper declarations outside "
                "the Original/Candidate black-box surface: "
                + ", ".join(unexpected)
            )
        return tuple(issues)


    def to_prompt_requirements(self) -> tuple[str, ...]:
        calls = ", ".join(
            f"{name}>=1" for name in self.required_top_function_names
        )
        return (
            (
                "Required public top-level function calls that must "
                "remain present: "
                + calls
                + ". Do not define, stub, wrap, or reimplement "
                "these functions in the testbench."
            ),
            (
                "Helper declarations, helper call counts, and macros "
                "copied from the current failing testbench are not "
                "deterministic preservation obligations. They may be "
                "removed or corrected when required by real compiler/"
                "linker evidence, provided the golden-vs-candidate "
                "comparison remains meaningful."
            ),
        )


_TESTBENCH_FORBIDDEN_ACTIONS = (
    "Never modify or propose changes to the original program.",
    "Never modify or propose changes to the candidate HLS kernel.",
    (
        "Only forward-declare the Original and Candidate top functions. "
        "Never define, stub, wrap, or reimplement either top inside the "
        "Testbench; never alias either top."
    ),
    (
        "Never declare, read, write, or reset implementation-private "
        "globals or file-scope variables owned by either implementation."
    ),
    (
        "Never copy or depend on implementation-private types, helper "
        "functions, allocator state, or internal data structures."
    ),
    (
        "Never weaken the golden-vs-Candidate comparison or make the "
        "Testbench return success unconditionally."
    ),
)

_TESTBENCH_OUTPUT_REQUIREMENTS = (
    (
        "Treat Original and Candidate implementations as read-only black "
        "boxes exposed only through their public top declarations."
    ),
    (
        "Existing Testbench declarations are not authoritative. Correct "
        "the public top declarations when real compiler or linker evidence "
        "shows a mismatch, while preserving their actual ABI."
    ),
    (
        "The Testbench may own headers, macros, data, and local helper "
        "definitions, but its only external function forward declarations "
        "must be the Original and Candidate tops."
    ),
    (
        "Preserve exact return types, parameter order/types, qualifiers, "
        "pointer/array notation, and C/C++ language linkage for those tops."
    ),
    (
        "Use deterministic public inputs, independent mutable storage, and "
        "equivalent clean logical states. Correctness takes priority over "
        "testcase count or coverage."
    ),
    (
        "When the failing Testbench attempted to reset implementation-private "
        "state, preserve clean-state semantics without private access. On a "
        "supported POSIX host simulation, a fresh child process and wait, or "
        "an equivalent supported harness-isolation technique, may be used."
    ),
    (
        "Test cases may be removed or replaced when required by real "
        "compiler/runtime evidence, but the resulting Testbench must retain "
        "a meaningful golden-vs-Candidate comparison and failure semantics."
    ),
    (
        "Return exactly one complete C++ Testbench in one fenced code block "
        "with no commentary."
    ),
)


def build_testbench_repair_prompt(
    request: TestbenchRepairRequest,
    *,
    family_instruction: str | None = None,
    family_profile: ModelFamilyProfile | None = None,
    builder: SharedLayeredPromptBuilder | None = None,
) -> LayeredPrompt:
    """Build one shared layered prompt from safe preflight evidence."""

    if not isinstance(request, TestbenchRepairRequest):
        raise TypeError(
            "request must be a TestbenchRepairRequest"
        )
    if builder is None:
        builder = SharedLayeredPromptBuilder()
    elif not isinstance(builder, SharedLayeredPromptBuilder):
        raise TypeError(
            "builder must be a SharedLayeredPromptBuilder or None"
        )

    operator_report = (
        TestbenchPreflightFeedbackAdapter().to_operator_report(
            request.preflight,
            report_id=(
                f"testbench-repair-{request.attempt}.operator"
            ),
        )
    )
    agent_report = (
        TestbenchPreflightFeedbackViewAdapter().to_agent_report(
            operator_report,
            report_id=(
                f"testbench-repair-{request.attempt}.agent"
            ),
        )
    )
    contract = TestbenchRepairContract.from_request(request)

    layered_request = LayeredPromptRequest(
        purpose=PromptPurpose.TESTBENCH_REPAIR,
        task=request.task,
        feedback=agent_report,
        objective=(
            "Perform a testbench-only repair supported by the "
            "structured preflight evidence."
        ),
        artifacts=(
            PromptArtifact(
                name="testbench",
                content=request.current_testbench,
            ),
            PromptArtifact(
                name="original_program",
                content=request.original_code,
            ),
            PromptArtifact(
                name="candidate_kernel",
                content=request.candidate_code,
            ),
        ),
        modification_scope=ModificationScope(
            editable_artifacts=("testbench",),
            read_only_artifacts=(
                "original_program",
                "candidate_kernel",
            ),
            forbidden_actions=_TESTBENCH_FORBIDDEN_ACTIONS,
        ),
        output_contract=PromptOutputContract(
            artifact_name="testbench",
            language="cpp",
            complete_replacement=True,
            fenced_code_block=True,
            commentary_allowed=False,
            additional_requirements=(
                _TESTBENCH_OUTPUT_REQUIREMENTS
                + contract.to_prompt_requirements()
            ),
        ),
        attempt=request.attempt,
        max_attempts=request.max_attempts,
        family_instruction=family_instruction,
        prior_attempt_summaries=(
            request.prior_attempt_summaries
        ),
        family_profile=family_profile,
    )

    return builder.build(layered_request)


def build_testbench_repair_messages(
    request: TestbenchRepairRequest,
    *,
    family_instruction: str | None = None,
    family_profile: ModelFamilyProfile | None = None,
    builder: SharedLayeredPromptBuilder | None = None,
) -> tuple[ChatMessage, ...]:
    """Compatibility wrapper returning shared layered messages."""

    return build_testbench_repair_prompt(
        request,
        family_instruction=family_instruction,
        family_profile=family_profile,
        builder=builder,
    ).messages


class ModelTestbenchRepairer:
    # One resolved model configuration for testbench-only repair.

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        model_name: str | None = None,
        effective_config: EffectiveModelConfig | None = None,
        parameters: Mapping[str, Any] | None = None,
        family_instructions: Mapping[str, str] | None = None,
        prompt_builder: SharedLayeredPromptBuilder | None = None,
        budget: BudgetManager | None = None,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("registry must be a ModelRegistry")
        if prompt_builder is None:
            prompt_builder = SharedLayeredPromptBuilder()
        elif not isinstance(
            prompt_builder,
            SharedLayeredPromptBuilder,
        ):
            raise TypeError(
                "prompt_builder must be a "
                "SharedLayeredPromptBuilder or None"
            )
        if budget is not None and not isinstance(
            budget,
            BudgetManager,
        ):
            raise TypeError(
                "budget must be a BudgetManager or None"
            )

        if effective_config is None:
            if model_name is None:
                raise ValueError(
                    "model_name is required when "
                    "effective_config is not provided"
                )
            (
                model,
                provider,
                family_profile,
            ) = registry.resolve_with_profile(model_name)
            raw_parameters = dict(parameters or {})
            effective_parameters = (
                family_profile.merge_parameters(
                    model.default_parameters,
                    raw_parameters,
                )
            )
            family_instruction = None
            if model.family:
                family_instruction = dict(
                    family_instructions or {}
                ).get(model.family)
            resolved_config = None
        else:
            if not isinstance(
                effective_config,
                EffectiveModelConfig,
            ):
                raise TypeError(
                    "effective_config must be an "
                    "EffectiveModelConfig or None"
                )
            conflicts = []
            if model_name is not None:
                conflicts.append("model_name")
            if parameters is not None:
                conflicts.append("parameters")
            if family_instructions is not None:
                conflicts.append("family_instructions")
            if conflicts:
                raise ValueError(
                    "effective_config is authoritative; "
                    "remove parallel constructor arguments: "
                    + ", ".join(conflicts)
                )
            resolved_config = effective_config
            model = resolved_config.to_model_spec()
            provider = registry.get_provider(
                resolved_config.provider_name
            )
            family_profile = resolved_config.family_profile
            effective_parameters = resolved_config.parameters
            family_instruction = (
                resolved_config.family_instruction
            )
            raw_parameters = {}

        self._effective_config = resolved_config
        self._model = model
        self._provider = provider
        self._family_profile = family_profile
        self._family_instruction = family_instruction
        self._parameters = raw_parameters
        self._effective_parameters = dict(
            effective_parameters
        )
        self._prompt_builder = prompt_builder
        self._budget = budget
        self._responses: list[ModelResponse] = []
        self._prompts: list[LayeredPrompt] = []
        self._audit_events: list[
            RepairModelObservation
        ] = []

        json.dumps(
            self._effective_parameters,
            ensure_ascii=False,
            sort_keys=True,
        )

    @property
    def family_profile(self) -> ModelFamilyProfile:
        return self._family_profile

    @property
    def effective_config(
        self,
    ) -> EffectiveModelConfig | None:
        return self._effective_config

    @property
    def budget(self) -> BudgetManager | None:
        return self._budget

    @property
    def records_budget_usage(self) -> bool:
        return self._budget is not None

    @property
    def effective_parameters(self) -> dict[str, Any]:
        return dict(self._effective_parameters)

    @property
    def responses(self) -> tuple[ModelResponse, ...]:
        return tuple(self._responses)

    @property
    def last_response(self) -> ModelResponse | None:
        return self._responses[-1] if self._responses else None

    @property
    def prompts(self) -> tuple[LayeredPrompt, ...]:
        return tuple(self._prompts)

    @property
    def last_prompt(self) -> LayeredPrompt | None:
        return self._prompts[-1] if self._prompts else None

    @property
    def audit_events(
        self,
    ) -> tuple[RepairModelObservation, ...]:
        return tuple(self._audit_events)

    def _with_estimated_cost(
        self,
        response: ModelResponse,
    ) -> ModelResponse:
        config = self._effective_config
        if (
            config is None
            or config.pricing_snapshot is None
        ):
            return response

        snapshot = config.pricing_snapshot
        estimated = estimate_model_cost(
            snapshot,
            response.usage,
            allow_approximate=(
                config.allow_approximate_cost
            ),
        )
        legacy_cost_usd = response.usage.cost_usd
        if (
            legacy_cost_usd is not None
            and estimated.currency not in (None, "USD")
        ):
            raise ValueError(
                "non-USD repair pricing estimate cannot "
                "be combined with provider cost_usd"
            )
        if (
            estimated.amount is not None
            and estimated.currency == "USD"
        ):
            if legacy_cost_usd is not None:
                if (
                    abs(
                        Decimal(str(legacy_cost_usd))
                        - estimated.amount
                    )
                    > Decimal("1e-12")
                ):
                    raise ValueError(
                        "provider cost_usd conflicts with "
                        "the explicit repair pricing snapshot"
                    )
            legacy_cost_usd = float(estimated.amount)

        usage = replace(
            response.usage,
            cost_usd=legacy_cost_usd,
            estimated_cost=estimated,
        )
        metadata = dict(response.metadata)
        metadata.update(
            {
                "pricing_estimation_attempted": True,
                "pricing_estimation_quality": (
                    estimated.quality.value
                ),
                "pricing_snapshot_sha256": (
                    snapshot.pricing_snapshot_sha256
                ),
                "pricing_currency": estimated.currency,
                "pricing_amount_available": (
                    estimated.amount is not None
                ),
            }
        )
        return replace(
            response,
            usage=usage,
            metadata=metadata,
        )

    def repair(
        self,
        request: TestbenchRepairRequest,
    ) -> str:
        if not isinstance(request, TestbenchRepairRequest):
            raise TypeError(
                "request must be a TestbenchRepairRequest"
            )

        prompt = build_testbench_repair_prompt(
            request,
            family_instruction=self._family_instruction,
            family_profile=self._family_profile,
            builder=self._prompt_builder,
        )
        self._prompts.append(prompt)
        self._audit_events.append(
            RepairModelObservation(
                prompt_manifest=prompt.manifest,
                model_call_observed=True,
            )
        )

        if self._budget is not None:
            self._budget.ensure_available(llm_calls=1)
            self._budget.consume(llm_calls=1)

        response = self._provider.generate(
            self._model,
            ModelRequest(
                messages=prompt.messages,
                parameters=dict(
                    self._effective_parameters
                ),
            ),
        )
        if not isinstance(response, ModelResponse):
            raise TypeError(
                "model provider must return a ModelResponse"
            )

        response = self._with_estimated_cost(response)
        self._responses.append(response)
        if self._budget is not None:
            self._budget.record_model_usage(
                response.usage
            )

        self._audit_events[-1] = (
            RepairModelObservation.from_response(
                prompt_manifest=prompt.manifest,
                response=response,
                model_call_observed=True,
            )
        )
        proposed = extract_complete_cpp_block(response.text)

        contract = TestbenchRepairContract.from_request(request)
        issues = contract.validate(proposed)
        if issues:
            raise TestbenchRepairResponseError(
                "repaired testbench violated deterministic "
                "contract: " + "; ".join(issues)
            )
        return proposed
