# V2.3 Inheritance-First Stage III Checkpoint

This checkpoint records the internal `src/` inheritance campaign. It does not
rewrite Stage I, Stage II, or Stage II-B evidence.

## Frozen boundary

- Branch: `research-roadmap-v2.3`
- Checkpoint commit before execution: `19050cafe3dd5d6e42f14478663c7cc5153de477`
- Execution boundary: ordinary `refactor`; R2-R4, repair memory, `optimize`, and `full` disabled
- Toolchain: Vitis HLS 2023.2
- Cohort: 56 internal designs from `app`, `c2hlsc`, `hlsrewritter`, `leetcode`, and `opt`
- Formal product entry: `python -m agrefactor.cli refactor`

## Evidence roots

- Generation: `/data/agrefactor_runs/inheritance_stage3_generation_v1`
- Zero-call preflight: `/data/agrefactor_runs/inheritance_stage3_preflight_v1`
- Source baseline: `/data/agrefactor_runs/inheritance_stage3_source_baseline_v1`
- Formal refactor: `/data/agrefactor_runs/inheritance_stage3_refactor_v1`
- Sealed checkpoint: `/data/agrefactor_runs/inheritance_stage3_checkpoint_v1`

The sealed campaign manifest SHA-256 is
`5ca345663a8138cced3fe495bda8fb0215f6ac636d173dc0e6ee42a903c95efc`.
The independent audit result SHA-256 is
`0b62f662d1698344b535b344edf9f478be0c31c2abcd5bf27edde65873a4ac32`.

## Results

| Outcome | Count |
|---|---:|
| Adapter/oracle invalid at zero-call preflight | 48 |
| Formal refactor blocked | 6 |
| Ordinary refactor success | 1 |
| Source-pass control, unnecessary rewrite | 1 |

The one ordinary success was `hlsrewritter/Exception_E3_Turbo_Encoder`.
The source-pass control was `hlsrewritter/Incomplete_E1_Fibonacci`; it is not
reported as refactor lift. Five qualified sources had real CSYNTH/COSIM
baseline failures, while one qualified source had a validation infrastructure
failure. No product defect was changed during this campaign.

## Accounting

| Ledger | Provider | Vitis launches |
|---|---:|---:|
| Stage II-B carried forward | 18 | 14 |
| Stage III testbench generation | 56 | 0 |
| Stage III source baseline | 0 | 19 |
| Stage III formal refactor | 72 | 12 |
| Stage II-B + Stage III total | 146 | 45 |

Independent audit used `0 Provider / 0 Vitis` and reported `passed` with zero
critical findings. Git history mutations during execution were zero. The
campaign stops here; Stage IV and R2-R4/experience-system execution are not
entered by this checkpoint.
