"""Declarative diagnostic classification catalog.

The catalog identifies a diagnostic's category and stage.  It deliberately
does not assign an owner; ownership is an execution-evidence decision made by
the existing evaluation and routing layers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from agrefactor.evidence import FeedbackCategory, FeedbackStage

_EVIDENCE_FIELDS = frozenset({
    "raw_diagnostic", "tool_launched", "execution_completed", "source_location",
    "compile_unit_provenance", "original_isolation", "runtime_contract",
    "invocation_identity", "report_identity",
})
_ACTIONS = frozenset({
    "review_unknown", "repair_candidate_if_proven", "repair_testbench_if_proven",
    "fix_configuration",
})
_REPAIR_ACTIONS = frozenset({"repair_candidate_if_proven", "repair_testbench_if_proven"})


def _fields(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise DiagnosticCatalogError(f"{context} has unknown fields: {sorted(unknown)}")


def _strings(value: Any, allowed: frozenset[str], context: str) -> tuple[str, ...]:
    if (not isinstance(value, list) or not all(isinstance(item, str) for item in value)
            or len(value) != len(set(value)) or set(value) - allowed):
        raise DiagnosticCatalogError(f"{context} contains invalid or duplicate values")
    return tuple(value)


class DiagnosticCatalogError(ValueError):
    """Raised when the versioned catalog is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class DiagnosticRule:
    diagnostic_id: str
    stage: FeedbackStage
    category: FeedbackCategory
    category_id: str
    summary: str
    parser_rule: str
    confidence: str
    priority: int
    owner_policy: str
    evidence_requirements: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    evaluation_split: str
    _hls_codes: frozenset[str]
    _patterns: tuple[re.Pattern[str], ...]

    def matches(self, record: Mapping[str, Any]) -> bool:
        message_id = str(record.get("message_id") or "").upper()
        if self._hls_codes and message_id not in self._hls_codes:
            return False
        if self._patterns and not any(
            pattern.search(str(record.get("message", "")))
            for pattern in self._patterns
        ):
            return False
        return bool(self._hls_codes or self._patterns)


@dataclass(frozen=True, slots=True)
class DiagnosticMatch:
    """A classification result with no ownership or routing decision."""

    diagnostic_id: str
    category: FeedbackCategory
    category_id: str
    stage: FeedbackStage
    summary: str
    parser_rule: str
    confidence: str
    owner_policy: str
    evidence_requirements: tuple[str, ...]
    allowed_actions: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "diagnostic_id": self.diagnostic_id,
            "category_id": self.category_id,
            "catalog_stage": self.stage.value,
            "owner_policy": self.owner_policy,
            "evidence_requirements": list(self.evidence_requirements),
            "allowed_actions": list(self.allowed_actions),
            "classification_confidence": self.confidence,
            "parser_rule": self.parser_rule,
        }


