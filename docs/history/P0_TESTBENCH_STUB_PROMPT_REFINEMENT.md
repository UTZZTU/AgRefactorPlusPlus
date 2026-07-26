# P0 Step D: Testbench/Stub Prompt and Error Ownership

## Baseline

```text
BASE_HEAD=551812700c6684267cc1a9978835d95fd6524535
BASELINE_TESTS=1429
ACTIVE_STEP=D
DEFAULT_LLM_CALLS=32
```

## Testbench contract

Public and held-out Testbench generation now treats Original and Candidate
implementations as read-only black boxes.

```text
only external forward declarations = Original top + Candidate top
Testbench must not define/stub/wrap/alias either top
implementation-private globals/types/helpers = forbidden in prompts
correctness > testcase count or coverage
```

The same contract is present in the lightweight generator, coverage-enhanced
generator, static agent prompts, and formal Testbench repair prompt.

## Stub contract

The generation-time Stub is the only temporary Candidate implementation.

```text
Candidate top definition count = exactly one
main = forbidden
Original top definition/wrapper/copy = forbidden
Candidate definition header = frozen ABI
Stub-owned failure = regenerate Stub only
```

## ABI freeze and Stub reuse

The first tool-qualified Testbench freezes the Candidate declaration and Public
macros. Later coverage-only rounds may expand deterministic inputs and checks,
but must retain the frozen ABI/macros and reuse the matching Stub. ABI drift is
recorded as a Testbench contract failure before another tool launch.

For Public generation, an ABI-only CSYNTH failure is the coordinated
correction path: the Testbench and Stub are regenerated together, the corrected
ABI is measured, and the new ABI is re-frozen. A held-out trajectory receives an
externally frozen Public-derived ABI and is never permitted to rewrite it; an
incompatible held-out ABI fails closed.

## Tool-backed ownership

Coverage compile/run evidence records:

```text
testbench.cpp compiler ownership -> repair Testbench
refactor_code.cpp compiler ownership -> regenerate Stub only
golden-vs-Stub nonzero result -> regenerate Stub only
runtime timeout -> repair Testbench
link/ABI ownership -> coordinated Public Testbench + Stub ABI correction
coverage shortfall -> expand inputs, preserve ABI, reuse Stub
formal Candidate failure -> existing Candidate repair path
```

Ownership derives from real g++/link/runtime/gcov diagnostics. It is not a new
static heuristic gate.

## Status

```text
STEP_D=completed
ACTIVE_STEP=E
MODEL_API_CALLED=false
VITIS_RUN=false
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
DEFAULT_LLM_CALLS=32
```
