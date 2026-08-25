"""Deterministic, content-free Testbench semantic manifests.

The manifest is intentionally conservative.  It records hashes and structural
counts, never Testbench source, and gives the independent evidence auditor
enough information to reject an obviously weakened oracle before a repaired
Testbench can enter full validation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any


_SAFE_SPLITS = frozenset({"public", "hidden"})
_SAFE_SOURCE_KINDS = frozenset(
    {
        "generated",
        "derived",
        "cached",
        "provided",
        "filesystem",
        "external",
        "unspecified",
    }
)
_AUTO_SOURCE_KINDS = frozenset({"generated", "derived", "cached"})
_PROVIDED_SOURCE_KINDS = frozenset({"provided", "filesystem", "external"})
_ORACLE_MARKERS = (
    "assert",
    "expect_eq",
    "expect_ne",
    "check",
    "memcmp",
)
_ALLOWED_EDIT_CLASSES = (
    "include_and_declaration_repair",
    "namespace_and_linkage_shell_repair",
    "compile_only_syntax_repair",
)
_FORBIDDEN_EDIT_CLASSES = (
    "input_case_removal_or_replacement",
    "expected_value_change",
    "comparison_or_tolerance_weakening",
    "failure_signal_removal",
    "top_call_removal_or_reimplementation",
    "runtime_protocol_weakening",
)


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strip_comments(code: str) -> str:
    """Remove C/C++ comments while preserving quoted literal boundaries."""

    result: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(code):
        char = code[index]
        nxt = code[index + 1] if index + 1 < len(code) else ""
        if state == "line":
            if char == "\n":
                result.append(char)
                state = "code"
            else:
                result.append(" ")
            index += 1
            continue
        if state == "block":
            if char == "*" and nxt == "/":
                result.extend((" ", " "))
                index += 2
                state = "code"
            else:
                result.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if state == "quote":
            result.append(char)
            if char == "\\" and nxt:
                result.append(nxt)
                index += 2
                continue
            if char == quote:
                state = "code"
            index += 1
            continue
        if char == "/" and nxt == "/":
            result.extend((" ", " "))
            index += 2
            state = "line"
            continue
        if char == "/" and nxt == "*":
            result.extend((" ", " "))
            index += 2
            state = "block"
            continue
        result.append(char)
        if char in {'"', "'"}:
            state = "quote"
            quote = char
        index += 1
    return "".join(result)


def _literal_fingerprints(code: str) -> tuple[str, ...]:
    pattern = re.compile(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
        r'(?<![A-Za-z_])(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|'
        r'(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)[uUlLfF]*(?![A-Za-z_])'
    )
    return tuple(sorted(_sha256_text(item.group(0)) for item in pattern.finditer(code)))


def _normalized_fragment(value: str) -> str:
    return re.sub(r"[\s;{}]+", "", value).casefold()


def _oracle_fingerprints(code: str) -> tuple[str, ...]:
    statements = re.split(r"[;\n]", code)
    selected = []
    for statement in statements:
        lowered = statement.casefold()
        if (
            any(re.search(rf"\b{re.escape(marker)}\s*\(", lowered) for marker in _ORACLE_MARKERS)
            or re.search(r"==|!=|<=|>=|(?<![<])<(?![<=])|(?<![>])>(?![>=])", statement)
            or ("return" in lowered and any(token in statement for token in ("?", "&&", "||")))
        ):
            normalized = _normalized_fragment(statement)
            if normalized:
                selected.append(_sha256_text(normalized))
    return tuple(sorted(selected))


def _name_counts(code: str, names: tuple[str, ...]) -> tuple[dict[str, int], dict[str, int]]:
    calls: dict[str, int] = {}
    definitions: dict[str, int] = {}
    for name in names:
        escaped = re.escape(name)
        calls[name] = len(re.findall(rf"\b{escaped}\s*\(", code))
        definitions[name] = len(
            re.findall(rf"\b{escaped}\s*\([^;{{}}]*\)\s*\{{", code, re.DOTALL)
        )
    return calls, definitions


def testbench_revision_authorization(*, split: str, source_kind: str) -> str:
    normalized_split = _required(split, "split").casefold()
    normalized_kind = _required(source_kind, "source_kind").casefold()
    if normalized_split not in _SAFE_SPLITS:
        raise ValueError("unsupported Testbench split")
    if normalized_kind not in _SAFE_SOURCE_KINDS:
        raise ValueError("unsupported Testbench source kind")
    if normalized_split == "hidden":
        return "forbidden_hidden"
    if normalized_kind in _PROVIDED_SOURCE_KINDS:
        return "review_required_provided"
    if normalized_kind in _AUTO_SOURCE_KINDS:
        return "auto_public_bounded"
    if normalized_kind == "unspecified":
        return "review_required_unspecified"
    raise AssertionError("unreachable Testbench authorization")


@dataclass(frozen=True, slots=True)
class TestbenchSemanticManifest:
    suite_id: str
    split: str
    source_kind: str
    revision: int
    content_sha256: str
    size_bytes: int
    main_count: int
    top_reference_counts: Mapping[str, int] = field(default_factory=dict)
    top_definition_counts: Mapping[str, int] = field(default_factory=dict)
    oracle_marker_counts: Mapping[str, int] = field(default_factory=dict)
    comparison_count: int = 0
    comparison_operator_counts: Mapping[str, int] = field(default_factory=dict)
    return_guard_count: int = 0
    failure_signal_counts: Mapping[str, int] = field(default_factory=dict)
    runtime_protocol_counts: Mapping[str, int] = field(default_factory=dict)
    control_flow_counts: Mapping[str, int] = field(default_factory=dict)
    literal_fingerprints: tuple[str, ...] = ()
    oracle_fingerprints: tuple[str, ...] = ()
    manifest_sha256: str = ""

    schema_version = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_id", _required(self.suite_id, "suite_id"))
        split = _required(self.split, "split").casefold()
        source_kind = _required(self.source_kind, "source_kind").casefold()
        if split not in _SAFE_SPLITS:
            raise ValueError("unsupported Testbench split")
        if source_kind not in _SAFE_SOURCE_KINDS:
            raise ValueError("unsupported Testbench source kind")
        object.__setattr__(self, "split", split)
        object.__setattr__(self, "source_kind", source_kind)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        for name in ("size_bytes", "main_count", "comparison_count", "return_guard_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "top_reference_counts",
            "top_definition_counts",
            "oracle_marker_counts",
            "comparison_operator_counts",
            "failure_signal_counts",
            "runtime_protocol_counts",
            "control_flow_counts",
        ):
            normalized = _count_mapping(getattr(self, name), name)
            object.__setattr__(self, name, normalized)
        literals = tuple(self.literal_fingerprints)
        if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in literals):
            raise ValueError("literal_fingerprints must contain SHA-256 digests")
        object.__setattr__(self, "literal_fingerprints", tuple(sorted(literals)))
        oracles = tuple(self.oracle_fingerprints)
        if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in oracles):
            raise ValueError("oracle_fingerprints must contain SHA-256 digests")
        object.__setattr__(self, "oracle_fingerprints", tuple(sorted(oracles)))
        payload = self._payload(include_digest=False)
        digest = _canonical_sha256(payload)
        if self.manifest_sha256 and self.manifest_sha256 != digest:
            raise ValueError("manifest_sha256 does not match manifest content")
        object.__setattr__(self, "manifest_sha256", digest)

    def _payload(self, *, include_digest: bool) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "split": self.split,
            "source_kind": self.source_kind,
            "revision": self.revision,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "main_count": self.main_count,
            "top_reference_counts": dict(self.top_reference_counts),
            "top_definition_counts": dict(self.top_definition_counts),
            "oracle_marker_counts": dict(self.oracle_marker_counts),
            "comparison_count": self.comparison_count,
            "comparison_operator_counts": dict(self.comparison_operator_counts),
            "return_guard_count": self.return_guard_count,
            "failure_signal_counts": dict(self.failure_signal_counts),
            "runtime_protocol_counts": dict(self.runtime_protocol_counts),
            "control_flow_counts": dict(self.control_flow_counts),
            "literal_fingerprints": list(self.literal_fingerprints),
            "oracle_fingerprints": list(self.oracle_fingerprints),
            "allowed_edit_classes": list(_ALLOWED_EDIT_CLASSES),
            "forbidden_edit_classes": list(_FORBIDDEN_EDIT_CLASSES),
            "source_content_persisted": False,
            "hidden_content_persisted": False,
        }
        if include_digest:
            payload["manifest_sha256"] = self.manifest_sha256
        return payload

    def to_dict(self) -> dict[str, Any]:
        return self._payload(include_digest=True)


def build_testbench_semantic_manifest(
    code: str,
    *,
    suite_id: str,
    split: str,
    source_kind: str,
    revision: int,
    original_top_function: str | None = None,
    candidate_top_function: str | None = None,
) -> TestbenchSemanticManifest:
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Testbench code must not be empty")
    cleaned = _strip_comments(code)
    tops = tuple(
        item
        for item in (
            _required(original_top_function, "original_top_function")
            if original_top_function is not None
            else None,
            _required(candidate_top_function, "candidate_top_function")
            if candidate_top_function is not None
            else None,
        )
        if item is not None
    )
    references, definitions = _name_counts(cleaned, tops)
    lowered = cleaned.casefold()
    marker_counts = {
        marker: len(re.findall(rf"\b{re.escape(marker)}\s*\(", lowered))
        for marker in _ORACLE_MARKERS
    }
    comparison_patterns = {
        "equal": r"==",
        "not_equal": r"!=",
        "less_equal": r"<=",
        "greater_equal": r">=",
        "less": r"(?<![<])<(?![<=])",
        "greater": r"(?<![>])>(?![>=])",
    }
    comparison_counts = {
        name: len(re.findall(pattern, cleaned))
        for name, pattern in comparison_patterns.items()
    }
    comparisons = sum(comparison_counts.values())
    return_guards = len(
        re.findall(r"\breturn\b[^;]*(?:==|!=|\?|&&|\|\|)[^;]*;", cleaned, re.DOTALL)
    )
    control = {
        keyword: len(re.findall(rf"\b{keyword}\s*\(", cleaned))
        for keyword in ("if", "for", "while", "switch")
    }
    failure_signals = {
        "assert": marker_counts["assert"],
        "abort": len(re.findall(r"\babort\s*\(", lowered)),
        "exit": len(re.findall(r"\bexit\s*\(", lowered)),
        "return_guard": return_guards,
        "explicit_nonzero_return": len(
            re.findall(r"\breturn\s+(?!0\s*;)[^;]+;", cleaned)
        ),
    }
    protocol = {
        "stream_read": len(re.findall(r"(?:\.read\s*\(|\bread\s*\()", cleaned)),
        "stream_write": len(re.findall(r"(?:\.write\s*\(|\bwrite\s*\()", cleaned)),
        "memcpy": len(re.findall(r"\bmemcpy\s*\(", cleaned)),
        "interface_pragma": len(re.findall(r"#\s*pragma\s+HLS\s+INTERFACE\b", cleaned, re.IGNORECASE)),
    }
    return TestbenchSemanticManifest(
        suite_id=suite_id,
        split=split,
        source_kind=source_kind,
        revision=revision,
        content_sha256=_sha256_text(code.rstrip() + "\n"),
        size_bytes=len(code.encode("utf-8")),
        main_count=len(re.findall(r"\bmain\s*\(", cleaned)),
        top_reference_counts=references,
        top_definition_counts=definitions,
        oracle_marker_counts=marker_counts,
        comparison_count=comparisons,
        comparison_operator_counts=comparison_counts,
        return_guard_count=return_guards,
        failure_signal_counts=failure_signals,
        runtime_protocol_counts=protocol,
        control_flow_counts=control,
        literal_fingerprints=_literal_fingerprints(cleaned),
        oracle_fingerprints=_oracle_fingerprints(cleaned),
    )


def build_testbench_semantic_revision(
    before_code: str,
    after_code: str,
    *,
    suite_id: str,
    split: str,
    source_kind: str,
    original_top_function: str | None = None,
    candidate_top_function: str | None = None,
) -> dict[str, Any]:
    before = build_testbench_semantic_manifest(
        before_code,
        suite_id=suite_id,
        split=split,
        source_kind=source_kind,
        revision=0,
        original_top_function=original_top_function,
        candidate_top_function=candidate_top_function,
    )
    after = build_testbench_semantic_manifest(
        after_code,
        suite_id=suite_id,
        split=split,
        source_kind=source_kind,
        revision=1,
        original_top_function=original_top_function,
        candidate_top_function=candidate_top_function,
    )
    payload = {
        "schema_version": 1,
        "suite_id": before.suite_id,
        "split": before.split,
        "source_kind": before.source_kind,
        "authorization": testbench_revision_authorization(
            split=before.split,
            source_kind=before.source_kind,
        ),
        "parent_revision_id": f"tbm-{before.manifest_sha256[:24]}",
        "revision_id": f"tbm-{after.manifest_sha256[:24]}",
        "changed": before.content_sha256 != after.content_sha256,
        "before": before.to_dict(),
        "after": after.to_dict(),
        "source_content_persisted": False,
        "hidden_content_persisted": False,
    }
    payload["revision_sha256"] = _canonical_sha256(payload)
    return payload


def literal_counter(manifest: Mapping[str, Any]) -> Counter[str]:
    values = manifest.get("literal_fingerprints", ())
    if not isinstance(values, list):
        raise TypeError("literal_fingerprints must be a list")
    return Counter(str(item) for item in values)


def _count_mapping(value: Mapping[str, int], name: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    normalized: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = _required(raw_key, name)
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
            raise ValueError(f"{name} values must be non-negative integers")
        normalized[key] = raw_value
    return dict(sorted(normalized.items()))
