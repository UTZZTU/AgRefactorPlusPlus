# P0 Testbench Repair Retry Feedback

## Observed real-run failure

A real DFS run on commit
`ad0bb39c2f6ab59cb6e9f4077b5b99a46850710d`
invoked two Public Testbench repair calls. Both responses were
deterministically rejected because they removed required
declarations for `insert` and `dfs_traverse`.

The second request still contained the original Testbench and
original preflight feedback, but did not include the first
response-contract rejection. The second attempt therefore did
not constitute evidence-driven refinement.

## Correction

Testbench repair requests now carry safe prior-attempt summaries.
Deterministic response-contract failures, empty responses and
unchanged responses are forwarded to the next bounded attempt.

Every Testbench repair prompt also states its computed preservation
contract explicitly:

- required function declaration names;
- required macros;
- minimum function-call counts.

The deterministic validator remains authoritative and unchanged.
Hidden Testbench content remains excluded. Repair attempts remain
bounded at two.

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
