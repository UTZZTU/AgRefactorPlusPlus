# V2.3 R5 Continual Memory Campaign Design

Date: 2026-09-18

Status: frozen for implementation; not accepted; no efficacy claim

Design base:
`03313195dda0612e80f8b57213c267a3694ad7d1`

Machine-readable contract:
`docs/roadmap/V2_3_R5_DESIGN.json`

## 1. Purpose

R5 must turn the accepted R2-R4 contracts into a time-ordered, executable
continual-memory mechanism and a causally interpretable A0-A6 campaign.

R5 answers whether verified positive and negative repair experience can be
accumulated, narrowed, quarantined, and deprecated without granting an AI
success authority. It does not train model weights, redesign the FSM, create a
new product CLI, or establish the final paper claim. R6 remains separate.

The primary product path is `refactor`. `full` consumes the accepted
refactor material and is tested as a composition boundary. Direct `optimize`
retains `safe-v1` as a supporting subsystem and is not an R5 research arm.

## 2. Fixed Safety Boundary

Every arm preserves these invariants:

- the only product entrypoints are `refactor`, `optimize`, and `full`;
- the ordinary product default remains `off`;
- known deterministic Candidate/Testbench recovery runs before R2;
- only agent-safe Public unknown/review evidence may enter R2;
- Original, Public Testbench, Hidden Testbench, Target, and configuration are
  never AI mutation targets;
- Hidden source, path, digest, oracle, and outcome never enter model-visible
  evidence or snapshot K;
- formal validation and the independent auditor are the only success
  authorities;
- at most one experimental Candidate mutation is allowed per arm run;
- failed mutation never replaces `best_correct`;
- Policy, Ledger, Budget, execution identity, provenance, and kill switch are
  shared across arms;
- critical safety violations stop the campaign and quarantine the responsible
  revision.

## 3. Runtime Data Model

### 3.1 Episode envelope

R3 shadow episodes and R4 repair episodes retain their existing payload
schemas. A new immutable envelope indexes both without weakening either:

```text
schema_version
episode_kind = r3_shadow | r4_repair
episode_id
payload_schema_version
payload_sha256
execution_identity_sha256
source_sha256
context_signature
created_at
observed_at
lineage
agent_safe_summary
outcome
manifest_sha256
```

The file-backed ledger is append-only. A duplicate ID, changed payload,
invalid canonical hash, incomplete identity, future/source leakage, Hidden or
secret content, raw provider response, or private reasoning makes the record
invalid and ineligible for lifecycle reduction.

### 3.2 Outcome eligibility

`verified_positive` requires legal authorization, a changed Candidate,
fresh formal validation through the required terminal prefix, semantic
preservation, complete identity, and a clean auditor result.

`verified_negative` additionally requires attributable failure and explicit
exclusion of provider, toolchain, environment, identity, and infrastructure
causes.

`abstained`, `inconclusive`, and `invalid_evidence` remain distinct.
None may contribute positive support. Invalid and inconclusive records never
promote a revision.

### 3.3 Pattern revision

Every revision is immutable and content addressed. A changed support,
exclusion, payload, lifecycle, or threshold creates a child revision. Required
fields are:

```text
revision_id / parent_revision_id / revision_sha256
failure_family / stage / owner
supported_when / avoid_when / exact_exclusions
required_evidence
positive_episode_refs / negative_episode_refs
calibration_refs
memory_payload_manifest_sha256
lifecycle / transition_reason
threshold_source
created_at
```

### 3.4 Candidate-only memory payload

The payload consumed by the existing Candidate repair prompt is typed and
separate from applicability conditions:

```text
schema_version
snippet_id
revision_id
revision_sha256
snapshot_sha256
repair_intent_or_recipe
supported_when
avoid_when
candidate_only_scope=true
source_episode_hashes
evidence_refs
payload_sha256
```

It may contain only agent-safe repair intent or a bounded repair recipe. It
must not contain Hidden/Testbench/Original mutation instructions, future
outcomes, raw provider responses, private reasoning, or an assertion of
success.

## 4. Lifecycle Policy r5-lifecycle-v1

Thresholds are frozen here before evidence inventory and evaluation.

### 4.1 Eligible context

An independent context requires a distinct source SHA-256 and context
signature. Repeated runs of the same source count for reliability but not as
new independent support.

### 4.2 Transitions

Quarantined -> Provisional requires:

- at least one verified-positive eligible episode;
- at least one independent source/context;
- zero false repair, unsafe-scope, authority, or leakage event;
- citation validity of 1.0 for supporting episodes.

Provisional -> Trusted requires:

- at least two verified-positive eligible episodes;
- at least two independent source hashes and context signatures;
- an accepted R2 calibration reference;
- attributable-negative rate <= 0.20 after exact exclusions;
- false-repair rate = 0;
- unsafe-scope rate = 0;
- citation validity = 1.0.

