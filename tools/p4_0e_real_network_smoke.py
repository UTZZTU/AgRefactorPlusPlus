#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from agrefactor.models import (
    ChatMessage,
    ModelCallRole,
    ModelRequest,
    credential_presence_evidence,
    load_invocation_dotenv,
    resolve_model_runtime,
)
from agrefactor.runtime import BudgetLimits, BudgetManager
from agrefactor.runtime.budget import BudgetExceededError


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class NetworkEvidenceClosureError(RuntimeError):
    """Fail-closed P4-0E-R1 network evidence error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_artifact(path: Path, payload: dict[str, Any]) -> str:
    _atomic_json(path, payload)
    digest = _sha256_bytes(path.read_bytes())
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n",
        encoding="utf-8",
    )
    return digest


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise NetworkEvidenceClosureError(
            "repository identity command failed: git " + " ".join(args)
        )
    return completed.stdout.strip()


def _clean_sha(name: str, value: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip().casefold()
    if pattern.fullmatch(cleaned) is None:
        raise ValueError(f"{name} has invalid format")
    return cleaned


def repository_identity(
    repository_root: str | os.PathLike[str],
    *,
    expected_head: str,
    expected_branch: str = "stage2-general-feedback",
) -> dict[str, Any]:
    repo = Path(repository_root).expanduser().resolve()
    if not (repo / ".git").exists():
        raise NetworkEvidenceClosureError("repository root is not a Git checkout")
    expected = _clean_sha("expected_head", expected_head, _GIT_SHA_RE)
    head = _git(repo, "rev-parse", "HEAD").casefold()
    branch = _git(repo, "branch", "--show-current")
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    if head != expected:
        raise NetworkEvidenceClosureError("repository HEAD does not match expected committed HEAD")
    if branch != expected_branch:
        raise NetworkEvidenceClosureError("repository branch does not match expected branch")
    if status:
        raise NetworkEvidenceClosureError("repository must be clean for authoritative network evidence")
    return {
        "root": str(repo),
        "head": head,
        "expected_head": expected,
        "exact_head_match": True,
        "branch": branch,
        "expected_branch": expected_branch,
        "branch_match": True,
        "clean": True,
    }


def _sample_identity(repo: Path) -> tuple[Path, dict[str, Any]]:
    sample_path = repo / "tests/fixtures/p4_0d_rtl_cosim/reference.cpp"
    if sample_path.is_symlink() or not sample_path.is_file():
        raise NetworkEvidenceClosureError(
            "committed real smoke sample is missing or unsafe"
        )
    relative = sample_path.relative_to(repo).as_posix()
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", relative)
    if tracked != relative:
        raise NetworkEvidenceClosureError("real smoke sample is not tracked")
    stage = _git(repo, "ls-files", "-s", "--", relative).split()
    if len(stage) < 4:
        raise NetworkEvidenceClosureError("tracked sample blob identity is unavailable")
    data = sample_path.read_bytes()
    return sample_path, {
        "path": relative,
        "sha256": _sha256_bytes(data),
        "git_blob_sha1": stage[1],
        "size_bytes": len(data),
        "committed_fixture": True,
        "tracked": True,
    }


def _budget_limits_dict(limits: BudgetLimits) -> dict[str, Any]:
    return {
        "max_llm_calls": limits.max_llm_calls,
        "max_tool_calls": limits.max_tool_calls,
        "max_compile_calls": limits.max_compile_calls,
        "max_csim_calls": limits.max_csim_calls,
        "max_csynth_calls": limits.max_csynth_calls,
        "max_cosim_calls": limits.max_cosim_calls,
        "max_tokens": limits.max_tokens,
        "max_cost_usd": limits.max_cost_usd,
        "max_wall_time_s": limits.max_wall_time_s,
    }


def _terminal_payload(
    *,
    status: str,
    reason_code: str,
    run_id: str,
    output_root: Path,
    package_manifest_sha256: str,
    repository: dict[str, Any] | None,
    environment: dict[str, Any] | None,
    credential: dict[str, Any] | None,
    budget: dict[str, Any] | None,
    error_type: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "section": "P4-0E-R1_NETWORK_BUDGET_AND_IDENTITY_CLOSURE",
        "status": status,
        "reason_code": reason_code,
        "run_id": run_id,
        "artifact_root": str(output_root),
        "package_manifest_sha256": package_manifest_sha256,
        "repository": repository,
        "environment": environment,
        "credential": credential,
        "budget": budget,
        "error_type": error_type,
        "secret_values_persisted": False,
        "dotenv_contents_persisted": False,
        "private_reasoning_persisted": False,
        "hidden_exposed_to_model": False,
        "raw_provider_error_persisted": False,
    }
    return {key: value for key, value in payload.items() if value is not None}


def run_network_smoke(
    *,
    output: str | os.PathLike[str],
    invocation_cwd: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    expected_head: str,
    run_id: str,
    package_manifest_sha256: str,
    model: str | None = None,
    family: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    expected_branch: str = "stage2-general-feedback",
    max_wall_time_s: float = 300.0,
    selection=None,
    budget: BudgetManager | None = None,
) -> dict[str, Any]:
    if _RUN_ID_RE.fullmatch(run_id or "") is None:
        raise ValueError("run_id must be a safe non-empty identifier")
    package_sha = _clean_sha(
        "package_manifest_sha256",
        package_manifest_sha256,
        _SHA256_RE,
    )
    output_root = Path(output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "p4_0e_r1_network_evidence.json"

    environment_evidence = load_invocation_dotenv(invocation_cwd)
    environment_dict = environment_evidence.to_dict()
    repo_identity: dict[str, Any] | None = None
    credential: dict[str, Any] | None = None
    budget_evidence: dict[str, Any] | None = None

    try:
        repo_identity = repository_identity(
            repository_root,
            expected_head=expected_head,
            expected_branch=expected_branch,
        )
    except Exception as exc:
        payload = _terminal_payload(
            status="blocked",
            reason_code="repository_identity_invalid",
            run_id=run_id,
            output_root=output_root,
            package_manifest_sha256=package_sha,
            repository=None,
            environment=environment_dict,
            credential=None,
            budget=None,
            error_type=type(exc).__name__,
        )
        _write_artifact(output_path, payload)
        raise NetworkEvidenceClosureError(
            "authoritative repository identity validation failed"
        ) from exc

    repo = Path(repo_identity["root"])
    sample_path, sample_identity = _sample_identity(repo)
    sample = sample_path.read_text(encoding="utf-8")

    selected = selection or resolve_model_runtime(
        model,
        family=family,
        base_url=base_url,
        api_key_env=api_key_env,
        reasoning_effort="auto",
    )
    config = selected.effective_config
    credential = credential_presence_evidence(config.api_key_env)
    if not credential["credential_present"]:
        payload = _terminal_payload(
            status="blocked",
            reason_code="selected_credential_missing",
            run_id=run_id,
            output_root=output_root,
            package_manifest_sha256=package_sha,
            repository=repo_identity,
            environment=environment_dict,
            credential=credential,
            budget=None,
        )
        _write_artifact(output_path, payload)
        raise NetworkEvidenceClosureError(
            "selected credential is missing before provider launch"
        )

    manager = budget or BudgetManager(
        BudgetLimits(max_llm_calls=1, max_wall_time_s=max_wall_time_s)
    )
    before = manager.snapshot()
    budget_evidence = {
        "manager": "agrefactor.runtime.BudgetManager",
        "shared_single_instance": True,
        "shared_across_precheck_call_and_observation": True,
        "limits": _budget_limits_dict(manager.limits),
        "requested_increment": {"llm_calls": 1},
        "prospective_check_before_provider": True,
        "prospective_check_passed": False,
        "physical_provider_calls": 0,
        "exact_once_llm_accounting": False,
        "usage_before": before.to_dict(),
    }
    try:
        manager.ensure_available(llm_calls=1)
        budget_evidence["prospective_check_passed"] = True
    except BudgetExceededError as exc:
        budget_evidence.update(
            {
                "blocked_resource": exc.resource,
                "blocked_limit": exc.limit,
                "blocked_attempted": exc.attempted,
                "usage_after": manager.snapshot().to_dict(),
            }
        )
        payload = _terminal_payload(
            status="blocked",
            reason_code="llm_budget_blocked_prelaunch",
            run_id=run_id,
            output_root=output_root,
            package_manifest_sha256=package_sha,
            repository=repo_identity,
            environment=environment_dict,
            credential=credential,
            budget=budget_evidence,
            error_type=type(exc).__name__,
        )
        _write_artifact(output_path, payload)
        raise NetworkEvidenceClosureError(
            "shared BudgetManager blocked provider launch"
        ) from exc

    parameters, policy = config.parameterize_call(
        ModelCallRole.REFACTOR_PLANNING
    )
    provider = selected.registry.get_provider(config.provider_name)

    # Consume immediately before the physical provider attempt. A provider
    # exception still represents one physically attempted LLM call.
    after_call = manager.consume(llm_calls=1)
    budget_evidence["physical_provider_calls"] = 1
    budget_evidence["usage_after_call"] = after_call.to_dict()
    budget_evidence["exact_once_llm_accounting"] = (
        after_call.llm_calls - before.llm_calls == 1
    )
    try:
        response = provider.generate(
            config.to_model_spec(),
            ModelRequest(
                messages=(
                    ChatMessage(
                        role="system",
                        content=(
                            "Read the committed C/C++ sample. Return only the "
                            "requested final token. Never expose private reasoning."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            "Committed sample SHA256: "
                            + sample_identity["sha256"]
                            + "\nSample:\n"
                            + sample
                            + "\nReply with exactly "
                            "AGREFACTOR_P4_0E_R1_NETWORK_OK."
                        ),
                    ),
                ),
                parameters=parameters,
                metadata={"model_call_policy": policy.to_dict()},
            ),
        )
    except Exception as exc:
        budget_evidence["usage_after_failure"] = manager.snapshot().to_dict()
        payload = _terminal_payload(
            status="failed",
            reason_code="provider_call_failed",
            run_id=run_id,
            output_root=output_root,
            package_manifest_sha256=package_sha,
            repository=repo_identity,
            environment=environment_dict,
            credential=credential,
            budget=budget_evidence,
            error_type=type(exc).__name__,
        )
        _write_artifact(output_path, payload)
        raise NetworkEvidenceClosureError(
            "provider call failed; raw provider error was not persisted"
        ) from exc

    after_observed = manager.record_model_usage(response.usage)
    budget_evidence["usage_after_observed"] = after_observed.to_dict()
    budget_evidence["exact_once_llm_accounting"] = (
        after_observed.llm_calls - before.llm_calls == 1
    )
    if not budget_evidence["exact_once_llm_accounting"]:
        raise NetworkEvidenceClosureError("LLM call accounting was not exact-once")

    final_text = response.text.strip()
    if (
        "AGREFACTOR_P4_0E_R1_NETWORK_OK" not in final_text
        or len(final_text) > 512
    ):
        payload = _terminal_payload(
            status="failed",
            reason_code="provider_final_contract_invalid",
            run_id=run_id,
            output_root=output_root,
            package_manifest_sha256=package_sha,
            repository=repo_identity,
            environment=environment_dict,
            credential=credential,
            budget=budget_evidence,
        )
        _write_artifact(output_path, payload)
        raise NetworkEvidenceClosureError(
            "provider final response did not satisfy bounded transport contract"
        )

    repo_after = repository_identity(
        repo,
        expected_head=expected_head,
        expected_branch=expected_branch,
    )
    if repo_after != repo_identity:
        raise NetworkEvidenceClosureError(
            "repository identity changed during provider execution"
        )

    identity_basis = {
        "schema_version": 1,
        "run_id": run_id,
        "artifact_root": str(output_root),
        "package_manifest_sha256": package_sha,
        "repository": repo_identity,
        "sample": sample_identity,
        "model": config.to_manifest(),
        "model_defaults_source": selected.defaults_source,
        "call_policy": policy.to_dict(),
        "budget_contract": {
            "manager": budget_evidence["manager"],
            "limits": budget_evidence["limits"],
            "requested_increment": budget_evidence["requested_increment"],
        },
    }
    artifact_identity_sha256 = _sha256_bytes(_canonical(identity_basis))

    payload = {
        "schema_version": 1,
        "section": "P4-0E-R1_NETWORK_BUDGET_AND_IDENTITY_CLOSURE",
        "status": "passed",
        "reason_code": "network_budget_identity_closure_passed",
        "run_id": run_id,
        "artifact_root": str(output_root),
        "artifact_identity": {
            "schema_version": 1,
            "sha256": artifact_identity_sha256,
            "basis": identity_basis,
        },
        "package_manifest_sha256": package_sha,
        "repository": repo_identity,
        "repository_after": repo_after,
        "environment": environment_dict,
        "credential": credential,
        "budget": budget_evidence,
        "model": config.to_manifest(),
        "model_defaults_source": selected.defaults_source,
        "call_policy": policy.to_dict(),
        "sample": sample_identity,
        "model_visible_inputs": [
            "committed_sample_sha256",
            "committed_sample_source",
        ],
        "hidden_boundary": {
            "hidden_exposed_to_model": False,
            "hidden_source_present": False,
            "hidden_diagnostics_present": False,
        },
        "response_sha256": _sha256_bytes(response.text.encode("utf-8")),
        "response_chars": len(response.text),
        "usage": response.usage.to_dict(),
        "provider_metadata": dict(response.metadata),
        "raw_response_persisted": False,
        "raw_provider_error_persisted": False,
        "private_reasoning_persisted": False,
        "secret_values_persisted": False,
        "dotenv_contents_persisted": False,
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    secret = os.environ.get(config.api_key_env or "")
    if isinstance(secret, str) and secret and secret in serialized:
        raise NetworkEvidenceClosureError(
            "selected credential value entered persisted evidence"
        )
    lowered = serialized.casefold()
    if any(tag in lowered for tag in ("<think", "</think", "<reasoning", "</reasoning")):
        raise NetworkEvidenceClosureError(
            "private reasoning tag entered persisted evidence"
        )
    artifact_sha256 = _write_artifact(output_path, payload)
    print(
        "P4_0E_R1_REAL_NETWORK_SMOKE_PASSED "
        f"head={repo_identity['head']} budget_llm_calls={after_observed.llm_calls} "
        f"model={config.model_id} thinking={str(policy.thinking_effective).lower()} "
        f"provider_effort={policy.effective_provider_reasoning_effort} "
        f"artifact_identity={artifact_identity_sha256} artifact_sha256={artifact_sha256} "
        "secret_free=true private_reasoning_persisted=false hidden_exposed=false"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--invocation-cwd", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-branch", default="stage2-general-feedback")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--package-manifest-sha256", required=True)
    parser.add_argument("--max-wall-time-s", type=float, default=300.0)
    parser.add_argument("--model", default=None)
    parser.add_argument("--family")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    args = parser.parse_args()
    try:
        run_network_smoke(
            output=args.output,
            invocation_cwd=args.invocation_cwd,
            repository_root=args.repository_root,
            expected_head=args.expected_head,
            expected_branch=args.expected_branch,
            run_id=args.run_id,
            package_manifest_sha256=args.package_manifest_sha256,
            max_wall_time_s=args.max_wall_time_s,
            model=args.model,
            family=args.family,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
        )
    except NetworkEvidenceClosureError as exc:
        raise SystemExit(
            "P4_0E_R1_REAL_NETWORK_SMOKE_FAILED "
            f"error_type={type(exc).__name__}"
        ) from exc


if __name__ == "__main__":
    main()
