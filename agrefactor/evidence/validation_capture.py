"""Append-only capture of Public formal validation stage evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from agrefactor.config import EvaluationSplit
from agrefactor.evaluation import ValidationState
from agrefactor.evidence.root_run_record import RootRunRecordStore


_STAGES = {
    ValidationState.PREFLIGHT: "preflight",
    ValidationState.PUBLIC_EVALUATION: "csim",
    ValidationState.CSYNTH: "csynth",
    ValidationState.PUBLIC_COSIM: "cosim",
}
_CODE_FIELDS = frozenset(
    {
        "candidate_code",
        "original_code",
        "testbench_code",
        "public_testbench_code",
        "suite_testbench_codes",
    }
)
_RECEIPT_HINTS = (
    "invocation",
    "suite_identity",
    "qualification",
    "interface_evidence",
)
_SECRET_KEY = re.compile(
    r"(?:secret|token|password|credential|api[_-]?key)", re.IGNORECASE
)
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:/[^\s\"']+|[A-Za-z]:\\[^\s\"']+)"
)


def _path_fingerprint(match: re.Match[str]) -> str:
    value = match.group(0)
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"<PATH:{digest}>"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_value(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        text = value.as_posix()
        if value.is_absolute():
            return {
                "basename": value.name,
                "path_sha256": sha256(text.encode("utf-8")).hexdigest(),
            }
        return text
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _safe_value(
                getattr(value, item.name), key=item.name
            )
            for item in fields(value)
            if not _SECRET_KEY.search(item.name)
            and item.name not in {"root", "work_dir", "csynth_work_dir"}
        }
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _safe_value(value.to_dict(), key=key)
        except Exception:
            return {"serialization": "unavailable"}
    if isinstance(value, Mapping):
        result = {}
        for raw_key, item in value.items():
            child_key = str(raw_key)
            if _SECRET_KEY.search(child_key) or "hidden" in child_key.casefold():
                continue
            result[child_key] = _safe_value(item, key=child_key)
        return result
    if isinstance(value, (tuple, list)):
        return [_safe_value(item, key=key) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return _ABSOLUTE_PATH.sub(_path_fingerprint, value)
        return value
    return str(value)


@dataclass(slots=True)
class _CaptureStart:
    stage: str
    state: str
    step_id: str
    started_evidence_id: str
    input_content_entries: tuple[dict[str, Any], ...]
    snapshot_sha256: str
    snapshot_complete: bool
    capture_errors: tuple[str, ...]
    work_dir: Path | None
    package_file_digests: dict[str, str]
    handler: Any


class ValidationEvidenceRecorder:
    """Store immutable Public-stage facts without affecting validation flow."""

    schema_version = 1

    def __init__(
        self,
        root_record: RootRunRecordStore,
        *,
        task: Any,
        run_id: str,
        validation_id: str,
        attempt_id: str | None = None,
    ) -> None:
        self._root = root_record
        self._task = task
        self._run_id = run_id
        self._validation_id = validation_id
        self._attempt_id = attempt_id

    def start_session(self, planned_states: tuple[ValidationState, ...]) -> None:
        self._root.record_validation_session(
            {
                "schema_version": self.schema_version,
                "validation_id": self._validation_id,
                "root_run_id": self._root.run_id,
                "validation_run_id": self._run_id,
                "task_id": self._task.task_id,
                "attempt_id": self._attempt_id,
                "planned_public_stages": [
                    _STAGES[state]
                    for state in planned_states
                    if state in _STAGES
                ],
                "evidence_view": "agent_safe",
                "hidden_content_persisted": False,
            }
        )

    def start_stage(
        self,
        *,
        state: ValidationState,
        step_id: str,
        handler: Any,
    ) -> _CaptureStart | None:
        stage = _STAGES.get(state)
        if stage is None:
            return None
        inputs = getattr(handler, "inputs", None)
        contract, content_entries, complete = self._contract_and_contents(
            stage, state, handler, inputs
        )
        capture_errors: list[str] = []
        if not complete:
            capture_errors.append("stage_contract_or_content_incomplete")
        hidden_hashes, hidden_hash_complete = (
            self._hidden_content_hashes(inputs)
        )
        if not hidden_hash_complete:
            capture_errors.append("hidden_content_manifest_incomplete")
        complete = complete and hidden_hash_complete
        work_dir = self._input_work_dir(inputs)
        files, tree_complete = self._capture_tree(
            work_dir,
            "stage_inputs",
            hidden_hashes=hidden_hashes,
        )
        if not tree_complete:
            capture_errors.append("stage_input_tree_incomplete")
        complete = complete and tree_complete
        external_entries, external_complete = self._capture_external_inputs(
            state,
            hidden_hashes=hidden_hashes,
        )
        files.extend(external_entries)
        if not external_complete:
            capture_errors.append("external_input_capture_incomplete")
        complete = complete and external_complete
        upstream_files: list[dict[str, Any]] = []
        upstream_captured = False
        producer_stage_evidence_id = None
        csynth_work_dir = getattr(inputs, "csynth_work_dir", None)
        if stage == "cosim" and csynth_work_dir is not None:
            upstream_files, upstream_complete = self._capture_csynth_prerequisite(
                Path(csynth_work_dir),
                hidden_hashes=hidden_hashes,
            )
            producer_stage_evidence_id = (
                self._latest_stage_result_evidence_id("csynth")
            )
            if producer_stage_evidence_id is None:
                upstream_complete = False
                capture_errors.append("csynth_producer_evidence_missing")
            if not upstream_complete:
                capture_errors.append("upstream_artifact_capture_incomplete")
            complete = complete and upstream_complete
            upstream_captured = True

        package_file_digests, package_entries, package_complete = (
            self._source_package_snapshot(hidden_hashes)
        )
        if package_file_digests:
            package_root = (
                work_dir if stage not in {"csim", "cosim"} else None
            )
            if package_root is not None:
                for relative, digest in package_file_digests.items():
                    path = package_root / relative
                    if not path.is_file() or path.is_symlink():
                        complete = False
                        capture_errors.append(
                            "stage_source_package_file_missing"
                        )
                    elif sha256(path.read_bytes()).hexdigest() != digest:
                        complete = False
                        capture_errors.append(
                            "stage_source_package_file_mismatch"
                        )
        if not package_complete:
            capture_errors.append("source_package_manifest_incomplete")
        complete = complete and package_complete
        snapshot = {
            "schema_version": self.schema_version,
            "input_contents": content_entries,
            "files": files,
            "source_package_files": package_entries,
            "upstream_artifacts": [
                {
                    **item,
                    "producer_validation_stage_id": producer_stage_evidence_id,
                }
                for item in upstream_files
            ] if upstream_captured else [],
            "source_package": self._source_package_descriptor(),
            "complete": complete,
            "replay_eligible": complete,
            "capture_errors": sorted(set(capture_errors)),
        }
        snapshot_sha256 = sha256(_canonical(snapshot)).hexdigest()
        started_id = self._evidence_id(step_id, "started")
        record = {
            **self._base_record(
                evidence_id=started_id,
                stage=stage,
                state=state.value,
                step_id=step_id,
            ),
            "execution_status": "started",
            "evidence_verdict": None,
            "capture_status": "complete" if complete else "incomplete",
            "capture_errors": sorted(set(capture_errors)),
            "stage_contract": contract,
            "stage_contract_sha256": sha256(
                _canonical(contract)
            ).hexdigest(),
            "input_snapshot": snapshot,
            "input_snapshot_sha256": snapshot_sha256,
            "hidden_content_persisted": False,
        }
        self._persist(record)
        return _CaptureStart(
            stage=stage,
            state=state.value,
            step_id=step_id,
            started_evidence_id=started_id,
            input_content_entries=tuple(content_entries),
            snapshot_sha256=snapshot_sha256,
            snapshot_complete=complete,
            capture_errors=tuple(sorted(set(capture_errors))),
            work_dir=work_dir,
            package_file_digests=package_file_digests,
            handler=handler,
        )

    def finish_stage(
        self,
        capture: _CaptureStart,
        *,
        report: Any | None = None,
        exception_type: str | None = None,
    ) -> None:
        capture_complete = capture.snapshot_complete
        capture_errors = list(capture.capture_errors)
        try:
            actual_inputs, actual_inputs_complete = (
                self._capture_actual_consumed_inputs(capture, report)
            )
        except Exception as exc:
            actual_inputs = []
            actual_inputs_complete = False
            capture_errors.append(
                f"actual_input_capture_failed:{type(exc).__name__}"
            )
        if not actual_inputs_complete:
            capture_errors.append("actual_consumed_inputs_incomplete")
        try:
            package_inputs, package_inputs_complete = (
                self._capture_actual_source_package_copies(capture, report)
            )
            actual_inputs.extend(package_inputs)
        except Exception as exc:
            package_inputs_complete = False
            capture_errors.append(
                f"source_package_copy_capture_failed:{type(exc).__name__}"
            )
        if not package_inputs_complete:
            capture_errors.append("source_package_copy_incomplete")
        capture_complete = capture_complete and actual_inputs_complete
        capture_complete = capture_complete and package_inputs_complete
        try:
            output_artifacts = self._capture_output_receipts(
                capture.work_dir,
                hidden_hashes=self._hidden_content_hashes(
                    getattr(capture.handler, "inputs", None)
                ),
            )
            result_payload = (
                None if report is None else _safe_value(report.to_dict())
            )
            result_ref = (
                None
                if result_payload is None
                else self._store_json(result_payload)
            )
        except Exception as exc:
            capture_complete = False
            output_artifacts = []
            result_ref = None
            capture_errors.append(
                f"result_or_receipt_capture_failed:{type(exc).__name__}"
            )
        environment_observation = self._tool_identity_observation(
            capture.stage,
            output_artifacts,
        )
        result_source_complete = (
            report is not None
            and self._required_receipts_present(
                capture.stage, report, output_artifacts
            )
            and environment_observation["verified"] is True
        )
        verdict = (
            None
            if report is None
            else self._verdict(
                capture.stage,
                report,
                capture_complete,
                result_source_complete,
            )
        )
        record = {
            **self._base_record(
                evidence_id=self._evidence_id(capture.step_id, "result"),
                stage=capture.stage,
                state=capture.state,
                step_id=capture.step_id,
            ),
            "execution_status": self._execution_status(
                capture.stage, report
            ),
            "evidence_verdict": verdict,
            "capture_status": (
                "complete" if capture_complete else "incomplete"
            ),
            "capture_errors": sorted(set(capture_errors)),
            "result_source_complete": result_source_complete,
            "started_evidence_id": capture.started_evidence_id,
            "input_snapshot_sha256": capture.snapshot_sha256,
            "result_report_ref": result_ref,
            "result_report_sha256": (
                None if result_ref is None else result_ref["sha256"]
            ),
            "output_artifacts": output_artifacts,
            "actual_consumed_inputs": actual_inputs,
            "environment_observation": environment_observation,
            "exception_type": exception_type,
            "observed_execution": (
                {}
                if report is None
                else _safe_value(
                    {
                        "metadata": report.metadata,
                        "source_evidence": report.source_evidence,
                    }
                )
            ),
            "hidden_content_persisted": False,
        }
        self._persist(record)

    def record_capture_failure(
        self,
        *,
        state: ValidationState,
        step_id: str,
        exception_type: str,
        report: Any | None = None,
    ) -> None:
        stage = _STAGES.get(state)
        if stage is None:
            return
        result_ref = None
        observed_execution = {}
        if report is not None:
            observed_execution = _safe_value(
                {
                    "metadata": report.metadata,
                    "source_evidence": report.source_evidence,
                }
            )
            try:
                result_ref = self._store_json(_safe_value(report.to_dict()))
            except Exception:
                pass
        record = {
            **self._base_record(
                evidence_id=self._evidence_id(step_id, "capture_failed"),
                stage=stage,
                state=state.value,
                step_id=step_id,
            ),
            "execution_status": (
                "interrupted"
                if report is None
                else self._execution_status(stage, report)
            ),
            "evidence_verdict": None,
            "capture_status": "failed",
            "capture_errors": [
                f"stage_start_capture_failed:{exception_type}"
            ],
            "result_report_ref": result_ref,
            "result_report_sha256": (
                None if result_ref is None else result_ref["sha256"]
            ),
            "observed_execution": observed_execution,
            "hidden_content_persisted": False,
        }
        self._persist(record)

    def record_unexecuted(
        self,
        *,
        state: ValidationState,
        reason: str,
        not_applicable: bool = False,
    ) -> None:
        stage = _STAGES.get(state)
        if stage is None:
            return
        status = "not_applicable" if not_applicable else "not_run"
        record = {
            **self._base_record(
                evidence_id=self._evidence_id(
                    f"{self._validation_id}.{stage}", status
                ),
                stage=stage,
                state=state.value,
                step_id=f"{self._validation_id}.{stage}",
            ),
            "execution_status": status,
            "evidence_verdict": None,
            "reason": reason,
            "capture_status": "not_required",
            "hidden_content_persisted": False,
        }
        self._persist(record)

    def record_evidence_disposition(
        self,
        *,
        source_evidence_id: str,
        disposition_id: str,
        evidence_verdict: str,
        reason_code: str,
    ) -> None:
        if evidence_verdict not in {
            "awaiting_post_run_evidence",
            "unverifiable",
        }:
            raise ValueError("unsupported evidence disposition")
        safe_code = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")
        if not safe_code.fullmatch(disposition_id):
            raise ValueError("disposition_id must be a stable safe identifier")
        if not safe_code.fullmatch(reason_code):
            raise ValueError("reason_code must be a stable safe identifier")
        source = next(
            (
                item
                for item in self._root.validation_evidence()
                if item.get("evidence_id") == source_evidence_id
            ),
            None,
        )
        if source is None or source.get("record_type") == "evidence_disposition":
            raise KeyError(source_evidence_id)
        if source.get("validation_id") != self._validation_id:
            raise ValueError("source evidence belongs to another validation")
        record = {
            **self._base_record(
                evidence_id=self._evidence_id(
                    source_evidence_id,
                    f"disposition.{disposition_id}",
                ),
                stage=str(source["stage"]),
                state=str(source["state"]),
                step_id=str(source["step_id"]),
            ),
            "record_type": "evidence_disposition",
            "attempt_id": source.get("attempt_id"),
            "disposition_of_evidence_id": source_evidence_id,
            "evidence_verdict": evidence_verdict,
            "reason_code": reason_code,
            "capture_status": source.get("capture_status"),
            "input_snapshot_sha256": source.get("input_snapshot_sha256"),
            "stage_contract_sha256": source.get("stage_contract_sha256"),
            "hidden_content_persisted": False,
        }
        self._persist(record)

    def _base_record(
        self, *, evidence_id: str, stage: str, state: str, step_id: str
    ) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": evidence_id,
            "root_run_id": self._root.run_id,
            "validation_id": self._validation_id,
            "validation_run_id": self._run_id,
            "attempt_id": self._attempt_id,
            "step_id": step_id,
            "state": state,
            "stage": stage,
            "split": "public",
            "evidence_view": "agent_safe",
        }

    def _persist(self, record: Mapping[str, Any]) -> None:
        self._root.record_validation_evidence(record)
        attempt_id = record.get("attempt_id")
        if attempt_id is not None:
            self._root.append_attempt_evidence_ref(
                str(attempt_id), str(record["evidence_id"])
            )

    def _latest_stage_result_evidence_id(self, stage: str) -> str | None:
        for record in reversed(self._root.validation_evidence()):
            if (
                record.get("validation_id") == self._validation_id
                and record.get("stage") == stage
                and record.get("evidence_id", "").endswith(":result")
            ):
                return str(record["evidence_id"])
        return None

    def _evidence_id(self, step_id: str, suffix: str) -> str:
        return f"{self._validation_id}:{step_id}:{suffix}"

    def _contract_and_contents(
        self, stage: str, state: ValidationState, handler: Any, inputs: Any
    ) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        complete = inputs is not None
        contents: list[dict[str, Any]] = []
        input_values: dict[str, Any] = {}
        hidden_ids = {
            suite.suite_id
            for suite in self._task.test_suites
            if suite.split is EvaluationSplit.HIDDEN
        }
        public_ids = {
            suite.suite_id
            for suite in self._task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        }
        if inputs is not None:
            if is_dataclass(inputs) and not isinstance(inputs, type):
                raw = {
                    item.name: getattr(inputs, item.name)
                    for item in fields(inputs)
                }
            else:
                raw = {
                    name: getattr(inputs, name)
                    for name in dir(inputs)
                    if not name.startswith("_")
                    and not callable(getattr(inputs, name, None))
                }
            required = {
                "preflight": {
                    "testbench_code",
                    "original_code",
                    "candidate_code",
                },
                "csim": {
                    "original_code",
                    "candidate_code",
                    "suite_testbench_codes",
                },
                "csynth": {"candidate_code"},
                "cosim": {
                    "original_code",
                    "candidate_code",
                    "suite_testbench_codes",
                },
            }.get(stage, set())
            complete = complete and required.issubset(raw)
            for name, value in raw.items():
                if name in {"work_dir", "csynth_work_dir"}:
                    continue
                if name not in _CODE_FIELDS:
                    input_values[name] = _safe_value(value, key=name)
                    continue
                if name == "suite_testbench_codes":
                    if not isinstance(value, Mapping):
                        complete = False
                        continue
                    if hidden_ids - set(str(key) for key in value):
                        complete = False
                    selected = {
                        str(suite_id): code
                        for suite_id, code in value.items()
                        if str(suite_id) in public_ids
                    }
                    if public_ids - set(selected):
                        complete = False
                    for suite_id in sorted(selected):
                        code = selected[suite_id]
                        if not isinstance(code, str):
                            complete = False
                            continue
                        ref = self._store_bytes(code.encode("utf-8"))
                        contents.append(
                            {
                                "role": "public_suite_testbench",
                                "logical_name": suite_id,
                                **ref,
                            }
                        )
                    input_values[name] = sorted(selected)
                elif value is not None:
                    if not isinstance(value, str):
                        complete = False
                        continue
                    ref = self._store_bytes(value.encode("utf-8"))
                    contents.append(
                        {"role": name, "logical_name": name, **ref}
                    )
                    input_values[name] = {
                        "sha256": ref["sha256"],
                        "size_bytes": ref["size_bytes"],
                    }
        target = _safe_value(self._task.target.to_dict(), key="target")
        suites = [
            _safe_value(suite.to_dict())
            for suite in self._task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        ]
        contract = {
            "schema_version": self.schema_version,
            "business_stage": stage,
            "validation_state": state.value,
            "handler": f"{type(handler).__module__}.{type(handler).__qualname__}",
            "handler_version": getattr(handler, "handler_version", None),
            "semantics_version": getattr(handler, "semantics_version", None),
            "orchestrator_version": 1,
            "public_suites": suites,
            "target_profile": target,
            "source_package": self._source_package_descriptor(),
            "inputs": input_values,
        }
        return contract, contents, complete

    def _source_package_descriptor(self) -> dict[str, Any] | None:
        package = getattr(self._task, "source_package", None)
        if package is None:
            return None
        return {
            "target": package.target_relative.as_posix(),
            "extra_sources": [
                path.as_posix()
                for path, absolute in zip(
                    package.extra_relative,
                    package.extra_sources,
                )
                if not self._is_hidden_path(absolute, package.root)
            ],
            "copy_policy": "all_regular_files_skip_symlinks",
        }

    def _source_package_snapshot(
        self,
        hidden_hashes: frozenset[str],
    ) -> tuple[dict[str, str], list[dict[str, Any]], bool]:
        package = getattr(self._task, "source_package", None)
        if package is None:
            return {}, [], True
        result: dict[str, str] = {}
        entries: list[dict[str, Any]] = []
        complete = True
        try:
            for path in sorted(package.root.rglob("*")):
                if (
                    path.is_symlink()
                    or self._is_hidden_path(path, package.root)
                ):
                    complete = False
                    continue
                if not path.is_file():
                    continue
                relative = path.relative_to(package.root).as_posix()
                content = path.read_bytes()
                if sha256(content).hexdigest() in hidden_hashes:
                    complete = False
                    continue
                ref = self._store_bytes(content)
                result[relative] = ref["sha256"]
                entries.append({"logical_path": relative, **ref})
        except OSError:
            complete = False
        return result, entries, complete

    def _capture_external_inputs(
        self,
        state: ValidationState,
        *,
        hidden_hashes: frozenset[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        if state is not ValidationState.PREFLIGHT:
            return [], True
        raw = Path(self._task.kernel_path).expanduser()
        if not raw.is_absolute():
            return [], False
        package = getattr(self._task, "source_package", None)
        if package is not None:
            try:
                raw.resolve().relative_to(package.root)
                return [], True
            except ValueError:
                pass
        root = raw.parent
        if not root.is_dir():
            return [], False
        return self._capture_tree(
            root,
            "external/kernel_inputs",
            hidden_hashes=hidden_hashes,
        )

    def _capture_csynth_prerequisite(
        self,
        root: Path,
        *,
        hidden_hashes: frozenset[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        receipt_path = root / "csynth_invocation.json"
        if receipt_path.is_symlink() or not receipt_path.is_file():
            return [], False
        files: list[dict[str, Any]] = []
        try:
            receipt_content = receipt_path.read_bytes()
            receipt = json.loads(receipt_content)
            files.append(
                {
                    "role": "csynth_invocation_receipt",
                    "logical_path": "upstream/csynth/csynth_invocation.json",
                    **self._store_bytes(receipt_content),
                }
            )
            references = []
            provenance = receipt.get("source_provenance", {})
            if isinstance(provenance, Mapping):
                references.extend(
                    (item.get("path"), item.get("sha256"), "csynth_source")
                    for item in provenance.values()
                    if isinstance(item, Mapping)
                )
            top_io = receipt.get("top_io", {})
            artifacts = (
                top_io.get("artifacts", [])
                if isinstance(top_io, Mapping)
                else []
            )
            if isinstance(artifacts, list):
                references.extend(
                    (item.get("path"), item.get("sha256"), "csynth_interface")
                    for item in artifacts
                    if isinstance(item, Mapping)
                )
            if not references:
                return files, False
            for raw_path, expected, role in references:
                if not isinstance(raw_path, str) or not isinstance(expected, str):
                    return files, False
                path = Path(raw_path)
                if not path.is_absolute():
                    path = root / path
                resolved = path.resolve()
                resolved.relative_to(root.resolve())
                if (
                    path.is_symlink()
                    or self._is_hidden_path(path, root)
                    or not path.is_file()
                ):
                    return files, False
                content = path.read_bytes()
                digest = sha256(content).hexdigest()
                if digest in hidden_hashes:
                    return files, False
                if digest != expected:
                    return files, False
                files.append(
                    {
                        "role": role,
                        "logical_path": (
                            "upstream/csynth/"
                            + path.relative_to(root).as_posix()
                        ),
                        **self._store_bytes(content),
                    }
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return files, False
        return files, True

    @staticmethod
    def _input_work_dir(inputs: Any) -> Path | None:
        value = getattr(inputs, "work_dir", None)
        return Path(value) if value is not None else None

    def _capture_tree(
        self,
        root: Path | None,
        prefix: str,
        *,
        hidden_hashes: frozenset[str] = frozenset(),
    ) -> tuple[list[dict[str, Any]], bool]:
        if root is None or not root.exists():
            return [], True
        if root.is_symlink():
            return [], False
        files: list[dict[str, Any]] = []
        complete = True
        for current, dirs, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            kept_dirs = []
            for name in sorted(dirs):
                path = current_path / name
                if path.is_symlink() or self._is_hidden_path(path, root):
                    complete = False
                else:
                    kept_dirs.append(name)
            dirs[:] = kept_dirs
            for name in sorted(names):
                path = current_path / name
                if path.is_symlink() or self._is_hidden_path(path, root):
                    complete = False
                    continue
                try:
                    if not path.is_file():
                        continue
                    content = path.read_bytes()
                    if sha256(content).hexdigest() in hidden_hashes:
                        complete = False
                        continue
                    ref = self._store_bytes(content)
                except OSError:
                    complete = False
                    continue
                files.append(
                    {
                        "role": "input_file",
                        "logical_path": (
                            f"{prefix}/{path.relative_to(root).as_posix()}"
                        ),
                        **ref,
                    }
                )
        return files, complete

    def _capture_output_receipts(
        self,
        work_dir: Path | None,
        *,
        hidden_hashes: frozenset[str],
    ) -> list[dict[str, Any]]:
        if work_dir is None or not work_dir.exists():
            return []
        result = []
        for path in sorted(work_dir.rglob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name.casefold()
            if not any(hint in name for hint in _RECEIPT_HINTS):
                continue
            if self._is_hidden_path(path, work_dir):
                continue
            content = path.read_bytes()
            if sha256(content).hexdigest() in hidden_hashes:
                continue
            payload = json.loads(content.decode("utf-8"))
            safe_payload = _safe_value(payload)
            if (
                isinstance(safe_payload, dict)
                and name == "testbench_preflight_invocation.json"
            ):
                compiler = payload.get("compiler")
                safe_payload["compiler_identity"] = (
                    self._compiler_identity(compiler)
                )
            ref = self._store_json(safe_payload)
            result.append(
                {
                    "role": "execution_receipt",
                    "logical_path": path.relative_to(work_dir).as_posix(),
                    **ref,
                }
            )
        return result

    @staticmethod
    def _compiler_identity(compiler: Any) -> dict[str, Any]:
        if not isinstance(compiler, str) or not compiler.strip():
            return {"verified": False, "reason": "compiler_identity_missing"}
        executable = compiler if os.path.isabs(compiler) else shutil.which(compiler)
        if not executable:
            return {"verified": False, "reason": "compiler_not_resolved"}
        path = Path(executable)
        try:
            if path.is_symlink() or not path.is_file():
                return {
                    "verified": False,
                    "reason": "compiler_not_regular_file",
                }
            digest = sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return {
                "verified": False,
                "reason": "compiler_fingerprint_unavailable",
            }
        return {
            "verified": True,
            "compiler_name": path.name,
            "compiler_sha256": digest.hexdigest(),
            "version": None,
        }

    def _hidden_content_hashes(
        self, inputs: Any
    ) -> tuple[frozenset[str], bool]:
        hidden_ids = {
            suite.suite_id
            for suite in self._task.test_suites
            if suite.split is EvaluationSplit.HIDDEN
        }
        mappings = getattr(inputs, "suite_testbench_codes", {})
        if not hidden_ids:
            return frozenset(), True
        if not isinstance(mappings, Mapping):
            return frozenset(), False
        hashes = set()
        for suite_id in hidden_ids:
            code = mappings.get(suite_id)
            if not isinstance(code, str) or not code:
                return frozenset(hashes), False
            hashes.add(sha256(code.encode("utf-8")).hexdigest())
        return frozenset(hashes), True

    def _is_hidden_path(self, path: Path, root: Path) -> bool:
        if any(part.casefold() == "hidden" for part in path.parts):
            return True
        basename = path.name.casefold()
        for suite in self._task.test_suites:
            if suite.split is not EvaluationSplit.HIDDEN:
                continue
            raw = suite.testbench_path
            if not isinstance(raw, str) or not raw.strip():
                continue
            normalized = raw.replace("\\", "/").rstrip("/")
            hidden_name = normalized.rsplit("/", 1)[-1].casefold()
            if basename == hidden_name:
                return True
            try:
                candidate = Path(normalized)
                if candidate.is_absolute():
                    if candidate.resolve() == path.resolve():
                        return True
                    candidate = candidate.resolve().relative_to(root.resolve())
                if candidate.as_posix().casefold() == path.relative_to(
                    root
                ).as_posix().casefold():
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _capture_actual_source_package_copies(
        self, capture: _CaptureStart, report: Any | None
    ) -> tuple[list[dict[str, Any]], bool]:
        if capture.stage not in {"csim", "cosim"}:
            return [], True
        if not capture.package_file_digests:
            return [], True
        if report is None:
            return [], False
        suite_work_dir = getattr(capture.handler, "_suite_work_dir", None)
        if not callable(suite_work_dir):
            return [], False
        metadata = getattr(report, "metadata", {})
        attempted = metadata.get("attempted_suite_ids", ())
        if not isinstance(attempted, (list, tuple)):
            return [], False
        public_suites = [
            suite.suite_id
            for suite in self._task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        ]
        entries: list[dict[str, Any]] = []
        complete = True
        for suite_id in attempted:
            if suite_id not in public_suites:
                complete = False
                continue
            index = public_suites.index(suite_id) + 1
            directory = Path(suite_work_dir(index))
            for relative, expected in capture.package_file_digests.items():
                path = directory / relative
                if path.is_symlink() or not path.is_file():
                    complete = False
                    entries.append(
                        {
                            "role": "actual_consumed_input",
                            "input_kind": "source_package_copy",
                            "suite_id": suite_id,
                            "logical_path": (
                                f"{capture.stage}/{suite_id}/{relative}"
                            ),
                            "matches_declared_input": False,
                            "capture_status": "missing",
                        }
                    )
                    continue
                try:
                    content = path.read_bytes()
                    digest = sha256(content).hexdigest()
                    ref = self._store_bytes(content)
                except OSError:
                    complete = False
                    entries.append(
                        {
                            "role": "actual_consumed_input",
                            "input_kind": "source_package_copy",
                            "suite_id": suite_id,
                            "logical_path": (
                                f"{capture.stage}/{suite_id}/{relative}"
                            ),
                            "matches_declared_input": False,
                            "capture_status": "unreadable",
                        }
                    )
                    continue
                matched = digest == expected
                if not matched:
                    complete = False
                entries.append(
                    {
                        "role": "actual_consumed_input",
                        "input_kind": "source_package_copy",
                        "suite_id": suite_id,
                        "logical_path": (
                            f"{capture.stage}/{suite_id}/{relative}"
                        ),
                        "matches_declared_input": matched,
                        **ref,
                    }
                )
        return entries, complete

    def _capture_actual_consumed_inputs(
        self,
        capture: _CaptureStart,
        report: Any | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        work_dir = capture.work_dir
        if work_dir is None or report is None:
            return [], False
        inputs = getattr(capture.handler, "inputs", None)
        entries: list[dict[str, Any]] = []
        complete = True

        def expected_digest(role: str, logical_name: str | None = None) -> str | None:
            for item in capture.input_content_entries:
                if item.get("role") != role:
                    continue
                if logical_name is not None and item.get("logical_name") != logical_name:
                    continue
                return str(item["sha256"])
            return None

        def capture_file(
            path: Path,
            *,
            logical_path: str,
            expected: str | None = None,
        ) -> bool:
            nonlocal complete
            try:
                if path.is_symlink() or not path.is_file():
                    complete = False
                    return False
                content = path.read_bytes()
                digest = sha256(content).hexdigest()
                ref = self._store_bytes(content)
            except OSError:
                complete = False
                return False
            matched = expected is None or digest == expected
            if not matched:
                complete = False
            entries.append(
                {
                    "role": "actual_consumed_input",
                    "logical_path": logical_path,
                    "matches_declared_input": matched,
                    **ref,
                }
            )
            return matched

        if capture.stage == "preflight":
            expected_files = (
                ("testbench.cpp", "testbench_code"),
                ("orig_code.cpp", "original_code"),
                ("refactor_code.cpp", "candidate_code"),
            )
            for filename, role in expected_files:
                capture_file(
                    work_dir / filename,
                    logical_path=filename,
                    expected=expected_digest(role),
                )
        elif capture.stage == "csim":
            attempted = report.metadata.get("attempted_suite_ids", ())
            if not isinstance(attempted, (list, tuple)):
                return [], False
            public_suites = [
                suite.suite_id
                for suite in self._task.test_suites
                if suite.split is EvaluationSplit.PUBLIC
            ]
            for suite_id in attempted:
                if suite_id not in public_suites:
                    complete = False
                    continue
                index = public_suites.index(suite_id) + 1
                suite_root = work_dir / "public" / f"suite_{index:03d}"
                for filename, role in (
                    ("candidate.cpp", "candidate_code"),
                    ("reference.cpp", "original_code"),
                ):
                    capture_file(
                        suite_root / filename,
                        logical_path=(
                            f"public/suite_{index:03d}/{filename}"
                        ),
                        expected=expected_digest(role),
                    )
                for filename in (
                    "testbench.cpp",
                    "agrefactor_csim_wrapper.cpp",
                    "agrefactor_case_evidence.hpp",
                ):
                    capture_file(
                        suite_root / filename,
                        logical_path=(
                            f"public/suite_{index:03d}/{filename}"
                        ),
                    )
        elif capture.stage == "csynth":
            invocation_path = work_dir / "csynth_invocation.json"
            try:
                invocation = json.loads(
                    invocation_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                return [], False
            provenance = invocation.get("source_provenance")
            if not isinstance(provenance, Mapping) or not provenance:
                return [], False
            candidate_found = False
            for logical_name, source in provenance.items():
                if not isinstance(source, Mapping):
                    complete = False
                    continue
                raw_path = source.get("path")
                expected = source.get("sha256")
                if not isinstance(raw_path, str) or not isinstance(expected, str):
                    complete = False
                    continue
                path = Path(raw_path)
                if not path.is_absolute():
                    path = work_dir / path
                try:
                    relative = path.resolve().relative_to(work_dir.resolve())
                except (OSError, ValueError):
                    complete = False
                    continue
                if source.get("role") == "candidate":
                    candidate_found = True
                    declared = expected_digest("candidate_code")
                    if declared is not None and declared != expected:
                        complete = False
                capture_file(
                    path,
                    logical_path=relative.as_posix(),
                    expected=expected,
                )
            if not candidate_found:
                complete = False
        elif capture.stage == "cosim":
            attempted = report.metadata.get("attempted_suite_ids", ())
            public_suites = [
                suite.suite_id
                for suite in self._task.test_suites
                if suite.split is EvaluationSplit.PUBLIC
            ]
            if not isinstance(attempted, (list, tuple)):
                return [], False
            for suite_id in attempted:
                if suite_id not in public_suites:
                    complete = False
                    continue
                index = public_suites.index(suite_id) + 1
                suite_root = work_dir / f"suite_{index:03d}"
                for filename, role in (
                    ("candidate.cpp", "candidate_code"),
                    ("reference.cpp", "original_code"),
                    ("public_testbench_original.cpp", "public_suite_testbench"),
                ):
                    logical_name = (
                        suite_id
                        if role == "public_suite_testbench"
                        else None
                    )
                    capture_file(
                        suite_root / filename,
                        logical_path=f"suite_{index:03d}/{filename}",
                        expected=expected_digest(role, logical_name),
                    )
                for filename in (
                    "public_testbench.cpp",
                    "agrefactor_cosim_wrapper.cpp",
                ):
                    capture_file(
                        suite_root / filename,
                        logical_path=f"suite_{index:03d}/{filename}",
                    )
        if not entries:
            complete = False
        return entries, complete

    @staticmethod
    def _execution_status(stage: str, report: Any | None) -> str:
        if report is None:
            return "interrupted"
        if stage == "cosim" and report.metadata.get("cosim_skipped") is True:
            return "not_run"
        launched = report.metadata.get("physical_execution") is True
        launched = launched or any(
            item.metadata.get("tool_launched") is True
            or item.metadata.get("physical_tool_launched") is True
            for item in report.items
        )
        if not launched and any(
            getattr(item.category, "value", str(item.category))
            == "budget_exhausted"
            for item in report.items
        ):
            return "blocked_before_launch"
        explicitly_not_launched = report.metadata.get("physical_execution") is False
        explicitly_not_launched = explicitly_not_launched or any(
            item.metadata.get("tool_launched") is False
            or item.metadata.get("physical_tool_launched") is False
            for item in report.items
        )
        if explicitly_not_launched:
            return "blocked_before_launch"
        return "completed"

    def _required_receipts_present(
        self,
        stage: str,
        report: Any,
        output_artifacts: list[dict[str, Any]],
    ) -> bool:
        paths = {
            str(item.get("logical_path", "")).casefold()
            for item in output_artifacts
        }
        if stage == "preflight":
            return any(
                path.endswith("testbench_preflight_invocation.json")
                for path in paths
            )
        if stage == "csynth":
            return any(
                path.endswith("csynth_invocation.json")
                for path in paths
            )
        if stage == "csim":
            stage_name = "public"
            declared = report.metadata.get("declared_suite_ids", ())
            attempted = report.metadata.get("attempted_suite_ids", ())
            if (
                not isinstance(declared, (list, tuple))
                or not isinstance(attempted, (list, tuple))
                or not attempted
                or any(suite_id not in declared for suite_id in attempted)
            ):
                return False
            return all(
                f"{stage_name}/suite_{declared.index(suite_id) + 1:03d}/csim_invocation.json"
                in paths
                for suite_id in attempted
            )
        if stage == "cosim":
            declared = [
                suite.suite_id
                for suite in self._task.test_suites
                if suite.split is EvaluationSplit.PUBLIC
            ]
            attempted = report.metadata.get("attempted_suite_ids", ())
            if (
                not declared
                or not isinstance(attempted, (list, tuple))
                or not attempted
                or any(suite_id not in declared for suite_id in attempted)
            ):
                return False
            return all(
                f"suite_{declared.index(suite_id) + 1:03d}/cosim_invocation.json"
                in paths
                for suite_id in attempted
            )
        return False

    def _tool_identity_observation(
        self,
        stage: str,
        output_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payloads: list[tuple[str, Mapping[str, Any]]] = []
        for artifact in output_artifacts:
            ref = artifact.get("artifact_ref")
            path = artifact.get("logical_path")
            if not isinstance(ref, str) or not isinstance(path, str):
                continue
            try:
                payload = json.loads(
                    self._root.read_validation_artifact(ref).decode("utf-8")
                )
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping):
                payloads.append((path, payload))

        if stage == "preflight":
            invocation = next(
                (
                    payload
                    for path, payload in payloads
                    if path.endswith("testbench_preflight_invocation.json")
                ),
                None,
            )
            identity = (
                None
                if invocation is None
                else invocation.get("compiler_identity")
            )
            if not isinstance(identity, Mapping):
                return {"verified": False, "reason": "compiler_identity_missing"}
            return dict(identity)

        required_statuses = {"matched", "detected"}
        observations = []
        for path, payload in payloads:
            matches_stage = (
                path.endswith("csim_invocation.json")
                if stage == "csim"
                else path.endswith("csynth_invocation.json")
                if stage == "csynth"
                else path.endswith("cosim_invocation.json")
                if stage == "cosim"
                else False
            )
            if not matches_stage:
                continue
            verification = payload.get("toolchain_version_verification")
            if not isinstance(verification, Mapping):
                observations.append(
                    {"verified": False, "reason": "toolchain_receipt_missing"}
                )
                continue
            version = verification.get("actual")
            verified = (
                verification.get("status") in required_statuses
                and isinstance(version, str)
                and bool(version.strip())
            )
            observations.append(
                {
                    "verified": verified,
                    "version": version if verified else None,
                    "reason": None if verified else "toolchain_version_unverified",
                }
            )
        if not observations or not all(
            item.get("verified") is True for item in observations
        ):
            return {
                "verified": False,
                "reason": "toolchain_identity_incomplete",
                "observations": observations,
            }
        versions = {item["version"] for item in observations}
        return {
            "verified": len(versions) == 1,
            "versions": sorted(versions),
            "reason": None if len(versions) == 1 else "toolchain_versions_disagree",
        }

    def _verdict(
        self,
        stage: str,
        report: Any,
        snapshot_complete: bool,
        result_source_complete: bool,
    ) -> str | None:
        if not snapshot_complete or not result_source_complete:
            return None
        metadata = report.metadata
        if report.blocking:
            blocking = [item for item in report.items if item.blocking]
            if not blocking:
                return None
            infrastructure = {
                "toolchain_failure",
                "invalid_configuration",
                "timeout",
                "budget_exhausted",
                "unknown",
            }
            for item in blocking:
                item_metadata = item.metadata
                owner = getattr(item.owner, "value", str(item.owner))
                category = getattr(item.category, "value", str(item.category))
                launched = (
                    metadata.get("physical_execution") is True
                    or item_metadata.get("tool_launched") is True
                    or item_metadata.get("physical_tool_launched") is True
                )
                if (
                    owner not in {"candidate", "testbench"}
                    or category in infrastructure
                    or item_metadata.get("evidence_complete") is not True
                    or not launched
                ):
                    return None
            return "confirmed_failed"
        if stage == "preflight":
            if (
                metadata.get("preflight_status") in {"passed", "succeeded"}
                and metadata.get("physical_execution") is True
            ):
                return "passed"
        elif stage == "csynth":
            if (
                metadata.get("physical_execution") is True
                and metadata.get("operator_invocation_available") is True
                and metadata.get("legacy_status") in {None, "succeeded"}
            ):
                return "passed"
        elif stage == "csim":
            declared = metadata.get("declared_suite_ids")
            attempted = metadata.get("attempted_suite_ids")
            if (
                metadata.get("native_vitis_csim") is True
                and isinstance(declared, list)
                and declared
                and attempted == declared
                and metadata.get("stopped_early") is False
            ):
                return "passed"
        elif stage == "cosim":
            summaries = report.source_evidence.get("suite_summaries", ())
            declared = metadata.get("declared_suite_ids")
            attempted = metadata.get("attempted_suite_ids")
            if (
                metadata.get("physical_execution") is True
                and isinstance(declared, list)
                and attempted == declared
                and metadata.get("stopped_early") is False
                and isinstance(summaries, (list, tuple))
                and summaries
                and all(
                    isinstance(item, Mapping)
                    and item.get("status") == "passed"
                    and item.get("cosim_launched") is True
                    for item in summaries
                )
            ):
                return "passed"
        return None

    def _store_bytes(self, content: bytes) -> dict[str, Any]:
        return self._root.store_validation_artifact(content)

    def _store_json(self, value: Any) -> dict[str, Any]:
        return self._store_bytes(_canonical(value))
