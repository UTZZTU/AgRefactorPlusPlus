"""Deterministic, agent-safe layered prompts for Stage 3 optimization.

S3.4 introduces only the Structural level.  These builders deliberately do not
reuse repair-only feedback contracts: optimization prompts are hypothesis
oriented, have no Hidden evidence, and expose exactly one complete source
artifact when a rewrite is requested.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any

from agrefactor.config import RunMode, TaskSpec
from agrefactor.models import ChatMessage
from agrefactor.optimization.state import HypothesisRecord, OptimizationLevel

from .layered import LayeredPrompt
from .test_source_isolation import assert_hidden_test_sources_absent


OPTIMIZATION_PROMPT_SCHEMA_VERSION = 1
STRUCTURAL_HYPOTHESIS_PURPOSE = "optimizer_structural_hypothesis"
STRUCTURAL_REWRITE_PURPOSE = "optimizer_structural_rewrite"


@dataclass(frozen=True, slots=True)
class StructuralHypothesisPromptRequest:
    """Inputs for one bounded Structural hypothesis model call."""

    task: TaskSpec
    parent_candidate_id: str
    parent_source: str
    round_number: int
    max_hypotheses: int
    supporting_evidence_ids: tuple[str, ...] = ()
    safe_context: Mapping[str, Any] = field(default_factory=dict)
    family_instruction: str | None = None

    schema_version = OPTIMIZATION_PROMPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_task(self.task)
        object.__setattr__(
            self,
            "parent_candidate_id",
            _required_text(self.parent_candidate_id, "parent_candidate_id"),
        )
        object.__setattr__(
            self,
            "parent_source",
            _required_source(self.parent_source, "parent_source"),
        )
        if isinstance(self.round_number, bool) or self.round_number < 1:
            raise ValueError("round_number must be positive")
        if isinstance(self.max_hypotheses, bool) or not 1 <= self.max_hypotheses <= 3:
            raise ValueError("max_hypotheses must be between 1 and 3")
        evidence = _clean_text_tuple(
            self.supporting_evidence_ids,
            "supporting_evidence_ids",
            allow_empty=True,
        )
        safe_context = _json_object(self.safe_context, "safe_context")
        _reject_agent_unsafe(safe_context, "safe_context")
        family = _optional_text(self.family_instruction, "family_instruction")
        object.__setattr__(self, "supporting_evidence_ids", evidence)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "family_instruction", family)


@dataclass(frozen=True, slots=True)
class StructuralRewritePromptRequest:
    """Inputs for one complete-source Structural rewrite model call."""

    task: TaskSpec
    candidate_id: str
    parent_candidate_id: str
    parent_source: str
    hypothesis: HypothesisRecord
    safe_context: Mapping[str, Any] = field(default_factory=dict)
    family_instruction: str | None = None

    schema_version = OPTIMIZATION_PROMPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_task(self.task)
        candidate_id = _required_text(self.candidate_id, "candidate_id")
        parent_id = _required_text(self.parent_candidate_id, "parent_candidate_id")
        source = _required_source(self.parent_source, "parent_source")
        if not isinstance(self.hypothesis, HypothesisRecord):
            raise TypeError("hypothesis must be HypothesisRecord")
        if self.hypothesis.level is not OptimizationLevel.STRUCTURAL:
            raise ValueError("S3.4 rewrite requires a Structural hypothesis")
        if self.hypothesis.parent_candidate_id != parent_id:
            raise ValueError("hypothesis parent does not match parent_candidate_id")
        safe_context = _json_object(self.safe_context, "safe_context")
        _reject_agent_unsafe(safe_context, "safe_context")
        family = _optional_text(self.family_instruction, "family_instruction")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "parent_candidate_id", parent_id)
        object.__setattr__(self, "parent_source", source)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "family_instruction", family)


class StructuralOptimizationPromptBuilder:
    """Build the two S3.4 model-facing prompts with stable identities."""

    def build_hypothesis(
        self,
        request: StructuralHypothesisPromptRequest,
    ) -> LayeredPrompt:
        if not isinstance(request, StructuralHypothesisPromptRequest):
            raise TypeError("request must be StructuralHypothesisPromptRequest")
        system = self._hypothesis_system(request)
        user = self._hypothesis_user(request)
        prompt = _finalize_prompt(
            system=system,
            user=user,
            manifest={
                "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
                "purpose": STRUCTURAL_HYPOTHESIS_PURPOSE,
                "task_id": request.task.task_id,
                "kernel_name": request.task.kernel_name,
                "target_profile": request.task.target.name,
                "mode": request.task.mode.value,
                "level": OptimizationLevel.STRUCTURAL.value,
                "round_number": request.round_number,
                "parent_candidate_id": request.parent_candidate_id,
                "parent_source_sha256": _text_sha256(request.parent_source),
                "max_hypotheses": request.max_hypotheses,
                "supporting_evidence_ids": list(request.supporting_evidence_ids),
                "safe_context": request.safe_context,
                "family_instruction_present": request.family_instruction is not None,
                "feedback_projection": "agent_safe_only",
                "hidden_test_source_isolation": "verified",
                "output_contract": {
                    "format": "json_object",
                    "schema": "structural_hypothesis_response_v1",
                    "commentary_allowed": False,
                    "max_hypotheses": request.max_hypotheses,
                },
            },
        )
        assert_hidden_test_sources_absent(task=request.task, messages=prompt.messages)
        return prompt

    def build_rewrite(
        self,
        request: StructuralRewritePromptRequest,
    ) -> LayeredPrompt:
        if not isinstance(request, StructuralRewritePromptRequest):
            raise TypeError("request must be StructuralRewritePromptRequest")
        system = self._rewrite_system(request)
        user = self._rewrite_user(request)
        prompt = _finalize_prompt(
            system=system,
            user=user,
            manifest={
                "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
                "purpose": STRUCTURAL_REWRITE_PURPOSE,
                "task_id": request.task.task_id,
                "kernel_name": request.task.kernel_name,
                "target_profile": request.task.target.name,
                "mode": request.task.mode.value,
                "level": OptimizationLevel.STRUCTURAL.value,
                "candidate_id": request.candidate_id,
                "parent_candidate_id": request.parent_candidate_id,
                "parent_source_sha256": _text_sha256(request.parent_source),
                "hypothesis_id": request.hypothesis.hypothesis_id,
                "hypothesis_sha256": _mapping_sha256(request.hypothesis.to_dict()),
                "safe_context": request.safe_context,
                "family_instruction_present": request.family_instruction is not None,
                "feedback_projection": "agent_safe_only",
                "hidden_test_source_isolation": "verified",
                "output_contract": {
                    "artifact_name": "candidate_kernel",
                    "language": "cpp",
                    "complete_replacement": True,
                    "fenced_code_block": True,
                    "commentary_allowed": False,
                    "top_function_interface_must_remain_unchanged": True,
                },
            },
        )
        assert_hidden_test_sources_absent(task=request.task, messages=prompt.messages)
        return prompt

    @staticmethod
    def _hypothesis_system(request: StructuralHypothesisPromptRequest) -> str:
        lines = [
            "You are the AgRefactor++ Stage 3 Structural optimization hypothesis component.",
            "",
            "System invariants:",
            "- Correctness is more important than performance.",
            "- Use only the parent source, task contract, target profile, and agent-safe context supplied here.",
            "- Never infer, request, or mention Hidden evaluation content.",
            "- Propose causal Structural changes: algorithms, loop organization, function boundaries, data layout, memory access order, local buffering, producer/consumer structure, or dataflow structure.",
            "- Do not disguise a pragma-only edit as a Structural hypothesis.",
            "- Do not claim that compilation, simulation, synthesis, or PPA improvement has already succeeded.",
            "- Return strict JSON only; no Markdown and no commentary.",
            "",
            "Output JSON contract:",
            '{"schema_version":1,"hypotheses":[{"claim":"...","expected_benefit":{"metric":"latency","direction":"decrease"},"risk":"low|medium|high","modification_scope":["..."],"verification_plan":["preflight","public","csynth","hidden"]}]}',
            f"- Return at most {request.max_hypotheses} hypotheses in priority order.",
            "- Each modification_scope and verification_plan must be non-empty.",
            "- Use exactly the keys shown above; do not add identifiers or evidence not present in the request.",
        ]
        if request.family_instruction:
            lines.extend(["", "Model-family instruction:", request.family_instruction])
        return "\n".join(lines)

    @staticmethod
    def _hypothesis_user(request: StructuralHypothesisPromptRequest) -> str:
        task_payload = {
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "mode": request.task.mode.value,
            "target": request.task.target.to_effective_dict(),
            "objective": "latency",
            "level": "structural",
            "round_number": request.round_number,
            "parent_candidate_id": request.parent_candidate_id,
            "supporting_evidence_ids": list(request.supporting_evidence_ids),
            "safe_context": request.safe_context,
        }
        return "\n".join(
            [
                "Task and policy contract:",
                "```json",
                _pretty_json(task_payload),
                "```",
                "",
                "Parent candidate source (read-only):",
                "```cpp",
                request.parent_source,
                "```",
                "",
                "Return the strict JSON object now.",
            ]
        )

    @staticmethod
    def _rewrite_system(request: StructuralRewritePromptRequest) -> str:
        lines = [
            "You are the AgRefactor++ Stage 3 Structural complete-source generator.",
            "",
            "System invariants:",
            "- Implement only the selected causal hypothesis.",
            "- Preserve functional behavior and the exact top-function interface.",
            "- Return the complete replacement translation unit, not a patch, diff, excerpt, or explanation.",
            "- Do not define main unless the top function itself is main.",
            "- Never weaken tests or fabricate compile, simulation, synthesis, or PPA success.",
            "- Never infer, request, or mention Hidden evaluation content.",
            "- Structural intent is carried by the explicit hypothesis; no static string matcher will certify the edit.",
            "- Return exactly one fenced C++ block and no text outside it.",
        ]
        if request.family_instruction:
            lines.extend(["", "Model-family instruction:", request.family_instruction])
        return "\n".join(lines)

    @staticmethod
    def _rewrite_user(request: StructuralRewritePromptRequest) -> str:
        payload = {
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "mode": request.task.mode.value,
            "target": request.task.target.to_effective_dict(),
            "candidate_id": request.candidate_id,
            "parent_candidate_id": request.parent_candidate_id,
            "hypothesis": request.hypothesis.to_dict(),
            "safe_context": request.safe_context,
        }
        return "\n".join(
            [
                "Selected Structural change contract:",
                "```json",
                _pretty_json(payload),
                "```",
                "",
                "Current complete source:",
                "```cpp",
                request.parent_source,
                "```",
                "",
                "Return exactly one complete replacement C++ source block.",
            ]
        )


def _finalize_prompt(*, system: str, user: str, manifest: Mapping[str, Any]) -> LayeredPrompt:
    base = _json_object(manifest, "manifest")
    identity_payload = {
        "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "manifest": base,
    }
    base["prompt_identity_sha256"] = _mapping_sha256(identity_payload)
    return LayeredPrompt(
        messages=(ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)),
        manifest=base,
    )


def _validate_task(task: TaskSpec) -> None:
    if not isinstance(task, TaskSpec):
        raise TypeError("task must be TaskSpec")
    if task.mode not in {RunMode.OPTIMIZE, RunMode.FULL}:
        raise ValueError("optimization prompts require mode optimize or full")


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in cleaned:
        raise ValueError(f"{name} must not contain NUL")
    return cleaned



def _required_source(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value

def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _clean_text_tuple(value: Sequence[str], name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of strings")
    result = tuple(_required_text(item, name) for item in value)
    if not allow_empty and not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _json_object(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        copied = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite JSON data") from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def _reject_agent_unsafe(value: Any, path: str) -> None:
    forbidden = {
        "hidden",
        "hidden_diagnostic",
        "hidden_report",
        "hidden_testbench",
        "operator_full",
        "private_testbench",
        "secret",
        "api_key",
        "access_token",
        "password",
        "authorization",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden or normalized.startswith("hidden_"):
                raise ValueError(f"{path} contains agent-unsafe key: {key}")
            _reject_agent_unsafe(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _reject_agent_unsafe(item, f"{path}[{index}]")


def _pretty_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
