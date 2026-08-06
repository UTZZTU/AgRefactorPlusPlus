"""Unknown-safe eligibility contracts for Refactor primary samples.

This module does not decide functional correctness. It answers two narrower
questions:

* can the source safely use auto-generated Public tests without relying on
  private mutable file-scope state; and
* when Original CSYNTH evidence is supplied, is the case eligible to be a
  primary full-Refactor campaign sample?

The source analysis uses a deterministic lexical/token structure pass. It does
not infer eligibility from substrings or message regexes. Any incomplete or
ambiguous structure remains review-required.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


class EligibilityStatus(str, Enum):
    ALLOWED = "allowed"
    REJECTED = "rejected"
    REVIEW_REQUIRED = "review_required"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str
    line: int
    offset: int


@dataclass(frozen=True, slots=True)
class _FunctionBody:
    name: str
    tokens: tuple[_Token, ...]


@dataclass(frozen=True, slots=True)
class SourceBoundaryEvidence:
    analysis_complete: bool
    top_function_found: bool
    reachable_functions: tuple[str, ...]
    mutable_file_scope_objects: tuple[str, ...]
    private_global_dependencies: tuple[str, ...]
    ambiguity_codes: tuple[str, ...]
    preprocessor_line_count: int
    tokenizer_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "analysis_method": "deterministic_cpp_lexical_structure_v1",
            "analysis_complete": self.analysis_complete,
            "top_function_found": self.top_function_found,
            "reachable_functions": list(self.reachable_functions),
            "mutable_file_scope_objects": list(
                self.mutable_file_scope_objects
            ),
            "private_global_dependencies": list(
                self.private_global_dependencies
            ),
            "ambiguity_codes": list(self.ambiguity_codes),
            "preprocessor_line_count": self.preprocessor_line_count,
            "tokenizer_version": self.tokenizer_version,
        }


@dataclass(frozen=True, slots=True)
class OriginalCsynthEvidence:
    """Immutable, identity-bound evidence for one Original CSYNTH run."""

    source_sha256: str
    top_function: str
    status: str
    tool_launched: bool
    csynth_launched: bool
    returncode: int | None
    timed_out: bool
    evidence_sha256: str | None
    evidence_ref: str | None = None
    evidence_view: str = "agent_safe"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_sha256(self.source_sha256, "source_sha256")
        if not self.top_function or not self.top_function.isidentifier():
            raise ValueError("top_function must be an unqualified identifier")
        status = _safe_code(self.status)
        if status not in {"passed", "failed", "error", "blocked"}:
            raise ValueError("unsupported Original CSYNTH status")
        object.__setattr__(self, "status", status)
        for name in ("tool_launched", "csynth_launched", "timed_out"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        if self.returncode is not None and (
            isinstance(self.returncode, bool)
            or not isinstance(self.returncode, int)
        ):
            raise TypeError("returncode must be an integer or null")
        if self.evidence_sha256 is not None:
            _require_sha256(self.evidence_sha256, "evidence_sha256")
        if self.evidence_ref is not None:
            ref = str(self.evidence_ref).strip()
            if not ref or len(ref) > 300:
                raise ValueError("evidence_ref must be a short non-empty string")
            object.__setattr__(self, "evidence_ref", ref)
        if self.evidence_view != "agent_safe":
            raise ValueError("Original CSYNTH evidence must be agent_safe")

    @property
    def authoritative_pass(self) -> bool:
        return (
            self.status == "passed"
            and self.tool_launched
            and self.csynth_launched
            and self.returncode == 0
            and not self.timed_out
            and self.evidence_sha256 is not None
        )

    def matches(self, *, source_sha256: str, top_function: str) -> bool:
        return (
            self.source_sha256 == source_sha256
            and self.top_function == top_function
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_view": self.evidence_view,
            "phase": "original_csynth",
            "source_sha256": self.source_sha256,
            "top_function": self.top_function,
            "status": self.status,
            "tool_launched": self.tool_launched,
            "csynth_launched": self.csynth_launched,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "evidence_sha256": self.evidence_sha256,
            "evidence_ref": self.evidence_ref,
            "authoritative_pass": self.authoritative_pass,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OriginalCsynthEvidence":
        if not isinstance(payload, Mapping):
            raise TypeError("Original CSYNTH evidence must be a mapping")
        allowed = {
            "schema_version", "evidence_view", "phase",
            "source_sha256", "top_function", "status",
            "tool_launched", "csynth_launched", "returncode",
            "timed_out", "evidence_sha256", "evidence_ref",
            "authoritative_pass",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "unknown Original CSYNTH evidence fields: "
                + ", ".join(sorted(unknown))
            )
        if payload.get("schema_version", 1) != 1:
            raise ValueError("unsupported Original CSYNTH schema_version")
        if payload.get("phase") != "original_csynth":
            raise ValueError("phase must be original_csynth")
        return cls(
            source_sha256=payload.get("source_sha256"),
            top_function=payload.get("top_function"),
            status=payload.get("status"),
            tool_launched=payload.get("tool_launched"),
            csynth_launched=payload.get("csynth_launched"),
            returncode=payload.get("returncode"),
            timed_out=payload.get("timed_out"),
            evidence_sha256=payload.get("evidence_sha256"),
            evidence_ref=payload.get("evidence_ref"),
            evidence_view=payload.get("evidence_view", "agent_safe"),
        )


def load_original_csynth_evidence(
    path: str | Path,
) -> OriginalCsynthEvidence:
    """Load a strict typed evidence file without accepting inferred fields."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    value = json.loads(source.read_text(encoding="utf-8"))
    return OriginalCsynthEvidence.from_dict(value)


