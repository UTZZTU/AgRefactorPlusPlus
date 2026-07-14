# AgRefactor++ Goal Traceability

> 本文档用于回答三个问题：最初目标是否还在、它在哪个 Stage 实现、目前究竟完成到什么程度。

## 1. 核心目标追踪表

| 目标 | 主要 Stage | 当前实现 | 仍缺内容 | 完成证据 |
|---|---|---|---|---|
| TargetProfile | Stage 1 | 数据结构、TaskSpec 引用、CLI 展示 | settings/tool/part/clock/flags/Tcl/parser 真正下传，effective profile artifact | 真实 Vitis run 使用 profile 且可审计 |
| 双模式 | Stage 5 | 当前仅 refactor/optimize/full 数据结构预留 | `migrate`、source profile、source baseline、migration report | 至少一组真实 source→target |
| Model API Registry | Stage 1 | Model Registry、OpenAI-compatible Provider、DeepSeek 验证 | 更多 provider profile、用户授权模型池配置 | 不改主流程即可接入授权模型 |
| 分层 Prompt | Stage 2 | Testbench repair prompt 与证据注入是局部实现 | 共享 Prompt Builder、阶段层、TargetProfile 层、family adapter | 多模型/多阶段真实工具结果 |
| 结构化反馈/状态机 | Stage 2 | Testbench preflight evidence | general parser、全流程 state machine | 多类错误正确分类并驱动动作 |
| 安全三级优化器 | Stage 3 | 仅有 legacy `simple_iter` baseline | hypothesis、3 levels、checkpoint、rollback、cache、best_correct | 多 kernel 与 baseline 对照 |
| Memory Applicability Gate | Stage 4 | 当前 RAG 可检索正负 trial | schema、score、abstention、off/gated/always | 负迁移与弃权实验 |
| BudgetManager | Stage 1/3 | token/cost core、repair calls、wall time | compile/test/csim/csynth/cosim 计数与硬限制 | 超预算前停止并返回 best_correct |

## 2. 防止概念偷换

以下等式均不成立：

```text
TargetProfile 类存在 ≠ TargetProfile 已驱动真实工具
RAG 检索存在 ≠ Memory Applicability Gate
simple_iter 能循环 ≠ 安全三级优化器
TaskSpec 有 version 字段 ≠ 版本迁移
110 个测试通过 ≠ 110 个真实 kernel
一个 kernel 成功 ≠ 普适支持
一次 PPA 改善 ≠ 稳定优化能力
```

## 3. 完成声明模板

对外声明某模块完成前，至少回答：

1. 数据结构是否存在？
2. 是否接入真实主流程？
3. 是否控制实际工具行为？
4. 是否有失败路径测试？
5. 是否有真实端到端证据？
6. 是否覆盖超过一个构造样例？
7. 文档是否同步？
8. 当前限制是否明确？

任何一项为否，应使用“部分完成”“核心完成”或“尚未验证”，不得写“全面支持”。

## 4. 当前下一任务

当前不是 Stage 3。下一任务是：

```text
TaskSpec/TargetProfile
→ LegacyRefactorAdapter
→ flow.new / csynth tooling
→ actual Vitis command and Tcl
```

先使 target settings、part、clock 与 flags 真正生效并持久化证据。
