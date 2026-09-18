#!/usr/bin/env python3
"""Build a deduplicated, machine-readable R5 predecessor evidence inventory.

This tool is deliberately outside the product CLI.  It reads immutable evidence
roots, never calls a provider or Vitis, and counts physical usage from typed
artifacts only.  Nested summaries are never recursively summed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


CAP = 500
R5_AUTHORIZATION_COMMIT = "9d03b9e90710b1e6e8cd5f57d728e95ba1fc58a8"
R4_ROOTS = (
    "/data/package_d_v1_1_1_r4_evidence",
    "/data/package_d_v1_1_1_r4_evidence_deepseek_flash",
    "/data/package_d_v1_2_r4_evidence",
    "/data/package_d_v1_3_r4_evidence",
    "/data/package_d_v1_3_1_r4_evidence",
    "/data/package_d_v1_3_2_r4_evidence",
    "/data/package_d_v1_4_r4_evidence_run1",
    "/data/package_d_v1_5_r4_evidence_run1",
    "/data/package_d_v1_6_r4_evidence_run1",
)
SMOKE_FILES = (
    "/data/r2_prompt_contract_smoke/R2_REAL_SMOKE_RESULT.json",
    "/data/r2_input_evidence_smoke/R2_INPUT_EVIDENCE_SMOKE_RESULT.json",
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_json(value: Any) -> str:
    return _sha_bytes(_canonical(value).encode("utf-8"))


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _walk(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "repository_commit", "case_id", "source_hashes", "original_source_sha256",
        "initial_candidate_sha256", "prompt_contract_sha256", "prompt_template_sha256",
        "r2_prompt_contract", "r2_provider_identity", "model_provider_identity",
        "model_identity", "target_profile_identity", "parser_profile_identity",
        "toolchain_identity", "toolchain_profile_identity", "frozen_at",
        "calibration_evidence",
    )
    return {key: manifest[key] for key in keys if key in manifest}


def _context_and_source(result: Mapping[str, Any], manifest: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    contexts: set[str] = set()
    sources: set[str] = set()
    for item in _walk(result):
        for key in ("context_signature", "context_signature_sha256"):
            value = item.get(key)
            if isinstance(value, str) and len(value) == 64:
                contexts.add(value)
        for key in ("source_sha256", "candidate_source_sha256", "original_source_sha256", "initial_candidate_sha256"):
            value = item.get(key)
            if isinstance(value, str) and len(value) == 64:
                sources.add(value)
    for key in ("original_source_sha256", "initial_candidate_sha256"):
        value = manifest.get(key)
        if isinstance(value, str) and len(value) == 64:
            sources.add(value)
    hashes = manifest.get("source_hashes")
    if isinstance(hashes, Mapping):
        for value in hashes.values():
            if isinstance(value, str) and len(value) == 64:
                sources.add(value)
    return sorted(contexts), sorted(sources)


def _r4_record(root: Path) -> dict[str, Any]:
    result_path = root / "PACKAGE_D_RESULT.json"
    result = _load(result_path)
    manifest_path = root / "frozen_manifest.json"
    manifest = _load(manifest_path) if manifest_path.exists() else {}
    archive_sha = result.get("evidence_archive_sha256")
    key = f"r4:{archive_sha}" if isinstance(archive_sha, str) else f"r4:manifest:{_sha_file(manifest_path)}"
    provider_calls = 0
    vitis_launches = 0
    observations: list[dict[str, Any]] = []
    for run_dir in sorted((root / "runs").glob("*")) if (root / "runs").is_dir() else ():
        result_file = run_dir / "result.json"
        if not result_file.exists():
            continue
        run = _load(result_file)
        shadow = run.get("shadow_accounting")
        shadow_calls = _int(shadow.get("provider_calls")) if isinstance(shadow, Mapping) else 0
        metadata = run.get("orchestration_result")
        metadata = metadata.get("metadata", {}) if isinstance(metadata, Mapping) else {}
        r4 = metadata.get("r4_integration", {}) if isinstance(metadata, Mapping) else {}
        budget = r4.get("budget", {}) if isinstance(r4, Mapping) else {}
        actual = budget.get("actual", {}) if isinstance(budget, Mapping) else {}
        mutation_calls = _int(actual.get("provider_calls")) if isinstance(actual, Mapping) else 0
        provider_calls += shadow_calls + mutation_calls
        run_vitis = 0
        for invocation in run_dir.rglob("*_invocation.json"):
            name = invocation.name
            if name not in {"csim_invocation.json", "csynth_invocation.json", "cosim_invocation.json"}:
                continue
            try:
                value = _load(invocation)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if value.get("budget", {}).get("status") == "consumed" if isinstance(value.get("budget"), Mapping) else False:
                run_vitis += 1
        vitis_launches += run_vitis
        contexts, sources = _context_and_source(run, manifest)
        observations.append({
            "run_id": run.get("run_id", run_dir.name),
            "status": run.get("status"),
            "provider_calls": shadow_calls + mutation_calls,
            "vitis_launches": run_vitis,
            "context_signatures": contexts,
            "source_hashes": sources,
            "result_sha256": _sha_file(result_file),
        })
    return {
        "record_id": root.name,
        "artifact_kind": "r4_package_d_campaign",
        "logical_artifact_key": key,
        "root": str(root),
        "artifact_status": result.get("status"),
        "reason": result.get("reason"),
        "evidence_archive_sha256": archive_sha,
        "manifest_sha256": _sha_file(manifest_path) if manifest_path.exists() else None,
        "identity": _identity(manifest),
        "observations": observations,
        "provider_calls": provider_calls,
        "vitis_launches": vitis_launches,
        "physical_counting_rule": "one typed csim/csynth/cosim invocation with budget.status=consumed; one shadow or R4 actual provider call",
        "supersedable": True,
    }


def _calibration_record(root: Path) -> dict[str, Any] | None:
    path = root / "calibration" / "calibration_bundle.json"
    if not path.exists():
        return None
    bundle = _load(path)
    execution = bundle.get("execution", {})
    if not isinstance(execution, Mapping):
        execution = {}
    certificate = bundle.get("certificate", {})
    if not isinstance(certificate, Mapping):
        certificate = {}
    key = bundle.get("manifest_sha256") or _sha_file(path)
    contexts, sources = _context_and_source(bundle, {})
    records = bundle.get("records", [])
    if isinstance(records, list):
        for item in records:
            if isinstance(item, Mapping):
                c, s = _context_and_source(item, {})
                contexts.extend(c)
                sources.extend(s)
    return {
        "record_id": f"{root.name}:calibration",
        "artifact_kind": "r2_calibration_bundle",
        "logical_artifact_key": f"r2-calibration:{key}",
        "root": str(root),
        "artifact_status": "accepted" if certificate.get("accepted") is True else "historical",
        "certificate_id": certificate.get("certificate_id"),
        "bundle_manifest_sha256": bundle.get("manifest_sha256"),
        "bundle_file_sha256": _sha_file(path),
        "identity": {
            "certificate": certificate,
            "provider_identity": bundle.get("provider_identity"),
            "model_runtime": bundle.get("model_runtime"),
            "policy": bundle.get("policy"),
            "protocol": bundle.get("protocol"),
        },
        "source_hashes": sorted(set(sources)),
        "context_signatures": sorted(set(contexts)),
        "provider_calls": _int(execution.get("provider_calls")),
        "vitis_launches": _int(execution.get("vitis_launches")),
        "physical_counting_rule": "use the bundle execution counters once per unique bundle manifest; ignore frozen hard-budget reservations",
        "supersedable": True,
    }


def _smoke_record(path: Path) -> dict[str, Any]:
    value = _load(path)
    return {
        "record_id": path.parent.name,
        "artifact_kind": "r2_real_smoke",
        "logical_artifact_key": f"r2-smoke:{_sha_file(path)}",
        "root": str(path.parent),
        "artifact_status": value.get("status"),
        "reason": value.get("reason"),
        "identity": {key: value[key] for key in ("provider", "model", "family", "base_url", "contract_version") if key in value},
        "provider_calls": _int(value.get("provider_calls")),
        "vitis_launches": _int(value.get("vitis_launches")),
        "physical_counting_rule": "use explicit smoke result counter once per content hash",
        "supersedable": False,
    }


def _deduplicate(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unique: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    first_by_key: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["logical_artifact_key"]
        first = first_by_key.get(key)
        if first is None:
            first_by_key[key] = record
            unique.append(record)
            continue
        duplicates.append({
            "record_id": record["record_id"],
            "duplicate_of": first["record_id"],
            "logical_artifact_key": key,
            "root": record["root"],
            "usage_excluded_from_totals": True,
        })
    return unique, duplicates


def build_inventory(roots: Iterable[Path]) -> dict[str, Any]:
    raw: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        if (root / "PACKAGE_D_RESULT.json").exists():
            raw.append(_r4_record(root))
        calibration = _calibration_record(root)
        if calibration is not None:
            raw.append(calibration)
    for path_text in SMOKE_FILES:
        path = Path(path_text)
        if path.exists():
            raw.append(_smoke_record(path))
    unique, duplicates = _deduplicate(raw)
    historical_provider = sum(_int(item.get("provider_calls")) for item in unique)
    historical_vitis = sum(_int(item.get("vitis_launches")) for item in unique)
    accepted_r4 = [item for item in unique if item["artifact_kind"] == "r4_package_d_campaign" and item.get("artifact_status") == "ready_for_manual_checkpoint"]
    if accepted_r4:
        accepted_id = sorted(accepted_r4, key=lambda item: item["record_id"])[-1]["record_id"]
        for item in unique:
            if item["artifact_kind"] == "r4_package_d_campaign" and item["record_id"] != accepted_id:
                item["superseded_by"] = accepted_id
    accepted_cal = [item for item in unique if item["artifact_kind"] == "r2_calibration_bundle" and item.get("artifact_status") == "accepted"]
    if accepted_cal:
        accepted_id = sorted(accepted_cal, key=lambda item: item["record_id"])[-1]["record_id"]
        for item in unique:
            if item["artifact_kind"] == "r2_calibration_bundle" and item["record_id"] != accepted_id:
                item["superseded_by"] = accepted_id
    primary_r4 = [item for item in unique if item["artifact_kind"] == "r4_package_d_campaign" and item.get("record_id", "").endswith("v1_6_r4_evidence_run1")]
    primary_sources = set()
    for item in primary_r4:
        for obs in item.get("observations", []):
            primary_sources.update(obs.get("source_hashes", []))
    insufficiency = []
    if len(primary_sources) < 2:
        insufficiency.append("accepted R4 repair evidence has fewer than two independent source hashes")
    insufficiency.extend(["no frozen R5 history/future case partition is present", "no paired positive and inapplicable repair controls are present"])
    payload = {
        "schema_version": 1,
        "inventory_id": "v2.3-r5-predecessor-evidence-inventory-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "r5_authorization": {
            "commit": R5_AUTHORIZATION_COMMIT,
            "budget_scope": "calls in predecessor archives are evidence, not R5 consumption; calls made after this authorization point must be appended to the R5 ledger",
            "product_default_enabled": False,
        },
        "deduplication": {
            "unique_artifact_count": len(unique),
            "duplicate_artifact_count": len(duplicates),
            "duplicates": duplicates,
            "nested_json_summaries_not_recursively_summed": True,
        },
        "artifacts": unique,
        "historical_evidence_usage": {
            "provider_calls": historical_provider,
            "vitis_launches": historical_vitis,
            "frozen_reservations_excluded": True,
        },
        "r5_budget_ledger": {
            "provider_cap": CAP,
            "vitis_cap": CAP,
            "consumed_provider_calls": 0,
            "consumed_vitis_launches": 0,
            "remaining_provider_calls": CAP,
            "remaining_vitis_launches": CAP,
            "historical_pre_r5_provider_calls": historical_provider,
            "historical_pre_r5_vitis_launches": historical_vitis,
            "recovery_and_audit_reserve_fraction": 0.10,
        },
        "dataset_readiness": {
            "primary_r5_campaign_ready": not insufficiency,
            "independent_primary_source_hash_count": len(primary_sources),
            "history_future_split": "missing",
            "source_holdout": "missing",
            "positive_and_inapplicable_controls": "missing",
            "reasons": insufficiency,
        },
        "inventory_sha256": "",
    }
    payload["inventory_sha256"] = _sha_json({key: value for key, value in payload.items() if key != "inventory_sha256"})
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", action="append", type=Path, dest="roots")
    args = parser.parse_args()
    roots = args.roots or tuple(Path(item) for item in R4_ROOTS)
    payload = build_inventory(roots)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"R5_EVIDENCE_INVENTORY_STATUS=ready")
    print(f"R5_EVIDENCE_INVENTORY_SHA256={payload['inventory_sha256']}")
    print(f"UNIQUE_ARTIFACTS={payload['deduplication']['unique_artifact_count']}")
    print(f"DUPLICATE_ARTIFACTS={payload['deduplication']['duplicate_artifact_count']}")
    print(f"HISTORICAL_PROVIDER_CALLS={payload['historical_evidence_usage']['provider_calls']}")
    print(f"HISTORICAL_VITIS_LAUNCHES={payload['historical_evidence_usage']['vitis_launches']}")
    print("R5_CONSUMED_PROVIDER_CALLS=0")
    print("R5_CONSUMED_VITIS_LAUNCHES=0")
    print(f"R5_REMAINING_PROVIDER_CALLS={CAP}")
    print(f"R5_REMAINING_VITIS_LAUNCHES={CAP}")
    print(f"R5_PRIMARY_DATASET_READY={str(payload['dataset_readiness']['primary_r5_campaign_ready']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
