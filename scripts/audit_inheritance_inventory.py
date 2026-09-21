#!/usr/bin/env python3
"""Independently audit the inheritance-first stage-I manifest and frozen pilot."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
ALLOWED_STATUSES = {"sample", "support", "generated", "duplicate", "blocked"}
TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|'
    r'(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|'
    r'<<=|>>=|->\*|::|\.\*|\+\+|--|==|!=|<=|>=|&&|\|\||<<|>>|'
    r'\+=|-=|\*=|/=|%=|&=|\|=|\^=|->|##|[{}()\[\];,?:~!%^&*+=|<>./-]'
)
IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
NUMBER_RE = re.compile(r"^(?:0[xX][0-9A-Fa-f]+|\d)")
KEYWORDS = frozenset(
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch char "
    "class compl concept const consteval constexpr constinit const_cast continue "
    "co_await co_return co_yield decltype default delete do double dynamic_cast else "
    "enum explicit export extern false float for friend goto if inline int long mutable "
    "namespace new noexcept not not_eq nullptr operator or or_eq private protected public "
    "register reinterpret_cast requires return short signed sizeof static static_assert "
    "static_cast struct switch template this thread_local throw true try typedef typeid "
    "typename union unsigned using virtual void volatile wchar_t while xor xor_eq".split()
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def repository_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def strip_comments(text: str) -> str:
    output = []
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char in {'"', "'"}:
                state, quote = "string", char
                output.append(char)
            elif char == "/" and nxt == "/":
                state = "line"
                output.append(" ")
                index += 1
            elif char == "/" and nxt == "*":
                state = "block"
                output.append(" ")
                index += 1
            else:
                output.append(char)
        elif state == "string":
            output.append(char)
            if char == "\\" and nxt:
                output.append(nxt)
                index += 1
            elif char == quote:
                state = "code"
        elif state == "line" and char == "\n":
            output.append(char)
            state = "code"
        elif state == "block" and char == "*" and nxt == "/":
            output.append(" ")
            state = "code"
            index += 1
        index += 1
    return "".join(output)


def structural_tokens(tokens: list[str]) -> list[str]:
    result = []
    for token in tokens:
        if token in KEYWORDS:
            result.append(token)
        elif IDENT_RE.fullmatch(token):
            result.append("ID")
        elif NUMBER_RE.match(token):
            result.append("NUMBER")
        elif token.startswith('"'):
            result.append("STRING")
        elif token.startswith("'"):
            result.append("CHAR")
        else:
            result.append(token)
    return result


def audit(root: Path, manifest_path: Path, cohort_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    warnings: list[str] = []
    unsigned = {key: value for key, value in manifest.items() if key != "inventory_sha256"}
    if manifest.get("inventory_sha256") != sha_value(unsigned):
        failures.append("inventory_sha256_mismatch")
    actual_files = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "src").rglob("*")
        if path.is_file() and path.suffix.lower() in CODE_SUFFIXES
    )
    records = manifest.get("files", [])
    recorded_paths = [record.get("path") for record in records]
    if recorded_paths != actual_files:
        failures.append("src_code_file_accounting_mismatch")
    if len(recorded_paths) != len(set(recorded_paths)):
        failures.append("duplicate_manifest_path")
    by_path = {record["path"]: record for record in records}
    d0: dict[str, list[str]] = defaultdict(list)
    d1: dict[str, list[str]] = defaultdict(list)
    shingles: dict[str, set[str]] = {}
    for record in records:
        path = root / record["path"]
        if record.get("status") not in ALLOWED_STATUSES:
            failures.append(f"invalid_status:{record.get('path')}")
        data = repository_bytes(path)
        source_hash = hashlib.sha256(data).hexdigest()
        tokens = TOKEN_RE.findall(strip_comments(data.decode("utf-8", errors="replace")))
        token_hash = sha_value(tokens)
        if source_hash != record.get("source_sha256"):
            failures.append(f"source_hash_mismatch:{record['path']}")
        if token_hash != record.get("normalized_token_sha256"):
            failures.append(f"token_hash_mismatch:{record['path']}")
        d0[source_hash].append(record["path"])
        d1[token_hash].append(record["path"])
        normalized = structural_tokens(tokens)
        shingles[record["path"]] = {
            "\x1f".join(normalized[index:index + 5])
            for index in range(max(0, len(normalized) - 4))
        }
        if record.get("inheritance_testable") != (record.get("status") == "sample"):
            failures.append(f"testable_status_mismatch:{record['path']}")
        if record.get("inheritance_testable") and not record.get("top"):
            failures.append(f"testable_without_top:{record['path']}")
        if record.get("inheritance_testable") and not record.get("dependencies", {}).get("resolvable"):
            failures.append(f"testable_with_unresolved_dependency:{record['path']}")
        reasons = set(record.get("role_reasons", []))
        if "missing_testbench" in reasons or "missing_vitis_tcl" in reasons:
            failures.append(f"missing_harness_auto_excluded:{record['path']}")
        if record.get("strict_efficacy_eligible"):
            failures.append(f"unfrozen_strict_case:{record['path']}")
        provenance = record.get("provenance", {})
        if not provenance.get("license") or not provenance.get("license_status"):
            failures.append(f"missing_provenance:{record['path']}")
        if any(ref.get("level") == "D2" for ref in record.get("duplicate_refs", [])) and record.get("status") == "duplicate":
            if not any(ref.get("level") in {"D0", "D1"} for ref in record.get("duplicate_refs", [])):
                failures.append(f"d2_only_auto_excluded:{record['path']}")
    for level, groups in (("D0", d0), ("D1", d1)):
        for members in groups.values():
            if len(members) < 2:
                continue
            for member in members:
                refs = {(item.get("path"), item.get("level")) for item in by_path[member].get("duplicate_refs", [])}
                if any((other, level) not in refs for other in members if other != member):
                    failures.append(f"unrecorded_{level.lower()}_relation:{member}")
    samples = [record for record in records if record.get("status") == "sample"]
    expected_d2: set[tuple[str, str]] = set()
    threshold = float(manifest.get("dedup_policy", {}).get("d2_threshold", 0.92))
    for index, left in enumerate(samples):
        for right in samples[index + 1:]:
            left_set = shingles[left["path"]]
            right_set = shingles[right["path"]]
            union = left_set | right_set
            score = len(left_set & right_set) / len(union) if union else 1.0
            if score >= threshold:
                expected_d2.add(tuple(sorted((left["path"], right["path"]))))
    actual_d2: set[tuple[str, str]] = set()
    for record in records:
        for ref in record.get("duplicate_refs", []):
            if ref.get("level") == "D2":
                actual_d2.add(tuple(sorted((record["path"], ref["path"]))))
    if expected_d2 != actual_d2:
        failures.append("d2_relation_mismatch")
    expected_families: dict[str, set[str]] = defaultdict(set)
    for record in samples:
        expected_families[record["algorithm_family"]].add(record["path"])
    for record in records:
        expected = sorted(expected_families.get(record["algorithm_family"], set()) - {record["path"]})
        if record.get("d3_family_members") != expected:
            failures.append(f"d3_family_mismatch:{record['path']}")
    directory_paths = {record["path"] for record in manifest.get("directories", [])}
    expected_directories = {str(Path(path).parent).replace("\\", "/") for path in actual_files}
    if directory_paths != expected_directories:
        failures.append("directory_accounting_mismatch")
    if manifest.get("summary", {}).get("code_file_count") != len(actual_files):
        failures.append("summary_file_count_mismatch")
    if manifest.get("summary", {}).get("registered_source_missing"):
        warnings.append("legacy_registry_contains_missing_source")

    cohort_unsigned = {key: value for key, value in cohort.items() if key != "cohort_sha256"}
    if cohort.get("cohort_sha256") != sha_value(cohort_unsigned):
        failures.append("cohort_sha256_mismatch")
    cohort_cases = cohort.get("cases", [])
    if not cohort_cases:
        failures.append("empty_heterorefactor_cohort")
    for case in cohort_cases:
        record = by_path.get(case.get("source_path"))
        if not record:
            failures.append(f"cohort_source_missing:{case.get('source_path')}")
            continue
        if record.get("source_family") != "heterorefactor" or not record.get("inheritance_testable"):
            failures.append(f"invalid_cohort_member:{record['path']}")
        if case.get("source_sha256") != record.get("source_sha256") or case.get("top") != record.get("top"):
            failures.append(f"cohort_identity_mismatch:{record['path']}")
    if cohort.get("execution_authorized"):
        failures.append("stage1_cohort_must_not_authorize_execution")

    result = {
        "schema_version": 1,
        "audit_id": "v2.3-inheritance-first-stage1-audit-v1",
        "status": "passed" if not failures else "failed",
        "checks": {
            "all_src_code_files_accounted": "src_code_file_accounting_mismatch" not in failures,
            "all_directories_accounted": "directory_accounting_mismatch" not in failures,
            "source_and_token_hashes_verified": not any("hash_mismatch" in item for item in failures),
            "missing_testbench_or_tcl_never_auto_excludes": not any("missing_harness" in item for item in failures),
            "d0_d1_relations_complete": not any(item.startswith("unrecorded_") for item in failures),
            "d2_never_auto_excludes": not any(item.startswith("d2_only_") for item in failures),
            "d2_relations_independently_recomputed": "d2_relation_mismatch" not in failures,
            "d3_families_independently_recomputed": not any(item.startswith("d3_family_") for item in failures),
            "provenance_fields_present": not any(item.startswith("missing_provenance:") for item in failures),
            "strict_cases_require_future_freeze": not any(item.startswith("unfrozen_strict_") for item in failures),
            "heterorefactor_cohort_identity_frozen": not any(item.startswith(("cohort_", "invalid_cohort")) for item in failures),
        },
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "next_step": "owner_review_then_stage2" if not failures else "fix_stage1_inventory",
    }
    result["audit_sha256"] = sha_value(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=ROOT / "configs" / "inheritance" / "inheritance_manifest_v1.json")
    parser.add_argument("--cohort", type=Path, default=ROOT / "configs" / "inheritance" / "heterorefactor_cohort_v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "roadmap" / "V2_3_INHERITANCE_STAGE1_AUDIT.json")
    args = parser.parse_args()
    result = audit(args.repo.resolve(), args.manifest.resolve(), args.cohort.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"INHERITANCE_STAGE1_AUDIT={result['status']}")
    print(f"FAILURES={len(result['failures'])}")
    print(f"WARNINGS={len(result['warnings'])}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
