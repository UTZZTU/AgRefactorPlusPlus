"""Deterministic, agent-safe layered prompts for Stage 3 optimization.

Stage 3 optimization prompts are level-specific and deliberately do not reuse
repair-only feedback contracts: they are hypothesis oriented, have no Hidden
evidence, and expose exactly one complete source artifact when a rewrite is
requested. S3.6 adds a typed Pragma action contract without any source-string
or pragma-count authority gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any

from agrefactor.config import RunMode, TaskSpec
from agrefactor.models import ChatMessage

from .layered import LayeredPrompt
from .test_source_isolation import assert_hidden_test_sources_absent

if TYPE_CHECKING:
    from agrefactor.optimization.state import HypothesisRecord


OPTIMIZATION_PROMPT_SCHEMA_VERSION = 2
CANDIDATE_REWRITE_ABSTENTION_TOKEN = "AGREFACTOR_ABSTAIN"
STRUCTURAL_HYPOTHESIS_PURPOSE = "optimizer_structural_hypothesis"
STRUCTURAL_REWRITE_PURPOSE = "optimizer_structural_rewrite"
BOTTLENECK_ANALYSIS_PURPOSE = "optimizer_bottleneck_analysis"
BOTTLENECK_REWRITE_PURPOSE = "optimizer_bottleneck_rewrite"
PRAGMA_ANALYSIS_PURPOSE = "optimizer_pragma_analysis"
PRAGMA_REWRITE_PURPOSE = "optimizer_pragma_rewrite"
BOTTLENECK_ALLOWED_SIGNAL_FIELDS = (
    "latency_cycles_min",
    "latency_cycles_max",
    "initiation_interval_min",
    "initiation_interval_max",
    "target_clock_period_ns",
    "achieved_clock_period_ns",
    "max_resource_utilization_ratio",
    "objective_feasible",
    "constraint_violations",
    "parser_warnings",
    "resources_used.bram_18k",
    "resources_used.dsp",
    "resources_used.ff",
    "resources_used.lut",
    "resources_used.uram",
    "resources_available.bram_18k",
    "resources_available.dsp",
    "resources_available.ff",
    "resources_available.lut",
    "resources_available.uram",
)
PRAGMA_ALLOWED_SIGNAL_FIELDS = BOTTLENECK_ALLOWED_SIGNAL_FIELDS
PRAGMA_ALLOWED_KINDS = (
    "pipeline",
    "unroll",
    "array_partition",
    "dataflow",
    "inline",
    "bind_storage",
    "bind_op",
    "unknown",
)
PRAGMA_ALLOWED_TARGET_KINDS = (
    "function",
    "loop",
    "array",
    "operation",
    "region",
    "unknown",
)


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
        _require_hypothesis_level(self.hypothesis, "structural")
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
                "level": "structural",
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
                "level": "structural",
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
                    "fenced_code_block_preferred": True,
                    "raw_complete_source_allowed": True,
                    "commentary_allowed": False,
                    "explicit_abstention_token": CANDIDATE_REWRITE_ABSTENTION_TOKEN,
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
            "- Structural planning must not add, remove, or modify HLS pragmas/directives; directive ownership belongs to the later Pragma level.",
            "- Emit a hypothesis only when it is implementable as a concrete source-only change under these invariants; otherwise return an empty hypotheses array.",
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
            "- Do not add, remove, or modify HLS pragmas/directives; Structural rewrite owns source structure, while the later Pragma level owns directive edits.",
            "- Preserve functional behavior and the exact top-function interface.",
            "- Return the complete replacement translation unit, not a patch, diff, excerpt, or explanation.",
            "- Do not define main unless the top function itself is main.",
            "- Never weaken tests or fabricate compile, simulation, synthesis, or PPA success.",
            "- Never infer, request, or mention Hidden evaluation content.",
            "- Structural intent is carried by the explicit hypothesis; no static string matcher will certify the edit.",
            "- Prefer exactly one fenced C++ block. A raw complete translation unit with no prose is also accepted.",
            f"- If the selected change cannot be implemented safely and non-trivially, return exactly {CANDIDATE_REWRITE_ABSTENTION_TOKEN} and nothing else.",
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
                f"Return one complete changed C++ translation unit, or exactly {CANDIDATE_REWRITE_ABSTENTION_TOKEN} when safe implementation is unavailable.",
            ]
        )



@dataclass(frozen=True, slots=True)
class BottleneckAnalysisPromptRequest:
    """Inputs for one bounded Bottleneck classification/hypothesis call."""

    task: TaskSpec
    parent_candidate_id: str
    parent_source: str
    round_number: int
    max_classifications: int
    max_hypotheses: int
    evidence: Mapping[str, Any]
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
        for name in ("max_classifications", "max_hypotheses"):
            value = getattr(self, name)
            if isinstance(value, bool) or not 1 <= value <= 3:
                raise ValueError(f"{name} must be between 1 and 3")
        evidence = _json_object(self.evidence, "evidence")
        if evidence.get("raw_report_included") is not False:
            raise ValueError("Bottleneck evidence must exclude the raw report")
        if evidence.get("hidden_evidence_included") is not False:
            raise ValueError("Bottleneck evidence must exclude Hidden evidence")
        safe_evidence = dict(evidence)
        safe_evidence.pop("raw_report_included", None)
        safe_evidence.pop("hidden_evidence_included", None)
        _reject_agent_unsafe(safe_evidence, "evidence")
        safe_context = _json_object(self.safe_context, "safe_context")
        _reject_agent_unsafe(safe_context, "safe_context")
        family = _optional_text(self.family_instruction, "family_instruction")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "family_instruction", family)


@dataclass(frozen=True, slots=True)
class BottleneckRewritePromptRequest:
    """Inputs for one complete-source Bottleneck rewrite call."""

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
        _require_hypothesis_level(self.hypothesis, "bottleneck")
        if self.hypothesis.parent_candidate_id != parent_id:
            raise ValueError("hypothesis parent does not match parent_candidate_id")
        classification = self.hypothesis.model_identity.get("classification")
        if not isinstance(classification, Mapping):
            raise ValueError("Bottleneck hypothesis must carry classification metadata")
        classification = _json_object(classification, "classification")
        _reject_agent_unsafe(classification, "classification")
        if classification.get("authoritative") is not False:
            raise ValueError("Bottleneck model classification must be non-authoritative")
        safe_context = _json_object(self.safe_context, "safe_context")
        _reject_agent_unsafe(safe_context, "safe_context")
        family = _optional_text(self.family_instruction, "family_instruction")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "parent_candidate_id", parent_id)
        object.__setattr__(self, "parent_source", source)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "family_instruction", family)


class BottleneckOptimizationPromptBuilder:
    """Build S3.5 evidence-driven prompts with deterministic identities."""

    def build_analysis(self, request: BottleneckAnalysisPromptRequest) -> LayeredPrompt:
        if not isinstance(request, BottleneckAnalysisPromptRequest):
            raise TypeError("request must be BottleneckAnalysisPromptRequest")
        system = self._analysis_system(request)
        user = self._analysis_user(request)
        prompt = _finalize_prompt(
            system=system,
            user=user,
            manifest={
                "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
                "purpose": BOTTLENECK_ANALYSIS_PURPOSE,
                "task_id": request.task.task_id,
                "kernel_name": request.task.kernel_name,
                "target_profile": request.task.target.name,
                "mode": request.task.mode.value,
                "level": "bottleneck",
                "round_number": request.round_number,
                "parent_candidate_id": request.parent_candidate_id,
                "parent_source_sha256": _text_sha256(request.parent_source),
                "evidence_id": request.evidence.get("evidence_id"),
                "evidence_projection_sha256": _mapping_sha256(request.evidence),
                "max_classifications": request.max_classifications,
                "max_hypotheses": request.max_hypotheses,
                "safe_context": request.safe_context,
                "family_instruction_present": request.family_instruction is not None,
                "feedback_projection": "agent_safe_typed_ppa_only",
                "raw_report_included": False,
                "hidden_test_source_isolation": "verified",
                "classification_authority": "model_inference_not_tool_fact",
                "output_contract": {
                    "format": "json_object",
                    "schema": "bottleneck_analysis_response_v1",
                    "commentary_allowed": False,
                    "unknown_allowed": True,
                    "max_classifications": request.max_classifications,
                    "max_hypotheses": request.max_hypotheses,
                },
            },
        )
        assert_hidden_test_sources_absent(task=request.task, messages=prompt.messages)
        return prompt

    def build_rewrite(self, request: BottleneckRewritePromptRequest) -> LayeredPrompt:
        if not isinstance(request, BottleneckRewritePromptRequest):
            raise TypeError("request must be BottleneckRewritePromptRequest")
        system = self._rewrite_system(request)
        user = self._rewrite_user(request)
        classification = request.hypothesis.model_identity["classification"]
        prompt = _finalize_prompt(
            system=system,
            user=user,
            manifest={
                "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
                "purpose": BOTTLENECK_REWRITE_PURPOSE,
                "task_id": request.task.task_id,
                "kernel_name": request.task.kernel_name,
                "target_profile": request.task.target.name,
                "mode": request.task.mode.value,
                "level": "bottleneck",
                "candidate_id": request.candidate_id,
                "parent_candidate_id": request.parent_candidate_id,
                "parent_source_sha256": _text_sha256(request.parent_source),
                "hypothesis_id": request.hypothesis.hypothesis_id,
                "hypothesis_sha256": _mapping_sha256(request.hypothesis.to_dict()),
                "classification_id": classification.get("classification_id"),
                "classification_sha256": _mapping_sha256(classification),
                "safe_context": request.safe_context,
                "family_instruction_present": request.family_instruction is not None,
                "feedback_projection": "agent_safe_typed_ppa_only",
                "hidden_test_source_isolation": "verified",
                "output_contract": {
                    "artifact_name": "candidate_kernel",
                    "language": "cpp",
                    "complete_replacement": True,
                    "fenced_code_block_preferred": True,
                    "raw_complete_source_allowed": True,
                    "commentary_allowed": False,
                    "explicit_abstention_token": CANDIDATE_REWRITE_ABSTENTION_TOKEN,
                    "top_function_interface_must_remain_unchanged": True,
                },
            },
        )
        assert_hidden_test_sources_absent(task=request.task, messages=prompt.messages)
        return prompt

    @staticmethod
    def _analysis_system(request: BottleneckAnalysisPromptRequest) -> str:
        kinds = (
            "initiation_interval|loop_carried_dependency|memory_port_contention|"
            "critical_path|resource_bottleneck|unknown_loop_bound|"
            "dataflow_stall_risk|latency_structure|objective_constraint|unknown"
        )
        signals = json.dumps(
            list(BOTTLENECK_ALLOWED_SIGNAL_FIELDS),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines = [
            "You are the AgRefactor++ Stage 3 Bottleneck analysis component.",
            "",
            "System invariants:",
            "- Correctness is more important than performance.",
            "- Use only the complete parent source and the typed agent-safe PPA projection supplied here.",
            "- The raw synthesis report and Hidden evaluation evidence are unavailable and must never be inferred or requested.",
            "- A classification is a model inference, not an authoritative tool fact.",
            "- Cite only supplied evidence_id values and explicit signal_fields from the frozen allowlist.",
            "- If evidence is insufficient, emit kind=unknown with confidence=low and no executable hypothesis for that classification.",
            "- Do not use source-string, pragma-count, warning-regex, or pattern matching as a correctness or bottleneck gate.",
            "- Do not claim that compilation, simulation, synthesis, or PPA improvement has already succeeded.",
            "- Return strict JSON only; no Markdown and no commentary.",
            "- The final message content must be non-empty; its first non-whitespace character must be { and its last must be }.",
            "",
            "Allowed classification kinds:",
            f"- {kinds}",
            "Allowed signal_fields exact-string array:",
            signals,
            "- Use only exact strings from that array.",
            "- resources_used and resources_available are JSON container names, not valid signal_fields.",
            "- For resource evidence, cite one or more exact leaf paths such as resources_used.lut.",
            "",
            "Output JSON contract:",
            '{"schema_version":1,"classifications":[{"kind":"...","claim":"...","confidence":"low|medium|high","supporting_evidence_ids":["..."],"signal_fields":["..."]}],"hypotheses":[{"classification_index":1,"claim":"...","supporting_evidence_ids":["..."],"expected_benefit":{"metric":"latency","direction":"decrease"},"risk":"low|medium|high","modification_scope":["..."],"verification_plan":["preflight","public","csynth","hidden"]}]}',
            f"- Return at most {request.max_classifications} classifications and {request.max_hypotheses} hypotheses, in priority order.",
            "- classification_index is one-based and must reference a non-unknown classification.",
            "- Hypothesis evidence must be a subset of its classification evidence.",
            "- Emit a Bottleneck hypothesis only when a concrete source-only causal change is available without adding, removing, or modifying HLS pragmas; otherwise keep the classification and omit its hypothesis.",
            "- Use exactly the keys shown above; do not invent evidence IDs or tool facts.",
        ]
        if request.family_instruction:
            lines.extend(["", "Model-family instruction:", request.family_instruction])
        return "\n".join(lines)

    @staticmethod
    def _analysis_user(request: BottleneckAnalysisPromptRequest) -> str:
        task_payload = {
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "mode": request.task.mode.value,
            "target": request.task.target.to_effective_dict(),
            "objective": "latency",
            "level": "bottleneck",
            "round_number": request.round_number,
            "parent_candidate_id": request.parent_candidate_id,
            "safe_context": request.safe_context,
            "allowed_signal_fields": list(BOTTLENECK_ALLOWED_SIGNAL_FIELDS),
        }
        return "\n".join(
            [
                "Task and policy contract:",
                "```json",
                _pretty_json(task_payload),
                "```",
                "",
                "Typed agent-safe PPA evidence projection:",
                "```json",
                _pretty_json(request.evidence),
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
    def _rewrite_system(request: BottleneckRewritePromptRequest) -> str:
        lines = [
            "You are the AgRefactor++ Stage 3 Bottleneck complete-source generator.",
            "",
            "System invariants:",
            "- Implement only the selected evidence-linked Bottleneck hypothesis.",
            "- Do not add, remove, or modify HLS pragmas/directives; Bottleneck rewrite owns causal source changes, while the later Pragma level owns directive edits.",
            "- Treat the classification as a non-authoritative model inference that still requires full qualification.",
            "- Preserve functional behavior and the exact top-function interface.",
            "- Return the complete replacement translation unit, not a patch, diff, excerpt, or explanation.",
            "- Do not define main unless the top function itself is main.",
            "- Never weaken tests or fabricate compile, simulation, synthesis, or PPA success.",
            "- Never infer, request, or mention Hidden evaluation content.",
            "- No static matcher will certify Bottleneck intent; qualification and PPA evidence remain authoritative.",
            "- Prefer exactly one fenced C++ block. A raw complete translation unit with no prose is also accepted.",
            f"- If the selected source-only change cannot be implemented safely and non-trivially, return exactly {CANDIDATE_REWRITE_ABSTENTION_TOKEN} and nothing else.",
        ]
        if request.family_instruction:
            lines.extend(["", "Model-family instruction:", request.family_instruction])
        return "\n".join(lines)

    @staticmethod
    def _rewrite_user(request: BottleneckRewritePromptRequest) -> str:
        payload = {
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "mode": request.task.mode.value,
            "target": request.task.target.to_effective_dict(),
            "candidate_id": request.candidate_id,
            "parent_candidate_id": request.parent_candidate_id,
            "hypothesis": request.hypothesis.to_dict(),
            "classification": request.hypothesis.model_identity["classification"],
            "safe_context": request.safe_context,
        }
        return "\n".join(
            [
                "Selected Bottleneck change contract:",
                "```json",
                _pretty_json(payload),
                "```",
                "",
                "Current complete source:",
                "```cpp",
                request.parent_source,
                "```",
                "",
                f"Return one complete changed C++ translation unit, or exactly {CANDIDATE_REWRITE_ABSTENTION_TOKEN} when safe implementation is unavailable.",
            ]
        )

@dataclass(frozen=True, slots=True)
class PragmaAnalysisPromptRequest:
    """Inputs for one bounded Pragma applicability/action call."""

    task: TaskSpec
    parent_candidate_id: str
    parent_source: str
    round_number: int
    max_actions: int
    max_hypotheses: int
    evidence: Mapping[str, Any]
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
        for name in ("max_actions", "max_hypotheses"):
            value = getattr(self, name)
            if isinstance(value, bool) or not 1 <= value <= 3:
                raise ValueError(f"{name} must be between 1 and 3")
        evidence = _json_object(self.evidence, "evidence")
        if evidence.get("raw_report_included") is not False:
            raise ValueError("Pragma evidence must exclude the raw report")
        if evidence.get("hidden_evidence_included") is not False:
            raise ValueError("Pragma evidence must exclude Hidden evidence")
        safe_evidence = dict(evidence)
        safe_evidence.pop("raw_report_included", None)
        safe_evidence.pop("hidden_evidence_included", None)
        _reject_agent_unsafe(safe_evidence, "evidence")
        safe_context = _json_object(self.safe_context, "safe_context")
        _reject_agent_unsafe(safe_context, "safe_context")
        family = _optional_text(self.family_instruction, "family_instruction")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "family_instruction", family)


@dataclass(frozen=True, slots=True)
class PragmaRewritePromptRequest:
    """Inputs for one complete-source Pragma rewrite call."""

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
        _require_hypothesis_level(self.hypothesis, "pragma")
        if self.hypothesis.parent_candidate_id != parent_id:
            raise ValueError("hypothesis parent does not match parent_candidate_id")
        action = self.hypothesis.model_identity.get("pragma_action")
        if not isinstance(action, Mapping):
            raise ValueError("Pragma hypothesis must carry pragma_action metadata")
        action = _json_object(action, "pragma_action")
        _reject_agent_unsafe(action, "pragma_action")
        if action.get("authoritative") is not False:
            raise ValueError("Pragma model action must be non-authoritative")
        safe_context = _json_object(self.safe_context, "safe_context")
        _reject_agent_unsafe(safe_context, "safe_context")
        family = _optional_text(self.family_instruction, "family_instruction")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "parent_candidate_id", parent_id)
        object.__setattr__(self, "parent_source", source)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "family_instruction", family)


class PragmaOptimizationPromptBuilder:
    """Build S3.6 evidence-driven Pragma prompts with deterministic identities."""

    def build_analysis(self, request: PragmaAnalysisPromptRequest) -> LayeredPrompt:
        if not isinstance(request, PragmaAnalysisPromptRequest):
            raise TypeError("request must be PragmaAnalysisPromptRequest")
        system = self._analysis_system(request)
        user = self._analysis_user(request)
        prompt = _finalize_prompt(
            system=system,
            user=user,
            manifest={
                "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
                "purpose": PRAGMA_ANALYSIS_PURPOSE,
                "task_id": request.task.task_id,
                "kernel_name": request.task.kernel_name,
                "target_profile": request.task.target.name,
                "mode": request.task.mode.value,
                "level": "pragma",
                "round_number": request.round_number,
                "parent_candidate_id": request.parent_candidate_id,
                "parent_source_sha256": _text_sha256(request.parent_source),
                "evidence_id": request.evidence.get("evidence_id"),
                "evidence_projection_sha256": _mapping_sha256(request.evidence),
                "max_actions": request.max_actions,
                "max_hypotheses": request.max_hypotheses,
                "safe_context": request.safe_context,
                "family_instruction_present": request.family_instruction is not None,
                "feedback_projection": "agent_safe_typed_ppa_only",
                "raw_report_included": False,
                "hidden_test_source_isolation": "verified",
                "pragma_action_authority": "model_proposal_not_tool_fact",
                "static_pragma_gate": False,
                "output_contract": {
                    "format": "json_object",
                    "schema": "pragma_analysis_response_v1",
                    "commentary_allowed": False,
                    "unknown_allowed": True,
                    "max_actions": request.max_actions,
                    "max_hypotheses": request.max_hypotheses,
                },
            },
        )
        assert_hidden_test_sources_absent(task=request.task, messages=prompt.messages)
        return prompt

    def build_rewrite(self, request: PragmaRewritePromptRequest) -> LayeredPrompt:
        if not isinstance(request, PragmaRewritePromptRequest):
            raise TypeError("request must be PragmaRewritePromptRequest")
        system = self._rewrite_system(request)
        user = self._rewrite_user(request)
        action = request.hypothesis.model_identity["pragma_action"]
        prompt = _finalize_prompt(
            system=system,
            user=user,
            manifest={
                "schema_version": OPTIMIZATION_PROMPT_SCHEMA_VERSION,
                "purpose": PRAGMA_REWRITE_PURPOSE,
                "task_id": request.task.task_id,
                "kernel_name": request.task.kernel_name,
                "target_profile": request.task.target.name,
                "mode": request.task.mode.value,
                "level": "pragma",
                "candidate_id": request.candidate_id,
                "parent_candidate_id": request.parent_candidate_id,
                "parent_source_sha256": _text_sha256(request.parent_source),
                "hypothesis_id": request.hypothesis.hypothesis_id,
                "hypothesis_sha256": _mapping_sha256(request.hypothesis.to_dict()),
                "pragma_action_id": action.get("action_id"),
                "pragma_action_sha256": _mapping_sha256(action),
                "safe_context": request.safe_context,
                "family_instruction_present": request.family_instruction is not None,
                "feedback_projection": "agent_safe_typed_ppa_only",
                "hidden_test_source_isolation": "verified",
                "static_pragma_gate": False,
                "output_contract": {
                    "artifact_name": "candidate_kernel",
                    "language": "cpp",
                    "complete_replacement": True,
                    "fenced_code_block_preferred": True,
                    "raw_complete_source_allowed": True,
                    "commentary_allowed": False,
                    "explicit_abstention_token": CANDIDATE_REWRITE_ABSTENTION_TOKEN,
                    "top_function_interface_must_remain_unchanged": True,
                },
            },
        )
        assert_hidden_test_sources_absent(task=request.task, messages=prompt.messages)
        return prompt

    @staticmethod
    def _analysis_system(request: PragmaAnalysisPromptRequest) -> str:
        kinds = "|".join(PRAGMA_ALLOWED_KINDS)
        targets = "|".join(PRAGMA_ALLOWED_TARGET_KINDS)
        signals = json.dumps(
            list(PRAGMA_ALLOWED_SIGNAL_FIELDS),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines = [
            "You are the AgRefactor++ Stage 3 Pragma planning component.",
            "",
            "System invariants:",
            "- Correctness is more important than performance.",
            "- Pragma is the final optimization level; propose only a narrow directive change, not an algorithm rewrite.",
            "- Use only the complete parent source and the typed agent-safe PPA projection supplied here.",
            "- The raw synthesis report and Hidden evaluation evidence are unavailable and must never be inferred or requested.",
            "- A pragma action is a model proposal, not an authoritative statement that a source target exists or that a directive is legal/effective.",
            "- Cite only supplied evidence_id values and exact signal_fields from the frozen allowlist.",
            "- Do not scan text, count existing pragmas, or use regex/pattern matching as an applicability or correctness gate.",
            "- If evidence or target applicability is insufficient, emit kind=unknown with confidence=low and no executable hypothesis for that action.",
            "- Do not claim compilation, simulation, synthesis, or PPA improvement has succeeded.",
            "- Return strict JSON only; no Markdown and no commentary.",
            "- The final message content must be non-empty and contain exactly one JSON object.",
            "",
            "Allowed action kinds:",
            f"- {kinds}",
            "Allowed target_kind values:",
            f"- {targets}",
            "Allowed signal_fields exact-string array:",
            signals,
            "- resources_used and resources_available are containers, not valid signal_fields; cite exact leaf paths.",
            "- Evidence values are nested under metrics in the input JSON, but signal_fields MUST omit the metrics. prefix; use initiation_interval_max, never metrics.initiation_interval_max.",
            "",
            "Directive/target compatibility matrix (exact):",
            "- pipeline -> loop or function",
            "- unroll -> loop",
            "- array_partition -> array",
            "- dataflow -> function or region",
            "- inline -> function",
            "- bind_storage -> non-interface local array only; top-level interface arrays require INTERFACE storage options and are outside safe-v1",
            "- bind_op -> operation",
            "- unknown -> unknown",
            "",
            "Directive-specific parameters (exact keys only):",
            '- pipeline: {"ii":positive_integer optional,"rewind":boolean optional}',
            '- unroll: {"factor":positive_integer optional,"skip_exit_check":boolean optional}; empty means complete unroll proposal',
            '- array_partition: {"type":"complete|block|cyclic","factor":positive_integer when block/cyclic,"dim":positive_integer optional}',
            '- dataflow: {}',
            '- inline: {} for ordinary INLINE, or {"mode":"off|recursive"}; the string on is not a Vitis HLS pragma argument',
            '- bind_storage: {"type":"ram_1p|ram_2p|ram_t2p|rom_1p|rom_2p|rom_np|fifo","impl":"auto|bram|bram_ecc|lutram|uram|uram_ecc|memory|srl","latency":non_negative_integer optional}',
            '- bind_op: {"op":"add|sub|mul|div|rem|fadd|fsub|fmul|fdiv|dadd|dsub|dmul|ddiv","impl":"fabric|dsp|maxdsp|fulldsp|meddsp|primitivedsp","latency":non_negative_integer optional}',
            '- unknown: {}; target_kind=unknown; target_ref=null; empty evidence and signals',
            "- Generic RESOURCE is not a safe-v1 action; use typed bind_storage or bind_op when supported.",
            "",
            "Output JSON contract:",
            '{"schema_version":1,"actions":[{"kind":"...","target_kind":"...","target_ref":"... or null","parameters":{},"claim":"...","confidence":"low|medium|high","supporting_evidence_ids":["..."],"signal_fields":["..."]}],"hypotheses":[{"action_index":1,"claim":"...","supporting_evidence_ids":["..."],"expected_benefit":{"metric":"latency","direction":"decrease"},"risk":"low|medium|high","modification_scope":["..."],"verification_plan":["preflight","public","csynth","hidden"]}]}',
            "Valid executable shape example (copy the key structure, but use only supplied evidence/targets):",
            '{"schema_version":1,"actions":[{"kind":"pipeline","target_kind":"loop","target_ref":"top.loop_i","parameters":{"ii":1},"claim":"Propose one loop pipeline action for later qualification.","confidence":"medium","supporting_evidence_ids":["SUPPLIED_EVIDENCE_ID"],"signal_fields":["latency_cycles_max"]}],"hypotheses":[{"action_index":1,"claim":"Apply only the selected pipeline action.","supporting_evidence_ids":["SUPPLIED_EVIDENCE_ID"],"expected_benefit":{"metric":"latency","direction":"decrease"},"risk":"medium","modification_scope":["selected loop pragma only"],"verification_plan":["preflight","public","csynth","hidden"]}]}',
            "Valid safe-unknown shape example:",
            '{"schema_version":1,"actions":[{"kind":"unknown","target_kind":"unknown","target_ref":null,"parameters":{},"claim":"Evidence or target applicability is insufficient.","confidence":"low","supporting_evidence_ids":[],"signal_fields":[]}],"hypotheses":[]}',
            "- The placeholder SUPPLIED_EVIDENCE_ID must be replaced by the exact evidence_id in the request; never return the placeholder literally.",
            "- Before returning, self-check every action against the compatibility matrix, parameter schema, exact evidence ID, and exact signal-field allowlist.",
            f"- Return at most {request.max_actions} actions and {request.max_hypotheses} hypotheses, in priority order.",
            "- action_index is one-based and must reference a non-unknown action.",
            "- Hypothesis evidence must be a subset of its action evidence.",
            "- Use exactly the keys shown; do not invent evidence IDs, signal fields, directive kinds, or tool facts.",
        ]
        if request.family_instruction:
            lines.extend(["", "Model-family instruction:", request.family_instruction])
        return "\n".join(lines)

    @staticmethod
    def _analysis_user(request: PragmaAnalysisPromptRequest) -> str:
        task_payload = {
            "task_id": request.task.task_id,
            "kernel_name": request.task.kernel_name,
            "mode": request.task.mode.value,
            "target": request.task.target.to_effective_dict(),
            "objective": "latency",
            "level": "pragma",
            "round_number": request.round_number,
            "parent_candidate_id": request.parent_candidate_id,
            "safe_context": request.safe_context,
            "allowed_signal_fields": list(PRAGMA_ALLOWED_SIGNAL_FIELDS),
            "allowed_action_kinds": list(PRAGMA_ALLOWED_KINDS),
            "allowed_target_kinds": list(PRAGMA_ALLOWED_TARGET_KINDS),
        }
        return "\n".join(
            [
                "Task and policy contract:",
                "```json",
                _pretty_json(task_payload),
                "```",
                "",
                "Typed agent-safe PPA evidence projection:",
                "```json",
                _pretty_json(request.evidence),
                "```",
                "",
                "Parent candidate source (read-only; use it to name a precise target_ref, not as a static gate):",
                "```cpp",
                request.parent_source,
                "```",
                "",
                "Return the strict JSON object now.",
            ]
        )

    @staticmethod
    def _rewrite_system(request: PragmaRewritePromptRequest) -> str:
        lines = [
            "You are the AgRefactor++ Stage 3 Pragma complete-source generator.",
            "",
            "System invariants:",
            "- Implement only the selected typed Pragma action and hypothesis.",
            "- Treat the action as a non-authoritative model proposal that still requires full qualification.",
            "- Keep algorithm structure and functional behavior unchanged except for the narrow directive insertion or adjustment.",
            "- Preserve the exact top-function interface.",
            "- Return the complete replacement translation unit, not a patch, diff, excerpt, or explanation.",
            "- Do not define main unless the top function itself is main.",
            "- Never weaken tests or fabricate compile, simulation, synthesis, or PPA success.",
            "- Never infer, request, or mention Hidden evaluation content.",
            "- No source-string or pragma-count matcher will certify the result; downstream qualification remains authoritative.",
            "- Prefer exactly one fenced cpp block. A raw complete translation unit with no prose is also accepted.",
            f"- If the typed target cannot be located or the directive cannot be applied without guessing, return exactly {CANDIDATE_REWRITE_ABSTENTION_TOKEN} and nothing else.",
        ]
        if request.family_instruction:
            lines.extend(["", "Model-family instruction:", request.family_instruction])
        return "\n".join(lines)

    @staticmethod
    def _rewrite_user(request: PragmaRewritePromptRequest) -> str:
        action = request.hypothesis.model_identity["pragma_action"]
        payload = {
            "candidate_id": request.candidate_id,
            "parent_candidate_id": request.parent_candidate_id,
            "top_function": request.task.kernel_name,
            "objective": "latency",
            "pragma_action": action,
            "hypothesis": request.hypothesis.to_dict(),
            "safe_context": request.safe_context,
        }
        return "\n".join(
            [
                "Selected Pragma action contract:",
                "```json",
                _pretty_json(payload),
                "```",
                "",
                "Current complete source:",
                "```cpp",
                request.parent_source,
                "```",
                "",
                f"Return one complete changed C++ translation unit, or exactly {CANDIDATE_REWRITE_ABSTENTION_TOKEN} when safe implementation is unavailable.",
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


def _require_hypothesis_level(hypothesis: Any, expected_level: str) -> None:
    """Validate optimizer state types lazily to keep prompt imports acyclic."""

    from agrefactor.optimization.state import HypothesisRecord, OptimizationLevel

    if not isinstance(hypothesis, HypothesisRecord):
        raise TypeError("hypothesis must be HypothesisRecord")
    level = OptimizationLevel(expected_level)
    if hypothesis.level is not level:
        raise ValueError(f"hypothesis level must be {expected_level}")


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
