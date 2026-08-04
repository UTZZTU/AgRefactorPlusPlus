
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
from typing import Any, Protocol, TextIO
from uuid import uuid4

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
)
from agrefactor.config import (
    DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
    DEFAULT_CSIM_TIMEOUT_S,
    DEFAULT_CSYNTH_TIMEOUT_S,
    DEFAULT_COSIM_TIMEOUT_S,
    DEFAULT_HIDDEN_COVERAGE_ROUNDS,
    DEFAULT_HIDDEN_GENERATION_TRAJECTORIES,
    DEFAULT_PUBLIC_COVERAGE_ROUNDS,
    DEFAULT_PUBLIC_GENERATION_TRAJECTORIES,
    DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
    DEFAULT_TEST_GENERATION_TRAJECTORIES,
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
    TestGenerationProfile,
    resolve_target_profile,
    resolve_test_generation_profile,
    validate_csim_timeout_s,
    validate_csynth_timeout_s,
    validate_cosim_timeout_s,
    validate_repair_attempts,
    validate_test_generation_count,
)
from agrefactor.evaluation import TestbenchPreflight
from agrefactor.testing import (
    TestbenchRepairLoop,
    build_openai_compatible_testbench_repairer,
)
from agrefactor.models import (
    CandidateModelAdapter,
    EffectiveModelConfig,
    ModelRuntimeSelection,
    infer_model_family,
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

from .run_output import (
    capture_product_streams,
    finalize_product_artifacts,
    write_rejection_support_artifacts,
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


def _hidden_exposure_from_boundary(value: object) -> bool:
    """Fail closed: True means exposure or incomplete evidence."""

    if not isinstance(value, Mapping):
        return True
    if value.get("complete") is not True:
        return True
    forbidden = (
        "public_generation_hidden_inputs",
        "candidate_generation_hidden_inputs",
        "public_repair_hidden_inputs",
        "candidate_repair_hidden_inputs",
    )
    if any(value.get(name) not in ([], ()) for name in forbidden):
        return True
    if value.get("hidden_testbench_exposed_to_generation_model") is not False:
        return True
    if value.get("hidden_generation_enabled") is True:
        if value.get("hidden_generation_after_candidate") is not True:
            return True
        order = value.get("generation_event_order")
        if not isinstance(order, Sequence) or isinstance(order, (str, bytes)):
            return True
        try:
            public_index = list(order).index("public_generation")
            candidate_index = list(order).index("candidate_generation")
            hidden_index = list(order).index("hidden_generation")
        except ValueError:
            return True
        if not (public_index < candidate_index < hidden_index):
            return True
    return False


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
        artifact_root_override: str | os.PathLike[str] | None = None,
    ) -> "SourceRunLayout":
        safe = _safe_component(run_id)
        if artifact_base is not None and artifact_root_override is not None:
            raise ValueError(
                "artifact_base and artifact_root_override are mutually exclusive"
            )
        if artifact_root_override is None:
            artifact_parent = Path(
                artifact_base
                if artifact_base is not None
                else os.getenv(
                    "AGREFACTOR_RUN_ROOT",
                    os.getenv("RUN_DIR", "/data/agrefactor_runs"),
                )
            ).expanduser()
            artifact_root = artifact_parent / f"source_run_{safe}"
        else:
            artifact_root = (
                Path(artifact_root_override).expanduser().resolve()
            )
        work_parent = Path(
            work_base
            if work_base is not None
            else os.getenv(
                "AGREFACTOR_WORK_ROOT",
                os.getenv("WORK_DIR", "/data/agrefactor_work"),
            )
        ).expanduser()
        work_root = work_parent / f"source_run_{safe}"
        if artifact_root.resolve() == work_root.resolve():
            raise ValueError(
                "persistent artifact directory must differ from work directory"
            )
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
    max_testbench_repairs: int = (
        DEFAULT_TESTBENCH_REPAIR_ATTEMPTS
    )
    test_generation_profile: TestGenerationProfile = (
        TestGenerationProfile.LIGHTWEIGHT
    )
    public_coverage_rounds: int = DEFAULT_PUBLIC_COVERAGE_ROUNDS
    hidden_coverage_rounds: int = DEFAULT_HIDDEN_COVERAGE_ROUNDS
    public_generation_trajectories: int = (
        DEFAULT_PUBLIC_GENERATION_TRAJECTORIES
    )
    hidden_generation_trajectories: int = (
        DEFAULT_HIDDEN_GENERATION_TRAJECTORIES
    )
    # Deprecated shared internal constructor field retained for compatibility.
    # When supplied, it populates both Public and Hidden trajectories.
    test_generation_trajectories: int | None = None
    csim_timeout_s: int = DEFAULT_CSIM_TIMEOUT_S
    csynth_timeout_s: int = DEFAULT_CSYNTH_TIMEOUT_S
    cosim_timeout_s: int = DEFAULT_COSIM_TIMEOUT_S
    cosim_policy: str = "required"
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
                "normal refactor/full execution requires at least one "
                "Public suite; use auto or --public-test"
            )
        if not isinstance(self.budget_contract, EffectiveRunBudget):
            raise TypeError(
                "budget_contract must be EffectiveRunBudget"
            )
        validate_repair_attempts(
            self.max_testbench_repairs,
            field_name="max_testbench_repairs",
        )
        validate_repair_attempts(
            self.max_candidate_repairs,
            field_name="max_candidate_repairs",
        )
        profile = resolve_test_generation_profile(
            self.test_generation_profile
        )
        public_trajectories = self.public_generation_trajectories
        hidden_trajectories = self.hidden_generation_trajectories
        shared_trajectories = self.test_generation_trajectories
        if shared_trajectories is not None:
            validate_test_generation_count(
                shared_trajectories,
                field_name="test_generation_trajectories",
            )
            if (
                public_trajectories
                != DEFAULT_PUBLIC_GENERATION_TRAJECTORIES
                or hidden_trajectories
                != DEFAULT_HIDDEN_GENERATION_TRAJECTORIES
            ):
                raise ValueError(
                    "deprecated shared test_generation_trajectories "
                    "conflicts with independent trajectory fields"
                )
            public_trajectories = shared_trajectories
            hidden_trajectories = shared_trajectories
        for name, value in (
            ("public_coverage_rounds", self.public_coverage_rounds),
            ("hidden_coverage_rounds", self.hidden_coverage_rounds),
            (
                "public_generation_trajectories",
                public_trajectories,
            ),
            (
                "hidden_generation_trajectories",
                hidden_trajectories,
            ),
        ):
            validate_test_generation_count(
                value,
                field_name=name,
            )
        validate_csim_timeout_s(self.csim_timeout_s)
        validate_csynth_timeout_s(self.csynth_timeout_s)
        validate_cosim_timeout_s(self.cosim_timeout_s)
        from agrefactor.runtime.cosim_stage import validate_cosim_policy
        object.__setattr__(self, "cosim_policy", validate_cosim_policy(self.cosim_policy))
        object.__setattr__(
            self,
            "public_generation_trajectories",
            public_trajectories,
        )
        object.__setattr__(
            self,
            "hidden_generation_trajectories",
            hidden_trajectories,
        )
        object.__setattr__(
            self,
            "test_generation_trajectories",
            (
                public_trajectories
                if public_trajectories == hidden_trajectories
                else None
            ),
        )
        object.__setattr__(
            self,
            "test_generation_profile",
            profile,
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
            "max_testbench_repairs": self.max_testbench_repairs,
            "max_candidate_repairs": self.max_candidate_repairs,
            "test_generation_profile": (
                self.test_generation_profile.value
            ),
            "public_coverage_rounds": self.public_coverage_rounds,
            "hidden_coverage_rounds": self.hidden_coverage_rounds,
            "public_generation_trajectories": (
                self.public_generation_trajectories
            ),
            "hidden_generation_trajectories": (
                self.hidden_generation_trajectories
            ),
            "test_generation_trajectories": (
                self.test_generation_trajectories
            ),
            "csim_timeout_s": self.csim_timeout_s,
            "csynth_timeout_s": self.csynth_timeout_s,
            "cosim_timeout_s": self.cosim_timeout_s,
            "cosim_policy": self.cosim_policy,
        }



