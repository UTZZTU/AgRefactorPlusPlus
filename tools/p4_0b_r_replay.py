#!/usr/bin/env python3
"""Self-contained deterministic P4-0B-R lineage replay."""

from __future__ import annotations

import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agrefactor.optimization import (
    BoundedRecoveryOptimizerStateMachine,
    CandidateQualificationResult,
    CandidateRecord,
    CandidateStatus,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    FakeExecutionStatus,
    FakeHypothesisProvider,
    OptimizeCandidateRecoveryResult,
    OptimizeRecoveryStage,
    OptimizeRecoveryStatus,
    OptimizerArtifactStore,
    OptimizerCheckpointWriter,
    OptimizerState,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
    QualificationStage,
    QualificationStatus,
    QualificationStepOutcome,
    QualificationStepRecord,
)
from agrefactor.runtime import (
    BudgetManager,
    TraceRecorder,
)


TOP_SOURCE = b"void kernel(int *a) { a[0] = a[0] + 1; }\n"
REPAIRED_SOURCE = b"void kernel(int *a) { a[0] = a[0] + 2; }\n"


def _ppa(
    candidate_id: str,
    latency: int,
) -> PpaEvidence:
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="fixture",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"fake/{candidate_id}.xml",
        report_sha256=sha256(
            f"{candidate_id}:{latency}".encode()
        ).hexdigest(),
        comparison_context_identity_sha256="a" * 64,
        latency_cycles_min=latency,
        latency_cycles_max=latency,
        initiation_interval_min=1,
        initiation_interval_max=1,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.0,
        resources_used=PpaResourceUsage(
            bram_18k=1,
            dsp=1,
            ff=10,
            lut=10,
            uram=0,
        ),
        resources_available=PpaResourceUsage(
            bram_18k=100,
            dsp=100,
            ff=1000,
            lut=1000,
            uram=10,
        ),
        max_resource_utilization_ratio=0.1,
        objective_feasible=True,
        constraint_violations=(),
        parser_warnings=("fixture",),
    )


def _accepted(
    candidate_id: str,
    latency: int,
) -> CandidateQualificationResult:
    steps = tuple(
        QualificationStepRecord(
            stage=stage,
            outcome=QualificationStepOutcome.PASSED,
            evidence_view=(
                "operator_full"
                if stage is QualificationStage.HIDDEN
                else "internal_safe"
            ),
            route_action=None,
            source="fixture",
            source_report_id=(
                None
                if stage is QualificationStage.HIDDEN
                else f"{candidate_id}-{stage.value}"
            ),
            source_item_count=0,
            source_blocking=False,
            reason_codes=("fixture_passed",),
            metadata={"physical_execution": False},
        )
        for stage in (
            QualificationStage.SOURCE,
            QualificationStage.PREFLIGHT,
            QualificationStage.PUBLIC,
            QualificationStage.CSYNTH,
            QualificationStage.HIDDEN,
            QualificationStage.PPA,
            QualificationStage.FEASIBILITY,
        )
    )
    return CandidateQualificationResult(
        qualification_id=f"qual-{candidate_id}",
        candidate_id=candidate_id,
        status=QualificationStatus.ACCEPTED,
        steps=steps,
        correctness_passed=True,
        synthesis_passed=True,
        objective_feasible=True,
        ppa=_ppa(candidate_id, latency),
        cache_key_sha256=sha256(
            f"cache:{candidate_id}".encode()
        ).hexdigest(),
        cache_hit=False,
        budget_before={},
        budget_after={},
        decision={
            "decision": "accept",
            "reason_codes": ["fixture_passed"],
        },
    )


class _DeterministicRecovery:
    name = "p4-0b-r-deterministic-replay"
    uses_network = False
    uses_vitis = False

    def __init__(
        self,
        result: OptimizeCandidateRecoveryResult,
    ) -> None:
        self._result = result
        self.requests = []

    def recover(self, request):
        self.requests.append(request)
        return self._result

    def summary(self):
        return {
            "attempted": len(self.requests),
            "validated": int(
                self._result.status
                is OptimizeRecoveryStatus.VALIDATED
            ),
        }


