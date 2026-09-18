#!/usr/bin/env python3
"""Import accepted Package D R4 episodes as R5 predecessor history."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.recovery import (
    Lifecycle,
    R5LifecycleReducer,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    canonical_sha256,
    import_package_d_predecessor,
)


STATE_PATH = Path("docs/roadmap/V2_3_STATE.json")


class PredecessorImportCommandError(RuntimeError):
    """Raised when repository state does not authorize predecessor import."""


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PredecessorImportCommandError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        raise PredecessorImportCommandError("git command failed: " + " ".join(args))
    return completed.stdout.strip()


def validate_state(repo: Path, evidence_root: Path) -> tuple[dict[str, Any], str]:
    state = _load_object(repo / STATE_PATH)
    if _git(repo, "branch", "--show-current") != "research-roadmap-v2.3":
        raise PredecessorImportCommandError("predecessor import requires the R5 branch")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise PredecessorImportCommandError("predecessor import requires a clean repository")
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
    ):
        raise PredecessorImportCommandError("roadmap state does not permit predecessor import")
    expected_root = Path(str(state.get("R4_EXTERNAL_ACCEPTANCE_EVIDENCE_ROOT", ""))).resolve()
    if expected_root != evidence_root:
        raise PredecessorImportCommandError("evidence root differs from accepted R4 state")
    archive_sha256 = state.get("R4_EXTERNAL_ACCEPTANCE_ARCHIVE_SHA256")
    if not isinstance(archive_sha256, str):
        raise PredecessorImportCommandError("accepted R4 archive identity is missing")
    if state.get("R2_TO_R4_CALIBRATION_CERTIFICATE_ID") is None:
        raise PredecessorImportCommandError("accepted calibration identity is missing")
    return state, archive_sha256


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    evidence_root = args.evidence_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise PredecessorImportCommandError("output directory must not already exist")
    state, archive_sha256 = validate_state(repo, evidence_root)
    output.mkdir(parents=True)
    imported = import_package_d_predecessor(
        evidence_root=evidence_root,
        ledger_root=output / "ledger",
        expected_archive_sha256=archive_sha256,
        expected_calibration_certificate_id=str(
            state["R2_TO_R4_CALIBRATION_CERTIFICATE_ID"]
        ),
    )
    reduction = R5LifecycleReducer().reduce(
        imported.envelopes,
        revision_id="r5-predecessor-unsupported-construct-r1",
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
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    if (
        reduction.revision.lifecycle is not Lifecycle.PROVISIONAL
        or reduction.positive_count != 2
        or reduction.independent_sources != 1
        or reduction.independent_contexts < 1
    ):
        raise PredecessorImportCommandError(
            "predecessor evidence must establish exactly Provisional support"
        )
    result = {
        **imported.to_dict(),
        "repository_head": _git(repo, "rev-parse", "HEAD"),
        "lifecycle_reduction": {
            "lifecycle": reduction.revision.lifecycle.value,
            "revision": reduction.revision.to_dict(),
            "positive_count": reduction.positive_count,
            "negative_count": reduction.negative_count,
            "independent_sources": reduction.independent_sources,
            "independent_contexts": reduction.independent_contexts,
        },
        "status": "ready_for_independent_predecessor_import_audit",
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = canonical_sha256(result)
    _atomic_json(output / "predecessor_import_result.json", result)
    print("R5_PREDECESSOR_IMPORT_STATUS=ready_for_independent_audit")
    print("R5_PREDECESSOR_LIFECYCLE=Provisional")
    print(f"R5_PREDECESSOR_INDEPENDENT_SOURCES={reduction.independent_sources}")
    print(f"R5_PREDECESSOR_CONTEXT_SIGNATURES={reduction.independent_contexts}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
