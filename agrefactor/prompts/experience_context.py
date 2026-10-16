"""Build safe experience queries and prompt selections for one repair call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from difflib import unified_diff
from hashlib import sha256
from functools import lru_cache
import json
import re
from pathlib import Path
from typing import Any, Callable

from agrefactor.evidence.case_store import ExperienceStore
from agrefactor.evidence.experience_retrieval import (
    CurrentCaseQuery,
    ExperienceRetrievalResult,
    retrieve_experiences,
)


_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{1,63}")
_CODE = re.compile(r"[^a-z0-9_.-]+")
_SOURCE_FIELDS = (
    "symbol",
    "function",
    "function_name",
    "top_function",
    "type_name",
    "interface_type",
    "argument_name",
    "top_argument",
)
_EVIDENCE_FIELDS = (
    "evidence_class",
    "evidence_classes",
    "evidence_kind",
    "evidence_type",
    "evidence_state",
)


def _codes(values: Sequence[str], *, limit: int = 32) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        code = _CODE.sub("_", value.casefold()).strip("_.-")[:120]
        if code and code not in result:
            result.append(code)
        if len(result) >= limit:
            break
    return tuple(result)


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def current_case_query_from_feedback(
    report: Any,
    *,
    stage: str,
    visibility: str,
    owner: str,
    components: Sequence[str],
    validation_contract: str | None = None,
    toolchain: str | None = None,
    attempted_change_fingerprints: Sequence[str] = (),
) -> CurrentCaseQuery:
    """Project only allowlisted structured features from Public feedback."""

    metadata = getattr(report, "metadata", {})
    if not isinstance(metadata, Mapping) or metadata.get("evidence_view") != "agent_safe":
        raise ValueError("experience queries require agent_safe feedback")
    if visibility in {"hidden", "hidden_evaluation", "hidden-generation"}:
        raise ValueError("formal Hidden feedback cannot be queried")
    items = tuple(getattr(report, "items", ()))
    codes: list[str] = []
    terms: list[str] = []
    owners: list[str] = []
    evidence_terms: list[str] = []
    source_terms: list[str] = []
    for item in items:
        item_metadata = getattr(item, "metadata", {})
        if not isinstance(item_metadata, Mapping):
            item_metadata = {}
        category = getattr(getattr(item, "category", None), "value", None)
        item_owner = getattr(getattr(item, "owner", None), "value", None)
        owners.extend((item_owner,) if isinstance(item_owner, str) else ())
        codes.extend(
            value
            for name in ("message_id", "diagnostic_code", "category_id", "parser_rule")
            for value in _strings(item_metadata.get(name))
        )
        if isinstance(category, str):
            codes.append(category)
        for value in (
            getattr(item, "summary", None),
            getattr(item, "detail", None),
        ):
            if isinstance(value, str):
                terms.extend(_TOKEN.findall(value))
        for name in _EVIDENCE_FIELDS:
            evidence_terms.extend(_strings(item_metadata.get(name)))
        for name in _SOURCE_FIELDS:
            source_terms.extend(_strings(item_metadata.get(name)))

    return CurrentCaseQuery(
        stage=stage,
        visibility=visibility,
        owner=owner,
        diagnostic_codes=_codes(codes),
        diagnostic_terms=_codes((*codes, *terms)),
        owner_clues=_codes(owners),
        components=tuple(components),
        evidence_terms=_codes(evidence_terms),
        source_terms=_codes(source_terms),
        validation_contract=validation_contract,
        toolchain=toolchain,
        attempted_change_fingerprints=tuple(attempted_change_fingerprints),
    )


def render_experience_reference(candidate: Any) -> str:
    record = candidate.item.record
    label = "positive" if candidate.polarity.value == "positive" else "negative"
    lines = [
        f"[{label} experience {record.experience_id}]",
        f"Applies when: {', '.join(record.applicability_conditions)}",
        f"Action: {record.action_summary}",
        f"Validated scope: {record.validation_scope} / {record.validation_contract}",
        f"Observed result: {record.failure_code or 'passed'}",
        "Historical reference only; follow current evidence and the active repair contract.",
    ]
    if candidate.change_available:
        lines.extend(("Verified change:", candidate.item.change.patch))
    if candidate.previously_attempted:
        lines.append(
            "A similar change was already attempted in this case; inspect new evidence before proposing an improved version."
        )
    return "\n".join(lines)


def change_fingerprints_from_attempts(attempts: Sequence[Any]) -> tuple[str, ...]:
    fingerprints: list[str] = []
    for attempt in attempts:
        before = getattr(attempt, "input_candidate", None)
        after = getattr(attempt, "proposal", None)
        if not isinstance(before, str) or not isinstance(after, str) or before == after:
            continue
        lines = list(
            unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile="a/candidate.cpp",
                tofile="b/candidate.cpp",
                lineterm="",
                n=3,
            )
        )
        patch = "\n".join(lines) + "\n"
        fingerprints.append(
            sha256(patch.encode("utf-8")).hexdigest()
        )
    return tuple(dict.fromkeys(fingerprints))


@dataclass(frozen=True, slots=True)
class ExperiencePromptSelection:
    retrieval: ExperienceRetrievalResult
    snippets: tuple[str, ...]
    budget: Any
    audit: Mapping[str, Any]


def select_experience_prompt_context(
    query: CurrentCaseQuery,
    *,
    store_root: str | Path,
    counter: Any | None,
    provider: str,
    model_id: str,
    base_url: str,
    request_mode: str,
    call_parameters: Mapping[str, Any],
    render_messages: Callable[[tuple[str, ...]], Sequence[Any]],
) -> ExperiencePromptSelection:
    from agrefactor.models.model_token_counting import select_experience_context

    snapshot = ExperienceStore(store_root).read_snapshot()
    retrieval = retrieve_experiences(query, snapshot)
    _messages, budget = select_experience_context(
        retrieval,
        counter=counter,
        provider=provider,
        model_id=model_id,
        base_url=base_url,
        request_mode=request_mode,
        call_parameters=call_parameters,
        render_messages=render_messages,
    )
    selected = set(budget.selected_ids)
    skipped = dict(budget.skipped)
    candidates = (*retrieval.positive, *retrieval.negative)
    audit = {
        "query_sha256": sha256(
            json.dumps(
                asdict(query),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "store_revision": snapshot.revision,
        "provider": provider,
        "effective_model": model_id,
        "base_url": base_url.rstrip("/"),
        "request_mode": request_mode,
        "tokenizer": (
            None
            if counter is None
            else {
                "verification_status": counter.config.verification_status,
                "context_window": counter.config.context_window,
                "safe_margin_tokens": counter.config.safe_margin_tokens,
                "tokenizer_sha256": dict(counter.config.tokenizer_hashes),
            }
        ),
        "candidate_count": len(candidates),
        "excluded": [
            {"experience_id": identity, "reason": reason}
            for identity, reason in retrieval.excluded
        ],
        "candidates": [
            {
                "experience_id": item.item.record.experience_id,
                "polarity": item.polarity.value,
                "score": list(item.score),
                "reasons": list(item.reasons),
                "previously_attempted": item.previously_attempted,
                "injected": item.item.record.experience_id in selected,
                "excluded_reason": skipped.get(item.item.record.experience_id),
            }
            for item in candidates
        ],
        "injected_ids": list(budget.selected_ids),
        "skipped": [
            {"experience_id": identity, "reason": reason}
            for identity, reason in budget.skipped
        ],
        "base_tokens": budget.base_tokens,
        "final_tokens": budget.final_tokens,
        "experience_budget": budget.experience_budget,
        "context_window": budget.context_window,
        "output_reserve": budget.output_reserve,
        "safe_margin": budget.safe_margin,
        "reason": budget.reason,
        "evidence_view": query.visibility,
        "query_text_persisted": False,
    }
    if query.visibility == "hidden_generation":
        audit = {
            "evidence_view": "hidden_generation",
            "audit_persisted": False,
            "public_projection_only": True,
        }
    if not budget.selected_ids and budget.reason:
        for item in audit.get("candidates", ()):
            if item["excluded_reason"] is None:
                item["excluded_reason"] = budget.reason
    return ExperiencePromptSelection(
        retrieval=retrieval,
        snippets=budget.selected_snippets,
        budget=budget,
        audit=audit,
    )


def candidate_experience_context_provider(
    *,
    store_root: str | Path,
    counter: Any | None,
    effective_config: Any,
    task: Any,
):
    """Create the per-attempt Candidate lookup used by the repair loop."""
    from agrefactor.models import ModelCallRole

    call_parameters = effective_config.parameters_for_call(
        ModelCallRole.CANDIDATE_REPAIR
    )

    def provide(
        *,
        feedback: Any,
        route_decision: Any,
        failure_state: Any,
        attempts: Sequence[Any],
        render_messages: Callable[[tuple[str, ...]], Sequence[Any]],
        **_: Any,
    ) -> tuple[tuple[str, ...], Mapping[str, Any]]:
        selected_ids = set(route_decision.selected_feedback_ids)
        selected = tuple(
            item
            for item in feedback.items
            if item.feedback_id in selected_ids
        )
        stages = tuple(
            getattr(getattr(item, "stage", None), "value", "")
            for item in selected
        )
        stage = next((value for value in stages if value), failure_state.value)
        owner_values = {
            getattr(getattr(item, "owner", None), "value", "unknown")
            for item in selected
        }
        owner = (
            next(iter(owner_values))
            if len(owner_values) == 1
            else "unknown"
        )
        if owner not in {"candidate", "testbench", "stub"}:
            owner = "unknown"
        query = current_case_query_from_feedback(
            feedback,
            stage=stage,
            visibility="public",
            owner=owner,
            components=(owner,) if owner != "unknown" else (),
            validation_contract=feedback.metadata.get(
                "validation_contract"
            ),
            toolchain=None,
            attempted_change_fingerprints=(
                change_fingerprints_from_attempts(attempts)
            ),
        )
        try:
            selection = select_experience_prompt_context(
                query,
                store_root=store_root,
                counter=counter,
                provider=effective_config.provider_name,
                model_id=effective_config.model_id,
                base_url=effective_config.base_url or "",
                request_mode="chat_completions",
                call_parameters=call_parameters,
                render_messages=render_messages,
            )
            return selection.snippets, selection.audit
        except Exception as exc:
            return (), {
                "status": "unavailable",
                "reason": "experience_lookup_failed",
                "error_type": type(exc).__name__,
                "query_text_persisted": False,
            }

    return provide


@lru_cache(maxsize=1)
def default_model_token_counter():
    from agrefactor.models.model_token_counting import ModelTokenCounter

    manifest = (
        Path(__file__).resolve().parents[1]
        / "models"
        / "tokenization_sources"
        / "deepseek_v4_flash.json"
    )
    try:
        return ModelTokenCounter.from_manifest(manifest)
    except Exception:
        return None


def testbench_experience_context_provider(
    *,
    store_root: str | Path,
    counter: Any | None,
    effective_config: Any,
    task: Any,
):
    """Create a Public Testbench lookup for preparation and repair loops."""
    from agrefactor.evaluation import (
        TestbenchPreflightFeedbackAdapter,
        TestbenchPreflightFeedbackViewAdapter,
    )
    from agrefactor.models import ModelCallRole

    call_parameters = effective_config.parameters_for_call(
        ModelCallRole.TESTBENCH_REPAIR
    )

    def provide(request: Any, *, render_messages=None):
        if not callable(render_messages):
            raise TypeError("render_messages is required for Testbench budgeting")
        report = request.runtime_feedback
        failure_state = request.failure_state
        if report is None:
            operator_report = TestbenchPreflightFeedbackAdapter().to_operator_report(
                request.preflight,
                report_id=f"testbench-experience-{request.attempt}.operator",
            )
            report = TestbenchPreflightFeedbackViewAdapter().to_agent_report(
                operator_report,
                report_id=f"testbench-experience-{request.attempt}.agent",
            )
            stage = "preflight"
            owner = getattr(request.preflight.failure_owner, "value", "unknown")
        else:
            stage = next(
                (
                    item.stage.value
                    for item in report.items
                    if item.blocking
                ),
                getattr(failure_state, "value", "public_evaluation"),
            )
            owners = {
                item.owner.value for item in report.items if item.blocking
            }
            owner = next(iter(owners)) if len(owners) == 1 else "unknown"
        if owner not in {"candidate", "testbench", "stub"}:
            owner = "unknown"
        attempted = tuple(
            sha256(item.case_change_patch.encode("utf-8")).hexdigest()
            for item in getattr(request, "attempted_changes", ())
            if isinstance(getattr(item, "case_change_patch", None), str)
            and item.case_change_patch
        )
        query = current_case_query_from_feedback(
            report,
            stage=stage,
            visibility="public",
            owner=owner,
            components=(owner,) if owner != "unknown" else (),
            validation_contract=report.metadata.get("validation_contract"),
            attempted_change_fingerprints=attempted,
        )

        selected = select_experience_prompt_context(
            query,
            store_root=store_root,
            counter=counter,
            provider=effective_config.provider_name,
            model_id=effective_config.model_id,
            base_url=effective_config.base_url or "",
            request_mode="chat_completions",
            call_parameters=call_parameters,
            render_messages=render_messages,
        )
        return selected.snippets, selected.audit

    return provide


def attach_experience_audit(prompt: Any, audit: Mapping[str, Any]) -> Any:
    from dataclasses import replace

    if not hasattr(prompt, "manifest"):
        raise TypeError("prompt must expose a manifest")
    return replace(
        prompt,
        manifest={
            **dict(prompt.manifest),
            "experience_retrieval": dict(audit),
        },
    )
