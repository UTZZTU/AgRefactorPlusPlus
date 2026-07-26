# P1/P4 Frozen-Contract Reconciliation

## Authority and scope

The sole authority is
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](../roadmap/PRE_STAGE3_PRODUCTIZATION_PLAN.md).

This is one integrated correction package. It does not create additional
P1/P4 sub-stages, does not implement the P2 source-only CLI, and does not
start Stage 3.

## Evidence

```text
base_head=5677c4454c3254523fda439aabdb67866b85cf0f
baseline_tests=1312
full_regression=1334/1334
new_tests=22
product_patch_id=c7dacd1afe4ad4e67a635f9e63d225a847aaf326
artifact_dir=/data/agrefactor_runs/pre_stage3_p1_p4_reconciliation_20260723_175539
model_api_called=false
real_csim=false
csynth=false
vitis_run=false
p2_status=next
stage3_started=false
```

## P1 frozen contract

The typed family profile now expresses:

```text
declared / deterministically_tested / network_smoke_verified
declared known-supported parameters (non-exhaustive)
explicit rejected parameters (hard blocks)
parameter aliases
artifact-specific defaults
typed maximum-output policy
request timeout
capability tags
prompt-profile identity
reasoning map / omit / reject
```

All family profiles use `deterministically_tested`. The bounded
`deepseek-v4-flash` network smoke remains a concrete-model evidence record and
is not widened into a claim about the whole DeepSeek family or every endpoint.

## P4 frozen contract

The product contract now includes:

```text
provided / generated / derived / cached source kinds
historical filesystem/external compatibility
independent Public and Hidden selections
multiple provided suites
provided / auto / hybrid mode derivation
suite id/version and split
source hash and operator artifact path
generation model/Profile and Prompt hash
trajectory and round
coverage and qualification status
feedback visibility
generated-source local materialization
Hidden operator-full versus agent-safe redaction
pre-model Hidden source isolation in prompt builder and Candidate adapter
```

The later P2 normal CLI must map its flags into `TestSourcePlan`; it must not
reimplement or weaken this contract.

`supported_parameters` is intentionally descriptive rather than a global
allowlist. Existing provider-specific extension parameters remain accepted;
hard denials are expressed through `rejected_parameters`.

## Correct status

```text
P1 frozen contract=reconciled
P4 frozen contract=reconciled
P2 source-only bootstrap=not implemented by this package
Execution Identity=not yet closed
P5=not yet closed
P0=not yet run through final normal CLI
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
