#!/usr/bin/env python3
"""Audit one AgRefactor++ product artifact root independently."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from agrefactor.evidence import audit_product_evidence


def _read(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--full-result", type=Path)
    parser.add_argument("--process-record", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.artifact_root
    if root is None and (args.summary is None or args.identity is None):
        parser.error(
            "provide --artifact-root or both --summary and --identity"
        )
    if root is not None:
        root = root.expanduser().resolve()
    summary_path = (
        args.summary
        or (root / "product_summary.json" if root is not None else None)
    )
    identity_path = (
        args.identity
        or (root / "execution_identity.json" if root is not None else None)
    )
    full_path = args.full_result
    if full_path is None and root is not None:
        candidate = root / "full_result.json"
        full_path = candidate if candidate.is_file() else None
    process_path = args.process_record
    if process_path is None and root is not None:
        candidate = root / "run_record.json"
        process_path = candidate if candidate.is_file() else None

    try:
        assert summary_path is not None and identity_path is not None
        report = audit_product_evidence(
            _read(summary_path, required=True),
            _read(identity_path, required=True),
            full_result=(
                None if full_path is None
                else _read(full_path, required=False)
            ),
            process_record=(
                None if process_path is None
                else _read(process_path, required=False)
            ),
        )
        payload = report.to_dict()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        if args.output is not None:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
        return 2 if report.has_errors else 0
    except Exception as exc:
        error = {
            "schema_version": 1,
            "status": "input_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        sys.stderr.write(json.dumps(error, ensure_ascii=False) + "\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
