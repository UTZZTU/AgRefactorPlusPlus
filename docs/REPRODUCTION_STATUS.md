# 复现状态与已验证功能

本文档只记录已经在当前实验环境中实际运行过的功能。仓库中“存在代码”不等于“已经完成复现”，未充分验证的模块会明确标注。

更新范围：2026 年 7 月。

## 状态定义

| 状态 | 含义 |
|---|---|
| 已验证 | 已完成至少一次端到端运行，并得到预期输出 |
| 部分验证 | 框架和主要步骤可运行，但样本规模或稳定性仍不足 |
| 暂未验证 | 仓库中存在相关代码，但尚未完成可靠复现 |
| 暂停 | 已尝试复现，但受外部依赖等因素阻塞 |

## 验证环境

| 项目 | 当前验证环境 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| HLS 工具 | Vitis HLS 2023.2 |
| 主要模型后端 | DeepSeek V4 Flash / Pro |
| API 类型 | OpenAI-compatible |
| 最小样例 | `src/heterorefactor/dfs/kernel.cpp` |
| 原 top function | `process_top` |
| 目标 top function | `process_top_hls` |

## 1. 单 kernel 重构：已验证

入口：

```text
python -m flow.new
```

已验证流程包括：

1. 读取原始 C/C++ kernel。
2. 生成 testbench。
3. 识别不可综合或不适合 HLS 的结构。
4. 生成重构计划。
5. 生成重构代码。
6. 运行 Vitis HLS csim 与 csynth。
7. 根据错误信息进行有限次数的自动修复。
8. 保存上下文、代码、日志和综合报告。

DeepSeek V4 Flash 与 Pro 都完成过 DFS 样例端到端运行。Pro 验证中曾出现 `RETRY_COUNT:0`，说明该次运行无需额外修复便通过主流程。

## 2. DeepSeek 与 AG2/AutoGen 兼容：已验证

已完成的适配包括：

- 使用 OpenAI-compatible 配置接入 DeepSeek。
- 适配 AG2/AutoGen 新版 `LLMConfig` 初始化方式。
- 将不兼容的 Pydantic structured output 转换为 JSON mode。
- 增大 thinking 模型的 token 预算，减少只有 reasoning、没有最终 content 的情况。
- 增加模型价格元数据，支持基础 token/cost 汇总。
- 增强 identifier JSON 解析，兼容对象和裸列表输出。

## 3. Token / Cost 汇总：已验证

`flow.new` 在运行开始时重置本次 usage registry，在成功或失败结束时打印 `Token / Cost Summary`。

汇总优先使用 AG2 的统一 usage 接口；聚合失败时，回退到各 agent 的 usage 信息。该功能已在 DeepSeek Flash 和 Pro 的重构实验中出现正常输出。

成本结果只用于实验记录和粗略比较。模型服务价格可能变化，不应把仓库内默认 price 当成长期准确报价。

## 4. RAG 长期记忆：已验证

相关入口参数：

```text
--enable_rag
--enable_rag_update
--reset_knowledge_db
--knowledge_db_path
```

当前已验证：

- 创建和重置本地知识数据库。
- 记录每轮综合/仿真的 trial outcome。
- 同时保存成功与失败尝试，而不是只保存成功样本。
- 在识别和规划阶段检索相近经验。
- 对模型返回的多种 JSON 形式进行更鲁棒的解析。

说明：RAG 是可选功能。默认运行不传 `--enable_rag` 时，不进行经验检索。

## 5. 多 kernel 并行实验：部分验证

入口：

```text
python -m flow.parallel_kernel
```

已验证的框架能力：

- 从 JSON 文件加载 kernel 列表。
- 为不同 kernel 和重复实验创建隔离工作目录。
- 并发调用 `flow.new`。
- 统计总运行数、成功率、执行时间和 retry 次数。
- 保存结构化 JSON 汇总结果。

当前局限：