@dataclass(frozen=True, slots=True)
class RefactorEligibilityReport:
    source_sha256: str
    top_function: str
    public_test_mode: str
    execution_status: EligibilityStatus
    primary_sample_status: EligibilityStatus
    original_csynth_evidence: OriginalCsynthEvidence | None
    reason_codes: tuple[str, ...]
    boundary: SourceBoundaryEvidence
    evidence_view: str = "agent_safe"
    schema_version: int = 1
    policy_version: int = 2

    def __post_init__(self) -> None:
        _require_sha256(self.source_sha256, "source_sha256")
        if not self.top_function or not self.top_function.isidentifier():
            raise ValueError(
                "top_function must be an unqualified identifier"
            )
        if self.public_test_mode not in {"auto", "provided", "none"}:
            raise ValueError("unsupported public_test_mode")
        if not isinstance(self.execution_status, EligibilityStatus):
            object.__setattr__(
                self,
                "execution_status",
                EligibilityStatus(str(self.execution_status)),
            )
        if not isinstance(self.primary_sample_status, EligibilityStatus):
            object.__setattr__(
                self,
                "primary_sample_status",
                EligibilityStatus(str(self.primary_sample_status)),
            )
        if (
            self.original_csynth_evidence is not None
            and not isinstance(
                self.original_csynth_evidence, OriginalCsynthEvidence
            )
        ):
            raise TypeError(
                "original_csynth_evidence must be typed evidence or null"
            )
        codes = tuple(
            dict.fromkeys(_safe_code(item) for item in self.reason_codes)
        )
        object.__setattr__(self, "reason_codes", codes)

    @property
    def execution_allowed(self) -> bool:
        return self.execution_status is EligibilityStatus.ALLOWED

    @property
    def primary_sample_eligible(self) -> bool:
        return self.primary_sample_status is EligibilityStatus.ALLOWED

    @property
    def original_csynth_passed(self) -> bool | None:
        evidence = self.original_csynth_evidence
        if evidence is None:
            return None
        if not evidence.matches(
            source_sha256=self.source_sha256,
            top_function=self.top_function,
        ):
            return None
        if evidence.authoritative_pass:
            return True
        if evidence.status in {"failed", "error", "blocked"}:
            return False
        return None

    @property
    def execution_reason_code(self) -> str:
        if self.public_test_mode == "none":
            return "public_test_source_required"
        if self.public_test_mode == "provided":
            return "operator_provided_public_tests"
        if self.boundary.private_global_dependencies:
            return "auto_public_tests_private_global_dependency"
        if not self.boundary.analysis_complete:
            return "auto_public_tests_boundary_unresolved"
        return "auto_public_tests_explicit_io_boundary"

    @property
    def primary_sample_reason_code(self) -> str:
        if self.execution_status is not EligibilityStatus.ALLOWED:
            return self.execution_reason_code
        for code in (
            "primary_sample_eligible",
            "original_csynth_failed",
            "original_csynth_identity_mismatch",
            "original_csynth_evidence_incomplete",
            "original_csynth_evidence_not_supplied",
        ):
            if code in self.reason_codes:
                return code
        return "original_csynth_evidence_not_supplied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "evidence_view": self.evidence_view,
            "source_sha256": self.source_sha256,
            "top_function": self.top_function,
            "public_test_mode": self.public_test_mode,
            "execution_status": self.execution_status.value,
            "execution_allowed": self.execution_allowed,
            "primary_sample_status": self.primary_sample_status.value,
            "primary_sample_eligible": self.primary_sample_eligible,
            "original_csynth_passed": self.original_csynth_passed,
            "original_csynth_evidence": (
                None
                if self.original_csynth_evidence is None
                else self.original_csynth_evidence.to_dict()
            ),
            "reason_codes": list(self.reason_codes),
            "execution_reason_code": self.execution_reason_code,
            "primary_sample_reason_code": (
                self.primary_sample_reason_code
            ),
            "boundary": self.boundary.to_dict(),
            "claims": {
                "functional_correctness_proven": False,
                "original_csynth_boolean_assertion_sufficient": False,
                "original_csynth_identity_bound": True,
                "hidden_evidence_used": False,
            },
        }

    def to_rejection(self) -> dict[str, Any]:
        reason = self.execution_reason_code
        return {
            "schema_version": 1,
            "kind": "refactor_eligibility_rejected",
            "reason_code": reason,
            "execution_status": self.execution_status.value,
            "primary_sample_status": self.primary_sample_status.value,
            "public_test_mode": self.public_test_mode,
            "top_function": self.top_function,
            "source_sha256": self.source_sha256,
            "private_global_dependencies": list(
                self.boundary.private_global_dependencies
            ),
            "analysis_complete": self.boundary.analysis_complete,
            "ambiguity_codes": list(self.boundary.ambiguity_codes),
            "provider_call_observed": False,
            "credential_value_persisted": False,
            "hidden_evidence_exposed": False,
        }


