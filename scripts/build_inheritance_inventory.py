#!/usr/bin/env python3
"""Build the zero-call inheritance inventory for every C/C++ file under src/."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
ALLOWED_STATUSES = {"sample", "support", "generated", "duplicate", "blocked"}
PILOT_SOURCES = (
    "src/heterorefactor/ahocorasick/kernel.cpp",
    "src/heterorefactor/dfs/kernel.cpp",
    "src/heterorefactor/mergesort/kernel.cpp",
    "src/heterorefactor/strassen/kernel.cpp",
)
SUPPORT_NAMES = {
    "main.cpp", "tb.cpp", "csim.cpp", "des_test.c", "present_test.c",
    "qs_main.cpp", "qs_include.cpp",
}
INCLUDE_RE = re.compile(r"^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]", re.MULTILINE)
TOP_RE = re.compile(r"(?:^|\n)\s*set_top\s+([^\s#]+)")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def repository_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_legacy_helpers(root: Path):
    path = root / "scripts" / "r5_1_build_dataset_registry.py"
    spec = importlib.util.spec_from_file_location("r5_1_registry_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load token helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registered_cases(root: Path) -> dict[str, dict[str, str]]:
    info = load_object(root / "src" / "info.json")
    result: dict[str, dict[str, str]] = {}
    for legacy_label in ("useful", "not_useful"):
        for alias, value in info.get(legacy_label, {}).items():
            source_path, top = value
            result[source_path] = {
                "alias": alias,
                "top": top,
                "top_source": "src/info.json",
                "legacy_label": legacy_label,
            }
    return result


def function_definitions(tokens: list[str]) -> list[str]:
    definitions: set[str] = set()
    controls = {"if", "for", "while", "switch", "catch"}
    for index in range(1, len(tokens) - 3):
        name = tokens[index]
        if not re.fullmatch(r"[A-Za-z_]\w*", name) or tokens[index + 1] != "(":
            continue
        if name in controls or tokens[index - 1] in {".", "->"}:
            continue
        depth = 0
        close = None
        for cursor in range(index + 1, len(tokens)):
            if tokens[cursor] == "(":
                depth += 1
            elif tokens[cursor] == ")":
                depth -= 1
                if depth == 0:
                    close = cursor
                    break
        if close is None:
            continue
        cursor = close + 1
        while cursor < len(tokens) and tokens[cursor] in {
            "const", "noexcept", "override", "final", "->", "ID", "[", "]"
        }:
            cursor += 1
        if cursor < len(tokens) and tokens[cursor] == "{":
            definitions.add(name)
    return sorted(definitions)


def tcl_tops(directory: Path) -> tuple[list[str], list[str]]:
    tcl_paths: list[str] = []
    tops: set[str] = set()
    for path in sorted(directory.glob("*.tcl")):
        tcl_paths.append(path.name)
        text = path.read_text(encoding="utf-8", errors="replace")
        tops.update(TOP_RE.findall(text))
    return sorted(tops), tcl_paths


def infer_top(
    relative: str,
    definitions: list[str],
    registered: dict[str, dict[str, str]],
    declared_tops: list[str],
) -> tuple[str | None, str | None, list[str]]:
    if relative in registered:
        record = registered[relative]
        return record["top"], record["top_source"], definitions
    matching_tcl = [top for top in declared_tops if top in definitions]
    if len(matching_tcl) == 1:
        return matching_tcl[0], "vitis.tcl", definitions
    for preferred in ("top", "process_top"):
        if preferred in definitions:
            return preferred, "conventional_top_name", definitions
    stem = Path(relative).stem
    if stem in definitions:
        return stem, "source_filename_match", definitions
    public = [name for name in definitions if name != "main"]
    if len(public) == 1:
        return public[0], "single_function_definition", definitions
    return None, None, definitions


def dependency_record(root: Path, source: Path, text: str) -> dict[str, Any]:
    includes = []
    unresolved = []
    vitis_root = Path(os.environ.get("XILINX_HLS", "/data/Xilinx/Vitis_HLS/2023.2"))
    search_roots = [source.parent, root / "src", vitis_root / "include"]
    for delimiter, name in INCLUDE_RE.findall(text):
        resolution = "system"
        resolved_path = None
        if delimiter == '"':
            resolution = "unresolved"
            for base in search_roots:
                candidate = base / name
                if candidate.is_file():
                    resolution = "repository" if candidate.is_relative_to(root) else "toolchain"
                    resolved_path = (
                        candidate.relative_to(root).as_posix()
                        if candidate.is_relative_to(root)
                        else f"$XILINX_HLS/include/{name}"
                    )
                    break
            if resolution == "unresolved":
                unresolved.append(name)
        includes.append({"name": name, "kind": "quote" if delimiter == '"' else "system", "resolution": resolution, "path": resolved_path})
    return {
        "includes": includes,
        "unresolved_local_includes": sorted(set(unresolved)),
        "resolvable": not unresolved,
    }


def role_for(relative: str, registered: dict[str, dict[str, str]], top: str | None) -> tuple[str, list[str]]:
    path = Path(relative)
    name = path.name
    if relative in registered:
        return ("sample", []) if path.is_file() else ("blocked", ["registered_source_missing"])
    if name == "streaming_example.c":
        return "blocked", ["malformed_source_and_ambiguous_duplicate_top"]
    if "kernel copy" in name:
        return "duplicate", ["explicit_copy_filename"]
    if path.suffix in {".h", ".hpp"}:
        return "support", ["header_or_support_library"]
    if name in SUPPORT_NAMES or name.endswith("_test.c"):
        return "support", ["test_or_host_driver"]
    if relative.startswith("src/c2hlsc/") and name != "kernel.cpp":
        return "support", ["kernel_construction_source_or_test_support"]
    if top:
        return "sample", []
    return "blocked", ["top_not_deterministically_identifiable"]


def algorithm_family(relative: str, alias: str | None, policy: dict[str, Any]) -> str:
    families = policy.get("semantic_families", {})
    if alias and alias in families:
        return families[alias]
    path = Path(relative)
    if "strassen" in relative or path.stem in {"2mm_extra_large", "3mm_large"}:
        return "matrix_multiplication"
    if path.stem in {"mm_chain_dp_new", "minimap2"}:
        return "sequence_chaining"
    if path.stem == "optical_flow":
        return "optical_flow"
    return f"unclassified:{path.parent.as_posix()}"


def build_inventory(root: Path) -> dict[str, Any]:
    legacy = load_legacy_helpers(root)
    policy = load_object(root / "configs" / "r5_1" / "deduplication_policy.json")
    registered = registered_cases(root)
    files = sorted(
        path for path in (root / "src").rglob("*")
        if path.is_file() and path.suffix.lower() in CODE_SUFFIXES
    )
    records: list[dict[str, Any]] = []
    tokens_by_path: dict[str, list[str]] = {}
    shingles_by_path: dict[str, set[str]] = {}
    d0: dict[str, list[str]] = defaultdict(list)
    d1: dict[str, list[str]] = defaultdict(list)

    for source in files:
        relative = source.relative_to(root).as_posix()
        data = repository_bytes(source)
        text = data.decode("utf-8", errors="replace")
        tokens = legacy.lexical_tokens(text)
        normalized = legacy.structural_tokens(tokens)
        definitions = function_definitions(tokens)
        declared_tops, tcl_names = tcl_tops(source.parent)
        top, top_source, definitions = infer_top(relative, definitions, registered, declared_tops)
        status, reasons = role_for(relative, registered, top)
        dependencies = dependency_record(root, source, text)
        if status == "sample" and top not in definitions:
            status = "blocked"
            reasons.append("declared_top_definition_not_found")
        if status == "sample" and not dependencies["resolvable"]:
            status = "blocked"
            reasons.append("unresolved_local_dependency")
        alias = registered.get(relative, {}).get("alias")
        source_sha = hashlib.sha256(data).hexdigest()
        token_sha = sha_value(tokens)
        structural_shingles = {
            "\x1f".join(normalized[index:index + 5])
            for index in range(max(0, len(normalized) - 4))
        }
        d0[source_sha].append(relative)
        d1[token_sha].append(relative)
        tokens_by_path[relative] = tokens
        shingles_by_path[relative] = structural_shingles
        testbench_paths = sorted(
            candidate.relative_to(root).as_posix()
            for candidate in source.parent.iterdir()
            if candidate.is_file()
            and (
                candidate.name in SUPPORT_NAMES
                or "test" in candidate.stem.lower()
                or candidate.name.endswith("_test.txt")
            )
        )
        record = {
            "path": relative,
            "directory": source.parent.relative_to(root).as_posix(),
            "status": status,
            "role_reasons": sorted(set(reasons)),
            "source_sha256": source_sha,
            "normalized_token_sha256": token_sha,
            "top": top,
            "top_source": top_source,
            "function_definitions": definitions,
            "dependencies": dependencies,
            "existing_testbench_paths": testbench_paths,
            "existing_tcl_paths": [
                (source.parent / name).relative_to(root).as_posix() for name in tcl_names
            ],
            "testbench_plan": "existing_and_review_required" if testbench_paths else "product_generated_and_freeze_required",
            "source_family": source.relative_to(root / "src").parts[0],
            "algorithm_family": algorithm_family(relative, alias, policy),
            "provenance": dict(policy.get("source_catalog", {}).get(
                source.relative_to(root / "src").parts[0],
                {
                    "admission": "inventory_only",
                    "license": "NOASSERTION",
                    "license_status": "not_recorded_in_legacy_source_catalog",
                    "upstream_commit": None,
                    "upstream_repo": None,
                },
            )),
            "legacy_alias": alias,
            "legacy_label": registered.get(relative, {}).get("legacy_label"),
            "inheritance_testable": status == "sample",
            "strict_efficacy_eligible": False,
            "strict_efficacy_blockers": (
                ["not_an_independent_sample"] if status != "sample" else
                ["oracle_not_frozen", "target_profile_not_frozen", "formal_validation_not_run"]
            ),
            "duplicate_refs": [],
        }
        records.append(record)

    by_path = {record["path"]: record for record in records}
    for level, groups in (("D0", d0), ("D1", d1)):
        for members in groups.values():
            if len(members) < 2:
                continue
            for member in members:
                for other in members:
                    if other != member:
                        by_path[member]["duplicate_refs"].append({"path": other, "level": level, "score": 1.0})
    samples = [record for record in records if record["status"] == "sample"]
    for index, left in enumerate(samples):
        left_set = shingles_by_path[left["path"]]
        for right in samples[index + 1:]:
            right_set = shingles_by_path[right["path"]]
            union = left_set | right_set
            score = len(left_set & right_set) / len(union) if union else 1.0
            if score >= float(policy["d2_similarity_threshold"]):
                rounded = round(score, 6)
                left["duplicate_refs"].append({"path": right["path"], "level": "D2", "score": rounded})
                right["duplicate_refs"].append({"path": left["path"], "level": "D2", "score": rounded})
    families: dict[str, list[str]] = defaultdict(list)
    for record in samples:
        families[record["algorithm_family"]].append(record["path"])
    for record in records:
        record["d3_family_members"] = sorted(
            path for path in families.get(record["algorithm_family"], []) if path != record["path"]
        )
        record["duplicate_refs"] = sorted(
            { (item["path"], item["level"], item["score"]): item for item in record["duplicate_refs"] }.values(),
            key=lambda item: (item["level"], item["path"]),
        )

    directories = []
    for directory in sorted({record["directory"] for record in records}):
        members = [record for record in records if record["directory"] == directory]
        statuses = {record["status"] for record in members}
        directory_status = "sample" if "sample" in statuses else "blocked" if "blocked" in statuses else "support"
        directories.append({
            "path": directory,
            "status": directory_status,
            "file_count": len(members),
            "sample_paths": sorted(record["path"] for record in members if record["status"] == "sample"),
            "blocked_paths": sorted(record["path"] for record in members if record["status"] == "blocked"),
        })

    status_counts = defaultdict(int)
    for record in records:
        status_counts[record["status"]] += 1
    inventory = {
        "schema_version": 1,
        "inventory_id": "v2.3-inheritance-first-stage1-v1",
        "scope": "all_c_cpp_files_under_src",
        "environment_contract": {
            "bootstrap": "source /data/agrefactorpp_env.sh",
            "vitis_version": "2023.2",
            "provider_required": False,
        },
        "dedup_policy": {
            "path": "configs/r5_1/deduplication_policy.json",
            "sha256": hashlib.sha256(repository_bytes(root / "configs" / "r5_1" / "deduplication_policy.json")).hexdigest(),
            "d2_threshold": policy["d2_similarity_threshold"],
            "d2_auto_excludes": False,
            "d3_auto_excludes": False,
        },
        "files": records,
        "directories": directories,
        "summary": {
            "code_file_count": len(records),
            "directory_count": len(directories),
            "status_counts": dict(sorted(status_counts.items())),
            "inheritance_testable_count": sum(record["inheritance_testable"] for record in records),
            "strict_efficacy_eligible_count": sum(record["strict_efficacy_eligible"] for record in records),
            "registered_source_missing": sorted(set(registered) - set(by_path)),
        },
        "invariants": {
            "missing_testbench_never_auto_excludes": True,
            "missing_tcl_never_auto_excludes": True,
            "d2_never_auto_excludes": True,
            "d3_never_auto_excludes": True,
            "provider_calls": 0,
            "vitis_launches": 0,
            "git_history_mutations": 0,
        },
    }
    inventory["inventory_sha256"] = sha_value(inventory)
    return inventory


def build_heterorefactor_cohort(inventory: dict[str, Any]) -> dict[str, Any]:
    by_path = {record["path"]: record for record in inventory["files"]}
    rationale = {
        "src/heterorefactor/ahocorasick/kernel.cpp": "dynamic trie and state-machine traversal",
        "src/heterorefactor/dfs/kernel.cpp": "dynamic binary-tree construction and recursion",
        "src/heterorefactor/mergesort/kernel.cpp": "recursive linked-list sorting",
        "src/heterorefactor/strassen/kernel.cpp": "recursive matrix allocation and multiplication",
    }
    cases = []
    for source_path in PILOT_SOURCES:
        record = by_path[source_path]
        if not record["inheritance_testable"]:
            raise ValueError(f"pilot source is not inheritance-testable: {source_path}")
        cases.append({
            "case_id": f"heterorefactor-{Path(source_path).parent.name}",
            "source_path": source_path,
            "source_sha256": record["source_sha256"],
            "top": record["top"],
            "algorithm_family": record["algorithm_family"],
            "existing_testbench_paths": record["existing_testbench_paths"],
            "existing_tcl_paths": record["existing_tcl_paths"],
            "testbench_plan": record["testbench_plan"],
            "selection_rationale": rationale[source_path],
            "strict_efficacy_eligible": False,
        })
    cohort = {
        "schema_version": 1,
        "cohort_id": "v2.3-inheritance-first-heterorefactor-pilot-v1",
        "inventory_sha256": inventory["inventory_sha256"],
        "selection_policy": "four structurally distinct cases; defer adjacent linkedlist and strassen_break variants",
        "cases": cases,
        "execution_authorized": False,
        "execution_requires_owner_review": True,
        "r2_r4_enabled": False,
        "memory_enabled": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
    }
    cohort["cohort_sha256"] = sha_value(cohort)
    return cohort


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "configs" / "inheritance" / "inheritance_manifest_v1.json",
    )
    parser.add_argument(
        "--cohort-output", type=Path,
        default=ROOT / "configs" / "inheritance" / "heterorefactor_cohort_v1.json",
    )
    args = parser.parse_args()
    inventory = build_inventory(args.repo.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cohort = build_heterorefactor_cohort(inventory)
    args.cohort_output.parent.mkdir(parents=True, exist_ok=True)
    args.cohort_output.write_text(json.dumps(cohort, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("INHERITANCE_STAGE1_BUILD=complete")
    print(f"CODE_FILES={inventory['summary']['code_file_count']}")
    print(f"INHERITANCE_TESTABLE={inventory['summary']['inheritance_testable_count']}")
    print(f"STRICT_EFFICACY_ELIGIBLE={inventory['summary']['strict_efficacy_eligible_count']}")
    print(f"HETEROREFACTOR_PILOT_CASES={len(cohort['cases'])}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
