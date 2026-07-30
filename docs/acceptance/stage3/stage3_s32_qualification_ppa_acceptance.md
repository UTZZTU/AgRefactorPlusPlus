# Stage 3.2 Qualification and PPA Evidence Acceptance

## Status

```text
implementation_baseline=9e55601f873e46e6edf83b5092970e47fbe132c0
s32_focused=85/85
optimizer_regression=135/135
full_deterministic_regression=1643/1643
real_replay_status=accepted
actual_vitis_version=2023.2
model_api_called=false
s3_2_accepted=true
next_package=S3.3_DETERMINISTIC_OPTIMIZER_STATE_MACHINE
```

## Accepted scope

S3.2 implements only:

```text
independent baseline/candidate qualification orchestration
typed PPA report evidence adapter
frozen latency comparator
objective/resource feasibility
exact validation cache identity
immutable Hidden-safe validation evidence cache
deterministic tool/report fixtures
one existing real baseline replay
```

It does not implement model hypothesis generation, Structural/Bottleneck/Pragma search, product `optimize/full`, Memory Applicability Gate, or version migration.

## Qualification order

The accepted independent Stage 3 path is:

```text
source/schema validation
→ Preflight
→ Public suites
→ CSYNTH
→ Hidden suites
→ PPA extraction
→ objective feasibility
```

The implementation reuses existing Stage 2 handlers and the shared `BudgetManager`/`TraceRecorder`, but does not change the Stage 2 orchestrator's previously accepted order.

## Deterministic evidence

```text
S3.2 focused tests=85/85
all optimizer tests=135/135
full unittest=1643/1643
```

Covered failure/safety cases include Preflight/Public gates before CSYNTH, CSYNTH before Hidden, Hidden suppression, blocked/review routing, report parse failure, resource infeasibility, comparator tie-breaking, exact cache misses, atomic cache integrity, zero-launch cache hits, baseline best pointers, and normal token usage in candidate budget snapshots.

## Real baseline replay

```text
artifact_root=/data/agrefactor_runs/stage3_s32_real_replay_20260730T153256Z_2390707
source_run_artifact_root=/data/AgRefactor/agrefactor/smoke/stage2_corpus.py
top_function=candidate_top
target_profile=vitis-2023.2-default
requested_vitis_version=2023.2
actual_vitis_version=2023.2
qualification_status=accepted
correctness_passed=true
synthesis_passed=true
objective_feasible=true
latency_cycles_max=152
initiation_interval_max=153
best_correct_candidate_id=baseline
best_ppa_candidate_id=baseline
cache_hit_replay=true
cache_hit_real_tool_delta_zero=true
```

Physical usage for the first real qualification plus cache-hit replay:

```text
llm_calls=0
tool_calls=6
compile_calls=3
csim_calls=2
csynth_calls=1
tokens=0
cost_usd=0.0
```

The cache-hit replay reused the exact validation evidence and did not increase LLM/tool/compile/CSIM/CSYNTH counters.

## S3.1 interface refinement

S3.2 exposed that S3.1's generic secret-key rejection treated the normal `BudgetUsage.tokens` field as a credential. The accepted fix applies an explicit budget-field allowlist to `CandidateRecord.budget_before/budget_after`; model identity and other sensitive mappings retain secret rejection. This is a consumer-driven compatibility fix, not a Stage 3 contract relaxation.

## Hidden and execution-class boundary

```text
Hidden suite details remain operator-only
safe qualification/cache/trace artifacts contain no Hidden path or raw diagnostic
model API calls=0
real g++/CSIM/CSYNTH evidence is distinct from deterministic fixtures
one real replay does not prove arbitrary kernel/device/version support
```

## Evidence roots

```text
deterministic_audit_root=/data/agrefactor_runs/stage3_s32_closure_prepare_20260730T153923Z_2416071
real_replay_root=/data/agrefactor_runs/stage3_s32_real_replay_20260730T153256Z_2390707
```

## Next package

```text
S3.3 Deterministic Optimizer State Machine
```

S3.3 must remain deterministic, use FakeProvider hypotheses, and must not call a real network model or Vitis.
