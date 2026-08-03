# Stage 3.8 Multi-Kernel Evaluation Acceptance

## Status

```text
PACKAGE=S3.8_EVALUATION_V2_LEGACY_CORRECTION
BASELINE_COMMIT=84b6fac0a00469fc9651f5f6553b50febedb21c7
STATUS=ACCEPTED_ONLY_AFTER_TARGET_HOST_MATRIX
S38_FOCUSED=63/63
OPTIMIZER_REGRESSION=405/405
FULL_DETERMINISTIC_REGRESSION=2007/2007
KERNELS=array-map,reduction,nested-stencil
REPEATS=2
ARMS=safe-optimize,source-full,simple-iter
PLANNED_REAL_RUNS=18
AUTOMATIC_MODEL_RETRY=false
STABLE_SUPERIORITY_CLAIMED=false
```

The implementation is retained only after the target-host installer completes
all deterministic checks and the frozen real evaluation matrix. A failed or
incomplete gate restores the exact S3.7 closure worktree while preserving the
external evaluation artifacts.

## Required real matrix

```text
safe direct optimize: 3 kernels × 2 repeats
live source-only full: 3 kernels × 2 repeats
Legacy simple_iter: 3 kernels × 2 repeats
```

Legacy input and selected output are independently qualified. Its internal
feedback testbench cannot establish correctness, Hidden acceptance, or PPA.

## Acceptance conditions

```text
complete_matrix=true
record_contract_issues=[]
infrastructure_failure_count=0
direct_optimize_accepted=true
live_source_full_accepted=true
multi_kernel_real_vitis_observed=true
legacy_simple_iter_comparison_executed=true
legacy_simple_iter_execution_observed_runs=6
stable_superiority_claimed=false
```

Candidate rejection, safe abstention, no improvement, and rollback remain
measured outcomes rather than infrastructure failures. No outcome may weaken
correctness, Hidden isolation, budget accounting, or `best_correct`.

## Reported evidence

The canonical `evaluation_report.json`, `run_records.json`, CSV table, protocol,
plan, immutable corpus manifest, per-run process logs, product artifacts, and
independent Legacy qualification artifacts are retained under the external
audit root.

## Claim boundary

This acceptance is a bounded three-kernel, two-repeat Stage 3 gate. It does not
claim statistical significance, stable superiority, arbitrary-kernel coverage,
or portability beyond the pinned model/Target/toolchain contract.

## Resume contract

- accepted and candidate-failure records are immutable observations and are reused;
- infrastructure-failure attempts are archived and rerun;
- interrupted run directories without a terminal record are archived and rerun;
- archive/retry never changes the frozen protocol identity.

## V2 Legacy observer correction

The first target-host V1 matrix produced authoritative `optimize` and `full`
evidence, but all six Legacy records stopped before Legacy model execution. The
external baseline qualification correctly reported the full Stage 3 order:

```text
source → preflight → public → csynth → hidden → ppa → feasibility
```

The V1 observer incorrectly expected a shorter order and raised
`qualification stage order mismatch`. The generic exception classifier then
misclassified this observer defect as a candidate failure, allowing an invalid
fair-comparison claim. V1's `stage3_s38_accepted=true` is therefore not closure
evidence.

V2 preserves the exact protocol identity and the twelve immutable product-arm
records, archives the six invalid Legacy attempts, and reruns only the
`simple-iter` arm. Run-record schema v2 adds explicit evidence that baseline
qualification completed, Legacy execution started, a safe Legacy evaluation
artifact was observed, at least one physical model call occurred, and final
independent qualification occurred when a candidate was selected. Product-arm
v1 records remain readable; all corrected Legacy records must be v2. Observer,
record-contract, and adapter invariant failures are infrastructure failures.
