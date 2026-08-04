# P4-0E-R1 Network Budget and Identity Closure Acceptance

## Accepted state

```text
P4_0E_R1_NETWORK_EVIDENCE_CLOSURE=accepted
P4_0E_R1_PARENT_COMMIT=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
P4_0E_R1_ACCEPTED_COMMIT=81804dff2c846b4f79d636cc412fca5b33eca8eb
P4_0E_R1_ACCEPTED_RUN_ID=p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
P4_0E_R1_ACCEPTED_ARTIFACT_ROOT=/data/agrefactor_runs/p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
P4_0E_R1_FOCUSED_TESTS=4
P4_0E_R1_FULL_REGRESSION=2108
P4_0E_R1_REPOSITORY_CLEAN=true
P4_0E_R1_REPOSITORY_BRANCH=stage2-general-feedback
P4_0E_R1_SHARED_BUDGET_MANAGER=true
P4_0E_R1_MAX_LLM_CALLS=1
P4_0E_R1_PROSPECTIVE_LLM_CHECK=true
P4_0E_R1_PHYSICAL_PROVIDER_CALLS=1
P4_0E_R1_LLM_CALLS_AFTER=1
P4_0E_R1_EXACT_ONCE_LLM_ACCOUNTING=true
P4_0E_R1_REAL_NETWORK_SMOKE=passed
P4_0E_R1_ARTIFACT_IDENTITY_SHA256=db6d4996d71ba2a6bfe99beb804f7ad1826684ff2b737220933f17061e2b7c2d
P4_0E_R1_ARTIFACT_FILE_SHA256=0211f48cb908bf3bb76ec3edd3c7465828b320fb4da211debd7e0c08e40d31c3
P4_0E_R1_SECRET_VALUES_PERSISTED=false
P4_0E_R1_DOTENV_CONTENTS_PERSISTED=false
P4_0E_R1_PRIVATE_REASONING_PERSISTED=false
P4_0E_R1_RAW_PROVIDER_ERROR_PERSISTED=false
P4_0E_R1_HIDDEN_EXPOSED_TO_MODEL=false
P4_0F_BEHAVIOR_CHANGED=false
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0F
```

## Accepted correction

The authoritative committed smoke ran only from a clean
`stage2-general-feedback` checkout at `81804dff2c846b4f79d636cc412fca5b33eca8eb`. It proved the sample was tracked,
created one shared `BudgetManager(max_llm_calls=1)`, performed a prospective
check before provider launch, consumed exactly once immediately before the
physical provider attempt, and recorded observed token/cost usage without
incrementing the LLM-call count again.

The evidence binds the exact repository commit, branch, run ID, artifact root,
package-manifest identity, committed sample identity, model/endpoint/API-key
environment name, safe call policy, and the artifact identity hash. The
repository identity was rechecked after provider execution.

No credential value, `.env` content, raw provider error, raw private reasoning,
or Hidden source/detail entered persisted evidence or model-visible inputs.

## Claim boundary

This correction changes only the evidence harness. It does not change model
runtime behavior, mode-specific budget defaults, Full reserves, CLI visibility,
Vitis behavior, optimizer behavior, or Stage 4 eligibility. The network response
is not evidence of stable model quality, arbitrary-kernel support, optimization
success, or PPA superiority.