_GENERATION_FAILURE_SCALAR_KEYS = (
    "schema_version",
    "failure_kind",
    "terminal_class",
    "bounded",
    "split",
    "stage",
    "trajectory_count",
    "qualified_count",
    "attempt_count",
    "failure_owner",
    "next_action",
    "diagnostic_kind",
    "diagnostic_excerpt",
    "hidden_testbench_exposed_to_model",
    "retry_guidance",
)


def _raw_context_get(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:
            return None
    try:
        return value[key]  # type: ignore[index]
    except Exception:
        return None


def _extract_generation_failure_metadata(
    raw_result: object,
) -> dict[str, Any]:
    # Extract only code-free, product-safe bounded failure metadata.

    if (
        not isinstance(raw_result, tuple)
        or len(raw_result) < 2
    ):
        return {}
    context_variables = raw_result[1]
    raw_failure = _raw_context_get(
        context_variables,
        "generation_failure",
    )
    if not isinstance(raw_failure, Mapping):
        return {}

    failure: dict[str, Any] = {}
    for key in _GENERATION_FAILURE_SCALAR_KEYS:
        value = raw_failure.get(key)
        if key == "retry_guidance":
            if isinstance(value, list) and all(
                isinstance(item, str) for item in value
            ):
                failure[key] = list(value)
            continue
        if isinstance(value, (str, int, bool)) or value is None:
            failure[key] = value

    if failure.get("failure_kind") != "testbench_generation_exhausted":
        return {}
    if failure.get("bounded") is not True:
        return {}
    if failure.get("split") not in {"public", "hidden"}:
        return {}
    stage = failure.get("stage")
    if not isinstance(stage, str) or not stage:
        return {}

    diagnostic = failure.get("diagnostic_excerpt")
    if isinstance(diagnostic, str):
        failure["diagnostic_excerpt"] = diagnostic[-2000:]

    return {
        "failed_stage": stage,
        "failure_owner": failure.get("failure_owner", "unknown"),
        "next_action": failure.get(
            "next_action",
            "change_generation_strategy",
        ),
        "diagnostic_kind": failure.get(
            "diagnostic_kind",
            "generation_qualification_failed",
        ),
        "attempt_count": failure.get("attempt_count", 0),
        "trajectory_count": failure.get("trajectory_count", 0),
        "hidden_testbench_exposed_to_model": False,
        "generation_failure": failure,
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



@dataclass(frozen=True, slots=True)
class PublicTestbenchPreparationResult:
    'Safe summary plus the prepared Public Testbench source.'

    status: str
    testbench_code: str
    reason: str
    repair_attempts_used: int
    prompt_sha256: str | None
    trajectory_id: str | None
    artifact_path: str
    repair_artifact_manifest_path: str
    cost_observations: tuple[Mapping[str, Any], ...] = ()
    prompt_evidence: tuple[Mapping[str, Any], ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status == "passed"

    @property
    def changed(self) -> bool:
        return self.succeeded and self.repair_attempts_used > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "reason": self.reason,
            "repair_attempts_used": self.repair_attempts_used,
            "changed": self.changed,
            "prompt_sha256": self.prompt_sha256,
            "trajectory_id": self.trajectory_id,
            "artifact_path": self.artifact_path,
            "repair_artifact_manifest_path": (
                self.repair_artifact_manifest_path
            ),
            "cost_observation_count": len(self.cost_observations),
            "prompt_evidence_count": len(self.prompt_evidence),
            "hidden_testbench_exposed_to_model": False,
        }


def _prepare_public_testbench(
    *,
    task: TaskSpec,
    testbench_code: str,
    original_code: str,
    candidate_code: str,
    effective_model_config: EffectiveModelConfig,
    budget: BudgetManager,
    work_dir: Path,
    max_repair_attempts: int = 2,
) -> PublicTestbenchPreparationResult:
    'Preflight and, when necessary, repair one Public Testbench.'

    repairer = build_openai_compatible_testbench_repairer(
        effective_config=effective_model_config,
        budget=budget,
    )
    loop = TestbenchRepairLoop(
        preflight=TestbenchPreflight(),
        repairer=repairer,
        max_repair_attempts=max_repair_attempts,
    )
    result = loop.run(
        work_dir=work_dir,
        testbench_code=testbench_code,
        original_code=original_code,
        candidate_code=candidate_code,
        budget=budget,
        task=task,
    )

    prompt_evidence: list[dict[str, Any]] = []
    for attempt_index, observation in enumerate(
        getattr(repairer, "audit_events", ()),
        start=1,
    ):
        manifest = dict(
            getattr(observation, "prompt_manifest", {}) or {}
        )
        sequence_hash = manifest.get("message_sequence_sha256")
        template_id = manifest.get("prompt_template_id")
        template_version = manifest.get("prompt_template_version")
        if not (
            isinstance(sequence_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", sequence_hash)
            and isinstance(template_id, str)
            and template_id
            and isinstance(template_version, int)
            and not isinstance(template_version, bool)
        ):
            raise RuntimeError(
                "testbench repair audit event lacks a complete "
                "safe prompt identity"
            )
        prompt_evidence.append(
            {
                "schema_version": 1,
                "template_id": template_id,
                "template_version": template_version,
                "system_message_sha256": manifest.get(
                    "rendered_system_message_sha256"
                ),
                "invocation_sha256": manifest.get(
                    "rendered_user_message_sha256"
                ),
                "message_sequence_sha256": sequence_hash,
                "provider_call_observed": bool(
                    getattr(
                        observation,
                        "model_call_observed",
                        False,
                    )
                ),
                "metadata": {
                    "source": "testbench_repair",
                    "attempt": attempt_index,
                },
            }
        )

    prompt_sha256 = None
    trajectory_id = None
    if result.repair_attempts_used > 0:
        if len(prompt_evidence) != result.repair_attempts_used:
            raise RuntimeError(
                "testbench repair attempt count does not match "
                "retained prompt evidence"
            )
        if not all(
            item["provider_call_observed"] is True
            for item in prompt_evidence
        ):
            raise RuntimeError(
                "completed testbench repair lacks an observed model call"
            )
        prompt_sha256 = str(
            prompt_evidence[-1]["message_sequence_sha256"]
        )
        trajectory_id = f"{task.task_id}.public-testbench-repair"

    cost_observations: list[dict[str, Any]] = []
    for response in getattr(repairer, "responses", ()):
        usage = getattr(response, "usage", None)
        estimate = getattr(usage, "estimated_cost", None)
        if estimate is not None:
            cost_observations.append(estimate.to_dict())

    return PublicTestbenchPreparationResult(
        status=result.status.value,
        testbench_code=result.testbench_code,
        reason=result.reason,
        repair_attempts_used=result.repair_attempts_used,
        prompt_sha256=prompt_sha256,
        trajectory_id=trajectory_id,
        artifact_path=result.artifact_path,
        repair_artifact_manifest_path=(
            result.repair_artifact_manifest_path
        ),
        cost_observations=tuple(cost_observations),
        prompt_evidence=tuple(prompt_evidence),
    )


def _apply_repaired_public_suite(
    *,
    suites: Sequence[TestSuiteSpec],
    suite_codes: Mapping[str, str],
    public_suite: TestSuiteSpec,
    preparation: PublicTestbenchPreparationResult,
    effective_model_config: EffectiveModelConfig,
) -> tuple[
    tuple[TestSuiteSpec, ...],
    dict[str, str],
    TestSuiteSpec,
]:
    'Persist a repaired Public suite with derived provenance.'

    if public_suite.split is not EvaluationSplit.PUBLIC:
        raise ValueError("only a Public suite may be repaired")
    if not preparation.changed:
        return tuple(suites), dict(suite_codes), public_suite
    if preparation.prompt_sha256 is None:
        raise ValueError(
            "changed Public Testbench requires prompt provenance"
        )
    if public_suite.testbench_path is None:
        raise ValueError("Public suite requires a materialized path")

    target_path = Path(public_suite.testbench_path)
    normalized_code = preparation.testbench_code.rstrip() + "\n"
    _atomic_text(target_path, normalized_code)
    persisted_code = target_path.read_text(encoding="utf-8")
    digest = _sha256_file(target_path)

    derived_source = TestSourceSpec(
        source_id=f"public-derived-{digest[:16]}",
        source_revision=str(preparation.repair_attempts_used),
        source_kind=TestSourceKind.DERIVED,
        expected_content_sha256=digest,
        operator_artifact_path=str(target_path),
        generation_model=effective_model_config.model_id,
        generation_profile=(
            effective_model_config.family_profile_name
        ),
        prompt_sha256=preparation.prompt_sha256,
        trajectory_id=preparation.trajectory_id,
        round_index=preparation.repair_attempts_used,
    )
    replacement = TestSuiteSpec(
        suite_id=public_suite.suite_id,
        suite_version=(
            f"{public_suite.suite_version or '1'}"
            f"+tb-repair-{preparation.repair_attempts_used}"
        ),
        split=EvaluationSplit.PUBLIC,
        case_count=public_suite.case_count,
        testbench_path=str(target_path),
        source=derived_source,
    )

    updated_suites = tuple(
        replacement if suite.suite_id == public_suite.suite_id else suite
        for suite in suites
    )
    updated_codes = dict(suite_codes)
    updated_codes[public_suite.suite_id] = persisted_code
    return updated_suites, updated_codes, replacement


class SourceCommandRejected(ValueError):
    """Structured pre-launch rejection with persisted product artifacts."""

    def __init__(
        self,
        message: str,
        *,
        artifact_root: Path,
        rejection: Mapping[str, Any],
    ) -> None:
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.rejection = dict(rejection)
        super().__init__(message)


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
        self._public_testbench_cost_observations: tuple[
            Mapping[str, Any], ...
        ] = ()
        self._public_testbench_prompt_evidence: tuple[
            Mapping[str, Any], ...
        ] = ()
        self._accepted_optimization_material = None

    @property
    def accepted_optimization_material(self):
        return self._accepted_optimization_material

    def __call__(self, context: RunContext) -> PhaseResult:
        if self._request.mode not in {RunMode.REFACTOR, RunMode.FULL}:
            raise ValueError(
                "source bootstrap execution supports refactor/full only; "
                "direct optimize uses the Stage 3 product adapter"
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
            raw_result = getattr(
                self._generation_adapter,
                "last_raw_result",
                None,
            )
            failure_metadata = _extract_generation_failure_metadata(
                raw_result
            )
            failure_payload = failure_metadata.get(
                "generation_failure",
                {},
            )
            failure_reason = (
                failure_payload.get("diagnostic_excerpt")
                if isinstance(failure_payload, Mapping)
                else None
            )
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
            metadata = {
                "source_bootstrap": True,
                "generation_only": True,
                "formal_validation_started": False,
                "execution_identity": identity_summary,
            }
            metadata.update(failure_metadata)
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=generation_result.status,
                summary=(
                    "Initial generation failed before formal validation: "
                    + (
                        str(failure_reason)
                        if failure_reason
                        else (
                            generation_result.summary
                            or "unknown failure"
                        )
                    )
                ),
                metadata=metadata,
            )

        raw_result = getattr(
            self._generation_adapter,
            "last_raw_result",
            None,
        )
        generated = self._extract_generation_result(raw_result)
        model_data_boundary = generated["model_data_boundary"]
        hidden_testbench_exposed_to_model = (
            _hidden_exposure_from_boundary(model_data_boundary)
        )
        if hidden_testbench_exposed_to_model:
            raise ValueError(
                "generation returned incomplete or exposed Hidden boundary evidence"
            )
        _atomic_json(
            bootstrap_root / "model_data_boundary.json",
            model_data_boundary,
        )
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
        identity_task = _product_identity_task(formal_task, self._request.mode)

        public_preparation = None
        if (
            self._request.test_source_plan.public.mode
            is TestSourceSelectionMode.AUTO
        ):
            context.trace.record(
                "public_testbench.preparation.started",
                phase="preflight",
                status="running",
                metadata={
                    "suite_id": preflight_suite.suite_id,
                    "max_repair_attempts": self._request.max_testbench_repairs,
                    "hidden_testbench_exposed_to_model": False,
                },
            )
            public_preparation = _prepare_public_testbench(
                task=formal_task,
                testbench_code=preflight_code,
                original_code=original_code,
                candidate_code=candidate_code,
                effective_model_config=(
                    self._request.effective_model_config
                ),
                budget=context.budget,
                work_dir=(
                    bootstrap_root / "public_testbench_repair"
                ),
                max_repair_attempts=self._request.max_testbench_repairs,
            )
            self._public_testbench_cost_observations = (
                public_preparation.cost_observations
            )
            self._public_testbench_prompt_evidence = (
                public_preparation.prompt_evidence
            )
            _atomic_json(
                bootstrap_root
                / "public_testbench_preparation.json",
                public_preparation.to_dict(),
            )
            context.trace.record(
                "public_testbench.preparation.finished",
                phase="preflight",
                status=(
                    "passed"
                    if public_preparation.succeeded
                    else "failed"
                ),
                metadata=public_preparation.to_dict(),
            )
            if not public_preparation.succeeded:
                identity_summary = self._write_execution_identity(
                    context=context,
                    normalized_task=identity_task,
                    suites=suites,
                    execution_status="failed",
                    initial_candidate_path=(
                        bootstrap_root / "initial_candidate.cpp"
                    ),
                    final_candidate_path=(
                        bootstrap_root / "initial_candidate.cpp"
                    ),
                    require_accepted_ready=False,
                    hard_budget_exhaustion=None,
                )
                return PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.FAILED,
                    summary=(
                        "Auto-generated Public Testbench could not "
                        "be prepared for formal validation: "
                        + public_preparation.reason
                    ),
                    metadata={
                        "source_bootstrap": True,
                        "generation_only": True,
                        "formal_validation_started": False,
                        "failed_stage": (
                            "public_testbench_preparation"
                        ),
                        "public_testbench_repair_status": (
                            public_preparation.status
                        ),
                        "public_testbench_repair_attempts": (
                            public_preparation.repair_attempts_used
                        ),
                        "hidden_testbench_exposed_to_model": (
                            hidden_testbench_exposed_to_model
                        ),
                        "execution_identity": identity_summary,
                    },
                )
            if public_preparation.changed:
                (
                    suites,
                    suite_codes,
                    preflight_suite,
                ) = _apply_repaired_public_suite(
                    suites=suites,
                    suite_codes=suite_codes,
                    public_suite=preflight_suite,
                    preparation=public_preparation,
                    effective_model_config=(
                        self._request.effective_model_config
                    ),
                )
                public_suites = tuple(
                    suite
                    for suite in suites
                    if suite.split is EvaluationSplit.PUBLIC
                )
                preflight_code = suite_codes[
                    preflight_suite.suite_id
                ]
                formal_task = TaskSpec(
                    task_id=f"{self._request.run_id}.formal",
                    kernel_path=str(self._request.source_path),
                    kernel_name=candidate_top,
                    target=self._request.target,
                    mode=RunMode.REFACTOR,
                    testbench_path=(
                        preflight_suite.testbench_path
                    ),
                    test_suites=suites,
                )
                identity_task = _product_identity_task(formal_task, self._request.mode)

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
            reference_top_function=(
                self._request.top_function
            ),
            candidate_top_function=candidate_top,
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
                "public_testbench_preparation": (
                    None
                    if public_preparation is None
                    else public_preparation.to_dict()
                ),
                "shared_budget": True,
                "shared_trace": True,
                "model_data_boundary_path": "model_data_boundary.json",
                "model_data_boundary_sha256": _sha256_file(
                    bootstrap_root / "model_data_boundary.json"
                ),
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
            normalized_task=identity_task,
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
        if accepted:
            from .stage3_optimizer import AcceptedOptimizationMaterial

            self._accepted_optimization_material = AcceptedOptimizationMaterial(
                baseline_source_path=final_candidate_path,
                reference_source_path=self._request.source_path,
                top_function=candidate_top,
                reference_top_function=self._request.top_function,
                target=self._request.target,
                suites=tuple(suites),
                suite_codes=dict(suite_codes),
                preflight_suite_id=preflight_suite.suite_id,
                provenance={
                    "kind": "accepted_refactor_handoff",
                    "formal_task_id": formal_task.task_id,
                    "source_run_id": context.run_id,
                    "execution_identity": identity_summary,
                },
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
                "test_generation_profile": (
                    self._request.test_generation_profile.value
                ),
                "public_coverage_rounds": (
                    self._request.public_coverage_rounds
                ),
                "hidden_coverage_rounds": (
                    self._request.hidden_coverage_rounds
                ),
                "public_generation_trajectories": (
                    self._request.public_generation_trajectories
                ),
                "hidden_generation_trajectories": (
                    self._request.hidden_generation_trajectories
                ),
                "test_generation_trajectories": (
                    self._request.test_generation_trajectories
                ),
                "csim_timeout_s": self._request.csim_timeout_s,
                "csynth_timeout_s": self._request.csynth_timeout_s,
                "cosim_timeout_s": self._request.cosim_timeout_s,
                "cosim_policy": self._request.cosim_policy,
                "public_suite_count": len(public_suites),
                "hidden_suite_count": len(suites) - len(public_suites),
                "public_testbench_repair_status": (
                    None
                    if public_preparation is None
                    else public_preparation.status
                ),
                "public_testbench_repair_attempts": (
                    0
                    if public_preparation is None
                    else public_preparation.repair_attempts_used
                ),
                "hidden_testbench_exposed_to_model": (
                    hidden_testbench_exposed_to_model
                ),
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
        for raw_evidence in self._public_testbench_prompt_evidence:
            evidence = dict(raw_evidence)
            evidence["call_index"] = len(calls) + 1
            calls.append(evidence)

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
        cosim_observed: dict[tuple[str, str], Mapping[str, Any]] = {}
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
        for path in sorted(
            self._layout.work_root.rglob(
                "cosim_suite_identity_evidence.json"
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
                cosim_observed[(split, suite_id)] = payload
        result: list[dict[str, Any]] = []
        for suite in suites:
            manifest = suite.to_dict()
            manifest["public_rtl_cosim_required"] = (
                suite.split is EvaluationSplit.PUBLIC
                and self._request.cosim_policy == "required"
            )
            manifest["public_rtl_cosim_status"] = (
                "skipped"
                if suite.split is EvaluationSplit.PUBLIC
                and self._request.cosim_policy == "off"
                else None
            )
            manifest["public_rtl_cosim_evidence_sha256"] = None
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
            cosim_evidence = cosim_observed.get(
                (suite.split.value, suite.suite_id)
            )
            if cosim_evidence is not None:
                manifest["public_rtl_cosim_required"] = (
                    cosim_evidence.get("policy") == "required"
                )
                manifest["public_rtl_cosim_status"] = (
                    cosim_evidence.get("evaluation_status")
                )
                manifest["public_rtl_cosim_evidence_sha256"] = (
                    cosim_evidence.get("evidence_sha256")
                )
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

        for index, estimate in enumerate(
            self._public_testbench_cost_observations,
            start=1,
        ):
            add_estimate(
                estimate,
                f"testbench_repair:{index}",
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
    def _extract_generation_result(raw_result: object) -> dict[str, Any]:
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
        public_hls_decl = _mapping_get(
            context_variables,
            "public_hls_decl_verbatim",
        )
        public_hls_decl_sha256 = _mapping_get(
            context_variables,
            "public_hls_decl_sha256",
        )
        model_data_boundary = _mapping_get(
            context_variables,
            "model_data_boundary",
        )
        for name, value in (
            ("candidate_code", candidate),
            ("candidate_top", candidate_top),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"generation-only backend returned no {name}"
                )
        if not isinstance(public_hls_decl, str) or not public_hls_decl.strip():
            raise ValueError(
                "generation-only backend returned no frozen Public ABI"
            )
        if not isinstance(public_hls_decl_sha256, str) or not public_hls_decl_sha256.strip():
            raise ValueError(
                "generation-only backend returned no Public ABI hash"
            )
        if _sha256_text(public_hls_decl) != public_hls_decl_sha256:
            raise ValueError("Public ABI hash does not match declaration")
        if not isinstance(model_data_boundary, Mapping):
            raise ValueError(
                "generation-only backend returned no model-data boundary"
            )
        copied_boundary = json.loads(
            json.dumps(dict(model_data_boundary), sort_keys=True)
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
            "public_hls_decl": public_hls_decl,
            "public_hls_decl_sha256": public_hls_decl_sha256,
            "model_data_boundary": copied_boundary,
        }

    def _materialize_suites(
        self,
        *,
        bootstrap_root: Path,
        generated: Mapping[str, Any],
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
    replacement_flags = list(getattr(args, "compile_flags", ()) or ())
    deprecated_flags = list(
        getattr(args, "deprecated_compile_flags", ()) or ()
    )
    if replacement_flags and deprecated_flags:
        raise ValueError(
            "--replace-compile-flag and deprecated --compile-flag "
            "cannot be combined"
        )
    selected_flags = replacement_flags or deprecated_flags
    if selected_flags:
        overrides["compile_flags"] = selected_flags
    return resolve_target_profile(overrides)


def _reasoning_effort_from_cli(args) -> str | None:
    """Keep the implicit medium default from blocking generic endpoints."""

    value = getattr(args, "reasoning_effort", None)
    if getattr(args, "reasoning_effort_explicit", False):
        return value
    family = (
        args.model_family
        if isinstance(getattr(args, "model_family", None), str)
        and args.model_family.strip()
        else infer_model_family(args.model)
    )
    if family.strip().casefold() in {
        "default",
        "generic-openai-compatible",
        "openai",
    }:
        return None
    return value


def _generation_trajectories_from_cli(args) -> tuple[int, int]:
    shared = getattr(
        args,
        "test_generation_trajectories",
        DEFAULT_TEST_GENERATION_TRAJECTORIES,
    )
    shared_explicit = bool(
        getattr(args, "test_generation_trajectories_explicit", False)
    )
    public_explicit = bool(
        getattr(args, "public_generation_trajectories_explicit", False)
    )
    hidden_explicit = bool(
        getattr(args, "hidden_generation_trajectories_explicit", False)
    )
    if shared_explicit:
        if public_explicit or hidden_explicit:
            raise ValueError(
                "deprecated --test-generation-trajectories cannot be "
                "combined with independent Public/Hidden trajectory options"
            )
        return shared, shared
    return (
        args.public_generation_trajectories,
        args.hidden_generation_trajectories,
    )


def _budget_from_cli(args, selection: ModelRuntimeSelection) -> EffectiveRunBudget:
    requested = {
        name: getattr(args, name, None)
        for name in (
            "max_llm_calls",
            "max_tool_calls",
            "max_compile_calls",
            "max_csim_calls",
            "max_csynth_calls",
            "max_cosim_calls",
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
        cost_budget_currency=(
            currency
            if args.cost_budget is not None
            else None
        ),
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
            name: getattr(args, name, None)
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
        requested = getattr(args, name, None)
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
    write_rejection_support_artifacts(
        layout.artifact_root,
        rejection=rejection,
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


def _build_generation_settings(
    request: SourceBootstrapRequest,
    layout: SourceRunLayout,
    *,
    debug: bool,
) -> LegacyRefactorSettings:
    """Map the typed product profile to the existing generation backend."""

    profile = request.test_generation_profile
    enhanced = (
        profile is TestGenerationProfile.COVERAGE_ENHANCED
    )
    public_auto = (
        request.test_source_plan.public.mode
        is TestSourceSelectionMode.AUTO
    )
    hidden_auto = (
        request.test_source_plan.hidden.mode
        is TestSourceSelectionMode.AUTO
    )
    return LegacyRefactorSettings(
        effective_model_config=request.effective_model_config,
        output_dir=str(layout.work_root / "generation"),
        max_retry_attempts=0,
        debug=debug,
        generation_only=True,
        external_kernel_name=f"{request.top_function}_hls",
        test_generation_profile=profile.value,
        enable_tb_coverage_loop=public_auto and enhanced,
        public_tb_rounds=(
            request.public_coverage_rounds if enhanced else 1
        ),
        public_tb_trajectories=(
            request.public_generation_trajectories if enhanced else 1
        ),
        public_tb_target=80.0,
        enable_hidden_tb_eval=hidden_auto,
        hidden_tb_rounds=(
            request.hidden_coverage_rounds if enhanced else 1
        ),
        hidden_tb_trajectories=(
            request.hidden_generation_trajectories if enhanced else 1
        ),
        hidden_tb_target=90.0,
    )



def _product_identity_task(formal_task: TaskSpec, requested_mode: RunMode) -> TaskSpec:
    """Keep refactor validation internal while root identity reflects full mode."""

    if not isinstance(formal_task, TaskSpec):
        raise TypeError("formal_task must be TaskSpec")
    mode = requested_mode if isinstance(requested_mode, RunMode) else RunMode(requested_mode)
    if mode is RunMode.REFACTOR:
        return formal_task
    if mode is not RunMode.FULL:
        raise ValueError("source bootstrap root identity supports refactor/full only")
    return TaskSpec(
        task_id=formal_task.task_id,
        kernel_path=formal_task.kernel_path,
        kernel_name=formal_task.kernel_name,
        target=formal_task.target,
        mode=RunMode.FULL,
        testbench_path=formal_task.testbench_path,
        test_suites=formal_task.test_suites,
    )

def run_source_command(
    args,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> SourceBootstrapRunResult:
    """Execute one normal source command using internally managed paths."""

    mode = RunMode(args.command)

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
        reasoning_effort=_reasoning_effort_from_cli(args),
    )
    plan = build_test_source_plan(
        public_mode=args.public_tests,
        public_paths=args.public_tests_provided,
        hidden_mode=args.hidden_tests,
        hidden_paths=args.hidden_tests_provided,
    )
    target = _target_from_cli(args)
    public_trajectories, hidden_trajectories = (
        _generation_trajectories_from_cli(args)
    )
    layout = SourceRunLayout.create(
        run_id,
        artifact_root_override=getattr(args, "output_dir", None),
    )
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
        raise SourceCommandRejected(
            f"{rejection['resource']}={rejection['user_requested']} "
            f"exceeds system safety ceiling "
            f"{rejection['system_safety_ceiling']}; "
            f"rejection artifacts: {layout.artifact_root}",
            artifact_root=layout.artifact_root,
            rejection=rejection,
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
        max_candidate_repairs=getattr(
            args,
            "max_candidate_repairs",
            DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
        ),
        run_id=run_id,
        max_testbench_repairs=getattr(
            args,
            "max_testbench_repairs",
            DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
        ),
        test_generation_profile=(
            resolve_test_generation_profile(
                getattr(
                    args,
                    "test_generation_profile",
                    TestGenerationProfile.LIGHTWEIGHT.value,
                )
            )
        ),
        public_coverage_rounds=getattr(
            args,
            "public_coverage_rounds",
            DEFAULT_PUBLIC_COVERAGE_ROUNDS,
        ),
        hidden_coverage_rounds=getattr(
            args,
            "hidden_coverage_rounds",
            DEFAULT_HIDDEN_COVERAGE_ROUNDS,
        ),
        public_generation_trajectories=public_trajectories,
        hidden_generation_trajectories=hidden_trajectories,
        csim_timeout_s=getattr(
            args,
            "csim_timeout_s",
            DEFAULT_CSIM_TIMEOUT_S,
        ),
        csynth_timeout_s=getattr(
            args,
            "csynth_timeout_s",
            DEFAULT_CSYNTH_TIMEOUT_S,
        ),
        cosim_timeout_s=getattr(
            args,
            "cosim_timeout_s",
            DEFAULT_COSIM_TIMEOUT_S,
        ),
        cosim_policy=getattr(args, "cosim_policy", "required"),
        require_complete_execution_identity=True,
    )

    phase = None
    generation_settings = None
    handlers: dict[RunPhase, Callable[[RunContext], PhaseResult]] = {}
    direct_material = None

    if mode in {RunMode.REFACTOR, RunMode.FULL}:
        generation_settings = _build_generation_settings(
            request,
            layout,
            debug=bool(getattr(args, "debug", False)),
        )
        generation_adapter = LegacyRefactorAdapter(generation_settings)
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
                csynth_timelimit=request.csynth_timeout_s,
                csim_timelimit=request.csim_timeout_s,
                cosim_timelimit=request.cosim_timeout_s,
                cosim_policy=request.cosim_policy,
            )

        phase = SourceBootstrapPhase(
            request=request,
            layout=layout,
            generation_adapter=generation_adapter,
            formal_phase_builder=formal_builder,
        )
        handlers[RunPhase.REFACTOR] = phase
    else:
        from .stage3_optimizer import build_direct_optimization_material

        direct_material = build_direct_optimization_material(
            source_path=source,
            top_function=request.top_function,
            reference_source_path=getattr(args, "reference_source", None),
            reference_top_function=getattr(args, "reference_top", None),
            public_test_paths=args.public_tests_provided,
            hidden_test_paths=args.hidden_tests_provided,
            target=target,
        )

    if mode in {RunMode.OPTIMIZE, RunMode.FULL}:
        from .stage3_optimizer import (
            ProductOptimizerRequest,
            Stage3ProductOptimizationPhase,
        )

        handlers[RunPhase.OPTIMIZE] = Stage3ProductOptimizationPhase(
            ProductOptimizerRequest(
                run_id=run_id,
                mode=mode,
                registry=model_runtime.registry,
                effective_model_config=model_runtime.effective_config,
                budget_contract=budget,
                artifact_root=layout.artifact_root,
                work_root=layout.work_root,
                csim_timeout_s=request.csim_timeout_s,
                csynth_timeout_s=request.csynth_timeout_s,
                cosim_timeout_s=request.cosim_timeout_s,
                cosim_policy=request.cosim_policy,
                optimizer_profile=getattr(args, "optimizer_profile", "safe-v1"),
                optimization_objective=getattr(args, "optimization_objective", "latency"),
                direct_material=direct_material,
                refactor_phase=phase,
            )
        )

    source_task = TaskSpec(
        task_id=f"{run_id}.source",
        kernel_path=str(source),
        kernel_name=request.top_function,
        target=target,
        mode=mode,
    )
    runner = UnifiedRunner(
        handlers,
        budget_limits=budget.to_budget_limits(),
    )
    with capture_product_streams(
        layout.work_root,
        stdout=stdout,
        stderr=stderr,
        tee_debug=bool(getattr(args, "debug", False)),
    ) as captured:
        result = runner.run(
            source_task,
            run_id=run_id,
            trace_path=layout.artifact_root / "trace.jsonl",
            artifact_root=layout.artifact_root,
            run_metadata={
                "execution_mode": (
                    "source_bootstrap" if mode is RunMode.REFACTOR else "stage3_product"
                ),
                "legacy_mode": False,
                "model_selection": "user_fixed",
                "model_defaults_source": (
                    model_runtime.defaults_source
                ),
                "artifact_root": str(layout.artifact_root),
                "work_root": str(layout.work_root),
                "test_source_mode": plan.overall_mode.value,
                "test_generation_profile": (
                    request.test_generation_profile.value
                ),
                "test_generation_settings": {
                    "public_coverage_rounds": (
                        request.public_coverage_rounds
                    ),
                    "hidden_coverage_rounds": (
                        request.hidden_coverage_rounds
                    ),
                    "public_generation_trajectories": (
                        request.public_generation_trajectories
                    ),
                    "hidden_generation_trajectories": (
                        request.hidden_generation_trajectories
                    ),
                    "test_generation_trajectories": (
                        request.test_generation_trajectories
                    ),
                    "csim_timeout_s": request.csim_timeout_s,
                    "csynth_timeout_s": request.csynth_timeout_s,
                    "cosim_timeout_s": request.cosim_timeout_s,
                    "cosim_policy": request.cosim_policy,
                    "public_coverage_enabled": (
                        False if generation_settings is None
                        else generation_settings.enable_tb_coverage_loop
                    ),
                    "effective_public_rounds": (
                        None if generation_settings is None
                        else generation_settings.public_tb_rounds
                    ),
                    "effective_public_trajectories": (
                        None if generation_settings is None
                        else generation_settings.public_tb_trajectories
                    ),
                    "effective_hidden_rounds": (
                        None if generation_settings is None
                        else generation_settings.hidden_tb_rounds
                    ),
                    "effective_hidden_trajectories": (
                        None if generation_settings is None
                        else generation_settings.hidden_tb_trajectories
                    ),
                },
                "budget_contract": budget.to_dict(),
                "pre_stage3_step": (
                    "P0 Step C dual generation profiles"
                    if mode is RunMode.REFACTOR
                    else None
                ),
                "stage3_product_adapter": mode in {RunMode.OPTIMIZE, RunMode.FULL},
            },
        )
    finalize_product_artifacts(
        result,
        artifact_root=layout.artifact_root,
        work_root=layout.work_root,
        captured=captured,
    )
    return SourceBootstrapRunResult(
        result=result,
        layout=layout,
    )
