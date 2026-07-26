# P1-C3A Typed Legacy Translation Acceptance

## Status

```text
package=P1-C3A
status=deterministic_accepted
p1_c_overall_status=active
parent_commit=e73d0999e6fa6425831c55aca6af215834101883
implementation_commit=c14650b2a474478cd82c0a9d1798fdd9b80d971b
implementation_subject=feat: translate effective config to legacy flow
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-C3A is accepted as the typed Legacy translation foundation. The existing
Legacy compatibility path may now consume one accepted `EffectiveModelConfig`,
translate it into a generic AG2 runtime override, and propagate a safe manifest
without re-resolving model parameters inside `flow.new`.

P1-C3B generic AG2 loader policy migration is now the only active P1
implementation subpackage. P1-C3C currency-correct Legacy usage, P1-C4 parity
acceptance and P1-D bounded network smoke remain pending.

## Accepted Legacy selection contract

```text
explicit model + explicit model family
-> typed EffectiveModelConfig resolution

model present + family absent
-> raw Legacy model/reasoning/base-URL compatibility path

model absent
-> existing YAML/raw Legacy compatibility path
```

No family is inferred from model strings or base URLs. Explicit unknown families
still fail. Matching old `model` and `base_url` values remain compatibility
views; conflicting values and parallel `reasoning_effort` authority are
rejected.

## Accepted translation contract

```text
EffectiveModelConfig
-> concrete model ID
-> generic provider-to-AG2 api_type mapping
-> base URL identity
-> detached effective parameters
-> family instruction
-> credential-safe manifest
-> model_configuration_source
```

Reserved identity or credential keys cannot enter the parameter payload.

`flow.new.resolve_runtime_llm_config()` uses a deep copy of the translated
override when present. When absent, the old `make_llm_config()` path remains
callable for bounded compatibility.

## Deterministic evidence

```text
baseline_full_unittest=1119/1119
new_tests=34
p1c3a_full_unittest=1153/1153
baseline_targeted_files=8/8 passed
worktree_targeted_files=9/9 passed
main_targeted_files=9/9 passed
existing_targeted_counts_preserved=true
patch_id=b5302f1d3205042b01884e9be4c4e9c0095fb380
```

## Exact implementation scope

```text
agrefactor/cli.py
agrefactor/compat/legacy_refactor.py
flow/new.py
tests/test_legacy_effective_config_translation.py
```

## P1-C3 audit finding disposition

Closed or partially closed by P1-C3A:

```text
P1C3-F01 LegacyRefactorSettings independent model configuration authority
P1C3-F02 raw Legacy kwargs lacked one resolved manifest
P1C3-F03 existing Legacy CLI constructed raw settings
P1C3-F04 flow.new had a second resolver
P1C3-F10 Legacy traces lacked the accepted config manifest
```

The raw no-family compatibility path deliberately remains until P1-C4 parity
acceptance. P1-C3A therefore removes typed-path duplication without deleting
the compatibility surface.

Still open for P1-C3B:

```text
P1C3-F05 HLSAgentLoader DeepSeek-specific policy
P1C3-F06 flow.base_agent duplicate hard-coded pricing authority
```

Still open for P1-C3C:

```text
P1C3-F07 AG2 usage summary is currency-implicit
P1C3-F08 Legacy Budget bridge records only cost_usd
P1C3-F09 separate testbench-repair model selection needs the shared contract
```

## Preserved exclusions

```text
HLSAgentLoader policy changes
flow/base_agent.py changes
hard-coded price removal
Legacy usage/cost accounting migration
BudgetManager/BudgetUsage changes
testbench-repair factory migration
Provider transport modification
automatic pricing selection
normal source-only CLI
currency conversion
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
P4, P2, P5, P0 or Stage 3 work
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1c3a_legacy_translation_20260723_014847
```

SHA-256:

```text
audit_sha256_check.log  018208811a1a24f63d5f135fdfdde197381b2df4ae695f6fbe1298e75f69a468
baseline_full_unittest.log  00573d7a9b8927a33af90e8976bf06f604d8d7be99ecac07618ad9e97ae89c99
baseline_targeted_unittest.log  46c2fdb7eb451e70a3fcef2d0a1729355b6e1c0a405844f807c01fabcf82596d
baseline_targeted_counts.json  11ffe0392a1a6fd6ae8429c2307769d0d39e6cd69b2098a2ebc1c95ae4bb5716
worktree_add.log  546379a6d346e5804e1ab410e3bce6d499fe4c664558cfc49f314d915fffbe93
p1c3a.patch  bf074fade2e9d1bd559493c8fe0a0854f3748d460eafd53becf4fd901e601038
worktree_targeted_unittest.log  02a6451657c9e8b964b5202f195250dd1b45104e1bda170c89fa18db6a56bee0
worktree_full_unittest.log  20f80a53088f8154ca6a1d88ee778348fb9098f54c6366edff9b225c79e20547
worktree_staged.patch  bf074fade2e9d1bd559493c8fe0a0854f3748d460eafd53becf4fd901e601038
main_staged.patch  bf074fade2e9d1bd559493c8fe0a0854f3748d460eafd53becf4fd901e601038
main_targeted_unittest.log  f1a7c93064e6f7aaccf996f9bae88c870b891f1471da2066f6312899399d2645
staged_stat.txt  c00d79cb5bed8281134af8547884c2a8de2142a87299dd612bbe4e7d40a44f9e
```

## P1-C3B acceptance linkage

Formal evidence:
[`P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md`](P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md).

P1-C3B completed deterministic acceptance at `343d23c5b811f7c529991450b0952299f460c820` with
**1184/1184** tests and patch ID `4e4597fb64f4dc3dab29a6b51228143586cb174c`. Vendor-specific Loader
authority is removed; P1-C3C currency-correct usage migration is active.

## Ordered continuation

```text
P1-C unified effective configuration          active
P1-C1 typed effective model resolution        completed
P1-C2 modern consumer migration               completed
P1-C3 Legacy authority migration              active
P1-C3A typed Legacy translation               completed
P1-C3B generic AG2 loader policy migration    completed
P1-C3C currency-correct Legacy usage bridge   active
P1-C4 deterministic parity acceptance         pending
P1-D bounded real-model smoke                  pending
P4 Public/Hidden source contract               pending
```

P1-C3B completed the generic Loader policy migration. P1-C3C is active to
replace currency-implicit Legacy usage and the remaining hard-coded usage-price
fallback with typed token/cost provenance and the existing native-currency
Budget ledger. Loader policy, normal CLI, P1-C4, P1-D and Stage 3 must not
change in that subpackage.
