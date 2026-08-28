<!-- V2_3_EVIDENCE_ONLY_NOTICE:BEGIN -->
> V2.3 authority notice (R0 synchronization): this V2.2 R1 decision record is retained as evidence-only. The current route is `docs/roadmap/RESEARCH_ROADMAP_V2_3.md`; its implementation lineage and acceptance claims require independent reconciliation under V2.3. This file is not a current execution pointer.
<!-- V2_3_EVIDENCE_ONLY_NOTICE:END -->

# R1 Deterministic Repair/Evidence Closure Decision

## 1. Authority and status

```text
research_route=docs/roadmap/RESEARCH_ROADMAP_V2_2.md
implementation_base_head=4d4cfdb92c8d181cf607cdf79368a9585ec4ca0e
behavior_parent_head=5ef7fa9a6011534362a2094e159eee75c672619c
branch=stage2-general-feedback
primary_empirical_environment=Vitis HLS 2023.2
R1_STARTED=true
R1_IMPLEMENTED_PENDING_EXTERNAL_AUDIT=false
R1_ACCEPTED=true
R2_STARTED=false
```

This record freezes the R1 implementation boundary.  It does not accept its
own implementation.  Acceptance requires a separate review of the execution
archive produced on the server.

## 2. Real-code gap closed by R1-A

Before R1, `ValidationStateMachine` and `RecoveryPolicy` both allowed a
deterministically Testbench-owned Preflight failure to route to Testbench
repair, but `CandidateRepairValidationOrchestrator` only executed runtime
Testbench repair for Public CSIM and Public COSIM.  R1 closes that mismatch:

- Preflight is an explicit runtime Testbench-recovery state;
- exactly one Public suite must match the Preflight Testbench content;
- GENERATED/DERIVED/CACHED Public sources are bounded AUTO repair inputs;
- PROVIDED/FILESYSTEM/EXTERNAL/unspecified sources stop for review;
- Hidden remains forbidden;
- one Testbench repair uses the existing `TestbenchRepairLoop`;
- revalidation restarts from Preflight and shares the same
  `RecoveryLedger`, `BudgetManager`, and `TraceRecorder`.

No new FSM node or success authority is introduced.

## 3. R1-B semantic revision contract

`TestbenchSemanticManifest` persists no source.  It binds a suite revision to:

- suite/split/source-kind identity and content SHA-256;
- Original/Candidate top reference and definition counts;
- case literals and normalized oracle-expression fingerprints;
- comparison operators, oracle markers, failure signals and control flow;
- stream/memory/interface protocol markers;
- allowed and forbidden edit classes;
- parent/revision IDs and a canonical revision SHA-256.

The independent evidence auditor rejects unauthorized revisions, removed or
changed existing literals/oracles, weaker comparisons/failure signals,
removed top calls, added top reimplementations, or weakened runtime protocol.
An unprovable change is blocked before full revalidation; passing compilation
alone is not sufficient.

PROVIDED Public is deliberately conservative in R1: it is review-required
rather than silently edited.  Narrow, provable PROVIDED transformations remain
a later extension after a separate authorization design.  Hidden is never
repaired or persisted as semantic content.

## 4. R1-C effective quota explanation

`EffectiveRepairQuotaSummary` combines, without replacing either authority:

- requested Candidate maximum;
- lane-local limits;
- `RecoveryPolicy` limits and `RecoveryLedger` counts;
- run-total and validation-restart limits;
- hard-budget configuration, observed use, active reserve and current
  remainder;
- accepted attempts and denial reasons.

The artifact is explicitly `explanation_only`.  It creates neither a counter
nor budget authority.

## 5. R1-D DiagnosticEvent projection

`DiagnosticEventProjector` projects allowlisted fields from existing typed
Public evidence.  Each event binds stage, owner, failure class, route action,
target/toolchain fingerprints, Candidate hash, Public suite identities and a
stable context signature.  It cannot:

- project Hidden evidence;
- retain source content or raw secret paths;
- declare acceptance;
- mutate the FSM;
- replace Router, validators, `RecoveryPolicy`, or the evidence auditor.

## 6. R1-E corpus v1 evidence levels

The R1 package creates a hash-manifested corpus with explicit evidence levels:

- E2 deterministic fixtures cover the required taxonomy and abstention lanes;
- E3 verified Vitis 2023.2 replay evidence is imported from the previously
  produced P0 archive, whose hash is independently checked;
- successful Strassen CSYNTH and Aho-Corasick COSIM are positive witnesses;
- LinkedList COSIM is retained as a real failure/unknown witness with
  `evidence_complete=false`, `repair_eligible=false`, and no false acceptance;
- incomplete or invalid evidence cannot be promoted.

Synthetic E2 fixtures are not represented as real failures.  The corpus
manifest preserves that distinction.

## 7. Required R1 gates

- focused R1 owner/repair/semantic/negative tests pass;
- complete deterministic unit regression passes in the `agrefactor` env;
- no provider call is made by the package;
- no new physical Vitis run is needed: the embedded E3 archive is verified and
  behavior-compatible because the R0 head is documentation-only over the P0
  behavior parent;
- zero Hidden source read and zero Hidden repair;
- no false acceptance in negative/unknown evidence;
- package/repository manifests and all referenced hashes verify;
- independent external review is clean.

All R1 gates were independently checked and `R1_ACCEPTED=true`; R2 remains
blocked until a new bounded plan is frozen.

## 8. Explicit non-goals

R1 does not enable AI advisory, memory retrieval, learned repair-pattern
promotion, FSM mutation, toolchain-version migration, dynamic optimization, or
cross-version generalization claims.


## 9. External acceptance

R1 was independently accepted at `2026-08-25T14:08:59+08:00` from result archive
`agrefactor_r1_consolidated_implementation_validation_v1_20260825T060024Z_1639926.tar.gz` (`sha256:d93088ec50ae7f44105cc5acf87673fcc33e3c53c4d10ca35ef9f174c852fc29`).  The external decision digest is
`15c7b0e4f167596906a379722bb3a6ca065b18e96dd435d0ea60f18d5eafe55f`.  Package self-acceptance remained false.
