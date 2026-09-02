# V2.3 R4 Gate-Authorized Candidate Repair Design

Status: implementation applied; pending external validation.

Route: V2.3. Required predecessor evidence: R0, R1-Safety, R1-Data, R2,
and R3 are independently accepted. R3 validation archive:
`agrefactor_v23_r3_conditioned_memory_gate_external_validation_20260831T130943Z_3145805.tar.gz`,
SHA-256 `401b7369f8f3125018ffee17fbcbb2fad9fe26a034cd1c8aab1829d865e42ca8`.

Implementation base: `54e422989c6bff962312efc468200c28dd7b4276` on
`research-roadmap-v2.3`. The three R3 predecessor receipts listed in
`V2_3_R4_DESIGN.json` must be independently hash-verified before this design
may reconcile R3 state. This document is a contract, not an implementation
claim and not an acceptance receipt.

## 1. Purpose and Scope

R4 is the first V2.3 phase permitted to add a new LLM-advisory repair authority.
That authority is strictly limited to mutating a Candidate in a pre-registered
canary. It must never mutate an Original source, any Public or Hidden
Testbench, target/toolchain configuration, fixed validation FSM, existing
deterministic route, optimizer state, or `best_correct` pointer.

The R4 lane is distinct from existing deterministic Candidate repair. Existing
deterministic Testbench repair remains unchanged. An R4 authorization neither
rewrites nor replaces a deterministic `FeedbackRouteDecision`; it records that
decision and starts an explicitly labelled experimental Candidate lane only if
all hard gates allow it. An R4 candidate that passes is accepted only because a
fresh formal validator completes its required prefix, never because a model,
advisory, Gate, package, or controller says it is correct.

R4 is not a general repair router, a Testbench repair system, a new success
authority, an automatic model fallback, a memory promotion phase, a campaign,
or a claim of open-world generalization. R5 remains responsible for paired
arms, time ordering, and causal comparisons. R3's contract validation proves
identity/evidence partitioning and shadow-only behavior; it does not by itself
establish a time-ordered memory efficacy result.

## 2. Existing Integration Facts

The current integration point is
`agrefactor/runtime/candidate_repair_integration.py`.
`CandidateRepairOrchestrationRequest` already carries
`llm_advisory_mode` (`off` or `candidate-only`) and
`approved_memory_snippets`; neither field is authorization by itself.
`CandidateRepairValidationOrchestrator` already creates a `RecoveryLedger`,
uses the parent `BudgetManager`, and revalidates candidate attempts through a
fresh `ValidationOrchestrator`. R2 currently projects diagnostics and runs an
optional shadow advisor without changing the deterministic route. R3 adds an
append-only `EpisodeStore`, immutable `DiagnosticEpisode`, versioned
`RepairPatternRevision`, and a deterministic `ApplicabilityGate`; its accept
decision remains shadow-only.

R4 must extend these boundaries rather than duplicate tool evidence, feedback
parsing, validation stages, budget accounting, trace recording, or candidate
loop behavior. The existing `RecoveryPolicy` and `RecoveryLedger` are the
permission/accounting authority; the existing formal validator remains the
correctness authority.

## 3. Fixed Authorization Chain

The implementation shall expose an adjacent R4 controller, proposed as
`agrefactor/recovery/gated_candidate_repair.py`, with an immutable
`R4CandidateRepairAuthorization` record. Its `authorization_id` is a canonical
SHA-256 of at least the run identity, terminal diagnostic event reference,
advisory identity, Gate contract hash, pattern revision hash, canary manifest
hash, pre-repair Candidate hash, policy decision id, and budget reservation id.

The controller may request exactly one Candidate mutation only after this fixed
order completes:

1. A typed physical failure and complete agent-safe `DiagnosticEvent` exist.
2. R2 returns a schema-valid advisory or abstains. The advisory remains
   `accepted=false` and retains only approved evidence references.
3. R3 Gate returns `accept` for a `Trusted` revision; rejected, abstained,
   quarantined, deprecated, rejected, sparse, conflicting, out-of-distribution,
   or uncalibrated decisions are terminal non-mutation outcomes.
4. The pre-registered canary manifest matches the run, source/case identity,
   target, toolchain, model/prompt identity, stage, and maximum attempt rule.
