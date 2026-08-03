"""Product adapters for the accepted Stage 3 safe optimizer.

S3.7 exposes normal ``optimize`` and ``full`` execution without creating a
second optimizer. Baseline qualification, Structural/Bottleneck/Pragma model
integration, candidate qualification, PPA comparison, rollback and
``best_correct`` all reuse the accepted S3.1--S3.6 contracts.

Direct ``optimize`` requires an independent reference source plus explicit
Public and Hidden suites. The product never treats the input as its own oracle,
invents Hidden evidence, silently falls back to refactor, or uses source-string
heuristics as optimization gates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TargetProfile,
    TestSourceKind,
    TestSourceSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import FeedbackRouteAction, FeedbackRouter
from agrefactor.evidence import FeedbackReport
from agrefactor.models import (
    CandidateModelAdapter,
    EffectiveModelConfig,
    ModelRegistry,
)
from agrefactor.optimization import (
    BottleneckModelArtifactWriter,
    BoundedOptimizeCandidateRecoveryCoordinator,
    BoundedRecoveryOptimizerStateMachine,
    BottleneckModelCandidateExecutor,
    BottleneckModelCandidateGenerator,
    BottleneckModelHypothesisProvider,
    BudgetIncrement,
    CandidateQualificationRequest,
    CandidateExecutionResult,
    CandidateQualificationResult,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    LevelDispatchCandidateExecutor,
    LevelDispatchHypothesisProvider,
    OptimizationLevel,
    OptimizeRecoveryEvidence,
    OptimizeRecoveryStage,
    OptimizeRecoveryValidationRequest,
    OptimizerArtifactStore,
    OptimizerCheckpointWriter,
    OptimizerRunCounters,
    OptimizerState,
    OptimizerTerminalStatus,
    PragmaModelArtifactWriter,
    PragmaModelCandidateExecutor,
    PragmaModelCandidateGenerator,
    PragmaModelHypothesisProvider,
    QualificationEvidenceCache,
    QualificationStage,
    QualificationStatus,
    QualificationStepOutcome,
    SafeOptimizerPolicy,
    Stage3QualificationOrchestrator,
    StructuralModelArtifactWriter,
    StructuralModelCandidateExecutor,
    StructuralModelCandidateGenerator,
    StructuralModelHypothesisProvider,
    ValidationCacheIdentity,
    empty_optimize_recovery_summary,
    build_toolchain_fingerprint,
    initialize_qualified_baseline,
    suite_identity_from_file,
)
from agrefactor.runtime import (
    BudgetManager,
    PhaseResult,
    PhaseStatus,
    RunArtifactWriter,
    RunContext,
    RunPhase,
    build_execution_identity_bundle,
    execution_identity_summary,
    file_sha256,
    validate_execution_identity_bundle,
    write_execution_identity_bundle,
)
from agrefactor.runtime.budget_profile import EffectiveRunBudget


PRODUCT_OPTIMIZER_SCHEMA_VERSION = 1


class UnifiedStage3ModelArtifactWriter(
    BottleneckModelArtifactWriter,
    PragmaModelArtifactWriter,
):
    """Single append sequence supporting all three accepted model levels."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True)
    )
    if not isinstance(result, dict):
        raise TypeError("value must normalize to a JSON object")
    return result


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    copied = _json_copy(payload)
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
            json.dump(copied, handle, ensure_ascii=False, indent=2, sort_keys=True)
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


def _regular_code(path: str | os.PathLike[str], label: str) -> tuple[Path, str]:
    resolved = Path(path).expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise FileNotFoundError(f"{label} not found or unsafe: {resolved}")
    code = resolved.read_text(encoding="utf-8")
    if not code.strip():
        raise ValueError(f"{label} must not be empty: {resolved}")
    return resolved, code


def _stage3_model_call_summary(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {
            "path": str(path),
            "record_count": 0,
            "valid_record_count": 0,
            "invalid_record_count": 0,
            "call_kind_counts": {},
            "error_code_counts": {},
            "error_reason_code_counts": {},
            "records_sha256": None,
            "pricing": {
                "cost_estimation_quality": "unavailable",
                "actual_estimation": {
                    "quality": "unavailable",
                    "amounts_by_currency": {},
                    "is_invoice": False,
                },
                "is_invoice": False,
            },
        }
    records: list[dict[str, Any]] = []
    amounts: dict[str, Decimal] = {}
    qualities: set[str] = set()
    call_kind_counts: dict[str, int] = {}
    error_code_counts: dict[str, int] = {}
    error_reason_code_counts: dict[str, int] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"model_calls.jsonl line {line_number} is invalid JSON") from exc
        if not isinstance(value, Mapping) or value.get("sequence") != line_number:
            raise ValueError("model-call records must have contiguous sequence numbers")
        record = dict(value)
        records.append(record)
        call_kind = record.get("call_kind")
        if isinstance(call_kind, str) and call_kind:
            call_kind_counts[call_kind] = call_kind_counts.get(call_kind, 0) + 1
        error_code = record.get("error_code")
        if isinstance(error_code, str) and error_code:
            error_code_counts[error_code] = error_code_counts.get(error_code, 0) + 1
        reason_codes = record.get("error_reason_codes", [])
        if isinstance(reason_codes, list):
            for reason_code in reason_codes:
                if isinstance(reason_code, str) and reason_code:
                    error_reason_code_counts[reason_code] = (
                        error_reason_code_counts.get(reason_code, 0) + 1
                    )
        usage = record.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        estimate = usage_map.get("estimated_cost")
        estimate_map = estimate if isinstance(estimate, Mapping) else {}
        quality = estimate_map.get("quality")
        if isinstance(quality, str) and quality:
            qualities.add(quality)
        currency = estimate_map.get("currency")
        amount = estimate_map.get("amount")
        if isinstance(currency, str) and isinstance(amount, (str, int, float)):
            try:
                number = Decimal(str(amount))
            except Exception:
                continue
            if number.is_finite() and number >= 0:
                amounts[currency] = amounts.get(currency, Decimal("0")) + number
    if not records:
        quality = "unavailable"
    elif qualities == {"verified"}:
        quality = "verified"
    elif amounts:
        quality = "partial"
    else:
        quality = "unavailable"
    return {
        "path": str(path),
        "record_count": len(records),
        "valid_record_count": sum(
            1 for record in records if record.get("response_valid") is True
        ),
        "invalid_record_count": sum(
            1 for record in records if record.get("response_valid") is not True
        ),
        "call_kind_counts": dict(sorted(call_kind_counts.items())),
        "error_code_counts": dict(sorted(error_code_counts.items())),
        "error_reason_code_counts": dict(sorted(error_reason_code_counts.items())),
        "records_sha256": file_sha256(path),
        "pricing": {
            "cost_estimation_quality": quality,
            "actual_estimation": {
                "quality": quality,
                "amounts_by_currency": {
                    currency: "0" if value == 0 else format(value.normalize(), "f")
                    for currency, value in sorted(amounts.items())
                },
                "is_invoice": False,
            },
            "is_invoice": False,
        },
    }


