# P4 Public/Hidden Test Source Provenance Acceptance

## Status

```text
p4_status=completed
p2_status=active
implementation_parent=18ac186f00770b87dc569fbf58afc58298727698
implementation_patch_id=bd85479221d8729c9aad23df6a91ccfaf4d7333b
baseline_tests=1275
new_tests=37
full_regression=1312/1312
model_api_called=false
vitis_run=false
stage3_started=false
```

P4 closes the missing source-identity contract without rebuilding the existing
Public/Hidden state machine.

## Accepted contract

```text
TestSourceSpec
-> explicit source_id and optional revision
-> optional expected SHA-256
-> TestSuiteSpec source declaration
-> pre-executor filesystem resolution
-> exact file-content SHA-256
-> exact context_variables['testbench'] equality
-> TestSourceProvenance
-> TestEvaluationEvidence
-> TraceRecorder audience policy
```

A provenance-enabled local suite cannot launch its executor when:

```text
the source file is missing
the source path is not a regular file
the declared digest does not match
the runtime testbench text differs from the resolved file
the source kind is not locally resolvable
```

## Public/Hidden boundary

Operator-full evidence contains source identity, revision, content digest, byte
size and resolved path. Public agent evidence may contain the same information.
Hidden agent-safe evidence retains source identity/revision only and removes the
digest, size and resolved path together with existing diagnostics/artifacts.

Legacy suites without an explicit source declaration remain backward
compatible. Multiple suites retain independent source identities and digests.

## Evidence

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p4_source_provenance_20260723_151119
focused_tests=37/37
full_regression=1312/1312
patch_id=bd85479221d8729c9aad23df6a91ccfaf4d7333b
```

No model API, real CSIM, CSYNTH or Vitis execution was used. The product
integration is deterministic; final real source-only DFS acceptance remains P0.

The next active package is P2 source-only bootstrap and unified normal-user CLI.
Execution Identity, P5, P0, Pre-Stage-3 closure and Stage 3 remain ordered later
work.
