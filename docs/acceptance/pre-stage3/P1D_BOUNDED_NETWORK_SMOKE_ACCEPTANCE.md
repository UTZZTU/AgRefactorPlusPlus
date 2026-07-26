> **Scope correction:** This remains valid concrete-model network evidence for
> `deepseek-v4-flash`. It does not by itself close the broader frozen P1
> Profile-schema contract. The family profile is now
> `deterministically_tested`; `network_smoke_verified` remains attached to the
> exact concrete-model evidence only. See
> [`P1_P4_FROZEN_CONTRACT_RECONCILIATION.md`](../../audits/P1_P4_FROZEN_CONTRACT_RECONCILIATION.md).

# P1-D Bounded DeepSeek Network Smoke Acceptance

## Status

```text
p1_d_status=network_smoke_verified
p1_overall_status=completed
p4_status=active
repository_head=70c802270ed2af60e7af360e526d57b49b728528
concrete_model=deepseek-v4-flash
provider=deepseek
base_url=https://api.deepseek.com
api_key_env=DEEPSEEK_API_KEY
model_api_call_limit=1
model_api_call_count=1
second_call_blocked=true
full_deterministic_regression=1275/1275
model_api_called=true
vitis_run=false
stage3_started=false
```

P1-D is accepted for the exact concrete model `deepseek-v4-flash` against the official
DeepSeek OpenAI-compatible endpoint. This does not promote every model in the
DeepSeek family; the family profile records deterministic contract verification,
while this concrete model receives `network_smoke_verified` evidence.

## Accepted chain

```text
EffectiveModelConfig
-> DeepSeek family profile
-> exact official pricing snapshot
-> OpenAICompatibleProvider
-> one real Chat Completions request
-> normalized TokenUsage
-> native CNY CostEstimate
-> BudgetManager.record_model_usage
-> prospective second call blocked by max_llm_calls=1
```

## Observed evidence

```text
prompt_tokens=42
completion_tokens=9
total_tokens=51
cost_currency=CNY
cost_amount=0.00006
cost_quality=verified
cost_is_invoice=false
budget_llm_calls=1
budget_cost_usd=0.0
budget_costs_by_currency={"CNY": "0.00006"}
credential_persisted=false
tool_calls=0
compile_calls=0
csim_calls=0
csynth_calls=0
vitis_run=false
```

Cost is an estimate based on the accepted exact-model pricing snapshot, not an
invoice. No currency conversion was performed.

## Evidence boundary

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1d_deepseek_smoke_20260723_142950
smoke_artifact=/data/agrefactor_runs/pre_stage3_p1d_deepseek_smoke_20260723_142950/p1d_network_smoke.json
network_log=/data/agrefactor_runs/pre_stage3_p1d_deepseek_smoke_20260723_142950/network_smoke.log
targeted_tests=/data/agrefactor_runs/pre_stage3_p1d_deepseek_smoke_20260723_142950/targeted_unittest.log
full_regression=/data/agrefactor_runs/pre_stage3_p1d_deepseek_smoke_20260723_142950/full_unittest.log
```

Artifacts retain the credential environment-variable name only. The secret
value is neither printed nor serialized, and the artifact directory passed a
value-level credential scan.

## P1 closure

```text
P1-A static compatibility                    completed
P1-B pricing and native cost                 completed
P1-C unified runtime configuration           completed
P1-D bounded concrete-model network smoke    completed
P1 overall                                   completed
```

The next active package is P4 Public/Hidden test-source contract and
provenance. P2, Execution Identity, P5, P0 and Stage 3 remain ordered later
packages.
