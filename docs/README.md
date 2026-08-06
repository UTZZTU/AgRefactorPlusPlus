# AgRefactor++ Documentation

这里是仓库文档的统一入口。文档按“当前权威、未来实施、用户指南、验收证据、审计和历史”分类，避免把已经关闭的阶段计划误当成当前执行指针。

<!-- P4_0F_R5_AUTHORITY_V2:BEGIN -->
## 当前 P4-0F-R5 权威入口（2026-08-07）

1. [P4-0F-R5 权威执行计划 v2](roadmap/PRE_STAGE4_P4_0F_R5_AUTHORITATIVE_EXECUTION_PLAN_V2.md)
2. [当前机器可读状态](roadmap/pre_stage4_current_state.json)
3. [R5-E-R1 决策记录](roadmap/PRE_STAGE4_P4_0F_R5_E_R1_DECISION_RECORD.md)
4. [Legacy 差分与真实代码 discovery 执行合同](roadmap/PRE_STAGE4_LEGACY_DIFFERENTIAL_AND_REAL_CODE_DISCOVERY_EXECUTION_CONTRACT.md)
5. [R5-E v1/v2 真实 canary 裁决](audits/P4_0F_R5_E_V1_V2_REAL_CANARY_ADJUDICATION.md)

当前行为 checkpoint 为 `0ca5dd99fabec1c2c003446975e28128a0926c52`。R5-D 已接受；R5-E 尚未接受；唯一下一实现包是 P4-0F-R5-E-R1。Legacy 差分 batch A 与 Real-code discovery batch A 在 R5 accepted 后、P4-0F-Final 前执行；扩展 discovery batch B 在 P4-0G 后、P4-0H 前执行。
<!-- P4_0F_R5_AUTHORITY_V2:END -->

## 当前权威阅读顺序

1. [当前项目状态](roadmap/PROJECT_STATE.md)
2. [长期路线](roadmap/ROADMAP.md)
3. [目标追踪](roadmap/GOAL_TRACEABILITY.md)
4. [Stage 3 冻结实施合同](roadmap/STAGE3_IMPLEMENTATION_CONTRACT.md)
5. [使用指南](guides/USAGE.md)
6. [CLI 参数参考](guides/CLI_PARAMETER_REFERENCE.md)
7. [复现与验证状态](guides/REPRODUCTION_STATUS.md)

## 当前路线与未来阶段

- [AgRefactor++ Development Roadmap](roadmap/ROADMAP.md)
- [AgRefactor++ Goal Traceability](roadmap/GOAL_TRACEABILITY.md)
- [Stage 3 — Safe Three-Level Optimizer](roadmap/STAGE3_SAFE_OPTIMIZER.md)
- [Stage 3 Frozen Implementation Contract](roadmap/STAGE3_IMPLEMENTATION_CONTRACT.md)
- [Stage 4 — Memory Applicability Gate](roadmap/STAGE4_MEMORY_GATE.md)
- [Stage 5 — Version-Aware Vitis HLS Migration](roadmap/STAGE5_VERSION_MIGRATION.md)
- [Stage 6 — Evaluation, Ablation, and Final Delivery](roadmap/STAGE6_EVALUATION.md)
- [Product Capability Backlog](roadmap/PRODUCT_CAPABILITY_BACKLOG.md)

## 用户指南

- [使用说明](guides/USAGE.md)
- [CLI 参数参考](guides/CLI_PARAMETER_REFERENCE.md)
- [环境与复现说明](guides/ENVIRONMENT.md)
- [当前复现与验证状态](guides/REPRODUCTION_STATUS.md)
- [Legacy Baseline Status](guides/LEGACY_BASELINE_STATUS.md)

## 已关闭阶段合同与计划

这些文件保留历史和审计价值，但不再是当前执行指针：

- [Pre-Stage-3 产品化与关闭计划](roadmap/PRE_STAGE3_PRODUCTIZATION_PLAN.md)
- [Stage 0 — Reproduction Baseline](roadmap/STAGE0_BASELINE.md)
- [Stage 1 — Shared Infrastructure](roadmap/STAGE1_INFRASTRUCTURE.md)
- [Stage 2 — Structured Evidence Loop](roadmap/STAGE2_EVIDENCE_LOOP.md)
- [Stage 2 Audit, Hardening, and Closure Plan](roadmap/STAGE2_HARDENING_PLAN.md)
- [Stage 2.6 Closure-readiness Audit](roadmap/STAGE2_CLOSURE_READINESS_AUDIT.md)
- [Stage 2.5 Multi-type Kernel Smoke Evidence Summary](roadmap/stage2_smoke_evidence_summary.md)
- [Stage 2.7.5 Real Network-model Candidate Repair Smoke](roadmap/stage2_real_network_candidate_repair_smoke.md)
- [Stage 2.7.6 Evidence-gated Ground-truth Revalidation](roadmap/stage2_evidence_gated_ground_truth_revalidation.md)

