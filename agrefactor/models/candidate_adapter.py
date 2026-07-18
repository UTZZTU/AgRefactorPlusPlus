"""Provider-neutral model adapter for one candidate replacement."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from agrefactor.config import TaskSpec
from agrefactor.prompts import LayeredPrompt, PromptPurpose

from .base import ModelRequest, ModelResponse, ModelSpec
from .registry import ModelRegistry


_CANDIDATE_ARTIFACT = "candidate_kernel"
_CPP_LANGUAGES = frozenset({"cpp", "c++", "cxx"})
_CANDIDATE_PURPOSES = frozenset(
    {
        PromptPurpose.CANDIDATE_COMPILE_REPAIR.value,
        PromptPurpose.CANDIDATE_CSYNTH_REPAIR.value,
        PromptPurpose.CANDIDATE_PUBLIC_CSIM_REPAIR.value,
    }
)
_FENCE_RE = re.compile(
    r"```[ \t]*(?P<language>cpp|c\+\+|cxx)[ \t]*\r?\n"
    r"(?P<code>.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_PATCH_LINE_RE = re.compile(
    r"(?m)^\s*(?:diff --git\b|@@\b|---\s+[ab]/|\+\+\+\s+[ab]/|"
    r"\*\*\*\s+(?:Begin|End) Patch\b)"
)


class CandidateResponseError(ValueError):
    """Raised when a candidate model response violates its contract."""


def _copy_json_mapping(
    value: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must contain finite JSON-serializable data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{field_name} must normalize to an object")
    return copied


def _validate_required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _as_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(value)
    if not all(isinstance(item, str) for item in result):
        raise TypeError(f"{field_name} must contain only strings")
    return result


@dataclass(frozen=True, slots=True)
class CandidateModelRequest:
    """Immutable inputs for one provider call over a candidate prompt."""

    prompt: LayeredPrompt
    task: TaskSpec
    current_candidate: str

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, LayeredPrompt):
            raise TypeError("prompt must be a LayeredPrompt")
        if not isinstance(self.task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        _validate_required_text(
            self.current_candidate,
            "current_candidate",
        )
        self._validate_prompt_manifest()

    def _validate_prompt_manifest(self) -> None:
        manifest = self.prompt.manifest
        purpose = manifest.get("purpose")
        if purpose not in _CANDIDATE_PURPOSES:
            raise ValueError(
                "CandidateModelRequest requires a candidate repair prompt"
            )

        expected_identity = {
            "task_id": self.task.task_id,
            "kernel_name": self.task.kernel_name,
            "target_profile": self.task.target.name,
        }
        for key, expected in expected_identity.items():
            if manifest.get(key) != expected:
                raise ValueError(
                    f"prompt manifest {key} does not match the TaskSpec"
                )

        editable = _as_string_tuple(
            manifest.get("editable_artifacts"),
            "prompt editable_artifacts",
        )
        if editable != (_CANDIDATE_ARTIFACT,):
            raise ValueError(
                "candidate prompt must expose candidate_kernel as the "
                "only editable artifact"
            )

        output = manifest.get("output_contract")
        if not isinstance(output, Mapping):
            raise TypeError("prompt output_contract must be a mapping")
        required_output = {
            "artifact_name": _CANDIDATE_ARTIFACT,
            "language": "cpp",
            "complete_replacement": True,
            "fenced_code_block": True,
            "commentary_allowed": False,
        }
        for key, expected in required_output.items():
            if output.get(key) != expected:
                raise ValueError(
                    f"prompt output contract has invalid {key}"
                )


@dataclass(frozen=True, slots=True)
class CandidateResponseContract:
    """Deterministic response obligations for one candidate kernel."""

    top_function_name: str
    interface_header: str
    current_candidate_semantic_sha256: str

    @classmethod
    def from_candidate(
        cls,
        task: TaskSpec,
        current_candidate: str,
    ) -> "CandidateResponseContract":
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        _validate_required_text(
            current_candidate,
            "current_candidate",
        )
        definitions = _find_function_definitions(
            current_candidate,
            task.kernel_name,
        )
        if len(definitions) != 1:
            raise CandidateResponseError(
                "current candidate must contain exactly one definition "
                f"of top function {task.kernel_name}"
            )
        return cls(
            top_function_name=task.kernel_name,
            interface_header=definitions[0],
            current_candidate_semantic_sha256=(
                _semantic_sha256(current_candidate)
            ),
        )

    def __post_init__(self) -> None:
        _validate_required_text(
            self.top_function_name,
            "top_function_name",
        )
        _validate_required_text(
            self.interface_header,
            "interface_header",
        )
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            self.current_candidate_semantic_sha256,
        ):
            raise ValueError(
                "current_candidate_semantic_sha256 must be a SHA-256 hex digest"
            )

    def extract_and_validate(self, response_text: str) -> str:
        """Extract one replacement and enforce deterministic safeguards."""

        proposed = _extract_complete_cpp_replacement(response_text)
        issues = self.validate_replacement(proposed)
        if issues:
            raise CandidateResponseError(
                "candidate response violated deterministic contract: "
                + "; ".join(issues)
            )
        return proposed

    def validate_replacement(self, proposed: str) -> tuple[str, ...]:
        _validate_required_text(proposed, "proposed candidate")
        issues: list[str] = []

        if _PATCH_LINE_RE.search(proposed):
            issues.append("response contains patch or diff markers")

        definitions = _find_function_definitions(
            proposed,
            self.top_function_name,
        )
        if not definitions:
            issues.append(
                "missing required top function definition: "
                + self.top_function_name
            )
        elif len(definitions) > 1:
            issues.append(
                "multiple definitions of required top function: "
                + self.top_function_name
            )
        elif definitions[0] != self.interface_header:
            issues.append(
                "top function interface was changed: "
                + self.top_function_name
            )

        if self.top_function_name != "main" and _find_function_definitions(
            proposed,
            "main",
        ):
            issues.append("candidate replacement must not define main")

        if (
            _semantic_sha256(proposed)
            == self.current_candidate_semantic_sha256
        ):
            issues.append("candidate replacement is semantically unchanged")

        return tuple(issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_function_name": self.top_function_name,
            "interface_header": self.interface_header,
            "current_candidate_semantic_sha256": (
                self.current_candidate_semantic_sha256
            ),
        }


@dataclass(frozen=True, slots=True)
class CandidateModelResult:
    """Auditable normalized result from one candidate model call."""

    candidate_code: str
    logical_model_name: str
    provider_name: str
    response: ModelResponse
    request_parameters: Mapping[str, Any] = field(default_factory=dict)
    prompt_manifest: Mapping[str, Any] = field(default_factory=dict)
    response_contract: CandidateResponseContract | None = None

    def __post_init__(self) -> None:
        _validate_required_text(self.candidate_code, "candidate_code")
        _validate_required_text(
            self.logical_model_name,
            "logical_model_name",
        )
        _validate_required_text(self.provider_name, "provider_name")
        if not isinstance(self.response, ModelResponse):
            raise TypeError("response must be a ModelResponse")
        if not isinstance(
            self.response_contract,
            CandidateResponseContract,
        ):
            raise TypeError(
                "response_contract must be a CandidateResponseContract"
            )
        object.__setattr__(
            self,
            "request_parameters",
            _copy_json_mapping(
                self.request_parameters,
                "request_parameters",
            ),
        )
        object.__setattr__(
            self,
            "prompt_manifest",
            _copy_json_mapping(
                self.prompt_manifest,
                "prompt_manifest",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_code": self.candidate_code,
            "logical_model_name": self.logical_model_name,
            "provider_name": self.provider_name,
            "request_parameters": dict(self.request_parameters),
            "prompt_manifest": dict(self.prompt_manifest),
            "response_contract": self.response_contract.to_dict(),
            "response": {
                "text": self.response.text,
                "model": self.response.model,
                "usage": {
                    "prompt_tokens": (
                        self.response.usage.prompt_tokens
                    ),
                    "completion_tokens": (
                        self.response.usage.completion_tokens
                    ),
                    "total_tokens": self.response.usage.total_tokens,
                    "cost_usd": self.response.usage.cost_usd,
                },
                "finish_reason": self.response.finish_reason,
                "metadata": dict(self.response.metadata),
            },
        }


class CandidateModelAdapter:
    """Call one fixed registered model and validate one candidate result."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        model_name: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("registry must be a ModelRegistry")
        self._model, self._provider = registry.resolve(model_name)
        self._parameters = _copy_json_mapping(
            parameters or {},
            "parameters",
        )
        self._prompts: list[LayeredPrompt] = []
        self._responses: list[ModelResponse] = []
        self._results: list[CandidateModelResult] = []

    @property
    def model_spec(self) -> ModelSpec:
        return self._model

    @property
    def prompts(self) -> tuple[LayeredPrompt, ...]:
        return tuple(self._prompts)

    @property
    def responses(self) -> tuple[ModelResponse, ...]:
        return tuple(self._responses)

    @property
    def results(self) -> tuple[CandidateModelResult, ...]:
        return tuple(self._results)

    @property
    def last_prompt(self) -> LayeredPrompt | None:
        return self._prompts[-1] if self._prompts else None

    @property
    def last_response(self) -> ModelResponse | None:
        return self._responses[-1] if self._responses else None

    @property
    def last_result(self) -> CandidateModelResult | None:
        return self._results[-1] if self._results else None

    def generate(
        self,
        request: CandidateModelRequest,
    ) -> CandidateModelResult:
        """Make one provider call and return one validated replacement."""

        if not isinstance(request, CandidateModelRequest):
            raise TypeError("request must be a CandidateModelRequest")

        contract = CandidateResponseContract.from_candidate(
            request.task,
            request.current_candidate,
        )
        parameters = dict(self._model.default_parameters)
        parameters.update(self._parameters)

        self._prompts.append(request.prompt)
        response = self._provider.generate(
            self._model,
            ModelRequest(
                messages=request.prompt.messages,
                parameters=parameters,
            ),
        )
        if not isinstance(response, ModelResponse):
            raise TypeError(
                "model provider must return a ModelResponse"
            )
        self._responses.append(response)

        candidate_code = contract.extract_and_validate(response.text)
        result = CandidateModelResult(
            candidate_code=candidate_code,
            logical_model_name=self._model.name,
            provider_name=self._provider.name,
            response=response,
            request_parameters=parameters,
            prompt_manifest=request.prompt.manifest,
            response_contract=contract,
        )
        self._results.append(result)
        return result


