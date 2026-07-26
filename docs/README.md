# AgRefactor++ Documentation

这里是仓库文档的统一入口。首页 README 只保留项目介绍和上手方式；
开发路线、验收证据、审计记录和历史实施过程在这里分类维护。

## 推荐阅读顺序

1. [使用指南](guides/USAGE.md)
2. [CLI 参数参考](guides/CLI_PARAMETER_REFERENCE.md)
3. [环境配置](guides/ENVIRONMENT.md)
4. [项目路线](roadmap/ROADMAP.md)
5. [当前项目状态](roadmap/PROJECT_STATE.md)

## 使用指南

安装、环境配置、命令和复现说明。

- [环境与复现说明](guides/ENVIRONMENT.md)
- [CLI 参数参考](guides/CLI_PARAMETER_REFERENCE.md)
- [复现状态与已验证功能](guides/REPRODUCTION_STATUS.md)
- [使用说明](guides/USAGE.md)

## 项目设计与路线

项目状态、长期路线、阶段设计和目标追踪。

- [AgRefactor++ Goal Traceability](roadmap/GOAL_TRACEABILITY.md)
- [Pre-Stage-3 产品化与关闭计划](roadmap/PRE_STAGE3_PRODUCTIZATION_PLAN.md)
- [AgRefactor++ Current Project State](roadmap/PROJECT_STATE.md)
- [AgRefactor++ Development Roadmap](roadmap/ROADMAP.md)
- [Product Capability Backlog](roadmap/PRODUCT_CAPABILITY_BACKLOG.md)
- [Stage 0 — Reproduction Baseline](roadmap/STAGE0_BASELINE.md)
- [Stage 1 — Shared Infrastructure](roadmap/STAGE1_INFRASTRUCTURE.md)
- [Stage 2.6 Closure-readiness Audit](roadmap/STAGE2_CLOSURE_READINESS_AUDIT.md)
- [Stage 2 — Structured Evidence Loop](roadmap/STAGE2_EVIDENCE_LOOP.md)
- [Stage 2 Audit, Hardening, and Closure Plan](roadmap/STAGE2_HARDENING_PLAN.md)
- [Stage 3 — Safe Three-Level Optimizer](roadmap/STAGE3_SAFE_OPTIMIZER.md)
- [Stage 4 — Memory Applicability Gate](roadmap/STAGE4_MEMORY_GATE.md)
- [Stage 5 — Version-Aware Vitis HLS Migration](roadmap/STAGE5_VERSION_MIGRATION.md)
- [Stage 6 — Evaluation, Ablation, and Final Delivery](roadmap/STAGE6_EVALUATION.md)
- [Stage 2.7.6 Evidence-gated Contract/Parser Delta and Ground-truth Revalidation](roadmap/stage2_evidence_gated_ground_truth_revalidation.md)
- [Stage 2.7.5 Real Network-model Candidate Repair Smoke](roadmap/stage2_real_network_candidate_repair_smoke.md)
- [Stage 2.5 Multi-type Kernel Smoke Evidence Summary](roadmap/stage2_smoke_evidence_summary.md)

## Stage 1 验收

Stage 1 基础设施与真实工具验收。

- [Stage 1 Compile and C Simulation Hard Budget Acceptance](acceptance/stage1/stage1_compile_csim_budget_acceptance.md)
- [Stage 1 Core Acceptance](acceptance/stage1/stage1_core_acceptance.md)
- [Stage 1 csynth Hard Budget Acceptance](acceptance/stage1/stage1_csynth_budget_acceptance.md)
- [Stage 1 TargetProfile Acceptance Record](acceptance/stage1/stage1_target_profile_acceptance.md)

## Stage 2 验收

Stage 2 证据闭环、可靠性与关闭证据。

- [Stage 2 Test-Suite Identity and Evidence Acceptance](acceptance/stage2/STAGE2_TEST_SUITE_EVIDENCE_ACCEPTANCE.md)
- [Stage 2 Acceptance Record](acceptance/stage2/stage2_acceptance.md)
- [Stage 2.8 Final Documentation and Stage 2 Closure Acceptance](acceptance/stage2/stage2_closure_acceptance.md)
- [Stage 2.7.7 Cross-stage Regression and Stage 2.8 Handoff Acceptance](acceptance/stage2/stage2_hardening_acceptance.md)
- [Stage 2.7.2 Minimal ModelFamilyProfile Acceptance](acceptance/stage2/stage2_model_family_profile_acceptance.md)
- [Stage 2.7.4 Formal Repair-aware UnifiedRunner / CLI Acceptance](acceptance/stage2/stage2_repair_aware_cli_acceptance.md)
- [Stage 2.7.1 Repair Protocol and Artifact Schema Acceptance](acceptance/stage2/stage2_repair_protocol_acceptance.md)
- [Stage 2 Runtime Evidence Acceptance](acceptance/stage2/stage2_runtime_evidence_acceptance.md)
- [Stage 2.5.1 Smoke Corpus Acceptance](acceptance/stage2/stage2_smoke_corpus_acceptance.md)
- [Stage 2.5.3 Fault / Ownership / Hidden Matrix Acceptance](acceptance/stage2/stage2_smoke_fault_matrix_acceptance.md)
- [Stage 2.5.2 Real Full-chain Pass Matrix Acceptance](acceptance/stage2/stage2_smoke_pass_matrix_acceptance.md)
- [Stage 2.7.3 Stage 1 Hardening Batch A Acceptance](acceptance/stage2/stage2_stage1_hardening_batch_a_acceptance.md)

## Pre-Stage-3 验收

模型、测试来源、CLI、Execution Identity、输出、P0 与关闭证据。