@dataclass(frozen=True, slots=True)
class AcceptedOptimizationMaterial:
    """Independent material required to qualify and optimize a baseline."""

    baseline_source_path: Path
    reference_source_path: Path
    top_function: str
    reference_top_function: str
    target: TargetProfile
    suites: tuple[TestSuiteSpec, ...]
    suite_codes: Mapping[str, str]
    preflight_suite_id: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    schema_version = PRODUCT_OPTIMIZER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        baseline, _ = _regular_code(self.baseline_source_path, "baseline source")
        reference, _ = _regular_code(self.reference_source_path, "reference source")
        if baseline == reference:
            raise ValueError("baseline and reference source paths must be independent")
        top = str(self.top_function).strip()
        reference_top = str(self.reference_top_function).strip()
        if not top or not reference_top:
            raise ValueError("top functions must not be empty")
        if not isinstance(self.target, TargetProfile):
            raise TypeError("target must be TargetProfile")
        if self.target.device is None or not str(self.target.device).strip():
            raise ValueError("optimization target requires a concrete device")
        if self.target.clock_period_ns is None or self.target.clock_period_ns <= 0:
            raise ValueError("optimization target requires a positive clock period")
        suites = tuple(self.suites)
        if not suites or not all(isinstance(item, TestSuiteSpec) for item in suites):
            raise ValueError("optimization material requires typed suites")
        ids = [item.suite_id for item in suites]
        if len(ids) != len(set(ids)):
            raise ValueError("suite ids must be unique")
        if not any(item.split is EvaluationSplit.PUBLIC for item in suites):
            raise ValueError("optimization material requires a Public suite")
        if not any(item.split is EvaluationSplit.HIDDEN for item in suites):
            raise ValueError("optimization material requires a Hidden suite")
        codes = dict(self.suite_codes)
        if set(codes) != set(ids):
            raise ValueError("suite_codes must match all declared suites exactly")
        suite_paths: dict[str, Path] = {}
        suite_digests: dict[str, str] = {}
        for suite in suites:
            code = codes[suite.suite_id]
            if not isinstance(code, str) or not code.strip():
                raise ValueError(f"suite code is empty: {suite.suite_id}")
            if suite.testbench_path is None:
                raise ValueError(f"suite requires a testbench path: {suite.suite_id}")
            path, persisted = _regular_code(suite.testbench_path, "testbench")
            if path in suite_paths.values():
                raise ValueError("testbench paths must be unique across suites")
            suite_paths[suite.suite_id] = path
            suite_digests[suite.suite_id] = file_sha256(path)
            if persisted != code:
                raise ValueError(f"suite code does not match persisted testbench: {suite.suite_id}")
            if suite.source is not None:
                expected = suite.source.expected_content_sha256
                if expected is not None and expected != file_sha256(path):
                    raise ValueError(f"suite source digest mismatch: {suite.suite_id}")
        public_digests = {
            suite_digests[item.suite_id]
            for item in suites if item.split is EvaluationSplit.PUBLIC
        }
        hidden_digests = {
            suite_digests[item.suite_id]
            for item in suites if item.split is EvaluationSplit.HIDDEN
        }
        if public_digests & hidden_digests:
            raise ValueError("Public and Hidden suites must have independent content")
        if self.preflight_suite_id not in codes:
            raise ValueError("preflight_suite_id must reference a declared suite")
        preflight = next(item for item in suites if item.suite_id == self.preflight_suite_id)
        if preflight.split is not EvaluationSplit.PUBLIC:
            raise ValueError("preflight suite must be Public")
        object.__setattr__(self, "baseline_source_path", baseline)
        object.__setattr__(self, "reference_source_path", reference)
        object.__setattr__(self, "top_function", top)
        object.__setattr__(self, "reference_top_function", reference_top)
        object.__setattr__(self, "suites", suites)
        object.__setattr__(self, "suite_codes", codes)
        object.__setattr__(self, "provenance", _json_copy(self.provenance))

    @property
    def baseline_code(self) -> str:
        return self.baseline_source_path.read_text(encoding="utf-8")

    @property
    def reference_code(self) -> str:
        return self.reference_source_path.read_text(encoding="utf-8")

    @property
    def preflight_code(self) -> str:
        return self.suite_codes[self.preflight_suite_id]

    @property
    def task(self) -> TaskSpec:
        preflight = next(
            item for item in self.suites if item.suite_id == self.preflight_suite_id
        )
        return TaskSpec(
            task_id=f"product-optimize-{file_sha256(self.baseline_source_path)[:16]}",
            kernel_path=str(self.baseline_source_path),
            kernel_name=self.top_function,
            target=self.target,
            mode=RunMode.OPTIMIZE,
            testbench_path=preflight.testbench_path,
            test_suites=self.suites,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "baseline_source_path": str(self.baseline_source_path),
            "baseline_source_sha256": file_sha256(self.baseline_source_path),
            "reference_source_path": str(self.reference_source_path),
            "reference_source_sha256": file_sha256(self.reference_source_path),
            "top_function": self.top_function,
            "reference_top_function": self.reference_top_function,
            "target": self.target.to_effective_dict(),
            "preflight_suite_id": self.preflight_suite_id,
            "suites": [item.to_dict() for item in self.suites],
            "provenance": dict(self.provenance),
        }


