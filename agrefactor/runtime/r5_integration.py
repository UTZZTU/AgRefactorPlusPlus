"""R5 post-baseline repair integration using the accepted R4 safety kernel.

The module consumes a real R2 shadow result and a frozen R5 arm definition.
It does not create an entrypoint or a Vitis path.  A2/A3 use the adjacent R5
authorization without fabricating a memory revision; A4-A6 additionally bind
the real Gate, snapshot, revision, and candidate-only payload manifest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from agrefactor.config import EvaluationSplit
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    build_candidate_compile_repair_prompt,
    build_candidate_csynth_repair_prompt,
    build_candidate_public_csim_repair_prompt,
    build_candidate_public_cosim_repair_prompt,
)
from agrefactor.recovery.episode_ledger import (
    AppendOnlyEpisodeLedger,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
    canonical_sha256,
)
from agrefactor.recovery.gated_candidate_repair import (
    R4CanaryManifest,
    R4CandidateRepairController,
    R4KillSwitchState,
    R4_CONTROLLER_CONTRACT_SHA256,
    R5CandidateRepairAuthorization,
    R5ExecutionInput,
)
from agrefactor.recovery.memory_gate import (
    ApplicabilityGate,
    GateDecision,
    PatternLifecycle,
    RepairPatternRevision,
)
from agrefactor.recovery.pattern_lifecycle import (
    Lifecycle,
    LifecycleReduction,
    R5PatternRevision,
)
from agrefactor.recovery.policy import (
    RecoveryAction,
    RecoveryAuthority,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
)
from agrefactor.recovery.r4_budget import (
    R4ReservePlan,
    build_r4_budget_actual,
    build_r4_reserve_plan_from_handlers,
    record_r4_budget_reservation,
)
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.recovery.r5_authorization import (
    R5AuthorizationMode,
    R5ResearchAuthorization,
)
from agrefactor.recovery.r5_memory_payload import (
    R5MemoryPayload,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    memory_payload_manifest_sha256,
    render_candidate_memory_snippets,
)
from agrefactor.recovery.r5_snapshot_builder import R5MemorySnapshot
from agrefactor.recovery.shadow_advisor import (
    CalibrationCertificate,
    verify_calibrated_advisory,
)

from .r4_integration import (
    CandidateModelR4MutationAdapter,
    ExistingFormalR4Auditor,
    ExistingR4ValidationAdapter,
    R4MutationAdapter,
)
from .r5_profile import R5Arm, R5Profile


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True)
    )


def _budget_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: after[key] - before[key]
        for key in (
            "llm_calls",
            "tool_calls",
            "compile_calls",
            "csim_calls",
            "csynth_calls",
            "cosim_calls",
            "tokens",
            "cost_usd",
            "elapsed_s",
        )
    }


def _enum(enum_type: Any, value: Any, fallback: Any) -> Any:
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        return fallback


def _advisory_failure_class(advisory: Mapping[str, Any]) -> str | None:
    value = advisory.get(
        "suspected_failure_class",
        advisory.get("failure_class"),
    )
    return value if isinstance(value, str) and value else None


class R5CandidatePromptFactory:
    """Build the existing Candidate repair prompt from agent-safe evidence."""

    def __init__(
        self,
        *,
        request: Any,
        approved_memory_snippets: Sequence[str],
    ) -> None:
        self._request = request
        self._approved_memory_snippets = tuple(approved_memory_snippets)

    def build(
        self,
        *,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        task: Any,
        candidate: str,
    ) -> Any:
        raw_items = event.get("diagnostic_items", ())
        items: list[FeedbackItem] = []
        iterable = raw_items if isinstance(raw_items, (list, tuple)) else ()
        for index, raw in enumerate(iterable, start=1):
            if not isinstance(raw, Mapping):
                continue
            deterministic_owner = raw.get("owner")
            advisory_owner = advisory.get("suspected_owner")
            effective_owner = deterministic_owner
            if (
                deterministic_owner in {None, "unknown"}
                and advisory.get("calibration_verified") is True
                and advisory_owner == "candidate"
            ):
                effective_owner = advisory_owner
            evidence_ref = str(
                raw.get("evidence_ref")
                or f"{event.get('event_id', 'event')}.item.{index}"
            )
            items.append(
                FeedbackItem(
                    feedback_id=evidence_ref,
                    stage=_enum(
                        FeedbackStage,
                        raw.get("stage"),
                        FeedbackStage.CSYNTH,
                    ),
                    category=_enum(
                        FeedbackCategory,
                        raw.get("category"),
                        FeedbackCategory.UNKNOWN,
                    ),
                    severity=_enum(
                        FeedbackSeverity,
                        raw.get("severity"),
                        FeedbackSeverity.ERROR,
                    ),
                    owner=_enum(
                        FeedbackOwner,
                        effective_owner,
                        FeedbackOwner.UNKNOWN,
                    ),
                    summary=str(raw.get("summary") or "Agent-safe diagnostic"),
                    detail=str(raw.get("detail") or "") or None,
                    source="r5_agent_safe_event",
                    evidence_ref=evidence_ref,
                    metadata={
                        "diagnostic_code": raw.get("diagnostic_code"),
                        "deterministic_owner": deterministic_owner,
                        "advisory_owner": advisory_owner,
                        "owner_projection": (
                            "calibrated_advisory"
                            if effective_owner != deterministic_owner
                            else "deterministic"
                        ),
                    },
                )
            )
        if not items:
            refs = tuple(event.get("evidence_refs", ()))
            evidence_ref = str(refs[0] if refs else event.get("event_id", "event"))
            items.append(
                FeedbackItem(
                    feedback_id=evidence_ref,
                    stage=FeedbackStage.CSYNTH,
                    category=FeedbackCategory.UNKNOWN,
                    severity=FeedbackSeverity.ERROR,
                    owner=FeedbackOwner.UNKNOWN,
                    summary="Agent-safe diagnostic evidence is unavailable",
                    detail="No diagnostic item was retained for this repair request.",
                    source="r5_agent_safe_event",
                    evidence_ref=evidence_ref,
                )
            )
        feedback = FeedbackReport(
            report_id=str(event.get("event_id") or "r5-event"),
            source="r5_agent_safe_event",
            items=tuple(items),
            source_evidence={"evidence_refs": list(event.get("evidence_refs", ()))},
            metadata={
                "advisory_owner": advisory.get("suspected_owner"),
                "evidence_view": "agent_safe",
                "feedback_visible_to_agent": True,
            },
        )
        inputs = CandidateRepairPromptInputs(
            task=task,
            feedback=feedback,
            candidate_code=candidate,
            original_code=self._request.original_code,
            public_testbench_code=(
                self._request.prompt_public_testbench_code
                or self._request.preflight_testbench_code
            ),
            attempt=1,
            max_attempts=1,
            family_instruction=self._request.family_instruction,
            approved_memory_snippets=self._approved_memory_snippets,
        )
        stage = str(event.get("stage", "csynth"))
        if stage in {"preflight", "compile", "link", "static_check"}:
            return build_candidate_compile_repair_prompt(inputs)
        if stage in {"public_evaluation", "csim", "test"}:
            return build_candidate_public_csim_repair_prompt(inputs)
        if stage in {"public_cosim", "cosim"}:
            return build_candidate_public_cosim_repair_prompt(inputs)
        return build_candidate_csynth_repair_prompt(inputs)


@dataclass(frozen=True, slots=True)
class R5IntegrationConfig:
    profile: R5Profile
    canary: R4CanaryManifest
    execution_identity: Mapping[str, Any]
    calibration_certificate: CalibrationCertificate
    episode_ledger_root: str
    campaign_manifest_sha256: str
    mutation_adapter: R4MutationAdapter
    memory_snapshot: R5MemorySnapshot | None = None
    revision: R5PatternRevision | None = None
    lifecycle_reduction: LifecycleReduction | None = None
    memory_payloads: tuple[R5MemoryPayload, ...] = ()
    retrieval_manifest_sha256: str | None = None
    reserve_plan: R4ReservePlan | None = None
    validation_wall_time_s: float = 1200.0
    kill_switch: R4KillSwitchState = R4KillSwitchState()

    def __post_init__(self) -> None:
        if not isinstance(self.profile, R5Profile) or self.profile.arm not in {
            R5Arm.A2,
            R5Arm.A3,
            R5Arm.A4,
            R5Arm.A5,
            R5Arm.A6,
        }:
            raise TypeError("R5 integration requires a mutation arm profile")
        if not isinstance(self.canary, R4CanaryManifest):
            raise TypeError("canary must be R4CanaryManifest")
        if canonical_artifact_sha256(self.canary.to_dict()) != self.canary.manifest_sha256:
            raise ValueError("R5 canary manifest is not content-addressed")
        if not isinstance(self.calibration_certificate, CalibrationCertificate):
            raise TypeError("calibration_certificate must be CalibrationCertificate")
        if not self.calibration_certificate.accepted:
            raise ValueError("R5 requires an accepted calibration certificate")
        if not callable(getattr(self.mutation_adapter, "mutate", None)):
            raise TypeError("mutation_adapter must provide mutate")
        if not isinstance(self.episode_ledger_root, str) or not self.episode_ledger_root.strip():
            raise ValueError("episode_ledger_root must be non-empty")
        if not isinstance(self.campaign_manifest_sha256, str) or not _SHA256.fullmatch(
            self.campaign_manifest_sha256
        ):
            raise ValueError("campaign_manifest_sha256 must be SHA-256")
        payloads = tuple(self.memory_payloads)
        if any(not isinstance(item, R5MemoryPayload) for item in payloads):
            raise TypeError("memory_payloads must contain R5MemoryPayload")
        object.__setattr__(self, "memory_payloads", payloads)
        arm = self.profile.arm
        if arm is R5Arm.A2:
            if payloads or self.retrieval_manifest_sha256 is not None:
                raise ValueError("A2 must not carry memory")
        elif arm is R5Arm.A3:
            if payloads or not isinstance(self.retrieval_manifest_sha256, str) or not _SHA256.fullmatch(
                self.retrieval_manifest_sha256
            ):
                raise ValueError("A3 requires only a content-addressed retrieval manifest")
        else:
            if self.retrieval_manifest_sha256 is not None:
                raise ValueError("A4-A6 cannot use a similarity-only manifest")
            if not isinstance(self.memory_snapshot, R5MemorySnapshot) or not isinstance(
                self.revision, R5PatternRevision
            ):
                raise TypeError("A4-A6 require a frozen snapshot and revision")
            if not isinstance(self.lifecycle_reduction, LifecycleReduction):
                raise TypeError("A4-A6 require the deterministic lifecycle reduction")
            if (
                self.lifecycle_reduction.revision.revision_sha256
                != self.revision.revision_sha256
            ):
                raise ValueError("lifecycle reduction does not produce the bound revision")
            if self.revision.lifecycle is not Lifecycle.TRUSTED:
                raise ValueError("A4-A6 require a real Trusted revision")
            if (
                self.revision.memory_payload_manifest_sha256
                != R5_MEMORY_PAYLOAD_POLICY_SHA256
            ):
                raise ValueError("revision does not bind the frozen payload policy")
            if self.calibration_certificate.certificate_id not in self.revision.calibration_refs:
                raise ValueError("Trusted revision does not reference the active calibration")
            if self.revision.revision_sha256 not in self.memory_snapshot.selected_revision_hashes:
                raise ValueError("revision is not selected by the frozen snapshot")
            if not payloads:
                raise ValueError("A4-A6 require gated memory payloads")
            if any(
                item.revision_sha256 != self.revision.revision_sha256
                or item.snapshot_sha256 != self.memory_snapshot.snapshot_sha256
                for item in payloads
            ):
                raise ValueError("payload revision/snapshot binding mismatch")
            if arm is R5Arm.A4 and self.revision.negative_episode_refs:
                raise ValueError("A4 positive-only memory cannot include negative support")
        if self.reserve_plan is not None and not isinstance(self.reserve_plan, R4ReservePlan):
            raise TypeError("reserve_plan must be R4ReservePlan or None")
        if isinstance(self.validation_wall_time_s, bool) or self.validation_wall_time_s <= 0:
            raise ValueError("validation_wall_time_s must be positive")

    @property
    def approved_memory_snippets(self) -> tuple[str, ...]:
        if self.memory_payloads:
            return render_candidate_memory_snippets(self.memory_payloads)
        if self.profile.arm is R5Arm.A3 and self.retrieval_manifest_sha256:
            return (
                json.dumps(
                    {
                        "retrieval_manifest_sha256": self.retrieval_manifest_sha256,
                        "memory_mode": "similarity_only",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        return ()


class ExistingOrchestratorR5Integration:
    """Execute one R5 mutation arm from an existing baseline terminal."""

    def __init__(
        self,
        config: R5IntegrationConfig,
        *,
        gate: ApplicabilityGate | None = None,
        policy: RecoveryPolicy | None = None,
        auditor: Any | None = None,
    ) -> None:
        if not isinstance(config, R5IntegrationConfig):
            raise TypeError("config must be R5IntegrationConfig")
        self._config = config
        self._gate = gate or ApplicabilityGate()
        self._policy = policy or RecoveryPolicy()
        self._auditor = auditor or ExistingFormalR4Auditor()

    @property
    def approved_memory_snippets(self) -> tuple[str, ...]:
        return self._config.approved_memory_snippets

    @property
    def profile(self) -> R5Profile:
        return self._config.profile

    def run_from_existing_orchestrator(
        self,
        *,
        context: Any,
        request: Any,
        main_result: Any,
        handler_factory: Any,
    ) -> Mapping[str, Any]:
        return self.run(
            context=context,
            request=request,
            main_result=main_result,
            handler_factory=handler_factory,
        )

    def run(
        self,
        *,
        context: Any,
        request: Any,
        main_result: Any,
        handler_factory: Any,
    ) -> Mapping[str, Any]:
        if request.llm_advisory_mode != "candidate-only":
            raise ValueError("R5 mutation extension requires candidate-only mode")
        if request.r5_arm != self._config.profile.arm.value:
            raise ValueError("request arm does not match R5 integration profile")
        selected = self._eligible_pair(main_result)
        if selected is None:
            return self._terminal("abstained", "eligible_r2_event_not_unique")
        event, shadow, advisory = selected
        calibration = verify_calibrated_advisory(
            self._config.calibration_certificate,
            shadow=shadow,
        )
        if not calibration.verified:
            return self._terminal(
                "abstained",
                "r2_calibration_unverified",
                calibration=calibration.to_dict(),
            )
        advisory["advisory_id"] = canonical_artifact_sha256(
            {
                "event_id": event["event_id"],
                "request_sha256": shadow.get("request_sha256"),
                "advisory": advisory,
                "provider_identity": shadow.get("provider_identity", {}),
                "calibration_certificate_id": calibration.certificate_id,
            }
        )
        advisory["calibration_certificate_id"] = calibration.certificate_id
        advisory["calibration_verified"] = True
        if not set(advisory.get("evidence_refs", ())).issubset(
            set(event.get("evidence_refs", ()))
        ):
            return self._terminal("invalid_evidence", "advisory_evidence_out_of_scope")

        gate_map: dict[str, Any] = {
            "decision": "not_applicable",
            "reasons": [],
            "evidence_refs": list(advisory.get("evidence_refs", ())),
            "checked_order": [],
            "contract_hash": None,
        }
        if self._config.profile.arm in {R5Arm.A4, R5Arm.A5, R5Arm.A6}:
            gate_result = self._evaluate_gate(event=event, advisory=advisory)
            gate_map = gate_result.to_dict()
            if gate_result.decision is not GateDecision.ACCEPT:
                episode_path, episode_hash = self._append_episode(
                    event=event,
                    advisory=advisory,
                    gate=gate_map,
                    authorization=None,
                    result=None,
                    validation=None,
                    budget_actual={},
                    outcome=R5EpisodeOutcome.ABSTAINED,
                    reason="gate_" + gate_result.decision.value,
                )
                return self._terminal(
                    "abstained",
                    "gate_" + gate_result.decision.value,
                    gate=gate_map,
                    episode_path=episode_path,
                    episode_hash=episode_hash,
                )

        testbench_hashes = {
            "preflight": _text_sha(request.preflight_testbench_code),
            **{
                key: _text_sha(value)
                for key, value in request.suite_testbench_codes.items()
            },
        }
        deterministic_terminal = {
            "validation_id": main_result.validation_id,
            "state": main_result.last_validation_state.value,
            "status": main_result.status.value,
            "candidate_sha256": _text_sha(main_result.final_candidate),
        }
        preauthorization_id = canonical_artifact_sha256(
            {
                "event": event["event_id"],
                "terminal": deterministic_terminal,
                "arm": self._config.profile.arm.value,
                "snapshot": (
                    None
                    if self._config.memory_snapshot is None
                    else self._config.memory_snapshot.snapshot_sha256
                ),
            }
        )
        plan, handlers = self._validation_plan(
            context=context,
            request=request,
            handler_factory=handler_factory,
            candidate=main_result.final_candidate,
            validation_id=f"{preauthorization_id}.r5",
        )
        del plan
        reserve_plan = build_r4_reserve_plan_from_handlers(
            handlers,
            wall_time_s=self._config.validation_wall_time_s,
            expected_plan=self._config.reserve_plan,
        )
        budget_before = context.budget.snapshot().to_dict()
        ledger = RecoveryLedger(self._policy)
        policy_request = RecoveryRequest(
            action=RecoveryAction.REPAIR,
            role=RecoveryRole.CANDIDATE,
            stage=RecoveryStage(event["stage"]),
            evidence_view="agent_safe",
            owner_authority=RecoveryAuthority.LLM_ADVISORY,
            lineage_id=event["run_id"],
            physical_tool_launched=True,
            evidence_complete=True,
            advisory_mode="candidate-only",
        )
        try:
            policy_decision = ledger.reserve(
                policy_request,
                budget=context.budget,
                restart_reserve=reserve_plan.to_budget_request(),
            )
            budget_after_admission = context.budget.snapshot().to_dict()
        except Exception as exc:
            return self._terminal(
                "inconclusive",
                "policy_ledger_or_budget_denied",
                detail=type(exc).__name__,
            )
        policy_artifact = policy_decision.to_dict()
        ledger_event = ledger.events[-1].to_dict()
        reservation = record_r4_budget_reservation(
            reserve_plan,
            budget_before=budget_before,
            budget_after_admission=budget_after_admission,
        )
        research = self._build_research_authorization(
            event=event,
            advisory=advisory,
            gate=gate_map,
            policy_decision=policy_artifact,
            ledger_event=ledger_event,
            budget_reservation=reservation.to_dict(),
        )
        authorization = R5CandidateRepairAuthorization(
            research_authorization=research,
            run_id=event["run_id"],
            event_ref=event["event_id"],
            before_candidate_sha256=_text_sha(main_result.final_candidate),
            canary_manifest_sha256=self._config.canary.manifest_sha256,
            deterministic_terminal_ref=canonical_artifact_sha256(
                deterministic_terminal
            ),
            revision_sha256=(
                None
                if self._config.revision is None
                else self._config.revision.revision_sha256
            ),
        )
        provenance_reasons = self._provenance_reasons(
            event=event,
            advisory=advisory,
            authorization=authorization,
            deterministic_terminal=deterministic_terminal,
            candidate=main_result.final_candidate,
            original=request.original_code,
        )
        if provenance_reasons:
            return self._terminal(
                "invalid_evidence",
                "r5_provenance_validation_failed",
                reasons=provenance_reasons,
            )
        execution = R5ExecutionInput(
            authorization=authorization,
            canary=self._config.canary,
            kill_switch=self._config.kill_switch,
            execution_identity=self._config.execution_identity,
            advisory=advisory,
            calibration_certificate=self._config.calibration_certificate.to_dict(),
            candidate=main_result.final_candidate,
            original=request.original_code,
            testbench_hashes=testbench_hashes,
            route_fingerprint=authorization.deterministic_terminal_ref,
            policy_decision=policy_artifact,
            ledger_event=ledger_event,
            budget_reservation=reservation.to_dict(),
        )
        validator = ExistingR4ValidationAdapter(
            context=context,
            request=request,
            handler_factory=handler_factory,
            authorization_id=authorization.authorization_id,
            testbench_hashes=testbench_hashes,
            expected_reserve_plan=reserve_plan,
        )
        audit_result: dict[str, Any] = {"status": "not_run"}
        auditor_reads = 0

        def audit(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            nonlocal audit_result, auditor_reads
            auditor_reads += 1
            audit_result = _plain(self._auditor.audit(payload["validation"]))
            return audit_result

        controller = R4CandidateRepairController()
        result = controller.run(
            execution,
            mutate_candidate=lambda candidate: self._config.mutation_adapter.mutate(
                candidate=candidate,
                event=event,
                advisory=advisory,
                task=context.task,
            ),
            validate_candidate=validator.validate,
            audit=audit,
        )
        budget_after = context.budget.snapshot().to_dict()
        actual_provider_calls = budget_after["llm_calls"] - budget_before["llm_calls"]
        if actual_provider_calls < 0 or actual_provider_calls > 1:
            raise RuntimeError("r5_provider_call_accounting_out_of_range")
        if isinstance(
            self._config.mutation_adapter, CandidateModelR4MutationAdapter
        ) and result.provider_call_count != actual_provider_calls:
            raise RuntimeError("r5_provider_call_accounting_mismatch")
        budget_actual = build_r4_budget_actual(
            reserve_plan,
            budget_before=budget_before,
            budget_after=budget_after,
            provider_calls=result.provider_call_count,
            mutation_calls=result.mutation_count,
            auditor_reads=auditor_reads,
        )
        episode_path, episode_hash = self._append_episode(
            event=event,
            advisory=advisory,
            gate=gate_map,
            authorization=authorization,
            result=result,
            validation=validator.last_result,
            budget_actual=budget_actual,
            outcome=R5EpisodeOutcome(result.outcome.value),
            reason=";".join(result.reasons),
        )
        return {
            "schema_version": "r5-existing-orchestrator-integration-v1",
            "arm": self._config.profile.arm.value,
            "status": result.outcome.value,
            "r4_controller_result": result.to_dict(),
            "authorization": authorization.to_dict(),
            "gate": gate_map,
            "episode_path": episode_path,
            "episode_hash": episode_hash,
            "budget": {
                "requested": reserve_plan.to_dict(),
                "effective": reservation.to_dict(),
                "actual": budget_actual,
                "delta": _budget_delta(budget_before, budget_after),
            },
            "main_result_unchanged": True,
            "accepted_by_integration": False,
        }

    @staticmethod
    def _eligible_pair(main_result: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        events = list(main_result.metadata.get("diagnostic_events", ()))
        shadows = list(main_result.metadata.get("r2_shadow_diagnostics", ()))
        pairs = []
        for event in events:
            matches = [
                item for item in shadows if item.get("event_id") == event.get("event_id")
            ]
            if len(matches) != 1:
                continue
            shadow = dict(matches[0])
            advisory = dict(shadow.get("advisory", {}))
            if (
                shadow.get("input_status") == "eligible"
                and shadow.get("critical_safety_violation") is False
                and shadow.get("equivalence", {}).get("equivalent") is True
                and advisory.get("suspected_owner") == "candidate"
                and advisory.get("repair_scope") == "candidate_only"
                and advisory.get("abstain_reason") is None
            ):
                pairs.append((dict(event), shadow, advisory))
        return pairs[0] if len(pairs) == 1 else None

    def _evaluate_gate(
        self,
        *,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
    ):
        assert self._config.memory_snapshot is not None
        assert self._config.revision is not None
        revision = self._config.revision
        adapted = RepairPatternRevision(
            revision_id=revision.revision_id + ":" + revision.revision_sha256,
            parent_revision_id=revision.parent_revision_id,
            supported_when=revision.supported_when,
            avoid_when=revision.avoid_when,
            exclusions=revision.exact_exclusions,
            required_evidence=revision.required_evidence,
            positive_episode_refs=revision.positive_episode_refs,
            negative_episode_refs=revision.negative_episode_refs,
            calibration_refs=revision.calibration_refs,
            lifecycle=PatternLifecycle.TRUSTED,
            threshold_source=revision.threshold_source,
        )
        facts = dict(self._config.memory_snapshot.conflict_sparsity_ood_facts)
        identity = self._config.execution_identity
        context = {
            "snapshot_id": self._config.memory_snapshot.snapshot_id,
            "identity_complete": event.get(
                "identity_complete", identity.get("identity_complete")
            ),
            "hidden_input_count": event.get(
                "hidden_input_count", identity.get("hidden_input_count", 0)
            ),
            "secret_present": event.get(
                "secret_present", identity.get("secret_present", False)
            ),
            "evidence_predicates": tuple(event.get("evidence_refs", ())),
            "stage": event.get("stage"),
            "owner": advisory.get("suspected_owner"),
            "failure_family": _advisory_failure_class(advisory),
            "calibrated_risk_ok": True,
            "avoid_when_match": bool(facts.get("avoid_when_match", False)),
            "conflict": bool(facts.get("conflict", False)),
            "sparse": bool(facts.get("sparse", False)),
            "ood": bool(facts.get("ood", False)),
        }
        return self._gate.evaluate(
            context=context,
            revision=adapted,
            evidence_refs=tuple(advisory.get("evidence_refs", ())),
        )

    def _build_research_authorization(
        self,
        *,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        gate: Mapping[str, Any],
        policy_decision: Mapping[str, Any],
        ledger_event: Mapping[str, Any],
        budget_reservation: Mapping[str, Any],
    ) -> R5ResearchAuthorization:
        arm = self._config.profile.arm
        assert arm is not None
        mode = {
            R5Arm.A2: R5AuthorizationMode.ADVISOR_ONLY,
            R5Arm.A3: R5AuthorizationMode.SIMILARITY_ONLY,
            R5Arm.A4: R5AuthorizationMode.GATED_MEMORY,
            R5Arm.A5: R5AuthorizationMode.GATED_MEMORY,
            R5Arm.A6: R5AuthorizationMode.GATED_MEMORY,
        }[arm]
        gated = mode is R5AuthorizationMode.GATED_MEMORY
        return R5ResearchAuthorization(
            authorization_id="r5-" + canonical_sha256(
                {
                    "event": event["event_id"],
                    "arm": arm.value,
                    "advisory": advisory["advisory_id"],
                    "campaign": self._config.campaign_manifest_sha256,
                }
            )[:32],
            arm_id=arm.value,
            mode=mode,
            calibration_certificate_sha256=canonical_artifact_sha256(
                self._config.calibration_certificate.to_dict()
            ),
            advisory_sha256=canonical_artifact_sha256(advisory),
            policy_sha256=canonical_artifact_sha256(policy_decision),
            ledger_sha256=canonical_artifact_sha256(ledger_event),
            budget_reservation_sha256=canonical_artifact_sha256(
                budget_reservation
            ),
            r4_controller_contract_sha256=R4_CONTROLLER_CONTRACT_SHA256,
            memory_mode=self._config.profile.memory_mode,
            retrieval_manifest_sha256=(
                self._config.retrieval_manifest_sha256
                if mode is R5AuthorizationMode.SIMILARITY_ONLY
                else None
            ),
            gate_contract_sha256=(gate.get("contract_hash") if gated else None),
            revision_sha256=(
                self._config.revision.revision_sha256
                if gated and self._config.revision is not None
                else None
            ),
            snapshot_sha256=(
                self._config.memory_snapshot.snapshot_sha256
                if gated and self._config.memory_snapshot is not None
                else None
            ),
            payload_manifest_sha256=(
                memory_payload_manifest_sha256(self._config.memory_payloads)
                if gated
                else None
            ),
        )

    def _provenance_reasons(
        self,
        *,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        authorization: R5CandidateRepairAuthorization,
        deterministic_terminal: Mapping[str, Any],
        candidate: str,
        original: str,
    ) -> list[str]:
        reasons: list[str] = []
        identity = dict(self._config.execution_identity)
        if event.get("event_id") != authorization.event_ref:
            reasons.append("event_authorization_ref_mismatch")
        if event.get("run_id") != authorization.run_id:
            reasons.append("run_authorization_ref_mismatch")
        if identity.get("run_id") not in {None, event.get("run_id")}:
            reasons.append("execution_run_mismatch")
        if event.get("candidate_sha256") != _text_sha(candidate):
            reasons.append("candidate_hash_mismatch")
        if event.get("original_sha256") not in {None, _text_sha(original)}:
            reasons.append("original_hash_mismatch")
        if authorization.deterministic_terminal_ref != canonical_artifact_sha256(
            deterministic_terminal
        ):
            reasons.append("deterministic_terminal_mismatch")
        if identity.get("source_sha256") != _text_sha(original):
            reasons.append("execution_source_mismatch")
        if not self._config.canary.matches(identity):
            reasons.append("canary_identity_mismatch")
        event_target = event.get("target_identity", {})
        event_toolchain = event.get("toolchain_identity", {})
        if isinstance(event_target, Mapping) and event_target.get("fingerprint") not in {
            None,
            identity.get("target_identity"),
        }:
            reasons.append("event_target_mismatch")
        if isinstance(event_toolchain, Mapping) and event_toolchain.get("fingerprint") not in {
            None,
            identity.get("toolchain_identity"),
        }:
            reasons.append("event_toolchain_mismatch")
        if advisory.get("accepted") is not False or advisory.get("owner_authority") != "llm_advisory":
            reasons.append("advisory_authority_invalid")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _validation_plan(
        *,
        context: Any,
        request: Any,
        handler_factory: Any,
        candidate: str,
        validation_id: str,
    ) -> tuple[Any, Mapping[Any, Any]]:
        from agrefactor.runtime.candidate_repair_integration import (
            CandidateValidationPlanRequest,
        )

        plan = CandidateValidationPlanRequest(
            task=context.task,
            candidate_code=candidate,
            original_code=request.original_code,
            preflight_testbench_code=request.preflight_testbench_code,
            suite_testbench_codes=request.suite_testbench_codes,
            attempt=1,
            validation_id=validation_id,
            reference_top_function=request.reference_top_function,
            candidate_top_function=(
                request.candidate_top_function or context.task.kernel_name
            ),
        )
        handlers = handler_factory.build(plan)
        if not isinstance(handlers, Mapping):
            raise TypeError("handler_factory.build must return a mapping")
        return plan, handlers

    def _append_episode(
        self,
        *,
        event: Mapping[str, Any],
        advisory: Mapping[str, Any],
        gate: Mapping[str, Any],
        authorization: R5CandidateRepairAuthorization | None,
        result: Any | None,
        validation: Mapping[str, Any] | None,
        budget_actual: Mapping[str, Any],
        outcome: R5EpisodeOutcome,
        reason: str,
    ) -> tuple[str, str]:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        identity_sha = canonical_artifact_sha256(self._config.execution_identity)
        context_signature = event.get("context_signature")
        if not isinstance(context_signature, str) or not _SHA256.fullmatch(
            context_signature
        ):
            context_signature = canonical_sha256(
                {
                    "stage": event.get("stage"),
                    "target": event.get("target_identity", {}),
                    "toolchain": event.get("toolchain_identity", {}),
                    "owner": advisory.get("suspected_owner"),
                }
            )
        history = (
            ()
            if self._config.memory_snapshot is None
            else self._config.memory_snapshot.history_episode_ids
        )
        lineage = tuple(dict.fromkeys((*history, str(event["event_id"]))))
        authorization_map = (
            {"status": "not_created"}
            if authorization is None
            else authorization.to_dict()
        )
        payload = {
            "arm": self._config.profile.arm.value,
            "event_ref": event["event_id"],
            "advisory_id": advisory.get("advisory_id"),
            "gate": dict(gate),
            "authorization": authorization_map,
            "candidate_before_sha256": event.get("candidate_sha256"),
            "candidate_after_sha256": (
                None if result is None else result.after_candidate_sha256
            ),
            "formal_validation_id": (
                None if result is None else result.formal_validation_id
            ),
            "validation_evidence_refs": (
                []
                if validation is None
                else list(validation.get("validation_evidence_refs", ()))
            ),
            "provider_call_count": (
                0 if result is None else result.provider_call_count
            ),
            "vitis_phase_count": (
                0 if validation is None else int(validation.get("vitis_phase_count", 0))
            ),
            "budget_actual": dict(budget_actual),
            "outcome_reason": reason,
        }
        episode = R5EpisodeEnvelope(
            episode_kind="r4_repair",
            episode_id="r5-" + canonical_sha256(
                {
                    "event": event["event_id"],
                    "arm": self._config.profile.arm.value,
                    "authorization": authorization_map,
                    "outcome": outcome.value,
                }
            )[:40],
            payload_schema_version="r5-repair-episode-v1",
            payload=payload,
            execution_identity_sha256=identity_sha,
            source_sha256=str(self._config.execution_identity["source_sha256"]),
            context_signature=context_signature,
            created_at=timestamp,
            observed_at=timestamp,
            lineage=lineage,
            agent_safe_summary={
                "failure_family": (
                    _advisory_failure_class(advisory) or "unknown"
                ),
                "stage": event.get("stage", "unknown"),
                "owner": advisory.get("suspected_owner", "unknown"),
                "false_repair": False,
                "unsafe_scope": False,
                "critical_safety_violation": False,
                "reason": reason,
            },
            outcome=outcome,
            manifest_sha256=self._config.campaign_manifest_sha256,
        )
        path = AppendOnlyEpisodeLedger(
            Path(self._config.episode_ledger_root)
        ).append(episode)
        return str(path), episode.envelope_sha256

    @staticmethod
    def _terminal(status: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "schema_version": "r5-existing-orchestrator-integration-v1",
            "status": status,
            "reason": reason,
            "main_result_unchanged": True,
            "accepted_by_integration": False,
            **extra,
        }


__all__ = [
    "ExistingOrchestratorR5Integration",
    "R5CandidatePromptFactory",
    "R5IntegrationConfig",
]
