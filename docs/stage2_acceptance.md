# Stage 2 Acceptance Record

## Scope

Stage 2 establishes the correctness-first testbench reliability path for AgRefactor++:

- structured testbench preflight evidence and ownership;
- deterministic rejection of provable implementation-private file-scope dependencies;
- bounded model-backed testbench-only repair;
- ABI/linkage-aware repair constraints;
- preservation of public calls, tests, seeds, checks, and macros;
- unified CLI integration with legacy `flow.new`;
- combined AutoGen and testbench-repair usage accounting;
- structured trace and persisted repair artifacts.

## Accepted revision

- Branch: `stage2-testbench-reliability`
- Commit: `d91f1a32f58ef5a39737671ad16844513642d477`
- Record generated: `2026-07-14T14:09:41.176589+00:00`

## Deterministic regression suite

- Result: **110/110 tests passed**
- Command: `python -m unittest discover -s tests -p 'test_*.py' -v`

## Real unified CLI validation

- Run: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416`
- Run ID: `stage2-integrated-cli-20260714_213416`
- Status: `succeeded`
- Unified CLI succeeded: `True`
- Trace events: `7`
- Testbench repair status: `passed`
- Testbench repair attempts: `2`
- Final preflight: `passed` / `none` / `none`
- C synthesis reports: `4`
- C simulation binaries: `1`

## Known usage

- Combined known tokens: `23442`
- Known LLM calls: `2`
- Known cost: `$0.0032596199999999995`
- Accounting mode: `post_hoc_combined`
- LLM call count complete: `False`
- Cost complete: `False`
- Repair tokens: `8339`
- Repair calls: `2`
- Unknown-cost repair calls: `2`

## Evidence artifacts

- CLI result: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/cli_result.json`
- Trace: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/trace.jsonl`
- Final context: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/context_final.json`
- Repair artifact: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/testbench_repair_213539_214082/testbench_repair.json`
- C synthesis report: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/csynth_213606/csynth/solution/syn/report/process_top_hls_Pipeline_VITIS_LOOP_44_2_csynth.rpt`
- C synthesis report: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/csynth_213606/csynth/solution/syn/report/process_top_hls_Pipeline_VITIS_LOOP_65_4_csynth.rpt`
- C synthesis report: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/csynth_213606/csynth/solution/syn/report/process_top_hls_Pipeline_VITIS_LOOP_74_5_csynth.rpt`
- C synthesis report: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/csynth_213606/csynth/solution/syn/report/process_top_hls_csynth.rpt`
- C simulation binary: `/data/agrefactor_runs/stage2_integrated_cli_20260714_213416/legacy/csim_213606/csim`

## Explicit limitations

This acceptance record supports a Stage 2 milestone, not a claim of general correctness:

1. The real end-to-end result currently covers one stateful kernel and one host/toolchain configuration.
2. Clean-state process isolation was produced by the repair model under prompt constraints; a fully deterministic isolation policy is not yet enforced by the repair contract.
3. The private-dependency gate conservatively detects provable file-scope global dependencies; it does not prove the absence of every copied private type, helper, or internal structure.
4. AutoGen LLM-call counts and tool-call counts remain incomplete; the reported LLM calls are a known lower bound.
5. Repair-provider cost was unavailable in the accepted run, so the reported cost is incomplete rather than a full total.
6. This record does not validate Stage 3 optimization behavior.

## Acceptance checks

- `unified_cli_succeeded`: `true`
- `trace_finished`: `true`
- `repair_passed`: `true`
- `final_preflight_passed`: `true`
- `csynth_report_exists`: `true`
- `csim_binary_exists`: `true`
- `deterministic_tests_passed`: `true`

<!-- AGREFPP_STAGE2_CLOSURE_SCOPE:START -->
## Stage closure note

本文档验收的是 **Stage 2 Testbench Reliability 核心**，不是原始 Stage 2 全部范围。完整 Stage 2 仍需 general feedback parser、evidence state machine、layered stage/model Prompt、多类型真实 kernel smoke 与文档同步。

权威完成标准见 [`ROADMAP.md`](ROADMAP.md)，当前阶段细节见 [`STAGE2_EVIDENCE_LOOP.md`](STAGE2_EVIDENCE_LOOP.md)。
<!-- AGREFPP_STAGE2_CLOSURE_SCOPE:END -->


<!-- AGREFPP_STAGE2_RUNTIME_RELATION:START -->
## Relationship to the later Stage 2 runtime milestone

This document remains the acceptance record for the early
**Testbench Reliability** milestone. It is intentionally not rewritten as if
the later general feedback and runtime orchestration already existed at that
time.

The subsequent Stage 2.1–2.3 evolution is recorded in:

- [`STAGE2_EVIDENCE_LOOP.md`](STAGE2_EVIDENCE_LOOP.md);
- [`stage2_runtime_evidence_acceptance.md`](stage2_runtime_evidence_acceptance.md).

The later milestone adds Public/Hidden suite evidence, generic feedback and
state strategy, real Preflight/CSYNTH/Public-CSIM/Hidden-CSIM handlers, shared
physical budgets, safe traces, and a real Vitis 2023.2 full validation-chain
acceptance.
<!-- AGREFPP_STAGE2_RUNTIME_RELATION:END -->
