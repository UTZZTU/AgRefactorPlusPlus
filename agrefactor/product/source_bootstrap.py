
"""Source-only product bootstrap into the formal Stage-2 validation backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Protocol
from uuid import uuid4

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
)
from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TargetProfile,
    TestSourceKind,
    TestSourcePlan,
    TestSourceSelection,
    TestSourceSelectionMode,
    TestSourceSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.models import (
    CandidateModelAdapter,
    EffectiveModelConfig,
    ModelRuntimeSelection,
    resolve_model_runtime,
)
from agrefactor.runtime import (
    BudgetManager,
    CandidateRepairOrchestrationRequest,
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
    RunResult,
    TraceRecorder,
    UnifiedRunner,
    RunArtifactWriter,
    build_candidate_repair_phase,
    build_execution_identity_bundle,
    build_rejected_execution_identity_bundle,
    execution_identity_summary,
    get_model_prompt_evidence,
    reset_model_prompt_evidence,
    validate_execution_identity_bundle,
    write_execution_identity_bundle,
)
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
    HARD_BUDGET_FIELDS,
    EffectiveRunBudget,
)


_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CPP_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx"})


def _clean_required(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _safe_component(value: str) -> str:
    cleaned = _SAFE_COMPONENT_RE.sub("-", _clean_required("component", value))
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ValueError("component does not contain a safe character")
    return cleaned[:120]


def _read_code(path: Path, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    value = path.read_text(encoding="utf-8")
    if not value.strip():
        raise ValueError(f"{label} must not be empty: {path}")
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    copied = json.loads(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
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
                copied,
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


def _atomic_text(path: Path, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"text artifact must not be empty: {path}")
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
            handle.write(value.rstrip() + "\n")
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


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _mapping_get(value: object, key: str, default=None):
    if isinstance(value, Mapping):
        return value.get(key, default)
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                return getter(key)
            except Exception:
                return default
    try:
        return value[key]  # type: ignore[index]
    except Exception:
        return default


def _selection(
    split: EvaluationSplit,
    *,
    mode: str | None,
    paths: Sequence[str | os.PathLike[str]],
) -> TestSourceSelection:
    normalized_paths = tuple(str(Path(item).expanduser()) for item in paths)
    if normalized_paths:
        if mode is not None and mode != TestSourceSelectionMode.PROVIDED.value:
            raise ValueError(
                f"{split.value} provided paths conflict with "
                f"{split.value}-tests={mode}"
            )
        return TestSourceSelection.provided(split, normalized_paths)
    if mode is None or mode == TestSourceSelectionMode.AUTO.value:
        return TestSourceSelection.auto(split)
    if mode == TestSourceSelectionMode.NONE.value:
        return TestSourceSelection.none(split)
    if mode == TestSourceSelectionMode.PROVIDED.value:
        raise ValueError(
            f"{split.value} tests=provided requires at least one path"
        )
    raise ValueError(f"unsupported {split.value} test mode: {mode}")


def build_test_source_plan(
    *,
    public_mode: str | None = None,
    public_paths: Sequence[str | os.PathLike[str]] = (),
    hidden_mode: str | None = None,
    hidden_paths: Sequence[str | os.PathLike[str]] = (),
) -> TestSourcePlan:
    """Build independent split selections and derive the overall mode."""

    return TestSourcePlan(
        public=_selection(
            EvaluationSplit.PUBLIC,
            mode=public_mode,
            paths=public_paths,
        ),
        hidden=_selection(
            EvaluationSplit.HIDDEN,
            mode=hidden_mode,
            paths=hidden_paths,
        ),
    )


@dataclass(frozen=True, slots=True)
class SourceRunLayout:
    run_id: str
    artifact_root: Path
    work_root: Path

    @classmethod
    def create(
        cls,
        run_id: str,
        *,
        artifact_base: str | os.PathLike[str] | None = None,
        work_base: str | os.PathLike[str] | None = None,
    ) -> "SourceRunLayout":
        safe = _safe_component(run_id)
        artifact_parent = Path(
            artifact_base
            if artifact_base is not None
            else os.getenv(
                "AGREFACTOR_RUN_ROOT",
                os.getenv("RUN_DIR", "/data/agrefactor_runs"),
            )
        ).expanduser()
        work_parent = Path(
            work_base
            if work_base is not None
            else os.getenv(
                "AGREFACTOR_WORK_ROOT",
                os.getenv("WORK_DIR", "/data/agrefactor_work"),
            )
        ).expanduser()
        artifact_root = artifact_parent / f"source_run_{safe}"
        work_root = work_parent / f"source_run_{safe}"
        if artifact_root.exists():
            if not artifact_root.is_dir():
                raise FileExistsError(
                    f"artifact root is not a directory: {artifact_root}"
                )
            if any(artifact_root.iterdir()):
                raise FileExistsError(
                    f"artifact root already contains data: {artifact_root}"
                )
        if work_root.exists():
            if not work_root.is_dir():
                raise FileExistsError(
                    f"work root is not a directory: {work_root}"
                )
            if any(work_root.iterdir()):
                raise FileExistsError(
                    f"work root already contains data: {work_root}"
                )
        work_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_id=_clean_required("run_id", run_id),
            artifact_root=artifact_root,
            work_root=work_root,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "artifact_root": str(self.artifact_root),
            "work_root": str(self.work_root),
        }


@dataclass(frozen=True, slots=True)
class SourceBootstrapRequest:
    source_path: Path
    top_function: str
    mode: RunMode
    effective_model_config: EffectiveModelConfig
    target: TargetProfile
    test_source_plan: TestSourcePlan
    budget_contract: EffectiveRunBudget
    max_candidate_repairs: int
    run_id: str
    require_complete_execution_identity: bool = False

    def __post_init__(self) -> None:
        source = Path(self.source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source file not found: {source}")
        if source.suffix.casefold() not in _CPP_SUFFIXES:
            raise ValueError(
                "source file must use a C/C++ suffix: "
                + ", ".join(sorted(_CPP_SUFFIXES))
            )
        _read_code(source, "source file")
        mode = self.mode if isinstance(self.mode, RunMode) else RunMode(
            str(self.mode)
        )
        if not isinstance(
            self.effective_model_config,
            EffectiveModelConfig,
        ):
            raise TypeError(
                "effective_model_config must be EffectiveModelConfig"
            )
        if not isinstance(self.target, TargetProfile):
            raise TypeError("target must be TargetProfile")
        if not isinstance(self.test_source_plan, TestSourcePlan):
            raise TypeError("test_source_plan must be TestSourcePlan")
        if (
            self.test_source_plan.public.mode
            is TestSourceSelectionMode.NONE
        ):
            raise ValueError(
                "normal refactor execution requires at least one "
                "Public suite; use auto or --public-test"
            )
        if not isinstance(self.budget_contract, EffectiveRunBudget):
            raise TypeError(
                "budget_contract must be EffectiveRunBudget"
            )
        if (
            isinstance(self.max_candidate_repairs, bool)
            or not isinstance(self.max_candidate_repairs, int)
            or self.max_candidate_repairs <= 0
        ):
            raise ValueError(
                "max_candidate_repairs must be a positive integer"
            )
        object.__setattr__(self, "source_path", source)
        object.__setattr__(
            self,
            "top_function",
            _clean_required("top_function", self.top_function),
        )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self,
            "run_id",
            _clean_required("run_id", self.run_id),
        )
        if not isinstance(
            self.require_complete_execution_identity,
            bool,
        ):
            raise TypeError(
                "require_complete_execution_identity must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "require_complete_execution_identity": (
                self.require_complete_execution_identity
            ),
            "source_path": str(self.source_path),
            "source_sha256": _sha256_file(self.source_path),
            "top_function": self.top_function,
            "mode": self.mode.value,
            "effective_model_config": (
                self.effective_model_config.to_manifest()
            ),
            "effective_target_profile": (
                self.target.to_effective_dict()
            ),
            "test_source_plan": (
                self.test_source_plan.to_operator_dict()
            ),
            "budget_contract": self.budget_contract.to_dict(),
            "max_candidate_repairs": self.max_candidate_repairs,
        }


@dataclass(frozen=True, slots=True)
class SourceBootstrapRunResult:
    result: RunResult
    layout: SourceRunLayout

    def __post_init__(self) -> None:
        if not isinstance(self.result, RunResult):
            raise TypeError("result must be RunResult")
        if not isinstance(self.layout, SourceRunLayout):
            raise TypeError("layout must be SourceRunLayout")


class FormalPhaseBuilder(Protocol):
    def __call__(
        self,
        task: TaskSpec,
        request: CandidateRepairOrchestrationRequest,
    ) -> Callable[[RunContext], PhaseResult]:
        ...


class SourceBootstrapPhase:
    """Generate once, then let the formal Stage-2 backend adjudicate."""

    def __init__(
        self,
        *,
        request: SourceBootstrapRequest,
        layout: SourceRunLayout,
        generation_adapter: LegacyRefactorAdapter,
        formal_phase_builder: FormalPhaseBuilder,
    ) -> None:
        if not isinstance(request, SourceBootstrapRequest):
            raise TypeError("request must be SourceBootstrapRequest")
        if not isinstance(layout, SourceRunLayout):
            raise TypeError("layout must be SourceRunLayout")
        if not callable(generation_adapter):
            raise TypeError("generation_adapter must be callable")
        if not callable(formal_phase_builder):
            raise TypeError("formal_phase_builder must be callable")
        self._request = request
        self._layout = layout
        self._generation_adapter = generation_adapter
        self._formal_phase_builder = formal_phase_builder
        self._last_generation_result: PhaseResult | None = None
        self._last_formal_phase: Any = None

    def __call__(self, context: RunContext) -> PhaseResult:
        if self._request.mode is not RunMode.REFACTOR:
            raise ValueError(
                "source bootstrap execution currently supports refactor; "
                "optimize/full remain gated until the Stage-3 optimizer"
            )
        bootstrap_root = self._layout.artifact_root / "bootstrap"
        bootstrap_root.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            bootstrap_root / "source_request.json",
            self._request.to_dict(),
        )
        _atomic_json(
            bootstrap_root / "test_source_plan.json",
            self._request.test_source_plan.to_operator_dict(),
        )
        _atomic_json(
            bootstrap_root / "effective_model_config.json",
            self._request.effective_model_config.to_manifest(),
        )
        _atomic_json(
            bootstrap_root / "effective_target_profile.json",
            self._request.target.to_effective_dict(),
        )
        _atomic_json(
            bootstrap_root / "effective_budget_contract.json",
            self._request.budget_contract.to_dict(),
        )

        generation_contract = {
            "schema_version": 1,
            "run_id": self._request.run_id,
            "source_sha256": _sha256_file(
                self._request.source_path
            ),
            "source_top_function": self._request.top_function,
            "model": self._request.effective_model_config.to_manifest(),
            "target": self._request.target.to_dict(),
            "public_mode": (
                self._request.test_source_plan.public.mode.value
            ),
            "hidden_mode": (
                self._request.test_source_plan.hidden.mode.value
            ),
            "generator": "legacy_generation_only_bridge",
            "legacy_success_is_final_verdict": False,
        }
        generation_contract_path = (
            bootstrap_root / "initial_generation_request.json"
        )
        _atomic_json(generation_contract_path, generation_contract)
        generation_prompt_sha = _sha256_file(
            generation_contract_path
        )
        self._write_execution_identity(
            context=context,
            normalized_task=context.task,
            suites=(),
            execution_status="running",
            initial_candidate_path=None,
            final_candidate_path=None,
            require_accepted_ready=False,
            hard_budget_exhaustion=None,
        )

        generation_task = TaskSpec(
            task_id=f"{self._request.run_id}.generation",
            kernel_path=str(self._request.source_path),
            kernel_name=self._request.top_function,
            target=self._request.target,
            mode=RunMode.REFACTOR,
            testbench_path=self._first_public_provided_path(),
        )
        generation_context = RunContext(
            run_id=context.run_id,
            task=generation_task,
            budget=context.budget,
            trace=context.trace,
        )
        generation_result = self._generation_adapter(
            generation_context
        )
        self._last_generation_result = generation_result
        if not generation_result.succeeded:
            identity_summary = self._write_execution_identity(
                context=context,
                normalized_task=context.task,
                suites=(),
                execution_status=generation_result.status.value,
                initial_candidate_path=None,
                final_candidate_path=None,
                require_accepted_ready=False,
                hard_budget_exhaustion=(
                    {
                        "resource": generation_result.metadata.get("resource"),
                        "stage": "initial_generation",
                    }
                    if generation_result.metadata.get("resource")
                    else None
                ),
            )
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=generation_result.status,
                summary=(
                    "Initial generation failed before formal validation: "
                    + (generation_result.summary or "unknown failure")
                ),
                metadata={
                    "source_bootstrap": True,
                    "generation_only": True,
                    "formal_validation_started": False,
                    "execution_identity": identity_summary,
                },
            )

        raw_result = getattr(
            self._generation_adapter,
            "last_raw_result",
            None,
        )
        generated = self._extract_generation_result(raw_result)
        original_code = _read_code(
            self._request.source_path,
            "source file",
        )
        candidate_code = generated["candidate_code"]
        candidate_top = generated["candidate_top"]
        if candidate_top == self._request.top_function:
            raise ValueError(
                "generation-only backend returned the original top name; "
                "formal original/candidate comparison requires a distinct "
                "candidate top"
            )

        _atomic_text(
            bootstrap_root / "initial_candidate.cpp",
            candidate_code,
        )
        actual_generation_prompts = get_model_prompt_evidence()
        if actual_generation_prompts.get("actual_call_count", 0) > 0:
            generation_prompt_sha = str(
                actual_generation_prompts["aggregate_sha256"]
            )
        suites, suite_codes = self._materialize_suites(
            bootstrap_root=bootstrap_root,
            generated=generated,
            generation_prompt_sha=generation_prompt_sha,
        )
        public_suites = tuple(
            suite for suite in suites
            if suite.split is EvaluationSplit.PUBLIC
        )
        if not public_suites:
            raise ValueError(
                "formal source bootstrap requires at least one Public suite"
            )
        preflight_suite = public_suites[0]
        assert preflight_suite.testbench_path is not None
        preflight_code = suite_codes[preflight_suite.suite_id]

        formal_task = TaskSpec(
            task_id=f"{self._request.run_id}.formal",
            kernel_path=str(self._request.source_path),
            kernel_name=candidate_top,
            target=self._request.target,
            mode=RunMode.REFACTOR,
            testbench_path=preflight_suite.testbench_path,
            test_suites=suites,
        )
        _atomic_json(
            bootstrap_root / "normalized_task.json",
            formal_task.to_dict(),
        )
        formal_request = CandidateRepairOrchestrationRequest(
            initial_candidate=candidate_code,
            original_code=original_code,
            preflight_testbench_code=preflight_code,
            suite_testbench_codes=suite_codes,
            prompt_public_testbench_code=preflight_code,
            max_attempts=self._request.max_candidate_repairs,
            family_instruction=(
                self._request.effective_model_config.family_instruction
            ),
        )
        _atomic_json(
            bootstrap_root / "formal_validation_request.json",
            {
                "schema_version": 1,
                "formal_task_id": formal_task.task_id,
                "candidate_sha256": _sha256_text(candidate_code),
                "candidate_top": candidate_top,
                "public_prompt_suite_id": preflight_suite.suite_id,
                "suite_ids": [suite.suite_id for suite in suites],
                "suite_hashes": {
                    suite.suite_id: _sha256_text(
                        suite_codes[suite.suite_id]
                    )
                    for suite in suites
                },
                "max_candidate_repairs": (
                    self._request.max_candidate_repairs
                ),
                "shared_budget": True,
                "shared_trace": True,
                "legacy_success_is_final_verdict": False,
            },
        )

        formal_phase = self._formal_phase_builder(
            formal_task,
            formal_request,
        )
        self._last_formal_phase = formal_phase
        formal_context = RunContext(
            run_id=context.run_id,
            task=formal_task,
            budget=context.budget,
            trace=context.trace,
        )
        formal_result = formal_phase(formal_context)
        if not isinstance(formal_result, PhaseResult):
            raise TypeError(
                "formal phase builder must return a PhaseResult handler"
            )
        metadata = dict(formal_result.metadata)
        accepted = bool(metadata.get("accepted", False))
        final_candidate_path = (
            self._layout.artifact_root
            / RunPhase.REFACTOR.value
            / "final_candidate.cpp"
        )
        if (
            self._request.require_complete_execution_identity
            and accepted
            and not final_candidate_path.is_file()
        ):
            raise FileNotFoundError(
                "accepted formal validation did not persist final_candidate.cpp"
            )
        if not final_candidate_path.is_file():
            final_candidate_path = (
                bootstrap_root / "initial_candidate.cpp"
            )
        identity_summary = self._write_execution_identity(
            context=context,
            normalized_task=formal_task,
            suites=suites,
            execution_status=(
                "accepted" if accepted else formal_result.status.value
            ),
            initial_candidate_path=(
                bootstrap_root / "initial_candidate.cpp"
            ),
            final_candidate_path=final_candidate_path,
            require_accepted_ready=(
                self._request.require_complete_execution_identity
                and accepted
            ),
            hard_budget_exhaustion=(
                {
                    "resource": metadata.get("resource"),
                    "stage": metadata.get(
                        "failed_stage",
                        "formal_validation",
                    ),
                }
                if metadata.get("resource")
                else None
            ),
        )
        metadata.update(
            {
                "source_bootstrap": True,
                "generation_only": True,
                "legacy_success_is_final_verdict": False,
                "formal_validation_started": True,
                "formal_task_id": formal_task.task_id,
                "source_top_function": self._request.top_function,
                "candidate_top_function": candidate_top,
                "test_source_mode": (
                    self._request.test_source_plan.overall_mode.value
                ),
                "public_suite_count": len(public_suites),
                "hidden_suite_count": len(suites) - len(public_suites),
                "shared_budget": True,
                "shared_trace": True,
                "bootstrap_manifest": (
                    "bootstrap/source_request.json"
                ),
                "execution_identity": identity_summary,
            }
        )
        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=formal_result.status,
            summary=formal_result.summary,
            metadata=metadata,
        )

    def _write_execution_identity(
        self,
        *,
        context: RunContext,
        normalized_task: TaskSpec,
        suites: Sequence[TestSuiteSpec],
        execution_status: str,
        initial_candidate_path: Path | None,
        final_candidate_path: Path | None,
        require_accepted_ready: bool,
        hard_budget_exhaustion: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        bootstrap_root = self._layout.artifact_root / "bootstrap"
        prompt_hashes: dict[str, str] = {}
        for name in (
            "initial_generation_request",
            "formal_validation_request",
        ):
            path = bootstrap_root / f"{name}.json"
            if path.is_file():
                prompt_hashes[name] = _sha256_file(path)
        if not prompt_hashes:
            raise RuntimeError(
                "execution identity requires at least one persisted prompt contract"
            )
        try:
            usage = context.budget.snapshot().to_dict()
        except Exception:
            usage = None
        model_manifest = (
            self._request.effective_model_config.to_manifest()
        )
        model_manifest["actual_cost_estimation"] = (
            self._actual_cost_estimation()
        )
        bundle = build_execution_identity_bundle(
            run_id=context.run_id,
            source_path=self._request.source_path,
            top_function=self._request.top_function,
            normalized_task=normalized_task.to_dict(),
            model_manifest=model_manifest,
            prompt_hashes=prompt_hashes,
            target_manifest=self._request.target.to_effective_dict(),
            prompt_evidence=self._collect_prompt_evidence(),
            suite_manifests=self._qualified_suite_manifests(suites),
            initial_candidate_path=initial_candidate_path,
            final_candidate_path=final_candidate_path,
            budget_contract=self._request.budget_contract.to_dict(),
            budget_usage=usage,
            artifact_schema_version=RunArtifactWriter.schema_version,
            execution_status=execution_status,
            repository_root=Path(__file__).resolve().parents[2],
            toolchain_evidence_root=self._layout.work_root,
            hard_budget_exhaustion=hard_budget_exhaustion,
        )
        validate_execution_identity_bundle(
            bundle,
            require_accepted_ready=require_accepted_ready,
        )
        path = self._layout.artifact_root / "execution_identity.json"
        write_execution_identity_bundle(path, bundle)
        return execution_identity_summary(bundle)

    def _collect_prompt_evidence(self) -> dict[str, Any]:
        payload = get_model_prompt_evidence()
        calls = list(payload.get("calls", []))
        formal_result = getattr(
            self._last_formal_phase,
            "last_result",
            None,
        )
        repair_result = getattr(formal_result, "repair_result", None)
        attempts = getattr(repair_result, "attempts", ())
        for attempt in attempts:
            manifest = dict(getattr(attempt, "prompt_manifest", {}) or {})
            sequence_hash = manifest.get("message_sequence_sha256")
            template_id = manifest.get("prompt_template_id")
            template_version = manifest.get("prompt_template_version")
            if not (
                isinstance(sequence_hash, str)
                and isinstance(template_id, str)
                and isinstance(template_version, int)
            ):
                continue
            calls.append(
                {
                    "schema_version": 1,
                    "call_index": len(calls) + 1,
                    "template_id": template_id,
                    "template_version": template_version,
                    "system_message_sha256": manifest.get(
                        "rendered_system_message_sha256"
                    ),
                    "invocation_sha256": manifest.get(
                        "rendered_user_message_sha256"
                    ),
                    "message_sequence_sha256": sequence_hash,
                    "provider_call_observed": (
                        str(getattr(attempt, "status", ""))
                        != "CandidateRepairAttemptStatus.BUDGET_BLOCKED"
                        and getattr(
                            getattr(attempt, "status", None),
                            "value",
                            None,
                        ) != "budget_blocked"
                    ),
                    "metadata": {
                        "source": "candidate_repair",
                        "attempt": getattr(attempt, "attempt", None),
                    },
                }
            )
        return {
            "schema_version": 1,
            "actual_call_count": sum(
                1
                for item in calls
                if item.get("provider_call_observed") is True
            ),
            "calls": calls,
            "aggregate_sha256": _sha256_text(
                json.dumps(
                    calls,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        }

    def _qualified_suite_manifests(
        self,
        suites: Sequence[TestSuiteSpec],
    ) -> list[dict[str, Any]]:
        observed: dict[tuple[str, str], Mapping[str, Any]] = {}
        for path in sorted(
            self._layout.work_root.rglob(
                "suite_identity_evidence.json"
            ),
            key=lambda item: (item.stat().st_mtime_ns, str(item)),
        ):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            suite_id = payload.get("suite_id")
            split = payload.get("split")
            if isinstance(suite_id, str) and isinstance(split, str):
                observed[(split, suite_id)] = payload
        result: list[dict[str, Any]] = []
        for suite in suites:
            manifest = suite.to_dict()
            evidence = observed.get((suite.split.value, suite.suite_id))
            if evidence is not None:
                manifest["evaluation_status"] = evidence.get(
                    "evaluation_status"
                )
                provenance = evidence.get("source_provenance")
                if isinstance(provenance, Mapping):
                    source = dict(manifest.get("source") or {})
                    source.update(dict(provenance))
                    manifest["source"] = source
            result.append(manifest)
        return result

    def _actual_cost_estimation(self) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []

        def add_estimate(value: object, source: str) -> None:
            if not isinstance(value, Mapping):
                return
            quality = str(value.get("quality", "unavailable"))
            if quality not in {"verified", "approximate", "unavailable"}:
                quality = "unavailable"
            observations.append(
                {
                    "source": source,
                    "quality": quality,
                    "amount": value.get("amount"),
                    "currency": value.get("currency"),
                    "pricing_snapshot_sha256": value.get(
                        "pricing_snapshot_sha256"
                    ),
                    "assumptions": list(value.get("assumptions", ())),
                }
            )

        generation = self._last_generation_result
        legacy_usage = (
            generation.metadata.get("legacy_usage", {})
            if isinstance(generation, PhaseResult)
            else {}
        )
        models = (
            legacy_usage.get("models", {})
            if isinstance(legacy_usage, Mapping)
            else {}
        )
        if isinstance(models, Mapping):
            for model_name, info in models.items():
                if not isinstance(info, Mapping):
                    continue
                token_usage = info.get("token_usage")
                if isinstance(token_usage, Mapping):
                    add_estimate(
                        token_usage.get("estimated_cost"),
                        f"legacy:{model_name}",
                    )

        formal_result = getattr(
            self._last_formal_phase,
            "last_result",
            None,
        )
        repair_result = getattr(formal_result, "repair_result", None)
        for attempt in getattr(repair_result, "attempts", ()):
            response = getattr(attempt, "model_response", None)
            usage = getattr(response, "usage", None)
            estimate = getattr(usage, "estimated_cost", None)
            if estimate is not None:
                add_estimate(
                    estimate.to_dict(),
                    f"candidate_repair:{getattr(attempt, 'attempt', 0)}",
                )

        priced = [
            item
            for item in observations
            if item.get("amount") is not None
            and isinstance(item.get("currency"), str)
        ]
        if not observations or any(
            item.get("quality") == "unavailable"
            for item in observations
        ):
            quality = "unavailable"
        elif any(
            item.get("quality") == "approximate"
            for item in observations
        ):
            quality = "approximate"
        else:
            quality = "verified"
        amounts: dict[str, Decimal] = {}
        for item in priced:
            try:
                amount = Decimal(str(item["amount"]))
            except (InvalidOperation, ValueError):
                continue
            currency = str(item["currency"]).upper()
            amounts[currency] = amounts.get(currency, Decimal("0")) + amount
        return {
            "schema_version": 1,
            "quality": quality,
            "observations": observations,
            "amounts_by_currency": {
                currency: format(amount.normalize(), "f")
                for currency, amount in sorted(amounts.items())
            },
            "complete": bool(observations) and len(priced) == len(observations),
            "is_invoice": False,
        }

    def _first_public_provided_path(self) -> str | None:
        public = self._request.test_source_plan.public
        if public.mode is not TestSourceSelectionMode.PROVIDED:
            return None
        return public.provided_paths[0]

    @staticmethod
    def _extract_generation_result(raw_result: object) -> dict[str, str]:
        if (
            not isinstance(raw_result, tuple)
            or len(raw_result) < 2
        ):
            raise ValueError(
                "generation-only backend did not expose its raw result"
            )
        success = bool(raw_result[0])
        if not success:
            raise ValueError(
                "generation-only backend reported failure"
            )
        context_variables = raw_result[1]
        candidate = _mapping_get(
            context_variables,
            "curr_code",
        )
        candidate_top = _mapping_get(
            context_variables,
            "new_kernel_name",
        )
        public_testbench = _mapping_get(
            context_variables,
            "testbench",
        )
        hidden_testbench = _mapping_get(
            context_variables,
            "generated_hidden_testbench",
        )
        for name, value in (
            ("candidate_code", candidate),
            ("candidate_top", candidate_top),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"generation-only backend returned no {name}"
                )
        return {
            "candidate_code": candidate,
            "candidate_top": candidate_top.strip(),
            "public_testbench": (
                public_testbench
                if isinstance(public_testbench, str)
                else ""
            ),
            "hidden_testbench": (
                hidden_testbench
                if isinstance(hidden_testbench, str)
                else ""
            ),
        }

    def _materialize_suites(
        self,
        *,
        bootstrap_root: Path,
        generated: Mapping[str, str],
        generation_prompt_sha: str,
    ) -> tuple[tuple[TestSuiteSpec, ...], dict[str, str]]:
        suites: list[TestSuiteSpec] = []
        codes: dict[str, str] = {}
        for selection in (
            self._request.test_source_plan.public,
            self._request.test_source_plan.hidden,
        ):
            split = selection.split
            if selection.mode is TestSourceSelectionMode.NONE:
                continue
            if selection.mode is TestSourceSelectionMode.PROVIDED:
                entries = tuple(
                    (
                        Path(raw).expanduser().resolve(),
                        _read_code(
                            Path(raw).expanduser().resolve(),
                            f"{split.value} provided test",
                        ),
                    )
                    for raw in selection.provided_paths
                )
                kind = TestSourceKind.PROVIDED
            else:
                key = (
                    "public_testbench"
                    if split is EvaluationSplit.PUBLIC
                    else "hidden_testbench"
                )
                code = generated.get(key, "")
                if not code:
                    raise ValueError(
                        f"auto {split.value} selection produced no testbench"
                    )
                entries = ((None, code),)
                kind = TestSourceKind.GENERATED

            for index, (source_path, code) in enumerate(
                entries,
                start=1,
            ):
                suite_id = f"{split.value}-{index:03d}"
                target_path = (
                    bootstrap_root
                    / "tests"
                    / split.value
                    / f"{suite_id}.cpp"
                )
                _atomic_text(target_path, code)
                digest = _sha256_text(code.rstrip() + "\n")
                if kind is TestSourceKind.PROVIDED:
                    source = TestSourceSpec(
                        source_id=(
                            f"{split.value}-provided-{digest[:16]}"
                        ),
                        source_revision=None,
                        source_kind=kind,
                        expected_content_sha256=digest,
                        operator_artifact_path=str(target_path),
                    )
                else:
                    source = TestSourceSpec(
                        source_id=(
                            f"{split.value}-generated-{digest[:16]}"
                        ),
                        source_revision="1",
                        source_kind=kind,
                        expected_content_sha256=digest,
                        operator_artifact_path=str(target_path),
                        generation_model=(
                            self._request.effective_model_config.model_id
                        ),
                        generation_profile=(
                            self._request.effective_model_config
                            .family_profile_name
                        ),
                        prompt_sha256=generation_prompt_sha,
                        trajectory_id=(
                            f"{self._request.run_id}."
                            f"{split.value}.generation"
                        ),
                        round_index=0,
                    )
                suite = TestSuiteSpec(
                    suite_id=suite_id,
                    suite_version="1",
                    split=split,
                    testbench_path=str(target_path),
                    source=source,
                )
                suites.append(suite)
                codes[suite_id] = code
                if source_path is not None:
                    _atomic_json(
                        target_path.with_suffix(".source.json"),
                        {
                            "source_path": str(source_path),
                            "source_sha256": _sha256_file(source_path),
                            "materialized_path": str(target_path),
                        },
                    )
        return tuple(suites), codes


def _target_from_cli(args) -> TargetProfile:
    overrides: dict[str, Any] = {
        "profile": args.target,
    }
    if args.part is not None:
        overrides["device"] = args.part
    if args.clock_period is not None:
        overrides["clock_period_ns"] = args.clock_period
    if args.compile_flags:
        overrides["compile_flags"] = list(args.compile_flags)
    return resolve_target_profile(overrides)


def _budget_from_cli(args, selection: ModelRuntimeSelection) -> EffectiveRunBudget:
    requested = {
        name: getattr(args, name)
        for name in (
            "max_llm_calls",
            "max_tool_calls",
            "max_compile_calls",
            "max_csim_calls",
            "max_csynth_calls",
            "max_wall_time_s",
        )
    }
    pricing = selection.effective_config.pricing_snapshot
    currency = (
        None if pricing is None else pricing.currency
    )
    if args.cost_budget is not None and currency is None:
        raise ValueError(
            "--cost-budget requires a verified pricing snapshot "
            "with a declared currency"
        )
    return DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(
        user_requested=requested,
        token_budget=args.token_budget,
        cost_budget=args.cost_budget,
        cost_budget_currency=currency,
    )


def _budget_request_identity(args) -> dict[str, Any]:
    defaults = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.system_defaults
    ceilings = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.system_safety_ceilings
    return {
        "schema_version": 1,
        "system_defaults": {
            name: getattr(defaults, name)
            for name in HARD_BUDGET_FIELDS
        },
        "system_safety_ceilings": {
            name: getattr(ceilings, name)
            for name in HARD_BUDGET_FIELDS
        },
        "user_requested": {
            name: getattr(args, name)
            for name in HARD_BUDGET_FIELDS
        },
        "effective_hard_limits": None,
        "soft_usage_budgets": {
            "token_budget": args.token_budget,
            "cost_budget": args.cost_budget,
            "enforcement": "observed_only",
            "blocking": False,
        },
    }


def _safety_ceiling_rejection(args) -> dict[str, Any] | None:
    ceilings = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.system_safety_ceilings
    for name in HARD_BUDGET_FIELDS:
        requested = getattr(args, name)
        ceiling = getattr(ceilings, name)
        if requested is not None and ceiling is not None and requested > ceiling:
            return {
                "schema_version": 1,
                "kind": "safety_ceiling_exceeded",
                "resource": name,
                "user_requested": requested,
                "system_safety_ceiling": ceiling,
                "effective_budget": None,
                "credential_persisted": False,
            }
    return None


def _write_request_rejection_artifacts(
    *,
    layout: SourceRunLayout,
    source: Path,
    top_function: str,
    mode: RunMode,
    model_runtime: ModelRuntimeSelection,
    plan: TestSourcePlan,
    target: TargetProfile,
    args,
    rejection: Mapping[str, Any],
) -> None:
    layout.artifact_root.mkdir(parents=True, exist_ok=True)
    task = TaskSpec(
        task_id=f"{layout.run_id}.source",
        kernel_path=str(source),
        kernel_name=top_function,
        target=target,
        mode=mode,
    )
    model_manifest = model_runtime.effective_config.to_manifest()
    model_manifest["actual_cost_estimation"] = {
        "schema_version": 1,
        "quality": "unavailable",
        "observations": [],
        "amounts_by_currency": {},
        "complete": False,
        "is_invoice": False,
    }
    budget_request = _budget_request_identity(args)
    bundle = build_rejected_execution_identity_bundle(
        run_id=layout.run_id,
        source_path=source,
        top_function=top_function,
        normalized_task=task.to_dict(),
        model_manifest=model_manifest,
        target_manifest=target.to_effective_dict(),
        test_source_plan=plan.to_operator_dict(),
        budget_request=budget_request,
        rejection=rejection,
        artifact_schema_version=RunArtifactWriter.schema_version,
        repository_root=Path(__file__).resolve().parents[2],
    )
    _atomic_json(
        layout.artifact_root / "request_rejection.json",
        dict(rejection),
    )
    write_execution_identity_bundle(
        layout.artifact_root / "execution_identity.json",
        bundle,
    )
    files = []
    for path in sorted(layout.artifact_root.iterdir()):
        if path.is_file() and path.name != "run_artifact_manifest.json":
            files.append(
                {
                    "relative_path": path.name,
                    "sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    _atomic_json(
        layout.artifact_root / "run_artifact_manifest.json",
        {
            "schema_version": RunArtifactWriter.schema_version,
            "run_id": layout.run_id,
            "status": "request_rejected",
            "execution_mode": "source_bootstrap",
            "legacy_mode": False,
            "evidence_view": "agent_safe",
            "execution_identity": execution_identity_summary(bundle),
            "files": files,
        },
    )


def run_source_command(args) -> SourceBootstrapRunResult:
    """Execute one normal source command using internally managed paths."""

    mode = RunMode(args.command)
    if mode is not RunMode.REFACTOR:
        raise ValueError(
            f"{mode.value} command is reserved by the frozen CLI contract "
            "but execution remains gated until the Stage-3 optimizer; "
            "no placeholder optimization result is produced"
        )

    source = Path(args.source).expanduser().resolve()
    run_id = (
        args.run_id.strip()
        if isinstance(args.run_id, str) and args.run_id.strip()
        else (
            f"{mode.value}-"
            + uuid4().hex
        )
    )
    reset_model_prompt_evidence()
    model_runtime = resolve_model_runtime(
        args.model,
        family=args.model_family,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        reasoning_effort=args.reasoning_effort,
    )
    plan = build_test_source_plan(
        public_mode=args.public_tests,
        public_paths=args.public_tests_provided,
        hidden_mode=args.hidden_tests,
        hidden_paths=args.hidden_tests_provided,
    )
    target = _target_from_cli(args)
    layout = SourceRunLayout.create(run_id)
    rejection = _safety_ceiling_rejection(args)
    if rejection is not None:
        _write_request_rejection_artifacts(
            layout=layout,
            source=source,
            top_function=args.top,
            mode=mode,
            model_runtime=model_runtime,
            plan=plan,
            target=target,
            args=args,
            rejection=rejection,
        )
        raise ValueError(
            f"{rejection['resource']}={rejection['user_requested']} "
            f"exceeds system safety ceiling "
            f"{rejection['system_safety_ceiling']}; "
            f"rejection artifacts: {layout.artifact_root}"
        )
    budget = _budget_from_cli(args, model_runtime)

    request = SourceBootstrapRequest(
        source_path=source,
        top_function=args.top,
        mode=mode,
        effective_model_config=model_runtime.effective_config,
        target=target,
        test_source_plan=plan,
        budget_contract=budget,
        max_candidate_repairs=args.max_candidate_repairs,
        run_id=run_id,
        require_complete_execution_identity=True,
    )

    public_auto = (
        plan.public.mode is TestSourceSelectionMode.AUTO
    )
    hidden_auto = (
        plan.hidden.mode is TestSourceSelectionMode.AUTO
    )
    generation_settings = LegacyRefactorSettings(
        effective_model_config=model_runtime.effective_config,
        output_dir=str(layout.work_root / "generation"),
        max_retry_attempts=0,
        debug=False,
        generation_only=True,
        external_kernel_name=f"{request.top_function}_hls",
        enable_tb_coverage_loop=public_auto,
        public_tb_rounds=3,
        public_tb_target=80.0,
        enable_hidden_tb_eval=hidden_auto,
        hidden_tb_rounds=6,
        hidden_tb_trajectories=3,
        hidden_tb_target=90.0,
    )
    generation_adapter = LegacyRefactorAdapter(
        generation_settings
    )
    candidate_adapter = CandidateModelAdapter(
        registry=model_runtime.registry,
        effective_config=model_runtime.effective_config,
    )

    def formal_builder(
        task: TaskSpec,
        formal_request: CandidateRepairOrchestrationRequest,
    ):
        del task
        return build_candidate_repair_phase(
            model_adapter=candidate_adapter,
            request=formal_request,
            work_root=layout.work_root / "formal_validation",
            artifact_root=layout.artifact_root,
            csynth_timelimit=300,
            csim_timelimit=60,
        )

    phase = SourceBootstrapPhase(
        request=request,
        layout=layout,
        generation_adapter=generation_adapter,
        formal_phase_builder=formal_builder,
    )
    source_task = TaskSpec(
        task_id=f"{run_id}.source",
        kernel_path=str(source),
        kernel_name=request.top_function,
        target=target,
        mode=mode,
    )
    runner = UnifiedRunner(
        {RunPhase.REFACTOR: phase},
        budget_limits=budget.to_budget_limits(),
    )
    result = runner.run(
        source_task,
        run_id=run_id,
        trace_path=layout.artifact_root / "trace.jsonl",
        artifact_root=layout.artifact_root,
        run_metadata={
            "execution_mode": "source_bootstrap",
            "legacy_mode": False,
            "model_selection": "user_fixed",
            "model_defaults_source": (
                model_runtime.defaults_source
            ),
            "artifact_root": str(layout.artifact_root),
            "work_root": str(layout.work_root),
            "test_source_mode": plan.overall_mode.value,
            "budget_contract": budget.to_dict(),
            "pre_stage3_step": "Execution Identity",
        },
    )
    return SourceBootstrapRunResult(
        result=result,
        layout=layout,
    )
