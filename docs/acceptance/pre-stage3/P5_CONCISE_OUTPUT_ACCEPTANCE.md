# P5 Concise Output and Log Capture Acceptance

## Authority and scope

The sole authority is `PRE_STAGE3_PRODUCTIZATION_PLAN.md`, section 9 and Step 5.
This package does not run P0, close Pre-Stage-3, or start Stage 3.

## Closed contract

```text
default = concise human-readable summary
--json = one stable machine-readable summary object
--verbose = concise summary plus phase-level status
--debug = captured diagnostics tee plus safe final summary
Legacy/model/tool stdout and stderr = captured by default
Hidden paths/operator-only evidence = absent from ordinary summaries
Token/Cost = soft, observed-only, non-blocking
LLM/tool/compile/CSIM/CSYNTH/wall-time = effective hard budgets
```

Every normal source run persists:

```text
full_result.json
trace.jsonl
model_calls.json
tool_calls.json
stdout.log
stderr.log
execution_identity.json
run_artifact_manifest.json
```

`run_result.json` remains temporarily as a compatibility artifact until the
post-P0 cleanup/deprecation package.

## Evidence

```text
base_head=0a1d816fa1d7f738dd3757a19a243df22020caf5
full_regression=1391/1391
new_tests=19
artifact_dir=/data/agrefactor_runs/pre_stage3_p5_concise_output_20260724_015301
model_api_called=false
vitis_run=false
P5 frozen contract=closed
P0=next, not run
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