def build_direct_optimization_material(
    *,
    source_path: str | os.PathLike[str],
    top_function: str,
    reference_source_path: str | os.PathLike[str] | None,
    reference_top_function: str | None,
    public_test_paths: Sequence[str | os.PathLike[str]],
    hidden_test_paths: Sequence[str | os.PathLike[str]],
    target: TargetProfile,
) -> AcceptedOptimizationMaterial:
    """Build direct-optimize material without inventing missing evidence."""

    baseline, _ = _regular_code(source_path, "optimization baseline")
    if reference_source_path is None:
        raise ValueError(
            "direct optimize requires --reference-source; the product will not "
            "treat the optimization input as its own correctness oracle"
        )
    reference, _ = _regular_code(reference_source_path, "reference source")
    if baseline == reference or file_sha256(baseline) == file_sha256(reference):
        raise ValueError(
            "direct optimize requires an independent reference source; "
            "the baseline cannot be its own correctness oracle"
        )
    reference_top = (
        reference_top_function.strip()
        if isinstance(reference_top_function, str) and reference_top_function.strip()
        else str(top_function).strip()
    )
    if not public_test_paths or not hidden_test_paths:
        raise ValueError(
            "direct optimize requires at least one provided Public and one "
            "provided Hidden testbench; auto-generated suites require full mode"
        )
    suites: list[TestSuiteSpec] = []
    codes: dict[str, str] = {}
    for split, paths in (
        (EvaluationSplit.PUBLIC, public_test_paths),
        (EvaluationSplit.HIDDEN, hidden_test_paths),
    ):
        for index, raw_path in enumerate(paths, 1):
            path, code = _regular_code(raw_path, f"{split.value} testbench")
            digest = file_sha256(path)
            suite_id = f"{split.value}-{index}"
            source = TestSourceSpec(
                source_id=f"provided-{split.value}-{digest[:16]}",
                source_revision="1",
                source_kind=TestSourceKind.PROVIDED,
                expected_content_sha256=digest,
                operator_artifact_path=str(path),
            )
            suites.append(
                TestSuiteSpec(
                    suite_id=suite_id,
                    suite_version="1",
                    split=split,
                    testbench_path=str(path),
                    source=source,
                )
            )
            codes[suite_id] = code
    return AcceptedOptimizationMaterial(
        baseline_source_path=baseline,
        reference_source_path=reference,
        top_function=top_function,
        reference_top_function=reference_top,
        target=target,
        suites=tuple(suites),
        suite_codes=codes,
        preflight_suite_id="public-1",
        provenance={
            "kind": "direct_optimize",
            "reference_required": True,
            "auto_suite_generation_allowed": False,
        },
    )


