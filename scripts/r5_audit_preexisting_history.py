#!/usr/bin/env python3
"""Zero-call audit for one frozen pre-existing R5 history Candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.recovery.r5_historical_candidate import (
    canonical_sha256,
    file_sha256,
    verify_historical_candidate_plan,
)


class PreexistingHistoryAuditError(RuntimeError):
    """Raised when the frozen history acquisition cannot run safely."""


def _certificate_scope(certificate: Mapping[str, Any]) -> list[str]:
    explicit = certificate.get("failure_class_scope")
    if isinstance(explicit, list) and all(
        isinstance(item, str) and item for item in explicit
    ):
        return list(explicit)
    if (
        certificate.get("schema_version") == 1
        and certificate.get("split_id")
        == "v23-r2-real-vitis-unsupported-construct-v1"
    ):
        return ["unsupported_construct"]
    raise PreexistingHistoryAuditError("calibration failure-class scope is missing")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryAuditError(f"invalid JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryAuditError(f"JSON root is not an object: {path}")
    return value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PreexistingHistoryAuditError("git command failed: " + " ".join(args))
    return completed.stdout.strip()


def _verify_fixed_prior_attempt(
    *, repository: Path, plan: Mapping[str, Any], bundle: Any, head: str
) -> dict[str, Any]:
    prior = plan.get("prior_failed_attempt")
    if not isinstance(prior, Mapping):
        raise PreexistingHistoryAuditError("fixed resume lacks prior attempt binding")
    result_path = Path(str(prior.get("acquisition_result_path", ""))).resolve()
    audit_path = Path(str(prior.get("independent_audit_path", ""))).resolve()
    reconciliation_relative = Path(str(prior.get("reconciliation_path", "")))
    reconciliation_path = (repository / reconciliation_relative).resolve()
    try:
        reconciliation_path.relative_to(repository)
    except ValueError as exc:
        raise PreexistingHistoryAuditError("failure reconciliation is unsafe") from exc
    for path in (result_path, audit_path, reconciliation_path):
        if path.is_symlink() or not path.is_file():
            raise PreexistingHistoryAuditError("prior attempt evidence is missing")
    expected_hashes = {
        result_path: prior.get("acquisition_result_file_sha256"),
        audit_path: prior.get("independent_audit_file_sha256"),
        reconciliation_path: prior.get("reconciliation_file_sha256"),
    }
    if any(
        file_sha256(path) != expected
        for path, expected in expected_hashes.items()
    ):
        raise PreexistingHistoryAuditError("prior attempt evidence hash mismatch")
    result = _load(result_path)
    audit = _load(audit_path)
    reconciliation = _load(reconciliation_path)
    prior_status = str(prior.get("status", ""))
    expected_provider_calls = (
        2
        if prior_status == "clean_provider_or_response_contract_failure"
        else 1
    )
    expected_reconciliation_status = prior.get(
        "reconciliation_status",
        (
            "audited_pre_provider_model_adapter_failure_fixed"
            if prior_status == "clean_pre_provider_model_adapter_failure"
            else None
        ),
    )
    budget = plan.get("budget")
    if (
        prior_status
        not in {
            "clean_pre_provider_model_adapter_failure",
            "clean_provider_or_response_contract_failure",
        }
        or not isinstance(budget, Mapping)
        or not isinstance(expected_reconciliation_status, str)
        or result.get("status") != "inconclusive"
        or result.get("provider_calls") != expected_provider_calls
        or result.get("vitis_launches") != 2
        or audit.get("status") != prior_status
        or audit.get("source_sha256") != bundle.source_sha256
        or audit.get("critical_finding_count") != 0
        or audit.get("blocking_finding_count") != 1
        or audit.get("provider_calls") != expected_provider_calls
        or audit.get("vitis_launches") != 2
        or reconciliation.get("status") != expected_reconciliation_status
        or reconciliation.get("source_sha256") != bundle.source_sha256
        or reconciliation.get("remaining_attempts") != 2
        or reconciliation.get("budget_after")
        != {
            "provider_calls": budget.get("provider_calls_before"),
            "vitis_launches": budget.get("vitis_launches_before"),
        }
    ):
        raise PreexistingHistoryAuditError("prior attempt boundary is incompatible")
    fix_commit = str(prior.get("fix_commit", ""))
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            fix_commit,
            head,
        ],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise PreexistingHistoryAuditError("isolation fix is not an ancestor")
    return {
        "status": audit["status"],
        "acquisition_result_file_sha256": file_sha256(result_path),
        "independent_audit_file_sha256": file_sha256(audit_path),
        "independent_audit_sha256": audit.get("audit_sha256"),
        "reconciliation_file_sha256": file_sha256(reconciliation_path),
        "fix_commit": fix_commit,
        "fix_is_ancestor": True,
    }


def _verify_final_prior_attempt(
    *, repository: Path, plan: Mapping[str, Any], bundle: Any
) -> dict[str, Any]:
    prior = plan.get("prior_attempt")
    if not isinstance(prior, Mapping):
        raise PreexistingHistoryAuditError("final resume lacks prior attempt binding")
    parent_relative = Path(str(prior.get("parent_plan_path", "")))
    parent_path = (repository / parent_relative).resolve()
    result_path = Path(str(prior.get("acquisition_result_path", ""))).resolve()
    audit_path = Path(str(prior.get("independent_audit_path", ""))).resolve()
    reconciliation_relative = Path(str(prior.get("reconciliation_path", "")))
    reconciliation_path = (repository / reconciliation_relative).resolve()
    for path in (parent_path, reconciliation_path):
        try:
            path.relative_to(repository)
        except ValueError as exc:
            raise PreexistingHistoryAuditError("final resume repo evidence is unsafe") from exc
    expected_hashes = {
        parent_path: prior.get("parent_plan_file_sha256"),
        result_path: prior.get("acquisition_result_file_sha256"),
        audit_path: prior.get("independent_audit_file_sha256"),
        reconciliation_path: prior.get("reconciliation_file_sha256"),
    }
    if any(path.is_symlink() or not path.is_file() for path in expected_hashes):
        raise PreexistingHistoryAuditError("final resume evidence is missing")
    if any(
        file_sha256(path) != expected
        for path, expected in expected_hashes.items()
    ):
        raise PreexistingHistoryAuditError("final resume evidence hash mismatch")
    parent = verify_historical_candidate_plan(repository, _load(parent_path))
    result = _load(result_path)
    audit = _load(audit_path)
    reconciliation = _load(reconciliation_path)
    if (
        parent.source_sha256 != bundle.source_sha256
        or parent.isolated_candidate_sha256 != bundle.isolated_candidate_sha256
        or result.get("status") != "abstained"
        or result.get("provider_calls") != 1
        or result.get("vitis_launches") != 2
        or audit.get("status") != "clean_safe_calibration_abstention"
        or audit.get("source_sha256") != bundle.source_sha256
        or audit.get("critical_finding_count") != 0
        or audit.get("blocking_finding_count") != 0
        or reconciliation.get("status")
        != "clean_safe_calibration_abstention_one_attempt_remaining"
        or reconciliation.get("source_sha256") != bundle.source_sha256
        or reconciliation.get("remaining_attempts") != 1
        or reconciliation.get("budget_after")
        != {"provider_calls": 98, "vitis_launches": 43}
    ):
        raise PreexistingHistoryAuditError("final prior attempt is incompatible")
    return {
        "status": audit["status"],
        "parent_plan_file_sha256": file_sha256(parent_path),
        "acquisition_result_file_sha256": file_sha256(result_path),
        "independent_audit_file_sha256": file_sha256(audit_path),
        "independent_audit_sha256": audit.get("audit_sha256"),
        "reconciliation_file_sha256": file_sha256(reconciliation_path),
    }


def _verify_cosim_prior_attempt(
    *,
    repository: Path,
    plan: Mapping[str, Any],
    bundle: Any,
    head: str,
) -> dict[str, Any]:
    prior = plan.get("prior_attempt")
    if not isinstance(prior, Mapping):
        raise PreexistingHistoryAuditError(
            "post-COSIM-fix resume lacks prior attempt binding"
        )
    parent_relative = Path(str(prior.get("parent_plan_path", "")))
    reconciliation_relative = Path(str(prior.get("reconciliation_path", "")))
    parent_path = (repository / parent_relative).resolve()
    reconciliation_path = (repository / reconciliation_relative).resolve()
    for path in (parent_path, reconciliation_path):
        try:
            path.relative_to(repository)
        except ValueError as exc:
            raise PreexistingHistoryAuditError(
                "post-COSIM-fix repo evidence is unsafe"
            ) from exc
    result_path = Path(str(prior.get("acquisition_result_path", ""))).resolve()
    audit_path = Path(str(prior.get("independent_audit_path", ""))).resolve()
    expected_hashes = {
        parent_path: prior.get("parent_plan_file_sha256"),
        result_path: prior.get("acquisition_result_file_sha256"),
        audit_path: prior.get("independent_audit_file_sha256"),
        reconciliation_path: prior.get("reconciliation_file_sha256"),
    }
    if any(path.is_symlink() or not path.is_file() for path in expected_hashes):
        raise PreexistingHistoryAuditError(
            "post-COSIM-fix prior evidence is missing"
        )
    if any(
        file_sha256(path) != expected
        for path, expected in expected_hashes.items()
    ):
        raise PreexistingHistoryAuditError(
            "post-COSIM-fix prior evidence hash mismatch"
        )
    parent = verify_historical_candidate_plan(repository, _load(parent_path))
    result = _load(result_path)
    audit = _load(audit_path)
    reconciliation = _load(reconciliation_path)
    budget = plan.get("budget")
    candidate_after = audit.get("outcome", {}).get("candidate_after_sha256")
    if (
        parent.source_sha256 != bundle.source_sha256
        or parent.isolated_candidate_sha256 != bundle.isolated_candidate_sha256
        or prior.get("status")
        != "clean_cosim_interface_depth_configuration_failure"
        or prior.get("attempts_consumed") != 2
        or prior.get("maximum_remaining_attempts") != 1
        or result.get("status") != "inconclusive"
        or result.get("provider_calls") != 2
        or result.get("vitis_launches") != 5
        or audit.get("status")
        != "clean_cosim_interface_depth_configuration_failure"
        or audit.get("source_sha256") != bundle.source_sha256
        or audit.get("critical_finding_count") != 0
        or audit.get("blocking_finding_count") != 1
        or audit.get("provider_calls") != 2
        or audit.get("vitis_launches") != 5
        or not isinstance(candidate_after, str)
        or not re.fullmatch(r"[0-9a-f]{64}", candidate_after)
        or reconciliation.get("status")
        != "audited_cosim_interface_depth_contract_fixed"
        or reconciliation.get("source_sha256") != bundle.source_sha256
        or reconciliation.get("candidate_after_sha256") != candidate_after
        or reconciliation.get("remaining_attempts") != 1
        or not isinstance(budget, Mapping)
        or reconciliation.get("budget_after")
        != {
            "provider_calls": budget.get("provider_calls_before"),
            "vitis_launches": budget.get("vitis_launches_before"),
        }
    ):
        raise PreexistingHistoryAuditError(
            "post-COSIM-fix prior attempt is incompatible"
        )
    fix_commit = str(prior.get("fix_commit", ""))
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            fix_commit,
            head,
        ],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise PreexistingHistoryAuditError(
            "COSIM interface contract fix is not an ancestor"
        )
    return {
        "status": audit["status"],
        "parent_plan_file_sha256": file_sha256(parent_path),
        "acquisition_result_file_sha256": file_sha256(result_path),
        "independent_audit_file_sha256": file_sha256(audit_path),
        "independent_audit_sha256": audit.get("audit_sha256"),
        "reconciliation_file_sha256": file_sha256(reconciliation_path),
        "candidate_after_sha256": candidate_after,
        "fix_commit": fix_commit,
        "fix_is_ancestor": True,
    }


def _compile_and_run(
    *,
    compiler: str,
    reference_code: str,
    candidate_code: str,
    test_code: str,
    split: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="r5-history-audit-") as temporary:
        root = Path(temporary)
        reference = root / "reference.cpp"
        candidate = root / "candidate.cpp"
        test = root / f"{split.casefold()}.cpp"
        executable = root / "oracle"
        reference.write_text(reference_code, encoding="utf-8")
        candidate.write_text(candidate_code, encoding="utf-8")
        test.write_text(test_code, encoding="utf-8")
        compile_result = subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O0",
                str(reference),
                str(candidate),
                str(test),
                "-o",
                str(executable),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if compile_result.returncode != 0:
            raise PreexistingHistoryAuditError(
                f"{split} host adapter compile failed"
            )
        run_result = subprocess.run(
            [str(executable)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if run_result.returncode != 0:
            raise PreexistingHistoryAuditError(
                f"{split} host differential oracle failed"
            )
        return {
            "split": split,
            "compile_returncode": compile_result.returncode,
            "run_returncode": run_result.returncode,
            "stdout_sha256": hashlib.sha256(run_result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(run_result.stderr).hexdigest(),
            "source_persisted": False,
        }


def audit(
    *,
    repository: Path,
    plan_path: Path,
    state_path: Path,
    output_path: Path,
    compiler: str,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    plan = _load(plan_path)
    state = _load(state_path)
    bundle = verify_historical_candidate_plan(repository, plan)
    budget = plan.get("budget")
    if not isinstance(budget, Mapping):
        raise PreexistingHistoryAuditError("plan budget is missing")

    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise PreexistingHistoryAuditError("protocol audit requires the clean R5 branch")
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_CONSUMED_PROVIDER_CALLS")
        != budget.get("provider_calls_before")
        or state.get("R5_CONSUMED_VITIS_LAUNCHES")
        != budget.get("vitis_launches_before")
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
    ):
        raise PreexistingHistoryAuditError("roadmap state does not permit acquisition")
    prior_attempt_binding = (
        _verify_fixed_prior_attempt(
            repository=repository,
            plan=plan,
            bundle=bundle,
            head=head,
        )
        if plan.get("schema_version") == 3
        else (
            _verify_final_prior_attempt(
                repository=repository,
                plan=plan,
                bundle=bundle,
            )
            if plan.get("schema_version") == 4
            else (
                _verify_cosim_prior_attempt(
                    repository=repository,
                    plan=plan,
                    bundle=bundle,
                    head=head,
                )
                if plan.get("schema_version") == 5
                else None
            )
        )
    )

    predecessor_path = Path(str(plan["predecessor_import_result"]))
    if (
        file_sha256(predecessor_path)
        != plan.get("predecessor_import_result_file_sha256")
    ):
        raise PreexistingHistoryAuditError("predecessor import result hash mismatch")
    predecessor = _load(predecessor_path)
    predecessor_sources = sorted(
        {
            item.get("source_sha256")
            for item in (
                _load(path)
                for path in sorted(
                    (predecessor_path.parent / "ledger").glob("*.json")
                )
                if path.name != "ledger_manifest.json"
            )
        }
    )
    if (
        predecessor.get("status")
        != "ready_for_independent_predecessor_import_audit"
        or predecessor.get("verified_positive_count") != 2
        or predecessor.get("independent_source_count") != 1
        or predecessor.get("lifecycle_reduction", {}).get("lifecycle")
        != "Provisional"
        or predecessor_sources != sorted(plan["predecessor_source_sha256s"])
        or bundle.source_sha256 in predecessor_sources
    ):
        raise PreexistingHistoryAuditError("predecessor independence is not proven")

    calibration_path = Path(str(plan["calibration_bundle"]))
    if file_sha256(calibration_path) != plan.get("calibration_bundle_file_sha256"):
        raise PreexistingHistoryAuditError("calibration bundle hash mismatch")
    calibration = _load(calibration_path)
    certificate = calibration.get("certificate")
    certificate_scope = (
        _certificate_scope(certificate)
        if isinstance(certificate, Mapping)
        else []
    )
    if (
        not isinstance(certificate, Mapping)
        or certificate.get("accepted") is not True
        or certificate.get("certificate_id")
        != plan.get("calibration_certificate_id")
        or certificate_scope != plan.get("calibration_failure_class_scope")
    ):
        raise PreexistingHistoryAuditError("calibration scope is incompatible")

    legacy = bundle.legacy_candidate_code
    required_markers = plan.get("required_legacy_markers")
    if required_markers is None:
        required_markers = [
            "insert(newitem, tree.space[root].right)",
            "insert(newitem, tree.space[root].left)",
            "dfs_traverse(tree.space[root].left)",
            "dfs_traverse(tree.space[root].right)",
        ]
    if not isinstance(required_markers, list) or not all(
        isinstance(marker, str) and marker and marker in legacy
        for marker in required_markers
    ):
        raise PreexistingHistoryAuditError("recursive construct evidence is missing")
    host_checks = [
        _compile_and_run(
            compiler=compiler,
            reference_code=bundle.reference_code,
            candidate_code=bundle.isolated_candidate_code,
            test_code=bundle.public_test_code,
            split="Public",
        ),
        _compile_and_run(
            compiler=compiler,
            reference_code=bundle.reference_code,
            candidate_code=bundle.isolated_candidate_code,
            test_code=bundle.hidden_test_code,
            split="Hidden",
        ),
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-preexisting-history-zero-call-audit-v1",
        "status": "ready_for_preexisting_history_acquisition",
        "repository_head": head,
        "repository_branch": branch,
        "plan_path": str(plan_path),
        "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": bundle.plan_sha256,
        "state_file_sha256": file_sha256(state_path),
        "case_identity": bundle.to_identity(),
        "public_runtime_contract": dict(bundle.public_runtime_contract),
        "host_adapter_checks": host_checks,
        "predecessor_import_result_file_sha256": file_sha256(predecessor_path),
        "predecessor_source_sha256s": predecessor_sources,
        "prior_observed_source_sha256s": list(
            plan.get("prior_observed_source_sha256s", predecessor_sources)
        ),
        "source_independence_verified": True,
        "prior_failed_attempt": prior_attempt_binding,
        "calibration_bundle_file_sha256": file_sha256(calibration_path),
        "calibration_certificate_id": certificate["certificate_id"],
        "calibration_failure_class_scope": certificate_scope,
        "provider_calls_before": int(budget["provider_calls_before"]),
        "vitis_launches_before": int(budget["vitis_launches_before"]),
        "provider_call_upper_bound": int(budget["provider_call_upper_bound"]),
        "vitis_launch_upper_bound": int(budget["vitis_launch_upper_bound"]),
        "provider_calls": 0,
        "vitis_launches": 0,
        "future_holdout_case_ids": list(plan["future_holdout_case_ids"]),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "trusted_revision_creation_allowed": False,
        "r5_real_campaign_allowed": False,
        "r5_accepted": False,
        "r6_started": False,
        "critical_finding_count": 0,
        "findings": [],
        "next_step": "run_one_preexisting_history_candidate_through_existing_r2_r4",
    }
    result["audit_sha256"] = canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name("." + output_path.name + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path(
            "configs/r5/preexisting_history/recursive_e2_dfs_plan.json"
        ),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("docs/roadmap/V2_3_STATE.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args(argv)
    result = audit(
        repository=args.repo,
        plan_path=args.plan,
        state_path=args.state,
        output_path=args.output,
        compiler=args.compiler,
    )
    print("R5_PREEXISTING_HISTORY_AUDIT_STATUS=" + str(result["status"]))
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
