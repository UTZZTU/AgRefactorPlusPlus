"""Stable, secret-free execution identity for reproducible AgRefactor++ runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

EXECUTION_IDENTITY_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "access_token",
    }
)
_HARD_USAGE_FIELDS = {
    "max_llm_calls": "llm_calls",
    "max_tool_calls": "tool_calls",
    "max_compile_calls": "compile_calls",
    "max_csim_calls": "csim_calls",
    "max_csynth_calls": "csynth_calls",
    "max_wall_time_s": "elapsed_s",
}


def canonical_json_sha256(value: Any) -> str:
    """Hash one finite JSON value using a stable canonical encoding."""

    normalized = _copy_json(value, "canonical value")
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"identity file not found: {resolved}")
    return sha256(resolved.read_bytes()).hexdigest()


def build_execution_identity_bundle(
    *,
    run_id: str,
    source_path: str | os.PathLike[str],
    top_function: str,
    normalized_task: Mapping[str, Any],
    model_manifest: Mapping[str, Any],
    prompt_hashes: Mapping[str, str],
    target_manifest: Mapping[str, Any],
    suite_manifests: Sequence[Mapping[str, Any]],
    initial_candidate_path: str | os.PathLike[str] | None,
    final_candidate_path: str | os.PathLike[str] | None,
    budget_contract: Mapping[str, Any],
    budget_usage: Mapping[str, Any] | None,
    artifact_schema_version: int,
    execution_status: str,
    repository_root: str | os.PathLike[str] | None = None,
    toolchain_evidence_root: str | os.PathLike[str] | None = None,
    hard_budget_exhaustion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the operator-full identity bundle and deterministic cache identity."""

    cleaned_run_id = _required_text(run_id, "run_id")
    cleaned_top = _required_text(top_function, "top_function")
    cleaned_status = _required_text(execution_status, "execution_status")
    if (
        isinstance(artifact_schema_version, bool)
        or not isinstance(artifact_schema_version, int)
        or artifact_schema_version < 1
    ):
        raise ValueError("artifact_schema_version must be a positive integer")

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source file not found: {source}")
    source_identity = {
        "path": str(source),
        "sha256": file_sha256(source),
        "size_bytes": source.stat().st_size,
        "top_function": cleaned_top,
    }

    task_value = _copy_mapping(normalized_task, "normalized_task")
    model_value = _copy_mapping(model_manifest, "model_manifest")
    target_value = _copy_mapping(target_manifest, "target_manifest")
    budget_value = _copy_mapping(budget_contract, "budget_contract")
    usage_value = (
        None
        if budget_usage is None
        else _copy_mapping(budget_usage, "budget_usage")
    )
    prompt_value = _normalize_prompt_hashes(prompt_hashes)
    suite_value = [
        _suite_identity(item)
        for item in suite_manifests
    ]
    suite_value.sort(key=lambda item: (item["split"], item["suite_id"]))

    candidates = {
        "initial": _candidate_identity(initial_candidate_path),
        "final": _candidate_identity(final_candidate_path),
    }
    toolchain = _toolchain_identity(
        target_value,
        toolchain_evidence_root,
    )
    repository = _repository_identity(repository_root)
    budget = _budget_identity(
        budget_value,
        usage_value,
        hard_budget_exhaustion,
    )
    pricing = _pricing_identity(model_value)

    task_cache = dict(task_value)
    task_cache.pop("task_id", None)
    task_cache.pop("kernel_path", None)
    task_cache.pop("testbench_path", None)
    task_cache.pop("test_suites", None)
    suite_cache = [
        {
            key: value
            for key, value in item.items()
            if key not in {
                "testbench_path",
                "operator_artifact_path",
                "trajectory_id",
            }
        }
        for item in suite_value
    ]
    toolchain_cache = {
        "requested_profile": toolchain["requested_profile"],
        "fingerprint_sha256": toolchain["fingerprint_sha256"],
        "actual_version_recorded": toolchain[
            "actual_version_recorded"
        ],
    }
    repository_cache = {
        "status": repository["status"],
        "commit": repository.get("commit"),
        "clean": repository.get("clean"),
        "dirty_state_sha256": repository.get("dirty_state_sha256"),
    }
    cache_material = {
        "schema_version": EXECUTION_IDENTITY_SCHEMA_VERSION,
        "source": source_identity,
        "normalized_task": task_cache,
        "model": model_value,
        "prompt_hashes": prompt_value,
        "target": target_value,
        "toolchain": toolchain_cache,
        "suites": suite_cache,
        "budget_contract": budget_value,
        "pricing": pricing,
        "repository": repository_cache,
        "artifact_schema_version": artifact_schema_version,
    }
    request_identity_sha256 = canonical_json_sha256(cache_material)
    cache_identity_sha256 = request_identity_sha256

    required_fields_present = all(
        (
            source_identity["sha256"],
            cleaned_top,
            task_value,
            model_value,
            target_value,
            budget_value,
        )
    ) and bool(prompt_value)
    accepted_ready = bool(
        required_fields_present
        and suite_value
        and candidates["initial"] is not None
        and candidates["final"] is not None
        and usage_value is not None
        and toolchain["actual_version_recorded"]
    )

    bundle: dict[str, Any] = {
        "schema_version": EXECUTION_IDENTITY_SCHEMA_VERSION,
        "evidence_view": "operator_full",
        "execution_id": None,
        "run_id": cleaned_run_id,
        "task_id": task_value.get("task_id"),
        "execution_status": cleaned_status,
        "request_identity_sha256": request_identity_sha256,
        "cache_identity_sha256": cache_identity_sha256,
        "source": source_identity,
        "normalized_task": {
            "sha256": canonical_json_sha256(task_value),
            "value": task_value,
        },
        "model": {
            "sha256": canonical_json_sha256(model_value),
            "value": model_value,
            "pricing": pricing,
        },
        "prompt_hashes": prompt_value,
        "target": {
            "sha256": canonical_json_sha256(target_value),
            "value": target_value,
            "toolchain": toolchain,
        },
        "suites": suite_value,
        "candidates": candidates,
        "budget": budget,
        "repository": repository,
        "artifact_schema_version": artifact_schema_version,
        "completeness": {
            "required_non_sensitive_fields_present": (
                required_fields_present
            ),
            "actual_toolchain_version_recorded": toolchain[
                "actual_version_recorded"
            ],
            "initial_candidate_recorded": (
                candidates["initial"] is not None
            ),
            "final_candidate_recorded": (
                candidates["final"] is not None
            ),
            "budget_usage_recorded": usage_value is not None,
            "accepted_ready": accepted_ready,
        },
    }
    bundle["execution_id"] = _execution_id(bundle)
    _reject_secret_keys(bundle, "execution_identity")
    bundle["bundle_sha256"] = canonical_json_sha256(bundle)
    return bundle


