"""Typed Vitis HLS PPA evidence and the frozen Stage 3 latency comparator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET


PPA_SCHEMA_VERSION = 1
_MAX_REPORT_BYTES = 32 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESOURCE_NAMES = ("bram_18k", "dsp", "ff", "lut", "uram")
_RESOURCE_XML_TAGS = {
    "bram_18k": ("BRAM_18K", "BRAM18K", "BRAM"),
    "dsp": ("DSP", "DSP48E", "DSP48E1", "DSP48E2"),
    "ff": ("FF", "REG"),
    "lut": ("LUT",),
    "uram": ("URAM",),
}
_RESOURCE_LIMIT_FIELDS = {
    "bram_18k": "max_bram_18k",
    "dsp": "max_dsp",
    "ff": "max_ff",
    "lut": "max_lut",
    "uram": "max_uram",
}


class PpaReportFormat(str, Enum):
    XML = "xml"
    TEXT = "text"


class PpaComparisonDecision(str, Enum):
    BETTER = "better"
    NOT_BETTER = "not_better"
    INCOMPARABLE = "incomparable"


class PpaParseError(ValueError):
    """Raised when a report cannot form comparable Stage 3 PPA evidence."""


@dataclass(frozen=True, slots=True)
class PpaResourceUsage:
    """Resource counts extracted from one synthesis report."""

    bram_18k: int | None = None
    dsp: int | None = None
    ff: int | None = None
    lut: int | None = None
    uram: int | None = None

    def __post_init__(self) -> None:
        for name in _RESOURCE_NAMES:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer or null")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in _RESOURCE_NAMES}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "PpaResourceUsage":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise TypeError("resource usage must be a mapping or null")
        unknown = set(value) - set(_RESOURCE_NAMES)
        if unknown:
            raise ValueError(
                "Unknown resource fields: " + ", ".join(sorted(unknown))
            )
        return cls(**{name: value.get(name) for name in _RESOURCE_NAMES})


@dataclass(frozen=True, slots=True)
class PpaEvidence:
    """Comparable, source-backed PPA evidence for one correct candidate."""

    evidence_id: str
    parser_profile: str
    report_format: PpaReportFormat
    report_relative_path: str
    report_sha256: str
    comparison_context_identity_sha256: str
    latency_cycles_min: int | None
    latency_cycles_max: int
    initiation_interval_min: int | None
    initiation_interval_max: int | None
    target_clock_period_ns: float | None
    achieved_clock_period_ns: float | None
    resources_used: PpaResourceUsage
    resources_available: PpaResourceUsage
    max_resource_utilization_ratio: float | None
    objective_feasible: bool | None
    constraint_violations: tuple[str, ...] = ()
    parser_warnings: tuple[str, ...] = ()

    schema_version = PPA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        evidence_id = _required_id(self.evidence_id, "evidence_id")
        parser_profile = _required_id(self.parser_profile, "parser_profile")
        report_format = _enum(
            self.report_format,
            PpaReportFormat,
            "report_format",
        )
        relative = _relative_artifact_path(
            self.report_relative_path,
            "report_relative_path",
        )
        report_sha = _sha256(self.report_sha256, "report_sha256")
        context_sha = _sha256(
            self.comparison_context_identity_sha256,
            "comparison_context_identity_sha256",
        )
        latency_min = _optional_nonnegative_int(
            self.latency_cycles_min,
            "latency_cycles_min",
        )
        latency_max = _nonnegative_int(
            self.latency_cycles_max,
            "latency_cycles_max",
        )
        if latency_min is not None and latency_min > latency_max:
            raise ValueError("latency_cycles_min cannot exceed latency_cycles_max")
        ii_min = _optional_nonnegative_int(
            self.initiation_interval_min,
            "initiation_interval_min",
        )
        ii_max = _optional_nonnegative_int(
            self.initiation_interval_max,
            "initiation_interval_max",
        )
        if ii_min is not None and ii_max is not None and ii_min > ii_max:
            raise ValueError(
                "initiation_interval_min cannot exceed initiation_interval_max"
            )
        target_clock = _optional_positive_float(
            self.target_clock_period_ns,
            "target_clock_period_ns",
        )
        achieved_clock = _optional_positive_float(
            self.achieved_clock_period_ns,
            "achieved_clock_period_ns",
        )
        if not isinstance(self.resources_used, PpaResourceUsage):
            raise TypeError("resources_used must be PpaResourceUsage")
        if not isinstance(self.resources_available, PpaResourceUsage):
            raise TypeError("resources_available must be PpaResourceUsage")
        ratio = _optional_nonnegative_float(
            self.max_resource_utilization_ratio,
            "max_resource_utilization_ratio",
        )
        if self.objective_feasible is not None and not isinstance(
            self.objective_feasible,
            bool,
        ):
            raise TypeError("objective_feasible must be boolean or null")
        violations = _text_tuple(
            self.constraint_violations,
            "constraint_violations",
        )
        warnings = _text_tuple(self.parser_warnings, "parser_warnings")
        if self.objective_feasible is True and violations:
            raise ValueError(
                "objective_feasible=true cannot have constraint violations"
            )
        if self.objective_feasible is False and not violations:
            raise ValueError(
                "objective_feasible=false requires constraint violations"
            )

        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "parser_profile", parser_profile)
        object.__setattr__(self, "report_format", report_format)
        object.__setattr__(self, "report_relative_path", relative)
        object.__setattr__(self, "report_sha256", report_sha)
        object.__setattr__(
            self,
            "comparison_context_identity_sha256",
            context_sha,
        )
        object.__setattr__(self, "latency_cycles_min", latency_min)
        object.__setattr__(self, "latency_cycles_max", latency_max)
        object.__setattr__(self, "initiation_interval_min", ii_min)
        object.__setattr__(self, "initiation_interval_max", ii_max)
        object.__setattr__(self, "target_clock_period_ns", target_clock)
        object.__setattr__(self, "achieved_clock_period_ns", achieved_clock)
        object.__setattr__(self, "max_resource_utilization_ratio", ratio)
        object.__setattr__(self, "constraint_violations", violations)
        object.__setattr__(self, "parser_warnings", warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "parser_profile": self.parser_profile,
            "report_format": self.report_format.value,
            "report_relative_path": self.report_relative_path,
            "report_sha256": self.report_sha256,
            "comparison_context_identity_sha256": (
                self.comparison_context_identity_sha256
            ),
            "latency_cycles_min": self.latency_cycles_min,
            "latency_cycles_max": self.latency_cycles_max,
            "initiation_interval_min": self.initiation_interval_min,
            "initiation_interval_max": self.initiation_interval_max,
            "target_clock_period_ns": self.target_clock_period_ns,
            "achieved_clock_period_ns": self.achieved_clock_period_ns,
            "resources_used": self.resources_used.to_dict(),
            "resources_available": self.resources_available.to_dict(),
            "max_resource_utilization_ratio": (
                self.max_resource_utilization_ratio
            ),
            "objective_feasible": self.objective_feasible,
            "constraint_violations": list(self.constraint_violations),
            "parser_warnings": list(self.parser_warnings),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PpaEvidence":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "evidence_id",
                "parser_profile",
                "report_format",
                "report_relative_path",
                "report_sha256",
                "comparison_context_identity_sha256",
                "latency_cycles_min",
                "latency_cycles_max",
                "initiation_interval_min",
                "initiation_interval_max",
                "target_clock_period_ns",
                "achieved_clock_period_ns",
                "resources_used",
                "resources_available",
                "max_resource_utilization_ratio",
                "objective_feasible",
                "constraint_violations",
                "parser_warnings",
            },
            "PPA evidence",
        )
        return cls(
            evidence_id=value["evidence_id"],
            parser_profile=value["parser_profile"],
            report_format=value["report_format"],
            report_relative_path=value["report_relative_path"],
            report_sha256=value["report_sha256"],
            comparison_context_identity_sha256=value[
                "comparison_context_identity_sha256"
            ],
            latency_cycles_min=value["latency_cycles_min"],
            latency_cycles_max=value["latency_cycles_max"],
            initiation_interval_min=value["initiation_interval_min"],
            initiation_interval_max=value["initiation_interval_max"],
            target_clock_period_ns=value["target_clock_period_ns"],
            achieved_clock_period_ns=value["achieved_clock_period_ns"],
            resources_used=PpaResourceUsage.from_dict(
                value["resources_used"]
            ),
            resources_available=PpaResourceUsage.from_dict(
                value["resources_available"]
            ),
            max_resource_utilization_ratio=value[
                "max_resource_utilization_ratio"
            ],
            objective_feasible=value["objective_feasible"],
            constraint_violations=tuple(value["constraint_violations"]),
            parser_warnings=tuple(value["parser_warnings"]),
        )


@dataclass(frozen=True, slots=True)
class PpaComparison:
    decision: PpaComparisonDecision
    better: bool | None
    reason: str
    decisive_metric: str | None
    candidate_value: int | float | None
    incumbent_value: int | float | None

    schema_version = PPA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        decision = _enum(
            self.decision,
            PpaComparisonDecision,
            "decision",
        )
        if self.better is not None and not isinstance(self.better, bool):
            raise TypeError("better must be boolean or null")
        reason = _required_text(self.reason, "reason")
        decisive_metric = (
            None
            if self.decisive_metric is None
            else _required_id(self.decisive_metric, "decisive_metric")
        )
        if decision is PpaComparisonDecision.BETTER and self.better is not True:
            raise ValueError("better decision requires better=true")
        if (
            decision is PpaComparisonDecision.NOT_BETTER
            and self.better is not False
        ):
            raise ValueError("not_better decision requires better=false")
        if (
            decision is PpaComparisonDecision.INCOMPARABLE
            and self.better is not None
        ):
            raise ValueError("incomparable decision requires better=null")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "decisive_metric", decisive_metric)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision.value,
            "better": self.better,
            "reason": self.reason,
            "decisive_metric": self.decisive_metric,
            "candidate_value": self.candidate_value,
            "incumbent_value": self.incumbent_value,
        }


class VitisHlsPpaReportAdapter:
    """Parse one real or fixture Vitis HLS report without invoking tools."""

    adapter_version = 1

    def parse(
        self,
        work_dir: str | os.PathLike[str],
        *,
        top_function: str,
        parser_profile: str,
        comparison_context_identity_sha256: str,
        resource_limits: Mapping[str, int | None] | Any | None = None,
        evidence_id: str = "evidence-ppa",
    ) -> PpaEvidence:
        root = _real_directory(work_dir, "work_dir")
        top = _required_id(top_function, "top_function")
        profile = _required_id(parser_profile, "parser_profile")
        context_sha = _sha256(
            comparison_context_identity_sha256,
            "comparison_context_identity_sha256",
        )
        limits = _normalize_resource_limits(resource_limits)
        report_path, report_format = self._find_report(root, top)
        data = _read_regular_file(report_path)
        if report_format is PpaReportFormat.XML:
            raw = self._parse_xml(data)
        else:
            raw = self._parse_text(data.decode("utf-8", errors="replace"))

        latency_max = raw.get("latency_cycles_max")
        if latency_max is None:
            raise PpaParseError(
                "CSYNTH report lacks worst-case/maximum latency cycles"
            )
        resources_used = PpaResourceUsage.from_dict(raw.get("resources_used"))
        resources_available = PpaResourceUsage.from_dict(
            raw.get("resources_available")
        )
        ratio = _max_resource_ratio(resources_used, resources_available)
        feasible, violations, feasibility_warnings = _objective_feasibility(
            resources_used,
            limits,
        )
        warnings = tuple(raw.get("warnings", ())) + feasibility_warnings
        relative = report_path.relative_to(root).as_posix()
        return PpaEvidence(
            evidence_id=evidence_id,
            parser_profile=profile,
            report_format=report_format,
            report_relative_path=relative,
            report_sha256=sha256(data).hexdigest(),
            comparison_context_identity_sha256=context_sha,
            latency_cycles_min=raw.get("latency_cycles_min"),
            latency_cycles_max=latency_max,
            initiation_interval_min=raw.get("initiation_interval_min"),
            initiation_interval_max=raw.get("initiation_interval_max"),
            target_clock_period_ns=raw.get("target_clock_period_ns"),
            achieved_clock_period_ns=raw.get(
                "achieved_clock_period_ns"
            ),
            resources_used=resources_used,
            resources_available=resources_available,
            max_resource_utilization_ratio=ratio,
            objective_feasible=feasible,
            constraint_violations=violations,
            parser_warnings=warnings,
        )

    @staticmethod
    def _find_report(
        root: Path,
        top: str,
    ) -> tuple[Path, PpaReportFormat]:
        exact_xml = (
            root
            / "csynth"
            / "solution"
            / "syn"
            / "report"
            / f"{top}_csynth.xml"
        )
        exact_rpt = exact_xml.with_suffix(".rpt")
        for path, format_value in (
            (exact_xml, PpaReportFormat.XML),
            (exact_rpt, PpaReportFormat.TEXT),
        ):
            if path.is_symlink():
                raise PpaParseError("PPA report must not be a symbolic link")
            if path.is_file():
                return path.resolve(), format_value

        xml_matches = _safe_report_matches(root, f"{top}_csynth.xml")
        if len(xml_matches) == 1:
            return xml_matches[0], PpaReportFormat.XML
        if len(xml_matches) > 1:
            raise PpaParseError("multiple matching XML CSYNTH reports found")
        text_matches = _safe_report_matches(root, f"{top}_csynth.rpt")
        if len(text_matches) == 1:
            return text_matches[0], PpaReportFormat.TEXT
        if len(text_matches) > 1:
            raise PpaParseError("multiple matching text CSYNTH reports found")
        raise FileNotFoundError(
            f"No CSYNTH report found for top function {top!r} under {root}"
        )

    @staticmethod
    def _parse_xml(data: bytes) -> dict[str, Any]:
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise PpaParseError("Invalid Vitis HLS CSYNTH XML report") from exc

        values = _xml_leaf_values(root)
        used = _xml_resource_group(root, "Resources")
        available = _xml_resource_group(root, "AvailableResources")
        return {
            "latency_cycles_min": _first_int(
                values,
                ("Best-caseLatency", "BestCaseLatency"),
            ),
            "latency_cycles_max": _first_int(
                values,
                ("Worst-caseLatency", "WorstCaseLatency"),
            ),
            "initiation_interval_min": _first_int(
                values,
                ("Interval-min", "IntervalMin"),
            ),
            "initiation_interval_max": _first_int(
                values,
                ("Interval-max", "IntervalMax"),
            ),
            "target_clock_period_ns": _first_float(
                values,
                ("TargetClockPeriod",),
            ),
            "achieved_clock_period_ns": _first_float(
                values,
                ("EstimatedClockPeriod", "AchievedClockPeriod"),
            ),
            "resources_used": used,
            "resources_available": available,
            "warnings": (),
        }

    @staticmethod
    def _parse_text(text: str) -> dict[str, Any]:
        values: dict[str, Any] = {
            "latency_cycles_min": _regex_int(
                text,
                (
                    r"Best[- ]case\s+Latency\s*[:|]\s*(\d+)",
                    r"Latency\s*\(cycles\).*?\n.*?\|\s*(\d+)\s*\|",
                ),
            ),
            "latency_cycles_max": _regex_int(
                text,
                (
                    r"Worst[- ]case\s+Latency\s*[:|]\s*(\d+)",
                    r"Latency\s*\(cycles\).*?\n.*?\|\s*\d+\s*\|\s*(\d+)\s*\|",
                ),
            ),
            "initiation_interval_min": _regex_int(
                text,
                (r"Interval[- ]min\s*[:|]\s*(\d+)",),
            ),
            "initiation_interval_max": _regex_int(
                text,
                (r"Interval[- ]max\s*[:|]\s*(\d+)",),
            ),
            "target_clock_period_ns": _regex_float(
                text,
                (r"Target\s+Clock\s+Period\s*[:|]\s*([0-9.]+)",),
            ),
            "achieved_clock_period_ns": _regex_float(
                text,
                (
                    r"Estimated\s+Clock\s+Period\s*[:|]\s*([0-9.]+)",
                    r"Achieved\s+Clock\s+Period\s*[:|]\s*([0-9.]+)",
                ),
            ),
            "resources_used": {},
            "resources_available": {},
            "warnings": (
                "text_report_fallback_used",
            ),
        }
        for name, tags in _RESOURCE_XML_TAGS.items():
            tag_pattern = "|".join(re.escape(tag) for tag in tags)
            used = _regex_int(
                text,
                (
                    rf"(?:{tag_pattern})\s*[:|]\s*(\d+)",
                    rf"\|\s*(?:{tag_pattern})\s*\|\s*(\d+)\s*\|",
                ),
            )
            available = _regex_int(
                text,
                (
                    rf"Available\s+(?:{tag_pattern})\s*[:|]\s*(\d+)",
                    rf"\|\s*(?:{tag_pattern})\s*\|\s*\d+\s*\|\s*(\d+)\s*\|",
                ),
            )
            values["resources_used"][name] = used
            values["resources_available"][name] = available
        return values


class LatencyPpaComparator:
    """Implement the frozen deterministic Stage 3 v1 latency ordering."""

    comparator_version = 1

    def compare(
        self,
        candidate: PpaEvidence,
        incumbent: PpaEvidence,
        *,
        candidate_sequence: int,
        incumbent_sequence: int,
    ) -> PpaComparison:
        if not isinstance(candidate, PpaEvidence):
            raise TypeError("candidate must be PpaEvidence")
        if not isinstance(incumbent, PpaEvidence):
            raise TypeError("incumbent must be PpaEvidence")
        candidate_seq = _nonnegative_int(
            candidate_sequence,
            "candidate_sequence",
        )
        incumbent_seq = _nonnegative_int(
            incumbent_sequence,
            "incumbent_sequence",
        )
        if (
            candidate.comparison_context_identity_sha256
            != incumbent.comparison_context_identity_sha256
        ):
            return _comparison_incomparable("comparison_context_mismatch")
        if candidate.objective_feasible is not True:
            return _comparison_incomparable(
                "candidate_not_objective_feasible"
            )
        if incumbent.objective_feasible is not True:
            return _comparison_incomparable(
                "incumbent_not_objective_feasible"
            )

        ordered: list[tuple[str, int | float | None, int | float | None]] = [
            (
                "latency_cycles_max",
                candidate.latency_cycles_max,
                incumbent.latency_cycles_max,
            )
        ]
        if (
            candidate.initiation_interval_max is not None
            and incumbent.initiation_interval_max is not None
        ):
            ordered.append(
                (
                    "initiation_interval_max",
                    candidate.initiation_interval_max,
                    incumbent.initiation_interval_max,
                )
            )
        if (
            candidate.max_resource_utilization_ratio is not None
            and incumbent.max_resource_utilization_ratio is not None
        ):
            ordered.append(
                (
                    "max_resource_utilization_ratio",
                    candidate.max_resource_utilization_ratio,
                    incumbent.max_resource_utilization_ratio,
                )
            )
        if (
            candidate.achieved_clock_period_ns is not None
            and incumbent.achieved_clock_period_ns is not None
        ):
            ordered.append(
                (
                    "achieved_clock_period_ns",
                    candidate.achieved_clock_period_ns,
                    incumbent.achieved_clock_period_ns,
                )
            )
        ordered.append(("candidate_sequence", candidate_seq, incumbent_seq))

        for metric, candidate_value, incumbent_value in ordered:
            assert candidate_value is not None and incumbent_value is not None
            if candidate_value < incumbent_value:
                return PpaComparison(
                    decision=PpaComparisonDecision.BETTER,
                    better=True,
                    reason=f"lower_{metric}",
                    decisive_metric=metric,
                    candidate_value=candidate_value,
                    incumbent_value=incumbent_value,
                )
            if candidate_value > incumbent_value:
                return PpaComparison(
                    decision=PpaComparisonDecision.NOT_BETTER,
                    better=False,
                    reason=f"higher_{metric}",
                    decisive_metric=metric,
                    candidate_value=candidate_value,
                    incumbent_value=incumbent_value,
                )

        return PpaComparison(
            decision=PpaComparisonDecision.NOT_BETTER,
            better=False,
            reason="deterministic_tie_keeps_incumbent",
            decisive_metric="candidate_sequence",
            candidate_value=candidate_seq,
            incumbent_value=incumbent_seq,
        )


def _comparison_incomparable(reason: str) -> PpaComparison:
    return PpaComparison(
        decision=PpaComparisonDecision.INCOMPARABLE,
        better=None,
        reason=reason,
        decisive_metric=None,
        candidate_value=None,
        incumbent_value=None,
    )


def _xml_leaf_values(root: ET.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for element in root.iter():
        tag = _local_tag(element.tag)
        text = (element.text or "").strip()
        if text:
            result.setdefault(tag, []).append(text)
    return result


def _xml_resource_group(root: ET.Element, group_name: str) -> dict[str, int | None]:
    group: ET.Element | None = None
    for element in root.iter():
        if _local_tag(element.tag) == group_name:
            group = element
            break
    values = {name: None for name in _RESOURCE_NAMES}
    if group is None:
        return values
    leaves = _xml_leaf_values(group)
    for name, tags in _RESOURCE_XML_TAGS.items():
        values[name] = _first_int(leaves, tags)
    return values


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_int(
    values: Mapping[str, list[str]],
    names: tuple[str, ...],
) -> int | None:
    for name in names:
        for raw in values.get(name, ()):
            parsed = _parse_int(raw)
            if parsed is not None:
                return parsed
    return None


def _first_float(
    values: Mapping[str, list[str]],
    names: tuple[str, ...],
) -> float | None:
    for name in names:
        for raw in values.get(name, ()):
            parsed = _parse_float(raw)
            if parsed is not None:
                return parsed
    return None


def _parse_int(value: str) -> int | None:
    cleaned = value.strip().replace(",", "")
    if cleaned in {"", "-", "N/A", "NA", "?"}:
        return None
    try:
        parsed = int(float(cleaned))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_float(value: str) -> float | None:
    cleaned = value.strip().replace(",", "")
    if cleaned in {"", "-", "N/A", "NA", "?"}:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed if isfinite(parsed) and parsed > 0 else None


def _regex_int(text: str, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            parsed = _parse_int(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _regex_float(text: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            parsed = _parse_float(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _max_resource_ratio(
    used: PpaResourceUsage,
    available: PpaResourceUsage,
) -> float | None:
    ratios: list[float] = []
    for name in _RESOURCE_NAMES:
        used_value = getattr(used, name)
        available_value = getattr(available, name)
        if used_value is None or available_value is None or available_value <= 0:
            continue
        ratios.append(used_value / available_value)
    return max(ratios) if ratios else None


def _normalize_resource_limits(
    value: Mapping[str, int | None] | Any | None,
) -> dict[str, int | None]:
    if value is None:
        return {field: None for field in _RESOURCE_LIMIT_FIELDS.values()}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("resource_limits must be a mapping, typed limits, or null")
    allowed = set(_RESOURCE_LIMIT_FIELDS.values())
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            "Unknown resource limit fields: " + ", ".join(sorted(unknown))
        )
    normalized: dict[str, int | None] = {}
    for field in allowed:
        raw = value.get(field)
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int)):
            raise TypeError(f"{field} must be an integer or null")
        if raw is not None and raw < 0:
            raise ValueError(f"{field} must be non-negative")
        normalized[field] = raw
    return normalized


def _objective_feasibility(
    used: PpaResourceUsage,
    limits: Mapping[str, int | None],
) -> tuple[bool | None, tuple[str, ...], tuple[str, ...]]:
    violations: list[str] = []
    unknown: list[str] = []
    for resource, limit_field in _RESOURCE_LIMIT_FIELDS.items():
        limit = limits.get(limit_field)
        if limit is None:
            continue
        actual = getattr(used, resource)
        if actual is None:
            unknown.append(resource)
            continue
        if actual > limit:
            violations.append(
                f"{resource}_used_{actual}_exceeds_limit_{limit}"
            )
    if violations:
        return False, tuple(violations), tuple(
            f"resource_usage_missing:{name}" for name in unknown
        )
    if unknown:
        return None, (), tuple(
            f"resource_usage_missing:{name}" for name in unknown
        )
    return True, (), ()


def _safe_report_matches(root: Path, name: str) -> tuple[Path, ...]:
    matches: list[Path] = []
    for path in root.rglob(name):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        matches.append(resolved)
    return tuple(sorted(matches))


def _read_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PpaParseError("PPA report must be a regular file")
    size = path.stat().st_size
    if size <= 0:
        raise PpaParseError("PPA report is empty")
    if size > _MAX_REPORT_BYTES:
        raise PpaParseError("PPA report exceeds safety size limit")
    return path.read_bytes()


def _real_directory(value: str | os.PathLike[str], name: str) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be a non-empty path")
    path = Path(raw).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise FileNotFoundError(f"{name} is not a real directory: {path}")
    return path.resolve()


def _strict_payload(
    payload: Mapping[str, Any],
    allowed: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{name} payload must be a mapping")
    unknown = set(payload) - allowed
    missing = allowed - set(payload)
    if unknown or missing:
        raise ValueError(
            f"{name} fields mismatch: unknown={sorted(unknown)} "
            f"missing={sorted(missing)}"
        )
    if payload.get("schema_version") != PPA_SCHEMA_VERSION:
        raise ValueError(f"unsupported {name} schema_version")
    return json.loads(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
    )


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _required_id(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", cleaned):
        raise ValueError(f"{name} contains unsafe characters")
    return cleaned


def _relative_artifact_path(value: Any, name: str) -> str:
    cleaned = _required_text(value, name).replace("\\", "/")
    path = Path(cleaned)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be a contained relative path")
    return path.as_posix()


def _sha256(value: Any, name: str) -> str:
    cleaned = _required_text(value, name).lower()
    if _SHA256_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return cleaned


def _enum(value: Any, enum_type: type[Enum], name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"unsupported {name}: {value!r}") from exc


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _optional_nonnegative_int(value: Any, name: str) -> int | None:
    return None if value is None else _nonnegative_int(value, name)


def _optional_positive_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number or null")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _optional_nonnegative_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number or null")
    result = float(value)
    if not isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _text_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(_required_text(item, name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} values must be unique")
    return result
