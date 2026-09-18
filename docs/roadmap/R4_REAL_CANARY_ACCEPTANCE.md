# V2.3 R4 Real Canary Acceptance

Date: 2026-09-18

## Decision

R4 Gate-authorized Candidate repair is accepted at implementation base
`2e5971b0b74f7557870fc46b23f8ed5389d1685b`.

This is an external project-state decision made after the Package D v1.6
file-only independent audit reached `ready_for_manual_checkpoint`. The package
did not accept itself: `package_self_acceptance=false` remains part of the
contract and evidence.

The only product entrypoints remain `refactor`, `optimize`, and `full`. R4 is
default-off and is enabled only by the existing canary/authorization controls.

## Evidence

Identity-complete R2 calibration:

```text
certificate_id=r2-calibration-87bc3a17b85a03ae7c323d7c4cf9bdbc
calibration_bundle_sha256=d14947a09c4c4310017e53731eeafae184598d6612928eceebb01c5ce83ff516
independent_audit_sha256=1469d7c37b0d5f46601f295cc7bb19a9985517d4a6c0211613cd13497e2ab69c
provider_calls=16
vitis_launches=0
independent_audit=clean
```

Package D v1.6:

```text
evidence_root=/data/package_d_v1_6_r4_evidence_run1
evidence_archive_sha256=41f24168856b18683ee47c6285bf925cc48ddea97e369a440af38fd780d26703
baseline_runs=3
canary_runs=3
canary_statuses=abstained,verified_positive,verified_positive
verified_positive_canaries=2
safe_selective_abstentions=1
provider_calls=5
vitis_launches=20
git_history_mutations=0
critical_findings=0
environmental_findings=0
independent_audit=ready_for_manual_checkpoint
```

The archive SHA-256 sidecar was rechecked before this decision. The two
positive canaries each contain one bounded Candidate mutation, valid R4
provenance, a clean product auditor result, and a fresh full validation prefix.
The remaining canary was rejected before mutation because its R2 confidence
was not covered by the accepted high-confidence calibration boundary. That is
the required safe refusal behavior, not a failed repair hidden as success.

## Accepted Claims

- The existing R2 advisor, R3 Gate, R4 controller, existing orchestrator,
  provider registry, Vitis executors, and product auditor operate as one
  bounded Candidate-only repair path.
- Exact provider/model/endpoint/prompt/decoding identity is bound to the
  accepted calibration certificate.
- R4 preserves one-attempt, Candidate-only, unchanged-testbench,
  best-correct, kill-switch, quarantine, provenance, budget, and full-prefix
  validation boundaries.
- Formal validation and independent audit remain success authorities; model
  confidence and package exit status are not success authorities.
- Evidence insufficiency can terminate as a selective abstention without a
  Candidate mutation.

## Claims Not Yet Established

R4 acceptance does not establish historical-memory efficacy, open-world
generalization, negative-transfer rate, temporal learning benefit, or causal
improvement over all ablation arms. `R3_TEMPORAL_MEMORY_EFFICACY_ESTABLISHED`
therefore remains `false`. Those questions belong to the frozen V2.3 R5
time-ordered A0-A6 campaign.

## Next Step

```text
R4_ACCEPTED=true
R5_STARTED=false
NEXT_STEP=V2.3-R5-campaign-design
PACKAGE_SELF_ACCEPTANCE=false
```
