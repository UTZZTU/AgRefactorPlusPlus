"""Synchronous, fail-soft campaign runner with durable progress evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any

from agrefactor.product.refactor_eligibility import (
    EligibilityStatus,
    OriginalCsynthEvidence,
    RefactorEligibilityReport,
    assess_refactor_eligibility,
    load_original_csynth_evidence,
)


class CampaignInvariantError(RuntimeError):
    """A severe campaign-contract violation that must stop all execution."""


@dataclass(frozen=True, slots=True)
class CampaignCase:
    case_id: str
    argv: tuple[str, ...]
    cwd: Path
    timeout_s: float
    primary_sample: bool = False
    eligibility_source_path: Path | None = None
    eligibility_top_function: str | None = None
    public_test_mode: str | None = None
    original_csynth_evidence: OriginalCsynthEvidence | None = None

    def __post_init__(self) -> None:
        case_id = _safe_component(self.case_id, "case_id")
        argv = tuple(self.argv)
        if not argv or not all(
            isinstance(item, str) and item
            for item in argv
        ):
            raise CampaignInvariantError(
                "case argv must contain non-empty string arguments"
            )
        cwd = Path(self.cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise CampaignInvariantError(
                f"case cwd is not a directory: {cwd}"
            )
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or self.timeout_s <= 0
        ):
            raise CampaignInvariantError(
                "case timeout_s must be positive"
            )
        if not isinstance(self.primary_sample, bool):
            raise CampaignInvariantError(
                "primary_sample must be boolean"
            )
        source_path = self.eligibility_source_path
        top_function = self.eligibility_top_function
        public_mode = self.public_test_mode
        csynth_evidence = self.original_csynth_evidence
        if self.primary_sample:
            if source_path is None or top_function is None or public_mode is None:
                raise CampaignInvariantError(
                    "primary sample requires source_path, top_function, "
                    "and public_test_mode eligibility inputs"
                )
            source_path = Path(source_path).expanduser().resolve()
            if not source_path.is_file():
                raise CampaignInvariantError(
                    f"eligibility source does not exist: {source_path}"
                )
            if not isinstance(top_function, str) or not top_function.isidentifier():
                raise CampaignInvariantError(
                    "eligibility top_function must be an identifier"
                )
            public_mode = str(public_mode).strip().casefold()
            if public_mode not in {"auto", "provided", "none"}:
                raise CampaignInvariantError(
                    "public_test_mode must be auto, provided, or none"
                )
            if not isinstance(csynth_evidence, OriginalCsynthEvidence):
                raise CampaignInvariantError(
                    "primary sample requires typed Original CSYNTH evidence"
                )
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "timeout_s", float(self.timeout_s))
        object.__setattr__(self, "eligibility_source_path", source_path)
        object.__setattr__(self, "eligibility_top_function", top_function)
        object.__setattr__(self, "public_test_mode", public_mode)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        base_dir: Path,
        default_timeout_s: float,
    ) -> "CampaignCase":
        if not isinstance(payload, Mapping):
            raise CampaignInvariantError("campaign case must be an object")
        unknown = set(payload) - {
            "case_id",
            "argv",
            "cwd",
            "timeout_s",
            "primary_sample",
            "eligibility",
        }
        if unknown:
            raise CampaignInvariantError(
                "unknown campaign case fields: "
                + ", ".join(sorted(unknown))
            )
        raw_argv = payload.get("argv")
        if (
            not isinstance(raw_argv, Sequence)
            or isinstance(raw_argv, (str, bytes))
        ):
            raise CampaignInvariantError(
                "case argv must be a JSON array, never a shell string"
            )
        raw_cwd = payload.get("cwd", ".")
        cwd = _resolve_from(base_dir, raw_cwd)
        eligibility = payload.get("eligibility")
        eligibility_map = (
            {}
            if eligibility is None
            else _mapping(eligibility, "eligibility")
        )
        unknown_eligibility = set(eligibility_map) - {
            "source_path",
            "top_function",
            "public_test_mode",
            "original_csynth_evidence_path",
        }
        if unknown_eligibility:
            raise CampaignInvariantError(
                "unknown eligibility fields: "
                + ", ".join(sorted(unknown_eligibility))
            )
        source = eligibility_map.get("source_path")
        evidence_path = eligibility_map.get(
            "original_csynth_evidence_path"
        )
        try:
            csynth_evidence = (
                None
                if evidence_path is None
                else load_original_csynth_evidence(
                    _resolve_from(base_dir, evidence_path)
                )
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CampaignInvariantError(
                "invalid Original CSYNTH evidence: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return cls(
            case_id=payload.get("case_id"),
            argv=tuple(raw_argv),
            cwd=cwd,
            timeout_s=payload.get("timeout_s", default_timeout_s),
            primary_sample=payload.get("primary_sample", False),
            eligibility_source_path=(
                None
                if source is None
                else _resolve_from(base_dir, source)
            ),
            eligibility_top_function=eligibility_map.get(
                "top_function"
            ),
            public_test_mode=eligibility_map.get(
                "public_test_mode"
            ),
            original_csynth_evidence=csynth_evidence,
        )

    def command_identity(self) -> dict[str, Any]:
        encoded = json.dumps(
            list(self.argv),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "executable": Path(self.argv[0]).name,
            "argument_count": len(self.argv),
            "argv_sha256": sha256(encoded).hexdigest(),
            "shell": False,
        }


@dataclass(frozen=True, slots=True)
class CampaignManifest:
    campaign_id: str
    cases: tuple[CampaignCase, ...]
    heartbeat_interval_s: float = 30.0
    default_case_timeout_s: float = 3600.0
    max_wall_time_s: float | None = None

    def __post_init__(self) -> None:
        campaign_id = _safe_component(
            self.campaign_id,
            "campaign_id",
        )
        cases = tuple(self.cases)
        if not cases or not all(
            isinstance(item, CampaignCase) for item in cases
        ):
            raise CampaignInvariantError(
                "campaign requires at least one typed case"
            )
        ids = [item.case_id for item in cases]
        if len(ids) != len(set(ids)):
            raise CampaignInvariantError(
                "campaign case IDs must be unique"
            )
        heartbeat = _positive_number(
            self.heartbeat_interval_s,
            "heartbeat_interval_s",
        )
        default_timeout = _positive_number(
            self.default_case_timeout_s,
            "default_case_timeout_s",
        )
        max_wall = self.max_wall_time_s
        if max_wall is not None:
            max_wall = _positive_number(
                max_wall,
                "max_wall_time_s",
            )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "cases", cases)
        object.__setattr__(
            self,
            "heartbeat_interval_s",
            heartbeat,
        )
        object.__setattr__(
            self,
            "default_case_timeout_s",
            default_timeout,
        )
        object.__setattr__(self, "max_wall_time_s", max_wall)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        base_dir: Path,
    ) -> "CampaignManifest":
        value = _mapping(payload, "campaign manifest")
        unknown = set(value) - {
            "schema_version",
            "campaign_id",
            "heartbeat_interval_s",
            "default_case_timeout_s",
            "max_wall_time_s",
            "cases",
        }
        if unknown:
            raise CampaignInvariantError(
                "unknown campaign manifest fields: "
                + ", ".join(sorted(unknown))
            )
        if value.get("schema_version", 1) != 1:
            raise CampaignInvariantError(
                "unsupported campaign schema_version"
            )
        default_timeout = value.get(
            "default_case_timeout_s",
            3600.0,
        )
        raw_cases = value.get("cases")
        if (
            not isinstance(raw_cases, Sequence)
            or isinstance(raw_cases, (str, bytes))
        ):
            raise CampaignInvariantError(
                "campaign cases must be a JSON array"
            )
        cases = tuple(
            CampaignCase.from_dict(
                item,
                base_dir=base_dir,
                default_timeout_s=default_timeout,
            )
            for item in raw_cases
        )
        return cls(
            campaign_id=value.get("campaign_id"),
            cases=cases,
            heartbeat_interval_s=value.get(
                "heartbeat_interval_s",
                30.0,
            ),
            default_case_timeout_s=default_timeout,
            max_wall_time_s=value.get("max_wall_time_s"),
        )


@dataclass(frozen=True, slots=True)
class CampaignResult:
    campaign_id: str
    status: str
    case_results: tuple[Mapping[str, Any], ...]
    started_at: str
    finished_at: str
    elapsed_s: float
    artifact_root: str
    schema_version: int = 1

    @property
    def failed_case_count(self) -> int:
        return sum(
            item.get("status")
            not in {"passed"}
            for item in self.case_results
        )

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.case_results:
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": self.elapsed_s,
            "artifact_root": self.artifact_root,
            "case_count": len(self.case_results),
            "failed_case_count": self.failed_case_count,
            "status_counts": counts,
            "case_results": [
                _json_copy(dict(item))
                for item in self.case_results
            ],
        }


class _EvidenceWriter:
    def __init__(self, artifact_root: Path, campaign_id: str) -> None:
        self.root = artifact_root
        self.events_path = artifact_root / "campaign_events.jsonl"
        self.progress_path = artifact_root / "campaign_progress.json"
        self.sequence = 0
        self.campaign_id = campaign_id

    def event(
        self,
        event: str,
        *,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        record = {
            "schema_version": 1,
            "sequence": self.sequence,
            "timestamp": _utc_now(),
            "campaign_id": self.campaign_id,
            "event": _safe_code(event),
            "status": _safe_code(status),
            "metadata": _json_copy(dict(metadata or {})),
        }
        with self.events_path.open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def progress(self, payload: Mapping[str, Any]) -> None:
        value = {
            "schema_version": 1,
            "campaign_id": self.campaign_id,
            "last_sequence": self.sequence,
            "updated_at": _utc_now(),
            **_json_copy(dict(payload)),
        }
        _atomic_json(self.progress_path, value)


class CampaignRunner:
    """Run cases serially, continue after case failures, fail fast on invariants."""

    def __init__(
        self,
        manifest: CampaignManifest,
        *,
        artifact_root: str | os.PathLike[str],
    ) -> None:
        if not isinstance(manifest, CampaignManifest):
            raise TypeError("manifest must be CampaignManifest")
        root = Path(artifact_root).expanduser().resolve()
        if root.exists():
            if not root.is_dir():
                raise CampaignInvariantError(
                    f"artifact root is not a directory: {root}"
                )
            if any(root.iterdir()):
                raise CampaignInvariantError(
                    "campaign artifact root must be empty"
                )
        root.mkdir(parents=True, exist_ok=True)
        (root / "cases").mkdir()
        self.manifest = manifest
        self.artifact_root = root
        self.writer = _EvidenceWriter(root, manifest.campaign_id)
        self._active_process: subprocess.Popen[Any] | None = None

    def run(self) -> CampaignResult:
        started_wall = _utc_now()
        started = time.monotonic()
        results: list[dict[str, Any]] = []
        self.writer.event(
            "campaign_started",
            status="running",
            metadata={
                "case_count": len(self.manifest.cases),
                "heartbeat_interval_s": (
                    self.manifest.heartbeat_interval_s
                ),
                "max_wall_time_s": self.manifest.max_wall_time_s,
                "fail_soft_case_errors": True,
                "severe_invariant_fail_fast": True,
            },
        )
        self.writer.progress(
            self._progress_payload(
                state="running",
                results=results,
                current_case_id=None,
                started=started,
            )
        )
        try:
            for index, case in enumerate(self.manifest.cases, start=1):
                if self._campaign_timed_out(started):
                    remaining = self.manifest.cases[index - 1 :]
                    for skipped in remaining:
                        results.append(
                            self._not_launched_result(
                                skipped,
                                status="campaign_timeout",
                                reason_code="campaign_wall_time_exhausted",
                            )
                        )
                    self.writer.event(
                        "campaign_wall_time_exhausted",
                        status="failed",
                        metadata={
                            "next_case_index": index,
                            "remaining_case_count": len(remaining),
                        },
                    )
                    break
                result = self._run_case(
                    case,
                    case_index=index,
                    campaign_started=started,
                    prior_results=results,
                )
                results.append(result)
        except KeyboardInterrupt:
            self._terminate_active()
            self.writer.event(
                "campaign_interrupted",
                status="aborted",
                metadata={"completed_case_count": len(results)},
            )
            self.writer.progress(
                self._progress_payload(
                    state="aborted",
                    results=results,
                    current_case_id=None,
                    started=started,
                )
            )
            raise

        elapsed = time.monotonic() - started
        status = (
            "passed"
            if results
            and all(item["status"] == "passed" for item in results)
            else "completed_with_failures"
        )
        finished = _utc_now()
        result = CampaignResult(
            campaign_id=self.manifest.campaign_id,
            status=status,
            case_results=tuple(results),
            started_at=started_wall,
            finished_at=finished,
            elapsed_s=elapsed,
            artifact_root=str(self.artifact_root),
        )
        _atomic_json(
            self.artifact_root / "campaign_result.json",
            result.to_dict(),
        )
        self.writer.event(
            "campaign_finished",
            status=status,
            metadata={
                "case_count": len(results),
                "failed_case_count": result.failed_case_count,
                "elapsed_s": elapsed,
            },
        )
        self.writer.progress(
            self._progress_payload(
                state=status,
                results=results,
                current_case_id=None,
                started=started,
            )
        )
        return result

    def _run_case(
        self,
        case: CampaignCase,
        *,
        case_index: int,
        campaign_started: float,
        prior_results: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        case_root = (
            self.artifact_root
            / "cases"
            / f"{case_index:03d}_{case.case_id}"
        )
        case_root.mkdir()
        identity = case.command_identity()
        self.writer.event(
            "case_started",
            status="running",
            metadata={
                "case_id": case.case_id,
                "case_index": case_index,
                "command_identity": identity,
                "timeout_s": case.timeout_s,
                "primary_sample": case.primary_sample,
            },
        )
        self.writer.progress(
            self._progress_payload(
                state="running",
                results=prior_results,
                current_case_id=case.case_id,
                started=campaign_started,
            )
        )

        eligibility = self._case_eligibility(case, case_root)
        if eligibility is not None and not eligibility.primary_sample_eligible:
            status = (
                "ineligible"
                if eligibility.primary_sample_status
                is EligibilityStatus.REJECTED
                else "review_required"
            )
            result = self._not_launched_result(
                case,
                status=status,
                reason_code=(
                    eligibility.primary_sample_reason_code
                ),
                eligibility=eligibility,
            )
            _atomic_json(case_root / "case_result.json", result)
            self.writer.event(
                "case_not_launched",
                status=status,
                metadata={
                    "case_id": case.case_id,
                    "reason_code": result["reason_code"],
                    "provider_call_observed": False,
                    "tool_launch_observed": False,
                },
            )
            return result

        stdout_path = case_root / "stdout.log"
        stderr_path = case_root / "stderr.log"
        started = time.monotonic()
        timed_out = False
        returncode: int | None = None
        heartbeat_count = 0
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    list(case.argv),
                    cwd=case.cwd,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    start_new_session=(os.name == "posix"),
                )
            except (OSError, ValueError) as exc:
                result = {
                    **self._not_launched_result(
                        case,
                        status="launch_error",
                        reason_code="case_process_launch_failed",
                        eligibility=eligibility,
                    ),
                    "error_type": type(exc).__name__,
                }
                _atomic_json(case_root / "case_result.json", result)
                self.writer.event(
                    "case_finished",
                    status="launch_error",
                    metadata={
                        "case_id": case.case_id,
                        "reason_code": result["reason_code"],
                        "error_type": type(exc).__name__,
                    },
                )
                return result
            self._active_process = process
            self.writer.event(
                "case_process_launched",
                status="running",
                metadata={
                    "case_id": case.case_id,
                    "pid": process.pid,
                    "command_identity": identity,
                },
            )

            next_heartbeat = (
                time.monotonic() + self.manifest.heartbeat_interval_s
            )
            while process.poll() is None:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= case.timeout_s:
                    timed_out = True
                    self._terminate_process(process)
                    break
                if self._campaign_timed_out(campaign_started):
                    timed_out = True
                    self._terminate_process(process)
                    break
                if now >= next_heartbeat:
                    heartbeat_count += 1
                    self.writer.event(
                        "heartbeat",
                        status="running",
                        metadata={
                            "case_id": case.case_id,
                            "case_index": case_index,
                            "case_elapsed_s": elapsed,
                            "campaign_elapsed_s": (
                                now - campaign_started
                            ),
                            "heartbeat_count": heartbeat_count,
                            "completed_case_count": len(prior_results),
                        },
                    )
                    self.writer.progress(
                        self._progress_payload(
                            state="running",
                            results=prior_results,
                            current_case_id=case.case_id,
                            started=campaign_started,
                            case_elapsed_s=elapsed,
                            heartbeat_count=heartbeat_count,
                        )
                    )
                    next_heartbeat = (
                        now + self.manifest.heartbeat_interval_s
                    )
                time.sleep(
                    min(
                        0.05,
                        max(
                            self.manifest.heartbeat_interval_s / 4.0,
                            0.005,
                        ),
                    )
                )
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._terminate_process(process)
                returncode = process.wait(timeout=5)
            finally:
                self._active_process = None

        elapsed = time.monotonic() - started
        if timed_out:
            status = "timeout"
            reason = "case_timeout"
        elif returncode == 0:
            status = "passed"
            reason = "case_completed"
        else:
            status = "failed"
            reason = "case_nonzero_exit"

        result = {
            "schema_version": 1,
            "case_id": case.case_id,
            "status": status,
            "reason_code": reason,
            "returncode": returncode,
            "timed_out": timed_out,
            "tool_launch_observed": True,
            "heartbeat_count": heartbeat_count,
            "elapsed_s": elapsed,
            "command_identity": identity,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "eligibility": (
                None
                if eligibility is None
                else eligibility.to_dict()
            ),
        }
        _atomic_json(case_root / "case_result.json", result)
        self.writer.event(
            "case_finished",
            status=status,
            metadata={
                "case_id": case.case_id,
                "reason_code": reason,
                "returncode": returncode,
                "timed_out": timed_out,
                "heartbeat_count": heartbeat_count,
                "elapsed_s": elapsed,
            },
        )
        return result

    def _case_eligibility(
        self,
        case: CampaignCase,
        case_root: Path,
    ) -> RefactorEligibilityReport | None:
        if not case.primary_sample:
            return None
        assert case.eligibility_source_path is not None
        assert case.eligibility_top_function is not None
        assert case.public_test_mode is not None
        report = assess_refactor_eligibility(
            source_code=case.eligibility_source_path.read_text(
                encoding="utf-8"
            ),
            top_function=case.eligibility_top_function,
            public_test_mode=case.public_test_mode,
            original_csynth_evidence=case.original_csynth_evidence,
        )
        _atomic_json(case_root / "eligibility.json", report.to_dict())
        self.writer.event(
            "case_eligibility_evaluated",
            status=report.primary_sample_status.value,
            metadata={
                "case_id": case.case_id,
                "execution_status": report.execution_status.value,
                "primary_sample_status": (
                    report.primary_sample_status.value
                ),
                "reason_codes": list(report.reason_codes),
                "source_sha256": report.source_sha256,
                "top_function": report.top_function,
            },
        )
        return report

    def _not_launched_result(
        self,
        case: CampaignCase,
        *,
        status: str,
        reason_code: str,
        eligibility: RefactorEligibilityReport | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "case_id": case.case_id,
            "status": _safe_code(status),
            "reason_code": _safe_code(reason_code),
            "returncode": None,
            "timed_out": False,
            "tool_launch_observed": False,
            "heartbeat_count": 0,
            "elapsed_s": 0.0,
            "command_identity": case.command_identity(),
            "stdout_path": None,
            "stderr_path": None,
            "eligibility": (
                None
                if eligibility is None
                else eligibility.to_dict()
            ),
        }

    def _progress_payload(
        self,
        *,
        state: str,
        results: Sequence[Mapping[str, Any]],
        current_case_id: str | None,
        started: float,
        case_elapsed_s: float | None = None,
        heartbeat_count: int | None = None,
    ) -> dict[str, Any]:
        return {
            "state": _safe_code(state),
            "current_case_id": current_case_id,
            "completed_case_count": len(results),
            "total_case_count": len(self.manifest.cases),
            "campaign_elapsed_s": time.monotonic() - started,
            "case_elapsed_s": case_elapsed_s,
            "heartbeat_count": heartbeat_count,
            "status_counts": _status_counts(results),
        }

    def _campaign_timed_out(self, started: float) -> bool:
        limit = self.manifest.max_wall_time_s
        return (
            limit is not None
            and time.monotonic() - started >= limit
        )

    def _terminate_active(self) -> None:
        if self._active_process is not None:
            self._terminate_process(self._active_process)
            self._active_process = None

    @staticmethod
    def _terminate_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass


def load_campaign_manifest(
    path: str | os.PathLike[str],
) -> CampaignManifest:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"campaign manifest not found: {manifest_path}"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return CampaignManifest.from_dict(
        payload,
        base_dir=manifest_path.parent,
    )


def _resolve_from(base_dir: Path, value: Any) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise CampaignInvariantError("path value must be path-like")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignInvariantError(f"{name} must be an object")
    return value


def _positive_number(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
    ):
        raise CampaignInvariantError(f"{name} must be positive")
    return float(value)


def _safe_component(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise CampaignInvariantError(f"{name} must be a string")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > 120
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in cleaned
        )
    ):
        raise CampaignInvariantError(
            f"{name} contains an unsafe character"
        )
    return cleaned


def _safe_code(value: Any) -> str:
    if not isinstance(value, str):
        raise CampaignInvariantError("status/event code must be a string")
    cleaned = value.strip().casefold()
    if (
        not cleaned
        or len(cleaned) > 120
        or not cleaned[0].isalnum()
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyz0123456789_.:-"
            for character in cleaned
        )
    ):
        raise CampaignInvariantError(
            f"unsafe status/event code: {value!r}"
        )
    return cleaned


def _status_counts(
    results: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    value = _json_copy(dict(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                value,
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
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
