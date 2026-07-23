"""Deterministic layered prompts built from agent-safe evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any, Protocol, runtime_checkable

from agrefactor.config import TaskSpec
from agrefactor.evidence import (
    FeedbackOwner,
    FeedbackReport,
    FeedbackStage,
)
from agrefactor.models import ChatMessage
from .test_source_isolation import assert_hidden_test_sources_absent


_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:[^/\s]+/)+[^/\s]*"
)
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\(?:[^\\\s]+\\)+[^\\\s]*"
)


def _clean_required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _clean_optional(
    value: str | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string or null"
        )
    cleaned = value.strip()
    return cleaned or None


def _clean_text_sequence(
    value: Sequence[str],
    field_name: str,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(
            f"{field_name} must be a sequence of strings"
        )
    normalized = tuple(
        _clean_required(item, field_name)
        for item in value
    )
    return normalized


def _sanitize_feedback_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None
    sanitized = _WINDOWS_PATH_RE.sub(
        "<absolute-path>",
        value,
    )
    sanitized = _POSIX_PATH_RE.sub(
        "<absolute-path>",
        sanitized,
    )
    return sanitized


class PromptPurpose(str, Enum):
    """Supported repair prompt purposes."""

    TESTBENCH_REPAIR = "testbench_repair"
    CANDIDATE_COMPILE_REPAIR = (
        "candidate_compile_repair"
    )
    CANDIDATE_CSYNTH_REPAIR = (
        "candidate_csynth_repair"
    )
    CANDIDATE_PUBLIC_CSIM_REPAIR = (
        "candidate_public_csim_repair"
    )

    @property
    def expected_owner(self) -> FeedbackOwner:
        if self is PromptPurpose.TESTBENCH_REPAIR:
            return FeedbackOwner.TESTBENCH
        return FeedbackOwner.CANDIDATE

    @property
    def allowed_stages(self) -> frozenset[FeedbackStage]:
        if self is PromptPurpose.TESTBENCH_REPAIR:
            return frozenset(
                {
                    FeedbackStage.STATIC_CHECK,
                    FeedbackStage.COMPILE,
                    FeedbackStage.LINK,
                    FeedbackStage.TEST,
                }
            )
        if self is PromptPurpose.CANDIDATE_COMPILE_REPAIR:
            return frozenset(
                {
                    FeedbackStage.STATIC_CHECK,
                    FeedbackStage.COMPILE,
                    FeedbackStage.LINK,
                }
            )
        if self is PromptPurpose.CANDIDATE_CSYNTH_REPAIR:
            return frozenset(
                {
                    FeedbackStage.CSYNTH,
                }
            )
        return frozenset(
            {
                FeedbackStage.TEST,
                FeedbackStage.CSIM,
            }
        )


@dataclass(frozen=True, slots=True)
class PromptArtifact:
    """One named source artifact exposed to the model."""

    name: str
    content: str
    language: str = "cpp"
    agent_safe: bool = True

    def __post_init__(self) -> None:
        name = _clean_required(
            self.name,
            "PromptArtifact.name",
        )
        if "/" in name or "\\" in name:
            raise ValueError(
                "PromptArtifact.name must be a logical name, "
                "not a path"
            )
        content = _clean_required(
            self.content,
            "PromptArtifact.content",
        )
        language = _clean_required(
            self.language,
            "PromptArtifact.language",
        )
        if not isinstance(self.agent_safe, bool):
            raise TypeError(
                "PromptArtifact.agent_safe must be a boolean"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "language", language)


@dataclass(frozen=True, slots=True)
class ModificationScope:
    """Declare exactly which logical artifacts may change."""

    editable_artifacts: tuple[str, ...]
    read_only_artifacts: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        editable = _clean_text_sequence(
            self.editable_artifacts,
            "ModificationScope.editable_artifacts",
        )
        read_only = _clean_text_sequence(
            self.read_only_artifacts,
            "ModificationScope.read_only_artifacts",
        )
        forbidden = _clean_text_sequence(
            self.forbidden_actions,
            "ModificationScope.forbidden_actions",
        )

        if not editable:
            raise ValueError(
                "ModificationScope must contain at least "
                "one editable artifact"
            )
        if len(set(editable)) != len(editable):
            raise ValueError(
                "editable artifact names must be unique"
            )
        if len(set(read_only)) != len(read_only):
            raise ValueError(
                "read-only artifact names must be unique"
            )
        overlap = set(editable) & set(read_only)
        if overlap:
            raise ValueError(
                "artifacts cannot be both editable and read-only: "
                + ", ".join(sorted(overlap))
            )

        object.__setattr__(
            self,
            "editable_artifacts",
            editable,
        )
        object.__setattr__(
            self,
            "read_only_artifacts",
            read_only,
        )
        object.__setattr__(
            self,
            "forbidden_actions",
            forbidden,
        )


@dataclass(frozen=True, slots=True)
class PromptOutputContract:
    """Describe the required model response shape."""

    artifact_name: str
    language: str = "cpp"
    complete_replacement: bool = True
    fenced_code_block: bool = True
    commentary_allowed: bool = False
    additional_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_name",
            _clean_required(
                self.artifact_name,
                "PromptOutputContract.artifact_name",
            ),
        )
        object.__setattr__(
            self,
            "language",
            _clean_required(
                self.language,
                "PromptOutputContract.language",
            ),
        )
        for field_name in (
            "complete_replacement",
            "fenced_code_block",
            "commentary_allowed",
        ):
            if not isinstance(
                getattr(self, field_name),
                bool,
            ):
                raise TypeError(
                    f"{field_name} must be a boolean"
                )
        object.__setattr__(
            self,
            "additional_requirements",
            _clean_text_sequence(
                self.additional_requirements,
                (
                    "PromptOutputContract."
                    "additional_requirements"
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_name": self.artifact_name,
            "language": self.language,
            "complete_replacement": (
                self.complete_replacement
            ),
            "fenced_code_block": (
                self.fenced_code_block
            ),
            "commentary_allowed": (
                self.commentary_allowed
            ),
            "additional_requirements": list(
                self.additional_requirements
            ),
        }


@runtime_checkable
class FamilyInstructionProfile(Protocol):
    """Prompt-safe structural interface for family instructions."""

    def render_instruction(self) -> str | None:
        ...

    def to_manifest(self) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class LayeredPromptRequest:
    """All explicit inputs used by the shared prompt builder."""

    purpose: PromptPurpose
    task: TaskSpec
    feedback: FeedbackReport
    objective: str
    artifacts: tuple[PromptArtifact, ...]
    modification_scope: ModificationScope
    output_contract: PromptOutputContract
    attempt: int = 1
    max_attempts: int = 1
    family_instruction: str | None = None
    family_profile: FamilyInstructionProfile | None = None
    prior_attempt_summaries: tuple[str, ...] = ()
    approved_memory_snippets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        purpose = self.purpose
        if not isinstance(purpose, PromptPurpose):
            try:
                purpose = PromptPurpose(str(purpose))
            except ValueError as exc:
                raise ValueError(
                    f"unsupported prompt purpose: "
                    f"{self.purpose!r}"
                ) from exc

        if not isinstance(self.task, TaskSpec):
            raise TypeError(
                "LayeredPromptRequest.task must be a TaskSpec"
            )
        if not isinstance(self.feedback, FeedbackReport):
            raise TypeError(
                "LayeredPromptRequest.feedback must be "
                "a FeedbackReport"
            )
        if not isinstance(
            self.modification_scope,
            ModificationScope,
        ):
            raise TypeError(
                "modification_scope must be "
                "a ModificationScope"
            )
        if not isinstance(
            self.output_contract,
            PromptOutputContract,
        ):
            raise TypeError(
                "output_contract must be "
                "a PromptOutputContract"
            )

        artifacts = tuple(self.artifacts)
        if not artifacts:
            raise ValueError(
                "LayeredPromptRequest.artifacts must not "
                "be empty"
            )
        if not all(
            isinstance(item, PromptArtifact)
            for item in artifacts
        ):
            raise TypeError(
                "artifacts must contain only "
                "PromptArtifact values"
            )
        names = [item.name for item in artifacts]
        if len(set(names)) != len(names):
            raise ValueError(
                "artifact names must be unique"
            )

        scope_names = set(
            self.modification_scope.editable_artifacts
        ) | set(
            self.modification_scope.read_only_artifacts
        )
        artifact_names = set(names)
        missing = scope_names - artifact_names
        extra = artifact_names - scope_names
        if missing:
            raise ValueError(
                "scope references missing artifacts: "
                + ", ".join(sorted(missing))
            )
        if extra:
            raise ValueError(
                "artifacts are not declared in scope: "
                + ", ".join(sorted(extra))
            )
        if (
            self.output_contract.artifact_name
            not in self.modification_scope.editable_artifacts
        ):
            raise ValueError(
                "output contract artifact must be editable: "
                + self.output_contract.artifact_name
            )
        for artifact in artifacts:
            if not artifact.agent_safe:
                raise ValueError(
                    "operator-only artifacts cannot enter "
                    "an agent prompt"
                )

        for field_name in ("attempt", "max_attempts"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
            ):
                raise TypeError(
                    f"{field_name} must be an integer"
                )
            if value <= 0:
                raise ValueError(
                    f"{field_name} must be positive"
                )
        if self.attempt > self.max_attempts:
            raise ValueError(
                "attempt must not exceed max_attempts"
            )

        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(
            self,
            "objective",
            _clean_required(
                self.objective,
                "LayeredPromptRequest.objective",
            ),
        )
        object.__setattr__(self, "artifacts", artifacts)
        if (
            self.family_profile is not None
            and not isinstance(
                self.family_profile,
                FamilyInstructionProfile,
            )
        ):
            raise TypeError(
                "LayeredPromptRequest.family_profile must satisfy "
                "FamilyInstructionProfile or be None"
            )
        object.__setattr__(
            self,
            "family_instruction",
            _clean_optional(
                self.family_instruction,
                (
                    "LayeredPromptRequest."
                    "family_instruction"
                ),
            ),
        )
        object.__setattr__(
            self,
            "prior_attempt_summaries",
            _clean_text_sequence(
                self.prior_attempt_summaries,
                (
                    "LayeredPromptRequest."
                    "prior_attempt_summaries"
                ),
            ),
        )
        object.__setattr__(
            self,
            "approved_memory_snippets",
            _clean_text_sequence(
                self.approved_memory_snippets,
                (
                    "LayeredPromptRequest."
                    "approved_memory_snippets"
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class LayeredPrompt:
    """Rendered messages plus a non-sensitive build manifest."""

    messages: tuple[ChatMessage, ...]
    manifest: Mapping[str, Any] = field(
        default_factory=dict
    )
    schema_version: int = 1

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if len(messages) != 2:
            raise ValueError(
                "LayeredPrompt requires exactly two messages"
            )
        if [message.role for message in messages] != [
            "system",
            "user",
        ]:
            raise ValueError(
                "LayeredPrompt roles must be system then user"
            )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version <= 0
        ):
            raise ValueError(
                "schema_version must be a positive integer"
            )
        try:
            manifest = json.loads(
                json.dumps(
                    dict(self.manifest),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "manifest must be finite "
                "JSON-serializable data"
            ) from exc

        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "manifest", manifest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in self.messages
            ],
            "manifest": dict(self.manifest),
        }


def _compose_family_instruction(
    request: LayeredPromptRequest,
) -> tuple[str | None, str]:
    parts: list[str] = []
    source_parts: list[str] = []
    if request.family_profile is not None:
        rendered = request.family_profile.render_instruction()
        if rendered is not None:
            parts.append(rendered)
            source_parts.append("profile")
    if request.family_instruction is not None:
        parts.append(request.family_instruction)
        source_parts.append("explicit")
    return (
        "\n\n".join(parts) if parts else None,
        "+".join(source_parts) if source_parts else "none",
    )


class SharedLayeredPromptBuilder:
    """Build deterministic prompts from safe structured inputs."""

    builder_version = 1

    def build(
        self,
        request: LayeredPromptRequest,
    ) -> LayeredPrompt:
        if not isinstance(request, LayeredPromptRequest):
            raise TypeError(
                "request must be a LayeredPromptRequest"
            )

        self._validate_feedback(request)
        family_instruction, family_source = (
            _compose_family_instruction(request)
        )
        system_prompt = self._build_system_prompt(
            request,
            family_instruction=family_instruction,
        )
        user_prompt = self._build_user_prompt(request)
        assert_hidden_test_sources_absent(
            task=request.task,
            messages=(system_prompt, user_prompt),
            artifacts=request.artifacts,
        )

        manifest = {
            "builder_version": self.builder_version,
            "purpose": request.purpose.value,
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "target_profile": request.task.target.name,
            "feedback_source": request.feedback.source,
            "feedback_item_count": len(
                request.feedback.items
            ),
            "editable_artifacts": list(
                request.modification_scope.editable_artifacts
            ),
            "read_only_artifacts": list(
                request.modification_scope.read_only_artifacts
            ),
            "attempt": request.attempt,
            "max_attempts": request.max_attempts,
            "family_instruction_present": (
                family_instruction is not None
            ),
            "family_instruction_source": family_source,
            "model_family_profile": (
                None
                if request.family_profile is None
                else request.family_profile.to_manifest()
            ),
            "prior_attempt_count": len(
                request.prior_attempt_summaries
            ),
            "approved_memory_count": len(
                request.approved_memory_snippets
            ),
            "output_contract": (
                request.output_contract.to_dict()
            ),
            "feedback_projection": (
                "agent_safe_items_only"
            ),
            "hidden_test_source_isolation": "verified",
        }

        return LayeredPrompt(
            messages=(
                ChatMessage(
                    role="system",
                    content=system_prompt,
                ),
                ChatMessage(
                    role="user",
                    content=user_prompt,
                ),
            ),
            manifest=manifest,
        )

    def _validate_feedback(
        self,
        request: LayeredPromptRequest,
    ) -> None:
        report = request.feedback
        metadata = report.metadata

        if metadata.get("evidence_view") != "agent_safe":
            raise ValueError(
                "prompt feedback must use the agent_safe "
                "evidence view"
            )
        if metadata.get(
            "feedback_visible_to_agent"
        ) is False:
            raise ValueError(
                "feedback marked invisible to the agent "
                "cannot enter a prompt"
            )

        split = metadata.get(
            "evaluation_split",
            metadata.get("split"),
        )
        if split is not None and str(split).lower() == "hidden":
            raise ValueError(
                "hidden feedback cannot enter a model prompt"
            )

        blocking = tuple(
            item for item in report.items if item.blocking
        )
        if not blocking:
            raise ValueError(
                "repair prompts require at least one "
                "blocking feedback item"
            )

        expected_owner = request.purpose.expected_owner
        invalid_owners = {
            item.owner.value
            for item in blocking
            if item.owner is not expected_owner
        }
        if invalid_owners:
            raise ValueError(
                "blocking feedback owner does not match "
                f"{request.purpose.value}: "
                + ", ".join(sorted(invalid_owners))
            )

        invalid_stages = {
            item.stage.value
            for item in blocking
            if item.stage
            not in request.purpose.allowed_stages
        }
        if invalid_stages:
            raise ValueError(
                "blocking feedback stage does not match "
                f"{request.purpose.value}: "
                + ", ".join(sorted(invalid_stages))
            )

        if (
            request.purpose
            is PromptPurpose.CANDIDATE_PUBLIC_CSIM_REPAIR
        ):
            if str(split).lower() != "public":
                raise ValueError(
                    "public CSIM repair requires an "
                    "explicit public feedback split"
                )
            if metadata.get(
                "feedback_visible_to_agent"
            ) is not True:
                raise ValueError(
                    "public CSIM repair feedback must be "
                    "explicitly visible to the agent"
                )

    def _build_system_prompt(
        self,
        request: LayeredPromptRequest,
        *,
        family_instruction: str | None,
    ) -> str:
        scope = request.modification_scope
        contract = request.output_contract

        lines = [
            "You are an AgRefactor++ HLS repair component.",
            "",
            "System invariants:",
            "- Correctness evidence takes priority over "
            "optimization or convenience.",
            "- Use only the structured agent-safe feedback "
            "provided in this request.",
            "- Never infer or request hidden evaluation "
            "content.",
            "- Modify only explicitly editable artifacts.",
            "- Treat read-only artifacts as context, never "
            "as editable output.",
            "- Do not weaken tests, remove required behavior, "
            "or fabricate tool success.",
            "",
            "Current purpose:",
            f"- {request.purpose.value}",
            "",
            "Modification scope:",
            "- Editable artifacts: "
            + ", ".join(scope.editable_artifacts),
            "- Read-only artifacts: "
            + (
                ", ".join(scope.read_only_artifacts)
                if scope.read_only_artifacts
                else "none"
            ),
        ]

        if scope.forbidden_actions:
            lines.append("- Forbidden actions:")
            lines.extend(
                f"  - {item}"
                for item in scope.forbidden_actions
            )

        if family_instruction is not None:
            lines.extend(
                [
                    "",
                    "Model-family instruction:",
                    family_instruction,
                ]
            )

        lines.extend(
            [
                "",
                "Output contract:",
                f"- Artifact: {contract.artifact_name}",
                f"- Language: {contract.language}",
                "- Complete replacement: "
                + str(
                    contract.complete_replacement
                ).lower(),
                "- Fenced code block: "
                + str(
                    contract.fenced_code_block
                ).lower(),
                "- Commentary allowed: "
                + str(
                    contract.commentary_allowed
                ).lower(),
            ]
        )
        lines.extend(
            f"- {item}"
            for item in contract.additional_requirements
        )

        return "\n".join(lines)

    def _build_user_prompt(
        self,
        request: LayeredPromptRequest,
    ) -> str:
        task_payload = {
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "mode": request.task.mode.value,
            "target": request.task.target.to_dict(),
        }

        feedback_payload = {
            "schema_version": (
                request.feedback.schema_version
            ),
            "source": request.feedback.source,
            "items": [
                {
                    "stage": item.stage.value,
                    "category": item.category.value,
                    "severity": item.severity.value,
                    "owner": item.owner.value,
                    "summary": _sanitize_feedback_text(
                        item.summary
                    ),
                    "detail": _sanitize_feedback_text(
                        item.detail
                    ),
                    "source": item.source,
                    "blocking": item.blocking,
                }
                for item in request.feedback.items
            ],
        }

        sections = [
            "Task contract:",
            "```json",
            json.dumps(
                task_payload,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "```",
            "",
            "Repair objective:",
            request.objective,
            "",
            "Attempt:",
            (
                f"{request.attempt} of "
                f"{request.max_attempts}"
            ),
            "",
            "Agent-safe structured feedback:",
            "```json",
            json.dumps(
                feedback_payload,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "```",
            "",
            "Prior attempt summaries:",
        ]

        if request.prior_attempt_summaries:
            sections.extend(
                f"- {item}"
                for item in request.prior_attempt_summaries
            )
        else:
            sections.append("- none")

        sections.extend(
            [
                "",
                (
                    "Approved memory snippets "
                    "(already gated by the caller):"
                ),
            ]
        )
        if request.approved_memory_snippets:
            sections.extend(
                f"- {item}"
                for item in request.approved_memory_snippets
            )
        else:
            sections.append("- none")

        artifact_by_name = {
            artifact.name: artifact
            for artifact in request.artifacts
        }
        sections.extend(["", "Artifacts:"])

        for name in (
            request.modification_scope.editable_artifacts
        ):
            artifact = artifact_by_name[name]
            sections.extend(
                [
                    "",
                    f"Editable artifact: {name}",
                    f"```{artifact.language}",
                    artifact.content,
                    "```",
                ]
            )

        for name in (
            request.modification_scope.read_only_artifacts
        ):
            artifact = artifact_by_name[name]
            sections.extend(
                [
                    "",
                    f"Read-only artifact: {name}",
                    f"```{artifact.language}",
                    artifact.content,
                    "```",
                ]
            )

        return "\n".join(sections)
