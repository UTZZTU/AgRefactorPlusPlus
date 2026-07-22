# P1-B4A Usage Normalization and Serialization Acceptance

## Status

```text
package=P1-B4A
status=deterministic_accepted
parent_commit=4e9353f81c6c284a32f514811de61f0067045cbb
implementation_commit=ae276f3df79685a7edd36dc6b06c7d82d5784e7a
implementation_subject=feat: normalize and serialize model usage
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-B4A is accepted as the first compatibility-migration subpackage. It
normalizes observed Provider usage breakdowns and establishes one shared,
backwards-compatible safe serialization shape for `TokenUsage` and
`ModelResponse`.

P1-B4B completed deterministic acceptance at
`f650478e842e9020c23489adb407b1b50f1c4438` with **1052/1052** tests and patch ID
`5360788b724a9c6d6fcebff107943436efb8a510`. P1-B4 is now complete.

## Accepted code contract

`TokenUsage.to_dict()` preserves the legacy keys:

```text
prompt_tokens
completion_tokens
total_tokens
cost_usd
```

and appends:

```text
breakdown
estimated_cost
```

`ModelResponse.to_dict()` is the shared response serializer used by:

```text
CandidateModelResult.to_dict()
Repair model_response_to_safe_dict()
```

No Candidate or Repair serializer maintains a second hand-written usage shape.

## Accepted Provider normalization

The OpenAI-compatible transport accepts both object and mapping responses and
normalizes observed aliases for:

```text
prompt_tokens / input_tokens
completion_tokens / output_tokens
prompt_cache_hit_tokens
prompt_cache_miss_tokens
cached_tokens
prompt_tokens_details.cached_tokens
input_tokens_details.cached_tokens
cache_read_input_tokens
cache_creation_input_tokens
completion_tokens_details.reasoning_tokens
output_tokens_details.reasoning_tokens
```

Rules:

```text
missing categories remain None
observed zero remains zero
conflicting aliases fail explicitly
cache hit + cache miss must equal prompt total when both are present
a single observed cache partition may derive its complement
reasoning tokens may not exceed completion tokens
negative and boolean token counts are rejected
```

Provider metadata records only category presence:

```text
usage_breakdown_observed
usage_breakdown_categories
```

It does not expose private reasoning content.

## Compatibility policy

```text
old TokenUsage constructors remain valid
old cost_usd JSON key remains present
breakdown=None when the Provider exposes no category data
estimated_cost=None because estimation is not attempted in P1-B4A
Candidate and Repair response JSON shapes are identical
```

## Deterministic evidence

```text
baseline_full_unittest=993/993
new_tests=23
p1b4a_full_unittest=1016/1016
targeted_files=7/7 passed
main_targeted_files=7/7 passed
patch_id=89db552f6660c8e5fa9ac2a67deb21909ae25ae3
```

Targeted counts:

```text
usage_compatibility_migration=23
openai_compatible_provider=7
candidate_model_adapter=24
repair_protocol=33
model_pricing=31
cost_estimator=43
package_imports=6
```

## Exact implementation scope

```text
agrefactor/models/base.py
agrefactor/models/candidate_adapter.py
agrefactor/models/openai_compatible.py
agrefactor/repair/protocol.py
tests/test_usage_compatibility_migration.py
```

## Finding disposition

Closed at P1-B4A:

```text
P1B0-F04 Candidate/repair usage serialization migration
Provider token-category normalization portion of P1B0-F03/F06
```

Partially closed:

```text
P1B0-F03 normalized usage now carries cache/thinking categories;
          explicit pricing estimation wiring remains P1-B4B
P1B0-F09 response/runtime serialization seam is migrated;
          Budget/runner native-currency state remains P1-B4B
F15 estimated_cost can be serialized without abusing cost_usd;
    run-level native-currency accounting remains P1-B4B
```

Still open:

```text
P1B0-F02 Budget USD-only state/limit                 P1-B4B/P2
P1B0-F05/F11 Legacy pricing authority migration      P1-C
F01 unified reasoning/effective config               P1-C
```

## Preserved exclusions

```text
estimate_model_cost invocation
automatic pricing snapshot selection
TokenUsage mutation after Provider return
BudgetManager/BudgetUsage modification
run-level native-currency aggregation
runtime runner modification
Legacy pricing/config migration
normal CLI or P5
currency conversion
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b4a_usage_migration_20260722_211632
```

SHA-256:

```text
baseline_full_unittest.log  d57022db48bafaadb6eaca222962e01f04df2262a5cb4d14bff9b0f80c9abd23
worktree_add.log  30f0578a577621da495617d0ddc7e974af0234412bca0384ab4434f1db8ae9e5
p1b4a.patch  5afb6d2cb48286da89252c57d1817caacb46022063f02de7b61c3c23b7389d4e
worktree_targeted_unittest.log  52f810f6eeb9d8d9a2ece673b9dfa2bb038b2f3828e8e655029884e020b0e1d6
worktree_full_unittest.log  4e437cb74788c7993dfa4f022786c9618783aa50263dd37c556bbd52a3e3a651
worktree_staged.patch  5afb6d2cb48286da89252c57d1817caacb46022063f02de7b61c3c23b7389d4e
main_staged.patch  5afb6d2cb48286da89252c57d1817caacb46022063f02de7b61c3c23b7389d4e
main_targeted_unittest.log  af5cc11ee0f2f7d398797b802530e2e87be099c5f939ecc7610bebf0b3a8a5bd
staged_stat.txt  2a4dd78fdd5f1bd2d7a5f0895a4292dbd94343ce7144981a8e0b9ef3d7224bbd
```

## P1-B4B and P1-B4 closure linkage

Formal evidence:
[`P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md`](P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md).

P1-B4B connects an explicit pricing snapshot to the accepted estimator,
preserves the exact snapshot identity, records observed native-currency costs
through the single BudgetManager/BudgetUsage ledger, and migrates all known
BudgetUsage serializers. P1-B4A plus P1-B4B close P1-B4.

## Ordered continuation

```text
P1-B4 compatibility migration                 completed
P1-B4A usage normalization and serialization  completed
P1-B4B estimation and native-cost accounting  completed
P1-C unified effective config                 active
P1-D bounded real-model smoke                 pending
```

P1-B4B satisfied the explicit-snapshot, exact-identity, single-ledger and
observed-only requirements without adding automatic selection, FX conversion or
native-currency hard limits. P1-C is now active for unified effective
configuration and Legacy authority migration.
