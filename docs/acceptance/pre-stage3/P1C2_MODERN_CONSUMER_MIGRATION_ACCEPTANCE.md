# P1-C2 Modern Consumer Migration Acceptance

## Status

```text
package=P1-C2
status=deterministic_accepted
p1_c_overall_status=active
parent_commit=3f1995e62449fcb3872ce8440a802ef1361f165a
implementation_commit=4a39ed894da4d04e3d46772c7b2f5d400ed98093
implementation_subject=feat: migrate modern model consumers
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-C2 is accepted as the modern-consumer migration of P1-C. The repair-aware
production path now resolves one `EffectiveModelConfig`, and downstream modern
components consume that same immutable configuration without a second family or
parameter merge.

P1-C3 Legacy authority migration is now the only active P1 implementation
subpackage. P1-C4 parity acceptance and P1-D bounded real-model smoke remain
pending.

## Accepted modern configuration path

```text
repair-aware CLI
-> register Provider and ModelSpec
-> collect explicit reasoning override
-> ModelRegistry.resolve_effective_config() exactly once
-> CandidateModelAdapter(effective_config=...)
-> repair orchestration
-> safe effective_model_config manifest
```

The old `CandidateModelAdapter(registry, model_name, ...)` constructor remains
compatible, but delegates to the same Registry resolver. The pre-resolved path
rejects parallel model name, parameters, pricing snapshot, or approximate-cost
authority and never merges parameters a second time.

## Accepted family-instruction contract

```text
resolved instruction + empty request       -> resolved authority
resolved instruction + identical request   -> resolved authority
resolved instruction + conflicting request -> fail before validation/Provider
empty resolved + request value              -> request_compatibility
both empty                                  -> none
```

The orchestration result records the selected source and one credential-safe
`EffectiveModelConfig.to_manifest()`.

## Deterministic evidence

```text
baseline_full_unittest=1089/1089
new_tests=30
p1c2_full_unittest=1119/1119
baseline_targeted_files=8/8 passed
worktree_targeted_files=9/9 passed
main_targeted_files=9/9 passed
existing_targeted_counts_preserved=true
patch_id=01d5e3c292b82e9fb58a8c9f14b02c7a90b5a9c9
```

## Exact implementation scope

```text
agrefactor/cli.py
agrefactor/models/candidate_adapter.py
agrefactor/runtime/candidate_repair_integration.py
tests/test_modern_effective_config_migration.py
```

## P1-C finding disposition

Closed by P1-C2:

```text
P1C-F01 modern Candidate adapter had an internal resolver
P1C-F08 explicit pricing snapshot caller coverage
```

The modern path now resolves once, preserves the exact explicit pricing
snapshot identity, uses one family instruction authority and records a safe
effective configuration manifest.

Still open for P1-C3:

```text
P1C-F02 Legacy independent settings authority
P1C-F03 HLSAgentLoader DeepSeek-specific policy
P1C-F04 flow/base_agent.py duplicate pricing authority
P1C-F07 Legacy USD-only usage bridge
```

## Preserved exclusions

```text
LegacyRefactorSettings/Adapter migration
HLSAgentLoader simplification
flow/base_agent.py pricing/config removal
flow/new.py behavior changes
repair_phase.py changes
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
artifact_dir=/data/agrefactor_runs/pre_stage3_p1c2_modern_consumers_20260723_010836
```

SHA-256:

```text
audit_sha256_check.log  018208811a1a24f63d5f135fdfdde197381b2df4ae695f6fbe1298e75f69a468
baseline_full_unittest.log  a90b63e7150c0d0033b0fe3d79aceeb77288b97e0e19ac7f0a716f4985ec14fb
baseline_targeted_unittest.log  5aee652a817fd5eb2f5393ae6357e3782cf193f967abc47fb5f6acb61054576e
baseline_targeted_counts.json  a1049cdffb6b09d1e1d086fdceebe6700480db368b902175250bd45d6e417c4d
worktree_add.log  c45e33776ad3dd5c4b5a1681bca67f31b7f4c760bf2ec5d589483e5af8c6871f
p1c2.patch  b31cbf8f1e7a0304fff63035f5c108528d8bca87c56f3c98a9af01f307617004
worktree_targeted_unittest.log  06baabdfe13e424d89b232904a4ed9d6e47328d446bf1f59796a4f723782e94a
worktree_full_unittest.log  446a00444cfd69e8ca5677a9baa072255322d4564033ae31714ede78feb85a52
worktree_staged.patch  b31cbf8f1e7a0304fff63035f5c108528d8bca87c56f3c98a9af01f307617004
main_staged.patch  b31cbf8f1e7a0304fff63035f5c108528d8bca87c56f3c98a9af01f307617004
main_targeted_unittest.log  9b08110ae3e9544bbae270e70b3e5279d9e3ee1cd916faa5b52b453e285b58dc
staged_stat.txt  aea56e4f27fdb0d03c879a9d0f0d0f31f7b21f1a5de74325e37d8c1cc42272af
```

## P1-C3A acceptance linkage

Formal evidence:
[`P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md`](P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md).

P1-C3A completed deterministic acceptance at `c14650b2a474478cd82c0a9d1798fdd9b80d971b` with
**1153/1153** tests and patch ID `b5302f1d3205042b01884e9be4c4e9c0095fb380`. Typed Legacy translation and
safe manifest propagation are complete; P1-C3B Loader policy migration is
active.

## Ordered continuation

```text
P1-C unified effective configuration          active
P1-C1 typed effective model resolution        completed
P1-C2 modern consumer migration               completed
P1-C3 Legacy authority migration              active
P1-C3A typed Legacy translation               completed
P1-C3B generic AG2 loader policy migration    active
P1-C3C currency-correct Legacy usage bridge   pending
P1-C4 deterministic parity acceptance         pending
P1-D bounded real-model smoke                  pending
P4 Public/Hidden source contract               pending
```

P1-C3A completed typed translation into the Legacy flow. P1-C3B is active to
remove DeepSeek-specific Loader policy and hard-coded pricing from configuration
authority while retaining generic AG2 construction. Usage accounting remains
P1-C3C. Normal CLI, P4, P5, P0, P1-D and Stage 3 must not begin.
