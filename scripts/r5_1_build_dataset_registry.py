#!/usr/bin/env python3
"""Build the deterministic R5.1 internal-case registry without real calls."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INFO = ROOT / "src" / "info.json"
DEFAULT_POLICY = ROOT / "configs" / "r5_1" / "deduplication_policy.json"
DEFAULT_OUTPUT = ROOT / "configs" / "r5_1" / "dataset_registry.json"
IMPORT_COMMIT = "e3cb81b4ed53eec60fbf2cf5092267bda3882e82"

_TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r"[A-Za-z_]\w*|(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|"
    r"<<=|>>=|->\*|::|\.\*|\+\+|--|==|!=|<=|>=|&&|\|\||<<|>>|"
    r"\+=|-=|\*=|/=|%=|&=|\|=|\^=|->|##|[{}()\[\];,?:~!%^&*+=|<>./-]"
)
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
_NUMBER_RE = re.compile(r"^(?:0[xX][0-9A-Fa-f]+|\d)")
_KEYWORDS = frozenset(
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch char "
    "class compl concept const consteval constexpr constinit const_cast continue "
    "co_await co_return co_yield decltype default delete do double dynamic_cast else "
    "enum explicit export extern false float for friend goto if inline int long mutable "
    "namespace new noexcept not not_eq nullptr operator or or_eq private protected public "
    "register reinterpret_cast requires return short signed sizeof static static_assert "
    "static_cast struct switch template this thread_local throw true try typedef typeid "
    "typename union unsigned using virtual void volatile wchar_t while xor xor_eq".split()
)
_CONTROL = frozenset("if for while switch case catch return break continue goto throw".split())


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def repository_bytes(path: Path) -> bytes:
    """Return checkout-independent repository text bytes.

    Git may materialize text files as CRLF on Windows. R5.1 identities use the
    LF form stored by the authoritative Linux checkout; no whitespace, token,
    or semantic normalization occurs at D0.
    """

    return path.read_bytes().replace(b"\r\n", b"\n")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _strip_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char in {'"', "'"}:
                quote = char
                state = "string"
                output.append(char)
            elif char == "/" and nxt == "/":
                state = "line_comment"
                output.append(" ")
                index += 1
            elif char == "/" and nxt == "*":
                state = "block_comment"
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
        elif state == "line_comment":
            if char == "\n":
                output.append("\n")
                state = "code"
        elif state == "block_comment" and char == "*" and nxt == "/":
            output.append(" ")
            state = "code"
            index += 1
        index += 1
    return "".join(output)


def lexical_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_strip_comments(text))


def structural_tokens(tokens: Iterable[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        if token in _KEYWORDS:
            result.append(token)
        elif _IDENT_RE.fullmatch(token):
            result.append("ID")
        elif _NUMBER_RE.match(token):
            result.append("NUMBER")
        elif token.startswith('"'):
            result.append("STRING")
        elif token.startswith("'"):
            result.append("CHAR")
        else:
            result.append(token)
    return result


def _top_signature(tokens: list[str], top: str) -> tuple[list[str], bool]:
    for index, token in enumerate(tokens[:-1]):
        if token != top or tokens[index + 1] != "(":
            continue
        depth = 0
        signature: list[str] = []
        for item in tokens[index + 1 :]:
            signature.append(item)
            if item == "(":
                depth += 1
            elif item == ")":
                depth -= 1
                if depth == 0:
                    return structural_tokens(signature), True
        break
    return [], False


def _structure(tokens: list[str], top: str) -> tuple[dict[str, Any], set[str]]:
    normalized = structural_tokens(tokens)
    signature, top_found = _top_signature(tokens, top)
    controls = Counter(token for token in tokens if token in _CONTROL)
    calls = sorted(
        {
            token
            for index, token in enumerate(tokens[:-1])
            if _IDENT_RE.fullmatch(token)
            and tokens[index + 1] == "("
            and token not in _CONTROL
        }
    )
    structure = {
        "normalized_tokens": normalized,
        "top_signature": signature,
        "top_found": top_found,
        "control_counts": dict(sorted(controls.items())),
        "call_name_count": len(calls),
        "brace_count": tokens.count("{"),
        "loop_count": sum(controls.get(item, 0) for item in ("for", "while")),
    }
    shingles = {
        "\x1f".join(normalized[index : index + 5])
        for index in range(max(0, len(normalized) - 4))
    }
    return structure, shingles


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _source_family(source_path: str) -> str:
    parts = Path(source_path).parts
    if len(parts) < 3 or parts[0] != "src":
        raise ValueError(f"unexpected source path: {source_path}")
    return parts[1]


def _partition(family: str, policy: Mapping[str, Any]) -> str:
    split = policy["history_future_split"]
    digest = hashlib.sha256(f"{split['salt']}:{family}".encode()).digest()
    return "history" if digest[0] < int(split["history_threshold_byte"]) else "future"


def _oracle_record(root: Path, alias: str, source_path: str) -> dict[str, Any]:
    adapter = root / "configs" / "r5" / "oracle_adapters" / alias
    public = adapter / "public.cpp"
    hidden = adapter / "hidden.cpp"
    source_dir = (root / source_path).parent
    legacy_tb = source_dir / "tb.cpp"
    tcl = source_dir / "vitis.tcl"

    def ref(path: Path) -> dict[str, Any]:
        return {
            "path": path.relative_to(root).as_posix() if path.exists() else None,
            "sha256": sha_bytes(repository_bytes(path)) if path.exists() else None,
            "present": path.is_file(),
        }

    public_ref = ref(public)
    hidden_ref = ref(hidden)
    return {
        "public": public_ref,
        "hidden": hidden_ref,
        "legacy_testbench": ref(legacy_tb),
        "vitis_tcl": ref(tcl),
        "independent_public_hidden_pair": bool(
            public_ref["present"]
            and hidden_ref["present"]
            and public_ref["sha256"] != hidden_ref["sha256"]
        ),
    }


def _execution_contract(root: Path, source_path: str, top: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    source = root / source_path
    tcl = source.parent / "vitis.tcl"
    program_files: dict[str, dict[str, Any]] = {}
    if source.is_file():
        data = repository_bytes(source)
        text = data.decode("utf-8", errors="replace")
        program_files[source_path] = {
            "source_sha256": sha_bytes(data),
            "normalized_token_sha256": sha_value(lexical_tokens(text)),
            "role": "registered_source",
        }
    if not tcl.is_file():
        return {
            "status": "absent",
            "tcl_path": None,
            "declared_top": None,
            "declared_top_matches_registry": None,
            "added_source_paths": [],
            "registered_source_declared": None,
            "declared_top_found_in_added_source": None,
        }, program_files

    text = tcl.read_text(encoding="utf-8", errors="replace")
    top_match = re.search(r"(?m)^\s*set_top\s+['\"]?([^'\"\s;]+)", text)
    declared_top = top_match.group(1) if top_match else None
    raw_paths = re.findall(r"(?m)^\s*add_files\s+(?:-[^\s]+\s+)*['\"]?([^'\"\s;]+)", text)
    added_paths: list[str] = []
    unresolved = False
    top_found = False
    for raw_path in raw_paths:
        if "$" in raw_path or "[" in raw_path:
            unresolved = True
            continue
        candidate = (source.parent / raw_path).resolve()
        try:
            relative = candidate.relative_to(root.resolve()).as_posix()
        except ValueError:
            unresolved = True
            continue
        added_paths.append(relative)
        if candidate.is_file() and candidate.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}:
            data = repository_bytes(candidate)
            candidate_text = data.decode("utf-8", errors="replace")
            candidate_tokens = lexical_tokens(candidate_text)
            _, found = _top_signature(candidate_tokens, declared_top or top)
            top_found = top_found or found
            program_files[relative] = {
                "source_sha256": sha_bytes(data),
                "normalized_token_sha256": sha_value(candidate_tokens),
                "role": "tcl_added_source",
            }
    registered_declared = source_path in added_paths if added_paths else False
    status = "unresolved" if unresolved and not added_paths else "consistent"
    if declared_top is not None and declared_top != top:
        status = "inconsistent"
    if added_paths and (not registered_declared or not top_found):
        status = "inconsistent"
    return {
        "status": status,
        "tcl_path": tcl.relative_to(root).as_posix(),
        "declared_top": declared_top,
        "declared_top_matches_registry": declared_top == top if declared_top else None,
        "added_source_paths": sorted(set(added_paths)),
        "registered_source_declared": registered_declared,
        "declared_top_found_in_added_source": top_found if added_paths else None,
    }, program_files


def _raw_cases(root: Path, info: Mapping[str, Any], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    semantic = policy.get("semantic_families", {})
    source_catalog = policy.get("source_catalog", {})
    claims = policy.get("legacy_claims", {})
    rows: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for label in ("useful", "not_useful"):
        group = info.get(label)
        if not isinstance(group, Mapping):
            raise ValueError(f"src/info.json.{label} must be an object")
        for alias, spec in group.items():
            if alias in aliases:
                raise ValueError(f"duplicate alias in src/info.json: {alias}")
            aliases.add(str(alias))
            if not isinstance(spec, list) or len(spec) != 2:
                raise ValueError(f"invalid case specification: {alias}")
            source_path, top = (str(spec[0]), str(spec[1]))
            path = root / source_path
            family = _source_family(source_path)
            source_present = path.is_file()
            source = repository_bytes(path) if source_present else b""
            text = source.decode("utf-8", errors="replace")
            tokens = lexical_tokens(text)
            structure, shingles = _structure(tokens, top)
            source_sha = sha_bytes(source) if source_present else None
            identity_value = source_sha if source_sha is not None else "MISSING"
            case_key = sha_bytes(f"{source_path}\0{identity_value}".encode())[:16]
            oracles = _oracle_record(root, str(alias), source_path)
            execution_contract, program_files = _execution_contract(
                root, source_path, top
            )
            catalog = dict(source_catalog.get(family, {}))
            reasons: list[str] = []
            if not source_present:
                eligibility = "reject"
                reasons.append("source_file_missing")
            elif not structure["top_found"]:
                eligibility = "quarantine"
                reasons.append("top_not_lexically_found_in_registered_source")
            elif execution_contract["status"] == "inconsistent":
                eligibility = "quarantine"
                reasons.append("vitis_tcl_execution_contract_inconsistent")
            elif catalog.get("license_status") != "verified_for_official_repository":
                eligibility = "quarantine"
                reasons.append("license_or_checked_in_provenance_unresolved")
            elif not oracles["independent_public_hidden_pair"]:
                eligibility = "adapter_required"
                reasons.append("independent_public_hidden_oracle_pair_missing")
            else:
                eligibility = "eligible_for_p3_smoke"
            rows.append(
                {
                    "case_id": f"internal-{family}-{case_key}",
                    "alias": str(alias),
                    "source_path": source_path,
                    "source_family": family,
                    "legacy_info_label": label,
                    "source_sha256": source_sha,
                    "normalized_token_sha256": sha_value(tokens) if source_present else None,
                    "ast_structural_fingerprint": sha_value(structure) if source_present else None,
                    "algorithm_family": str(semantic.get(alias, f"unclassified:{family}")),
                    "top": top,
                    "interface": {
                        "top_lexically_found": structure["top_found"],
                        "top_signature_sha256": sha_value(structure["top_signature"])
                        if structure["top_found"]
                        else None,
                        "call_name_count": structure["call_name_count"],
                    },
                    "execution_contract": execution_contract,
                    "oracles": oracles,
                    "raw_source_baseline": {"status": "not_run", "evidence_ref": None},
                    "eligibility": eligibility,
                    "exclusion_reasons": reasons,
                    "history_future_partition": _partition(
                        str(semantic.get(alias, f"unclassified:{family}")), policy
                    ),
                    "nearest_duplicate_refs": [],
                    "upstream": {
                        **catalog,
                        "upstream_path": None,
                        "checked_in_import_commit": IMPORT_COMMIT,
                        "checked_in_copy_matches_upstream_commit": "unverified",
                    },
                    "legacy_claim_refs": sorted(str(item) for item in claims.get(alias, [])),
                    "_shingles": shingles,
                    "_program_files": program_files,
                }
            )
    return rows


def _apply_duplicates(rows: list[dict[str, Any]], threshold: float) -> None:
    d0: dict[str, list[dict[str, Any]]] = defaultdict(list)
    d1: dict[str, list[dict[str, Any]]] = defaultdict(list)
    d3: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["source_sha256"] is not None:
            d0[row["source_sha256"]].append(row)
        if row["normalized_token_sha256"] is not None:
            d1[row["normalized_token_sha256"]].append(row)
        d3[row["algorithm_family"]].append(row)

    program_d0: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    program_d1: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for row in rows:
        for path, identity in row.get("_program_files", {}).items():
            program_d0[identity["source_sha256"]].append((row, path))
            program_d1[identity["normalized_token_sha256"]].append((row, path))

    def add_ref(row: dict[str, Any], other: dict[str, Any], level: str, score: float, reason: str) -> None:
        ref = {
            "case_id": other["case_id"],
            "level": level,
            "score": round(score, 6),
            "reason": reason,
        }
        if ref not in row["nearest_duplicate_refs"]:
            row["nearest_duplicate_refs"].append(ref)

    for level, groups in (("D0", d0), ("D1", d1)):
        for members in groups.values():
            if len(members) < 2:
                continue
            canonical_row = min(members, key=lambda row: row["source_path"])
            for row in members:
                for other in members:
                    if row is not other:
                        add_ref(row, other, level, 1.0, f"{level.lower()}_exact_match")
                if row is not canonical_row:
                    row["eligibility"] = "duplicate_excluded"
                    reason = f"{level.lower()}_duplicate_of:{canonical_row['case_id']}"
                    if reason not in row["exclusion_reasons"]:
                        row["exclusion_reasons"].append(reason)

    for level, groups in (("D0", program_d0), ("D1", program_d1)):
        for members in groups.values():
            case_ids = {row["case_id"] for row, _ in members}
            if len(case_ids) < 2:
                continue
            for row, path in members:
                for other, other_path in members:
                    if row["case_id"] == other["case_id"]:
                        continue
                    add_ref(
                        row,
                        other,
                        level,
                        1.0,
                        f"case_file_{level.lower()}_match:{path}:{other_path}",
                    )
                if row["execution_contract"]["status"] == "inconsistent":
                    reason = f"{level.lower()}_duplicate_in_inconsistent_execution_contract"
                    if reason not in row["exclusion_reasons"]:
                        row["exclusion_reasons"].append(reason)

    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left["source_sha256"] is None or right["source_sha256"] is None:
                continue
            if left["source_sha256"] == right["source_sha256"]:
                continue
            score = _jaccard(left["_shingles"], right["_shingles"])
            if score >= threshold:
                add_ref(left, right, "D2", score, "structural_candidate_manual_review_required")
                add_ref(right, left, "D2", score, "structural_candidate_manual_review_required")

    for members in d3.values():
        if len(members) < 2:
            continue
        for row in members:
            for other in members:
                if row is not other:
                    add_ref(row, other, "D3", 1.0, "same_semantic_family_split_isolation")

    for row in rows:
        row["nearest_duplicate_refs"].sort(
            key=lambda item: (item["level"], item["case_id"])
        )
        row["exclusion_reasons"] = sorted(set(row["exclusion_reasons"]))
        row.pop("_shingles", None)
        row.pop("_program_files", None)


def build_registry(root: Path, info: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    rows = _raw_cases(root, info, policy)
    if len(rows) != 57:
        raise ValueError(f"expected 57 registered cases, found {len(rows)}")
    _apply_duplicates(rows, float(policy["d2_similarity_threshold"]))
    rows.sort(key=lambda row: row["case_id"])
    summary = {
        "case_count": len(rows),
        "legacy_useful_count": sum(row["legacy_info_label"] == "useful" for row in rows),
        "legacy_not_useful_count": sum(row["legacy_info_label"] == "not_useful" for row in rows),
        "eligibility_counts": dict(sorted(Counter(row["eligibility"] for row in rows).items())),
        "source_family_counts": dict(sorted(Counter(row["source_family"] for row in rows).items())),
        "partition_counts": dict(sorted(Counter(row["history_future_partition"] for row in rows).items())),
        "legacy_claim_case_count": sum(bool(row["legacy_claim_refs"]) for row in rows),
        "missing_source_count": sum(row["source_sha256"] is None for row in rows),
        "d0_case_count": sum(any(ref["level"] == "D0" for ref in row["nearest_duplicate_refs"]) for row in rows),
        "d1_case_count": sum(any(ref["level"] == "D1" for ref in row["nearest_duplicate_refs"]) for row in rows),
        "d2_candidate_case_count": sum(any(ref["level"] == "D2" for ref in row["nearest_duplicate_refs"]) for row in rows),
    }
    result = {
        "schema_version": 1,
        "registry_id": "v2.3-r5.1-internal-dataset-registry-v1",
        "frozen_at": policy["frozen_at"],
        "source_info_sha256": sha_value(info),
        "policy_sha256": sha_value(policy),
        "cases": rows,
        "summary": summary,
        "invariants": {
            "all_internal_cases_accounted_for": True,
            "d2_candidates_auto_excluded": False,
            "family_split_isolated": True,
            "future_outcomes_observed_before_split": False,
            "hidden_content_fingerprinted": False,
            "provider_calls": 0,
            "vitis_launches": 0,
            "git_history_mutations": 0,
            "source_hash_canonicalization": "crlf_to_lf_only",
        },
    }
    result["registry_sha256"] = sha_value(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--info", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repo.resolve()
    info_path = args.info or root / DEFAULT_INFO.relative_to(ROOT)
    policy_path = args.policy or root / DEFAULT_POLICY.relative_to(ROOT)
    output_path = args.output or root / DEFAULT_OUTPUT.relative_to(ROOT)
    registry = build_registry(root, load_object(info_path), load_object(policy_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("R5_1_DATASET_REGISTRY_STATUS=generated")
    print(f"R5_1_INTERNAL_CASES={registry['summary']['case_count']}")
    print(f"R5_1_REGISTRY_SHA256={registry['registry_sha256']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
