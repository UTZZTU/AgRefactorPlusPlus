# P4-0F-R5-E v1/v2 Real-canary Adjudication

## Evidence identities

```text
v1_run_id=p4_0f_r5_e_20260806T171335Z_28799
v1_archive_sha256=c9aaacc58aa09b8928b70d061beaad0be6a1ffd0a6f892385dae1a385655b722
v2_run_id=p4_0f_r5_e_20260806T172137Z_60901
v2_archive_sha256=5fb38e6cc01cfcab2a4237e424d692ff8c146caa00e1687981b3e8ca8138159d
```

## v1

- complete deterministic regression: 2268/2268;
- campaign not launched because the package passed unsupported `--manifest` to a positional CLI;
- repository unchanged;
- classification: package execution harness defect.

## v2

- complete deterministic regression: 2268/2268;
- five no-model cases launched;
- campaign heartbeat, sequence, shell=false, independent roots, and fail-soft continuation verified;
- baseline real-Vitis qualification passed;
- Public CSIM and COSIM Testbench hybrid recovery passed;
- Public CSIM and COSIM Candidate recovery safely failed before provider repair;
- real provider diagnostic blocked by the stop rule.

## Root causes

1. Native CSIM physical simulation failure was misreported as Testbench compile failure.
2. COSIM C pre-check pass remained stale after RTL post-check failure.

The 22 critical-risk entries in the v2 closure report are not 22 independent defects. They comprise two roots, direct recovery consequences, and provider evidence intentionally absent after the stop rule.

## Safety result

No false acceptance, Hidden leak, secret leak, private reasoning persistence, budget bypass, best_correct corruption, shell execution, or repository mutation was observed.
