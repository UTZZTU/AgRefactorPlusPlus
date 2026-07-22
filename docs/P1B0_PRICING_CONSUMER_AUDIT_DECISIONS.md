# P1-B0 Pricing Consumer Audit Decisions

## Status

```text
package=P1-B0
status=completed
audit_head=24918d6fcfe1250043cd6a72082456241fa4679e
repository_modified=false
model_api_called=false
vitis_run=false
next_active_package=P1-B1
```

P1-B0 is a read-only architecture and provenance audit. It freezes the type
boundary for P1-B1 but does not approve official numeric prices for code.

## Evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1b0_pricing_consumer_audit_20260722_154815
tracked_files=461
text_files_scanned=270
pricing_usage_occurrences=972
cost_pricing_occurrences=190
automated_findings=8
official_sources=6
official_snapshots_retrieved=5
official_pages_unreadable=1
```

SHA-256:

```text
report.md  843cdd3b5aa9617142a175f6794015d903a51f3294cc43f88051113e69338671
audit.json  ae484040343279caa57b4666062c1524904b24f600c77dd4e64f396c7be6971a
official_pricing_sources.json  6cb70ec7d3789a440a8927bb93ceb7e9c482a43eba85ddff119d2f088f4b8609
proposed_schema.md  ac6b33135fc2ffedd34dfd1d5e81c64a0321e63b159613cad3b427c525f806f6
cost_usd_consumers.tsv  7b448ad778fbd85c38629105558265bb8bbd709a5186b013969aef830574fd1a
usage_flow.tsv  64e71650c4b78b5edca71faab36ac28e574363101cac763f14a5e45522f41bf1
```

## Automated findings

```text
P1B0-F01 TokenUsage is USD-only                         -> P1-B1
P1B0-F02 BudgetManager has USD-only state/limit         -> P1-B4/P2
P1B0-F03 Provider returns normalized usage, no cost     -> P1-B3
P1B0-F04 Candidate evidence serializes cost_usd         -> P1-B4
P1B0-F05 Legacy base_agent is a second price source     -> P1-B2/P1-C
P1B0-F06 Cache token categories are not represented     -> P1-B1/P1-B3
P1B0-F07 No typed pricing provenance or stable hash     -> P1-B1
P1B0-F08 Billing dimensions are not family-wide         -> P1-B2
```

## Manual review amendments

### P1B0-F09 — Runtime migration surface is wider than the summary flow

The automated classification grouped many product consumers under
`other_consumer`. The compatibility migration surface includes at least:

```text
agrefactor/repair/protocol.py
agrefactor/repair/candidate_loop.py
agrefactor/runtime/runner.py
agrefactor/evaluation/testbench_preflight.py
agrefactor/compat/legacy_refactor.py
agrefactor/smoke/stage2_matrix.py
agrefactor/smoke/stage2_fault_matrix.py
agrefactor/smoke/stage2_pass_matrix.py
flow/tools/csim.py
flow/tools/csynth.py
flow/tools/general.py
```

Decision:

```text
P1-B1 must be constructor- and serialization-compatible.
P1-B1 does not migrate these consumers.
P1-B4 owns formal serialization/runtime migration.
P1-C owns Legacy pricing/config parity.
```

### P1B0-F10 — Proposed schema needs two identity hashes and a corrected test path

The draft used one `snapshot_sha256` for two different identities:

```text
raw official source content
normalized canonical pricing object
```

These must be separate:

```text
source_content_sha256
pricing_snapshot_sha256
```

The draft also named `tests/test_model_api.py`, which does not exist in the
current repository. P1-B1 uses a new `tests/test_model_pricing.py`; the existing
`tests/test_openai_compatible_provider.py` remains a compatibility regression
consumer and does not require a Provider product-code change.

## Official source disposition

```text
deepseek | deepseek_pricing_zh | official_snapshot_retrieved | http=200 | sha256=de70ea3a08da1e0ad1c5859779b99b6c2eb95b530146bed351b473abb89c88c8
kimi | kimi_pricing_overview | official_snapshot_retrieved | http=200 | sha256=36c924328e1a1ca34a05e7d1f69c0667c3ec463822f1b96d396128729739d075
kimi | kimi_k26_pricing | official_snapshot_retrieved | http=200 | sha256=3f0db1e9743ab0f15f7e2520912a229eed4ec62bd37c0f7e0f3606d0b0902a40
minimax | minimax_paygo | official_snapshot_retrieved | http=200 | sha256=1d187c925e4bf608acde607b5bbf1076221c2a9a7ab25bcdf9dd6e90cc6bc21c
qwen | qwen_model_pricing | official_snapshot_retrieved | http=200 | sha256=61e5aef7451196d49f0411ffdbe61e31c20da51c261f402d5bf29eb47735e67d
glm | glm_pricing | official_page_unreadable | http=200 | sha256=c988273f16307238bfd94e0c9fe8d4531a3193b88a53724c3c5f436bd3443994
```

Five official pages were captured. The GLM page was reachable but not
machine-readable. This is not permission to infer or copy a non-official price.

## Frozen P1-B1 types

P1-B1 defines types and validation only:

```text
PricingVerificationStatus
CostEstimationQuality
PricingApplicability
PricingRate
ModelPricingSnapshot
TokenUsageBreakdown
CostEstimate
```

### PricingApplicability

It must be able to represent material billing selectors without a
family-wide shortcut:

```text
region
deployment_scope
billing_mode
thinking_mode
cache_mode
service_tier
```

### Hash identities

```text
source_content_sha256
= hash of the retained official page/source bytes

