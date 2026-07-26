# P1-C3B Generic AG2 Loader Policy Acceptance

## Status

```text
package=P1-C3B
status=deterministic_accepted
p1_c_overall_status=active
parent_commit=fe7b2a590541570fec1bf9767a43b10da62b91c2
implementation_commit=343d23c5b811f7c529991450b0952299f460c820
implementation_subject=refactor: make AG2 loader policy generic
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-C3B is accepted as the removal of vendor-specific configuration authority
from `HLSAgentLoader`. The Loader now performs generic, deep-copy-safe layering
and construction without selecting behavior from model or base-URL strings.

P1-C3C currency-correct Legacy usage bridge is now the only active P1
implementation subpackage. P1-C4 parity acceptance and P1-D bounded network
smoke remain pending.

## Accepted Loader contract

```text
per-agent YAML
< global YAML
< runtime typed override
```

Every layer is copied before use. Runtime dictionaries overlay all entries of a
list-form model configuration. Agents with or without a local `llm_config`
consume the same global/runtime layers. Explicit `api_type`, `price`,
`max_tokens` and `response_format` values remain explicit inputs.

The Loader does not:

```text
infer vendors from model or base_url
inject api_type
inject price
inject max_tokens
rewrite response_format by vendor
append vendor-specific output instructions
```

String `response_format` imports are resolved on plain dictionary/list values
before `LLMConfig` construction. Prebuilt `LLMConfig` objects remain atomic.

## Preserved generic responsibilities

```text
YAML loading
generic configuration layering
import resolution
ContextVariables conversion
LLMConfig construction
ConversableAgent construction
usage-agent registration
```

## Deterministic evidence

```text
baseline_full_unittest=1153/1153
new_tests=31
p1c3b_full_unittest=1184/1184
focused_loader_policy=31/31 passed
baseline_targeted_files=8/8 passed
worktree_targeted_files=9/9 passed
main_targeted_files=9/9 passed
existing_targeted_counts_preserved=true
patch_id=4e4597fb64f4dc3dab29a6b51228143586cb174c
```

## Exact implementation scope

```text
flow/base_agent.py
tests/test_hls_agent_loader_policy.py
```

## P1-C3 finding disposition

Closed by P1-C3B:

```text
P1C3-F05 HLSAgentLoader DeepSeek-specific policy
P1C3B-F01 vendor inference from model/base URL
P1C3B-F02 Loader api_type injection
P1C3B-F03 Loader price injection authority
P1C3B-F04 vendor output/response-format rewrite
P1C3B-F05 model-specific max_tokens injection
P1C3B-F07 asymmetric/in-place configuration merge
P1C3B-F10 missing direct Loader policy tests
```

Partially closed:

```text
P1C3-F06 flow.base_agent duplicate hard-coded pricing authority
```

The Loader-side price injection is removed, but
`_agrefactorpp_price_per_1k()` and its two usage fallback consumers remain
intentionally unchanged for P1-C3C.

Still open for P1-C3C:

```text
P1C3-F07 AG2 usage summary is currency-implicit
P1C3-F08 Legacy Budget bridge records only cost_usd
P1C3-F09 separate testbench-repair model selection needs the shared contract
P1C3B-F09 usage fallback retains hard-coded pricing
```

## Preserved exclusions

```text
flow.new changes
Legacy typed-translation changes
usage summary/schema/printing migration
Legacy Budget bridge changes
BudgetManager/BudgetUsage changes
testbench-repair model migration
Provider transport modification
official pricing snapshot changes
automatic pricing selection
currency conversion
normal source-only CLI
real model calls
formal C/C++ / CSIM / CSYNTH / Vitis acceptance
P4, P2, P5, P0 or Stage 3 work
```

## Artifact evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1c3b_loader_policy_20260723_022109
```

SHA-256:

```text
audit_sha256_check.log  018208811a1a24f63d5f135fdfdde197381b2df4ae695f6fbe1298e75f69a468
baseline_full_unittest.log  eb596bdeaec1e865134d942b53f4a491eb159dd8a096e224bd5c7a4f5161212f
baseline_targeted_unittest.log  87de0b5be29e26d98bd7e740f73be3ffd5bdced66755e7a79e98f1800eb79b7a
baseline_targeted_counts.json  43b9a46b0aa9ed0a09b741f3ed4853df28b4b87585bb3ac4cebb3a6e50db4d76
worktree_add.log  42f3455c2396cc285264c6e7d5c7aa158aabfa806cc6262c0fa6e611c10691d6
p1c3b.patch  b6e1b1580a885456ff291553ae2b1bff334569ea46118418858aac36b67b2c80
focused_loader_policy.log  bb300780c351223c53b9706868c4c78e269a9317738bc7d481fcc65d1fa3c70d
worktree_targeted_unittest.log  d3a15e255fd234df2b85ce0b4fa0b0600c930717fd1e84d4e6e088276866353b
worktree_full_unittest.log  0992c137f83719dbff7034816c8322332b6d746d23ce8fccc1f48745500134db
worktree_staged.patch  b6e1b1580a885456ff291553ae2b1bff334569ea46118418858aac36b67b2c80
main_staged.patch  b6e1b1580a885456ff291553ae2b1bff334569ea46118418858aac36b67b2c80
main_targeted_unittest.log  bb1c3d7025fad723bd29dcd8f32b501d58710309797fb0a7c85eb52d8fff46b0
staged_stat.txt  79a8944af19e4daba85f4c4d3efe34214b9c3c18341ce41f359037a183582f42
```

## P1-C3C1 acceptance linkage

Formal evidence:
[`P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md`](P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md).

P1-C3C1 completed deterministic acceptance at `d2f085b3cabefef87e8aa5099bdb1c2a8ce32b7d` with
**1220/1220** tests and patch ID `f5ecbba1271868d84d1ad5b8482c50926a013c6f`. Currency-unknown framework
amounts are quarantined from USD/native ledgers; P1-C3C2 is active.

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

P1-C3C1 completed the typed AG2 summary and removed hard-coded usage pricing.
P1-C3C2 is active to translate the summary into validated `TokenUsage`, estimate
only from the explicit accepted pricing snapshot, and record native currency
through the existing Budget ledger. Repair-model construction remains P1-C3C3;
Loader policy, normal CLI, P1-C4, P1-D and Stage 3 must not change.