_TYPE_OR_QUALIFIER = frozenset(
    {
        "alignas",
        "auto",
        "bool",
        "char",
        "char16_t",
        "char32_t",
        "const",
        "constexpr",
        "constinit",
        "double",
        "extern",
        "float",
        "int",
        "long",
        "mutable",
        "register",
        "short",
        "signed",
        "static",
        "struct",
        "thread_local",
        "unsigned",
        "volatile",
        "wchar_t",
    }
)
_DECLARATION_EXCLUSIONS = frozenset(
    {
        "class",
        "concept",
        "enum",
        "namespace",
        "static_assert",
        "template",
        "typedef",
        "union",
        "using",
    }
)
_CONTROL_NAMES = frozenset(
    {"if", "for", "while", "switch", "catch", "sizeof", "alignof"}
)


def assess_refactor_eligibility(
    *,
    source_code: str,
    top_function: str,
    public_test_mode: str,
    original_csynth_evidence: (
        OriginalCsynthEvidence | Mapping[str, Any] | None
    ) = None,
) -> RefactorEligibilityReport:
    """Return execution-boundary and primary-sample eligibility evidence."""

    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("source_code must not be empty")
    if not isinstance(top_function, str) or not top_function.isidentifier():
        raise ValueError(
            "top_function must be an unqualified identifier"
        )
    mode = str(public_test_mode).strip().casefold()
    if mode not in {"auto", "provided", "none"}:
        raise ValueError("public_test_mode must be auto, provided, or none")
    evidence = original_csynth_evidence
    if isinstance(evidence, Mapping):
        evidence = OriginalCsynthEvidence.from_dict(evidence)
    elif evidence is not None and not isinstance(
        evidence, OriginalCsynthEvidence
    ):
        raise TypeError(
            "original_csynth_evidence must be typed evidence, mapping, or null"
        )

    boundary = analyze_source_boundary(
        source_code=source_code,
        top_function=top_function,
    )
    reasons: list[str] = []

    if mode == "none":
        execution = EligibilityStatus.REJECTED
        reasons.append("public_test_source_required")
    elif mode == "provided":
        execution = EligibilityStatus.ALLOWED
        reasons.append("operator_provided_public_tests")
    elif boundary.private_global_dependencies:
        execution = EligibilityStatus.REJECTED
        reasons.append("auto_public_tests_private_global_dependency")
    elif not boundary.analysis_complete:
        execution = EligibilityStatus.REVIEW_REQUIRED
        reasons.append("auto_public_tests_boundary_unresolved")
    else:
        execution = EligibilityStatus.ALLOWED
        reasons.append("auto_public_tests_explicit_io_boundary")

    source_digest = sha256(source_code.encode("utf-8")).hexdigest()
    if execution is EligibilityStatus.REJECTED:
        primary = EligibilityStatus.REJECTED
    elif execution is EligibilityStatus.REVIEW_REQUIRED:
        primary = EligibilityStatus.REVIEW_REQUIRED
    elif evidence is None:
        primary = EligibilityStatus.NOT_EVALUATED
        reasons.append("original_csynth_evidence_not_supplied")
    elif not evidence.matches(
        source_sha256=source_digest,
        top_function=top_function,
    ):
        primary = EligibilityStatus.REVIEW_REQUIRED
        reasons.append("original_csynth_identity_mismatch")
    elif evidence.authoritative_pass:
        primary = EligibilityStatus.ALLOWED
        reasons.append("primary_sample_eligible")
    elif evidence.status in {"failed", "error", "blocked"}:
        primary = EligibilityStatus.REJECTED
        reasons.append("original_csynth_failed")
    else:
        primary = EligibilityStatus.REVIEW_REQUIRED
        reasons.append("original_csynth_evidence_incomplete")

    if (
        evidence is not None
        and evidence.authoritative_pass
        and execution is not EligibilityStatus.ALLOWED
    ):
        reasons.append("original_csynth_alone_not_sufficient")

    return RefactorEligibilityReport(
        source_sha256=source_digest,
        top_function=top_function,
        public_test_mode=mode,
        execution_status=execution,
        primary_sample_status=primary,
        original_csynth_evidence=evidence,
        reason_codes=tuple(reasons),
        boundary=boundary,
    )