5. R4's policy adapter requests `RecoveryAction.REPAIR`,
   `RecoveryRole.CANDIDATE`, `RecoveryAuthority.LLM_ADVISORY`, an allowed
   Candidate stage, `agent_safe` evidence, complete evidence, and
   `advisory_mode=candidate-only`. A denied, review-required, or
   budget-blocked policy result is final.
6. The same authorization is accepted by `RecoveryLedger` and an isolated R4
   reserve is acquired from the shared `BudgetManager` before any provider call.
7. A bounded Candidate-only loop produces at most one candidate descendant.
8. A fresh full prefix revalidation runs using the existing validator plan.
9. The independent evidence auditor examines identity, visibility, semantic,
   authority, and false-success conflicts.
10. An immutable R3-compatible episode records the outcome and all references.

The controller must preserve the original deterministic terminal decision in
its artifacts. It may never modify the FSM state set, transition table, route
decision, or terminal report. A canary repair begins only from an explicitly
eligible, non-Hidden terminal context; it is not a route coercion.

## 4. Hard Eligibility and Feature Firewall

Every check is fail-closed. A missing field, malformed hash, unknown enum,
incomplete citation, unrecognized stage, or unrecognized canary id denies
mutation.

Required conditions are: Candidate role; `agent_safe` view; no Hidden input;
no secret/private reasoning/raw-provider/raw-exception content; exact complete
execution identity; physical tool evidence; Gate `accept`; lifecycle `Trusted`;
calibrated advisory contract; compatible stage/scope; policy and ledger allow;
reserve acquired; active canary; kill switch clear; and exact before-Candidate
hash. The model may receive only the same agent-safe evidence already allowed
to a Candidate repair prompt plus Gate-approved memory snippets whose manifest
hash is cited by the authorization.

Forbidden inputs and effects include Hidden source, oracle, path, digest or
aggregate-derived feature; Testbench source or edit proposal; Original source
mutation; target/profile edits; changing a route/FSM; use of a similarity-only
revision; future outcome; reusing a stale authorization; nested R4 repair;
unbounded retries; and writing a best pointer. Any firewall violation is
`invalid_evidence`, triggers kill/quarantine, and forbids the mutation.

## 5. Canary, Kill Switch, and Quarantine

`R4CanaryManifest` is immutable, content-addressed, and loaded before the run.
It lists only eligible case/source ids, exact target/toolchain/parser/profile
identity, model and prompt identity, allowed stage/scope, one repair maximum,
reserve bounds, and an expiry. The default is disabled. No advisory, Gate, or
model output can enable or expand a canary.

`R4KillSwitchState` is operator-controlled and append-only. Once active, it
blocks every future LLM Candidate mutation for the scope named by the manifest;
R2 shadow observation may continue. Required triggers are: critical auditor
finding, false repair, semantic weakening, Hidden/secret leakage, identity
conflict, policy/ledger authority violation, authorization hash mismatch, or
an unsafe-scope attempt. The controller must read the kill state immediately
before reserve acquisition and immediately before provider invocation.

Quarantine must not mutate a `RepairPatternRevision` in place. An append-only
`R4RevisionSafetyRecord` references the revision hash, authorization id,
trigger, evidence refs, time, and resulting scope. Gate evaluation consults
this record before allowing the revision. A quarantined revision cannot make a
new automatic R4 repair; only a separately reviewed child revision may later
be considered.

## 6. Budget, Ledger, and Candidate Boundaries

The R4 reserve is labelled separately from main and R2 shadow usage in budget,
trace, identity, and result artifacts. It is acquired before the provider call
and released or finalized deterministically. The required reserve contains at
least one provider call, the bounded Candidate attempt, and every full-prefix
validation operation that may follow. No main-path budget, recovery count, or
restart count may be silently overwritten. If reserve acquisition fails, the
result is `inconclusive` or `abstained` as appropriate, with zero mutation.

The authorization has one lineage and one immutable before hash. It permits
one new Candidate file/string only. Public and Hidden Testbench hashes must be
recorded before and after and remain byte-identical. Original source and target
identity must remain byte-identical. Provider transport failure, malformed
response, unchanged response, validation failure, budget failure, or tool
failure consumes the frozen attempt according to the existing counted-attempt
rules and can never retry through a second R4 authorization in the same run.

R4 must never call an optimizer mutation path or write `best_correct`. A
failed candidate leaves every existing best pointer and the original candidate
unchanged. A validated descendant may be materialized only through existing
candidate-artifact governance and must cite its authorization id.

