# P1-B4B Native Cost Accounting Acceptance

## Status

```text
package=P1-B4B
status=deterministic_accepted
p1_b4_overall_status=completed
parent_commit=149e8aaf86da9185f50cc017f676299fa2f55eb2
implementation_commit=f650478e842e9020c23489adb407b1b50f1c4438
implementation_subject=feat: connect native model cost accounting
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-B4B is accepted as the second and final P1-B4 compatibility-migration
subpackage. Together with P1-B4A, it completes P1-B4.

P1-C unified effective configuration is now the only active P1 implementation
package. P1-D remains pending and no real model smoke has been performed here.

## Accepted estimation wiring

`CandidateModelAdapter` accepts an optional explicit
`ModelPricingSnapshot` and an explicit `allow_approximate_cost` flag.

```text
no snapshot
-> existing behavior remains unchanged

explicit snapshot
-> snapshot.model_id must equal ModelSpec.model
-> estimate_model_cost(snapshot, normalized TokenUsage)
-> immutable ModelResponse receives estimated_cost
-> exact pricing_snapshot_sha256 is preserved
-> callback and repair accounting observe the enriched response
```

The adapter does not discover or auto-select a snapshot by provider, family or
model name.

## Accepted native-currency accounting

`BudgetManager/BudgetUsage` remains the single run-level usage and cost source
of truth.

```text
internal cost state = one Decimal ledger keyed by currency
costs_by_currency["USD"] = authoritative USD entry when present
cost_usd = backwards-compatible view derived from the USD ledger
non-USD amount is never written into cost_usd
no FX conversion
```

`BudgetManager.record_model_usage(TokenUsage)` records total observed tokens
and any available verified or explicitly allowed approximate estimate after the
provider call. Unavailable estimates do not invent an amount.

Token and estimated cost remain observed-only soft budgets. P1-B4B does not
introduce native-currency hard limits. The existing `max_cost_usd` compatibility
limit continues to apply only to the USD ledger.

## Accepted serialization contract

The shared JSON-safe native-cost shape is used by:

```text
RunResult
CandidateRepairAttempt / CandidateRepairLoopResult
CandidateRepairOrchestrationResult
RepairObservedUsage
Stage2 smoke fault matrix
Stage2 smoke pass matrix
```

All Budget-related snapshots use `BudgetUsage.to_dict()` or the corresponding
typed `to_dict()` method. Repository-level AST validation rejects remaining
`asdict()` calls on budget usage, budget-before/after, total usage, observed
usage or budget delta values.

## Deterministic evidence

```text
baseline_full_unittest=1016/1016
new_tests=36
p1b4b_full_unittest=1052/1052
baseline_targeted_files=13/13 passed
worktree_targeted_files=14/14 passed
main_targeted_files=14/14 passed
existing_targeted_counts_preserved=true
patch_id=5360788b724a9c6d6fcebff107943436efb8a510
```

## Exact implementation scope

```text
agrefactor/models/candidate_adapter.py
agrefactor/repair/candidate_loop.py
agrefactor/repair/protocol.py
agrefactor/runtime/budget.py
agrefactor/runtime/candidate_repair_integration.py
agrefactor/runtime/runner.py
agrefactor/smoke/stage2_fault_matrix.py
agrefactor/smoke/stage2_pass_matrix.py
tests/test_native_cost_accounting.py
```

## P1-B finding disposition

Closed by P1-B4A plus P1-B4B:

```text
P1B0-F03 normalized Provider usage reaches explicit estimation
P1B0-F04 Candidate/repair usage serialization migration
P1B0-F06 cache/thinking categories and runtime pricing seam
P1B0-F09 runtime serialization/accounting migration surface
F12 formal Provider-to-estimator runtime connection
F15 native-currency run accounting with cost_usd compatibility
```

Still open outside P1-B:

```text
F01 authoritative reasoning/effective configuration       P1-C
F05 DeepSeek-specific Legacy compatibility logic           P1-C
F06/F11 Legacy pricing as a second authority               P1-C
P1-D bounded real-model smoke                              P1-D
```

## Preserved exclusions

```text
automatic pricing snapshot selection
native-currency hard limit configuration
currency conversion
Provider usage-normalization modification
official pricing snapshot modification
Legacy effective-config migration
normal source-only CLI
P5 concise output
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
P0 or Stage 3 work
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b4b_native_cost_accounting_20260722_225712
```

SHA-256:

```text
baseline_full_unittest.log  4d3d8e7ad7cb3cf4fea60389a3f27a0f5a4af446da30613dbddab1fb8cd32641
baseline_targeted_unittest.log  eba6ed7669e942aa3e289815567095232c65604449c504ba7d1a4dce7905666d
baseline_targeted_counts.json  bd84720f2e92c9f1fd6dda9d7c6d0cddce43be0b59c0e0c5e797a90e2679de7b
worktree_add.log  f9d6a38c519b17da25a3af3a515a2f2eb40b662558418ea0e585cc6334f2f6a5
p1b4b.patch  294470778fdd3167008310120becad0e66d1fff4c76e7acb1b779f11439a4c32
worktree_targeted_unittest.log  c2bfe622954fef279266a9b5c53282368516a457d2c807f144f67e5e367df066
worktree_full_unittest.log  4d9943078bdbb11572a6bbf566fedac3ee04410f4e134169eca772b52cf7e40c
worktree_staged.patch  294470778fdd3167008310120becad0e66d1fff4c76e7acb1b779f11439a4c32
main_staged.patch  294470778fdd3167008310120becad0e66d1fff4c76e7acb1b779f11439a4c32
main_targeted_unittest.log  1bd698fbd9f46a04c58f675a94014250897c6c5fb2aa4777e16395b95dda669d
staged_stat.txt  1a8f49dedafb63354ff0ccd11f850449ad49c55ea57e7df1c324799fe114a952
```

## P1-C1 acceptance linkage

Formal evidence:
[`P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md`](P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md).

P1-C1 completed deterministic acceptance at `3137a9cdbaf0201ed2ee3f5a28225121ceb04d56` with
**1089/1089** tests and patch ID `4a37e161da17664a073761837ce944ea7eff749d`. The typed foundation is
complete; P1-C2 modern consumer migration is active.

## Ordered continuation

```text
P1-B compatibility and pricing runtime        completed
P1-B4A usage normalization/serialization      completed
P1-B4B estimation/native accounting           completed
P1-C unified effective configuration          active
P1-C1 typed effective model resolution        completed
P1-C2 modern consumer migration               active
P1-C3 Legacy authority migration              pending
P1-C4 deterministic parity acceptance         pending
P1-D bounded real-model smoke                  pending
P4 Public/Hidden source contract               pending
```

P1-C1 established the immutable typed resolution foundation. P1-C2 is now
active to migrate the modern Candidate and repair-aware consumers. P1-C3 will
later migrate Legacy/AG2 authority, and P1-C4 will close deterministic parity.
P1-C must not begin normal source-only CLI, P5, P4, P0 or Stage 3 work.
