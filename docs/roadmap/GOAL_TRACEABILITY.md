# AgRefactor++ Goal Traceability

本文只保留当前目标追踪，不复制每个历史 package 的逐提交状态。历史证据由 acceptance、audit 和 history 文件承担。

## 当前总体状态

```text
Stage 0=baseline retained
Stage 1=closed
Stage 2=closed
Pre-Stage-3=closed
Stage 3 planning=frozen
Stage 3 implementation=not started
Stage 4=not started
Stage 5=not started
Stage 6=not started
```

## 核心目标追踪

| 核心目标 | 当前实现 | 仍缺内容 | 目标阶段 | 下一证据 |
|---|---|---|---|---|
| TargetProfile | Vitis 2023.2 committed profile、executable/settings、part、clock、compile flags、Tcl、parser identity、resource schema、per-field provenance | 多版本、更多设备/platform、版本特定 parser | Stage 5 扩展 | 至少一组真实 source/target 版本矩阵 |
| 双模式版本处理 | `refactor/optimize/full` 数据结构预留；普通 source-only refactor 已实现 | migrate mode、SourceProfile、source baseline、migration report | Stage 5 | 一组真实旧版→目标版迁移 |
| Model API Registry | Modern/Legacy/repair 统一 typed runtime；DeepSeek 真实 smoke；固定模型路径 | authorized auto pool、具体部署 reasoning mapping | 后续 hardening | 用户授权模型池与选择证据 |
| 分层 Prompt | Shared builder、Target/model/evidence/scope/output layers，Candidate/Testbench consumers | Stage 3 optimization hypothesis Prompt | Stage 3 | optimization prompt identity |
| 结构化反馈与状态机 | Preflight、CSYNTH、Public/Hidden、repair、owner、next action、Hidden suppression | Stage 3 PPA/optimization decision evidence | Stage 3 | candidate decision state tests |
| Multi-type ground truth | 7 baselines、7/7 full chains、9/9 fault matrix、16/16 labels | 更广 kernel/版本/设备统计 | Stage 6 | 固定 benchmark 扩展 |
| 安全三级优化器 | Legacy `opt.simple_iter` baseline；Stage 3 合同已冻结 | candidate lineage、hypothesis、三层策略、checkpoint、rollback、cache、best_correct | Stage 3 | deterministic candidate-state package |
| Memory Applicability Gate | Legacy RAG 正负 trial 可作为 baseline | schema、score、abstention、off/gated/always | Stage 4 | 负迁移和弃权实验 |
| BudgetManager | LLM/Tool/Compile/CSIM/CSYNTH/wall-time 硬控制；Token/Cost observed-only | Stage 3 candidate停止和 best_correct fallback；未来 cosim | Stage 3/后续 | budget exhaustion returns best_correct |
| 版本迁移 | 长期目标保留 | 真实 source→target 修复、验证、优化和报告 | Stage 5 | migration acceptance |
| 论文评测 | 真实工具证据和审计基础已具备 | safe optimizer 与 simple_iter、公平预算、多 kernel 重复实验、消融 | Stage 6 | 固定评测协议 |

## 防止概念偷换

```text
TargetProfile 一次真实运行成功
≠ 任意版本支持

TaskSpec 有 version 字段
≠ 真实版本迁移

Legacy RAG 可检索
≠ Memory Applicability Gate

simple_iter 可循环
≠ 安全三级优化器

一次真实模型 smoke
≠ 稳定修复成功率

1500+ deterministic tests
≠ 同数量真实 kernel

一次 PPA 改善
≠ 稳定优化收益
```

## Stage 3 当前追踪

### 已冻结

- baseline qualification；
- CandidateRecord 和 HypothesisRecord；
- `best_correct`、feasibility 和 PPA 比较边界；
- candidate lineage；
- checkpoint/rollback；
- validation cache identity；
-三级状态机；
-物理硬预算与策略上限的关系；
- optimize/full 语义；
- artifact schema；
-分包顺序和验收矩阵。

详细定义见 [STAGE3_IMPLEMENTATION_CONTRACT.md](STAGE3_IMPLEMENTATION_CONTRACT.md)。

### 尚未开始实现

```text
candidate state package
optimizer runtime
model hypothesis generation
three-level strategy
optimize/full adapter
real optimization acceptance
```

## 完成声明检查表

任何未来“已完成”声明必须同时回答：

1. 数据结构是否存在？
2. 是否接入真实主流程？
3. 是否控制真实工具行为？
4. 是否有失败路径和预算测试？
5. 是否有真实端到端 evidence？
6. artifact 是否足以复现？
7. 是否明确不能外推的边界？