def validate_execution_identity_bundle(
    bundle: Mapping[str, Any],
    *,
    require_accepted_ready: bool = False,
) -> None:
    value = _copy_mapping(bundle, "execution_identity")
    if value.get("schema_version") != EXECUTION_IDENTITY_SCHEMA_VERSION:
        raise ValueError("unsupported execution identity schema_version")
    declared = value.pop("bundle_sha256", None)
    if not isinstance(declared, str) or _SHA256_RE.fullmatch(declared) is None:
        raise ValueError("execution identity bundle_sha256 is missing or invalid")
    if canonical_json_sha256(value) != declared:
        raise ValueError("execution identity bundle_sha256 does not match")
    _reject_secret_keys(value, "execution_identity")
    completeness = value.get("completeness")
    if not isinstance(completeness, Mapping):
        raise ValueError("execution identity completeness is missing")
    if require_accepted_ready and not completeness.get("accepted_ready", False):
        raise ValueError(
            "accepted execution identity is incomplete: actual toolchain, "
            "suite, or candidate identity is missing"
        )


def finalize_execution_identity_bundle(
    bundle: Mapping[str, Any],
    *,
    budget_usage: Mapping[str, Any] | None,
    execution_status: str,
    hard_budget_exhaustion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh final runtime fields without changing the request/cache identity."""

    value = _copy_mapping(bundle, "execution_identity")
    validate_execution_identity_bundle(value)
    value.pop("bundle_sha256", None)
    value["execution_status"] = _required_text(
        execution_status,
        "execution_status",
    )
    budget = value.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("execution identity budget contract is missing")
    contract = budget.get("contract")
    if not isinstance(contract, Mapping):
        raise ValueError("execution identity budget.contract is missing")
    value["budget"] = _budget_identity(
        contract,
        budget_usage,
        hard_budget_exhaustion,
    )
    value["execution_id"] = _execution_id(value)
    value["bundle_sha256"] = canonical_json_sha256(value)
    validate_execution_identity_bundle(value)
    return value


def _execution_id(value: Mapping[str, Any]) -> str:
    candidates = value.get("candidates") or {}
    if not isinstance(candidates, Mapping):
        candidates = {}
    initial = candidates.get("initial") or {}
    final = candidates.get("final") or {}
    if not isinstance(initial, Mapping):
        initial = {}
    if not isinstance(final, Mapping):
        final = {}
    budget = value.get("budget") or {}
    usage = budget.get("usage") if isinstance(budget, Mapping) else None
    target = value.get("target") or {}
    toolchain = target.get("toolchain") if isinstance(target, Mapping) else {}
    material = {
        "run_id": value.get("run_id"),
        "cache_identity_sha256": value.get("cache_identity_sha256"),
        "execution_status": value.get("execution_status"),
        "initial_candidate_sha256": initial.get("sha256"),
        "final_candidate_sha256": final.get("sha256"),
        "budget_usage_sha256": (
            None if usage is None else canonical_json_sha256(usage)
        ),
        "toolchain_fingerprint_sha256": (
            toolchain.get("fingerprint_sha256")
            if isinstance(toolchain, Mapping)
            else None
        ),
    }
    return "exec-" + canonical_json_sha256(material)

def execution_identity_summary(
    bundle: Mapping[str, Any],
    *,
    artifact_path: str = "execution_identity.json",
) -> dict[str, Any]:
    value = _copy_mapping(bundle, "execution_identity")
    return {
        "schema_version": value.get("schema_version"),
        "execution_id": value.get("execution_id"),
        "request_identity_sha256": value.get(
            "request_identity_sha256"
        ),
        "cache_identity_sha256": value.get(
            "cache_identity_sha256"
        ),
        "bundle_sha256": value.get("bundle_sha256"),
        "artifact_path": artifact_path,
        "evidence_view": value.get("evidence_view"),
        "completeness": value.get("completeness"),
    }


def write_execution_identity_bundle(
    path: str | os.PathLike[str],
    bundle: Mapping[str, Any],
) -> None:
    validate_execution_identity_bundle(bundle)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _copy_mapping(bundle, "execution_identity")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _suite_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    suite = _copy_mapping(value, "suite_manifest")
    suite_id = _required_text(suite.get("suite_id"), "suite_id")
    split = _required_text(suite.get("split"), "split")
    source = suite.get("source") or {}
    if not isinstance(source, Mapping):
        raise TypeError("suite source must be a mapping or null")
    source_value = dict(source)
    raw_path = suite.get("testbench_path")
    path = None
    actual_hash = None
    size_bytes = None
    if isinstance(raw_path, str) and raw_path.strip():
        candidate = Path(raw_path).expanduser().resolve()
        path = str(candidate)
        if candidate.is_file():
            actual_hash = file_sha256(candidate)
            size_bytes = candidate.stat().st_size
    expected_hash = source_value.get("expected_content_sha256")
    if expected_hash is not None:
        expected_hash = str(expected_hash).lower()
        if _SHA256_RE.fullmatch(expected_hash) is None:
            raise ValueError("suite expected_content_sha256 is invalid")
    content_hash = actual_hash or expected_hash
    if content_hash is None:
        raise ValueError(f"suite {suite_id} has no resolvable content hash")
    if actual_hash is not None and expected_hash not in {None, actual_hash}:
        raise ValueError(
            f"suite {suite_id} content hash does not match declared source"
        )
    return {
        "suite_id": suite_id,
        "suite_version": suite.get("suite_version"),
        "split": split,
        "source_id": source_value.get("source_id"),
        "source_revision": source_value.get("source_revision"),
        "source_kind": source_value.get("source_kind"),
        "content_sha256": content_hash,
        "size_bytes": size_bytes,
        "testbench_path": path,
        "operator_artifact_path": source_value.get(
            "operator_artifact_path"
        ),
        "generation_model": source_value.get("generation_model"),
        "generation_profile": source_value.get("generation_profile"),
        "prompt_sha256": source_value.get("prompt_sha256"),
        "trajectory_id": source_value.get("trajectory_id"),
        "round_index": source_value.get("round_index"),
        "coverage": source_value.get("coverage"),
        "qualification_status": source_value.get(
            "qualification_status",
            "declared",
        ),
        "feedback_visibility": (
            "public"
            if split == "public"
            else "operator_only"
        ),
    }


def _candidate_identity(
    raw_path: str | os.PathLike[str] | None,
) -> dict[str, Any] | None:
    if raw_path is None:
        return None
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        return None
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _toolchain_identity(
    target_manifest: Mapping[str, Any],
    evidence_root: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    profile = target_manifest.get("profile", target_manifest)
    if not isinstance(profile, Mapping):
        profile = {}
    invocations: list[dict[str, Any]] = []
    root = None
    if evidence_root is not None:
        root = Path(evidence_root).expanduser().resolve()
    if root is not None and root.is_dir():
        for path in sorted(root.rglob("csynth_invocation.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, Mapping):
                continue
            verification = raw.get("toolchain_version_verification") or {}
            if not isinstance(verification, Mapping):
                verification = {}
            resolved_executable = raw.get("resolved_executable")
            executable_identity = _optional_file_identity(
                resolved_executable
            )
            settings_identity = _optional_file_identity(
                raw.get("resolved_settings_path")
                or raw.get("settings_path")
            )
            version_output = (
                str(verification.get("stdout") or "")
                + "\n"
                + str(verification.get("stderr") or "")
            )
            invocations.append(
                {
                    "evidence_path": str(path.relative_to(root)),
                    "profile_name": raw.get("profile_name"),
                    "requested_version": verification.get(
                        "requested",
                        raw.get("requested_toolchain_version"),
                    ),
                    "actual_version": verification.get("actual"),
                    "verification_status": verification.get("status"),
                    "executable": raw.get("executable"),
                    "resolved_executable": resolved_executable,
                    "executable_file": executable_identity,
                    "settings_path": raw.get("settings_path"),
                    "settings_file": settings_identity,
                    "command_source": raw.get("command_source"),
                    "probe_source": verification.get(
                        "probe_source",
                        raw.get("probe_source"),
                    ),
                    "version_output_sha256": sha256(
                        version_output.encode("utf-8")
                    ).hexdigest(),
                    "effective_value_provenance": raw.get(
                        "effective_value_provenance",
                        raw.get("target_profile_provenance", {}),
                    ),
                }
            )
    if not invocations:
        invocations.append(
            {
                "evidence_path": None,
                "profile_name": profile.get("name"),
                "requested_version": profile.get("toolchain_version"),
                "actual_version": None,
                "verification_status": "not_observed",
                "executable": profile.get("executable"),
                "resolved_executable": None,
                "executable_file": None,
                "settings_path": profile.get("settings_path"),
                "settings_file": None,
                "command_source": "target_manifest",
                "probe_source": None,
                "version_output_sha256": None,
                "effective_value_provenance": target_manifest.get(
                    "field_provenance",
                    {},
                ),
            }
        )
    fingerprint_material = [
        {
            key: value
            for key, value in item.items()
            if key != "evidence_path"
        }
        for item in invocations
    ]
    return {
        "requested_profile": _copy_json(profile, "target profile"),
        "actual_version_recorded": any(
            isinstance(item.get("actual_version"), str)
            and bool(item["actual_version"].strip())
            for item in invocations
        ),
        "invocations": invocations,
        "fingerprint_sha256": canonical_json_sha256(
            fingerprint_material
        ),
    }


def _budget_identity(
    contract: Mapping[str, Any],
    usage: Mapping[str, Any] | None,
    hard_exhaustion: Mapping[str, Any] | None,
) -> dict[str, Any]:
    effective = contract.get("effective_hard_limits") or {}
    if not isinstance(effective, Mapping):
        effective = {}
    remaining: dict[str, int | float | None] = {}
    for limit_name, usage_name in _HARD_USAGE_FIELDS.items():
        limit = effective.get(limit_name)
        actual = None if usage is None else usage.get(usage_name)
        if isinstance(limit, (int, float)) and isinstance(
            actual,
            (int, float),
        ):
            remaining[limit_name] = max(0, limit - actual)
        else:
            remaining[limit_name] = None

    soft = contract.get("soft_usage_budgets") or {}
    if not isinstance(soft, Mapping):
        soft = {}
    token_budget = soft.get("token_budget")
    tokens = None if usage is None else usage.get("tokens")
    token_exceeded = bool(
        isinstance(token_budget, int)
        and isinstance(tokens, int)
        and tokens > token_budget
    )
    cost_budget = soft.get("cost_budget")
    currency = soft.get("currency")
    costs = {} if usage is None else usage.get("costs_by_currency", {})
    actual_cost = None
    if isinstance(costs, Mapping) and isinstance(currency, str):
        actual_cost = costs.get(currency)
    try:
        cost_exceeded = bool(
            cost_budget is not None
            and actual_cost is not None
            and float(actual_cost) > float(cost_budget)
        )
    except (TypeError, ValueError):
        cost_exceeded = False
    return {
        "contract": _copy_json(contract, "budget contract"),
        "usage": None if usage is None else _copy_json(usage, "budget usage"),
        "remaining_hard_budget": remaining,
        "soft_budget_exceeded": {
            "tokens": token_exceeded,
            "cost": cost_exceeded,
        },
        "hard_budget_exhaustion": (
            None
            if hard_exhaustion is None
            else _copy_mapping(
                hard_exhaustion,
                "hard_budget_exhaustion",
            )
        ),
    }


def _pricing_identity(model_manifest: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = model_manifest.get("pricing_snapshot")
    if not isinstance(snapshot, Mapping):
        return {
            "snapshot_sha256": None,
            "source_status": "unavailable",
            "cost_estimation_quality": "unavailable",
            "currency": None,
        }
    status = snapshot.get("verification_status")
    return {
        "snapshot_sha256": snapshot.get("pricing_snapshot_sha256"),
        "source_status": status,
        "cost_estimation_quality": (
            "verified_snapshot"
            if status == "official_verified"
            else "unverified_snapshot"
        ),
        "currency": snapshot.get("currency"),
    }


def _repository_identity(
    repository_root: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    if repository_root is None:
        return {
            "status": "not_requested",
            "root": None,
            "commit": None,
            "clean": None,
            "dirty_state_sha256": None,
        }
    root = Path(repository_root).expanduser().resolve()
    try:
        top = _git(root, "rev-parse", "--show-toplevel").strip()
        commit = _git(root, "rev-parse", "HEAD").strip()
        status_text = _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        diff = _git(root, "diff", "--binary", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return {
            "status": "unavailable",
            "root": str(root),
            "commit": None,
            "clean": None,
            "dirty_state_sha256": None,
        }
    dirty_material = {
        "status": status_text.splitlines(),
        "diff_sha256": sha256(diff.encode("utf-8")).hexdigest(),
    }
    return {
        "status": "observed",
        "root": top,
        "commit": commit,
        "clean": not bool(status_text.strip()),
        "dirty_state_sha256": canonical_json_sha256(dirty_material),
    }


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def _optional_file_identity(value: object) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        return None
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _normalize_prompt_hashes(
    value: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("prompt_hashes must be a mapping")
    result: dict[str, str] = {}
    for raw_name, raw_digest in value.items():
        name = _required_text(raw_name, "prompt hash name")
        digest = _required_text(raw_digest, f"prompt hash {name}").lower()
        if _SHA256_RE.fullmatch(digest) is None:
            raise ValueError(f"prompt hash {name} must be SHA-256")
        result[name] = digest
    return dict(sorted(result.items()))


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            child_path = f"{path}.{raw_key}"
            if key.endswith("_env"):
                pass
            elif key in _SECRET_KEYS or any(
                key.endswith(f"_{item}")
                for item in _SECRET_KEYS
            ):
                raise ValueError(
                    "execution identity must not contain credential material: "
                    + child_path
                )
            _reject_secret_keys(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _copy_mapping(
    value: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    copied = _copy_json(dict(value), name)
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def _copy_json(value: Any, name: str) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must contain finite JSON-serializable data"
        ) from exc
