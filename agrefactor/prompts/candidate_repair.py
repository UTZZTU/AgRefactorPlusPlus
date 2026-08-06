"""Deterministic prompt policies for candidate-only HLS repair."""

from __future__ import annotations

from dataclasses import dataclass

from agrefactor.config import TaskSpec
from agrefactor.evidence import FeedbackReport

from .layered import (
    FamilyInstructionProfile,
    LayeredPrompt,
    LayeredPromptRequest,
    ModificationScope,
    PromptArtifact,
    PromptOutputContract,
    PromptPurpose,
    SharedLayeredPromptBuilder,
)


_CANDIDATE_ARTIFACT = "candidate_kernel"
_ORIGINAL_ARTIFACT = "original_program"
_PUBLIC_TESTBENCH_ARTIFACT = "public_testbench"

_COMMON_FORBIDDEN_ACTIONS = (
    "Never modify or propose changes to the original program.",
    "Never modify or propose changes to the Public testbench.",
    (
        "Never delete, rename, or change the candidate top-level "
        "public interface, function name, parameter contract, return "
        "contract, or required C/C++ language linkage."
    ),
    (
        "Do not weaken, remove, bypass, or special-case validation "
        "calls, assertions, comparisons, seeds, macros, or required "
        "behavior."
    ),
    (
        "Do not fabricate compiler, CSIM, CSYNTH, pragma, timing, "
        "resource, or success evidence."
    ),
    (
        "Do not delete required functionality or observable behavior "
        "to make the reported failure disappear."
    ),
    (
        "Do not infer, request, encode, or depend on Hidden evaluation "
        "identifiers, inputs, diagnostics, paths, or artifacts."
    ),
)

_COMMON_OUTPUT_REQUIREMENTS = (
    (
        "Preserve the declared top-level function name, public "
        "interface, required linkage, and externally observable "
        "behavior."
    ),
    (
        "Return exactly one complete replacement candidate C++ "
        "artifact in one fenced code block with no commentary."
    ),
    "Return no patch, diff, partial edit, explanation, testbench, or tool transcript.",
)

_COMPILE_FORBIDDEN_ACTIONS = _COMMON_FORBIDDEN_ACTIONS + (
    (
        "Do not add stubs, wrappers, duplicate top-level definitions, "
        "or test-specific branches to bypass compile or link evidence."
    ),
    (
        "Do not repair issues that are unsupported by the structured "
        "static-check, compile, or link feedback."
    ),
)

_CSYNTH_FORBIDDEN_ACTIONS = _COMMON_FORBIDDEN_ACTIONS + (
    (
        "Do not use simulation-only branches, unsupported constructs, "
        "or interface deletion to evade synthesis."
    ),
    (
        "Do not claim that a pragma, loop transformation, resource "
        "binding, or timing change succeeded without later tool evidence."
    ),
)

_PUBLIC_COSIM_FORBIDDEN_ACTIONS = _COMMON_FORBIDDEN_ACTIONS + (
    (
        "Do not hard-code RTL protocol timing, transaction counts, "
        "expected outputs, or Public Testbench behavior into the candidate."
    ),
    (
        "Do not remove, bypass, or weaken handshake, stream, memory, or "
        "interface behavior merely to silence the observed RTL COSIM failure."
    ),
)

_PUBLIC_CSIM_FORBIDDEN_ACTIONS = _COMMON_FORBIDDEN_ACTIONS + (
    (
        "Do not hard-code Public inputs, expected outputs, case counts, "
        "or diagnostic text into the candidate."
    ),
    (
        "Do not modify the Public testbench or reduce the generality of "
        "the candidate merely to satisfy the observed Public cases."
    ),
)


