# P4-0E Model Runtime Acceptance

## Accepted state

```text
P4_0E_MODEL_RUNTIME_IMPLEMENTED=true
P4_0E_MODEL_RUNTIME_ACCEPTANCE=accepted_real_network
P4_0E_DEFAULT_MODEL=deepseek-v4-flash
P4_0E_DEFAULT_FAMILY=deepseek
P4_0E_DEFAULT_BASE_URL=https://api.deepseek.com
P4_0E_DEFAULT_API_KEY_ENV=DEEPSEEK_API_KEY
P4_0E_REASONING_DEFAULT=auto
P4_0E_DOTENV_CWD_LOCAL=true
P4_0E_DOTENV_OVERRIDE=false
P4_0E_DEEPSEEK_THINKING=true
P4_0E_FOCUSED_TESTS=8
P4_0E_FULL_REGRESSION=2104
P4_0E_ACCEPTED_RUN_ID=p4_0e_model_runtime_v9_20260804T123830Z_3215756
P4_0E_ACCEPTED_ARTIFACT_ROOT=/data/agrefactor_runs/p4_0e_model_runtime_v9_20260804T123830Z_3215756
P4_0E_ACCEPTED_BEHAVIOR_COMMIT=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
P4_0E_REAL_NETWORK_SMOKE=accepted
P4_0E_SECRET_VALUES_PERSISTED=false
P4_0E_DOTENV_CONTENTS_PERSISTED=false
P4_0E_PRIVATE_REASONING_PERSISTED=false
P4_0E_R1_MAIN_CONTRACT_EVIDENCE_CLOSURE=accepted
P4_0E_R1_ACCEPTED_COMMIT=81804dff2c846b4f79d636cc412fca5b33eca8eb
P4_0E_REPOSITORY_CLOSURE=accepted
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0F
```

## Accepted implementation

Normal `refactor`, `optimize`, and `full` commands default to the exact fixed
model `deepseek-v4-flash`, family `deepseek`, endpoint
`https://api.deepseek.com`, and credential environment variable
`DEEPSEEK_API_KEY`. Users may explicitly override the model, family, endpoint,
and credential-variable name. There is no normal raw API-key value option.

The invocation working directory `.env` is loaded with `override=False` before
credential resolution. An already exported process variable wins; a missing
selected variable is rejected before provider launch with typed, value-free
evidence. Secret values and `.env` contents are never persisted.

Every model call has a typed role. Normal `--reasoning-effort auto` selects the
frozen project medium/high role policy. For `deepseek-v4-flash`, project medium
maps to provider `high`, project high maps to provider `max`, and Thinking is
explicitly enabled. Safe policy evidence is separate from provider-neutral
transport parameters. Provider reasoning payloads are not persisted, and final
content containing private-reasoning tags fails closed.

Legacy YAML model defaults were removed without deleting non-model YAML fields.
The legacy AG2 bridge strips its internal safe-evidence field before provider
configuration while preserving imported Python objects.

## Accepted evidence

The target-host P4-0E run proved:

```text
focused_tests=8/8
full_regression=2104/2104
real_network_smoke=passed
model=deepseek-v4-flash
thinking=true
provider_effort=max
selected_endpoint=true
selected_api_key_environment=true
committed_sample=true
exact_final_scope=true
secret_values_persisted=false
dotenv_contents_persisted=false
private_reasoning_persisted=false
```

P4-0E-R1 subsequently closed the master-contract requirements that every real
network run use one shared hard `BudgetManager` and bind exact repository and
artifact identity. See
[`p4_0e_r1_network_evidence_closure_acceptance.md`](p4_0e_r1_network_evidence_closure_acceptance.md).

## Claim boundary

The real provider response proves transport and the frozen safe configuration
contract only. It does not prove stable model quality, arbitrary-kernel success,
optimization success, or PPA superiority. P4-0F and later behavior is not
implemented here, and Stage 4 remains forbidden.
