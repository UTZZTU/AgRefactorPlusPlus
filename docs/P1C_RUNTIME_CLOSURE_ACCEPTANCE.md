# P1-C Runtime Closure Acceptance

## Status

```text
p1_c_status=completed
p1_c3c2_status=deterministic_accepted
p1_c4_status=deterministic_accepted
p1_d_status=active
implementation_commit=f0c06c32771916bb6ad3bd68eb4ac21473dcd41b
implementation_patch_id=6f77f6146e64a341623ac9e21a591f5a7e4cd7bd
p1c3c2_baseline=1220
p1c3c2_new_tests=30
p1c3c2_full=1250
p1c4_parity_tests=25
p1c_full_closure=1275
p1c4_parity_patch_id=731cfd6429c15e544af7b1b21a4e04853bf20c6d
model_api_called=false
vitis_run=false
stage3_started=false
```

P1-C is closed. Modern Candidate, Legacy main usage and provider-backed
testbench repair now consume one authoritative `EffectiveModelConfig`, use the
same exact pricing snapshot semantics, produce compatible `TokenUsage`, and
record through the same native-currency `BudgetManager` ledger.

## P1-C3C2 accepted behavior

```text
typed AG2 summary -> TokenUsage
exact model identity -> explicit pricing snapshot estimate
USD -> native USD ledger + cost_usd compatibility view
non-USD -> native ledger only
unknown framework currency -> audit only
repair calls -> exact-once hard call accounting
repair responses -> native usage/cost enrichment
live repair Budget records -> never replayed post hoc
legacy repair artifacts -> bounded deduplicated fallback
```

Exact implementation files:

```text
agrefactor/compat/legacy_refactor.py
agrefactor/testing/factory.py
agrefactor/testing/model_testbench_repairer.py
flow/new.py
flow/tools/general.py
tests/test_integrated_legacy_usage_closure.py
tests/test_legacy_refactor_adapter.py
```

## P1-C4 parity proof

The parity suite verifies:

```text
same EffectiveModelConfig identity
same model identity and effective parameters
same family profile
same pricing snapshot hash
same USD and non-USD CostEstimate
same cost_usd compatibility rule
same Budget native-currency ledger
same no-snapshot behavior
same exact-model attribution rule
same repair inheritance/dedicated-config rules
no active model-string family inference
vendor-neutral Loader
Provider remains transport-only
unknown framework currency remains outside the ledger
```

## Evidence

```text
p1c3c2_artifact_dir=/data/agrefactor_runs/pre_stage3_p1c3c2_integrated_20260723_133753
p1c_closure_artifact_dir=/data/agrefactor_runs/pre_stage3_p1c_runtime_closure_20260723_140635
focused_parity=25/25
full_closure=1275/1275
```

## Remaining ordered work

```text
P1-D bounded real-model smoke          active
P4 Public/Hidden source contract       pending
P2 source-only bootstrap               pending
Execution Identity                     pending
P5 concise output                      pending
P0 source-only real DFS acceptance     pending
Stage 3                                not started
```

P1-D is one bounded network smoke for one explicitly selected concrete model.
It must use the accepted typed config and hard LLM-call limit, record observed
usage/cost provenance, make no Vitis call, and never expose credentials.