Any critical safety event immediately quarantines the revision. A Trusted
revision is also quarantined when either condition holds in its current
applicability context:

- at least two attributable negatives in the last five eligible applications;
- attributable-negative rate > 0.25 in at least three eligible applications.

A quarantined revision becomes Deprecated when, after quarantine, at least
three eligible observations over at least two independent sources show an
attributable-negative rate > 0.40 and no refined child revision restores the
frozen thresholds.

Promotion, quarantine, and deprecation are deterministic reducer outputs.
They are never model decisions. A reviewed child revision may narrow
`supported_when` or add `avoid_when`/exact exclusions; the parent is never
rewritten.

## 5. Frozen Snapshot And Leakage Boundary

Each evaluation period uses:

```text
history T0..Tk
-> reduce eligible episodes
-> freeze revision DAG and snapshot K
-> evaluate future Tk+1..Tn
-> append outcomes only to the next-period ledger
```

Snapshot K includes the selected and rejected revision hashes, lifecycle
policy hash, evidence inventory hash, exact exclusions, conflict/sparsity/OOD
facts, and the latest allowed timestamp. It may read only history partitions.

Case-level and source-level holdouts are both required. Source hashes are
deduplicated before splitting. Future outcomes, post-hoc labels, repair
results, Hidden artifacts, and caches created by another arm are forbidden
inputs.

## 6. A0-A6 Arms

All arms share the same source, tests, TargetProfile, initial Candidate,
baseline terminal evidence, model/provider, prompt templates, seed where the
provider honors it, timeout, parallelism, per-case budget, and repeats.

| Arm | Advisor | Memory | Automatic Candidate repair |
|---|---|---|---|
| A0 | off | none | off |
| A1 | shadow | none | off |
| A2 | on | none | once |
| A3 | on | similarity-only | once |
| A4 | on | positive-only gated | once |
| A5 | on | positive+negative gated | once |
| A6 | on | full lifecycle gated | once |

A0 retains the common initial Candidate generation. “No automatic LLM
repair” applies only after that frozen initial Candidate.

A2 binds the calibrated advisory, Policy, Ledger, Budget, and
`memory_mode=none`; it must not fabricate a Trusted revision.

A3 binds a content-addressed similarity retrieval manifest. Similarity is not
a Gate accept and cannot override exact safety exclusions.

A4 uses only positive eligible support for memory selection. Shared hard
safety exclusions still apply.

A5 adds attributable negative support, conflict handling, and negative
transfer abstention.

A6 adds the full versioned lifecycle and period-to-period snapshot update.

### 6.1 Discriminated research authorization

The accepted R4 authorization remains unchanged. An adjacent R5 experiment
authorization records one of:

```text
advisor_only
similarity_only
gated_memory
```

`advisor_only` proves that no memory was supplied. `similarity_only` binds
the retrieval manifest without claiming Gate acceptance. `gated_memory`
binds Gate contract hash, revision hash, snapshot hash, and payload manifest
hash. All modes use the accepted R4 mutation controller, one-attempt limit,
fresh validator, and auditor.

## 7. Paired Physical Evidence

For each case/repeat, the campaign creates one immutable common baseline
terminal artifact from the existing refactor validation flow. All seven arms
fork from that exact Candidate and diagnostic event.

- A0 observes the common baseline and performs no extra provider/Vitis work.
- A1 performs only the advisor call.
- A2-A6 may perform an advisor call and at most one mutation call.
- Only an arm that mutates a Candidate launches a fresh full formal
  revalidation.

The common baseline is shared input evidence, not an independent observation
per arm. Reports must not multiply its Vitis cost or sample count. Mutated
Candidate validation is never shared or cached across arms.

This paired-parent design removes stochastic initial-generation and duplicate
baseline-Vitis confounding while preserving the real refactor orchestrator.
Separate entrypoint smoke tests prove ordinary `refactor` and `full`
composition.

## 8. Dataset Contract

P2 must build an immutable inventory from verified R1/R2/R4 and other real
Vitis archives. Synthetic fixtures are contract tests only.

Each primary failure family must have:

- history and future cases separated by time;
- at least two independent source hashes overall;
- positive and inapplicable/confusable controls;
- complete source/test/Target/toolchain/parser/model/prompt identity;
- no source hash crossing a source-level holdout boundary.

A family with 100% abstention caused by fixable evidence plumbing fails the
capability gate. Genuine ambiguity, OOD, conflict, or authority refusal remains
a correct abstention and is reported separately.

If the inventory cannot support a primary paired campaign, R5 stops for data
acquisition. It must not hand-create Trusted revisions or move future cases
into history.

