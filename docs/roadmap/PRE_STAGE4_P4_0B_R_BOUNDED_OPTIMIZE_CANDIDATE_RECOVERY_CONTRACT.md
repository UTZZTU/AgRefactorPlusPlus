# Pre-Stage-4 P4-0B-R Contract — Bounded Optimize Candidate Recovery

> **Status:** design frozen; implementation not yet claimed.
>
> **Placement:** after accepted P4-0B Global Typed Preflight and before P4-0C
> Public native Vitis CSIM.
>
> P4-0B-R reuses typed ownership and the existing provider-neutral repair
> substrate without reopening P4-0B or replacing the Stage 3 optimizer.

## 1. Why this package is placed here

P4-0B first makes Candidate ownership authoritative through independent physical
Preflight stages. A bounded Optimize recovery policy can only be safe after that
ownership exists.

The policy is frozen before P4-0C so native CSIM does not acquire an accidental
or inconsistent retry contract. P4-0C remains responsible for implementing real
Public Vitis CSIM. Public-CSIM-driven repair is not part of the initial
P4-0B-R implementation.

## 2. Product scope

P4-0B-R applies only to:

```text
optimize
full → optimize phase
```

It does not change Refactor Candidate repair. It does not modify Testbench,
Original/reference, Target configuration, or toolchain state.

## 3. Initial repairable failures

A failed Optimize Candidate is eligible only when all conditions are true:

```text
failure_owner=candidate
route_action=repair_candidate
agent-safe evidence is complete
shared budget can prospectively fund the attempt and mandatory restart prefix
the root Candidate has not already used its one recovery attempt
```

Initial supported classes:

```text
PREFLIGHT:
  candidate_compile_failed
  candidate_top_missing
  candidate-side interface_mismatch

CSYNTH legality:
  typed Candidate-owned non-synthesizable construct or interface failure
  typed Candidate-owned unsupported HLS language/pragma usage
```

A mixed or unisolated final `link_failed` remains unknown-safe and is not
repairable.

## 4. Explicitly non-repairable

```text
Testbench-owned failure during per-Candidate Optimize qualification
Reference/Original failure
toolchain_failed
configuration_failed
ownership_unknown
unisolated link_failed
Hidden failure or Hidden diagnostics
RTL COSIM failure
PPA regression
latency/resource/timing objective miss
feasible but non-improving Candidate
```

Performance evidence drives the next optimization hypothesis. It never causes
unbounded in-place Candidate repair.

Public CSIM repair is deferred. After P4-0C it may be evaluated as a separate,
default-off extension with its own acceptance evidence.

## 5. One-attempt invariant

```text
max_optimize_candidate_repairs_per_root_candidate=1
```

The limit is total across Preflight and CSYNTH. A Candidate repaired after
Preflight cannot receive a second repair after a later CSYNTH failure.

Nested repair descendants are forbidden.

## 6. Candidate lineage

A repair never overwrites the failed Candidate. It creates a new ordinary
`cand-N` record:

```text
source Candidate:       cand-K
repair descendant:      cand-N
parent_candidate_id:    cand-K
hypothesis_id:          unchanged
decision.recovery_of:   cand-K
decision.recovery_attempt: 1
decision.recovery_stage: preflight|csynth
decision.recovery_reason_codes: [...]
```

The source and repaired SHA-256 values, prompt identity, model identity, safe
feedback identity, budget delta, and validation result are persisted.

## 7. Prompt boundary

Only the Candidate artifact is editable.

Read-only material may include:

```text
Original/reference source
public top/interface contract
agent-safe Preflight or CSYNTH evidence
the originating optimization hypothesis and modification scope
```

Forbidden prompt inputs and actions include:

```text
Hidden source, identifiers, diagnostics, paths, or artifacts
modifying Testbench or Original/reference
renaming or weakening the top interface
hard-coded test cases
falling back silently to baseline
deleting the optimization merely to compile
claiming synthesis, correctness, or PPA success without tool evidence
```

## 8. Validation restart

Every changed repair descendant restarts from the beginning of the Optimize
qualification prefix. It never resumes after the failed subprocess:

```text
Source integrity
→ Typed Preflight
→ Public validation
→ CSYNTH
→ later frozen hardware/Hidden/PPA/feasibility stages
```

As P4-0C and P4-0D land, native CSIM and COSIM enter this same restart order.

## 9. `best_correct` invariant

`best_correct` is unchanged while the source Candidate or repair descendant is
being repaired or validated.

Only a completely qualified, feasible, objectively improved descendant may
replace `best_correct`. Provider error, invalid response, budget exhaustion,
repair failure, validation failure, or non-improvement preserves the existing
`best_correct`.

## 10. Budget contract

P4-0B-R uses the same run-wide `BudgetManager`.

Before the repair model call, the system prospectively checks capacity for:

```text
one permitted repair model attempt
required artifact/evidence writes
the mandatory validation restart prefix
```

Only physical calls are consumed. P4-0F remains responsible for normal
mode-specific defaults and Full Optimize reserves.

## 11. Reused infrastructure

The implementation should reuse, rather than duplicate:

```text
CandidateModelAdapter
candidate-only repair prompt constraints
repair protocol and artifact writer
provider/response validation
agent-safe/operator-full separation
budget snapshots and terminal stop reasons
```

Optimize-specific code remains authoritative for:

```text
qualification order
CandidateRecord lineage
cache identity
best_correct transitions
PPA comparison
optimizer continuation policy
```

The Refactor repair orchestrator is not copied wholesale because its historical
validation order differs from the frozen Optimize qualification order.

## 12. Implementation split

```text
P4-0B-R1  Optimize Preflight Candidate recovery and lineage
P4-0B-R2  Candidate-owned CSYNTH legality recovery
```

Both remain within the single one-attempt-per-root-Candidate invariant.

Public CSIM repair is not included in R1 or R2.

## 13. Acceptance criteria

P4-0B-R closes only when tests and evidence prove:

- eligible Candidate-owned failures receive at most one repair;
- ineligible owner/reason combinations never call the model;
- the repaired source is a new `cand-N`, never an overwrite;
- the same hypothesis and explicit recovery lineage are retained;
- validation restarts from Source/Preflight;
- no Hidden evidence reaches prompts or agent-safe artifacts;
- repair/provider/response/budget failures preserve `best_correct`;
- a repaired but non-improving Candidate does not replace `best_correct`;
- PPA and objective misses create new optimization work, not repair loops;
- direct Optimize and Full Optimize use the same policy;
- `safe-v1` historical replay remains reproducible.

## 14. Non-goals

P4-0B-R does not implement:

- native Public Vitis CSIM;
- Public RTL COSIM;
- mode-specific budget defaults;
- `dynamic-v1`;
- general Testbench repair inside Optimize;
- Hidden repair;
- PPA-driven in-place repair;
- more than one recovery attempt per root Candidate.
