#!/usr/bin/env python3
"""Acquire bounded R5 history episodes through the existing refactor/R2/R4 path."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.campaign import ExistingRefactorR5CampaignExecutor, R5Arm
from agrefactor.campaign.r5_protocol import (
    COMMON_BASELINE_PROVIDER_CAP,
    COMMON_BASELINE_VITIS_CAP,
    MUTATION_ARM_VITIS_CAP,
)
from agrefactor.cli import build_parser
from agrefactor.models import resolve_model_runtime
from agrefactor.product import R5CommonBaselineCapture, run_source_command_with_r5_capture
from agrefactor.recovery import (
    CalibrationCertificate,
    ProviderBackedShadowDiagnosticAdvisor,
    R4CanaryManifest,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
    ShadowReserve,
)
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.runtime.r4_integration import CandidateModelR4MutationAdapter
from agrefactor.runtime.r5_integration import (
    ExistingOrchestratorR5Integration,
    R5CandidatePromptFactory,
    R5IntegrationConfig,
)
from agrefactor.runtime.r5_profile import resolve_r5_profile


DEFAULT_ADAPTER_MANIFEST = Path(
    "/data/agrefactor_runs/r5_p2_adapter_freeze_v2/oracle_adapter_manifest.json"
)
DEFAULT_PROTOCOL_AUDIT = Path(
    "/data/agrefactor_runs/"
    "r5_p2_oracle_protocol_audit_v3_budget_corrected_6e523d6/"
    "protocol_audit.json"
)
DEFAULT_CALIBRATION_BUNDLE = Path(
    "/data/agrefactor_runs/v23_r2_real_calibration_identity_v2_20260918/"
    "calibration_bundle.json"
)
STATE_PATH = Path("docs/roadmap/V2_3_STATE.json")
HISTORY_PROVIDER_CAP = 66
HISTORY_VITIS_CAP = 48
PER_ATTEMPT_PROVIDER_CAP = COMMON_BASELINE_PROVIDER_CAP + 2
PER_ATTEMPT_VITIS_CAP = COMMON_BASELINE_VITIS_CAP + MUTATION_ARM_VITIS_CAP


class HistoryAcquisitionError(RuntimeError):
    """Raised when history evidence cannot be acquired without weakening R5."""


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run_namespace(output: Path, manifest_sha256: str) -> str:
    """Isolate ephemeral work roots across immutable campaign attempts."""

    return _canonical_sha256(
        {
            "output_root": str(output.expanduser().resolve()),
            "manifest_sha256": manifest_sha256,
        }
    )[:12]


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HistoryAcquisitionError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
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
        raise HistoryAcquisitionError("git command failed: " + " ".join(args))
    return completed.stdout.strip()


def _load_certificate(bundle: Mapping[str, Any]) -> CalibrationCertificate:
    raw = bundle.get("certificate")
    if not isinstance(raw, Mapping):
        raise HistoryAcquisitionError("calibration bundle lacks a certificate")
    value = dict(raw)
    value.pop("schema_version", None)
    certificate_id = value.pop("certificate_id", "")
    certificate = CalibrationCertificate(
        certificate_id=str(certificate_id),
        **value,
    )
    if not certificate.accepted:
        raise HistoryAcquisitionError("calibration certificate is not accepted")
    if certificate.failure_class_scope != ("unsupported_construct",):
        raise HistoryAcquisitionError("calibration scope is not the frozen R5 scope")
    return certificate


def _history_cases(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list):
        raise HistoryAcquisitionError("adapter manifest cases are missing")
    cases = tuple(
        dict(item)
        for item in raw_cases
        if isinstance(item, Mapping) and item.get("period") == "history"
    )
    if len(cases) != 2 or len({item.get("case_id") for item in cases}) != 2:
        raise HistoryAcquisitionError("R5 history split must contain two unique cases")
    for item in cases:
        if (
            item.get("control_role") != "positive"
            or item.get("expected_r2_failure_class") != "unsupported_construct"
            or item.get("expected_r2_entry_boundary") != "unknown_or_mixed_review"
            or item.get("deterministic_repair_expected") is not False
            or item.get("outcome_observed") is not False
        ):
            raise HistoryAcquisitionError("history case violates the frozen v2 role")
    return tuple(sorted(cases, key=lambda item: str(item["case_id"])))


def _validate_repository(repo: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    head = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "branch", "--show-current")
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise HistoryAcquisitionError("history acquisition requires the clean R5 branch")
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
    ):
        raise HistoryAcquisitionError("roadmap state does not permit history acquisition")
    if (
        state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
    ):
        raise HistoryAcquisitionError("R5 global budget contract is incompatible")
    for field, cap_field in (
        ("R5_CONSUMED_PROVIDER_CALLS", "R5_PROVIDER_CALL_HARD_CAP"),
        ("R5_CONSUMED_VITIS_LAUNCHES", "R5_VITIS_LAUNCH_HARD_CAP"),
    ):
        used = state.get(field)
        cap = state.get(cap_field)
        if (
            isinstance(used, bool)
            or not isinstance(used, int)
            or used < 0
            or used > cap
        ):
            raise HistoryAcquisitionError("R5 global budget ledger is invalid")
    return {"head": head, "branch": branch, "clean": True}


def load_preflight_contracts(
    *,
    repo: Path,
    adapter_manifest_path: Path,
    protocol_audit_path: Path,
    calibration_bundle_path: Path,
) -> dict[str, Any]:
    state = _load_json(repo / STATE_PATH)
    repository = _validate_repository(repo, state)
    adapter = _load_json(adapter_manifest_path)
    audit = _load_json(protocol_audit_path)
    bundle = _load_json(calibration_bundle_path)
    certificate = _load_certificate(bundle)

    if audit.get("status") != "ready_for_history_acquisition":
        raise HistoryAcquisitionError("zero-call protocol audit does not admit history")
    if audit.get("next_step") != "history_acquisition_through_existing_refactor_r4_path":
        raise HistoryAcquisitionError("protocol audit authorizes a different next step")
    if audit.get("adapter_manifest_file_sha256") != _file_sha256(adapter_manifest_path):
        raise HistoryAcquisitionError("adapter manifest file hash mismatch")
    if audit.get("adapter_manifest_sha256") != adapter.get("manifest_sha256"):
        raise HistoryAcquisitionError("adapter manifest identity mismatch")
    if adapter.get("repository_commit") != repository["head"] and subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            str(adapter.get("repository_commit")),
            repository["head"],
        ],
        check=False,
    ).returncode != 0:
        raise HistoryAcquisitionError("frozen adapter commit is not an ancestor")
    if state.get("R2_TO_R4_CALIBRATION_CERTIFICATE_ID") != certificate.certificate_id:
        raise HistoryAcquisitionError("calibration certificate differs from roadmap state")
    if state.get("R2_TO_R4_CALIBRATION_BUNDLE_SHA256") != _file_sha256(
        calibration_bundle_path
    ):
        raise HistoryAcquisitionError("calibration bundle hash mismatch")

    cases = _history_cases(adapter)
    for item in cases:
        paths = item.get("paths")
        hashes = item.get("hashes")
        if not isinstance(paths, Mapping) or not isinstance(hashes, Mapping):
            raise HistoryAcquisitionError("history case paths or hashes are missing")
        for role in ("source", "public_test", "hidden_test"):
            path = (repo / str(paths[role])).resolve()
            if not path.is_file() or _file_sha256(path) != hashes.get(role):
                raise HistoryAcquisitionError(
                    f"history {item['case_id']} {role} hash mismatch"
                )

    runtime = bundle.get("model_runtime")
    if not isinstance(runtime, Mapping) or not isinstance(
        runtime.get("request_parameters"), Mapping
    ):
        raise HistoryAcquisitionError("calibration model runtime is incomplete")
    if not os.environ.get(str(runtime.get("api_key_env", ""))):
        raise HistoryAcquisitionError("selected Provider credential is missing")
    return {
        "state": state,
        "repository": repository,
        "adapter": adapter,
        "audit": audit,
        "bundle": bundle,
        "certificate": certificate,
        "cases": cases,
        "model_runtime": dict(runtime),
        "file_hashes": {
            "adapter_manifest": _file_sha256(adapter_manifest_path),
            "protocol_audit": _file_sha256(protocol_audit_path),
            "calibration_bundle": _file_sha256(calibration_bundle_path),
        },
    }


def build_history_manifest(
    contracts: Mapping[str, Any], *, max_attempts_per_case: int
) -> dict[str, Any]:
    if max_attempts_per_case < 1 or max_attempts_per_case > 3:
        raise HistoryAcquisitionError("max attempts per case must be in 1..3")
    cases = contracts["cases"]
    provider_bound = len(cases) * max_attempts_per_case * PER_ATTEMPT_PROVIDER_CAP
    vitis_bound = len(cases) * max_attempts_per_case * PER_ATTEMPT_VITIS_CAP
    if provider_bound > HISTORY_PROVIDER_CAP or vitis_bound > HISTORY_VITIS_CAP:
        raise HistoryAcquisitionError("history acquisition exceeds its frozen sub-budget")
    prior_provider = int(contracts["state"]["R5_CONSUMED_PROVIDER_CALLS"])
    prior_vitis = int(contracts["state"]["R5_CONSUMED_VITIS_LAUNCHES"])
    if (
        prior_provider + provider_bound
        > int(contracts["state"]["R5_PROVIDER_CALL_HARD_CAP"])
        or prior_vitis + vitis_bound
        > int(contracts["state"]["R5_VITIS_LAUNCH_HARD_CAP"])
    ):
        raise HistoryAcquisitionError("history acquisition exceeds the global R5 budget")
    payload = {
        "schema_version": 1,
        "manifest_id": "v2.3-r5-history-acquisition-v1",
        "status": "frozen_before_history_outcome_observation",
        "repository_head": contracts["repository"]["head"],
        "adapter_manifest_sha256": contracts["file_hashes"]["adapter_manifest"],
        "protocol_audit_sha256": contracts["file_hashes"]["protocol_audit"],
        "calibration_bundle_sha256": contracts["file_hashes"]["calibration_bundle"],
        "calibration_certificate_id": contracts["certificate"].certificate_id,
        "history_case_ids": [str(item["case_id"]) for item in cases],
        "history_source_sha256": [str(item["hashes"]["source"]) for item in cases],
        "arm": "A2",
        "entrypoint": "refactor",
        "max_attempts_per_case": max_attempts_per_case,
        "provider_call_upper_bound": provider_bound,
        "vitis_launch_upper_bound": vitis_bound,
        "history_provider_cap": HISTORY_PROVIDER_CAP,
        "history_vitis_cap": HISTORY_VITIS_CAP,
        "provider_calls_before": prior_provider,
        "vitis_launches_before": prior_vitis,
        "future_outcomes_observed": False,
        "future_files_executed": False,
        "trusted_revision_creation_allowed": False,
        "r5_real_campaign_allowed": False,
    }
    payload["manifest_sha256"] = _canonical_sha256(payload)
    return payload


def _baseline_args(
    *,
    repo: Path,
    case: Mapping[str, Any],
    runtime: Mapping[str, Any],
    output: Path,
    run_id: str,
) -> argparse.Namespace:
    paths = case["paths"]
    argv = [
        "refactor",
        str((repo / str(paths["source"])).resolve()),
        "--top",
        str(case["top"]),
        "--model",
        str(runtime["model"]),
        "--model-family",
        str(runtime["family"]),
        "--base-url",
        str(runtime["base_url"]),
        "--api-key-env",
        str(runtime["api_key_env"]),
        "--public-test",
        str((repo / str(paths["public_test"])).resolve()),
        "--hidden-test",
        str((repo / str(paths["hidden_test"])).resolve()),
        "--max-testbench-repairs",
        "1",
        "--max-candidate-repairs",
        "1",
        "--max-llm-calls",
        str(COMMON_BASELINE_PROVIDER_CAP),
        "--max-tool-calls",
        "40",
        "--max-compile-calls",
        "40",
        "--max-csim-calls",
        "10",
        "--max-csynth-calls",
        "10",
        "--max-cosim-calls",
        "10",
        "--max-wall-time-s",
        "7200",
        "--output-dir",
        str(output),
        "--run-id",
        run_id,
        "--json",
    ]
    return build_parser().parse_args(argv)


def _single_event(capture: R5CommonBaselineCapture) -> dict[str, Any]:
    events = tuple(
        dict(item)
        for item in capture.formal_result.metadata.get("diagnostic_events", ())
        if isinstance(item, Mapping)
    )
    if len(events) != 1:
        raise HistoryAcquisitionError("history baseline did not produce one diagnostic event")
    return events[0]


def build_execution_identity_and_canary(
    *, capture: R5CommonBaselineCapture, case_id: str, certificate: CalibrationCertificate
) -> tuple[dict[str, Any], R4CanaryManifest]:
    event = _single_event(capture)
    target = event.get("target_identity")
    toolchain = event.get("toolchain_identity")
    if not isinstance(target, Mapping) or not isinstance(toolchain, Mapping):
        raise HistoryAcquisitionError("diagnostic identity is incomplete")
    source_sha = _text_sha256(capture.formal_request.original_code)
    prompt_sha = capture.execution_identity.get("request_identity_sha256")
    if not isinstance(prompt_sha, str) or len(prompt_sha) != 64:
        raise HistoryAcquisitionError("baseline request identity is incomplete")
    identity = {
        "run_id": event["run_id"],
        "case_id": case_id,
        "stage": event["stage"],
        "identity_complete": True,
        "hidden_input_count": 0,
        "secret_present": False,
        "private_reasoning_present": False,
        "source_sha256": source_sha,
        "target_identity": str(target["fingerprint"]),
        "toolchain_identity": str(toolchain["fingerprint"]),
        "parser_identity": certificate.strict_parser,
        "model_identity": capture.model_adapter.model_spec.model,
        "prompt_sha256": prompt_sha,
    }
    provisional = R4CanaryManifest(
        manifest_id=f"r5-history-{case_id}",
        manifest_sha256="0" * 64,
        enabled=True,
        operator_enabled=True,
        case_ids=(case_id,),
        source_sha256=source_sha,
        target_identity=identity["target_identity"],
        toolchain_identity=identity["toolchain_identity"],
        parser_identity=identity["parser_identity"],
        model_identity=identity["model_identity"],
        prompt_sha256=identity["prompt_sha256"],
        allowed_stage=str(event["stage"]),
        expires_at="2027-01-01T00:00:00Z",
    )
    canary = replace(
        provisional,
        manifest_sha256=canonical_artifact_sha256(provisional.to_dict()),
    )
    return identity, canary


def _advisor_factory(runtime_selection):
    def factory(capture, arm, context):
        del capture, arm
        return ProviderBackedShadowDiagnosticAdvisor(
            provider=runtime_selection.registry.get_provider(
                runtime_selection.effective_config.provider_name
            ),
            model=runtime_selection.effective_config.to_model_spec(),
            budget=context.budget,
            request_parameters=runtime_selection.effective_config.parameters,
            reserve=ShadowReserve(
                max_calls=1,
                max_tokens=8192,
                max_cost_usd=10.0,
                max_wall_time_s=600,
            ),
            trace=context.trace,
        )

    return factory


def _integration_factory(
    *,
    case_id: str,
    certificate: CalibrationCertificate,
    manifest_sha256: str,
):
    def factory(capture, arm, context, episode_root, model_adapter):
        if arm is not R5Arm.A2:
            raise HistoryAcquisitionError("history acquisition permits only A2")
        identity, canary = build_execution_identity_and_canary(
            capture=capture,
            case_id=case_id,
            certificate=certificate,
        )
        mutation = CandidateModelR4MutationAdapter(
            model_adapter=model_adapter,
            prompt_factory=R5CandidatePromptFactory(
                request=capture.formal_request,
                approved_memory_snippets=(),
            ),
            budget=context.budget,
        )
        return ExistingOrchestratorR5Integration(
            R5IntegrationConfig(
                profile=resolve_r5_profile("A2"),
                canary=canary,
                execution_identity=identity,
                calibration_certificate=certificate,
                episode_ledger_root=str(episode_root),
                campaign_manifest_sha256=manifest_sha256,
                mutation_adapter=mutation,
                validation_wall_time_s=1200.0,
            )
        )

    return factory


def verify_positive_observation(
    observation: Mapping[str, Any], *, source_sha256: str, manifest_sha256: str
) -> R5EpisodeEnvelope:
    if observation.get("status") != R5EpisodeOutcome.VERIFIED_POSITIVE.value:
        raise HistoryAcquisitionError("history A2 did not produce verified_positive")
    integration = observation.get("integration")
    if not isinstance(integration, Mapping):
        raise HistoryAcquisitionError("history A2 integration evidence is missing")
    path = integration.get("episode_path")
    if not isinstance(path, str) or not Path(path).is_file():
        raise HistoryAcquisitionError("history episode file is missing")
    episode = R5EpisodeEnvelope.from_dict(_load_json(Path(path)))
    if (
        episode.outcome is not R5EpisodeOutcome.VERIFIED_POSITIVE
        or episode.source_sha256 != source_sha256
        or episode.manifest_sha256 != manifest_sha256
        or episode.agent_safe_summary.get("failure_family")
        != "unsupported_construct"
        or episode.agent_safe_summary.get("owner") != "candidate"
        or episode.agent_safe_summary.get("false_repair") is not False
        or episode.agent_safe_summary.get("unsafe_scope") is not False
        or episode.agent_safe_summary.get("critical_safety_violation") is not False
    ):
        raise HistoryAcquisitionError("history episode failed independent invariants")
    if integration.get("main_result_unchanged") is not True:
        raise HistoryAcquisitionError("A2 changed the ordinary refactor verdict")
    return episode


def _write_attempt(
    path: Path,
    *,
    case_id: str,
    attempt: int,
    baseline: Mapping[str, Any] | None,
    observation: Mapping[str, Any] | None,
    error: str | None,
) -> None:
    _atomic_json(
        path,
        {
            "schema_version": 1,
            "case_id": case_id,
            "attempt": attempt,
            "baseline": None if baseline is None else dict(baseline),
            "observation": None if observation is None else dict(observation),
            "error": error,
            "raw_provider_response_persisted": False,
            "private_reasoning_persisted": False,
            "future_outcome_observed": False,
        },
    )


def _read_product_budget_usage(product_root: Path) -> tuple[int, int] | None:
    """Read authoritative partial usage written by the existing entrypoint."""

    path = product_root / "run_result.json"
    if not path.is_file():
        return None
    try:
        raw = _load_json(path).get("budget_usage")
        if not isinstance(raw, Mapping):
            return None
        provider = int(raw["llm_calls"])
        vitis = sum(
            int(raw[name])
            for name in ("csim_calls", "csynth_calls", "cosim_calls")
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if provider < 0 or vitis < 0:
        return None
    return provider, vitis


def acquire_history(
    *,
    repo: Path,
    output: Path,
    contracts: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_value = contracts["model_runtime"]
    runtime_selection = resolve_model_runtime(
        str(runtime_value["model"]),
        family=str(runtime_value["family"]),
        base_url=str(runtime_value["base_url"]),
        api_key_env=str(runtime_value["api_key_env"]),
        reasoning_effort="auto",
        parameters=dict(runtime_value["request_parameters"]),
    )
    max_attempts = int(manifest["max_attempts_per_case"])
    usage = {"provider_calls": 0, "vitis_launches": 0}
    positives: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    run_namespace = _run_namespace(output, str(manifest["manifest_sha256"]))
    for case in contracts["cases"]:
        case_id = str(case["case_id"])
        success = False
        for attempt in range(1, max_attempts + 1):
            if (
                usage["provider_calls"] + PER_ATTEMPT_PROVIDER_CAP
                > int(manifest["provider_call_upper_bound"])
                or usage["vitis_launches"] + PER_ATTEMPT_VITIS_CAP
                > int(manifest["vitis_launch_upper_bound"])
            ):
                raise HistoryAcquisitionError("history sub-budget exhausted")
            attempt_root = output / "attempts" / case_id / f"attempt-{attempt:02d}"
            product_root = attempt_root / "product"
            baseline_manifest = None
            observation = None
            charged = False
            attempt_provider: int | None = None
            attempt_vitis: int | None = None
            arm_started = False
            try:
                args = _baseline_args(
                    repo=repo,
                    case=case,
                    runtime=runtime_value,
                    output=product_root,
                    run_id=(
                        f"r5-history-{case_id}-{run_namespace}-{attempt:02d}"
                    ),
                )
                execution = run_source_command_with_r5_capture(args)
                capture = execution.r5_common_baseline
                if not isinstance(capture, R5CommonBaselineCapture):
                    raise HistoryAcquisitionError("ordinary refactor capture is missing")
                executor = ExistingRefactorR5CampaignExecutor(
                    artifact_root=attempt_root / "paired",
                    baseline_runner=lambda *unused: capture,
                    advisor_factory=_advisor_factory(runtime_selection),
                    integration_factory=_integration_factory(
                        case_id=case_id,
                        certificate=contracts["certificate"],
                        manifest_sha256=str(manifest["manifest_sha256"]),
                    ),
                )
                case_spec, baseline_manifest = executor.adopt_history_common_baseline(
                    case_id=case_id,
                    source_sha256=str(case["hashes"]["source"]),
                    repeat=attempt,
                    capture=capture,
                )
                attempt_provider = int(baseline_manifest["provider_calls"])
                attempt_vitis = int(baseline_manifest["vitis_launches"])
                arm_started = True
                observation = executor.run_arm(
                    case=case_spec,
                    repeat=attempt,
                    arm=R5Arm.A2,
                    baseline=baseline_manifest,
                    arm_index=0,
                )
                attempt_provider += int(observation["provider_calls"])
                attempt_vitis += int(observation["vitis_launches"])
                usage["provider_calls"] += attempt_provider
                usage["vitis_launches"] += attempt_vitis
                charged = True
                episode = verify_positive_observation(
                    observation,
                    source_sha256=_text_sha256(capture.formal_request.original_code),
                    manifest_sha256=str(manifest["manifest_sha256"]),
                )
                positives.append(
                    {
                        "case_id": case_id,
                        "attempt": attempt,
                        "source_sha256": episode.source_sha256,
                        "context_signature": episode.context_signature,
                        "episode_id": episode.episode_id,
                        "episode_sha256": episode.envelope_sha256,
                        "episode_path": observation["integration"]["episode_path"],
                    }
                )
                _write_attempt(
                    attempt_root / "attempt_result.json",
                    case_id=case_id,
                    attempt=attempt,
                    baseline=baseline_manifest,
                    observation=observation,
                    error=None,
                )
                attempts.append({"case_id": case_id, "attempt": attempt, "status": "verified_positive"})
                success = True
                break
            except Exception as exc:
                if not charged:
                    if attempt_provider is not None and attempt_vitis is not None:
                        usage["provider_calls"] += attempt_provider + (
                            2 if arm_started else 0
                        )
                        usage["vitis_launches"] += attempt_vitis + (
                            MUTATION_ARM_VITIS_CAP if arm_started else 0
                        )
                    else:
                        observed = _read_product_budget_usage(product_root)
                        if observed is None:
                            observed = (
                                COMMON_BASELINE_PROVIDER_CAP,
                                COMMON_BASELINE_VITIS_CAP,
                            )
                        usage["provider_calls"] += observed[0]
                        usage["vitis_launches"] += observed[1]
                reason = type(exc).__name__ + ":" + str(exc)
                _write_attempt(
                    attempt_root / "attempt_result.json",
                    case_id=case_id,
                    attempt=attempt,
                    baseline=baseline_manifest,
                    observation=observation,
                    error=reason,
                )
                attempts.append({"case_id": case_id, "attempt": attempt, "status": "failed_closed", "reason": reason})
                _atomic_json(output / "budget_ledger.json", {**usage, "accounting": "actual_or_conservative_upper_bound"})
            _atomic_json(output / "budget_ledger.json", {**usage, "accounting": "actual_or_conservative_upper_bound"})
        if not success:
            raise HistoryAcquisitionError(f"history case did not yield verified evidence: {case_id}")

    if (
        len(positives) != 2
        or len({item["source_sha256"] for item in positives}) != 2
        or len({item["context_signature"] for item in positives}) != 2
    ):
        raise HistoryAcquisitionError("history positives are not independent")
    result = {
        "schema_version": 1,
        "status": "ready_for_independent_history_audit",
        "manifest_sha256": manifest["manifest_sha256"],
        "positives": positives,
        "attempts": attempts,
        "usage": usage,
        "provider_calls_within_cap": usage["provider_calls"] <= HISTORY_PROVIDER_CAP,
        "vitis_launches_within_cap": usage["vitis_launches"] <= HISTORY_VITIS_CAP,
        "future_outcomes_observed": False,
        "future_files_executed": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    _atomic_json(output / "budget_ledger.json", {**usage, "accounting": "actual_or_conservative_upper_bound"})
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adapter-manifest", type=Path, default=DEFAULT_ADAPTER_MANIFEST)
    parser.add_argument("--protocol-audit", type=Path, default=DEFAULT_PROTOCOL_AUDIT)
    parser.add_argument("--calibration-bundle", type=Path, default=DEFAULT_CALIBRATION_BUNDLE)
    parser.add_argument("--max-attempts-per-case", type=int, default=3)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise HistoryAcquisitionError("output directory must not already exist")
    output.mkdir(parents=True)
    contracts = load_preflight_contracts(
        repo=repo,
        adapter_manifest_path=args.adapter_manifest.expanduser().resolve(),
        protocol_audit_path=args.protocol_audit.expanduser().resolve(),
        calibration_bundle_path=args.calibration_bundle.expanduser().resolve(),
    )
    manifest = build_history_manifest(
        contracts,
        max_attempts_per_case=args.max_attempts_per_case,
    )
    _atomic_json(output / "history_acquisition_manifest.json", manifest)
    print("R5_HISTORY_PREFLIGHT=passed")
    print(f"HISTORY_PROVIDER_UPPER_BOUND={manifest['provider_call_upper_bound']}")
    print(f"HISTORY_VITIS_UPPER_BOUND={manifest['vitis_launch_upper_bound']}")
    if args.preflight_only:
        print("R5_HISTORY_STATUS=ready_for_real_history_acquisition")
        print("PROVIDER_CALLS=0")
        print("VITIS_LAUNCHES=0")
        return 0

    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result = acquire_history(
        repo=repo,
        output=output,
        contracts=contracts,
        manifest=manifest,
    )
    result["started_at"] = started
    result["completed_at"] = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    result["result_sha256"] = _canonical_sha256(result)
    _atomic_json(output / "history_acquisition_result.json", result)
    print("R5_HISTORY_STATUS=" + str(result["status"]))
    print(f"PROVIDER_CALLS={result['usage']['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['usage']['vitis_launches']}")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