def analyze_source_boundary(
    *,
    source_code: str,
    top_function: str,
) -> SourceBoundaryEvidence:
    """Analyze reachable mutable file-scope dependencies conservatively."""

    tokens, preprocessor_count, lexical_ambiguities = _tokenize(source_code)
    functions, function_ambiguities = _find_functions(tokens)
    ambiguities = list(lexical_ambiguities) + list(function_ambiguities)
    top_found = top_function in functions

    function_spans = _function_spans(tokens)
    globals_found, global_ambiguities = _find_file_scope_objects(
        tokens,
        function_spans=function_spans,
    )
    ambiguities.extend(global_ambiguities)

    reachable: set[str] = set()
    dependencies: set[str] = set()
    if top_found:
        queue = deque([top_function])
        while queue:
            name = queue.popleft()
            if name in reachable:
                continue
            reachable.add(name)
            body = functions[name].tokens
            body_ids = {
                token.value for token in body if token.kind == "identifier"
            }
            shadowed, shadow_ambiguities = _local_shadow_evidence(
                body,
                globals_found,
            )
            ambiguities.extend(
                f"{name}:{item}" for item in shadow_ambiguities
            )
            for global_name in globals_found:
                if global_name not in body_ids:
                    continue
                if global_name in shadowed:
                    if _has_explicit_global_qualification(body, global_name):
                        dependencies.add(global_name)
                    else:
                        ambiguities.append(
                            f"{name}:global_name_shadowed:{global_name}"
                        )
                else:
                    dependencies.add(global_name)
            for called in _called_function_names(body):
                if called in functions and called not in reachable:
                    queue.append(called)
    else:
        ambiguities.append("top_function_definition_not_found")

    normalized_ambiguities = tuple(sorted(set(ambiguities)))
    return SourceBoundaryEvidence(
        analysis_complete=top_found and not normalized_ambiguities,
        top_function_found=top_found,
        reachable_functions=tuple(sorted(reachable)),
        mutable_file_scope_objects=tuple(sorted(globals_found)),
        private_global_dependencies=tuple(sorted(dependencies)),
        ambiguity_codes=normalized_ambiguities,
        preprocessor_line_count=preprocessor_count,
    )