@dataclass(frozen=True, slots=True)
class CandidateRepairPromptInputs:
    """Immutable caller-owned data for one candidate repair prompt."""

    task: TaskSpec
    feedback: FeedbackReport
    candidate_code: str
    original_code: str
    public_testbench_code: str | None = None
    attempt: int = 1
    max_attempts: int = 1
    family_instruction: str | None = None
    family_profile: FamilyInstructionProfile | None = None
    prior_attempt_summaries: tuple[str, ...] = ()
    approved_memory_snippets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskSpec):
            raise TypeError(
                "CandidateRepairPromptInputs.task must be a TaskSpec"
            )
        if not isinstance(self.feedback, FeedbackReport):
            raise TypeError(
                "CandidateRepairPromptInputs.feedback must be a "
                "FeedbackReport"
            )

        _validate_required_text(self.candidate_code, "candidate_code")
        _validate_required_text(self.original_code, "original_code")
        _validate_optional_text(
            self.public_testbench_code,
            "public_testbench_code",
        )
        _validate_optional_text(
            self.family_instruction,
            "family_instruction",
        )
        if (
            self.family_profile is not None
            and not isinstance(
                self.family_profile,
                FamilyInstructionProfile,
            )
        ):
            raise TypeError(
                "family_profile must satisfy "
                "FamilyInstructionProfile or be None"
            )
        _validate_text_tuple(
            self.prior_attempt_summaries,
            "prior_attempt_summaries",
        )
        _validate_text_tuple(
            self.approved_memory_snippets,
            "approved_memory_snippets",
        )

        for field_name in ("attempt", "max_attempts"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt must not exceed max_attempts")


@dataclass(frozen=True, slots=True)
class _CandidateRepairPromptSpec:
    purpose: PromptPurpose
    objective: str
    forbidden_actions: tuple[str, ...]
    require_public_testbench: bool


_COMPILE_SPEC = _CandidateRepairPromptSpec(
    purpose=PromptPurpose.CANDIDATE_COMPILE_REPAIR,
    objective=(
        "Repair only the candidate implementation using the structured "
        "agent-safe static-check, compile, or link evidence."
    ),
    forbidden_actions=_COMPILE_FORBIDDEN_ACTIONS,
    require_public_testbench=False,
)

_CSYNTH_SPEC = _CandidateRepairPromptSpec(
    purpose=PromptPurpose.CANDIDATE_CSYNTH_REPAIR,
    objective=(
        "Repair only the candidate implementation so it can be "
        "synthesized under the explicit effective TargetProfile, using "
        "only the structured agent-safe CSYNTH evidence."
    ),
    forbidden_actions=_CSYNTH_FORBIDDEN_ACTIONS,
    require_public_testbench=False,
)

_PUBLIC_COSIM_SPEC = _CandidateRepairPromptSpec(
    purpose=PromptPurpose.CANDIDATE_PUBLIC_COSIM_REPAIR,
    objective=(
        "Repair only the candidate implementation to address deterministic "
        "candidate-owned Public RTL COSIM evidence. The proposal remains "
        "untrusted until the entire validation chain is restarted."
    ),
    forbidden_actions=_PUBLIC_COSIM_FORBIDDEN_ACTIONS,
    require_public_testbench=True,
)

_PUBLIC_CSIM_SPEC = _CandidateRepairPromptSpec(
    purpose=PromptPurpose.CANDIDATE_PUBLIC_CSIM_REPAIR,
    objective=(
        "Repair only the candidate implementation to address the "
        "candidate-owned Public CSIM or test failure while preserving "
        "the general task contract."
    ),
    forbidden_actions=_PUBLIC_CSIM_FORBIDDEN_ACTIONS,
    require_public_testbench=True,
)


def build_candidate_compile_repair_prompt(
    inputs: CandidateRepairPromptInputs,
    *,
    builder: SharedLayeredPromptBuilder | None = None,
) -> LayeredPrompt:
    """Build a candidate static-check, compile, or link repair prompt."""

    return _build_candidate_repair_prompt(
        inputs,
        _COMPILE_SPEC,
        builder=builder,
    )


def build_candidate_csynth_repair_prompt(
    inputs: CandidateRepairPromptInputs,
    *,
    builder: SharedLayeredPromptBuilder | None = None,
) -> LayeredPrompt:
    """Build a candidate CSYNTH repair prompt."""

    return _build_candidate_repair_prompt(
        inputs,
        _CSYNTH_SPEC,
        builder=builder,
    )


def build_candidate_public_csim_repair_prompt(
    inputs: CandidateRepairPromptInputs,
    *,
    builder: SharedLayeredPromptBuilder | None = None,
) -> LayeredPrompt:
    """Build a candidate Public CSIM or test repair prompt."""

    return _build_candidate_repair_prompt(
        inputs,
        _PUBLIC_CSIM_SPEC,
        builder=builder,
    )


def build_candidate_public_cosim_repair_prompt(
    inputs: CandidateRepairPromptInputs,
    *,
    builder: SharedLayeredPromptBuilder | None = None,
) -> LayeredPrompt:
    """Build a candidate Public RTL COSIM repair prompt."""

    return _build_candidate_repair_prompt(
        inputs,
        _PUBLIC_COSIM_SPEC,
        builder=builder,
    )


def _build_candidate_repair_prompt(
    inputs: CandidateRepairPromptInputs,
    spec: _CandidateRepairPromptSpec,
    *,
    builder: SharedLayeredPromptBuilder | None,
) -> LayeredPrompt:
    if not isinstance(inputs, CandidateRepairPromptInputs):
        raise TypeError("inputs must be CandidateRepairPromptInputs")
    if not isinstance(spec, _CandidateRepairPromptSpec):
        raise TypeError("spec must be a _CandidateRepairPromptSpec")
    if builder is None:
        builder = SharedLayeredPromptBuilder()
    elif not isinstance(builder, SharedLayeredPromptBuilder):
        raise TypeError(
            "builder must be a SharedLayeredPromptBuilder or None"
        )

    if (
        spec.require_public_testbench
        and inputs.public_testbench_code is None
    ):
        raise ValueError(
            "Public CSIM repair requires public_testbench_code"
        )

    artifacts = [
        PromptArtifact(
            name=_CANDIDATE_ARTIFACT,
            content=inputs.candidate_code,
        ),
        PromptArtifact(
            name=_ORIGINAL_ARTIFACT,
            content=inputs.original_code,
        ),
    ]
    read_only = [_ORIGINAL_ARTIFACT]

    if inputs.public_testbench_code is not None:
        artifacts.append(
            PromptArtifact(
                name=_PUBLIC_TESTBENCH_ARTIFACT,
                content=inputs.public_testbench_code,
            )
        )
        read_only.append(_PUBLIC_TESTBENCH_ARTIFACT)

    request = LayeredPromptRequest(
        purpose=spec.purpose,
        task=inputs.task,
        feedback=inputs.feedback,
        objective=spec.objective,
        artifacts=tuple(artifacts),
        modification_scope=ModificationScope(
            editable_artifacts=(_CANDIDATE_ARTIFACT,),
            read_only_artifacts=tuple(read_only),
            forbidden_actions=spec.forbidden_actions,
        ),
        output_contract=PromptOutputContract(
            artifact_name=_CANDIDATE_ARTIFACT,
            language="cpp",
            complete_replacement=True,
            fenced_code_block=True,
            commentary_allowed=False,
            additional_requirements=_COMMON_OUTPUT_REQUIREMENTS,
        ),
        attempt=inputs.attempt,
        max_attempts=inputs.max_attempts,
        family_instruction=inputs.family_instruction,
        family_profile=inputs.family_profile,
        prior_attempt_summaries=inputs.prior_attempt_summaries,
        approved_memory_snippets=inputs.approved_memory_snippets,
    )
    return builder.build(request)


def _validate_required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _validate_optional_text(
    value: str | None,
    field_name: str,
) -> None:
    if value is None:
        return
    _validate_required_text(value, field_name)


def _validate_text_tuple(
    value: tuple[str, ...],
    field_name: str,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple of strings")
    for item in value:
        _validate_required_text(item, field_name)
