# P1-B3 Provider-Neutral Cost Estimator Acceptance

## Status

```text
package=P1-B3
status=deterministic_accepted
base_commit=2d9487cdedd8f15c811ef256a6a28909988438a5
implementation_commit=1c6c7efc9160c104319d4cc01a9b96c3ae0d082e
implementation_subject=feat: add provider-neutral cost estimator
correction_commit=2296a18f09aa478afcdc5cc9652b4d9166a44149
correction_subject=fix: deduplicate model package exports
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-B3 is accepted as a provider-neutral, explicit-snapshot usage-to-cost
estimation milestone. The final accepted code baseline includes the estimator
implementation commit and one minimal export-list correction commit.

It does not implement automatic snapshot selection, Provider response wiring,
runtime serialization migration, Budget native-currency aggregation, Legacy
migration or currency conversion.

## Accepted public contract

```python
estimate_model_cost(
    snapshot: ModelPricingSnapshot,
    usage: TokenUsage,
    *,
    allow_approximate: bool = False,
) -> CostEstimate
```

Inputs are explicit and immutable:

```text
explicit ModelPricingSnapshot
normalized TokenUsage
```

Output quality has three typed paths:

```text
VERIFIED
UNAVAILABLE
APPROXIMATE
```

No model family, Provider or endpoint lookup occurs inside the estimator.

## Accepted token semantics

```text
prompt_tokens     authoritative total input tokens
completion_tokens authoritative total output tokens
breakdown fields  optional partitions of those totals
```

Supported priced categories:

```text
input
output
cache_hit_input
cache_miss_input
cache_read
cache_write
thinking_output
```

The estimator prevents double charging:

```text
normal input = prompt_tokens - cache_read_tokens
non-thinking output = completion_tokens - thinking_output_tokens
```

Input-tier matching follows the accepted pricing schema:

```text
input_token_min_exclusive < prompt_tokens
prompt_tokens <= input_token_max_inclusive
```

## Quality policy

### VERIFIED

Requires an `official_verified` snapshot, one unique matching rate per
non-zero priced category, and enough observed breakdown information to
partition totals without assumptions.

### UNAVAILABLE

Returned by default when required categories, partitions, prices or snapshot
quality are insufficient. It carries no amount and records sorted
`unpriced_token_categories`.

### APPROXIMATE

Only possible when `allow_approximate=True` and every assumption is explicit.
Accepted assumptions include:

```text
cache_hit_input_tokens=0;cache_miss_input_tokens=prompt_tokens
cache_read_tokens=0
cache_write_tokens=0
thinking_output_tokens=0
pricing_snapshot_status=stale
```

Unknown, unpublished or unreadable snapshots remain unavailable even when
approximation is enabled.

## Native-currency and compatibility policy

```text
currency is preserved from the pricing snapshot
no FX conversion
TokenUsage.cost_usd is ignored by the estimator
input TokenUsage is not mutated
output pricing_snapshot_sha256 equals the exact input snapshot identity
```

## Evidence sequence

```text
P1-B2 baseline 950/950
-> detached validation worktree
-> 42 estimator tests
-> P1-B3 implementation regression 992/992
-> exact implementation patch ID
-> main-path compatibility tests
-> implementation commit
-> GitHub export-list review
-> minimal duplicate-export correction
-> one export uniqueness regression test
-> final regression 993/993
-> exact correction patch ID
-> correction commit and push
```

## Deterministic evidence

```text
p1b2_baseline=950/950
estimator_tests_added=42
implementation_full_unittest=992/992
export_fix_tests_added=1
final_p1b3_tests_in_file=43
final_full_unittest=993/993
implementation_targeted_files=5/5 passed
implementation_main_targeted_files=5/5 passed
correction_targeted_files=2/2 passed
correction_main_targeted_files=2/2 passed
implementation_patch_id=588353a2ff2107ad9a64c488e54715de9360af1f
correction_patch_id=91e17d224f49b8ee63c9999b24234776fcf70829
```

## Exact code change set

Implementation commit:

```text
agrefactor/models/__init__.py
agrefactor/models/cost_estimator.py
tests/test_cost_estimator.py
```

Correction commit:

```text
agrefactor/models/__init__.py
tests/test_cost_estimator.py
```

Final combined P1-B3 delta from `2d9487cdedd8f15c811ef256a6a28909988438a5`:

```text
agrefactor/models/__init__.py
agrefactor/models/cost_estimator.py
tests/test_cost_estimator.py
```

Final export invariant:

```text
len(agrefactor.models.__all__)
==
len(set(agrefactor.models.__all__))