class DiagnosticCatalog:
    """Validated, deterministic view of the declarative catalog."""

    schema_version = 1

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise DiagnosticCatalogError("catalog must be a JSON object")
        _fields(payload, {"schema_version", "categories", "rules"}, "catalog")
        if payload.get("schema_version") != self.schema_version:
            raise DiagnosticCatalogError("unsupported catalog schema_version")
        categories = payload.get("categories")
        rules = payload.get("rules")
        if not isinstance(categories, Mapping) or not isinstance(rules, list):
            raise DiagnosticCatalogError("catalog requires categories and rules")
        self._categories = self._validate_categories(categories)
        self._rules = tuple(self._build_rule(rule) for rule in rules)
        ids = [rule.diagnostic_id for rule in self._rules]
        if len(ids) != len(set(ids)):
            raise DiagnosticCatalogError("diagnostic rule ids must be unique")
        for index, rule in enumerate(self._rules):
            for other in self._rules[index + 1:]:
                if (rule.stage is other.stage and rule.priority == other.priority
                        and (rule.evaluation_split == other.evaluation_split
                             or "all" in {rule.evaluation_split, other.evaluation_split})
                        and rule.category_id != other.category_id
                        and (rule._hls_codes & other._hls_codes
                             or {p.pattern for p in rule._patterns} & {p.pattern for p in other._patterns})):
                    raise DiagnosticCatalogError("conflicting rules at the same priority")
        self._rules = tuple(sorted(self._rules, key=lambda rule: (-rule.priority, rule.diagnostic_id)))

    @property
    def rules(self) -> tuple[DiagnosticRule, ...]:
        return self._rules

    @property
    def categories(self) -> Mapping[str, Mapping[str, Any]]:
        return self._categories

    def classify(self, record: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
        match = self.match_record(record)
        if match is None:
            return None
        return (
            match.category.value,
            match.summary,
            match.parser_rule,
            match.confidence,
        )

    def match_record(self, record: Mapping[str, Any]) -> DiagnosticRule | None:
        stage = FeedbackStage(record.get("stage", "csynth"))
        split = str(record.get("evaluation_split", "public"))
        matches = [rule for rule in self._rules if rule.stage is stage
                   and rule.evaluation_split in {"all", split} and rule.matches(record)]
        if not matches:
            return None
        first = matches[0]
        if any(rule.priority == first.priority and rule.category_id != first.category_id
               for rule in matches[1:]):
            raise DiagnosticCatalogError("diagnostic matches conflicting rules at the same priority")
        return first

    def match(
        self,
        message: str | Mapping[str, Any],
        *,
        stage: FeedbackStage | str = FeedbackStage.CSYNTH,
        message_id: str | None = None,
        evaluation_split: str = "public",
    ) -> DiagnosticMatch | None:
        """Match one message while keeping classification separate from ownership."""
        normalized_stage = (
            stage if isinstance(stage, FeedbackStage) else FeedbackStage(str(stage))
        )
        if evaluation_split not in {"public", "hidden"}:
            raise DiagnosticCatalogError("unknown evaluation_split")
        if isinstance(message, Mapping):
            record = dict(message)
            record.setdefault("message_id", message_id)
        else:
            record = {"message": message, "message_id": message_id}
        record.update(stage=normalized_stage.value, evaluation_split=evaluation_split)
        rule = self.match_record(record)
        if rule is None or rule.stage is not normalized_stage:
            return None
        return DiagnosticMatch(
            diagnostic_id=rule.diagnostic_id,
            category=rule.category,
            category_id=rule.category_id,
            stage=rule.stage,
            summary=rule.summary,
            parser_rule=rule.parser_rule,
            confidence=rule.confidence,
            owner_policy=rule.owner_policy,
            evidence_requirements=rule.evidence_requirements,
            allowed_actions=(tuple(action for action in rule.allowed_actions if action not in _REPAIR_ACTIONS)
                             if evaluation_split == "hidden" else rule.allowed_actions),
        )

    @staticmethod
    def _validate_categories(value: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for name, definition in value.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise DiagnosticCatalogError("category names must be non-empty strings")
            if not isinstance(definition, Mapping):
                raise DiagnosticCatalogError(f"category {name!r} must be an object")
            _fields(definition, {"description", "feedback_category", "evidence_requirements", "allowed_actions"}, f"category {name!r}")
            try:
                FeedbackCategory(definition.get("feedback_category", name))
            except ValueError as exc:
                raise DiagnosticCatalogError(f"unknown category {name!r}") from exc
            allowed = definition.get("allowed_actions", [])
            required = definition.get("evidence_requirements", [])
            _strings(allowed, _ACTIONS, f"category {name!r} allowed_actions")
            _strings(required, _EVIDENCE_FIELDS, f"category {name!r} evidence_requirements")
            if set(allowed) & _REPAIR_ACTIONS and ("tool_launched" not in required or
                    not set(required) & {"source_location", "compile_unit_provenance", "execution_completed"}):
                raise DiagnosticCatalogError(f"category {name!r} repair requires execution/component evidence")
            result[name] = dict(definition)
        return result

    def _build_rule(self, value: Any) -> DiagnosticRule:
        if not isinstance(value, Mapping):
            raise DiagnosticCatalogError("each diagnostic rule must be an object")
        _fields(value, {"id", "match", "stage", "category", "summary", "parser_rule", "confidence", "owner_policy", "priority", "notes", "evaluation_split"}, "rule")
        required = ("id", "match", "stage", "category", "owner_policy", "priority")
        missing = [key for key in required if key not in value]
        if missing:
            raise DiagnosticCatalogError("rule missing fields: " + ", ".join(missing))
        if "owner" in value or "route" in value:
            raise DiagnosticCatalogError("rules may not assign owner or route directly")
        rule_id = value["id"]
        if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", rule_id):
            raise DiagnosticCatalogError("rule id must be a non-empty string")
        match = value["match"]
        if not isinstance(match, Mapping):
            raise DiagnosticCatalogError(f"rule {rule_id!r} has invalid match")
        _fields(match, {"hls_codes", "patterns"}, f"rule {rule_id!r} match")
        codes = match.get("hls_codes", [])
        patterns = match.get("patterns", [])
        if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
            raise DiagnosticCatalogError(f"rule {rule_id!r} has invalid hls_codes")
        if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
            raise DiagnosticCatalogError(f"rule {rule_id!r} has invalid patterns")
        if (not codes and not patterns) or any(not code.strip() for code in codes):
            raise DiagnosticCatalogError(f"rule {rule_id!r} must have a non-empty match")
        if any(not re.fullmatch(r"HLS \d+(?:-\d+)+", code) for code in codes):
            raise DiagnosticCatalogError(f"rule {rule_id!r} has invalid HLS codes")
        try:
            compiled = tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
        except re.error as exc:
            raise DiagnosticCatalogError(f"rule {rule_id!r} has invalid pattern") from exc
        if any(pattern.search("") for pattern in compiled):
            raise DiagnosticCatalogError(f"rule {rule_id!r} matches empty diagnostics")
        category_id = value["category"]
        if category_id not in self._categories:
            raise DiagnosticCatalogError(f"rule {rule_id!r} refers to an undefined category")
        definition = self._categories[category_id]
        try:
            stage = FeedbackStage(value["stage"])
            category = FeedbackCategory(definition.get("feedback_category", category_id))
        except ValueError as exc:
            raise DiagnosticCatalogError(f"rule {rule_id!r} has unknown stage/category") from exc
        owner_policy = value["owner_policy"]
        if owner_policy != "resolve_from_evidence":
            raise DiagnosticCatalogError(f"rule {rule_id!r} must resolve owner from evidence")
        priority = value["priority"]
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise DiagnosticCatalogError("priority must be an integer")
        confidence = value.get("confidence", "unknown")
        if confidence not in {"high", "partial", "aggregate", "unknown"}:
            raise DiagnosticCatalogError("invalid classification confidence")
        split = value.get("evaluation_split", "all")
        if split not in {"all", "public", "hidden"}:
            raise DiagnosticCatalogError("unknown evaluation_split")
        if split == "hidden" and set(definition.get("allowed_actions", [])) & _REPAIR_ACTIONS:
            raise DiagnosticCatalogError("Hidden rules cannot permit agent repair")
        return DiagnosticRule(
            diagnostic_id=rule_id,
            stage=stage,
            category=category,
            category_id=category_id,
            summary=str(value.get("summary", definition.get("description", rule_id))),
            parser_rule=str(value.get("parser_rule", rule_id)),
            confidence=confidence,
            priority=priority,
            owner_policy=owner_policy,
            evidence_requirements=tuple(definition.get("evidence_requirements", ())),
            allowed_actions=tuple(definition.get("allowed_actions", ())),
            evaluation_split=split,
            _hls_codes=frozenset(code.upper() for code in codes),
            _patterns=compiled,
        )


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "diagnostic_catalog.json"


def load_catalog(path: str | Path | None = None) -> DiagnosticCatalog:
    catalog_path = Path(path) if path is not None else default_catalog_path()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticCatalogError(f"unable to load diagnostic catalog: {catalog_path}") from exc
    return DiagnosticCatalog(payload)
