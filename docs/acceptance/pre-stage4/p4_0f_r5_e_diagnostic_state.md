# P4-0F-R5-E Diagnostic State

```text
R5_E_V1=failed_package_harness_before_campaign
R5_E_V1_RUN_ID=p4_0f_r5_e_20260806T171335Z_28799
R5_E_V1_ARCHIVE_SHA256=c9aaacc58aa09b8928b70d061beaad0be6a1ffd0a6f892385dae1a385655b722
R5_E_V2_RUN_ID=p4_0f_r5_e_20260806T172137Z_60901
R5_E_V2_ARCHIVE_SHA256=5fb38e6cc01cfcab2a4237e424d692ff8c146caa00e1687981b3e8ca8138159d
R5_E_V2_BASELINE=2268/2268
R5_E_V2_CASES_LAUNCHED=5/5
R5_E_V2_CASES_PASSED=3/5
R5_E_V2_CAMPAIGN_OBSERVABILITY=true
R5_E_V2_PROVIDER_DIAGNOSTIC=not_run_by_stop_rule
R5_E_RUNTIME_GATES_PASSED=false
R5_ACCEPTED=false
NEXT=P4-0F-R5-E-R1
```

This is a diagnostic state, not acceptance. R5-E must be rerun from a new checkpoint after E-R1 and independently audited before R5 can close.