def _tokenize(
    source: str,
) -> tuple[tuple[_Token, ...], int, tuple[str, ...]]:
    tokens: list[_Token] = []
    ambiguities: list[str] = []
    preprocessor_count = 0
    index = 0
    line = 1
    line_has_nonspace = False
    length = len(source)

    while index < length:
        char = source[index]
        if char == "\n":
            line += 1
            index += 1
            line_has_nonspace = False
            continue
        if char.isspace():
            index += 1
            continue

        if char == "#" and not line_has_nonspace:
            preprocessor_count += 1
            directive_start = index + 1
            directive_end = source.find("\n", directive_start)
            if directive_end < 0:
                directive_end = length
            directive_text = source[
                directive_start:directive_end
            ].lstrip()
            directive = (
                directive_text.split(None, 1)[0]
                if directive_text
                else ""
            )
            if directive in {
                "define",
                "elif",
                "else",
                "endif",
                "if",
                "ifdef",
                "ifndef",
                "undef",
            }:
                ambiguities.append(
                    f"preprocessor_boundary_semantics:{directive}"
                )
            while index < length and source[index] != "\n":
                if (
                    source[index] == "\\"
                    and index + 1 < length
                    and source[index + 1] == "\n"
                ):
                    index += 2
                    line += 1
                    continue
                index += 1
            continue

        line_has_nonspace = True
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                ambiguities.append("unterminated_block_comment")
                break
            line += source[index : end + 2].count("\n")
            index = end + 2
            continue

        if char in {'"', "'"}:
            quote = char
            start = index
            start_line = line
            index += 1
            escaped = False
            terminated = False
            while index < length:
                current = source[index]
                if current == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    index += 1
                    terminated = True
                    break
                index += 1
            if not terminated:
                ambiguities.append("unterminated_literal")
                break
            raw = source[start:index]
            tokens.append(_Token("literal", raw, start_line, start))
            continue

        if char.isalpha() or char == "_":
            start = index
            while (
                index < length
                and (source[index].isalnum() or source[index] == "_")
            ):
                index += 1
            tokens.append(
                _Token("identifier", source[start:index], line, start)
            )
            continue

        if char.isdigit():
            start = index
            while (
                index < length
                and (
                    source[index].isalnum()
                    or source[index] in "._'"
                )
            ):
                index += 1
            tokens.append(_Token("number", source[start:index], line, start))
            continue

        two = source[index : index + 2]
        three = source[index : index + 3]
        if three in {"<<=", ">>=", "..."}:
            tokens.append(_Token("punct", three, line, index))
            index += 3
            continue
        if two in {
            "::",
            "->",
            "++",
            "--",
            "&&",
            "||",
            "==",
            "!=",
            "<=",
            ">=",
            "<<",
            ">>",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "&=",
            "|=",
            "^=",
        }:
            tokens.append(_Token("punct", two, line, index))
            index += 2
            continue
        tokens.append(_Token("punct", char, line, index))
        index += 1

    return tuple(tokens), preprocessor_count, tuple(ambiguities)


def _matching_index(
    tokens: Sequence[_Token],
    start: int,
    opening: str,
    closing: str,
) -> int | None:
    depth = 0
    for index in range(start, len(tokens)):
        value = tokens[index].value
        if value == opening:
            depth += 1
        elif value == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _transparent_extern_c_braces(
    tokens: Sequence[_Token],
) -> tuple[set[int], set[int], list[str]]:
    opens: set[int] = set()
    closes: set[int] = set()
    ambiguities: list[str] = []
    for index in range(len(tokens) - 2):
        if (
            tokens[index].value == "extern"
            and tokens[index + 1].kind == "literal"
            and tokens[index + 1].value.replace(" ", "") == '"C"'
            and tokens[index + 2].value == "{"
        ):
            close = _matching_index(tokens, index + 2, "{", "}")
            if close is None:
                ambiguities.append("unterminated_extern_c_block")
            else:
                opens.add(index + 2)
                closes.add(close)
    return opens, closes, ambiguities


