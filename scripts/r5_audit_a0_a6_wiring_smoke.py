from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path("/data/AgRefactor")
STATE = ROOT / "docs/roadmap/V2_3_STATE.json"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object: {path}")
    return value

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    smoke_path = root / "wiring_smoke.json"
    protocol_path = root / "protocol_audit.json"
    state = load(STATE)
    smoke = load(smoke_path)
    protocol = load(protocol_path)
    findings: list[dict[str, str]] = []

    if smoke.get("status") != "passed":
        findings.append({"severity": "critical", "code": "smoke_not_passed"})
    if protocol.get("status") != "clean":
        findings.append({"severity": "critical", "code": "protocol_not_clean"})
    if smoke.get("synthetic_smoke") is not True:
        findings.append({"severity": "critical", "code": "synthetic_marker_missing"})
    if smoke.get("history_only") is not True or smoke.get("future_files_read") is not False:
        findings.append({"severity": "critical", "code": "history_boundary_violation"})
    if smoke.get("provider_calls") != 0 or smoke.get("vitis_launches") != 0:
        findings.append({"severity": "critical", "code": "unexpected_external_calls"})
    if smoke.get("git_history_mutations") != 0 or smoke.get("cross_arm_cache_used") is not False:
        findings.append({"severity": "critical", "code": "mutation_or_cache_violation"})
    expected_arms = ("A0", "A1", "A2", "A3", "A4", "A5", "A6")
    arms = smoke.get("arms", {})
    if tuple(arms) != expected_arms:
        findings.append({"severity": "critical", "code": "arm_inventory_mismatch"})
    for arm in expected_arms:
        row = arms.get(arm, {})
        if row.get("cross_arm_cache_used") is not False:
            findings.append({"severity": "critical", "code": f"{arm}_cache_violation"})
        if row.get("provider_calls") != 0 or row.get("vitis_launches") != 0:
            findings.append({"severity": "critical", "code": f"{arm}_external_call_violation"})
        if arm in {"A0", "A1"}:
            if row.get("status") != "observation_only" or row.get("mutation_calls") != 0:
                findings.append({"severity": "critical", "code": f"{arm}_observation_contract"})
        else:
            if row.get("status") != "verified_positive" or row.get("mutation_calls") != 1:
                findings.append({"severity": "critical", "code": f"{arm}_mutation_contract"})
        profile = row.get("profile", {})
        if profile.get("arm") != arm or profile.get("candidate_only") is not True:
            findings.append({"severity": "critical", "code": f"{arm}_profile_contract"})
    if smoke.get("trusted_revision_sha256") != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_REVISION_SHA256"):
        findings.append({"severity": "critical", "code": "revision_hash_mismatch"})
    if smoke.get("trusted_snapshot_sha256") != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_SNAPSHOT_SHA256"):
        findings.append({"severity": "critical", "code": "snapshot_hash_mismatch"})
    if smoke.get("trusted_payload_sha256") != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_PAYLOAD_SHA256"):
        findings.append({"severity": "critical", "code": "payload_hash_mismatch"})
    if smoke.get("repository_head") != state.get("head"):
        findings.append({"severity": "warning", "code": "repository_head_changed_since_smoke"})
    status = "clean" if not findings else "blocked"
    audit = {
        "schema_version": "r5-a0-a6-wiring-independent-audit-v1",
        "auditor": "r5-a0-a6-wiring-file-only-auditor-v1",
        "status": status,
        "reason": "all_arm_bindings_and_boundaries_verified" if not findings else "critical_or_warning_findings",
        "critical_finding_count": sum(item["severity"] == "critical" for item in findings),
        "findings": findings,
        "wiring_smoke_sha256": sha(smoke_path),
        "protocol_audit_sha256": sha(protocol_path),
        "repository_head": smoke.get("repository_head"),
        "trusted_revision_sha256": smoke.get("trusted_revision_sha256"),
        "trusted_snapshot_sha256": smoke.get("trusted_snapshot_sha256"),
        "trusted_payload_sha256": smoke.get("trusted_payload_sha256"),
        "future_files_read": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
    }
    audit["audit_sha256"] = hashlib.sha256(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (root / "independent_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    print("R5_A0_A6_WIRING_AUDIT_STATUS=" + status)
    print("R5_A0_A6_WIRING_AUDIT_REASON=" + audit["reason"])
    print("CRITICAL_FINDINGS=" + str(audit["critical_finding_count"]))
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0 if status == "clean" else 1

if __name__ == "__main__":
    raise SystemExit(main())

