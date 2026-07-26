# P0 Public Testbench Repair Routing and Output Limits

## Observed real-P0 failure

The real DFS source-only run completed Legacy generation and returned
a synthesizable Candidate. Formal preflight then rejected the generated
Public Testbench because it declared implementation-private globals:
`root`, `queue`, `front`, `rear`, and `g_fallback`.

Validation correctly routed the failure to `repair_testbench`, but the
formal phase only owned Candidate repair. It therefore terminated as
`repair_not_applicable` with zero repair attempts.

## Corrected execution order

For an automatically generated Public suite, source bootstrap now runs
the existing independent Testbench preflight/repair loop before the
Candidate repair-aware validation. Only Public Testbench content is
editable; Hidden content remains evaluation-only and absent from model
prompts. A changed Public suite is rebound as `derived` provenance with
a new content hash, suite version, prompt hash, trajectory and round.

## Model output limits

```text
Candidate:           32768
Testbench:           32768
Candidate repair:    32768
Testbench repair:    32768
Safety ceiling:      65536
```

This is an observed P0 blocker correction only.

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
