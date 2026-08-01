# Stage 3.3 Deterministic Optimizer State Machine Acceptance

## Status

```text
implementation_baseline=1ef34ef63d0f0ed82b0de3da22b20c9346f6bed8
s33_focused=46/46
optimizer_regression=181/181
full_deterministic_regression=1689/1689
model_api_called=false
network_called=false
vitis_called=false
real_csim=false
real_csynth=false
s3_3_accepted=true
next_package=S3.4_STRUCTURAL_MODEL_INTEGRATION
```

The installation/closure script leaves this document in the repository only if all three deterministic suites pass. On failure it restores the exact S3.2 baseline files and reports the audit root.

## Accepted scope

S3.3 implements only:

```text
typed frozen safe-v1 policy
Structural → Bottleneck → Pragma deterministic transitions
2/2/3 round limits
3 proposed / 1 selected / 1 executed per round
7 executed-candidate hard strategy ceiling
FakeHypothesisProvider
FakeCandidateExecutor with S3.2-compatible outcomes
provider-order first-valid selection
policy counters separate from physical BudgetManager counters
prospective budget preflight
candidate lineage and immutable hypothesis artifacts
PPA improve/regress/infeasible/incomparable decisions
best_correct / best_ppa protection
rollback
checkpointed next-action state
step/resume without repeating checkpointed candidates
deterministic decisions.jsonl and trace evidence
```

It does not implement a real optimization model, optimization prompt, real source rewriting, real Vitis/CSIM/CSYNTH, Bottleneck classification, pragma semantics, product `optimize/full`, Memory Gate, migration, or evaluation.

## Frozen ambiguity resolutions

The exact S3.3 decisions are recorded in [`STAGE3_S33_DECISION_RECORD.md`](../../roadmap/STAGE3_S33_DECISION_RECORD.md). Important points:

- first valid hypothesis in provider order is selected; no heuristic source matching;
- empty valid set ends the current level and advances;
- PPA regression remains an accepted historical candidate but cannot overwrite selected best pointers;
- rejected candidates rollback and continue; blocked/review/error stop the run;
- unknown feasibility and incomparable PPA require review rather than guessing;
- candidate terminal state and deterministic next action are coalesced into one authoritative checkpoint;
- Fake components consume zero physical budget by default.

## Deterministic evidence matrix

Focused tests cover:

- baseline rejected with zero provider/executor calls;
- exact Structural 2 → Bottleneck 2 → Pragma 3 order;
- 21 proposals, 7 selections and 7 executions at full safe-v1 capacity;
- contiguous `cand-1`…`cand-7` IDs and parent+hypothesis lineage;
- PPA improvement, regression, resource infeasibility, first feasible recovery, unknown feasibility and context mismatch;
- rejected rollback, blocked, review and error terminals;
- empty and malformed hypotheses, missing Bottleneck evidence and Hidden-like unsafe claims;
- provider and executor budget exhaustion before invocation;
- logical policy counters separated from simulated physical counters;
- baseline/round checkpoints, immutable best projections and resume without duplicate candidate execution;
- deterministic artifacts across repeated runs;
- explicit no-real-network/no-real-Vitis trace evidence.

## Full regression boundary

Expected closure counts on the authoritative `agrefactor` environment are:

```text
S3.3 focused: 46
all optimizer tests: 181
full unittest discovery: 1689
```

The portable package was additionally checked in an isolated sandbox that lacked the repository's `autogen` dependency. In that sandbox:

```text
S3.2 baseline full discovery: 1292 tests, 3 failures + 36 import errors
S3.3 worktree full discovery: 1338 tests, same 3 failures + same 36 import errors
root cause: ModuleNotFoundError: autogen
new S3.3 failures beyond baseline: 0
```

This differential evidence is not substituted for target-environment closure; `apply_s3_3.sh` requires the authoritative 1689/1689 full run before retaining the changes.

## Safety and boundaries

- Existing `state.py`, `checkpoint.py`, `qualification.py`, `ppa.py`, `cache.py`, `BudgetManager`, `TraceRecorder`, Stage 2 backend, product CLI and source bootstrap are not modified.
- `refactor` formal behavior is unchanged.
- product `optimize/full` remain gated.
- no incomplete static source matcher is introduced as an authoritative gate.
- Hidden/operator-full material is rejected from model-facing requests and safe decision artifacts.
- deterministic fixtures are not presented as real model or real Vitis acceptance.

## Acceptance decision

```text
S3.3_DETERMINISTIC_OPTIMIZER_STATE_MACHINE=accepted
STAGE3_IMPLEMENTATION_IN_PROGRESS=true
NEXT_PACKAGE=S3.4_STRUCTURAL_MODEL_INTEGRATION
```