def _run_replay():
    recovery = _DeterministicRecovery(
        OptimizeCandidateRecoveryResult(
            status=OptimizeRecoveryStatus.VALIDATED,
            source_candidate_id="cand-1",
            recovery_candidate_id="cand-2",
            stage=OptimizeRecoveryStage.PREFLIGHT,
            reason_codes=("candidate_compile_failed",),
            source=REPAIRED_SOURCE,
            qualification=_accepted(
                "cand-2",
                latency=80,
            ),
            budget_before={},
            budget_after={},
        )
    )

    with tempfile.TemporaryDirectory(
        prefix="p4_0b_r_replay_"
    ) as temporary:
        root = Path(temporary)
        writer = OptimizerCheckpointWriter(
            root / "optimizer"
        )
        baseline = CandidateRecord(
            candidate_id="baseline",
            sequence=0,
            parent_candidate_id=None,
            hypothesis_id=None,
            level=None,
            source_sha256=sha256(
                TOP_SOURCE
            ).hexdigest(),
            source_artifact=(
                "candidates/baseline/source.cpp"
            ),
            status=CandidateStatus.ACCEPTED,
            correctness={"passed": True},
            synthesis={"passed": True},
            ppa=_ppa("baseline", 100).to_dict(),
            decision={
                "decision": "baseline_accepted"
            },
            created_at_utc="2026-08-03T00:00:00Z",
        )
        writer.write_candidate_source(
            baseline,
            TOP_SOURCE,
        )
        state = replace(
            OptimizerState.initial(
                run_id="p4-0b-r-replay"
            ).with_qualified_baseline(baseline),
            best_ppa_candidate_id="baseline",
        )
        engine = BoundedRecoveryOptimizerStateMachine(
            state=state,
            candidates={"baseline": baseline},
            checkpoint_writer=writer,
            provider=FakeHypothesisProvider(),
            executor=FakeCandidateExecutor(
                outcomes={
                    1: FakeExecutionOutcome(
                        status=(
                            FakeExecutionStatus.REJECTED
                        ),
                        reason_code=(
                            "candidate_compile_failed"
                        ),
                        source_suffix=(
                            "\n// deterministic broken candidate\n"
                        ),
                    )
                }
            ),
            recovery_coordinator=recovery,
            budget=BudgetManager(),
            trace=TraceRecorder(
                "p4-0b-r-replay"
            ),
            artifact_store=OptimizerArtifactStore(
                writer.root
            ),
            resume=False,
        )
        return engine.step(), recovery


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result, recovery = _run_replay()
    source = result.candidates["cand-1"]
    repaired = result.candidates["cand-2"]

    payload = {
        "schema_version": 2,
        "replay_id": "p4-0b-r.bounded-optimize-recovery",
        "replay_kind": "deterministic_lineage_and_best_correct",
        "direct_entrypoint": True,
        "repo_root_bootstrap": True,
        "test_module_dependency": False,
        "network_used": False,
        "vitis_used": False,
        "source_candidate_id": source.candidate_id,
        "source_status": source.status.value,
        "recovery_candidate_id": repaired.candidate_id,
        "recovery_status": repaired.status.value,
        "parent_candidate_id": (
            repaired.parent_candidate_id
        ),
        "hypothesis_preserved": (
            repaired.hypothesis_id
            == source.hypothesis_id
        ),
        "best_correct_candidate_id": (
            result.state.best_correct_candidate_id
        ),
        "best_ppa_candidate_id": (
            result.state.best_ppa_candidate_id
        ),
        "executed_candidate_count": (
            result.state.executed_candidate_count
        ),
        "recovery_attempt_count": len(
            recovery.requests
        ),
        "nested_recovery_started": False,
        "hidden_evidence_exposed": False,
        "public_csim_repair": False,
        "ppa_repair": False,
    }
    payload["passed"] = bool(
        payload["source_status"] == "rejected"
        and payload["recovery_status"] == "accepted"
        and payload["parent_candidate_id"] == "cand-1"
        and payload["hypothesis_preserved"] is True
        and payload["best_correct_candidate_id"]
        == "cand-2"
        and payload["best_ppa_candidate_id"]
        == "cand-2"
        and payload["executed_candidate_count"] == 2
        and payload["recovery_attempt_count"] == 1
        and payload["nested_recovery_started"]
        is False
        and payload["hidden_evidence_exposed"]
        is False
        and payload["test_module_dependency"]
        is False
        and payload["network_used"] is False
        and payload["vitis_used"] is False
    )

    rendered = (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output.write_text(
            rendered,
            encoding="utf-8",
        )
    print(rendered, end="")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
