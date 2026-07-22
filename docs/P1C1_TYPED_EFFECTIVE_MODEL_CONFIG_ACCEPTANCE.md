# P1-C1 Typed Effective Model Configuration Acceptance

## Status

```text
package=P1-C1
status=deterministic_accepted
p1_c_overall_status=active
parent_commit=8fab046d3e705fb40db189984a0f51389b5b94d3
implementation_commit=3137a9cdbaf0201ed2ee3f5a28225121ceb04d56
implementation_subject=feat: add effective model configuration
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-C1 is accepted as the typed-resolution foundation of P1-C. It establishes
one immutable effective model configuration before Provider execution.

P1-C2 modern consumer migration is now the only active P1 implementation
subpackage. P1-C3 Legacy authority migration, P1-C4 parity acceptance and P1-D
bounded real-model smoke remain pending.

## Accepted type contract

`EffectiveModelConfig` records:

```text
logical model name
registered provider name
concrete API model ID
requested family name
canonical ModelFamilyProfile
base URL identity
API-key environment-variable name, never its value
deeply immutable effective request parameters
optional explicit ModelPricingSnapshot
exact pricing snapshot SHA-256
explicit allow_approximate_cost flag
```

The safe manifest is JSON serializable and does not read or expose credential
values. Credential-like keys are rejected from effective parameters.

## Accepted resolution contract

`ModelRegistry.resolve_effective_config()`:

```text
resolve_with_profile()
-> preserve strict unknown model/provider/family behavior
-> ModelFamilyProfile.merge_parameters()
-> family defaults < model defaults < explicit call overrides
-> existing alias/reject/reasoning policy exactly once
-> immutable EffectiveModelConfig
```

Absent family continues to use the neutral profile. An explicitly unknown
family continues to fail before Provider execution.

The resolver never invokes a Provider and never discovers or auto-selects
official pricing. A pricing snapshot, when present, is supplied explicitly and
must match the concrete API model ID.

## Deterministic evidence

```text
baseline_full_unittest=1052/1052
new_tests=37
p1c1_full_unittest=1089/1089
baseline_targeted_files=7/7 passed
worktree_targeted_files=8/8 passed
main_targeted_files=8/8 passed
existing_targeted_counts_preserved=true
patch_id=4a37e161da17664a073761837ce944ea7eff749d
```

## Exact implementation scope

```text
agrefactor/models/__init__.py
agrefactor/models/effective_config.py
agrefactor/models/registry.py
tests/test_effective_model_config.py
```

## P1-C audit finding disposition

Closed at the typed-foundation level:

```text
P1C-F05 registry/family primitives form the authoritative static seam
P1C-F06 canonical typed EffectiveModelConfig did not exist
P1C-F09 strict explicit-family behavior must remain unchanged
```

Partially closed:

```text
P1C-F01 modern Candidate adapter has an internal resolver
P1C-F08 explicit pricing snapshot caller coverage
```

They remain open until P1-C2 migrates modern consumers to this contract.

Still open:

```text
P1C-F02 Legacy independent settings authority
P1C-F03 HLSAgentLoader DeepSeek-specific policy
P1C-F04 flow/base_agent.py duplicate pricing authority
P1C-F07 Legacy USD-only usage bridge
```

They remain assigned to P1-C3.

## Preserved exclusions

```text
CandidateModelAdapter migration
repair-aware factory migration
normal source-only CLI
LegacyRefactorSettings/Adapter migration
HLSAgentLoader simplification
flow/base_agent.py pricing removal
Provider transport modification
automatic pricing selection
currency conversion
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
P4, P2, P5, P0 or Stage 3 work
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1c1_effective_config_20260723_002252
```

SHA-256:

```text
audit_sha256_check.log  018208811a1a24f63d5f135fdfdde197381b2df4ae695f6fbe1298e75f69a468
baseline_full_unittest.log  a5d1f1ea1a9929e95631f4af3ff0523dc2187308e0f6c221f7b2a6c8dc034214
baseline_targeted_unittest.log  bf5c368b8708c4639c71dece5356728dbb2c452c6849c13ebe17045bda4d042c
baseline_targeted_counts.json  f05b3d907c8ebe8b142a957228e442ea7d07c7edbd78cf40daeb5615026d397c
worktree_add.log  064a46cd94e2f37a220f3ae8c36572b59a7529e83b83dfa02e9d30550cd7ec3d
p1c1.patch  e9d2f73a35ccac7262eb00eafbd8b24f119aa72a1fd6872fb7cd55f276a3a5bc
worktree_targeted_unittest.log  d17a285490cc247e2f808973c1d20ddfc0697076d7fe6155660310e24542d23e
worktree_full_unittest.log  499f13b894029781cccc92b29396af4d310104cbd93d63f55eb5aebcb8eef83d
worktree_staged.patch  e9d2f73a35ccac7262eb00eafbd8b24f119aa72a1fd6872fb7cd55f276a3a5bc
main_staged.patch  e9d2f73a35ccac7262eb00eafbd8b24f119aa72a1fd6872fb7cd55f276a3a5bc
main_targeted_unittest.log  35e1e49c9d327b0f73ff1286c19679266591fa0a5c88a722498fe77c37a221f1
staged_stat.txt  86168307ac55397712d66c1dd6ed500437047054be9e6ad6c1fcf56ce0c609f1
```

## Ordered continuation

```text
P1-C unified effective configuration          active
P1-C1 typed effective model resolution        completed
P1-C2 modern consumer migration               active
P1-C3 Legacy authority migration              pending
P1-C4 deterministic parity acceptance         pending
P1-D bounded real-model smoke                  pending
P4 Public/Hidden source contract               pending
```

P1-C2 must make the modern Candidate/repair-aware path consume one
`EffectiveModelConfig` instead of independently resolving ModelSpec,
ModelFamilyProfile, effective parameters and pricing options. It must preserve
the old constructor during bounded migration, preserve the exact pricing
snapshot identity, and fail invalid family/reasoning configuration before a
Provider call. P1-C2 must not modify Legacy, normal CLI, P4, P5, P0 or Stage 3.
