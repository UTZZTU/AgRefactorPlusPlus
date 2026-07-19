# Stage 2.7.7 Cross-stage Regression and Stage 2.8 Handoff Acceptance

## Status

```text
code_baseline=5d9ca6b76162f30e6a33c76d933ebb0021955baf
related_tests=389/389
full_unittest=836/836
evidence_milestones=8/8
blockers_satisfied=5/5
artifact_manifests_validated=8
artifact_manifest_entries=34
execution_classes_distinct=true
closure_checklist=9/10
pending=C-09
ready_for_stage2_8=true
stage2_closed=false
stage3_allowed=false
```

## Evidence nodes

The audit indexed these local acceptance nodes:

```text
2.5.4 Multi-type Kernel Smoke Evidence Summary
2.6   Closure-readiness Audit
2.7.1 Repair Protocol and Artifact Schema
2.7.2 Minimal ModelFamilyProfile
2.7.3 Stage 1 Hardening Batch A
2.7.4 Formal Repair-aware UnifiedRunner / CLI
2.7.5 Real Network-model Candidate Repair Smoke
2.7.6 Evidence-gated Delta and Ground-truth Revalidation
```

Each node records a repository document, local acceptance directory, execution class,
file count, total bytes and deterministic tree SHA-256.

## Stage 2.6 blockers

| Blocker | Acceptance | Result |
|---|---|---|
| B-01 Formal repair-aware UnifiedRunner / CLI | 2.7.4 | satisfied |
| B-02 Shared repair protocol and artifacts | 2.7.1 | satisfied |
| B-03 Minimal ModelFamilyProfile | 2.7.2 | satisfied |
| B-04 Stage 1 Hardening Batch A | 2.7.3 | satisfied |
| B-05 Real network-model repair smoke | 2.7.5 | satisfied |

The original Stage 2.6 acceptance criteria remain authoritative. This document indexes
their accepted evidence; it does not rewrite the historical audit.

## Artifact integrity

Every discovered `artifact_manifest.json` and `run_artifact_manifest.json` was checked
for:

```text
safe relative paths
unique entries
existing regular files
SHA-256 equality
byte-size equality
no symlink targets
```

```text
manifests=8
manifest_entries=34
```

## Execution-class boundary

```text
deterministic protocol/profile/target tests: distinct
FakeProvider acceptance: distinct
real network-model response and usage: distinct
real local g++/Vitis evidence: distinct
independent ground-truth labels: distinct
classes merged: false
```

Stage 2.7.7 itself made:

```text
new network-model calls=0
new Vitis CSYNTH calls=0
new Vitis CSIM calls=0
optimizer executed=false
feature commit created=false
```

The complete unit regression may exercise local test fixtures; this milestone does not
claim a new Vitis acceptance run.

## Stage 2.8 frozen closure checklist

Nine of ten closure conditions are verified. The only pending condition is:

```text
C-09 final documentation synchronization
```

Stage 2.8 must synchronize:

```text
README.md
CHANGELOG.md
docs/USAGE.md
docs/REPRODUCTION_STATUS.md
docs/ROADMAP.md
docs/GOAL_TRACEABILITY.md
docs/PROJECT_STATE.md
docs/NEXT_CHAT_HANDOFF.md
docs/STAGE2_EVIDENCE_LOOP.md
docs/STAGE2_HARDENING_PLAN.md
docs/stage2_hardening_acceptance.md
docs/stage2_closure_acceptance.md (new)
```

It must then rerun the complete regression and create the formal closure acceptance.

## Current state

```text
ready_for_stage2_8=true
stage2_closed=false
stage3_allowed=false
```

Stage 2 may be declared closed only after the Stage 2.8 documentation, regression,
commit, push, local=remote and clean-worktree checks all pass.

## Artifacts

```text
/data/agrefactor_runs/stage2_7_7_cross_stage_regression_handoff_20260719_224214/acceptance
```

Key files:

```text
stage2_7_7_cross_stage_evidence_index.json
stage2_8_frozen_closure_handoff.json
stage2_7_7_cross_stage_regression_summary.json
related.log
full_unittest.log
```

Next:

```text
Stage 2.8 Final Documentation and Stage 2 Closure
```
