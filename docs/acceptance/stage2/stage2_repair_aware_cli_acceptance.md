# Stage 2.7.4 Formal Repair-aware UnifiedRunner / CLI Acceptance

## Status

```text
7e9aef66ba062b25465f6552f9bf346b8ed5eb86
feat: add formal repair-aware runner phase

20/20 targeted
836/836 full unittest
```

## Formal construction

```text
TaskSpec
→ CLI --repair-aware
→ UnifiedRunner
→ CandidateRepairPhase
→ CandidateRepairValidationOrchestrator
→ LocalCandidateValidationHandlerFactory
→ Preflight / CSYNTH / Public CSIM / Hidden CSIM
```

The phase does not copy or replace handlers, feedback routing, model contracts, repair
control, or validation orchestration. The acceptance also verifies that a declared
hidden-only plan (`Preflight → CSYNTH → Hidden`) is legal after candidate repair.

## Shared services

One UnifiedRunner run creates exactly one BudgetManager and one TraceRecorder. Initial
validation and every repaired-candidate revalidation receive those exact instances.

## Input boundary

The candidate source is explicit. Original, Preflight and suite paths are resolved from
TaskSpec or explicit CLI options. Multiple public suites require an explicit
prompt-facing public testbench. Hidden testbench code is never serialized.

## Artifact boundary

```text
run_result.json
run_artifact_manifest.json
trace.jsonl
refactor/orchestration_result.json
refactor/final_candidate.cpp
refactor/artifact_manifest.json
refactor/repair_artifacts/...
```

Manifests are written last, contain relative paths and SHA-256 hashes, and are marked
`agent_safe`.

## Mode boundary

Exactly one of `dry_run`, `legacy`, or `repair_aware` is required. Run results record
`execution_mode` and `legacy_mode`, so legacy cannot masquerade as the formal path.

## Execution class

```text
deterministic acceptance: true
FakeProvider used: true
network model executed: false
real tool executed: false
optimizer executed: false
hidden leakage: false
```

Acceptance directory:

```text
/data/agrefactor_runs/stage2_7_4_repair_aware_cli_v2_20260719_203354/acceptance
```

Next:

```text
Stage 2.7.5 Real Network-model Candidate Repair Smoke
```
