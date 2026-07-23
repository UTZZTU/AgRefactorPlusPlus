# Execution Identity and Reproducibility Acceptance

## Authority and scope

The sole implementation authority is
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md),
Step 4. This package does not implement P5, run P0, close Pre-Stage-3, or start
Stage 3.

## Prerequisite authority audit

```text
P1=complete
P4=complete
P2=complete
```

P1 has static family profiles, provider-launch validation, persisted effective
non-sensitive configuration, official pricing identity and the bounded real
`deepseek-v4-flash` network smoke. P4 has independent Public/Hidden selection,
multiple suites, full provenance, generated-source qualification and Hidden
isolation. P2 has the normal source-only CLI, internal TaskSpec/bootstrap,
Stage-2 formal adjudication and one shared pre-call run budget.

## Accepted identity contract

```text
run id
source path/hash and explicit top
normalized TaskSpec and hash
model/profile/provider and effective non-sensitive parameters
pricing snapshot/hash/status/currency
prompt-contract hashes
effective TargetProfile and per-field provenance
observed Vitis executable/version fingerprint from real CSYNTH evidence
Public/Hidden suite hashes and provenance
initial/final Candidate hashes
budget defaults/ceilings/requested/effective/usage/remaining/exhaustion
artifact schema version
repository commit/clean-state identity
request/cache identity and execution identity
```

The operator-full bundle is `execution_identity.json`. Only its secret-free
summary is promoted into `run_result.json` and `run_artifact_manifest.json`.
No additional unbudgeted Vitis probe is launched; the identity consumes the
existing `csynth_invocation.json` evidence.

## Evidence

```text
base_head=e65676fe6c77fe21dbae7b2ee7b7e0cf5b1ffb3d
full_regression=1362/1362
new_tests=10
artifact_dir=/data/agrefactor_runs/pre_stage3_execution_identity_20260724_002030
model_api_called=false
vitis_run=false
Execution Identity frozen contract=closed
P5=next, not started
P0=not run
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