## 7. Revalidation and Outcome Attribution

The only positive path is a new Candidate hash followed by the complete formal
prefix required by the task: preflight, applicable Public native CSIM, CSYNTH,
applicable Public COSIM, and terminal Hidden evaluation when the task contains
Hidden suites. Hidden remains terminal and never supplies model-visible data.
The final result must retain the formal validation id, stage artifacts,
execution identity, budget delta, suite provenance, and independent auditor
result.

`verified_positive` requires legal authorization, a changed Candidate, full
prefix success, semantic preservation, complete identity, and an auditor with
no critical finding. `verified_negative` requires a legal attempted repair,
complete before/after identities, attributable failure, and excluded
infrastructure/environment alternative. `abstained` records any intentional
non-authorization before mutation. `inconclusive` records budget, provider,
tool, timeout, or attribution uncertainty. `invalid_evidence` records every
firewall, identity, artifact, or authority violation. A compile pass, model
claim, individual return code, or package exit code is never a positive
outcome.

## 8. Proposed Implementation Surface

The implementation must be narrowly scoped and use existing local patterns.

| Area | Proposed responsibility | Prohibited responsibility |
| --- | --- | --- |
| `agrefactor/recovery/gated_candidate_repair.py` | immutable authorization, canary/kill/quarantine contracts, policy/ledger/budget admission | success authority, direct FSM rewrite, Testbench edit |
| `agrefactor/runtime/candidate_repair_integration.py` | opt-in controller placement after a preserved terminal decision; fresh full revalidation | treating `llm_advisory_mode` or a Gate accept as sufficient authorization |
| `agrefactor/recovery/memory_gate.py` | consume immutable Gate/revision/episode interfaces | mutate revision or promote from R4 outcome alone |
| `agrefactor/evidence/auditor.py` | check authorization, before/after identity, no-hidden, no-testbench-edit, best-pointer protection | infer model success |
| `tests/test_r4_*` | deterministic contracts, negative cases, replay stubs | simulated evidence presented as real Vitis/provider proof |

No broad refactor of legacy generation, prompt builders, model registry,
validation FSM, or optimizer is authorized by R4.

## 9. Required Deterministic Tests

The R4 implementation package must provide focused tests for: disabled canary;
wrong canary identity; Gate reject/abstain/quarantined/non-Trusted revision;
missing/Hidden/secret identity; unsupported role/stage/scope; policy denial;
ledger denial; insufficient reserve; stale authorization; changed Testbench or
Original hash; one-attempt cap; pre- and post-provider kill switch; quarantine
append-only behavior; model invalid/unchanged output; full-prefix revalidation
on every changed candidate; positive/negative/abstained/inconclusive/invalid
outcomes; preservation of deterministic route/FSM; no AI success authority;
and immutable `best_correct` on all R4 failure paths.

Tests must also prove R2 shadow/main equivalence still holds when R4 is
disabled, and that no R4 controller is imported or invoked by default product
paths. A deterministic fake validator may test mechanics, but cannot satisfy
the later real-evidence gate.

## 10. Evidence and Acceptance Plan

The implementation phase requires product code plus deterministic tests. Its
external validation must include a frozen canary manifest, exact implementation
hashes, clean baseline/full regression evidence, focused R4 matrix, explicit
provider/Vitis counters, real bounded repair only when separately authorized,
and a no-advisory/no-memory fair baseline.

The fair baseline is not a prose comparison. Before a real canary is allowed,
`V2_3_R4_FAIR_BASELINE.json` requires a concrete, content-addressed manifest
for both arms. It freezes the code and test hashes, case and source identity,
target/toolchain, model and prompt identity, decoding, seed, timeout,
parallelism, budget, retry limit, validator prefix, independent auditor,
repetition count, and reporting rules. The only permitted arm difference is
the R4 advisory plus Gate-approved-memory feature switch. Missing or unequal
fields make the run `invalid_evidence`, not a baseline result.

Independent acceptance must verify the receipt hashes, canary scope,
fair-baseline manifest, kill/quarantine records, complete revalidation, no
Hidden/Testbench leakage, no authority violation, and best-pointer
preservation.

This design package itself performs none of those implementation or real-world
actions. It may only advance the repository to
`V2.3-R4-design-external-acceptance` after independent review.