def _extract_complete_cpp_replacement(response_text: str) -> str:
    if not isinstance(response_text, str) or not response_text.strip():
        raise CandidateResponseError(
            "model response must not be empty"
        )

    cleaned = response_text.strip()
    matches = list(_FENCE_RE.finditer(cleaned))
    if len(matches) != 1 or cleaned.count("```") != 2:
        raise CandidateResponseError(
            "model response must contain exactly one fenced C++ block"
        )

    match = matches[0]
    if match.group("language").lower() not in _CPP_LANGUAGES:
        raise CandidateResponseError(
            "model response code block must use a C++ language tag"
        )
    outside = (
        cleaned[: match.start()] + cleaned[match.end() :]
    ).strip()
    if outside:
        raise CandidateResponseError(
            "model response must not contain commentary outside the C++ block"
        )

    code = match.group("code").strip()
    if not code:
        raise CandidateResponseError(
            "model returned an empty C++ block"
        )
    if _PATCH_LINE_RE.search(code):
        raise CandidateResponseError(
            "model response must be a complete replacement, not a patch or diff"
        )
    return code


def _mask_non_code(source: str) -> str:
    result: list[str] = []
    index = 0
    state = "normal"
    quote = ""

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "normal":
            if char == "/" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                result.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            if char in {'"', "'"}:
                quote = char
                result.append(char)
                index += 1
                state = "literal"
                continue
            result.append(char)
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                result.append("\n")
                state = "normal"
            else:
                result.append(" ")
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "normal"
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if state == "literal":
            if char == "\\":
                result.append(" ")
                if index + 1 < len(source):
                    result.append(" ")
                index += 2
                continue
            if char == quote:
                result.append(char)
                index += 1
                state = "normal"
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1

    return "".join(result)


