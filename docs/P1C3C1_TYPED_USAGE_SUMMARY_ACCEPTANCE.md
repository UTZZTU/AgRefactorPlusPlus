# P1-C3C1 Typed AG2 Usage Summary Acceptance

## Status

```text
package=P1-C3C1
status=deterministic_accepted
p1_c_overall_status=active
parent_commit=4cf848dea1e54128011ca63d67ec6f88e300e8a1
implementation_commit=d2f085b3cabefef87e8aa5099bdb1c2a8ce32b7d
implementation_subject=feat: add currency-safe AG2 usage provenance
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-C3C1 is accepted as the currency-safe AG2 usage-summary and provenance
foundation. Framework-reported amounts with no explicit currency are retained
for audit only and are never relabeled as USD or entered into the native ledger.

P1-C3C2 Legacy native-currency Budget bridge is now the only active P1
implementation subpackage. P1-C3C3 repair-model configuration/accounting,
P1-C4 parity acceptance and P1-D bounded network smoke remain pending.

## Accepted provenance contract

```text
framework_reported_cost:
  kind: framework_reported | unavailable
  amount: decimal text | null
  currency: null
  quality: reported_unverified_currency | unavailable
  source: explicit collection source
  ledger_eligible: false
  complete: boolean
  assumptions: [...]
```

A reported zero is represented as `amount="0"` and is distinct from unavailable
cost. Missing, invalid, negative or boolean cost values remain unavailable.

The following compatibility fields do not carry currency-unknown amounts:

```text
model.cost = null
model.cost_usd = null
summary.total_cost = null
summary.cost_usd = null
costs_by_currency = {}
estimated_cost = null
cost_complete = false
```

## Removed authority

```text
_agrefactorpp_price_per_1k
hard-coded DeepSeek usage rates
local cost synthesis when AG2 omits cost
hard-coded dollar printing
```

No model name or numeric amount implies currency.

## Preserved behavior

```text
usage-agent registration
aggregate AG2 collection
per-agent fallback collection
token aggregation
model breakdown
JSON-safe summary
human-readable compatibility printing
HLSAgentLoader policy
```

## Deterministic evidence

```text
baseline_full_unittest=1184/1184
new_tests=36
p1c3c1_full_unittest=1220/1220
focused_usage_summary=36/36 passed
baseline_targeted_files=9/9 passed
worktree_targeted_files=10/10 passed
main_targeted_files=10/10 passed
existing_targeted_counts_preserved=true
patch_id=f5ecbba1271868d84d1ad5b8482c50926a013c6f
```

## Exact implementation scope

```text
flow/base_agent.py
tests/test_hls_agent_loader_policy.py
tests/test_legacy_usage_summary.py
```

The Loader test change replaces only the expired P1-C3B assertion that the
usage price helper must remain. Loader behavior and its 31-test count are
unchanged.

## P1-C3C finding disposition

Closed by P1-C3C1:

```text
P1C3C-F01 hard-coded DeepSeek usage-price helper
P1C3C-F02 synthetic cost when AG2 omits cost
P1C3C-F03 currency-implicit cost fields in AG2 summary
P1C3C-F04 unavailable cost collapsed to numeric zero
P1C3C-F05 hard-coded USD human-readable output
P1C3B-F09 usage fallback retains hard-coded pricing
```

Partially closed:

```text
P1C3C-F09 framework-reported versus typed-estimate provenance
```

Framework-reported provenance now exists. Exact typed snapshot estimation and
native-ledger recording remain P1-C3C2.

Still open for P1-C3C2:

```text
P1C3C-F06 Legacy normalization relabels unknown amounts as USD
P1C3C-F07 Legacy Adapter bypasses TokenUsage/native ledger
P1C3C-F08 Legacy typed pricing identity is unused for usage
P1C3C-F09 typed snapshot estimate/native provenance completion
```

Still open for P1-C3C3:

```text
P1C3C-F10 repair aggregation is cost_usd-only
P1C3C-F11 repair factory infers family from model strings
P1C3C-F12 repairer has a second config merge and no cost enrichment
P1C3C-F13 flow.new selects repair model through a raw constructor
```

## Preserved exclusions

```text
Legacy Adapter normalization or Budget recording
BudgetManager/BudgetUsage changes
typed snapshot estimation
testbench-repair effective config/accounting
HLSAgentLoader policy
flow.new changes
Provider transport
official pricing snapshot changes
automatic pricing selection
currency conversion
normal source-only CLI
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
P4, P2, P5, P0, P1-C4, P1-D or Stage 3
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1c3c1_usage_summary_20260723_025521
```

SHA-256:

```text
audit_sha256_check.log  018208811a1a24f63d5f135fdfdde197381b2df4ae695f6fbe1298e75f69a468
baseline_full_unittest.log  63a5d2ebd6552554a5f757c4ca8db9f66121eb8e4add49aa57f94f28a7100756
baseline_targeted_unittest.log  f61451bd4e1586575d34fddb1e18b60ccec503bef2640d2c1f0e19fdd5174091
baseline_targeted_counts.json  65c020aa89e70ec5f808b88186db38158f7d4118e11aeca31ca2c9f843f18f5f
worktree_add.log  94fcf2e5f543a622c6feb0ff63ae9812a32f2b556211789717f01e60dde68cf9
usage_section.py  f558ffdae583aae52bf79697188f31d9af3b5ac736a61784ca1b28cffa7bd833
p1c3c1.patch  de8f0b8e5b002b8043c1a3ddeec99f03d1eeb219f9264e2a8335a2edd0574c9d
focused_usage_summary.log  e6566b87c01f4db00cf88e28a43718283eb0cfdc215840d4fbc8e32858ed6417
worktree_targeted_unittest.log  ab50bcffcde50c4761ca20dbad2b11284f330ec19b99e3da6f8d1c8c9b4b580f
worktree_full_unittest.log  236de23533eb304f55a5d6e4352942467ce1c9942e90e6dd502d31c56bc13774
worktree_staged.patch  de8f0b8e5b002b8043c1a3ddeec99f03d1eeb219f9264e2a8335a2edd0574c9d
main_staged.patch  de8f0b8e5b002b8043c1a3ddeec99f03d1eeb219f9264e2a8335a2edd0574c9d
main_targeted_unittest.log  28e7ebcf5ba0cd9a97c808a49f0d5399192980d4d645d68d0110ec2cd814fcc1
staged_stat.txt  c1e93314f1a02e12a4e9353cd045b2e2bad30bd9562d69eaff9e71a33c6143b1
```

## Ordered continuation

```text
P1-C unified effective configuration          active
P1-C1 typed effective model resolution        completed
P1-C2 modern consumer migration               completed
P1-C3 Legacy authority migration              active
P1-C3A typed Legacy translation               completed
P1-C3B generic AG2 loader policy migration    completed
P1-C3C currency-correct Legacy usage bridge   active
P1-C3C1 typed AG2 usage summary               completed
P1-C3C2 Legacy native-currency Budget bridge  active
P1-C3C3 repair config/accounting              pending
P1-C4 deterministic parity acceptance         pending
P1-D bounded real-model smoke                  pending
P4 Public/Hidden source contract               pending
```

P1-C3C2 must consume this typed summary, estimate cost only from the exact
pricing snapshot already accepted in `EffectiveModelConfig`, construct validated
`TokenUsage`, record through `BudgetManager.record_model_usage()`, and populate
`cost_usd` only for actual USD. It must not modify Loader policy, repair-model
construction, Provider transport, normal CLI, P1-C4, P1-D or Stage 3.