def _effective_depths(
    tokens: Sequence[_Token],
) -> tuple[list[int], tuple[str, ...]]:
    transparent_open, transparent_close, ambiguities = (
        _transparent_extern_c_braces(tokens)
    )
    depths: list[int] = []
    depth = 0
    for index, token in enumerate(tokens):
        if token.value == "}":
            if index not in transparent_close:
                depth = max(depth - 1, 0)
        depths.append(depth)
        if token.value == "{":
            if index not in transparent_open:
                depth += 1
    return depths, tuple(ambiguities)


def _find_functions(
    tokens: Sequence[_Token],
) -> tuple[dict[str, _FunctionBody], tuple[str, ...]]:
    depths, ambiguities = _effective_depths(tokens)
    found: dict[str, _FunctionBody] = {}
    issues = list(ambiguities)
    index = 0
    while index < len(tokens) - 2:
        token = tokens[index]
        if (
            depths[index] == 0
            and token.kind == "identifier"
            and token.value not in _CONTROL_NAMES
            and tokens[index + 1].value == "("
            and (index == 0 or tokens[index - 1].value not in {".", "->", "="})
        ):
            close_paren = _matching_index(tokens, index + 1, "(", ")")
            if close_paren is None:
                issues.append(f"unmatched_parameter_list:{token.value}")
                index += 1
                continue
            cursor = close_paren + 1
            while cursor < len(tokens) and tokens[cursor].value in {
                "const",
                "constexpr",
                "noexcept",
                "override",
                "final",
            }:
                cursor += 1
            if cursor < len(tokens) and tokens[cursor].value == "->":
                cursor += 1
                while cursor < len(tokens) and tokens[cursor].value not in {
                    "{",
                    ";",
                }:
                    cursor += 1
            if cursor < len(tokens) and tokens[cursor].value == "{":
                close_body = _matching_index(tokens, cursor, "{", "}")
                if close_body is None:
                    issues.append(f"unmatched_function_body:{token.value}")
                    index += 1
                    continue
                if token.value in found:
                    issues.append(
                        f"multiple_function_definitions:{token.value}"
                    )
                else:
                    found[token.value] = _FunctionBody(
                        name=token.value,
                        tokens=tuple(tokens[cursor + 1 : close_body]),
                    )
                index = close_body + 1
                continue
        index += 1
    return found, tuple(issues)


def _function_spans(
    tokens: Sequence[_Token],
) -> tuple[tuple[int, int], ...]:
    depths, _ = _effective_depths(tokens)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(tokens) - 2:
        if (
            depths[index] == 0
            and tokens[index].kind == "identifier"
            and tokens[index].value not in _CONTROL_NAMES
            and tokens[index + 1].value == "("
        ):
            close_paren = _matching_index(tokens, index + 1, "(", ")")
            if close_paren is not None:
                cursor = close_paren + 1
                while cursor < len(tokens) and tokens[cursor].value not in {
                    "{",
                    ";",
                }:
                    cursor += 1
                if cursor < len(tokens) and tokens[cursor].value == "{":
                    close_body = _matching_index(tokens, cursor, "{", "}")
                    if close_body is not None:
                        spans.append((index, close_body))
                        index = close_body + 1
                        continue
        index += 1
    return tuple(spans)


def _inside_spans(index: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= index <= end for start, end in spans)


def _find_file_scope_objects(
    tokens: Sequence[_Token],
    *,
    function_spans: Sequence[tuple[int, int]],
) -> tuple[set[str], tuple[str, ...]]:
    depths, depth_issues = _effective_depths(tokens)
    transparent_open, transparent_close, transparent_issues = (
        _transparent_extern_c_braces(tokens)
    )
    names: set[str] = set()
    ambiguities = list(depth_issues) + list(transparent_issues)
    statement: list[_Token] = []
    square_depth = 0
    brace_depth = 0

    for index, token in enumerate(tokens):
        if _inside_spans(index, function_spans):
            continue
        if depths[index] != 0:
            continue
        if token.value == "[":
            square_depth += 1
        elif token.value == "]":
            square_depth = max(square_depth - 1, 0)
        elif token.value == "{":
            if index not in transparent_open:
                brace_depth += 1
        elif token.value == "}":
            if index not in transparent_close:
                brace_depth = max(brace_depth - 1, 0)

        statement.append(token)
        if token.value == ";" and square_depth == 0 and brace_depth == 0:
            discovered, issue = _declaration_names(statement)
            names.update(discovered)
            if issue is not None:
                ambiguities.append(issue)
            statement = []

    return names, tuple(ambiguities)