## 9. Budget Contract

Global authorized ceilings:

```text
provider_calls <= 500
vitis_launches <= 500
```

The budget ledger begins at the user's original authorization point. All
observed calls after that point, including failed requests and superseded
pilot runs, count. P2 must reconcile accepted and failed artifact roots before
any new real call.

A provider call counts when a request crosses the provider boundary,
including timeout or invalid response. A Vitis launch counts every actual
CSIM, CSYNTH, or COSIM process launch.

Deterministic design, implementation, and regression phases consume zero.
Before any real phase:

1. compute the exact case/arm/repeat upper bound;
2. subtract reconciled consumption;
3. reserve at least 10% of the remaining provider and Vitis budgets for
   failure recovery and audit reproduction;
4. prove that mutation arms retain enough budget for complete fresh
   validation;
5. stop before execution if the upper bound does not fit.

Pilot consumption is capped at the smaller of 20% of reconciled remaining
budget or 60 provider/80 Vitis calls. Formal campaign size and repeats are
frozen only after P2 inventory. Target repeats are three; fewer repeats require
a protocol revision, not a silent runtime decision.

## 10. Endpoints And Statistics

Co-primary endpoints:

- paired verified-repair success difference;
- attributable negative-transfer/false-repair difference.

Secondary endpoints:

- owner/failure-family accuracy and unknown detection;
- citation validity and calibrated confidence;
- Gate accept/reject/abstain and reason distribution;
- promotion, quarantine, deprecation, and revision churn;
- full-chain pass and time-to-repair;
- provider/Vitis/token/cost/wall-time consumption.

Critical safety endpoints are not averaged away:

- Hidden/secret/private-reasoning/raw-response leak;
- unauthorized target mutation;
- false success or semantic weakening;
- identity/provenance conflict;
- source/time/cache contamination.

Reports use paired case/repeat results, exact paired differences, source-
clustered bootstrap confidence intervals when sample size permits, full
repeated-run distributions, and worst cases. Statistical significance cannot
override a critical safety violation. R5 may report an inconclusive or
negative result.

## 11. Campaign Order

```text
deterministic contract tests
-> full deterministic regression
-> one-case A0-A6 wiring smoke
-> bounded multi-case real pilot
-> freeze code/dataset/snapshot/arm manifest
-> counterbalanced formal A0-A6 campaign
-> independent file-only audit
-> project-owner R5 acceptance decision
```

Arm order is counterbalanced within case/repeat. Workspaces and mutable caches
are isolated. Resume may skip only a content-addressed completed arm artifact
whose full identity matches the frozen manifest.

## 12. Stop Rules

Stop the current real phase on:

- a critical safety or authority violation;
- source/time/cache leakage;
- provider/model/toolchain identity drift;
- mismatch between budget ledger and observed calls;
- inability to reserve complete post-mutation validation;
- a required change to A0-A6 semantics, primary endpoints, or inclusion rules;
- exhaustion or projected exhaustion of either 500-call ceiling.

Provider/environment failures are inconclusive, not negative memory. A common
family blocked by an evidence producer defect returns to the producer contract
and deterministic tests. A sample-specific log code is not added as an
authorization rule without stable family semantics and cross-source evidence.

## 13. Implementation Targets

Expected adjacent modules:

```text
agrefactor/recovery/episode_ledger.py
agrefactor/recovery/pattern_lifecycle.py
agrefactor/recovery/r5_memory_payload.py
agrefactor/recovery/r5_authorization.py
agrefactor/recovery/r5_snapshot_builder.py
agrefactor/campaign/r5_protocol.py
agrefactor/campaign/r5_runner.py
agrefactor/campaign/r5_reducer.py
```

Existing modules are extended narrowly:

```text
agrefactor/runtime/r4_integration.py
agrefactor/runtime/candidate_repair_integration.py
agrefactor/prompts/candidate_repair.py
agrefactor/product/source_bootstrap.py
agrefactor/evidence/auditor.py
```

Names may change only when existing ownership makes another adjacent location
clearly better. No new CLI or Vitis runner is permitted.

## 14. Completion Boundary

R5 is ready for project-owner acceptance only when:

- episode ledger, lifecycle, snapshot, payload, authorization, and campaign
  producers/consumers are real;
- default-off `refactor`, direct `optimize`, and `full` regressions pass;
- the frozen A0-A6 campaign is rebuildable and budget-auditable;
- time/source/cache leakage is zero;
- false repair, negative transfer, abstention, invalid, inconclusive, and cost
  results are separately reported;
- the independent auditor has no critical finding.

The implementation agent cannot accept R5. It stops at
`ready_for_user_acceptance`. R6 and paper writing remain prohibited until
the project owner explicitly accepts R5.
