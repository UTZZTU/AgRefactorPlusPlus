# Pre-Stage-4 P4-0B-R Decision Record

> **Package:** P4-0B-R Bounded Optimize Candidate Recovery
>
> **Parent commit:** `717efb78e4dd53fbe1fdc14d7db78632c227ea1a`
>
> **Implementation scope:** R1 Preflight + R2 CSYNTH legality.

## Decision

P4-0B-R extends the accepted safe-v1 engine through an injected recovery-aware
state-machine subclass. It does not alter the frozen `SafeOptimizerPolicy` and
does not create a second optimizer.

The failed source Candidate remains a terminal `cand-K`. One eligible recovery
creates a new contiguous `cand-N` descendant with:

```text
parent_candidate_id=cand-K
hypothesis_id=source hypothesis
recovery_attempt=1
recovery_stage=preflight|csynth
```

## Eligibility

Preflight is limited to:

```text
candidate_compile_failed
candidate_top_missing
interface_mismatch
```

CSYNTH is limited to Candidate-owned legality categories. Timing, resources,
PPA, Public, Hidden, Testbench, Reference, toolchain, configuration, mixed and
unknown ownership do not enter recovery.

## Interface decision

For `candidate_top_missing` and `interface_mismatch`, the response contract is
derived from the accepted parent Candidate interface. Exact fallback to the
parent source is rejected.

## Budget decision

Before launching the recovery model call, the coordinator prospectively checks
one LLM call plus a complete Optimize qualification restart:

```text
Source → Preflight → Public → CSYNTH → Hidden → PPA → Feasibility
```

Physical handlers remain authoritative for actual consumption.

## Historical compatibility

When no recovery coordinator is injected, the original
`DeterministicOptimizerStateMachine` and historical safe-v1 policy are
unchanged. Normal Optimize and the Optimize phase of Full inject the same
bounded coordinator.

## Deferred

Public native Vitis CSIM remains P4-0C. Public repair is false. Hidden, COSIM
and PPA repair remain forbidden.

## Product integration capability decision

The normal product path requires the qualification adapter to provide:

```text
recovery_evidence
recovery_budget_increment
validate_recovery
```

Missing capabilities are a configuration/programming error and fail loudly.
The product path never silently disables P4-0B-R.

The deterministic internal-chain fixture explicitly implements this capability.
Its rejected fake Candidates provide no typed agent-safe recovery evidence, so
both are recorded as `ineligible`, with zero repair model attempts and the
existing `best_correct` evolution preserved.
