"""Identity-bound structured case evidence for CSIM testbenches.

The protocol is deliberately separate from diagnostic text. A testbench opts
into it by calling the generated C++ helpers once per logical case and once
when all cases are complete. The evaluator accepts counts only after checking
identity, uniqueness, completion, and arithmetic consistency.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from flow.tools.typed_testbench_outcome import make_typed_outcome_identity

SCHEMA_VERSION = 1
EVIDENCE_FILENAME = "agrefactor_case_evidence.jsonl"


def make_identity(*, execution_id: str, phase: str, suite_id: str,
                  candidate_code: str, testbench_code: str) -> dict[str, str]:
    """Reuse the existing typed-outcome identity contract."""

    return make_typed_outcome_identity(
        execution_id=execution_id,
        phase=phase,
        suite_id=suite_id,
        candidate_code=candidate_code,
        testbench_code=testbench_code,
    )


def build_header(*, identity: Mapping[str, str], evidence_path: str = EVIDENCE_FILENAME) -> str:
    "Return a C++11 helper header for opt-in testbench instrumentation."

    required = (
        "execution_id", "phase", "suite_id_sha256", "candidate_sha256",
        "testbench_sha256",
    )
    if set(identity) != set(required):
        raise ValueError("unexpected case evidence identity fields")
    values: dict[str, str] = {}
    for key in required:
        value = identity[key]
        if not isinstance(value, str) or any(ord(ch) < 0x20 or ch in ('"', "\\") for ch in value):
            raise ValueError(f"unsafe identity field: {key}")
        values[key] = value
    if (not isinstance(evidence_path, str) or not evidence_path
            or "__EVIDENCE_PATH__" in evidence_path
            or any(ord(ch) < 0x20 or ch in ('"', "\\") for ch in evidence_path)):
        raise ValueError("unsafe case evidence path")
    template = r'''#ifndef AGREFACTOR_CASE_EVIDENCE_HPP
#define AGREFACTOR_CASE_EVIDENCE_HPP
#include <fstream>
#include <string>
namespace agrefactor_case_evidence {
namespace detail {
inline std::string json_escape(const char *value) {
    std::string out;
    if (value == nullptr) return out;
    const char *hex = "0123456789abcdef";
    for (const char *p = value; *p; ++p) {
        const unsigned char c = static_cast<unsigned char>(*p);
        if (c == '\\' || c == '"') { out.push_back('\\'); out.push_back(static_cast<char>(c)); }
        else if (c == '\b') out += "\\b";
        else if (c == '\f') out += "\\f";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else if (c < 0x20) {
            out += "\\u00";
            out.push_back(hex[(c >> 4) & 0xf]);
            out.push_back(hex[c & 0xf]);
        } else out.push_back(static_cast<char>(c));
    }
    return out;
}
inline bool &write_failed() { static bool value = false; return value; }
inline bool append(const std::string &line) {
    std::ofstream stream("__EVIDENCE_PATH__", std::ios::out | std::ios::app);
    if (!stream.is_open()) { write_failed() = true; return false; }
    stream << line << "\n";
    if (!stream.good()) { write_failed() = true; return false; }
    return true;
}
inline int &evaluated() { static int value = 0; return value; }
inline int &passed() { static int value = 0; return value; }
inline int &failed() { static int value = 0; return value; }
inline bool &completed() { static bool value = false; return value; }
}  // namespace detail
inline bool case_result(const char *case_id, bool passed) {
    if (detail::completed() || detail::write_failed()
            || case_id == nullptr || *case_id == '\0') return false;
    const std::string id = detail::json_escape(case_id);
    const bool result = passed;
    const std::string line =
        "{\"schema_version\":__SCHEMA__,\"event\":\"case\","
        "\"execution_id\":\"__EXECUTION_ID__\","
        "\"phase\":\"__PHASE__\","
        "\"suite_id_sha256\":\"__SUITE_HASH__\","
        "\"candidate_sha256\":\"__CANDIDATE_HASH__\","
        "\"testbench_sha256\":\"__TESTBENCH_HASH__\","
        "\"case_id\":\"" + id + "\","
        "\"status\":\"" + (result ? "passed" : "failed") + "\"}";
    if (!detail::append(line)) return false;
    ++detail::evaluated();
    if (result) ++detail::passed(); else ++detail::failed();
    return true;
}
inline bool complete() {
    if (detail::completed() || detail::write_failed()) return false;
    detail::completed() = true;
    const std::string line =
        "{\"schema_version\":__SCHEMA__,\"event\":\"complete\","
        "\"execution_id\":\"__EXECUTION_ID__\","
        "\"phase\":\"__PHASE__\","
        "\"suite_id_sha256\":\"__SUITE_HASH__\","
        "\"candidate_sha256\":\"__CANDIDATE_HASH__\","
        "\"testbench_sha256\":\"__TESTBENCH_HASH__\","
        "\"evaluated_cases\":" + std::to_string(detail::evaluated()) + ","
        "\"passed_cases\":" + std::to_string(detail::passed()) + ","
        "\"failed_cases\":" + std::to_string(detail::failed()) + "}";
    return detail::append(line);
}
}  // namespace agrefactor_case_evidence
#endif
'''
    replacements = {'__EVIDENCE_PATH__': evidence_path, '__SCHEMA__': str(SCHEMA_VERSION), '__EXECUTION_ID__': values['execution_id'], '__PHASE__': values['phase'], '__SUITE_HASH__': values['suite_id_sha256'], '__CANDIDATE_HASH__': values['candidate_sha256'], '__TESTBENCH_HASH__': values['testbench_sha256']}
    for marker, value in replacements.items(): template = template.replace(marker, value)
    return template


def read_case_counts(path: Path, *, expected_identity: Mapping[str, str],
                     declared_case_count: int | None = None) -> dict[str, Any] | None:
    """Read and verify a completed case sidecar; return normalized counts."""
    required_identity = {
        "execution_id", "phase", "suite_id_sha256",
        "candidate_sha256", "testbench_sha256",
    }
    if not isinstance(expected_identity, Mapping) or set(expected_identity) != required_identity:
        return None
    if any(not isinstance(value, str) or not value for value in expected_identity.values()):
        return None
    if declared_case_count is not None and (
        isinstance(declared_case_count, bool)
        or not isinstance(declared_case_count, int)
        or declared_case_count < 0
    ):
        return None
    if path.is_symlink() or not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines:
        return None
    cases: list[dict[str, Any]] = []
    completion: dict[str, Any] | None = None
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            return None
        if (not isinstance(value, Mapping)
                or type(value.get("schema_version")) is not int
                or value.get("schema_version") != SCHEMA_VERSION):
            return None
        for key, expected in expected_identity.items():
            if value.get(key) != expected:
                return None
        event = value.get("event")
        if completion is not None:
            return None
        if event == "case":
            if set(value) != {"schema_version", "event", *required_identity, "case_id", "status"}:
                return None
            case_id = value.get("case_id")
            status = value.get("status")
            if (
                not isinstance(case_id, str) or not case_id
                or any(ord(ch) < 0x20 for ch in case_id)
                or not isinstance(status, str)
                or status not in {"passed", "failed"}
            ):
                return None
            if any(item["case_id"] == case_id for item in cases):
                return None
            cases.append({"case_id": case_id, "status": status})
        elif event == "complete":
            expected_fields = {
                "schema_version", "event", *required_identity,
                "evaluated_cases", "passed_cases", "failed_cases",
            }
            if set(value) != expected_fields:
                return None
            counts = [value.get(field) for field in (
                "evaluated_cases", "passed_cases", "failed_cases"
            )]
            if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts):
                return None
            completion = dict(value)
        else:
            return None
    if completion is None:
        return None
    evaluated = len(cases)
    passed = sum(item["status"] == "passed" for item in cases)
    failed = evaluated - passed
    if (
        completion["evaluated_cases"] != evaluated
        or completion["passed_cases"] != passed
        or completion["failed_cases"] != failed
    ):
        return None
    if declared_case_count is not None and evaluated != declared_case_count:
        return None
    if declared_case_count is None and evaluated == 0:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "complete": True,
        "identity_verified": True,
        "evaluated_cases": evaluated,
        "passed_cases": passed,
        "failed_cases": failed,
        "case_ids": [item["case_id"] for item in cases],
    }