- 小规模实验中框架本身能够完成调度与汇总。
- 有限样本曾出现约一半端到端成功的情况，失败主要与模型生成 testbench 或代码的不稳定性有关。
- 该数字不是正式 benchmark 结论，不能代表项目总体成功率。
- 在开展大规模实验前，需要固定 kernel 集合、模型参数、随机性、并发数和评价口径。

## 6. `opt.simple_iter` 性能优化：已验证

入口：

```text
python -m opt.simple_iter.main
```

该流程会：

1. 让模型生成或修改 HLS 代码。
2. 运行 csynth。
3. 运行 testbench 检查功能正确性。
4. 解析 Vitis HLS 报告中的延迟与资源使用。
5. 把综合反馈发送给模型继续优化。
6. 在资源约束内选择并保存当前最佳设计。

已完成一次 DeepSeek V4 Pro 的 8 轮优化实验，并成功生成 `best_design` 记录。该结果证明优化闭环可以运行，但单次实验不能作为普适的 PPA 提升结论。

## 7. 测试强度相关功能：暂未纳入主验证基线

`flow.new` 和 `flow.parallel_kernel` 中已有以下可选参数：

```text
--enable_tb_coverage_loop
--enable_hidden_tb_eval
--use_cached_tb_as_public
```

这些功能用于覆盖率驱动的 public testbench 生成、hidden testbench 评价和缓存复用。代码路径存在，但当前文档不把它们列为稳定主流程；后续应单独记录 kernel 集合、覆盖率工具、缓存方式和评价结果。

## 8. HeteroRefactor：暂停

HeteroRefactor 属于可选外部工具，不是当前 AgRefactor++ 主流程的必要依赖。

实际复现中已经验证：

- 官方 Ubuntu 22.04 Apptainer 容器可以构建。
- 原容器定义缺少 `libltdl-dev`，补齐后 ROSE 可以继续编译。
- 构建最终阻塞在 ROSE 所需 EDG binary 分发服务不可用。

因此当前策略是：

- 不启用 `--hetero_enabled`。
- 保留仓库中的接口和容器定义。
- 优先推进 `flow.new`、RAG、批量实验和 optimization。

## 9. 仍需继续验证

- 更大规模的 kernel 批量复现。
- coverage-optimized public TB 与 hidden TB 评价。
- remote HLS server / MCP 相关流程。
- 不同模型和参数下的稳定性比较。
- 多版本 Vitis HLS 行为差异与迁移规则。
- 固定 benchmark 上的正确率、成功率、延迟和资源指标。

<!-- AGREFPP_STAGE1_STAGE2_STATUS:START -->
## AgRefactor++ Stage 1/2 补充状态

### Stage 1 共享架构

TaskSpec、TargetProfile、Model Registry、OpenAI-compatible Provider、UnifiedRunner、CLI、Budget core、Trace、Legacy Adapter 与已知 usage 合并已验证。TargetProfile 尚未完整控制实际 Vitis settings/part/clock/flags/Tcl，因此应表述为“核心架构完成，真实下传未完成”。

### Stage 2 Testbench Reliability

preflight、ownership、私有 global gate、ABI/linkage、bounded repair、剩余预算重试、artifact、统一 usage、110 个确定性测试与一个真实状态型 kernel E2E 已验证。

详见 [`STAGE2_EVIDENCE_LOOP.md`](STAGE2_EVIDENCE_LOOP.md) 与 [`stage2_acceptance.md`](stage2_acceptance.md)。

### 尚不能宣称

110 个测试不等于 110 个真实 kernel；尚无多类型真实 smoke；general VitisFeedbackParser、完整状态机、正式 Prompt Builder、安全优化器、Memory Gate 与真实版本迁移均未完成。
<!-- AGREFPP_STAGE1_STAGE2_STATUS:END -->

## 文档原则

后续更新时建议始终区分：

1. 原始 AgRefactor 已经提供的代码功能。
2. AgRefactor++ 新增或修改的代码。
3. 当前环境中已经实际复现的功能。
4. 未来研究目标。

这样可以避免把“仓库中存在模块”误写成“已经完成验证”，也避免把单次成功实验写成普遍结论。