## 验收证据

### Stage 1

- [Stage 1 Core Acceptance](acceptance/stage1/stage1_core_acceptance.md)
- [Stage 1 TargetProfile Acceptance](acceptance/stage1/stage1_target_profile_acceptance.md)
- [Stage 1 Compile and CSIM Budget Acceptance](acceptance/stage1/stage1_compile_csim_budget_acceptance.md)
- [Stage 1 CSYNTH Budget Acceptance](acceptance/stage1/stage1_csynth_budget_acceptance.md)

### Stage 2

- [Stage 2 Acceptance](acceptance/stage2/stage2_acceptance.md)
- [Stage 2 Runtime Evidence Acceptance](acceptance/stage2/stage2_runtime_evidence_acceptance.md)
- [Stage 2 Hardening Acceptance](acceptance/stage2/stage2_hardening_acceptance.md)
- [Stage 2 Closure Acceptance](acceptance/stage2/stage2_closure_acceptance.md)
- [Stage 2 Test-Suite Evidence Acceptance](acceptance/stage2/STAGE2_TEST_SUITE_EVIDENCE_ACCEPTANCE.md)

### Pre-Stage-3

- [Pre-Stage-3 Cleanup and Closure](acceptance/pre-stage3/PRE_STAGE3_CLEANUP_AND_CLOSURE_ACCEPTANCE.md)
- [Pre-Stage-3 Documentation Consistency](acceptance/pre-stage3/PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md)
- [CLI Parameter Contract Acceptance](acceptance/pre-stage3/CLI_PARAMETER_CONTRACT_ACCEPTANCE.md)
- [Post-CLI Real Source-Only Smoke](acceptance/pre-stage3/POST_CLI_REAL_SMOKE_ACCEPTANCE.md)
- [Documentation Cleanup and Stage 3 Planning Freeze](acceptance/pre-stage3/DOCUMENTATION_CLEANUP_AND_STAGE3_PLANNING_ACCEPTANCE.md)

其余 P0–P5 细分验收继续保留在 [`acceptance/pre-stage3/`](acceptance/pre-stage3/) 中，不在首页重复展开。

## 审计与决策

- [Pre-Stage-3 Deprecation Ledger](audits/PRE_STAGE3_DEPRECATION_LEDGER.md)
- [P1/P4 Frozen-Contract Reconciliation](audits/P1_P4_FROZEN_CONTRACT_RECONCILIATION.md)
- [P1 Model Runtime Audit Decisions](audits/P1_MODEL_RUNTIME_AUDIT_DECISIONS.md)
- [P1-B0 Pricing Consumer Audit Decisions](audits/P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md)
- [P0 Prompt Identity Reconciliation](audits/P0_PROMPT_IDENTITY_RECONCILIATION.md)
- [P0 Step F Lightweight Audit Decisions](audits/P0_STEP_F_LIGHTWEIGHT_AUDIT_DECISIONS.md)

## 实施历史

- [变更记录](history/CHANGELOG.md)
- [Pre-Stage-3 Transition Lessons](history/PRE_STAGE3_TRANSITION_LESSONS.md)
- [P0 Generation and Repair Stabilization](history/P0_GENERATION_REPAIR_STABILIZATION_PLAN.md)
- [P0 Heuristic Authority Removal](history/P0_HEURISTIC_AUTHORITY_REMOVAL.md)
- [P0 Hidden Boundary Correction](history/P0_HIDDEN_BOUNDARY_CORRECTION.md)
- [P0 Dual Generation Profiles](history/P0_DUAL_GENERATION_PROFILES.md)
- [P0 Repair Budget Parameterization](history/P0_REPAIR_BUDGET_PARAMETERIZATION.md)
- [P0 Testbench/Stub Prompt Refinement](history/P0_TESTBENCH_STUB_PROMPT_REFINEMENT.md)

## 目录约定

- `guides/`：当前用户合同与复现说明；
- `roadmap/`：当前状态、长期目标、未来阶段与冻结实施合同；
- `acceptance/`：可复核的阶段验收证据；
- `audits/`：审计、决策和兼容/弃用账本；
- `history/`：已完成实施、失败经验和历史计划，不作为当前执行指针。

原 `reference/` 中的一次性交接、过期桥接和单次失败备忘已合并到历史材料后删除。
