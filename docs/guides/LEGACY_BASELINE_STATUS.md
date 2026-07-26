# Legacy Baseline Status

本文记录原始 AgRefactor 或兼容路径中仍有研究、复现或对照价值的模块。它们不是当前普通产品入口。

## 1. `flow.new`

状态：**保留并被兼容适配层消费**。

用途：

- 原始单 kernel 生成/重构 baseline；
- 当前 source-only bootstrap 的初始生成能力；
- 与正式 Stage 2 validation backend 分离：Legacy success 不能作为最终裁决。

因此当前不能删除。

## 2. Legacy RAG

状态：**已做基础复现，保留为 Stage 4 baseline**。

已验证过本地知识库创建、正负 trial 写入和相似经验检索。它没有 Applicability Gate、可靠 abstention 或正式负迁移控制，因此不能宣称等于 Stage 4。

## 3. `flow.parallel_kernel`

状态：**部分验证**。

已验证 JSON kernel 列表、隔离工作目录、并发调度和结构化汇总。有限样本成功率不是 benchmark，当前不属于 Stage 3 实现关键路径。

## 4. `opt.simple_iter`

状态：**已验证的线性优化 baseline**。

完成过真实多轮 DeepSeek/Vitis 优化和 best-design 保存。它缺少 Stage 3 要求的：

- correctness-first candidate state；
- hypothesis lineage；
- checkpoint/rollback/cache；
- `best_correct` 保护；
-三级策略；
-统一物理预算和公平对照协议。

Stage 3/6 仍需要它作为 baseline，因此不能删除，但不应成为普通 `optimize` 产品实现。

## 5. Advanced `run task.json`

状态：**兼容保留**。

用途：

- TaskSpec 验证和高级复现；
-旧实验迁移；
- formal repair-aware backend 调试。

隐藏的 `--legacy`、`--repair-aware` 不属于普通用户界面。只有在正式 Stage 3/后续 adapters 稳定且旧实验有明确迁移工具后，才重新评估删除。

## 6. HeteroRefactor

状态：**暂停**。

ROSE/EDG 外部依赖阻塞，且不是当前主流程必要依赖。当前不投入修复资源，但在确认论文对照和代码 consumer 之前不直接删除。

## 7. Remote HLS / MCP

状态：**未纳入稳定主验证**。

不作为 Stage 3 blocker。后续应通过活跃 consumer 和评测价值审计决定保留、重新实现或删除。

## 8. 删除原则

Legacy 内容只有同时满足以下条件才可删除：

```text
无活跃 runtime consumer
+ 无高级复现合同
+ 无论文/评测 baseline 价值
+ 有迁移或替代证据
+ 完整回归通过
```
