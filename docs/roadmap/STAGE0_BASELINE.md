# Stage 0 — Reproduction Baseline

## 目标

冻结公开 AgRefactor 与当前环境中真实可运行的基线，严格区分“存在代码”和“完成验证”。

## 已验证环境与能力

- Ubuntu 22.04、Python 3.10、Vitis HLS 2023.2；
- OpenAI-compatible DeepSeek V4 Flash/Pro；
- `flow.new` 单 kernel 主流程；
- RAG reset/write/retrieve；
- `flow.parallel_kernel` 调度/隔离/汇总的部分验证；
- `opt.simple_iter` 多轮 baseline 与 best-design 保存；
- 基础 token/cost 汇总、日志、上下文、代码与报告保存。

## 局限

- 真实重复样例集中在 DFS `process_top`；
- parallel 成功率不是正式 benchmark 结论；
- `simple_iter` 不是最终安全优化器；
- HeteroRefactor 因外部 ROSE/EDG 不进入关键路径；
- coverage/hidden TB 未纳入稳定基线；
- 跨版本迁移未实现。

## 相关文档

- [`REPRODUCTION_STATUS.md`](../guides/REPRODUCTION_STATUS.md)
- [`USAGE.md`](../guides/USAGE.md)
- [`ENVIRONMENT.md`](../guides/ENVIRONMENT.md)

Stage 0 视为“复现基线基本完成”，但后续不得把未验证代码路径补写成已验证能力。
