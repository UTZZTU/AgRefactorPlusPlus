# Pre-Stage-3 Bridge

## Status

Stage 2 remains closed. Stage 3 has not started and is explicitly gated by this
bridge. Work must proceed in small, evidence-driven steps.

<!-- PRE_STAGE3_PRODUCTIZATION_PLAN:BEGIN -->
## Frozen productization and closure decisions

完整实施合同见
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md)。

普通 CLI 必须要求 source、`--top` 和 model，只暴露
`refactor / optimize / full`；普通用户不选择 `--legacy / --repair-aware`。

冻结顺序：

```text
P1 known-model profiles
→ P4 Public/Hidden source contract
→ P2 source-only bootstrap
→ Execution Identity
→ P5 concise output
→ P0 real DFS accepted
→ cleanup and closure
→ Stage 3
```

P3 不再是活跃工作项，三个退休的静态启发式硬门禁不得恢复为最终裁决者。
<!-- PRE_STAGE3_PRODUCTIZATION_PLAN:END -->

The earlier P0-P5 list remains the long-term product map, but the active
implementation scope is reduced to five compact work packages:

```text
A. Model Runtime Hardening
B. Testbench Generation and Qualification Hardening
C. Unified Bootstrap and Test Source Contract
D. Execution Identity and Reproducibility
E. Real DFS Acceptance and Closure
```

This bridge is not a new stage-sized subsystem. Existing components must be
modified first; a new top-level subsystem is allowed only after repeated real
failures prove that the current structure cannot support the requirement.

<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:BEGIN -->
## Refined budget and pricing decision

Pre-Stage-3 now distinguishes:

```text
system default
system safety ceiling
user requested value
```

Hard call/tool budgets use the system default unless the user selects a value
within the safety ceiling. Token and estimated cost are currently observed-only
soft budgets and do not stop the run. P1 owns official pricing metadata and
provenance; P2 owns budget resolution; P5 reports both effective hard limits and
actual usage.
<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:END -->

<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:BEGIN -->
## P1 audit decision ledger

The read-only model/budget/pricing consumer audit and manual review are complete.
The authoritative implementation decisions are recorded in
[`P1_MODEL_RUNTIME_AUDIT_DECISIONS.md`](P1_MODEL_RUNTIME_AUDIT_DECISIONS.md).

P1-A static model compatibility completed deterministic acceptance at `e9f4a51744ce44c04236466450b8af85ebf9be9c` with 889/889 tests. Evidence is recorded in
[`P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md`](P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md).

P1-B0 pricing/cost consumer audit is complete. Decisions and the P1-B1 boundary are recorded in
[`P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md`](P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md).

P1-B1 typed pricing/native-currency schema completed deterministic acceptance at `bb219ea9e3049b4f5959c9dbb9c0e585875afd82` with 920/920 tests. Evidence is recorded in
[`P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md`](P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md).

P1-B2 official concrete-model pricing snapshots completed deterministic acceptance at `571c51fcc250592a21bf40b3831b7dccfc6400aa` with 950/950 tests. Evidence is recorded in
[`P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md`](P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md).

P1-B3 provider-neutral usage-to-cost estimator completed deterministic acceptance with implementation commit `1c6c7efc9160c104319d4cc01a9b96c3ae0d082e`, correction commit `2296a18f09aa478afcdc5cc9652b4d9166a44149` and 993/993 final tests. Evidence is recorded in
[`P1B3_COST_ESTIMATOR_ACCEPTANCE.md`](P1B3_COST_ESTIMATOR_ACCEPTANCE.md).

P1-B4A usage normalization and shared serialization completed deterministic acceptance at `ae276f3df79685a7edd36dc6b06c7d82d5784e7a` with 1016/1016 tests. Evidence is recorded in
[`P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md`](P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md).

P1-B4B explicit estimation and native-cost accounting completed deterministic acceptance at `f650478e842e9020c23489adb407b1b50f1c4438` with 1052/1052 tests. P1-B is closed. Evidence is recorded in
[`P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md`](P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md).

P1-C1 typed effective model resolution completed deterministic acceptance at `3137a9cdbaf0201ed2ee3f5a28225121ceb04d56` with 1089/1089 tests. Evidence is recorded in
[`P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md`](P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md).

P1-B is complete and P1-C1 established the typed effective configuration foundation. The active package is P1-C2 modern consumer migration only. P1-C3 Legacy authority migration, P1-C4 parity, P1-D real-model smoke, P4, normal CLI migration, P5 output, P0 and Stage 3 remain separate later packages.
<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:END -->

## Scope control

1. Fix observed failures before hypothetical ones.
2. Prefer prompt, configuration and small qualification changes.
3. Do not build a large subsystem for one kernel-specific failure.
4. Do not create registries or schemas without a current consumer.
5. Keep retries, generation rounds and acceptance scripts bounded.
6. At closure, remove low-value acceptance scaffolding, duplicate runners,
   dead helpers and redundant logs.

The following items are deferred and do not block Stage 3:

