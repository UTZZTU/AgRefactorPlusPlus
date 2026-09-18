#!/usr/bin/env python3
"""Audit a persisted V2.3 R2 calibration bundle in a separate process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agrefactor.evidence import audit_r2_calibration_bundle


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bundle_path = args.bundle.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else bundle_path.with_name("independent_calibration_audit.json")
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    report = audit_r2_calibration_bundle(bundle)
    value = {
        **report.to_dict(),
        "independent_process": True,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
    }
    atomic_json(output_path, value)
    print(f"R2_CALIBRATION_INDEPENDENT_AUDIT={report.status}")
    print(f"R2_CALIBRATION_CERTIFICATE={report.summary_status}")
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("R4_ACCEPTED=false")
    return 0 if report.status == "clean" else 2


if __name__ == "__main__":
    raise SystemExit(main())
