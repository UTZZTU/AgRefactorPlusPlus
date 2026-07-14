# Stage 2 — Structured Evidence Loop

## 原始范围

通用 VitisFeedbackParser、证据状态机、阶段 Prompt、Testbench Reliability 与多类型 kernel smoke。

## 已完成：Testbench Reliability 核心

- compile/link preflight 与结构化 evidence；
- testbench/candidate ownership；
- 私有 file-scope global 依赖门禁；
- ABI/linkage diagnosis；
- bounded testbench-only model repair；
- 保护公共调用、宏、测试与检查的 contract；
- 空/未修改/provider error 使用剩余 repair budget；
- repair JSON artifact 与统一 usage；
- 接受样例中的 public-interface process isolation；
- 110 个确定性测试；
- 一个统一 CLI + DeepSeek + Vitis 的真实状态型 kernel 验收。

详见 [`stage2_acceptance.md`](stage2_acceptance.md)。该验收不代表所有 kernel/interface 的普适支持。

## 已知局限

- 私有依赖门禁是保守检测，不是完整证明；
- process isolation 目前由 prompt/model 生成，尚非确定性策略；
- POSIX fork 有 host 限制；
- test semantic preservation 尚无形式证明；
- 真实 kernel 多样性不足；
- 工具调用与 AutoGen 调用统计仍不完整。

## 剩余工作

1. general feedback classes：input/config、compile、public test、csim mismatch/crash/timeout、csynth unsupported、unknown bound、II/dependency、memory-port、timing、resource、tool failure；
2. 状态机：INPUT_CHECK、COMPILE_CHECK、PUBLIC_TEST、CSIM、CSYNTH、READY_FOR_OPTIMIZATION、STOP；
3. Prompt builder：shared contract + stage + effective TargetProfile + model family + evidence + gated Memory + output contract；
4. smoke matrix：array map、reduction、stencil、multi-output、`ap_int`/struct、`hls::stream`、stateful；
5. 文档与复现命令同步。

## 完成标准

只有 general parser、状态机、layered Prompt、多类型 smoke 与文档全部完成后，整个 Stage 2 才能关闭。