- [Execution Identity Authority Reconciliation Acceptance](acceptance/pre-stage3/EXECUTION_IDENTITY_ACCEPTANCE.md)
- [Pre-Stage-3 CLI Parameter Contract Acceptance](acceptance/pre-stage3/CLI_PARAMETER_CONTRACT_ACCEPTANCE.md)
- [P0 Cost-Budget Currency Blocker Correction](acceptance/pre-stage3/P0_COST_BUDGET_CURRENCY_BLOCKER_ACCEPTANCE.md)
- [P0 Portable Identifying JSON-Object Blocker Correction](acceptance/pre-stage3/P0_PORTABLE_IDENTIFYING_JSON_BLOCKER_ACCEPTANCE.md)
- [P0 Public Testbench Repair Routing and Output Limits](acceptance/pre-stage3/P0_PUBLIC_TESTBENCH_REPAIR_AND_OUTPUT_LIMITS_ACCEPTANCE.md)
- [P0 Real DFS Source-Only Acceptance](acceptance/pre-stage3/P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md)
- [P1-A Static Model Compatibility Acceptance](acceptance/pre-stage3/P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md)
- [P1-B1 Typed Pricing Schema Acceptance](acceptance/pre-stage3/P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md)
- [P1-B2 Official Pricing Snapshots Acceptance](acceptance/pre-stage3/P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md)
- [P1-B3 Provider-Neutral Cost Estimator Acceptance](acceptance/pre-stage3/P1B3_COST_ESTIMATOR_ACCEPTANCE.md)
- [P1-B4A Usage Normalization and Serialization Acceptance](acceptance/pre-stage3/P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md)
- [P1-B4B Native Cost Accounting Acceptance](acceptance/pre-stage3/P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md)
- [P1-C1 Typed Effective Model Configuration Acceptance](acceptance/pre-stage3/P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md)
- [P1-C2 Modern Consumer Migration Acceptance](acceptance/pre-stage3/P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md)
- [P1-C3A Typed Legacy Translation Acceptance](acceptance/pre-stage3/P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md)
- [P1-C3B Generic AG2 Loader Policy Acceptance](acceptance/pre-stage3/P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md)
- [P1-C3C1 Typed AG2 Usage Summary Acceptance](acceptance/pre-stage3/P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md)
- [P1-C Runtime Closure Acceptance](acceptance/pre-stage3/P1C_RUNTIME_CLOSURE_ACCEPTANCE.md)
- [P1-D Bounded DeepSeek Network Smoke Acceptance](acceptance/pre-stage3/P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md)
- [P2 Source-only Bootstrap Acceptance](acceptance/pre-stage3/P2_SOURCE_ONLY_BOOTSTRAP_ACCEPTANCE.md)
- [P4 Public/Hidden Test Source Provenance Acceptance](acceptance/pre-stage3/P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md)
- [P5 Concise Output and Log Capture Acceptance](acceptance/pre-stage3/P5_CONCISE_OUTPUT_ACCEPTANCE.md)
- [Pre-Stage-3 Cleanup and Closure Acceptance](acceptance/pre-stage3/PRE_STAGE3_CLEANUP_AND_CLOSURE_ACCEPTANCE.md)
- [Pre-Stage-3 Documentation Consistency Acceptance](acceptance/pre-stage3/PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md)

## 审计与决策

只读审计、决策账本、弃用账本与合同校正。

- [P0 Prompt Identity Reconciliation](audits/P0_PROMPT_IDENTITY_RECONCILIATION.md)
- [P0 Step F Lightweight Audit Decisions](audits/P0_STEP_F_LIGHTWEIGHT_AUDIT_DECISIONS.md)
- [P1-B0 Pricing Consumer Audit Decisions](audits/P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md)
- [P1 模型运行时审计决策账本](audits/P1_MODEL_RUNTIME_AUDIT_DECISIONS.md)
- [P1/P4 Frozen-Contract Reconciliation](audits/P1_P4_FROZEN_CONTRACT_RECONCILIATION.md)
- [Pre-Stage-3 Deprecation Ledger](audits/PRE_STAGE3_DEPRECATION_LEDGER.md)

## 实施历史

稳定化过程、迁移步骤与变更历史。

- [变更记录](history/CHANGELOG.md)
- [P0 Step C: Dual Testbench Generation Profiles](history/P0_DUAL_GENERATION_PROFILES.md)
- [P0 生成与修复稳定化执行计划](history/P0_GENERATION_REPAIR_STABILIZATION_PLAN.md)
- [P0 Step A: Heuristic Authority Removal](history/P0_HEURISTIC_AUTHORITY_REMOVAL.md)
- [P0 Step B: One-Way Hidden Evaluation Boundary](history/P0_HIDDEN_BOUNDARY_CORRECTION.md)
- [P0 Step E: Repair Budget Parameterization](history/P0_REPAIR_BUDGET_PARAMETERIZATION.md)
- [P0 Step D: Testbench/Stub Prompt and Error Ownership](history/P0_TESTBENCH_STUB_PROMPT_REFINEMENT.md)

## 技术参考

其他长期保留的设计、接口与技术说明。

- [AgRefactor++ Next-Chat Handoff](reference/NEXT_CHAT_HANDOFF.md)
- [P0 Testbench Repair Retry Feedback](reference/P0_TESTBENCH_REPAIR_RETRY_FEEDBACK.md)
- [Pre-Stage-3 Bridge](reference/PRE_STAGE3_BRIDGE.md)

## 目录约定

- `guides/` 面向使用者；
- `roadmap/` 保存当前状态、架构路线和阶段设计；
- `acceptance/` 保存可复核的阶段验收证据；
- `audits/` 保存审计、决策与兼容/弃用账本；
- `history/` 保存已经完成的实施过程，不作为当前执行指针；
- `reference/` 保存其余长期技术参考。
