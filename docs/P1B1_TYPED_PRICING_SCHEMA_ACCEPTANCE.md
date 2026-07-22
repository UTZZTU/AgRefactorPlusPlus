# P1-B1 Typed Pricing Schema Acceptance

## Status

```text
package=P1-B1
status=deterministic_accepted
implementation_parent=dbf1378b2515181ab4de984ba1a8d5f520d4e6f6
implementation_commit=bb219ea9e3049b4f5959c9dbb9c0e585875afd82
commit_subject=feat: add typed pricing and cost schema
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-B1 is accepted as a deterministic type and compatibility milestone. It does
not contain official numeric price tables and does not implement cost
estimation or runtime consumer migration.

## Accepted types

```text
PricingVerificationStatus
CostEstimationQuality
PricingApplicability
PricingRate
ModelPricingSnapshot
TokenUsageBreakdown
CostEstimate
```

## Accepted semantics

```text
Decimal rates and amounts reject negative, NaN and infinity
currency normalizes to an uppercase three-letter code
source_content_sha256 and pricing_snapshot_sha256 are separate
canonical pricing hash is deterministic and semantic
VERIFIED/APPROXIMATE require amount, currency and snapshot identity
UNAVAILABLE has no amount and records unpriced categories
missing cache/thinking token categories remain None
old TokenUsage positional and keyword constructors remain valid
non-USD estimated cost requires cost_usd=None
```

`TokenUsage` compatibility order remains:

```text
prompt_tokens
completion_tokens
cost_usd
breakdown
estimated_cost
```

## Evidence strategy

```text
889/889 clean baseline
-> detached validation worktree
-> 31 typed-pricing tests
-> 920/920 full deterministic regression
-> exact stable patch-id
-> main-path compatibility tests
-> compileall recovery with explicit status evidence
-> commit and push
```

The initial evidence script incorrectly required quiet `compileall -q` output
to be non-empty. A zero-byte output file is normal on success. Recovery reused
the already validated 920/920 regression and exact tested patch, reran
compileall with an explicit status record, reran all five main-path
compatibility suites, and then committed the unchanged patch.

## Deterministic evidence

```text
baseline_full_unittest=889/889
new_pricing_tests=31/31
p1b1_full_unittest=920/920
test_delta=+31
worktree_targeted_files=5/5 passed
main_targeted_files_before_recovery=5/5 passed
main_targeted_files_after_recovery=5/5 passed
compileall_recovery=passed
full_regression_reused=true
full_regression_rerun=false
patch_id=c793e3d1402bf63977e7a25d3ce829d46416fab2
```

Targeted compatibility files:

```text
tests/test_model_pricing.py              31 tests
tests/test_openai_compatible_provider.py  7 tests
tests/test_candidate_model_adapter.py    24 tests
tests/test_budget_manager.py              7 tests
tests/test_repair_protocol.py            33 tests
```

## Exact implementation change set

```text
agrefactor/models/__init__.py
agrefactor/models/base.py
agrefactor/models/pricing.py
tests/test_model_pricing.py
```

Commit statistics:

```text
4 files changed
1099 insertions
0 deletions
```

## P1-B0 finding disposition

```text
P1B0-F01 closed for typed TokenUsage native-currency structure
P1B0-F06 partially closed: optional token categories exist; Provider parsing remains P1-B3
P1B0-F07 closed for typed provenance and stable semantic hash
P1B0-F10 closed: source and semantic hashes are distinct; test path corrected
```

Still open:

```text
P1B0-F02 Budget USD-only state/limit                 P1-B4/P2
P1B0-F03 Provider usage normalization/cost seam      P1-B3
P1B0-F04 Candidate serialization migration          P1-B4
P1B0-F05 Legacy second pricing source                P1-B2/P1-C
P1B0-F08 concrete model/deployment snapshots         P1-B2
P1B0-F09 wider runtime migration surface             P1-B4/P1-C
F01 unified reasoning/effective config               P1-C
F11 official snapshot and Legacy pricing migration   P1-B2/P1-C
F12 estimator verified/unavailable/approximate paths P1-B3
F15 runtime native-currency migration                P1-B4
```

## Preserved exclusions

```text
official numeric prices
cost estimator
OpenAICompatibleProvider product modification
CandidateModelAdapter product modification
BudgetManager modification
runtime/repair serialization migration
Legacy pricing/config migration
flow tool modification
currency conversion
CLI or P5
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b1_typed_pricing_schema_20260722_161517
```

SHA-256:

```text
baseline.txt  1ad6f6e85eb2ab9528715b23c9c0d4ded11c6213a9cfc4e11e37ada52840c6cb
import_preflight.log  5cd9a293e97324e5fd0e3d69b4fc83b0ec78d3bbdd20ca592fa28007e7a89cf7
baseline_full_unittest.log  cd37fbd9217c3c9fac9257d7b0095cc070e49e3d8a172a0c6361bb3c10d05dd5
p1b1.patch  0c546d5ba01c0da34184d5e23431741e2639e8fe64636edd560f263fc42104dc
worktree_targeted_unittest.log  ee1e206bc974689b6746500cf43b8604470439186f218386d2811772edfd0de8
worktree_full_unittest.log  388f2df66aef48eb0309c7e3448b13150789f317e3a75f900d75b9b4467af904
worktree_staged.patch  0c546d5ba01c0da34184d5e23431741e2639e8fe64636edd560f263fc42104dc
main_staged.patch  0c546d5ba01c0da34184d5e23431741e2639e8fe64636edd560f263fc42104dc
main_targeted_unittest.log  882ac24b45033077b1f29a95fdab198e367184a7770fb946fa6068ef4b3edc88
recovery_main_staged.patch  0c546d5ba01c0da34184d5e23431741e2639e8fe64636edd560f263fc42104dc
compileall_recovery_status.txt  70f0a13af372c0f0754d767acd234dabdbbf72b05309d8e4f3ffd181354ae051
main_targeted_unittest_recovery.log  edfe4fc5e27fa35539981324701c9b880907c090e1262ce790b82cef9a2efbd1
staged_stat_recovery.txt  801f80e494e2f59620b2859a83c4fd1a2e7bd47489c84b81d193263cbc7e004f
```

## Next package

```text
P1-B0 audit/design freeze             completed
P1-B1 typed pricing schema            completed
P1-B2 official model-level snapshots  active
P1-B3 usage-to-cost estimator         pending
P1-B4 compatibility migration         pending
P1-C unified effective config         pending
P1-D bounded real-model smoke         pending
```

P1-B2 must use only official model-provider sources, retain original source
content hashes and retrieval metadata, create concrete model-level snapshots,
and represent unreadable or unpublished prices without guessing.