def _observe_toolchain(target: TargetProfile) -> dict[str, Any]:
    from flow.tools.csynth import probe_csynth_version, resolve_csynth_command

    resolution = resolve_csynth_command(target)
    verification = probe_csynth_version(resolution, target.toolchain_version)
    if verification.get("status") not in {"matched", "detected"}:
        raise RuntimeError(
            "Vitis toolchain observation failed: " + str(verification.get("status"))
        )
    version_text = str(verification.get("stdout") or "") + "\n" + str(
        verification.get("stderr") or ""
    )
    executable = resolution.get("resolved_executable")
    settings = resolution.get("resolved_settings_path")
    target_payload = target.to_effective_dict()
    return {
        "schema_version": 1,
        "profile_name": target.name,
        "requested_version": target.toolchain_version,
        "actual_version": verification.get("actual"),
        "verification_status": verification.get("status"),
        "command_source": resolution.get("command_source"),
        "probe_source": resolution.get("probe_source"),
        "resolved_executable": executable,
        "resolved_executable_sha256": (
            file_sha256(executable)
            if isinstance(executable, str) and Path(executable).is_file()
            else None
        ),
        "resolved_settings_path": settings,
        "resolved_settings_sha256": (
            file_sha256(settings)
            if isinstance(settings, str) and Path(settings).is_file()
            else None
        ),
        "version_output_sha256": sha256(version_text.encode("utf-8")).hexdigest(),
        "parser_profile": target.parser_profile,
        "effective_target_sha256": sha256(
            json.dumps(
                target_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


class ProductQualificationAdapter:
    """Real qualification adapter shared by all three optimization levels."""

    name = "stage3-product-qualification-adapter"
    uses_vitis = True

    def __init__(
        self,
        *,
        context: RunContext,
        material: AcceptedOptimizationMaterial,
        work_root: Path,
        artifact_root: Path,
        csim_timeout_s: int,
        csynth_timeout_s: int,
        toolchain_manifest: Mapping[str, Any],
    ) -> None:
        if not isinstance(context, RunContext):
            raise TypeError("context must be RunContext")
        if not isinstance(material, AcceptedOptimizationMaterial):
            raise TypeError("material must be AcceptedOptimizationMaterial")
        self._context = context
        self._material = material
        self._work_root = Path(work_root)
        self._artifact_root = Path(artifact_root)
        self._csim_timeout_s = int(csim_timeout_s)
        self._csynth_timeout_s = int(csynth_timeout_s)
        if self._csim_timeout_s <= 0 or self._csynth_timeout_s <= 0:
            raise ValueError("qualification timeouts must be positive")
        self._toolchain_manifest = _json_copy(toolchain_manifest)
        self._toolchain_fingerprint = build_toolchain_fingerprint(
            self._toolchain_manifest
        )
        self._cache = QualificationEvidenceCache(
            self._artifact_root / "validation_cache"
        )
        self._suite_identities = tuple(
            suite_identity_from_file(
                suite_id=suite.suite_id,
                split=suite.split.value,
                path=suite.testbench_path or "",
                suite_version=suite.suite_version,
                source_identity=None if suite.source is None else suite.source.to_dict(),
            )
            for suite in material.suites
        )
        self._recovery_router = FeedbackRouter()
        self._recovery_reports: dict[
            str,
            dict[QualificationStage, tuple[FeedbackReport, Any]],
        ] = {}

    def qualify_baseline(self, candidate: CandidateRecord) -> CandidateQualificationResult:
        if not isinstance(candidate, CandidateRecord):
            raise TypeError("candidate must be CandidateRecord")
        return self._qualify_candidate(
            candidate=candidate,
            source_path=self._material.baseline_source_path,
            qualification_id=f"{self._context.run_id}.baseline",
        )

    def qualify(self, request, source: bytes) -> CandidateQualificationResult:
        if not isinstance(source, bytes) or not source:
            raise ValueError("generated source must be non-empty bytes")
        candidate_root = self._work_root / request.candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        source_path = candidate_root / "source.cpp"
        source_path.write_bytes(source)
        candidate = CandidateRecord(
            candidate_id=request.candidate_id,
            sequence=request.sequence,
            parent_candidate_id=request.parent_candidate.candidate_id,
            hypothesis_id=request.hypothesis.hypothesis_id,
            level=request.level,
            source_sha256=sha256(source).hexdigest(),
            source_artifact=f"candidates/{request.candidate_id}/source.cpp",
            status=CandidateStatus.GENERATED,
            budget_before=request.budget_before,
            created_at_utc=_utc_now(),
        )
        return self._qualify_candidate(
            candidate=candidate,
            source_path=source_path,
            qualification_id=f"{self._context.run_id}.{request.candidate_id}",
        )

    def _qualify_candidate(
        self,
        *,
        candidate: CandidateRecord,
        source_path: Path,
        qualification_id: str,
    ) -> CandidateQualificationResult:
        from agrefactor.runtime import (
            CsimStageInputs,
            CsimValidationStageHandler,
            CsynthStageInputs,
            CsynthValidationStageHandler,
            PreflightStageInputs,
            PreflightValidationStageHandler,
        )

        candidate_code = source_path.read_text(encoding="utf-8")
        work = self._work_root / candidate.candidate_id
        public_codes = {
            suite.suite_id: self._material.suite_codes[suite.suite_id]
            for suite in self._material.suites
            if suite.split is EvaluationSplit.PUBLIC
        }
        hidden_codes = {
            suite.suite_id: self._material.suite_codes[suite.suite_id]
            for suite in self._material.suites
            if suite.split is EvaluationSplit.HIDDEN
        }
        handlers = {
            QualificationStage.PREFLIGHT: PreflightValidationStageHandler(
                PreflightStageInputs(
                    work_dir=work / "preflight",
                    testbench_code=self._material.preflight_code,
                    original_code=self._material.reference_code,
                    candidate_code=candidate_code,
                    original_top_function=(
                        self._material.reference_top_function
                    ),
                    candidate_top_function=(
                        self._material.top_function
                    ),
                )
            ),
            QualificationStage.PUBLIC: CsimValidationStageHandler(
                CsimStageInputs(
                    work_dir=work / "public",
                    original_code=self._material.reference_code,
                    candidate_code=candidate_code,
                    suite_testbench_codes=public_codes,
                    timelimit=self._csim_timeout_s,
                ),
                split=EvaluationSplit.PUBLIC,
            ),
            QualificationStage.CSYNTH: CsynthValidationStageHandler(
                CsynthStageInputs(
                    work_dir=work / "csynth",
                    candidate_code=candidate_code,
                    timelimit=self._csynth_timeout_s,
                )
            ),
            QualificationStage.HIDDEN: CsimValidationStageHandler(
                CsimStageInputs(
                    work_dir=work / "hidden",
                    original_code=self._material.reference_code,
                    candidate_code=candidate_code,
                    suite_testbench_codes=hidden_codes,
                    timelimit=self._csim_timeout_s,
                ),
                split=EvaluationSplit.HIDDEN,
            ),
        }
        handlers[QualificationStage.PREFLIGHT] = (
            self._capture_recovery_handler(
                candidate.candidate_id,
                QualificationStage.PREFLIGHT,
                handlers[QualificationStage.PREFLIGHT],
            )
        )
        handlers[QualificationStage.CSYNTH] = (
            self._capture_recovery_handler(
                candidate.candidate_id,
                QualificationStage.CSYNTH,
                handlers[QualificationStage.CSYNTH],
            )
        )

        cache_identity = ValidationCacheIdentity.build(
            source_sha256=candidate.source_sha256,
            effective_target=self._material.target.to_effective_dict(),
            toolchain_fingerprint_sha256=self._toolchain_fingerprint,
            suites=self._suite_identities,
            compile_flags=self._material.target.compile_flags,
            clock_period_ns=self._material.target.clock_period_ns,
            device=self._material.target.device,
            parser_profile=self._material.target.parser_profile,
        )
        orchestrator = Stage3QualificationOrchestrator(handlers, cache=self._cache)
        optimize_context = RunContext(
            run_id=self._context.run_id,
            task=self._material.task,
            budget=self._context.budget,
            trace=self._context.trace,
        )
        request = CandidateQualificationRequest(
            qualification_id=qualification_id,
            candidate=candidate,
            source_path=source_path,
            ppa_work_dir=work / "csynth",
            top_function=self._material.top_function,
            cache_identity=cache_identity,
            resource_limits=self._material.target.resource_limits.to_dict(),
        )
        return orchestrator.run(optimize_context, request)

    def _capture_recovery_handler(
        self,
        candidate_id: str,
        stage: QualificationStage,
        handler,
    ):
        def captured(context: RunContext):
            report = handler(context)
            if not isinstance(report, FeedbackReport):
                return report
            if report.metadata.get("evidence_view") != "agent_safe":
                return report
            decision = self._recovery_router.route(
                report,
                decision_id=(
                    f"{context.run_id}.{candidate_id}."
                    f"{stage.value}.recovery-decision"
                ),
            )
            self._recovery_reports.setdefault(candidate_id, {})[stage] = (
                report,
                decision,
            )
            return report

        return captured

    def recovery_evidence(
        self,
        candidate_id: str,
        qualification: CandidateQualificationResult,
    ) -> OptimizeRecoveryEvidence | None:
        if (
            not isinstance(qualification, CandidateQualificationResult)
            or qualification.candidate_id != candidate_id
            or qualification.status is not QualificationStatus.REJECTED
        ):
            return None
        records = self._recovery_reports.get(candidate_id, {})
        for stage, recovery_stage in (
            (QualificationStage.PREFLIGHT, OptimizeRecoveryStage.PREFLIGHT),
            (QualificationStage.CSYNTH, OptimizeRecoveryStage.CSYNTH),
        ):
            step = next(
                (
                    item
                    for item in qualification.steps
                    if item.stage is stage
                    and item.outcome is QualificationStepOutcome.FAILED
                ),
                None,
            )
            if (
                step is None
                or step.route_action is not FeedbackRouteAction.REPAIR_CANDIDATE
            ):
                continue
            captured = records.get(stage)
            if captured is None:
                return None
            report, decision = captured
            reasons = tuple(
                code
                for code in step.reason_codes
                if code not in {f"{stage.value}_failed", "repair_candidate"}
            )
            if recovery_stage is OptimizeRecoveryStage.CSYNTH and not reasons:
                reasons = ("candidate_csynth_legality_failed",)
            try:
                return OptimizeRecoveryEvidence(
                    stage=recovery_stage,
                    feedback=report,
                    route_decision=decision,
                    reason_codes=reasons,
                )
            except (TypeError, ValueError):
                return None
        return None

    def recovery_budget_increment(self) -> BudgetIncrement:
        suite_count = len(self._material.suites)
        return BudgetIncrement(
            tool_calls=10 + 2 * suite_count,
            compile_calls=6 + suite_count,
            csim_calls=suite_count,
            csynth_calls=1,
        )

    def validate_recovery(
        self,
        request: OptimizeRecoveryValidationRequest,
    ) -> CandidateExecutionResult:
        if not isinstance(request, OptimizeRecoveryValidationRequest):
            raise TypeError(
                "request must be OptimizeRecoveryValidationRequest"
            )
        candidate_root = self._work_root / request.candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        source_path = candidate_root / "source.cpp"
        source_path.write_bytes(request.source)
        candidate = CandidateRecord(
            candidate_id=request.candidate_id,
            sequence=request.sequence,
            parent_candidate_id=request.source_candidate.candidate_id,
            hypothesis_id=request.hypothesis.hypothesis_id,
            level=request.hypothesis.level,
            source_sha256=sha256(request.source).hexdigest(),
            source_artifact=f"candidates/{request.candidate_id}/source.cpp",
            status=CandidateStatus.GENERATED,
            budget_before=request.budget_before,
            created_at_utc=request.created_at_utc,
        )
        qualification = self._qualify_candidate(
            candidate=candidate,
            source_path=source_path,
            qualification_id=(
                f"{self._context.run_id}.{request.candidate_id}.recovery"
            ),
        )
        return CandidateExecutionResult(
            source=request.source,
            qualification=qualification,
        )


@dataclass(frozen=True, slots=True)
class ProductOptimizerRequest:
    run_id: str
    mode: RunMode
    registry: ModelRegistry
    effective_model_config: EffectiveModelConfig
    budget_contract: EffectiveRunBudget
    artifact_root: Path
    work_root: Path
    csim_timeout_s: int
    csynth_timeout_s: int
    optimizer_profile: str = "safe-v1"
    optimization_objective: str = "latency"
    acceptance_one_physical_round_per_level: bool = False
    direct_material: AcceptedOptimizationMaterial | None = None
    refactor_phase: Any = None

    def __post_init__(self) -> None:
        if self.mode not in {RunMode.OPTIMIZE, RunMode.FULL}:
            raise ValueError("product optimizer supports optimize/full only")
        if not isinstance(self.registry, ModelRegistry):
            raise TypeError("registry must be ModelRegistry")
        if not isinstance(self.effective_model_config, EffectiveModelConfig):
            raise TypeError("effective_model_config must be EffectiveModelConfig")
        if not isinstance(self.budget_contract, EffectiveRunBudget):
            raise TypeError("budget_contract must be EffectiveRunBudget")
        if self.optimizer_profile != "safe-v1":
            raise ValueError("Stage 3 v1 supports only optimizer_profile=safe-v1")
        if self.optimization_objective != "latency":
            raise ValueError("Stage 3 v1 supports only optimization_objective=latency")
        if not isinstance(self.acceptance_one_physical_round_per_level, bool):
            raise TypeError("acceptance_one_physical_round_per_level must be boolean")
        if self.mode is RunMode.OPTIMIZE and self.direct_material is None:
            raise ValueError("direct optimize requires direct_material")
        if self.mode is RunMode.FULL and self.refactor_phase is None:
            raise ValueError("full mode requires the refactor phase handoff")


class _OnePhysicalRoundProvider:
    """Acceptance-only wrapper: one analysis call per level, then abstain."""

    name = "s37-one-physical-round-provider"
    budget_increment = BudgetIncrement()
    uses_network = True

    def __init__(self, inner, budget: BudgetManager) -> None:
        self._inner = inner
        self._budget = budget
        self._seen: set[OptimizationLevel] = set()

    def propose(self, request):
        if request.level in self._seen:
            return ()
        self._seen.add(request.level)
        self._budget.ensure_available(llm_calls=1)
        try:
            return self._inner.propose(request)
        finally:
            self._budget.consume(llm_calls=1)


class _OnePhysicalRoundExecutor:
    """Acceptance-only wrapper preserving real rewrite and qualification."""

    name = "s37-one-physical-round-executor"
    budget_increment = BudgetIncrement()
    uses_network = True
    uses_vitis = True

    def __init__(self, inner, budget: BudgetManager) -> None:
        self._inner = inner
        self._budget = budget

    def execute(self, request):
        self._budget.ensure_available(llm_calls=1)
        try:
            return self._inner.execute(request)
        finally:
            self._budget.consume(llm_calls=1)


class Stage3ProductOptimizationPhase:
    """Normal product optimization phase using the accepted safe-v1 engine."""

    schema_version = PRODUCT_OPTIMIZER_SCHEMA_VERSION

    def __init__(self, request: ProductOptimizerRequest) -> None:
        if not isinstance(request, ProductOptimizerRequest):
            raise TypeError("request must be ProductOptimizerRequest")
        self._request = request
        self._last_result = None
        self._recovery_summary = empty_optimize_recovery_summary()

    @property
    def last_result(self):
        return self._last_result

    def __call__(self, context: RunContext) -> PhaseResult:
        material = self._resolve_material()
        if self._request.mode is RunMode.OPTIMIZE:
            identity_path = self._request.artifact_root / "execution_identity.json"
            if identity_path.exists():
                raise FileExistsError(
                    "direct optimize execution identity already exists before phase start"
                )
            write_direct_optimize_execution_identity(
                run_id=context.run_id,
                material=material,
                effective_model_config=self._request.effective_model_config,
                budget_contract=self._request.budget_contract,
                artifact_root=self._request.artifact_root,
                work_root=self._request.work_root,
            )
        root = self._request.artifact_root / "optimize"
        work = self._request.work_root / "optimize"
        root.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        _atomic_json(root / "optimization_material.json", material.to_dict())
        toolchain_manifest = _observe_toolchain(material.target)
        _atomic_json(root / "toolchain_manifest.json", toolchain_manifest)
        qualifier = ProductQualificationAdapter(
            context=context,
            material=material,
            work_root=work / "qualification",
            artifact_root=root,
            csim_timeout_s=self._request.csim_timeout_s,
            csynth_timeout_s=self._request.csynth_timeout_s,
            toolchain_manifest=toolchain_manifest,
        )
        baseline_bytes = material.baseline_source_path.read_bytes()
        baseline = CandidateRecord(
            candidate_id="baseline",
            sequence=0,
            parent_candidate_id=None,
            hypothesis_id=None,
            level=None,
            source_sha256=sha256(baseline_bytes).hexdigest(),
            source_artifact="candidates/baseline/source.cpp",
            status=CandidateStatus.GENERATED,
            budget_before=context.budget.snapshot().to_dict(),
            created_at_utc=_utc_now(),
        )
        checkpoint_writer = OptimizerCheckpointWriter(root / "optimizer")
        checkpoint_writer.write_candidate_source(baseline, baseline_bytes)
        baseline_result = qualifier.qualify_baseline(baseline)
        terminal_baseline = baseline_result.apply_to_candidate(baseline)
        _atomic_json(root / "baseline_qualification.json", baseline_result.to_dict())
        state = initialize_qualified_baseline(
            OptimizerState.initial(run_id=context.run_id),
            terminal_baseline,
            baseline_result,
        )
        candidates = {"baseline": terminal_baseline}
        if not baseline_result.accepted:
            checkpoint_writer.write_checkpoint(state, candidates)
            self._write_stage3_identity(
                context=context,
                material=material,
                state=state,
                candidates=candidates,
                terminal_status=state.terminal_status,
                final_candidate_path=None,
                baseline_result=baseline_result,
                optimizer_counters=OptimizerRunCounters().to_dict(),
            )
            return PhaseResult(
                phase=RunPhase.OPTIMIZE,
                status=PhaseStatus.FAILED,
                summary=(
                    "Optimization baseline qualification failed; no optimization "
                    "model call was launched"
                ),
                metadata={
                    "accepted": False,
                    "failed_stage": "baseline_qualification",
                    "baseline_status": baseline_result.status.value,
                    "optimizer_model_calls_started": False,
                    "silent_refactor_fallback": False,
                },
            )

        required_recovery_capabilities = (
            "recovery_evidence",
            "recovery_budget_increment",
            "validate_recovery",
        )
        missing_recovery_capabilities = tuple(
            name
            for name in required_recovery_capabilities
            if not callable(getattr(qualifier, name, None))
        )
        if missing_recovery_capabilities:
            raise TypeError(
                "Product qualification adapter is missing "
                "P4-0B-R recovery capabilities: "
                + ", ".join(missing_recovery_capabilities)
            )

        recovery_adapter = CandidateModelAdapter(
            registry=self._request.registry,
            effective_config=self._request.effective_model_config,
        )
        recovery = BoundedOptimizeCandidateRecoveryCoordinator(
            model_adapter=recovery_adapter,
            validator=qualifier,
            evidence_provider=qualifier.recovery_evidence,
            task=material.task,
            original_code=material.reference_code,
            budget=context.budget,
            validation_increment=qualifier.recovery_budget_increment(),
            artifact_root=root / "recovery",
        )

        artifacts = UnifiedStage3ModelArtifactWriter(root / "model")
        structural_provider = StructuralModelHypothesisProvider(
            registry=self._request.registry,
            effective_config=self._request.effective_model_config,
            task=material.task,
            budget=context.budget,
            artifacts=artifacts,
        )
        bottleneck_provider = BottleneckModelHypothesisProvider(
            registry=self._request.registry,
            effective_config=self._request.effective_model_config,
            task=material.task,
            budget=context.budget,
            artifacts=artifacts,
        )
        pragma_provider = PragmaModelHypothesisProvider(
            registry=self._request.registry,
            effective_config=self._request.effective_model_config,
            task=material.task,
            budget=context.budget,
            artifacts=artifacts,
        )
        structural_executor = StructuralModelCandidateExecutor(
            generator=StructuralModelCandidateGenerator(
                registry=self._request.registry,
                effective_config=self._request.effective_model_config,
                task=material.task,
                budget=context.budget,
                artifacts=artifacts,
            ),
            qualifier=qualifier,
        )
        bottleneck_executor = BottleneckModelCandidateExecutor(
            generator=BottleneckModelCandidateGenerator(
                registry=self._request.registry,
                effective_config=self._request.effective_model_config,
                task=material.task,
                budget=context.budget,
                artifacts=artifacts,
            ),
            qualifier=qualifier,
        )
        pragma_executor = PragmaModelCandidateExecutor(
            generator=PragmaModelCandidateGenerator(
                registry=self._request.registry,
                effective_config=self._request.effective_model_config,
                task=material.task,
                budget=context.budget,
                artifacts=artifacts,
            ),
            qualifier=qualifier,
        )
        provider = LevelDispatchHypothesisProvider(
            {
                OptimizationLevel.STRUCTURAL: structural_provider,
                OptimizationLevel.BOTTLENECK: bottleneck_provider,
                OptimizationLevel.PRAGMA: pragma_provider,
            }
        )
        executor = LevelDispatchCandidateExecutor(
            {
                OptimizationLevel.STRUCTURAL: structural_executor,
                OptimizationLevel.BOTTLENECK: bottleneck_executor,
                OptimizationLevel.PRAGMA: pragma_executor,
            }
        )
        if self._request.acceptance_one_physical_round_per_level:
            provider = _OnePhysicalRoundProvider(provider, context.budget)
            executor = _OnePhysicalRoundExecutor(executor, context.budget)
        engine = BoundedRecoveryOptimizerStateMachine(
            state=state,
            candidates=candidates,
            checkpoint_writer=checkpoint_writer,
            provider=provider,
            executor=executor,
            recovery_coordinator=recovery,
            budget=context.budget,
            trace=context.trace,
            policy=SafeOptimizerPolicy.safe_v1(),
            artifact_store=OptimizerArtifactStore(root / "optimizer"),
            resume=False,
        )
        run_result = engine.run()
        self._recovery_summary = dict(recovery.summary())
        self._last_result = run_result
        terminal = run_result.terminal_status
        best_id = run_result.state.best_correct_candidate_id
        final_candidate_path = None
        if best_id is not None:
            source_path = checkpoint_writer.root / run_result.candidates[best_id].source_artifact
            final_candidate_path = root / "final_candidate.cpp"
            shutil.copyfile(source_path, final_candidate_path)
        self._write_stage3_identity(
            context=context,
            material=material,
            state=run_result.state,
            candidates=run_result.candidates,
            terminal_status=terminal,
            final_candidate_path=final_candidate_path,
            baseline_result=baseline_result,
            optimizer_counters=run_result.counters.to_dict(),
        )
        accepted_terminals = {
            OptimizerTerminalStatus.ACCEPTED_IMPROVED,
            OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT,
            OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT,
            OptimizerTerminalStatus.NO_FEASIBLE_CANDIDATE,
        }
        succeeded = terminal in accepted_terminals and final_candidate_path is not None
        return PhaseResult(
            phase=RunPhase.OPTIMIZE,
            status=PhaseStatus.SUCCEEDED if succeeded else PhaseStatus.FAILED,
            summary=f"Stage 3 safe-v1 finished: {terminal.value if terminal else 'unknown'}",
            metadata={
                "accepted": succeeded,
                "optimizer_terminal_status": None if terminal is None else terminal.value,
                "best_correct_candidate_id": best_id,
                "best_ppa_candidate_id": run_result.state.best_ppa_candidate_id,
                "executed_candidate_count": run_result.state.executed_candidate_count,
                "hypothesis_generation_abstentions": run_result.counters.hypothesis_generation_abstentions,
                "candidate_generation_abstentions": run_result.counters.candidate_generation_abstentions,
                "final_candidate": None if final_candidate_path is None else str(final_candidate_path),
                "baseline_qualified_before_model": True,
                "shared_budget": True,
                "shared_trace": True,
                "static_optimization_gate_used": False,
                "hidden_evidence_exposed": False,
                "correctness_repair_attempts": self._recovery_summary.get("attempted", 0),
                "optimize_candidate_recovery": dict(self._recovery_summary),
            },
        )

    def _resolve_material(self) -> AcceptedOptimizationMaterial:
        if self._request.mode is RunMode.OPTIMIZE:
            assert self._request.direct_material is not None
            return self._request.direct_material
        material = getattr(self._request.refactor_phase, "accepted_optimization_material", None)
        if material is None:
            raise RuntimeError("full mode reached optimize without an accepted refactor handoff")
        if not isinstance(material, AcceptedOptimizationMaterial):
            raise TypeError("refactor handoff has an invalid material type")
        return material

    def _write_stage3_identity(
        self,
        *,
        context: RunContext,
        material: AcceptedOptimizationMaterial,
        state: OptimizerState,
        candidates: Mapping[str, CandidateRecord],
        terminal_status: OptimizerTerminalStatus | None,
        final_candidate_path: Path | None,
        baseline_result: CandidateQualificationResult,
        optimizer_counters: Mapping[str, int],
    ) -> None:
        root_identity_path = self._request.artifact_root / "execution_identity.json"
        upstream_execution_id = None
        if root_identity_path.is_file():
            try:
                upstream_execution_id = json.loads(
                    root_identity_path.read_text(encoding="utf-8")
                ).get("execution_id")
            except Exception:
                upstream_execution_id = None
        payload = {
            "schema_version": self.schema_version,
            "run_id": context.run_id,
            "mode": self._request.mode.value,
            "upstream_execution_id": upstream_execution_id,
            "material": material.to_dict(),
            "baseline_qualification": {
                "status": baseline_result.status.value,
                "qualification_id": baseline_result.qualification_id,
                "cache_key_sha256": baseline_result.cache_key_sha256,
                "ppa_evidence_id": None if baseline_result.ppa is None else baseline_result.ppa.evidence_id,
            },
            "requested_optimizer_profile": self._request.optimizer_profile,
            "requested_optimization_objective": self._request.optimization_objective,
            "policy": SafeOptimizerPolicy.safe_v1().to_dict(),
            "state": state.to_dict(),
            "candidate_index": {
                key: value.to_dict() for key, value in sorted(candidates.items())
            },
            "terminal_status": None if terminal_status is None else terminal_status.value,
            "optimizer_counters": dict(optimizer_counters),
            "final_candidate": (
                None
                if final_candidate_path is None
                else {"path": str(final_candidate_path), "sha256": file_sha256(final_candidate_path)}
            ),
            "boundaries": {
                "baseline_qualified_before_model": baseline_result.accepted,
                "static_source_gate_used": False,
                "hidden_evidence_exposed": False,
                "model_hypotheses_authoritative": False,
                "candidate_correctness_repair_attempts": self._recovery_summary.get("attempted", 0),
                "bounded_optimize_candidate_recovery": dict(self._recovery_summary),
                "silent_refactor_fallback": False,
                "acceptance_one_physical_round_per_level": (
                    self._request.acceptance_one_physical_round_per_level
                ),
                "normal_product_policy_unchanged": True,
            },
            "budget_usage": context.budget.snapshot().to_dict(),
            "model_calls": _stage3_model_call_summary(
                self._request.artifact_root / "optimize" / "model" / "model_calls.jsonl"
            ),
        }
        payload["identity_sha256"] = sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        _atomic_json(self._request.artifact_root / "stage3_execution_identity.json", payload)


def write_direct_optimize_execution_identity(
    *,
    run_id: str,
    material: AcceptedOptimizationMaterial,
    effective_model_config: EffectiveModelConfig,
    budget_contract: EffectiveRunBudget,
    artifact_root: Path,
    work_root: Path,
) -> dict[str, Any]:
    """Persist the normal root identity required before optimize-only runs."""

    artifact_root.mkdir(parents=True, exist_ok=True)
    contract_path = artifact_root / "optimize_request_contract.json"
    _atomic_json(
        contract_path,
        {
            "schema_version": 1,
            "run_id": run_id,
            "mode": "optimize",
            "material": material.to_dict(),
            "baseline_qualification_required_before_model": True,
            "optimizer_profile": "safe-v1",
            "optimization_objective": "latency",
        },
    )
    model_manifest = effective_model_config.to_manifest()
    model_manifest["actual_cost_estimation"] = {
        "schema_version": 1,
        "quality": "unavailable",
        "observations": [],
        "amounts_by_currency": {},
        "complete": False,
        "is_invoice": False,
    }
    bundle = build_execution_identity_bundle(
        run_id=run_id,
        source_path=material.baseline_source_path,
        top_function=material.top_function,
        normalized_task=material.task.to_dict(),
        model_manifest=model_manifest,
        prompt_hashes={"optimize_request_contract": file_sha256(contract_path)},
        target_manifest=material.target.to_effective_dict(),
        prompt_evidence={"schema_version": 1, "actual_call_count": 0, "calls": []},
        suite_manifests=[item.to_dict() for item in material.suites],
        initial_candidate_path=material.baseline_source_path,
        final_candidate_path=material.baseline_source_path,
        budget_contract=budget_contract.to_dict(),
        budget_usage=None,
        artifact_schema_version=RunArtifactWriter.schema_version,
        execution_status="running",
        repository_root=Path(__file__).resolve().parents[2],
        toolchain_evidence_root=work_root,
    )
    validate_execution_identity_bundle(bundle, require_accepted_ready=False)
    write_execution_identity_bundle(artifact_root / "execution_identity.json", bundle)
    return execution_identity_summary(bundle)
