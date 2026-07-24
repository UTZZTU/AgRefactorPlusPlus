# P0 Step A: Heuristic Authority Removal

## Baseline

```text
BASE_HEAD=f10b963bae4e8a7074c5d49ddd7e2ab0c089a87c
BASELINE_TESTS=1406
ACTIVE_STEP=A
```

## A1

Failure fingerprints remain diagnostic metadata, but no longer
terminate configured coverage rounds.

## A2

Private-dependency text helpers no longer block compiler launch.
Real compiler/linker evidence is authoritative.

## A3

The old repair contract preserved every declaration, macro and call
count from a failing Testbench. The minimal contract now protects only:

- a complete Testbench with `main`;
- calls to public original and Candidate top functions;
- prohibition on defining/stubbing/wrapping those public tops.

Helpers such as `insert` and `dfs_traverse`, private-state dependencies,
macros and helper call counts may be removed or corrected.

## Boundary

Hidden flow, generation profiles, repair defaults and global LLM
budget are unchanged.

```text
DEFAULT_LLM_CALLS=32
MODEL_API_CALLED=false
VITIS_RUN=false
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
NEXT_STEP=B
```
