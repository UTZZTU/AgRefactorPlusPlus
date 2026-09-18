#!/usr/bin/env python3
"""Independent file-only audit of the R4-to-R5 predecessor import."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.recovery import (
    AppendOnlyEpisodeLedger,
    Lifecycle,
    R5LifecycleReducer,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    canonical_sha256,
    verify_package_d_predecessor,
)


STATE_PATH = Path("docs/roadmap/V2_3_STATE.json")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--import-root", type=Path, required=True)
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    evidence_root = args.evidence_root.expanduser().resolve()
    import_root = args.import_root.expanduser().resolve()
    state = _load_object(repo / STATE_PATH)
    report_path = import_root / "predecessor_import_result.json"
    report = _load_object(report_path)
    unsigned = dict(report)
    stored = unsigned.pop("result_sha256", None)
    findings: list[dict[str, str]] = []
    if stored != canonical_sha256(unsigned):
        findings.append({"severity": "critical", "code": "import_report_hash_mismatch"})

    expected = verify_package_d_predecessor(
        evidence_root=evidence_root,
        expected_archive_sha256=str(state["R4_EXTERNAL_ACCEPTANCE_ARCHIVE_SHA256"]),
        expected_calibration_certificate_id=str(
            state["R2_TO_R4_CALIBRATION_CERTIFICATE_ID"]
        ),
    )
    ledger = AppendOnlyEpisodeLedger(import_root / "ledger")
    observed = ledger.records()
    expected_by_id = {item.episode_id: item.envelope_sha256 for item in expected}
    observed_by_id = {item.episode_id: item.envelope_sha256 for item in observed}
    if observed_by_id != expected_by_id:
        findings.append({"severity": "critical", "code": "ledger_evidence_mismatch"})
    reduction = R5LifecycleReducer().reduce(
        observed,
        revision_id="r5-predecessor-independent-audit-r1",
        failure_family="unsupported_construct",
        stage="csynth",
        owner="candidate",
        supported_when={
            "failure_family": "unsupported_construct",
            "stage": "csynth",
            "owner": "candidate",
        },
        avoid_when={"conflict": True, "sparse": True, "ood": True},
        exact_exclusions={},
        required_evidence=("agent_safe_diagnostic", "accepted_calibration"),
        calibration_refs=(str(state["R2_TO_R4_CALIBRATION_CERTIFICATE_ID"]),),
        memory_payload_manifest_sha256=R5_MEMORY_PAYLOAD_POLICY_SHA256,
        created_at="2026-09-19T00:00:00Z",
    )
    if (
        reduction.revision.lifecycle is not Lifecycle.PROVISIONAL
        or reduction.positive_count != 2
        or reduction.independent_sources != 1
        or reduction.independent_contexts < 1
    ):
        findings.append({"severity": "critical", "code": "lifecycle_overpromotion"})
    if state.get("R5_ACCEPTED") is not False or state.get("R6_STARTED") is not False:
        findings.append({"severity": "critical", "code": "roadmap_boundary_violated"})

    critical = sum(item["severity"] == "critical" for item in findings)
    audit = {
        "schema_version": 1,
        "auditor": "r5-predecessor-import-file-auditor-v1",
        "execution_boundary": "separate_python_process_file_only",
        "status": "clean" if not findings else "blocked",
        "reason": (
            "accepted_r4_predecessor_import_is_provisional_only"
            if not findings
            else "predecessor_import_finding"
        ),
        "findings": findings,
        "critical_finding_count": critical,
        "verified_positive_count": reduction.positive_count,
        "independent_source_count": reduction.independent_sources,
        "independent_context_count": reduction.independent_contexts,
        "lifecycle": reduction.revision.lifecycle.value,
        "provider_calls": 0,
        "vitis_launches": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = canonical_sha256(audit)
    _atomic_json(import_root / "independent_predecessor_import_audit.json", audit)
    print("R5_PREDECESSOR_IMPORT_AUDIT_STATUS=" + str(audit["status"]))
    print("CRITICAL_FINDINGS=" + str(critical))
    print("R5_PREDECESSOR_LIFECYCLE=" + reduction.revision.lifecycle.value)
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
