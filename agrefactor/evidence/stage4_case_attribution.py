"""Ordinary formal-failure attribution from captured root-run evidence."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


_STAGE_ORDER = {"preflight": 0, "csim": 1, "csynth": 2, "cosim": 3}
_STAGE_ALIASES = {
    "public_evaluation": "csim",
    "test_evaluation": "csim",
    "public_csim": "csim",
    "csynth": "csynth",
    "csynth_stage": "csynth",
    "public_cosim": "cosim",
    "public_rtl_cosim": "cosim",
    "hidden_evaluation": None,
    "hidden": None,
    "public_testbench_generation": None,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def normalize_stage(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in _STAGE_ORDER:
        return normalized
    return _STAGE_ALIASES.get(normalized)


def _sha256_value(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _verify_artifact_ref(root_record: Any, value: Mapping[str, Any]) -> bool:
    ref = value.get("artifact_ref")
    digest = value.get("sha256")
    if not isinstance(ref, str) or not ref.startswith("sha256:"):
        return False
    if not _sha256_value(digest) or ref != f"sha256:{digest}":
        return False
    try:
        content = root_record.read_validation_artifact(ref)
    except (OSError, ValueError, KeyError):
        return False
    return (
        sha256(content).hexdigest() == digest
        and value.get("size_bytes") == len(content)
    )


def _artifact_refs_valid(root_record: Any, value: Any) -> bool:
    if isinstance(value, Mapping):
        if "artifact_ref" in value and not _verify_artifact_ref(root_record, value):
            return False
        return all(_artifact_refs_valid(root_record, item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_artifact_refs_valid(root_record, item) for item in value)
    return True


def _snapshot_matches_contract(
    contract: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> bool:
    inputs = contract.get("inputs")
    contents = snapshot.get("input_contents")
    if not isinstance(inputs, Mapping) or not isinstance(contents, list):
        return False
    for role in ("candidate_code", "original_code", "testbench_code"):
        expected = inputs.get(role)
        if expected is None:
            continue
        matches = [
            item
            for item in contents
            if isinstance(item, Mapping) and item.get("role") == role
        ]
        if not isinstance(expected, Mapping) or len(matches) != 1:
            return False
        if (
            matches[0].get("sha256") != expected.get("sha256")
            or matches[0].get("size_bytes") != expected.get("size_bytes")
        ):
            return False
    suite_ids = inputs.get("suite_testbench_codes")
    if suite_ids is not None:
        entries = {
            item.get("logical_name"): item
            for item in contents
            if isinstance(item, Mapping)
            and item.get("role") == "public_suite_testbench"
        }
        if not isinstance(suite_ids, list) or set(entries) != set(suite_ids):
            return False
    return True


def _verified_evidence(root_record: Any, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("validation_evidence", {})
    values = []
    for record in raw.values():
        evidence = dict(record.get("payload", {}))
        evidence["_sequence"] = record.get("sequence")
        evidence["_eligible"] = False
        if evidence.get("record_type") == "evidence_disposition":
            values.append(evidence)
            continue
        started = raw.get(evidence.get("started_evidence_id"))
        if started is None:
            values.append(evidence)
            continue
        start = dict(started.get("payload", {}))
        stage_contract = start.get("stage_contract")
        input_snapshot = start.get("input_snapshot")
        consumed_inputs = evidence.get("actual_consumed_inputs")
        output_artifacts = evidence.get("output_artifacts")
        result_ref = evidence.get("result_report_ref")
        environment = evidence.get("environment_observation")
        valid = (
            evidence.get("schema_version") == 1
            and evidence.get("evidence_view") == "agent_safe"
            and evidence.get("split") == "public"
            and evidence.get("hidden_content_persisted") is False
            and evidence.get("record_type") is None
            and evidence.get("capture_status") == "complete"
            and evidence.get("result_source_complete") is True
            and isinstance(environment, Mapping)
            and environment.get("verified") is True
            and evidence.get("evidence_verdict") in {"passed", "confirmed_failed"}
            and start.get("execution_status") == "started"
            and start.get("capture_status") == "complete"
            and start.get("evidence_view") == "agent_safe"
            and start.get("split") == "public"
            and start.get("hidden_content_persisted") is False
            and isinstance(stage_contract, Mapping)
            and stage_contract.get("handler_version") is not None
            and stage_contract.get("semantics_version") is not None
            and isinstance(stage_contract.get("target_profile"), Mapping)
            and isinstance(stage_contract.get("public_suites"), list)
            and isinstance(input_snapshot, Mapping)
            and input_snapshot.get("complete") is True
            and input_snapshot.get("replay_eligible") is True
            and _snapshot_matches_contract(stage_contract, input_snapshot)
            and evidence.get("input_snapshot_sha256")
            == start.get("input_snapshot_sha256")
            and evidence.get("stage_contract_sha256")
            == start.get("stage_contract_sha256")
            and _sha256_value(evidence.get("input_snapshot_sha256"))
            and _sha256_value(evidence.get("stage_contract_sha256"))
            and _digest(stage_contract) == evidence.get("stage_contract_sha256")
            and _digest(input_snapshot) == evidence.get("input_snapshot_sha256")
            and isinstance(result_ref, Mapping)
            and result_ref.get("sha256") == evidence.get("result_report_sha256")
            and isinstance(consumed_inputs, list)
            and bool(consumed_inputs)
            and isinstance(output_artifacts, list)
            and bool(output_artifacts)
            and all(
                isinstance(item, Mapping)
                and item.get("role") == "actual_consumed_input"
                and item.get("matches_declared_input") is True
                for item in consumed_inputs
            )
            and _artifact_refs_valid(root_record, input_snapshot)
            and _artifact_refs_valid(root_record, evidence.get("actual_consumed_inputs"))
            and _artifact_refs_valid(root_record, output_artifacts)
            and _artifact_refs_valid(root_record, result_ref)
        )
        if valid:
            evidence["_eligible"] = True
            evidence["_stage_contract"] = deepcopy(stage_contract)
            evidence["_input_snapshot"] = deepcopy(input_snapshot)
        values.append(evidence)
    return sorted(values, key=lambda item: item.get("_sequence") or 0)


def _source_hashes(evidence: Mapping[str, Any]) -> dict[str, str]:
    snapshot = evidence.get("_input_snapshot")
    if not isinstance(snapshot, Mapping):
        return {}
    result: dict[str, str] = {}
    contract = evidence.get("_stage_contract", {})
    inputs = contract.get("inputs", {}) if isinstance(contract, Mapping) else {}
    candidate = inputs.get("candidate_code") if isinstance(inputs, Mapping) else None
    if isinstance(candidate, Mapping) and _sha256_value(candidate.get("sha256")):
        result["candidate"] = candidate["sha256"]
    for item in snapshot.get("input_contents", ()):
        if not isinstance(item, Mapping) or not _sha256_value(item.get("sha256")):
            continue
        role = item.get("role")
        logical_name = str(item.get("logical_name", ""))
        if role == "testbench_code":
            result["testbench"] = item["sha256"]
        elif role == "public_suite_testbench":
            result[f"testbench:{logical_name}"] = item["sha256"]
        elif role == "original_code":
            result["reference"] = item["sha256"]
    return result


def _contract_signature(evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    contract = evidence.get("_stage_contract")
    if not isinstance(contract, Mapping):
        return None
    suites = contract.get("public_suites")
    if not isinstance(suites, list):
        return None
    normalized_suites = []
    for suite in suites:
        if not isinstance(suite, Mapping):
            return None
        normalized_suites.append(
            {key: value for key, value in suite.items() if key != "content_sha256"}
        )
    inputs = contract.get("inputs")
    if not isinstance(inputs, Mapping):
        return None
    return {
        "schema_version": contract.get("schema_version"),
        "handler_version": contract.get("handler_version"),
        "semantics_version": contract.get("semantics_version"),
        "orchestrator_version": contract.get("orchestrator_version"),
        "public_suites": normalized_suites,
        "target_profile": contract.get("target_profile"),
        "source_package": contract.get("source_package"),
    }


def _environment_signature(evidence: Mapping[str, Any]) -> Any:
    environment = evidence.get("environment_observation")
    if not isinstance(environment, Mapping) or environment.get("verified") is not True:
        return None
    return {
        key: value
        for key, value in environment.items()
        if key not in {"verified", "reason", "observations"}
    }


def _immutable_consumed_inputs(evidence: Mapping[str, Any]) -> dict[str, str] | None:
    values = evidence.get("actual_consumed_inputs")
    if not isinstance(values, list):
        return None
    result = {}
    for item in values:
        if not isinstance(item, Mapping) or not _sha256_value(item.get("sha256")):
            return None
        path = item.get("logical_path")
        if not isinstance(path, str):
            return None
        filename = path.rsplit("/", 1)[-1].casefold()
        if filename in {
            "candidate.cpp",
            "refactor_code.cpp",
            "testbench.cpp",
            "public_testbench_original.cpp",
        }:
            continue
        result[path] = item["sha256"]
    return result


def _package_input_signature(evidence: Mapping[str, Any]) -> dict[str, str] | None:
    snapshot = evidence.get("_input_snapshot")
    contract = evidence.get("_stage_contract")
    if not isinstance(snapshot, Mapping) or not isinstance(contract, Mapping):
        return None
    package = contract.get("source_package")
    target = package.get("target") if isinstance(package, Mapping) else None
    entries = snapshot.get("source_package_files")
    if not isinstance(entries, list):
        return None
    result = {}
    for item in entries:
        if not isinstance(item, Mapping):
            return None
        path = item.get("logical_path")
        digest = item.get("sha256")
        if not isinstance(path, str) or not _sha256_value(digest):
            return None
        if target and path == target:
            continue
        result[path] = digest
    return result


def _comparable(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if not left.get("_eligible") or not right.get("_eligible"):
        return False
    if left.get("root_run_id") != right.get("root_run_id"):
        return False
    left_stage = normalize_stage(left.get("stage"))
    right_stage = normalize_stage(right.get("stage"))
    if left_stage is None or right_stage is None:
        return False
    left_signature = _contract_signature(left)
    right_signature = _contract_signature(right)
    if left_signature is None or right_signature is None or left_signature != right_signature:
        return False
    left_inputs = left.get("_stage_contract", {}).get("inputs", {})
    right_inputs = right.get("_stage_contract", {}).get("inputs", {})
    for key in set(left_inputs) & set(right_inputs):
        if key in {"candidate_code", "testbench_code", "suite_testbench_codes"}:
            continue
        if left_inputs[key] != right_inputs[key]:
            return False
    left_env = _environment_signature(left)
    right_env = _environment_signature(right)
    if left_stage == right_stage and left_env != right_env:
        return False
    if left_stage != right_stage and left_env is not None and right_env is not None:
        left_version = left_env.get("versions", left_env.get("version"))
        right_version = right_env.get("versions", right_env.get("version"))
        if left_version is not None and right_version is not None and left_version != right_version:
            return False
    left_hashes = _source_hashes(left)
    right_hashes = _source_hashes(right)
    for key in set(left_hashes) & set(right_hashes):
        if key == "reference" and left_hashes[key] != right_hashes[key]:
            return False
    left_package = _package_input_signature(left)
    right_package = _package_input_signature(right)
    if left_package is None or right_package is None or left_package != right_package:
        return False
    left_consumed = _immutable_consumed_inputs(left)
    right_consumed = _immutable_consumed_inputs(right)
    if left_consumed is None or right_consumed is None:
        return False
    if left_stage == right_stage and left_consumed != right_consumed:
        return False
    if left_stage != right_stage:
        common = set(left_consumed) & set(right_consumed)
        for path in common:
            if left_consumed[path] != right_consumed[path]:
                return False
    return True


def _same_effective_inputs(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        normalize_stage(left.get("stage")) == normalize_stage(right.get("stage"))
        and _comparable(left, right)
        and _source_hashes(left) == _source_hashes(right)
        and _package_input_signature(left) == _package_input_signature(right)
        and _immutable_consumed_inputs(left) == _immutable_consumed_inputs(right)
    )


def _event_failure_evidence(event: Mapping[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    event_stage = normalize_stage(event.get("stage"))
    if event_stage is None or not isinstance(event.get("validation_id"), str):
        return None
    matches = [
        item
        for item in evidence
        if item.get("record_type") is None
        and item.get("validation_id") == event.get("validation_id")
        and normalize_stage(item.get("stage")) == event_stage
        and item.get("_eligible")
    ]
    failed = [item for item in matches if item.get("evidence_verdict") == "confirmed_failed"]
    if len(failed) != 1:
        return None
    return failed[0]


def _case_ids(relation: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(relation, Mapping):
        return ()
    values = relation.get("case_ids", ())
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(item for item in values if isinstance(item, str)))


def _formal_case_ids(snapshot: Mapping[str, Any]) -> set[str]:
    cases = snapshot.get("cases", {})
    return {
        case_id
        for case_id, value in cases.items()
        if isinstance(value, Mapping) and value.get("case_type") == "formal"
    }


def _repair_targets(attempt: Mapping[str, Any]) -> tuple[str, ...]:
    payload = attempt.get("payload", {})
    values = payload.get("pre_action_event_refs", ()) if isinstance(payload, Mapping) else ()
    return tuple(dict.fromkeys(item for item in values if isinstance(item, str)))


def _attempt_source_cases(
    attempt: Mapping[str, Any],
    event_relations: Mapping[str, Mapping[str, Any]],
) -> tuple[set[str], bool]:
    targets = _repair_targets(attempt)
    source_ids: set[str] = set()
    unresolved = not targets
    for event_id in targets:
        relation = event_relations.get(event_id)
        if relation is None:
            unresolved = True
            continue
        source_ids.update(_case_ids(relation))
        if relation.get("state") != "confirmed":
            unresolved = True
    return source_ids, unresolved


def _attempt_component_change(
    attempt: Mapping[str, Any],
    current: dict[str, str],
    evidence: list[dict[str, Any]],
    end_sequence: int,
) -> tuple[bool, bool]:
    details = attempt.get("details", {})
    if not isinstance(details, Mapping) or "change" not in details:
        return False, False
    change = details.get("change")
    if change is None:
        return False, True
    if not isinstance(change, Mapping):
        return True, False
    component = change.get("component")
    before = change.get("before_sha256")
    after = change.get("after_sha256")
    if component not in {"candidate", "testbench"} or not _sha256_value(before) or not _sha256_value(after):
        return True, False
    if before == after:
        return False, True
    if component == "candidate" and details.get("candidate_input_sha256") != before:
        return True, False
    keys = [key for key in current if key == component or (component == "testbench" and key.startswith("testbench:"))]
    matching = [key for key in keys if current.get(key) == before]
    if len(matching) != 1:
        return True, False
    key = matching[0]
    attempt_id = attempt.get("attempt_id")
    corroborated = any(
        item.get("_eligible")
        and item.get("attempt_id") == attempt_id
        and (item.get("_sequence") or 0) <= end_sequence
        and _source_hashes(item).get(key) == after
        for item in evidence
    )
    if not corroborated:
        return True, False
    current[key] = after
    return True, True


def _choose_case_id(root_run_id: str, event_id: str) -> str:
    suffix = sha256(f"{root_run_id}\0{event_id}".encode("utf-8")).hexdigest()[:24]
    return f"case-{suffix}"


def _decision(
    *,
    event: Mapping[str, Any],
    failure: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    baseline: Mapping[str, Any] | None,
    interval_attempt_ids: list[str],
    state: str,
    case_ids: set[str],
    reason: str,
    possible_new_case_root: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "decision_id": f"attribution:{event['event_id']}",
        "root_run_id": event.get("run_id"),
        "event_id": event["event_id"],
        "failure_evidence_id": failure.get("evidence_id"),
        "prior_failure_event_id": None if prior is None else prior.get("event_id"),
        "stage": normalize_stage(event.get("stage")),
        "stage_comparison": (
            None
            if prior is None
            else {
                "prior_stage": normalize_stage(prior.get("stage")),
                "new_stage": normalize_stage(event.get("stage")),
            }
        ),
        "baseline_evidence_id": None if baseline is None else baseline.get("evidence_id"),
        "interval_attempt_ids": interval_attempt_ids,
        "relation": {"state": state, "case_ids": sorted(case_ids)},
        "reason": reason,
        "possible_new_case_root": possible_new_case_root,
        "evidence_view": "agent_safe",
        "hidden_content_persisted": False,
    }


def _adjudicate(
    snapshot: Mapping[str, Any],
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    events_raw = snapshot.get("events", {})
    attempts_raw = snapshot.get("attempts", {})
    formal_cases = _formal_case_ids(snapshot)
    event_relations: dict[str, dict[str, Any]] = {
        event_id: deepcopy(record.get("relation", {"state": "unassigned", "case_ids": []}))
        for event_id, record in events_raw.items()
    }
    event_sequence = {
        event_id: record.get("sequence", 0) for event_id, record in events_raw.items()
    }
    dispatched = [
        {**record, "attempt_id": attempt_id}
        for attempt_id, record in attempts_raw.items()
        if record.get("dispatch_state") == "dispatched"
    ]
    dispatched.sort(key=lambda item: item.get("sequence") or 0)
    failures = []
    for event_id, record in events_raw.items():
        event = record.get("payload", {})
        if (
            event.get("evidence_view") != "agent_safe"
            or event.get("split", "public") != "public"
            or event.get("visibility", "public") not in {"public", "public_generation"}
            or event.get("hidden_content_persisted") is True
            or event.get("evidence_complete") is not True
        ):
            continue
        stage = normalize_stage(event.get("stage"))
        if stage is None:
            continue
        failure = _event_failure_evidence(event, evidence)
        if failure is not None and event.get("run_id") == snapshot.get("run_id"):
            failures.append((failure.get("_sequence") or 0, event_id, event, failure))
    failures.sort(key=lambda item: (item[0], event_sequence.get(item[1], 0)))

    decisions: list[dict[str, Any]] = []
    decisions_by_event: dict[str, dict[str, Any]] = {}
    created_cases: set[str] = set()
    prior_failures: list[tuple[int, str, Mapping[str, Any], Mapping[str, Any]]] = []
    for failure_sequence, event_id, event, failure in failures:
        stage = normalize_stage(event.get("stage"))
        prior_entry = next(
            (item for item in reversed(prior_failures) if item[0] < failure_sequence),
            None,
        )
        baseline = None
        interval_ids: list[str] = []
        reason = "first_formal_failure"
        possible_new_root = False
        current_relation = event_relations.get(event_id, {"state": "unassigned", "case_ids": []})

        if prior_entry is None:
            existing = {case_id for case_id in _case_ids(current_relation) if case_id in formal_cases}
            if existing:
                case_ids = existing
                capture_pending = (
                    current_relation.get("basis") == "formal_capture_pending_adjudication"
                )
                relation_state = (
                    "confirmed"
                    if len(existing) == 1
                    and (
                        current_relation.get("state") == "confirmed"
                        or capture_pending
                    )
                    else "candidate"
                )
            else:
                case_id = _choose_case_id(str(event.get("run_id", "")), event_id)
                case_ids = {case_id}
                created_cases.add(case_id)
                relation_state = "confirmed"
            reason = "first_eligible_formal_failure_starts_case"
        else:
            _, prior_event_id, prior_event, prior_failure = prior_entry
            prior_decision = decisions_by_event.get(prior_event_id, {})
            prior_stage = normalize_stage(prior_event.get("stage"))
            prior_relation = event_relations.get(prior_event_id, {"state": "unassigned", "case_ids": []})
            if prior_stage is None:
                case_ids = set()
                relation_state = "unassigned"
                reason = "prior_failure_stage_unavailable"
            elif _STAGE_ORDER[stage] <= _STAGE_ORDER[prior_stage]:
                possible_new_root = prior_decision.get("possible_new_case_root") is True
                case_ids = {case_id for case_id in _case_ids(prior_relation) if case_id in formal_cases or case_id in created_cases}
                relation_state = prior_relation.get("state", "unassigned")
                if relation_state == "confirmed" and len(case_ids) == 1:
                    reason = "same_or_earlier_stage_joins_prior_chain"
                elif case_ids:
                    relation_state = "candidate"
                    reason = "same_or_earlier_stage_follows_pending_chain"
                else:
                    relation_state = "unassigned"
                    reason = (
                        "same_or_earlier_stage_follows_possible_new_case_root"
                        if possible_new_root
                        else "same_or_earlier_stage_prior_chain_has_no_case_id"
                    )
            else:
                baselines = [
                    item
                    for item in evidence
                    if (item.get("_sequence") or 0) < failure_sequence
                    and item.get("evidence_verdict") == "passed"
                    and normalize_stage(item.get("stage")) is not None
                    and _STAGE_ORDER[normalize_stage(item.get("stage"))] >= _STAGE_ORDER[stage]
                    and _comparable(item, failure)
                ]
                baseline = baselines[-1] if baselines else None
                if baseline is None:
                    case_ids = {
                        case_id
                        for _, prior_id, _, _ in prior_failures
                        for case_id in _case_ids(event_relations.get(prior_id, {}))
                    } | (formal_cases - created_cases)
                    relation_state = "candidate" if case_ids else "unassigned"
                    possible_new_root = True
                    reason = "no_comparable_pass_baseline_possible_new_chain_root"
                else:
                    baseline_sequence = baseline.get("_sequence") or 0
                    baseline_hashes = _source_hashes(baseline)
                    failure_hashes = _source_hashes(failure)
                    current_hashes = dict(baseline_hashes)
                    source_cases: set[str] = set()
                    unresolved = False
                    same_input_conflict = (
                        normalize_stage(baseline.get("stage")) == stage
                        and _same_effective_inputs(baseline, failure)
                        and baseline.get("evidence_verdict") == "passed"
                    )
                    interval = [
                        item
                        for item in dispatched
                        if baseline_sequence < (item.get("sequence") or 0) < failure_sequence
                    ]
                    for attempt in interval:
                        changed, verified = _attempt_component_change(
                            attempt, current_hashes, evidence, failure_sequence
                        )
                        if not changed:
                            if not verified:
                                interval_ids.append(attempt["attempt_id"])
                                ids, _ = _attempt_source_cases(
                                    attempt, event_relations
                                )
                                source_cases.update(ids)
                                unresolved = True
                            continue
                        interval_ids.append(attempt["attempt_id"])
                        ids, target_unresolved = _attempt_source_cases(attempt, event_relations)
                        source_cases.update(ids)
                        unresolved = unresolved or target_unresolved or not verified
                    for key, value in current_hashes.items():
                        if key in failure_hashes and failure_hashes[key] != value:
                            unresolved = True
                    state_changed = any(
                        baseline_hashes.get(key) != failure_hashes.get(key)
                        for key in set(baseline_hashes) | set(failure_hashes)
                    )
                    if same_input_conflict:
                        case_ids = {
                            case_id
                            for _, prior_id, _, _ in prior_failures
                            for case_id in _case_ids(event_relations.get(prior_id, {}))
                        }
                        relation_state = "candidate" if case_ids else "unassigned"
                        reason = "conflicting_pass_and_failure_for_same_effective_inputs"
                        unresolved = True
                    elif not interval_ids and state_changed:
                        case_ids = {
                            case_id
                            for _, prior_id, _, _ in prior_failures
                            for case_id in _case_ids(event_relations.get(prior_id, {}))
                        }
                        relation_state = "candidate" if case_ids else "unassigned"
                        reason = "baseline_to_failure_missing_attempt_or_unrecorded_change"
                        unresolved = True
                    elif len(source_cases) == 1 and not unresolved:
                        case_ids = source_cases
                        relation_state = "confirmed"
                        reason = "interval_modifications_from_one_case"
                    elif source_cases:
                        case_ids = source_cases
                        relation_state = "candidate"
                        reason = "interval_has_multiple_or_unverified_case_sources"
                    else:
                        case_ids = set()
                        relation_state = "unassigned"
                        reason = "interval_source_unassigned_or_incomplete"

        decision = _decision(
            event=event,
            failure=failure,
            prior=None if prior_entry is None else prior_entry[2],
            baseline=baseline,
            interval_attempt_ids=list(dict.fromkeys(interval_ids)),
            state=relation_state,
            case_ids=case_ids,
            reason=reason,
            possible_new_case_root=possible_new_root,
        )
        event_relations[event_id] = {
            "state": relation_state,
            "case_ids": sorted(case_ids),
            "basis": decision["decision_id"],
        }
        decisions.append(decision)
        decisions_by_event[event_id] = decision
        prior_failures.append((failure_sequence, event_id, event, failure))

    attempt_relations: dict[str, dict[str, Any]] = {}
    for attempt in dispatched:
        attempt_id = attempt["attempt_id"]
        targets = _repair_targets(attempt)
        if not targets:
            continue
        target_relations = [event_relations.get(event_id) for event_id in targets]
        if any(relation is None for relation in target_relations):
            attempt_relations[attempt_id] = {"state": "unassigned", "case_ids": [], "basis": "repair_target_event_unavailable"}
            continue
        case_ids = {case_id for relation in target_relations for case_id in _case_ids(relation)}
        if len(target_relations) == 1 and target_relations[0].get("state") == "confirmed" and len(case_ids) == 1:
            state = "confirmed"
        elif case_ids:
            state = "candidate"
        else:
            state = "unassigned"
        attempt_relations[attempt_id] = {
            "state": state,
            "case_ids": sorted(case_ids),
            "basis": "repair_target_event:" + ",".join(targets),
        }
        details = attempt.get("details", {})
        validation = details.get("validation", {}) if isinstance(details, Mapping) else {}
        validation_refs = details.get("validation_event_refs", ()) if isinstance(details, Mapping) else ()
        if not isinstance(validation_refs, (list, tuple)):
            validation_refs = ()
        if isinstance(validation, Mapping):
            nested_refs = validation.get("event_refs", ())
            if isinstance(nested_refs, (list, tuple)):
                validation_refs = (*validation_refs, *nested_refs)
        for event_id in dict.fromkeys(
            item for item in validation_refs if isinstance(item, str)
        ):
            if event_id not in event_relations or any(
                decision["event_id"] == event_id for decision in decisions
            ):
                continue
            event_relations[event_id] = {
                "state": state,
                "case_ids": sorted(case_ids),
                "basis": f"attempt_validation_ref:{attempt_id}",
            }
    return decisions, event_relations, attempt_relations, created_cases


def adjudicate_formal_root_run(root_record: Any) -> dict[str, Any]:
    """Apply deterministic ordinary-attribution decisions to a root record."""
    snapshot = root_record.snapshot()
    evidence = _verified_evidence(root_record, snapshot)
    decisions, event_relations, attempt_relations, created_cases = _adjudicate(
        snapshot, evidence
    )
    event_payloads = snapshot.get("events", {})
    for decision in decisions:
        event_id = decision["event_id"]
        event = event_payloads[event_id]["payload"]
        for case_id in decision["relation"]["case_ids"]:
            if case_id in created_cases:
                root_record.register_case(case_id, case_type="formal")
        relation = decision["relation"]
        root_record.set_case_relation(
            "event",
            event_id,
            state=relation["state"],
            case_ids=tuple(relation["case_ids"]),
            basis=decision["decision_id"],
        )
        root_record.record_attribution_decision(decision["decision_id"], decision)
    for event_id, relation in event_relations.items():
        if event_id not in event_payloads:
            continue
        previous = event_payloads[event_id].get("relation", {})
        if (
            previous.get("state") == relation.get("state")
            and previous.get("case_ids", []) == relation.get("case_ids", [])
            and previous.get("basis") == relation.get("basis")
        ):
            continue
        root_record.set_case_relation(
            "event",
            event_id,
            state=relation["state"],
            case_ids=tuple(relation["case_ids"]),
            basis=str(relation.get("basis") or "stage4_validation_event_reference"),
        )
    for attempt_id, relation in attempt_relations.items():
        for case_id in relation["case_ids"]:
            if case_id in created_cases:
                root_record.register_case(case_id, case_type="formal")
        root_record.set_case_relation(
            "attempt",
            attempt_id,
            state=relation["state"],
            case_ids=tuple(relation["case_ids"]),
            basis=relation["basis"],
        )
    final_snapshot = root_record.snapshot()
    confirmed_case_ids = {
        case_id
        for collection in ("events", "attempts")
        for record in final_snapshot.get(collection, {}).values()
        if (
            collection != "attempts"
            or record.get("dispatch_state") == "dispatched"
        )
        for case_id in record.get("relation", {}).get("case_ids", ())
        if record.get("relation", {}).get("state") == "confirmed"
        and final_snapshot.get("cases", {}).get(case_id, {}).get("case_type") == "formal"
    }
    return {
        "decision_count": len(decisions),
        "case_ids": sorted(confirmed_case_ids),
        "decisions": decisions,
    }
