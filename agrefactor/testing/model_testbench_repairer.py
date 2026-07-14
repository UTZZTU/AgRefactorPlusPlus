"""Provider-neutral LLM adapter for testbench-only repair."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agrefactor.models import (
    ChatMessage,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
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
    """Deterministic obligations a compile-only repair must preserve."""

    required_function_names: tuple[str, ...]
    required_macros: tuple[str, ...]
    minimum_call_counts: Mapping[str, int]

    @classmethod
    def from_testbench(
        cls,
        source: str,
    ) -> "TestbenchRepairContract":
        function_names = _extract_declared_function_names(source)
        call_counts = {
            name: _call_count(source, name)
            for name in function_names
        }

        return cls(
            required_function_names=function_names,
            required_macros=_extract_macros(source),
            minimum_call_counts=call_counts,
        )

    def validate(self, proposed: str) -> tuple[str, ...]:
        issues: list[str] = []

        if not re.search(r"\bint\s+main\s*\(", proposed):
            issues.append("missing int main(...) entry point")

        proposed_names = set(
            _extract_declared_function_names(proposed)
        )
        for function_name in self.required_function_names:
            if function_name not in proposed_names:
                issues.append(
                    "missing required declaration for function: "
                    + function_name
                )

            definition_pattern = re.compile(
                rf"^\s*(?:extern\s+\"C\"\s+)?"
                rf"(?:[A-Za-z_]\w*(?:::\w+)*"
                rf"(?:\s*[*&]\s*|\s+))+"
                rf"{re.escape(function_name)}\s*"
                rf"\([^;{{}}]*\)\s*\{{",
                re.MULTILINE,
            )
            if definition_pattern.search(proposed):
                issues.append(
                    "testbench must not define, stub, or wrap function: "
                    + function_name
                )

        proposed_macros = set(_extract_macros(proposed))
        for macro in self.required_macros:
            if macro not in proposed_macros:
                issues.append(
                    "missing required macro: "
                    + macro
                )

        for function_name, minimum in self.minimum_call_counts.items():
            actual = _call_count(proposed, function_name)
            if actual < minimum:
                issues.append(
                    f"reduced call count for {function_name}: "
                    f"expected at least {minimum}, got {actual}"
                )

        return tuple(issues)


_BASE_SYSTEM_PROMPT = """You repair C++ HLS testbenches.

Your scope is testbench-only:
- Return a complete replacement testbench.
- Never modify or propose changes to the original program.
- Never modify or propose changes to the candidate HLS kernel.
- Repair only defects supported by the structured compiler evidence.
- Preserve all existing test cases, seeds, comparisons, assertions,
  macros, required top-level calls, and return semantics.
- Do not reduce test count or weaken any check.
- Existing testbench declarations are not authoritative. When compiler
  or linker evidence shows an interface declaration mismatch, correct
  only the declaration's return type, parameter types, qualifiers, or
  C/C++ language linkage so it matches the corresponding definition in
  the read-only original or candidate source.
- Never define, stub, wrap, or reimplement an original or candidate
  top-level function inside the testbench.
- Treat original and candidate implementations as black boxes exposed
  only through their actual public top-level definitions.
- Never copy or redeclare implementation-private types. Never declare,
  read, write, or reset file-scope variables owned by the original or
  candidate implementation.
- When the current testbench resets private state between independent
  cases, preserve the same clean-state semantics without private access.
  On supported POSIX host simulation, run each independent case in a
  fresh child process and wait for it (or use an equivalent supported
  harness-isolation technique). Preserve every seed, call, comparison,
  assertion, and failure condition.
- Do not access implementation-private helper functions or internal
  data structures.
- The result must remain deterministic and self-contained.
- Output exactly one ```cpp ... ``` block and no commentary.
"""


def build_testbench_repair_messages(
    request: TestbenchRepairRequest,
    *,
    family_instruction: str | None = None,
) -> tuple[ChatMessage, ...]:
    """Build provider-neutral messages from structured evidence."""

    system_prompt = _BASE_SYSTEM_PROMPT
    if family_instruction and family_instruction.strip():
        system_prompt += (
            "\nModel-family instruction:\n"
            + family_instruction.strip()
            + "\n"
        )

    evidence = request.preflight.to_dict()
    user_prompt = (
        f"Repair attempt {request.attempt} of "
        f"{request.max_attempts}.\n\n"
        "Structured preflight evidence:\n"
        "```json\n"
        + json.dumps(
            evidence,
            indent=2,
            ensure_ascii=False,
        )
        + "\n```\n\n"
        "Current testbench to repair:\n"
        "```cpp\n"
        + request.current_testbench
        + "\n```\n\n"
        "Read-only original program context:\n"
        "```cpp\n"
        + request.original_code
        + "\n```\n\n"
        "Read-only candidate HLS context:\n"
        "```cpp\n"
        + request.candidate_code
        + "\n```\n"
    )

    return (
        ChatMessage(
            role="system",
            content=system_prompt,
        ),
        ChatMessage(
            role="user",
            content=user_prompt,
        ),
    )


class ModelTestbenchRepairer:
    """Use the shared model registry to propose testbench-only repairs."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        model_name: str,
        parameters: Mapping[str, Any] | None = None,
        family_instructions: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("registry must be a ModelRegistry")

        self._model, self._provider = registry.resolve(model_name)
        self._parameters = dict(parameters or {})
        self._family_instructions = dict(
            family_instructions or {}
        )
        self._responses: list[ModelResponse] = []

        json.dumps(
            self._parameters,
            ensure_ascii=False,
            sort_keys=True,
        )

    @property
    def responses(self) -> tuple[ModelResponse, ...]:
        """Return normalized responses observed by this adapter."""

        return tuple(self._responses)

    @property
    def last_response(self) -> ModelResponse | None:
        """Return the most recent normalized response."""

        return self._responses[-1] if self._responses else None

    def repair(self, request: TestbenchRepairRequest) -> str:
        """Generate and validate one complete repaired testbench."""

        if not isinstance(request, TestbenchRepairRequest):
            raise TypeError(
                "request must be a TestbenchRepairRequest"
            )

        family_instruction = None
        if self._model.family:
            family_instruction = self._family_instructions.get(
                self._model.family
            )

        messages = build_testbench_repair_messages(
            request,
            family_instruction=family_instruction,
        )

        parameters = dict(self._model.default_parameters)
        parameters.update(self._parameters)

        response = self._provider.generate(
            self._model,
            ModelRequest(
                messages=messages,
                parameters=parameters,
            ),
        )
        if not isinstance(response, ModelResponse):
            raise TypeError(
                "model provider must return a ModelResponse"
            )

        self._responses.append(response)
        proposed = extract_complete_cpp_block(response.text)

        contract = TestbenchRepairContract.from_testbench(
            request.current_testbench
        )
        issues = contract.validate(proposed)
        if issues:
            raise TestbenchRepairResponseError(
                "repaired testbench violated deterministic contract: "
                + "; ".join(issues)
            )

        return proposed
