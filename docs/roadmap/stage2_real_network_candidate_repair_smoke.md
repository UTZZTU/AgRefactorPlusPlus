# Stage 2.7.5 Real Network-model Candidate Repair Smoke

## Status

```text
code_baseline=7407da78b9371e853b44a201828ce4b9251fad8f
B05_SATISFIED=true
network_model_executed=true
model_call_count=1
model_response_observed=true
real_tool_executed=true
hidden_leakage=false
optimizer_executed=false
```

## Fixed model and endpoint

```text
requested_model=deepseek-v4-flash
response_model=deepseek-v4-flash
base_url=https://api.deepseek.com
api_key_environment=OPENAI_API_KEY
model_selection=user_fixed
```

The credential value remained environment-only and was scanned against the run
artifacts, CLI captures and logs. The value is not stored in this document.

## Deterministic trigger

The smoke used a minimal C++ candidate with a deterministic syntax error. A real g++
Preflight consumed the shared compile/tool budget, produced candidate-owned feedback
and routed exactly once to Candidate repair.

## Real model observation

```text
prompt_tokens=937
completion_tokens=169
total_tokens=1106
finish_reason=stop
response_contract_outcome=accepted
attempt_status=validation_failed
attempt_changed=True
```

A provider-only exception is not accepted as B-05 completion. This acceptance required
a non-null model response and non-zero token usage.

## Bounded repair outcome

```text
orchestration_status=validation_terminal
repair_stop_reason=terminal_feedback
repair_terminal_status=terminal
accepted=false
outcome=可信 terminal failure（validation_failed）
```

Successful repair is not a Stage 2 closing requirement. A strict-contract rejection or
real post-response validation failure is acceptable when the response, usage, budget,
terminal state and artifacts are trustworthy.

## Real local validation

```text
tool_calls=2
compile_calls=2
csynth_calls=0
csim_calls=0
preflight_invocations=2
csynth_invocations=0
csim_invocations=0
```

The initial candidate failure is always backed by a real Preflight invocation. A
contract-accepted changed proposal re-enters validation from Preflight; CSYNTH and CSIM
counts depend on how far that real proposal progressed.

## Hidden boundary

The TaskSpec included a Hidden suite containing a unique sentinel. The sentinel was
searched across all agent-safe run/phase/repair artifacts and CLI capture files and was
not present. Operator work directories remain separate from agent-safe artifacts.

## Artifacts

```text
/data/agrefactor_runs/stage2_7_5_real_network_candidate_repair_20260719_211334/acceptance
```

Key files:

```text
stage2_real_network_candidate_repair_summary.json
formal_run/run_result.json
formal_run/run_artifact_manifest.json
formal_run/refactor/orchestration_result.json
formal_run/refactor/artifact_manifest.json
formal_run/refactor/repair_artifacts/repair_run.json
formal_run/refactor/repair_artifacts/attempts/attempt_001.json
formal_run/refactor/repair_artifacts/artifact_manifest.json
```

## Scope boundary

This is one model, endpoint, host, Vitis profile and small kernel smoke. It does not
prove model accuracy, arbitrary-kernel repair, statistical reliability, automatic
model routing, multi-version Vitis support or Stage 3 optimization.

Next:

```text
Stage 2.7.6 Evidence-gated Contract/Parser Delta + Ground-truth Revalidation
```