estimate_model_cost exported exactly once
find_official_model_pricing_snapshots exported exactly once
```

## Finding disposition

Closed at P1-B3:

```text
F12 verified/unavailable/approximate estimator paths
```

Partially closed:

```text
P1B0-F03 provider-neutral estimator seam exists;
          Provider normalization/wiring remains P1-B4
P1B0-F06 cache/thinking pricing semantics are handled;
          Provider usage breakdown parsing remains P1-B4
F15 native-currency estimation is correct;
    runtime/Budget compatibility migration remains P1-B4
```

Still open:

```text
P1B0-F02 Budget USD-only state/limit                 P1-B4/P2
P1B0-F04 Candidate/repair serialization migration   P1-B4
P1B0-F05/F11 Legacy pricing authority migration      P1-C
P1B0-F09 wider runtime migration surface             P1-B4/P1-C
F01 unified reasoning/effective config               P1-C
```

## Documentation amendment

During P1-B3 acceptance review, the P1-B2 evidence block in
`P1_MODEL_RUNTIME_AUDIT_DECISIONS.md` was found to contain an accidental
status-line substitution in the `new_tests` field. This acceptance commit
restores the immutable P1-B2 fact:

```text
new_tests=30
```

The P1-B2 code, tests, artifact hashes and acceptance result are unchanged.

## Preserved exclusions

```text
automatic pricing snapshot selection
OpenAICompatibleProvider modification
provider-specific token breakdown parsing
TokenUsage mutation
Candidate/repair serialization migration
BudgetManager/BudgetUsage native-currency migration
Legacy pricing/config migration
normal CLI or P5
currency conversion
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
```

## Artifact evidence

Implementation artifact:

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b3_cost_estimator_20260722_191325
```

SHA-256:

```text
baseline_full_unittest.log  1f2482ea67cc29230d012d84e31a1d1017b8ed505d472cfa6a22cd7abbd7c1c9
worktree_add.log  036c3120d1ec7a84a1cb8bcd02900b329126a99e4fe95a754bb5511193cb36ca
p1b3.patch  7c31b7a5bed9a6ed17b004106fb0ce2259aac0917b200418f77f2fecf95f0a42
worktree_targeted_unittest.log  554bca506fec52103dba652436b6228f2c80e91d13bc532673a8730256a5bd3d
worktree_full_unittest.log  624b241a861ada16383b0debf8c2a22de3c24565ebbd8f9854d7fd5b0709bae5
worktree_staged.patch  7c31b7a5bed9a6ed17b004106fb0ce2259aac0917b200418f77f2fecf95f0a42
main_staged.patch  7c31b7a5bed9a6ed17b004106fb0ce2259aac0917b200418f77f2fecf95f0a42
main_targeted_unittest.log  554bca506fec52103dba652436b6228f2c80e91d13bc532673a8730256a5bd3d
staged_stat.txt  6bd4563b99ca8c1f95184104d5e246d3f968aad638180ce5e15e2ffaee95f869
```

Correction artifact:

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b3_export_fix_20260722_193040
```

SHA-256:

```text
baseline_full_unittest.log  f52a2f183f1abda668788933af7345359136b3010eb335b5638dc28e120c8838
worktree_add.log  fd52c0a04f6d5c1c4068ab01f5a2a29146a767761fa06cf0571bee85f768e596
export_fix.patch  c9fc34d03bc11f9636323af839ee08a37b010738c26e38c6d4a475337a2f04c2
worktree_targeted_unittest.log  a83220ed9381af3426f7ce54ab6cb6b6efe90987216dec333ad3e2af67d35083
worktree_full_unittest.log  42c847628e730819ac9dc3b1a542a251c5f2f3b92cd2252e32748b2039bca5db
worktree_staged.patch  c9fc34d03bc11f9636323af839ee08a37b010738c26e38c6d4a475337a2f04c2
main_staged.patch  c9fc34d03bc11f9636323af839ee08a37b010738c26e38c6d4a475337a2f04c2
main_targeted_unittest.log  cf4e7745b438ffc76fae480fced12c46bd9922dd9de793a02c0184b7ff881b24
staged_stat.txt  b9726680781b7c2f536fc6eac779007d8a5b34d135b7b800f5a838ddc5075b79
```

## Next package

```text
P1-B0 audit/design freeze             completed
P1-B1 typed pricing schema            completed
P1-B2 official model-level snapshots  completed
P1-B3 usage-to-cost estimator         completed
P1-B4 compatibility migration         active
P1-C unified effective config         pending
P1-D bounded real-model smoke         pending
```

P1-B4 must connect normalized observed usage and explicit pricing identity to
the accepted estimator while preserving old `cost_usd` compatibility. It may
migrate Provider usage breakdowns, Candidate/repair serialization and
run-level native-currency accounting, but must not perform Legacy effective
configuration migration, normal CLI/P5 work, FX conversion or Stage 3 work.
