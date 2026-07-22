# P1-B2 Official Pricing Snapshots Acceptance

## Status

```text
package=P1-B2
status=deterministic_accepted
implementation_parent=9140abfd0a3ff52be3598e955e6be81d90937335
implementation_commit=571c51fcc250592a21bf40b3831b7dccfc6400aa
commit_subject=feat: add official model pricing snapshots
snapshot_date=2026-07-22
local_head_equals_remote_head=true
worktree_clean=true
```

P1-B2 is accepted as a static official-source, concrete-model pricing snapshot milestone. It does not implement estimation, Provider wiring, runtime migration, Legacy migration or currency conversion.

## Accepted evidence

```text
source_records=5
official_verified_sources=4
official_page_unreadable_sources=1
verified_model_snapshots=6
baseline_full_unittest=920/920
new_official_pricing_tests=30/30
p1b2_full_unittest=950/950
patch_id=d0babc3b57dbdef9370786b7e11d0cc39b93760e
```

Accepted snapshots:

```text
deepseek-v4-flash
deepseek-v4-pro
kimi-k2.6
MiniMax-M3 / standard
MiniMax-M3 / priority
qwen3-coder-plus-2025-09-23 / global
```

GLM remains `official_page_unreadable`; no numeric GLM price was inferred.

## Policy

```text
official_sources_only=true
currency_conversion=false
family_level_price=false
promotional_price_used=false
glm_numeric_price_inferred=false
```

These are historical official snapshots dated 2026-07-22, not a claim that provider prices never change. Future refreshes must add new provenance instead of silently rewriting this accepted record.

## Exact implementation change set

```text
agrefactor/models/__init__.py
agrefactor/models/official_pricing.py
agrefactor/models/pricing_sources/official_pricing_sources_20260722.json
tests/test_official_pricing.py
```

```text
4 files changed
1231 insertions
0 deletions
```

## Finding disposition

Closed at snapshot level:

```text
P1B0-F08 concrete model/deployment snapshots
P1B0-F05/F11 official snapshot half
```

Still open:

```text
P1B0-F03/F06 Provider usage categories and cost seam  P1-B3
F12 verified/unavailable/approximate estimator paths   P1-B3
P1B0-F02/F04/F09 and F15 runtime migration             P1-B4/P2
P1B0-F05/F11 Legacy authority migration                P1-C
F01 unified effective model configuration               P1-C
```

## Preserved exclusions

```text
usage-to-cost estimator
Provider/Candidate/Budget product modification
runtime/repair serialization migration
Legacy pricing/config migration
currency conversion
CLI/P5/P0/Stage 3
real model calls or Vitis
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b2_official_pricing_snapshots_20260722_184059
```

SHA-256:

```text
official_source_validation.json  4d446fbd5c1a5a38c3542481280a2a8f6aa004dc6390046403e428b0616c0969
baseline_full_unittest.log  beae75c597d22a7660892690551c6c5b717202c9c3b20128209ce96cd5b25cb7
p1b2.patch  3fa68d4248d23a13012e07f0e070ec20cf520c3290c0c083bf48f99ad7d59298
worktree_targeted_unittest.log  c247c2465489e5e718fd18c469a6b3b2a0245454ed5c43268a302a0f142ef94c
worktree_full_unittest.log  5c5335c33c058f7a3b067bed0333afcc5f942aabf77382cdbcb5fac43df58712
worktree_staged.patch  3fa68d4248d23a13012e07f0e070ec20cf520c3290c0c083bf48f99ad7d59298
main_staged.patch  3fa68d4248d23a13012e07f0e070ec20cf520c3290c0c083bf48f99ad7d59298
main_targeted_unittest.log  c247c2465489e5e718fd18c469a6b3b2a0245454ed5c43268a302a0f142ef94c
staged_stat.txt  37b0496384ddc0f8435accd58b4214a3f076f5b128a2f059402750602ee688ec
```

## Next package

```text
P1-B0 audit/design freeze             completed
P1-B1 typed pricing schema            completed
P1-B2 official model-level snapshots  completed
P1-B3 usage-to-cost estimator         active
P1-B4 compatibility migration         pending
P1-C unified effective config         pending
P1-D bounded real-model smoke         pending
```

P1-B3 remains provider-neutral: explicit `ModelPricingSnapshot` plus normalized `TokenUsage` produces typed verified, unavailable or approximate `CostEstimate` results without yet modifying Provider, Budget, Candidate serialization, Legacy or P5.
