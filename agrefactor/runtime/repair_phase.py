"""Formal repair-aware refactor phase for the UnifiedRunner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from agrefactor.config import DEFAULT_COSIM_TIMEOUT_S, validate_cosim_timeout_s
from agrefactor.models import CandidateModelAdapter

from .candidate_repair_integration import (
    CandidateRepairOrchestrationRequest,
    CandidateRepairOrchestrationResult,
    CandidateRepairOrchestrationStatus,
    CandidateRepairValidationOrchestrator,
    CandidateValidationHandlerFactory,
    LocalCandidateValidationHandlerFactory,
)
from .runner import (
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
)


REPAIR_PHASE_ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CandidateRepairPhaseConfig:
    """Immutable inputs captured by one repair-aware refactor phase."""

    request: CandidateRepairOrchestrationRequest
    work_root: str | os.PathLike[str]
    artifact_root: str | os.PathLike[str]
    csynth_timelimit: int = 300
    csim_timelimit: int = 60
    cosim_timelimit: int = DEFAULT_COSIM_TIMEOUT_S
    cosim_policy: str = "required"

    def __post_init__(self) -> None:
        if not isinstance(
            self.request,
            CandidateRepairOrchestrationRequest,
        ):
            raise TypeError(
                "request must be "
                "CandidateRepairOrchestrationRequest"
            )
        object.__setattr__(
            self,
            "work_root",
            _clean_path(self.work_root, "work_root"),
        )
        object.__setattr__(
            self,
            "artifact_root",
            _clean_path(
                self.artifact_root,
                "artifact_root",
            ),
        )
        for name, value in (
            ("csynth_timelimit", self.csynth_timelimit),
            ("csim_timelimit", self.csim_timelimit),
            ("cosim_timelimit", self.cosim_timelimit),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
            ):
                raise TypeError(
                    f"{name} must be an integer"
                )
            if value <= 0:
                raise ValueError(
                    f"{name} must be positive"
                )
        object.__setattr__(
            self,
            "cosim_timelimit",
            validate_cosim_timeout_s(self.cosim_timelimit),
        )


@dataclass(frozen=True, slots=True)
class CandidateRepairPhaseArtifactWriteResult:
    root: str
    orchestration_result_path: str
    final_candidate_path: str
    repair_artifact_manifest_path: str | None
    artifact_manifest_path: str
    effective_repair_quota_path: str | None = None
    diagnostic_events_path: str | None = None
    testbench_semantic_revision_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "orchestration_result_path": (
                self.orchestration_result_path
            ),
            "final_candidate_path": (
                self.final_candidate_path
            ),
            "repair_artifact_manifest_path": (
                self.repair_artifact_manifest_path
            ),
            "artifact_manifest_path": (
                self.artifact_manifest_path
            ),
            "effective_repair_quota_path": (
                self.effective_repair_quota_path
            ),
            "diagnostic_events_path": self.diagnostic_events_path,
            "testbench_semantic_revision_path": (
                self.testbench_semantic_revision_path
            ),
        }


class CandidateRepairPhaseArtifactWriter:
    """Write one immutable, agent-safe phase bundle."""

    def __init__(
        self,
        root: str | os.PathLike[str],
    ) -> None:
        self._root = _clean_path(root, "root")

    @property
    def root(self) -> Path:
        return self._root

    def write(
        self,
        result: CandidateRepairOrchestrationResult,
    ) -> CandidateRepairPhaseArtifactWriteResult:
        if not isinstance(
            result,
            CandidateRepairOrchestrationResult,
        ):
            raise TypeError(
                "result must be "
                "CandidateRepairOrchestrationResult"
            )
        if self._root.exists():
            if not self._root.is_dir():
                raise FileExistsError(
                    "repair phase artifact root "
                    f"is not a directory: {self._root}"
                )
            if any(self._root.iterdir()):
                raise FileExistsError(
                    "repair phase artifact root "
                    f"is not empty: {self._root}"
                )
        self._root.mkdir(
            parents=True,
            exist_ok=True,
        )

        result_path = (
            self._root / "orchestration_result.json"
        )
        _atomic_json_write(
            result_path,
            {
                "schema_version": (
                    REPAIR_PHASE_ARTIFACT_SCHEMA_VERSION
                ),
                "evidence_view": "agent_safe",
                "result": result.to_dict(),
            },
        )

        candidate_path = (
            self._root / "final_candidate.cpp"
        )
        _atomic_text_write(
            candidate_path,
            result.final_candidate,
        )

        repair_manifest_path: str | None = None
        if result.repair_result is not None:
            repair_write = (
                result.write_repair_artifacts(
                    self._root / "repair_artifacts"
                )
            )
            repair_manifest_path = (
                repair_write.artifact_manifest_path
            )

        quota_path: str | None = None
        quota = result.metadata.get("effective_repair_quota")
        if isinstance(quota, Mapping):
            target = self._root / "effective_repair_quota.json"
            _atomic_json_write(target, quota)
            quota_path = str(target)

        diagnostic_path: str | None = None
        diagnostic_events = result.metadata.get("diagnostic_events")
        if isinstance(diagnostic_events, (list, tuple)):
            target = self._root / "diagnostic_events.json"
            _atomic_json_write(
                target,
                {
                    "schema_version": 1,
                    "evidence_view": "agent_safe",
                    "events": list(diagnostic_events),
                    "event_count": len(diagnostic_events),
                    "success_authority": False,
                },
            )
            diagnostic_path = str(target)

        semantic_path: str | None = None
        semantic_revision = result.metadata.get(
            "testbench_semantic_revision"
        )
        if isinstance(semantic_revision, Mapping):
            target = self._root / "testbench_semantic_revision.json"
            semantic_audit = result.metadata.get(
                "testbench_semantic_audit"
            )
            _atomic_json_write(
                target,
                {
                    "schema_version": 1,
                    "revision": dict(semantic_revision),
                    "independent_audit": (
                        dict(semantic_audit)
                        if isinstance(semantic_audit, Mapping)
                        else None
                    ),
                },
            )
            semantic_path = str(target)

        manifest_path = (
            self._root / "artifact_manifest.json"
        )
        files = _scan_files(
            self._root,
            excluded={manifest_path},
        )
        manifest = {
            "schema_version": (
                REPAIR_PHASE_ARTIFACT_SCHEMA_VERSION
            ),
            "phase": RunPhase.REFACTOR.value,
            "execution_mode": "repair_aware",
            "legacy_mode": False,
            "evidence_view": "agent_safe",
            "validation_id": result.validation_id,
            "orchestration_status": (
                result.status.value
            ),
            "accepted": result.accepted,
            "files": files,
        }
        _atomic_json_write(
            manifest_path,
            manifest,
        )
        _assert_no_temporary_files(self._root)

        return CandidateRepairPhaseArtifactWriteResult(
            root=str(self._root),
            orchestration_result_path=str(
                result_path
            ),
            final_candidate_path=str(
                candidate_path
            ),
            repair_artifact_manifest_path=(
                repair_manifest_path
            ),
            artifact_manifest_path=str(
                manifest_path
            ),
            effective_repair_quota_path=quota_path,
            diagnostic_events_path=diagnostic_path,
            testbench_semantic_revision_path=semantic_path,
        )


class CandidateRepairPhase:
    """Invoke the existing repair orchestration as one runner phase."""

    def __init__(
        self,
        *,
        model_adapter: CandidateModelAdapter,
        config: CandidateRepairPhaseConfig,
        handler_factory: (
            CandidateValidationHandlerFactory | None
        ) = None,
    ) -> None:
        if not isinstance(
            model_adapter,
            CandidateModelAdapter,
        ):
            raise TypeError(
                "model_adapter must be "
                "CandidateModelAdapter"
            )
        if not isinstance(
            config,
            CandidateRepairPhaseConfig,
        ):
            raise TypeError(
                "config must be "
                "CandidateRepairPhaseConfig"
            )
        if (
            handler_factory is not None
            and not callable(
                getattr(
                    handler_factory,
                    "build",
                    None,
                )
            )
        ):
            raise TypeError(
                "handler_factory must provide "
                "build(request)"
            )

        self._config = config
        self._handler_factory = (
            handler_factory
            or LocalCandidateValidationHandlerFactory(
                config.work_root,
                csynth_timelimit=(
                    config.csynth_timelimit
                ),
                csim_timelimit=(
                    config.csim_timelimit
                ),
                cosim_timelimit=config.cosim_timelimit,
                cosim_policy=config.cosim_policy,
            )
        )
        self._orchestrator = (
            CandidateRepairValidationOrchestrator(
                model_adapter=model_adapter,
                handler_factory=(
                    self._handler_factory
                ),
            )
        )
        self._last_result: (
            CandidateRepairOrchestrationResult
            | None
        ) = None
        self._last_artifacts: (
            CandidateRepairPhaseArtifactWriteResult
            | None
        ) = None

    @property
    def config(self) -> CandidateRepairPhaseConfig:
        return self._config

    @property
    def handler_factory(
        self,
    ) -> CandidateValidationHandlerFactory:
        return self._handler_factory

    @property
    def last_result(
        self,
    ) -> CandidateRepairOrchestrationResult | None:
        return self._last_result

    @property
    def last_artifacts(
        self,
    ) -> (
        CandidateRepairPhaseArtifactWriteResult
        | None
    ):
        return self._last_artifacts

    def __call__(
        self,
        context: RunContext,
    ) -> PhaseResult:
        if not isinstance(context, RunContext):
            raise TypeError(
                "context must be a RunContext"
            )

        validation_id = (
            f"{context.run_id}.candidate-repair"
        )
        result = self._orchestrator.run(
            context,
            self._config.request,
            validation_id=validation_id,
        )
        self._last_result = result

        phase_root = (
            Path(self._config.artifact_root)
            / RunPhase.REFACTOR.value
        )
        artifacts = (
            CandidateRepairPhaseArtifactWriter(
                phase_root
            ).write(result)
        )
        self._last_artifacts = artifacts

        status = _phase_status(result.status)
        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=status,
            summary=(
                "Repair-aware validation accepted "
                "the candidate."
                if result.accepted
                else (
                    "Repair-aware validation "
                    f"terminated with "
                    f"{result.status.value}."
                )
            ),
            metadata={
                "execution_mode": "repair_aware",
                "legacy_mode": False,
                "orchestration_status": (
                    result.status.value
                ),
                "accepted": result.accepted,
                "last_validation_state": (
                    result.last_validation_state.value
                ),
                "repair_attempt_count": (
                    result.metadata.get(
                        "repair_attempt_count",
                        0,
                    )
                ),
                "phase_artifact_manifest": (
                    "refactor/artifact_manifest.json"
                ),
                "repair_artifacts_available": (
                    result.repair_result is not None
                ),
                "shared_budget": True,
                "shared_trace": True,
            },
        )


def build_candidate_repair_phase(
    *,
    model_adapter: CandidateModelAdapter,
    request: CandidateRepairOrchestrationRequest,
    work_root: str | os.PathLike[str],
    artifact_root: str | os.PathLike[str],
    csynth_timelimit: int = 300,
    csim_timelimit: int = 60,
    cosim_timelimit: int = DEFAULT_COSIM_TIMEOUT_S,
    cosim_policy: str = "required",
    handler_factory: (
        CandidateValidationHandlerFactory | None
    ) = None,
) -> CandidateRepairPhase:
    """Build the formal repair-aware refactor handler."""

    return CandidateRepairPhase(
        model_adapter=model_adapter,
        config=CandidateRepairPhaseConfig(
            request=request,
            work_root=work_root,
            artifact_root=artifact_root,
            csynth_timelimit=csynth_timelimit,
            csim_timelimit=csim_timelimit,
            cosim_timelimit=cosim_timelimit,
            cosim_policy=cosim_policy,
        ),
        handler_factory=handler_factory,
    )


def _phase_status(
    status: CandidateRepairOrchestrationStatus,
) -> PhaseStatus:
    if (
        status
        is CandidateRepairOrchestrationStatus.ACCEPTED
    ):
        return PhaseStatus.SUCCEEDED
    if (
        status
        is CandidateRepairOrchestrationStatus.
        VALIDATOR_ERROR
    ):
        return PhaseStatus.ERROR
    return PhaseStatus.FAILED


def _clean_path(
    value: str | os.PathLike[str],
    name: str,
) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise TypeError(
            f"{name} must be path-like"
        ) from exc
    if not isinstance(raw, str):
        raise TypeError(
            f"{name} must resolve to a string"
        )
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError(
            f"{name} must not be empty"
        )
    return Path(cleaned).expanduser()


def _atomic_json_write(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    try:
        encoded = json.loads(
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "artifact payload must contain "
            "finite JSON data"
        ) from exc
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
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
                encoded,
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
            temporary.unlink(
                missing_ok=True
            )
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_text_write(
    path: Path,
    value: str,
) -> None:
    if not isinstance(value, str):
        raise TypeError(
            "artifact text must be a string"
        )
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
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
            handle.write(value)
            if value and not value.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(
                missing_ok=True
            )
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _scan_files(
    root: Path,
    *,
    excluded: set[Path],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path in excluded:
            continue
        if path.is_symlink():
            raise ValueError(
                "artifact bundles must not "
                "contain symbolic links"
            )
        data = path.read_bytes()
        relative = path.relative_to(
            root
        ).as_posix()
        records.append(
            {
                "relative_path": relative,
                "sha256": (
                    sha256(data).hexdigest()
                ),
                "size_bytes": len(data),
                "record_type": (
                    _record_type(relative)
                ),
            }
        )
    return records


def _record_type(relative: str) -> str:
    if relative == "orchestration_result.json":
        return "candidate_repair_orchestration"
    if relative == "final_candidate.cpp":
        return "validated_candidate"
    if relative.endswith(
        "artifact_manifest.json"
    ):
        return "nested_artifact_manifest"
    if relative.endswith(".json"):
        return "supporting_json"
    return "supporting_artifact"


def _assert_no_temporary_files(
    root: Path,
) -> None:
    leftovers = tuple(
        root.rglob("*.tmp")
    )
    if leftovers:
        raise RuntimeError(
            "temporary repair phase artifact "
            "files remain"
        )