def _strip_comments(source: str) -> str:
    output: list[str] = []
    index = 0
    state = "normal"
    quote = ""

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "normal":
            if char == "/" and next_char == "/":
                output.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                output.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            output.append(char)
            if char in {'\"', "'"}:
                quote = char
                state = "literal"
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                output.append("\n")
                state = "normal"
            else:
                output.append(" ")
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                output.extend((" ", " "))
                index += 2
                state = "normal"
                continue
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue

        output.append(char)
        if char == "\\" and index + 1 < len(source):
            output.append(source[index + 1])
            index += 2
            continue
        if char == quote:
            state = "normal"
        index += 1

    return "".join(output)


def _find_matching_parenthesis(source: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_definition_terminator(source: str, start: int) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif paren_depth == 0 and bracket_depth == 0:
            if char == "{":
                return index
            if char == ";":
                return None
    return None


def _segment_start(source: str, name_index: int) -> int:
    positions = [
        source.rfind(delimiter, 0, name_index)
        for delimiter in (";", "{", "}")
    ]
    return max(positions) + 1


def _canonicalize_interface(header: str) -> str:
    without_comments = _strip_comments(header)
    lines = [
        line
        for line in without_comments.splitlines()
        if not line.lstrip().startswith("#")
    ]
    normalized = re.sub(r"\s+", " ", " ".join(lines)).strip()
    normalized = re.sub(
        r"\s*([(),\[\]*&<>:=])\s*",
        r"\1",
        normalized,
    )
    return normalized


def _find_function_definitions(
    source: str,
    function_name: str,
) -> tuple[str, ...]:
    _validate_required_text(source, "source")
    _validate_required_text(function_name, "function_name")
    structural = _mask_non_code(source)
    pattern = re.compile(rf"\b{re.escape(function_name)}\b")
    definitions: list[str] = []

    for match in pattern.finditer(structural):
        cursor = match.end()
        while cursor < len(structural) and structural[cursor].isspace():
            cursor += 1
        if cursor >= len(structural) or structural[cursor] != "(":
            continue

        start = _segment_start(structural, match.start())
        prefix = structural[start : match.start()]
        if prefix.count("(") != prefix.count(")"):
            continue
        if prefix.count("[") != prefix.count("]"):
            continue
        if not re.search(r"[A-Za-z_]\w*", prefix):
            continue
        if re.search(
            r"\b(?:if|for|while|switch|return|sizeof|decltype)\s*$",
            prefix,
        ):
            continue

        closing = _find_matching_parenthesis(structural, cursor)
        if closing is None:
            continue
        terminator = _find_definition_terminator(
            structural,
            closing + 1,
        )
        if terminator is None:
            continue

        canonical = _canonicalize_interface(
            source[start:terminator]
        )
        if canonical:
            definitions.append(canonical)

    return tuple(definitions)


def _semantic_sha256(source: str) -> str:
    _validate_required_text(source, "source")
    output: list[str] = []
    index = 0
    state = "normal"
    quote = ""

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "normal":
            if char.isspace():
                index += 1
                continue
            if char == "/" and next_char == "/":
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                index += 2
                state = "block_comment"
                continue
            if char in {'"', "'"}:
                quote = char
                output.append(char)
                index += 1
                state = "literal"
                continue
            output.append(char)
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                index += 2
                state = "normal"
            else:
                index += 1
            continue

        if state == "literal":
            output.append(char)
            if char == "\\" and index + 1 < len(source):
                output.append(source[index + 1])
                index += 2
                continue
            if char == quote:
                state = "normal"
            index += 1

    return hashlib.sha256(
        "".join(output).encode("utf-8")
    ).hexdigest()
