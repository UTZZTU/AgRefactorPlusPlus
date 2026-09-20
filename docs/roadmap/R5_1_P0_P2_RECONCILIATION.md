# R5.1 P0-P2 Dataset and Dedup Reconciliation

Date: 2026-09-20  
Route: V2.3 R5.1  
Baseline head: `3d53bf9d657077a598efe82beb57a5e2f96ab359`  
Implementation head: `a8e229e1ee22edb6e9472334320ed8c392b4a1d4`  
Status: `passed_with_quarantine`

## Scope

This reconciliation closes P0-P2 of
`V2_3_PRE_R6_ROBUSTNESS_AND_EVIDENCE_PLAN.md`. It freezes the R5.1 budget and
predecessor boundary, records external-source admission decisions, gives all
57 `src/info.json` entries a stable identity and status, applies D0-D3
deduplication, and freezes the history/future partition by semantic family.
It does not call a Provider or Vitis, admit R5, start R6, or reinterpret an
older campaign.

## Frozen Inputs

| Artifact | SHA-256 |
|---|---|
| R5.1 route | `11635a6671720ac22e7ca289a09249f28f434364889429104fe70e3dd281c11b` |
| 650/650 budget authorization | `09564e119665d89a1a9f396d7874b58e793c7d7567884e922a6183ca96664392` |
| Dataset registry schema | `e1e0b7a08b9a2f243a906ba407df40d7183feaa9c0ac5acceb728ad9e48e0dcb` |
| Deduplication policy | `a065cb7ce9b390a944c119301505f540d15d64c6588608f51a6cceed668ee0e6` |
| External-source manifest | `6462b1001630d1fc3ca36006feeb868d3f4348fbd104809c191c4f02294b02da` |
| Generated registry file | `aa2f2b889e36482a0aa7b1def4588fc668132f01f78cb90948713531fff54321` |
| Independent audit file | `90a5f46a858bd785ec5907f4f304d88f78f40de75b0261f4d6c1651d4daba5b1` |

The registry's canonical content hash is
`ff0a5e25b28bc057f0a6ef96f185a1eace3270a131dd90d949ac725516f3f386`.
Source identities use repository LF bytes so Windows and Linux checkouts
reproduce the same content identity. No whitespace normalization is applied
at D0 beyond CRLF-to-LF repository canonicalization.

## Internal Registry Result

The 57 registered entries consist of 49 legacy `useful` and 8 legacy
`not_useful` labels. Those labels are retained as predecessor metadata, not
treated as current success or failure outcomes.

| Result | Count |
|---|---:|
| Requires a reviewed oracle adapter | 9 |
| Quarantined for unresolved license/provenance or execution contract | 47 |
| Rejected because the registered source is absent | 1 |
| History partition | 39 |
| Future partition | 18 |
| Cases with reconstructable legacy claim references | 11 |

The missing entry is `src/c2hlsc/runs/kernel.cpp`. The registry records a null
source hash and `source_file_missing`; it does not hash empty bytes or infer
content.

Four existing Tcl execution contracts are inconsistent with their registry
identity: `recalib_table_orig`, `mm_chain_dp_orig`, `DA_E2_binary_tree`, and
`Struct_E3_long`. These cases remain quarantined. In particular, the
`DA_E2_binary_tree` Tcl selects `kernel.cpp` and top `DFS`, while the selected
file does not contain that top. Its selected `kernel.cpp` is a D1 lexical
duplicate of `DA_E1_linear_program/kernel.cpp`. This duplicate is reported
symmetrically and is not silently deleted. No D2-only candidate was excluded.

## External Sources

The official repository identities are pinned before outcome observation:

| Source | Decision | Reason |
|---|---|---|
| C2HLSC | `admit` | GPL-3.0 and commit fixed; only non-duplicate cases with reviewed oracles may enter |
| HLSPilot | `external-only` | commit fixed, repository license not found |
| HLS-Eval | `external-only` | commit fixed, sub-dataset licenses require per-case review |
| HLSFactory | `external-only` | AGPL/CC-BY-SA metadata fixed; use pinned external checkout and per-dataset review |
| TimelyHLS | `quarantine` | no verified official dataset repository |
| arXiv 2407.03889 | `reject` | method reference, not an admitted dataset artifact |

No third-party code was copied into the repository.

## Verification

The authoritative Ubuntu/Vitis environment was loaded with
`source /data/agrefactorpp_env.sh`. The builder and independent auditor
reproduced the same registry hash on Windows and Linux. Ten new focused tests
and 32 adjacent R5 evidence/oracle tests passed, for 42 passing tests total.

```text
R5_1_DATASET_AUDIT_STATUS=passed_with_quarantine
R5_1_INTERNAL_CASES=57
R5_1_AUDIT_FAILURES=0
PROVIDER_CALLS=0
VITIS_LAUNCHES=0
GIT_HISTORY_MUTATIONS=0
```

## Decision and Next Step

P0-P2 are complete with explicit quarantine, not with blanket dataset
admission. `R5_ACCEPTED` remains false, `R6_STARTED` remains false, and real
campaign calls remain closed. P3 may now build external-checkout adapters and
run zero-Provider host/oracle smoke tests. Any Vitis launch still requires a
new machine-readable P3/P4 reserve and preflight against the cumulative
650/650 authorization.