pricing_snapshot_sha256
= stable canonical hash of normalized pricing metadata and rates
  excluding the hash field itself
```

### Token usage compatibility

`TokenUsage` keeps:

```text
prompt_tokens
completion_tokens
cost_usd
total_tokens
```

and gains optional fields through `TokenUsageBreakdown` plus optional
`estimated_cost`.

Rules:

```text
missing provider categories remain None, not zero
None estimated_cost means estimation not attempted
UNAVAILABLE means estimation was attempted but could not price the usage
non-USD estimated cost requires cost_usd=None
old TokenUsage constructors remain valid
old Candidate/repair/runtime serialization remains valid in P1-B1
```

## Exact P1-B1 code scope

Allowed:

```text
agrefactor/models/pricing.py                 new
agrefactor/models/base.py                    compatibility extension
agrefactor/models/__init__.py                typed exports
tests/test_model_pricing.py                  new
```

Regression-only consumers, not product modifications:

```text
tests/test_openai_compatible_provider.py
tests/test_candidate_model_adapter.py
tests/test_budget_manager.py
tests/test_repair_protocol.py
```

Explicitly forbidden in P1-B1:

```text
official numeric price tables
agrefactor/models/openai_compatible.py
agrefactor/models/candidate_adapter.py
agrefactor/runtime/budget.py
agrefactor/repair/protocol.py
agrefactor/repair/candidate_loop.py
agrefactor/runtime/runner.py
agrefactor/compat/legacy_refactor.py
flow/base_agent.py
flow/tools/*
CLI
P5 output
currency conversion
real model calls
Vitis
```

## P1-B1 acceptance

1. Decimal rates and amounts reject negative, NaN and infinity.
2. Currency is normalized to an uppercase three-letter code.
3. Verification and estimation status values are typed.
4. Canonical pricing hash is deterministic and changes when semantic pricing
   metadata changes.
5. Raw source hash and canonical pricing hash are distinct fields.
6. VERIFIED and APPROXIMATE estimates require amount, currency and pricing
   snapshot identity.
7. UNAVAILABLE estimates have no amount and record missing/unpriced categories.
8. Non-USD estimated cost cannot coexist with a populated `cost_usd`.
9. Existing `TokenUsage(prompt_tokens=..., completion_tokens=...,
   cost_usd=...)` construction remains valid.
10. Missing cache/thinking categories remain `None`.
11. No official numeric prices or estimator are introduced.
12. Full deterministic regression passes.

## P1-B1 acceptance linkage

P1-B1 completed deterministic acceptance at `bb219ea9e3049b4f5959c9dbb9c0e585875afd82` with
**920/920** tests and stable patch ID `c793e3d1402bf63977e7a25d3ce829d46416fab2`.

Evidence:
[`P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md`](P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md).

The automated-schema issues recorded as P1B0-F01, F07 and F10 are closed at
the typed-schema level. P1B0-F06 remains partially open until P1-B3 parses
provider-specific token categories.

## Ordered continuation

```text
P1-B0 read-only audit and design freeze       completed
P1-B1 typed pricing/native-currency schema    completed
P1-B2 official model-level snapshots          active
P1-B3 usage-to-cost estimator                 pending
P1-B4 serialization and compatibility migration pending
P1-C unified Stage-2/Legacy effective config  pending
P1-D bounded real-model smoke                 pending
```