def _declaration_names(
    statement: Sequence[_Token],
) -> tuple[set[str], str | None]:
    values = [item.value for item in statement if item.value != ";"]
    if not values:
        return set(), None
    if any(value in _DECLARATION_EXCLUSIONS for value in values):
        return set(), None
    initializer = values.index("=") if "=" in values else len(values)
    declaration_prefix = values[:initializer]
    if "(" in declaration_prefix or ")" in declaration_prefix:
        if "*" in declaration_prefix:
            return (
                set(),
                "unresolved_file_scope_callable_or_pointer_declaration",
            )
        return set(), None
    if "const" in values or "constexpr" in values or "constinit" in values:
        return set(), None

    chunks: list[list[_Token]] = [[]]
    bracket_depth = 0
    brace_depth = 0
    for token in statement:
        if token.value == "[":
            bracket_depth += 1
        elif token.value == "]":
            bracket_depth = max(bracket_depth - 1, 0)
        elif token.value == "{":
            brace_depth += 1
        elif token.value == "}":
            brace_depth = max(brace_depth - 1, 0)
        if (
            token.value == ","
            and bracket_depth == 0
            and brace_depth == 0
        ):
            chunks.append([])
        else:
            chunks[-1].append(token)

    discovered: set[str] = set()
    for chunk in chunks:
        before_initializer: list[_Token] = []
        nested = 0
        for token in chunk:
            if token.value in {"[", "{"}:
                nested += 1
            elif token.value in {"]", "}"}:
                nested = max(nested - 1, 0)
            if token.value == "=" and nested == 0:
                break
            before_initializer.append(token)
        identifiers = [
            token.value
            for token in before_initializer
            if token.kind == "identifier"
            and token.value not in _TYPE_OR_QUALIFIER
        ]
        if identifiers:
            discovered.add(identifiers[-1])

    if not discovered:
        return set(), "unresolved_file_scope_declaration"
    return discovered, None


def _local_shadow_evidence(
    body: Sequence[_Token],
    globals_found: set[str],
) -> tuple[set[str], tuple[str, ...]]:
    shadowed: set[str] = set()
    issues: list[str] = []
    statement: list[_Token] = []
    paren_depth = 0
    for token in body:
        if token.value == "(":
            paren_depth += 1
        elif token.value == ")":
            paren_depth = max(paren_depth - 1, 0)
        statement.append(token)
        if token.value == ";" and paren_depth == 0:
            identifiers = [
                item.value
                for item in statement
                if item.kind == "identifier"
            ]
            begins_like_declaration = bool(
                identifiers
                and (
                    identifiers[0] in _TYPE_OR_QUALIFIER
                    or (
                        len(identifiers) >= 2
                        and identifiers[0][:1].isupper()
                    )
                )
            )
            if begins_like_declaration:
                for name in globals_found:
                    if name in identifiers[1:]:
                        shadowed.add(name)
            statement = []
    return shadowed, tuple(issues)


def _has_explicit_global_qualification(
    body: Sequence[_Token],
    name: str,
) -> bool:
    for index, token in enumerate(body):
        if (
            token.value == name
            and index > 0
            and body[index - 1].value == "::"
        ):
            return True
    return False


def _called_function_names(body: Sequence[_Token]) -> set[str]:
    return {
        body[index].value
        for index in range(len(body) - 1)
        if body[index].kind == "identifier"
        and body[index + 1].value == "("
        and body[index].value not in _CONTROL_NAMES
    }


def _require_sha256(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip().lower()
    if (
        len(cleaned) != 64
        or any(character not in "0123456789abcdef" for character in cleaned)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return cleaned


def _safe_code(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("reason code must be a string")
    cleaned = value.strip().casefold()
    if (
        not cleaned
        or not cleaned[0].isalpha()
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyz0123456789_-.:"
            for character in cleaned
        )
    ):
        raise ValueError(f"unsafe reason code: {value!r}")
    return cleaned


def report_json(report: RefactorEligibilityReport) -> str:
    """Return a stable JSON representation for tools and fixtures."""

    return json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