```text
full Program Property Analyzer
full InputDomainSpec / constraint solver
GoldenRecord and deterministic harness framework
process-isolation / forkserver framework
KLEE, AFL++ and libFuzzer integration
mutation-testing framework
automatic unknown-model probing and profile persistence
automatic model routing
large test-discovery engine
full provider-response registry
```

## A. Model Runtime Hardening

Minimum scope:

```text
known-model static profile/config
artifact-appropriate reasoning defaults
larger output budget for code-generating agents
explicit model request timeout
bounded clean retry for empty/invalid output
minimal response evidence when exposed by the framework
```

The first active change is limited to the failing Testbench generation path:

```text
max_tokens = 65536
timeout = 480 seconds
```

Validate this before generalizing the defaults to all agents.

## B. Testbench Generation and Qualification Hardening

Generate one normal, complete Testbench directly. There is no preliminary
simple-Testbench stage.

The current Public/Hidden generation flow remains. Only small changes are in
scope:

```text
state-safe Testbench prompt
legal declared-size / memory-range prompt
safe delegating-stub prompt
strict artifact and signature checks
obvious constant-domain conflict check
obvious unsafe shared-state delegation check
bounded repair / coverage rounds
sanitizer diagnostic rerun only after runtime crash
```

A generated Testbench is not accepted merely because coverage is high. It must
compile, terminate normally and pass the active lightweight qualification.

If equivalent clean state cannot be established for original and `_hls`
execution, stop with a structured state-isolation-required failure instead of
inventing an unsafe reset.

## C. Unified Bootstrap and Test Source Contract

The source-only CLI remains a product goal, but this bridge wraps existing
`flow.new` and Stage 2 validation components instead of rewriting them.

Minimum recorded contract:

```text
source path/hash
kernel name
model/profile and effective parameters
target profile
test mode: provided / auto / hybrid
test provenance: provided / discovered / generated / derived
qualification result
candidate provenance
run artifact locations
final status
```

Normal output is concise. Detailed model/tool output belongs in run artifacts
and is exposed only through explicit verbose/debug modes.

## D. Execution Identity and Reproducibility

Keep minimum versions of the four approved items:

```text
Effective Target Provenance
Toolchain Fingerprint
versioned TargetProfile Registry
Cache Identity bound to the effective execution environment
```

No new database or complex inheritance system is required. Extend existing
TargetProfile, invocation evidence and cache metadata.

## E. Real DFS Acceptance and Closure

The bridge closes only after a real source-only DFS run proves:

```text
source-only input
real model-generated Testbench and candidate
qualified tests
formal Preflight
real Vitis HLS 2023.2 CSYNTH
qualified Public CSIM
qualified Hidden CSIM
accepted result
no Hidden or credential leakage
no repository source mutation
full deterministic regression
```

After success, perform a deletion/simplification audit before Stage 3:

```text
merge or remove repeated real-DFS runner versions
remove dead acceptance helpers
remove unused configuration fields and unconsumed schemas
consolidate duplicate logs where safe
keep only tests protecting meaningful compile/link/validation contracts
```

## Real DFS evidence so far

### Failure A: model/framework parameter mismatch

```text
requested reasoning_effort=max
installed AutoGen accepted=xhigh
normalization=max -> xhigh
```

This supports a small validated runtime profile for the user-selected model. It
does not justify automatic model routing.

### Failure B: generated Testbench/stub protocol

The stopped run:

```text
/data/agrefactor_runs/pre_stage3_real_dfs_end_to_end_v2_20260720_145405
```

proved:

1. a delegating stub lacked the original forward declaration despite separate
   translation-unit compilation;
2. later coverage rounds regenerated the Testbench but reused the first stub;
3. empty/malformed responses could enter C++ extraction.

Commit `53045b4cdc6c262e0be5cdcddedae0d302908812` added strict artifact
extraction, bounded format retry, declaration injection, per-round matching
stub regeneration, repeated-failure stopping, qualified-only selection and
operator-side debug artifacts.

### Failure C: unsafe shared-state delegation and empty repair output

The real DFS v3 run:

```text
/data/agrefactor_runs/pre_stage3_real_dfs_end_to_end_v3_20260720_175608
```

showed that the Testbench called the original and then a delegating stub in the
same process without restoring equivalent state. Mutable globals, heap-backed
tree/queue state and allocator state were reused, causing mismatch and heap
corruption. The following repair request produced empty terminal `content`
twice, and the bounded artifact contract stopped the run.

This justifies prompt hardening, a larger response budget, explicit request
timeout and a lightweight unsafe-delegation gate. It does not yet justify a new
process-isolation framework.

## Next execution order

```text
1. Testbench-agent output budget and request timeout
2. targeted configuration/regression validation
3. state-safe Testbench and stub prompt hardening
4. lightweight qualification gates
5. reduced-round real DFS rerun
6. unified bootstrap/identity/documentation cleanup
7. final DFS acceptance and deletion audit
```

Do not start a later step until the current step has focused evidence and no
regression.
