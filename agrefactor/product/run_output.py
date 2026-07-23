"""Stable product summaries, output levels, and captured run evidence."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, TextIO

from agrefactor.runtime import RunResult


PRODUCT_RUN_SUMMARY_SCHEMA_VERSION = 1
_PRODUCT_ARTIFACT_SCHEMA_VERSION = 1
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
    }
)
_HARD_USAGE_FIELDS = (
    ("LLM calls", "llm_calls", "max_llm_calls"),
    ("Tool calls", "tool_calls", "max_tool_calls"),
    ("Compile calls", "compile_calls", "max_compile_calls"),
    ("CSIM calls", "csim_calls", "max_csim_calls"),
    ("CSYNTH calls", "csynth_calls", "max_csynth_calls"),
)


class ProductOutputMode(str, Enum):
    DEFAULT = "default"
    JSON = "json"
    VERBOSE = "verbose"
    DEBUG = "debug"


@dataclass(frozen=True, slots=True)
class CapturedProductStreams:
    stdout_path: Path
    stderr_path: Path


class _TeeTextIO(io.TextIOBase):
    def __init__(self, primary: TextIO, secondary: TextIO) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def encoding(self) -> str:
        return getattr(self._secondary, "encoding", None) or "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        self._primary.write(value)
        self._secondary.write(value)
        return len(value)

    def flush(self) -> None:
        for stream in (self._primary, self._secondary):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        method = getattr(self._secondary, "isatty", None)
        return bool(method()) if callable(method) else False

    def fileno(self) -> int:
        method = getattr(self._secondary, "fileno", None)
        if callable(method):
            try:
                return int(method())
            except (OSError, ValueError):
                pass
        return int(self._primary.fileno())


@contextmanager
def capture_product_streams(
    work_root: str | os.PathLike[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    tee_debug: bool = False,
) -> Iterator[CapturedProductStreams]:
    """Capture Legacy/tool streams; debug mode also tees them to the terminal."""

    root = Path(work_root).expanduser() / "product_output_capture"
    root.mkdir(parents=True, exist_ok=True)
    stdout_path = root / "stdout.log"
    stderr_path = root / "stderr.log"
    terminal_stdout = stdout if stdout is not None else sys.stdout
    terminal_stderr = stderr if stderr is not None else sys.stderr
    with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as err_handle:
        redirected_out: TextIO = (
            _TeeTextIO(out_handle, terminal_stdout) if tee_debug else out_handle
        )
        redirected_err: TextIO = (
            _TeeTextIO(err_handle, terminal_stderr) if tee_debug else err_handle
        )
        with redirect_stdout(redirected_out), redirect_stderr(redirected_err):
            yield CapturedProductStreams(
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )


def resolve_output_mode(args: object) -> ProductOutputMode:
    selected = [
        mode
        for enabled, mode in (
            (bool(getattr(args, "json_output", False)), ProductOutputMode.JSON),
            (bool(getattr(args, "verbose", False)), ProductOutputMode.VERBOSE),
            (bool(getattr(args, "debug", False)), ProductOutputMode.DEBUG),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise ValueError("--json, --verbose, and --debug are mutually exclusive")
    return selected[0] if selected else ProductOutputMode.DEFAULT


def build_product_summary(
    result: RunResult,
    *,
    artifact_root: str | os.PathLike[str],
) -> dict[str, Any]:
    if not isinstance(result, RunResult):
        raise TypeError("result must be a RunResult")
    root = Path(artifact_root).expanduser().resolve()
    identity = _read_json(root / "execution_identity.json")
    phase = result.phases[-1] if result.phases else None
    phase_metadata = {} if phase is None else dict(phase.metadata)
    accepted = bool(result.succeeded and phase_metadata.get("accepted") is True)
    status = (
        "accepted"
        if accepted
        else ("rejected" if result.status.value == "failed" else "error")
    )
    suites = identity.get("suites", [])
    public_status = _suite_status(suites, "public")
    hidden_status = _suite_status(suites, "hidden")
    failed_stage = None if accepted else _failed_stage(identity, phase_metadata)
    candidate_path = root / "refactor" / "final_candidate.cpp"
    if not candidate_path.is_file():
        candidate_path = root / "bootstrap" / "initial_candidate.cpp"
    budget = _summary_budget(identity)
    pricing = _summary_pricing(identity)
    repairs = {
        "used": _nonnegative_int(phase_metadata.get("repair_attempt_count")),
        "limit": _repair_limit(root),
    }
    phases = [
        {
            "phase": item.phase.value,
            "status": item.status.value,
            "summary": item.summary,
        }
        for item in result.phases
    ]
    payload = {
        "schema_version": PRODUCT_RUN_SUMMARY_SCHEMA_VERSION,
        "status": status,
        "mode": result.mode.value,
        "kernel": _identity_top(identity),
        "candidate": str(candidate_path) if candidate_path.is_file() else None,
        "validation": {
            "csynth": _csynth_status(
                accepted=accepted,
                failed_stage=failed_stage,
                public_status=public_status,
                hidden_status=hidden_status,
            ),
            "public": public_status,
            "hidden": hidden_status,
        },
        "repairs": repairs,
        "failed_stage": failed_stage,
        "reason": None if accepted or phase is None else phase.summary,
        "artifacts": {
            "root": str(root),
            "details": str(root / "full_result.json"),
            "manifest": str(root / "run_artifact_manifest.json"),
        },
        **budget,
        "pricing": pricing,
        "cost_estimation_quality": pricing.get(
            "cost_estimation_quality", "unavailable"
        ),
        "execution_identity": _safe_execution_identity(identity),
        "phases": phases,
    }
    _assert_summary_safe(payload)
    return payload


def build_rejection_summary(
    artifact_root: str | os.PathLike[str],
) -> dict[str, Any]:
    root = Path(artifact_root).expanduser().resolve()
    identity = _read_json(root / "execution_identity.json")
    rejection = _read_json(root / "request_rejection.json")
    budget = _mapping_path(identity, "budget", "contract")
    if not isinstance(budget, Mapping):
        budget = {}
    pricing = _summary_pricing(identity)
    payload = {
        "schema_version": PRODUCT_RUN_SUMMARY_SCHEMA_VERSION,
        "status": "rejected",
        "mode": _mapping_path(identity, "normalized_task", "value", "mode")
        or "refactor",
        "kernel": _identity_top(identity),
        "candidate": None,
        "validation": {
            "csynth": "not_run",
            "public": "not_run",
            "hidden": "not_run",
        },
        "repairs": {"used": 0, "limit": None},
        "failed_stage": "request",
        "reason": (
            f"{rejection.get('resource')}={rejection.get('user_requested')} "
            f"exceeds system safety ceiling "
            f"{rejection.get('system_safety_ceiling')}"
        ),
        "artifacts": {
            "root": str(root),
            "details": str(root / "full_result.json"),
            "manifest": str(root / "run_artifact_manifest.json"),
        },
        "system_defaults": dict(budget.get("system_defaults", {})),
        "system_safety_ceilings": dict(
            budget.get("system_safety_ceilings", {})
        ),
        "user_requested": dict(budget.get("user_requested", {})),
        "effective_hard_limits": None,
        "soft_budgets": dict(budget.get("soft_usage_budgets", {})),
        "usage": None,
        "remaining": None,
        "hard_budget_exhausted": {
            "resource": rejection.get("resource"),
            "stage": "request",
            "kind": rejection.get("kind"),
        },
        "soft_budget_exceeded": {"tokens": False, "cost": False},
        "pricing": pricing,
        "cost_estimation_quality": pricing.get(
            "cost_estimation_quality", "unavailable"
        ),
        "execution_identity": _safe_execution_identity(identity),
        "phases": [],
    }
    _assert_summary_safe(payload)
    return payload


def render_product_output(
    summary: Mapping[str, Any],
    *,
    mode: ProductOutputMode | str,
    stdout: TextIO,
) -> None:
    resolved = mode if isinstance(mode, ProductOutputMode) else ProductOutputMode(mode)
    if resolved is ProductOutputMode.JSON:
        stdout.write(
            json.dumps(
                dict(summary),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        return
    stdout.write(_render_human(summary, include_phases=resolved in {
        ProductOutputMode.VERBOSE,
        ProductOutputMode.DEBUG,
    }))


def finalize_product_artifacts(
    result: RunResult,
    *,
    artifact_root: str | os.PathLike[str],
    work_root: str | os.PathLike[str],
    captured: CapturedProductStreams,
) -> None:
    root = Path(artifact_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(captured.stdout_path, root / "stdout.log")
    shutil.copyfile(captured.stderr_path, root / "stderr.log")
    _atomic_json(root / "full_result.json", result.to_dict())
    identity = _read_json(root / "execution_identity.json")
    _atomic_json(root / "model_calls.json", _model_calls_payload(identity))
    _atomic_json(
        root / "tool_calls.json",
        _tool_calls_payload(
            artifact_root=root,
            work_root=Path(work_root).expanduser().resolve(),
        ),
    )
    _refresh_manifest(root)


def write_rejection_support_artifacts(
    artifact_root: str | os.PathLike[str],
    *,
    rejection: Mapping[str, Any],
) -> None:
    root = Path(artifact_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "stdout.log").write_text("", encoding="utf-8")
    (root / "stderr.log").write_text("", encoding="utf-8")
    _atomic_json(
        root / "full_result.json",
        {
            "schema_version": 1,
            "status": "request_rejected",
            "request_rejection": dict(rejection),
        },
    )
    identity_path = root / "execution_identity.json"
    identity = _read_json(identity_path) if identity_path.is_file() else {}
    _atomic_json(root / "model_calls.json", _model_calls_payload(identity))
    _atomic_json(
        root / "tool_calls.json",
        {
            "schema_version": _PRODUCT_ARTIFACT_SCHEMA_VERSION,
            "evidence_view": "operator_full",
            "calls": [],
        },
    )


def _render_human(
    summary: Mapping[str, Any],
    *,
    include_phases: bool,
) -> str:
    lines = [f"Status: {summary.get('status')}"]
    lines.append(f"Mode: {summary.get('mode')}")
    lines.append(f"Kernel: {summary.get('kernel')}")
    if summary.get("candidate"):
        lines.append(f"Candidate: {summary['candidate']}")
    validation = summary.get("validation", {})
    if isinstance(validation, Mapping):
        lines.extend(
            [
                f"CSYNTH: {validation.get('csynth', 'unknown')}",
                f"Public tests: {validation.get('public', 'not_run')}",
                f"Hidden tests: {validation.get('hidden', 'not_run')}",
            ]
        )
    repairs = summary.get("repairs", {})
    if isinstance(repairs, Mapping):
        used = repairs.get("used", 0)
        limit = repairs.get("limit")
        lines.append(f"Repairs: {used}" + ("" if limit is None else f"/{limit}"))
    if summary.get("failed_stage"):
        lines.append(f"Failed stage: {summary['failed_stage']}")
    if summary.get("reason"):
        lines.append(f"Reason: {summary['reason']}")
    artifacts = summary.get("artifacts", {})
    if isinstance(artifacts, Mapping):
        lines.append(f"Artifacts: {artifacts.get('root')}")
        if summary.get("status") != "accepted":
            lines.append(f"Details: {artifacts.get('details')}")
    lines.append("")
    lines.append("Usage:")
    usage = summary.get("usage")
    usage_map = usage if isinstance(usage, Mapping) else {}
    soft = summary.get("soft_budgets")
    soft_map = soft if isinstance(soft, Mapping) else {}
    effective = summary.get("effective_hard_limits")
    effective_map = effective if isinstance(effective, Mapping) else {}
    tokens = usage_map.get("tokens", 0)
    token_budget = soft_map.get("token_budget")
    token_text = f"  Tokens: {_number(tokens)}"
    if token_budget is not None:
        token_text += f" / {_number(token_budget)}"
    token_text += " (soft, observed only)"
    if _mapping_path(summary, "soft_budget_exceeded", "tokens") is True:
        token_text += " [soft budget exceeded]"
    lines.append(token_text)
    for label, usage_name, limit_name in _HARD_USAGE_FIELDS:
        actual = usage_map.get(usage_name, 0)
        limit = effective_map.get(limit_name)
        text = f"  {label}: {_number(actual)}"
        if limit is not None:
            text += f" / {_number(limit)}"
        lines.append(text + " (hard)")
    lines.append(_cost_line(summary))
    elapsed = usage_map.get("elapsed_s", 0)
    wall_limit = effective_map.get("max_wall_time_s")
    wall_text = f"  Wall time: {_duration(elapsed)}"
    if wall_limit is not None:
        wall_text += f" / {_duration(wall_limit)}"
    lines.append(wall_text + " (hard)")
    if include_phases:
        lines.append("")
        lines.append("Phases:")
        for phase in summary.get("phases", []):
            if not isinstance(phase, Mapping):
                continue
            text = f"  {phase.get('phase')}: {phase.get('status')}"
            if phase.get("summary"):
                text += f" — {phase.get('summary')}"
            lines.append(text)
    return "\n".join(lines) + "\n"


def _cost_line(summary: Mapping[str, Any]) -> str:
    quality = str(summary.get("cost_estimation_quality", "unavailable"))
    pricing = summary.get("pricing", {})
    pricing_map = pricing if isinstance(pricing, Mapping) else {}
    actual = pricing_map.get("actual_estimation", {})
    actual_map = actual if isinstance(actual, Mapping) else {}
    amounts = actual_map.get("amounts_by_currency", {})
    if quality == "unavailable" or not isinstance(amounts, Mapping) or not amounts:
        return "  Estimated cost: unavailable (soft, observed only)"
    pieces = [f"{amount} {currency}" for currency, amount in sorted(amounts.items())]
    text = "  Estimated cost: " + ", ".join(pieces)
    soft = summary.get("soft_budgets", {})
    soft_map = soft if isinstance(soft, Mapping) else {}
    budget = soft_map.get("cost_budget")
    currency = soft_map.get("currency")
    if budget is not None and isinstance(currency, str) and len(pieces) == 1:
        text += f" / {budget} {currency}"
    text += f" (soft, observed only, {quality})"
    if _mapping_path(summary, "soft_budget_exceeded", "cost") is True:
        text += " [soft budget exceeded]"
    return text


def _summary_budget(identity: Mapping[str, Any]) -> dict[str, Any]:
    budget = identity.get("budget", {})
    budget_map = budget if isinstance(budget, Mapping) else {}
    contract = budget_map.get("contract", {})
    contract_map = contract if isinstance(contract, Mapping) else {}
    return {
        "system_defaults": dict(contract_map.get("system_defaults", {})),
        "system_safety_ceilings": dict(
            contract_map.get("system_safety_ceilings", {})
        ),
        "user_requested": dict(contract_map.get("user_requested", {})),
        "effective_hard_limits": dict(
            contract_map.get("effective_hard_limits", {})
        ),
        "soft_budgets": dict(contract_map.get("soft_usage_budgets", {})),
        "usage": (
            None
            if budget_map.get("usage") is None
            else dict(budget_map.get("usage", {}))
        ),
        "remaining": dict(budget_map.get("remaining_hard_budget", {})),
        "hard_budget_exhausted": budget_map.get("hard_budget_exhaustion"),
        "soft_budget_exceeded": dict(
            budget_map.get("soft_budget_exceeded", {})
        ),
    }


def _summary_pricing(identity: Mapping[str, Any]) -> dict[str, Any]:
    pricing = _mapping_path(identity, "model", "pricing")
    return dict(pricing) if isinstance(pricing, Mapping) else {
        "snapshot_sha256": None,
        "source_status": "unavailable",
        "cost_estimation_quality": "unavailable",
        "currency": None,
        "actual_estimation": {
            "quality": "unavailable",
            "amounts_by_currency": {},
            "is_invoice": False,
        },
        "is_invoice": False,
    }


def _suite_status(suites: object, split: str) -> str:
    if not isinstance(suites, list):
        return "not_run"
    selected = [item for item in suites if isinstance(item, Mapping) and item.get("split") == split]
    if not selected:
        return "not_run"
    statuses = [str(item.get("evaluation_status", "unknown")) for item in selected]
    return "passed" if all(value == "passed" for value in statuses) else "failed"


def _csynth_status(
    *, accepted: bool,
    failed_stage: str | None,
    public_status: str,
    hidden_status: str,
) -> str:
    if accepted or public_status != "not_run" or hidden_status != "not_run":
        return "passed"
    if failed_stage == "csynth":
        return "failed"
    return "not_run"


def _failed_stage(
    identity: Mapping[str, Any],
    phase_metadata: Mapping[str, Any],
) -> str | None:
    exhaustion = _mapping_path(identity, "budget", "hard_budget_exhaustion")
    if isinstance(exhaustion, Mapping) and exhaustion.get("stage"):
        return str(exhaustion["stage"])
    state = phase_metadata.get("last_validation_state")
    if isinstance(state, str) and state:
        return state
    return "refactor"


def _repair_limit(root: Path) -> int | None:
    path = root / "bootstrap" / "source_request.json"
    if not path.is_file():
        return None
    value = _read_json(path).get("max_candidate_repairs")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _identity_top(identity: Mapping[str, Any]) -> str | None:
    top = _mapping_path(identity, "source", "top_function")
    return str(top) if isinstance(top, str) else None


def _safe_execution_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": identity.get("execution_id"),
        "request_identity_sha256": identity.get("request_identity_sha256"),
        "cache_identity_sha256": identity.get("cache_identity_sha256"),
        "bundle_sha256": identity.get("bundle_sha256"),
        "artifact": "execution_identity.json",
    }


def _model_calls_payload(identity: Mapping[str, Any]) -> dict[str, Any]:
    model = _mapping_path(identity, "model", "value")
    prompt = identity.get("prompt_identity", {})
    pricing = _mapping_path(identity, "model", "pricing")
    return {
        "schema_version": _PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "evidence_view": "operator_full",
        "run_id": identity.get("run_id"),
        "model": dict(model) if isinstance(model, Mapping) else {},
        "prompt_identity": dict(prompt) if isinstance(prompt, Mapping) else {},
        "pricing": dict(pricing) if isinstance(pricing, Mapping) else {},
        "plaintext_prompts_persisted": False,
    }


def _tool_calls_payload(
    *, artifact_root: Path,
    work_root: Path,
) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for label, root in (("work", work_root), ("artifact", artifact_root)):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*_invocation.json")):
            key = (label, str(path.resolve()))
            if key in seen:
                continue
            seen.add(key)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {"status": "unreadable"}
            calls.append(
                {
                    "source_root": label,
                    "relative_path": path.relative_to(root).as_posix(),
                    "sha256": sha256(path.read_bytes()).hexdigest(),
                    "payload": _redact_secrets(payload),
                }
            )
    return {
        "schema_version": _PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "evidence_view": "operator_full",
        "calls": calls,
    }


def _refresh_manifest(root: Path) -> None:
    path = root / "run_artifact_manifest.json"
    manifest = _read_json(path)
    files = []
    for item in sorted(root.rglob("*")):
        if not item.is_file() or item == path:
            continue
        if item.is_symlink():
            raise ValueError("run artifacts must not contain symbolic links")
        data = item.read_bytes()
        relative = item.relative_to(root).as_posix()
        files.append(
            {
                "relative_path": relative,
                "sha256": sha256(data).hexdigest(),
                "size_bytes": len(data),
                "record_type": _record_type(relative),
            }
        )
    manifest["files"] = files
    manifest["product_output"] = {
        "schema_version": PRODUCT_RUN_SUMMARY_SCHEMA_VERSION,
        "levels": ["default", "json", "verbose", "debug"],
        "legacy_streams_captured": True,
    }
    _atomic_json(path, manifest)


def _record_type(relative: str) -> str:
    explicit = {
        "full_result.json": "full_result",
        "model_calls.json": "model_calls_operator_full",
        "tool_calls.json": "tool_calls_operator_full",
        "stdout.log": "captured_stdout",
        "stderr.log": "captured_stderr",
        "execution_identity.json": "execution_identity_operator_full",
        "trace.jsonl": "trace",
        "run_result.json": "run_result_compatibility",
    }
    if relative in explicit:
        return explicit[relative]
    if relative.endswith("artifact_manifest.json"):
        return "nested_artifact_manifest"
    if relative.endswith(".json"):
        return "supporting_json"
    return "supporting_artifact"


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            result[key] = (
                "<redacted>"
                if normalized in _SECRET_KEYS
                else _redact_secrets(item)
            )
        return result
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _assert_summary_safe(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    lowered = encoded.casefold()
    for key in _SECRET_KEYS:
        if f'"{key}"' in lowered:
            raise ValueError(f"product summary contains forbidden key: {key}")
    if "operator_artifact_path" in lowered or "testbench_path" in lowered:
        raise ValueError("product summary contains operator-only suite paths")


def _mapping_path(value: object, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON artifact must contain an object: {path}")
    return payload


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


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _number(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "0"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def _duration(value: object) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        seconds = 0
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute}m {second}s"
    if minutes:
        return f"{minute}m {second}s"
    return f"{second}s"
