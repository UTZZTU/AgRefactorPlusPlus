#!/usr/bin/env python3
"""Execute only the authorized replacement observation for pilot repeat 3."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.models import resolve_model_runtime
from scripts import r5_bounded_pilot as pilot


STATE = ROOT / "docs/roadmap/V2_3_STATE.json"
RUN1 = Path("/data/agrefactor_runs/r5_p5_bounded_real_pilot_99420db_run1")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON object required: " + str(path))
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(output: Path, protocol_path: Path) -> dict[str, Any]:
    state = load(STATE)
    protocol = load(protocol_path)
    unsigned_protocol = dict(protocol)
    stored_protocol_hash = unsigned_protocol.pop("audit_sha256", None)
    if hashlib.sha256(canonical(unsigned_protocol).encode()).hexdigest() != stored_protocol_hash:
        raise RuntimeError("resume protocol audit hash is invalid")
    if protocol.get("status") != "ready_for_repeat_03_resume":
        raise RuntimeError("resume protocol audit is not ready")
    if protocol.get("authorized_repeats") != [3]:
        raise RuntimeError("resume protocol does not authorize exactly repeat 3")
    for relative, expected in dict(protocol.get("source_file_sha256", {})).items():
        source_path = ROOT / relative
        if not source_path.is_file() or sha(source_path) != expected:
            raise RuntimeError("source changed after resume protocol audit: " + relative)
    if state.get("R5_CONSUMED_PROVIDER_CALLS") != protocol.get("provider_calls_before"):
        raise RuntimeError("provider ledger changed after resume audit")
    if state.get("R5_CONSUMED_VITIS_LAUNCHES") != protocol.get("vitis_launches_before"):
        raise RuntimeError("Vitis ledger changed after resume audit")
    if state.get("R5_ACCEPTED") is not False or state.get("R6_STARTED") is not False:
        raise RuntimeError("R5/R6 state does not permit resume")
    audit_head = str(protocol.get("repository_head", ""))
    if subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", audit_head, "HEAD"],
        check=False,
    ).returncode != 0:
        raise RuntimeError("resume protocol head is not an ancestor")
    original_manifest_path = RUN1 / "pilot_manifest.json"
    if sha(original_manifest_path) != protocol.get("original_pilot_manifest_file_sha256"):
        raise RuntimeError("original pilot manifest changed after resume audit")
    original_manifest = load(original_manifest_path)
    case = pilot.load_case()
    runtime_value = load(pilot.CALIBRATION_BUNDLE)["model_runtime"]
    runtime = resolve_model_runtime(
        str(runtime_value["model"]),
        family=str(runtime_value["family"]),
        base_url=str(runtime_value["base_url"]),
        api_key_env=str(runtime_value["api_key_env"]),
        reasoning_effort="auto",
        parameters=dict(runtime_value["request_parameters"]),
    )
    _, revision, snapshot, payload, reduction = pilot.objects()
    resume_manifest = {
        "schema_version": 1,
        "resume_id": "v2.3-r5-bounded-pilot-repeat-03-replacement-v1",
        "status": "frozen_before_repeat_03_replacement",
        "repository_head": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "original_run_root": str(RUN1),
        "original_pilot_manifest_file_sha256": sha(original_manifest_path),
        "original_pilot_manifest_sha256": original_manifest["pilot_manifest_sha256"],
        "partial_audit_file_sha256": sha(RUN1 / "partial_independent_audit.json"),
        "resume_protocol_audit_file_sha256": sha(protocol_path),
        "resume_protocol_audit_sha256": protocol["audit_sha256"],
        "authorized_repeats": [3],
        "forbidden_repeats": [1, 2],
        "provider_calls_before": protocol["provider_calls_before"],
        "vitis_launches_before": protocol["vitis_launches_before"],
        "provider_upper_bound": protocol["resume_provider_upper_bound"],
        "vitis_upper_bound": protocol["resume_vitis_upper_bound"],
        "r5_accepted": False,
        "r6_started": False,
    }
    resume_manifest["resume_manifest_sha256"] = hashlib.sha256(
        canonical(resume_manifest).encode()
    ).hexdigest()
    write(output / "resume_manifest.json", resume_manifest)
    baseline, observations, provider_calls, vitis_launches = pilot.run_repeat(
        output=output,
        pilot=original_manifest,
        case=case,
        runtime_value=runtime_value,
        runtime=runtime,
        certificate_value=pilot.certificate(),
        revision=revision,
        snapshot=snapshot,
        payload=payload,
        reduction=reduction,
        repeat=3,
        run_id_suffix=".replacement-" + protocol["audit_sha256"][:12],
    )
    if provider_calls > int(resume_manifest["provider_upper_bound"]):
        raise RuntimeError("resume exceeded Provider upper bound")
    if vitis_launches > int(resume_manifest["vitis_upper_bound"]):
        raise RuntimeError("resume exceeded Vitis upper bound")
    result = {
        "schema_version": 1,
        "status": "ready_for_combined_independent_audit",
        "resume_manifest_sha256": resume_manifest["resume_manifest_sha256"],
        "replacement_repeat": 3,
        "baseline": baseline,
        "observations": observations,
        "provider_calls": provider_calls,
        "vitis_launches": vitis_launches,
        "provider_calls_after": int(resume_manifest["provider_calls_before"]) + provider_calls,
        "vitis_launches_after": int(resume_manifest["vitis_launches_before"]) + vitis_launches,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = hashlib.sha256(canonical(result).encode()).hexdigest()
    write(output / "resume_result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("output directory already exists")
    output.mkdir(parents=True)
    result = run(output, args.protocol_audit.resolve())
    print("R5_PILOT_REPEAT_03_RESUME_STATUS=" + result["status"])
    print("PROVIDER_CALLS=" + str(result["provider_calls"]))
    print("VITIS_LAUNCHES=" + str(result["vitis_launches"]))
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
