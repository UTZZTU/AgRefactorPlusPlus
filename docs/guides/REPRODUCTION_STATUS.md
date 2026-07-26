# 当前复现与验证状态

本文只描述当前产品入口和当前证据。原始 AgRefactor/Legacy 模块的复现情况已移至 [LEGACY_BASELINE_STATUS.md](LEGACY_BASELINE_STATUS.md)。

## 状态定义

| 状态 | 含义 |
|---|---|
| 已验证 | 至少一次真实端到端运行并有可复核 artifacts |
| 确定性验收 | 通过单元/集成测试，但不等同于真实模型或 Vitis |
| 部分验证 | 框架可运行，但样本、版本或稳定性不足 |
| 未实现 | 只有路线或预留接口 |
| 暂停 | 受外部依赖阻塞且不是当前主线 |

## 当前验证环境

| 项目 | 环境 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| HLS 工具 | Vitis HLS 2023.2 |
| 已真实使用模型 | DeepSeek V4 Flash / Pro；当前产品 smoke 使用 `deepseek-v4-flash` |
| API 类型 | OpenAI-compatible |
| 代表性源码 | `src/heterorefactor/dfs/kernel.cpp` |
| 原 top | `process_top` |
| 默认 TargetProfile | `vitis-2023.2-default` |
| 器件 | `xcu200-fsgd2104-2-e` |

## 1. 普通 source-only refactor：已验证

正式入口：

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash
```

当前真实链：

```text
source-only input
→ model-generated Public Testbench and Candidate
→ Testbench qualification/repair
→ formal Preflight
→ real Vitis HLS 2023.2 CSYNTH
→ Public CSIM
→ Hidden CSIM
→ bounded Candidate repair when legal
→ accepted / trusted terminal result
```

最新 post-CLI smoke：

```text
run_id=post-cli-real-smoke-20260726_192331
status=accepted
csynth=passed
public=passed
hidden=passed
model_calls_observed=true
actual_vitis_version=2023.2
artifact_root=/data/agrefactor_runs/post_cli_real_smoke_20260726_192331/artifacts
```

## 2. 当前确定性回归：已验收

```text
full unittest=1500/1500
```

覆盖：

- CLI 参数合同；
- Model/Target typed resolution；
- Public/Hidden roles 和 provenance；
- Prompt identity；
- Budget prospective block 和 exact-once accounting；
- Preflight/CSYNTH/CSIM handlers；
- Candidate/Testbench repair；
- Execution Identity；
- concise output；
- Hidden suppression；
- acceptance artifact schemas。

这些测试不能冒充真实 Vitis 或真实模型调用。

## 3. TargetProfile 与工具预算：已验证

已真实证明：

- TargetProfile 控制 `vitis-run`、版本、part、clock 和 compile flags；
- requested/actual Vitis version mismatch 在 CSYNTH 前阻断；
- Tool/Compile/CSIM/CSYNTH 预算在真实 launch 前检查并 exact-once consume；
- 真实调用成功、失败、timeout 和 launcher exception 都有确定性语义；
- Execution Identity 记录实际工具版本和 invocation evidence。

## 4. Public/Hidden 与正式 Repair：已验证

已验收：

- 多 Public/Hidden suites；
- Public agent-safe feedback；
- Hidden operator-only evidence 和 fail-fast；
- Hidden 内容不进入模型 Prompt、普通结果或普通 trace；
- Testbench 与 Candidate 的有限 repair；
- unknown failure 不被猜成 candidate failure；
- accepted 必须满足完整 Execution Identity。

## 5. 当前模型范围

真实网络证据集中于 `deepseek-v4-flash`，并有早期 DeepSeek Pro 复现。

静态 Profile 已支持：

```text
DeepSeek
Kimi
GLM
MiniMax
Qwen
Generic OpenAI-compatible
```

除 DeepSeek 的已记录 smoke 外，不应把静态 Profile 存在表述为各模型均完成真实端到端验收。

具体模型/部署 reasoning 映射仍属于后续 hardening。

## 6. Stage 3：规划冻结，尚未实现

已经完成：

- 高层安全三级优化器设计；
- 直接可实施的冻结合同；
- baseline、candidate、best_correct、checkpoint、cache、预算和 artifact 语义；
- 分包顺序和验收矩阵。

尚未实现：

- CandidateState/Checkpoint 运行时代码；
- hypothesis generation；
- Structural/Bottleneck/Pragma 策略；
- optimize/full adapters；
- 真实多 kernel 优化对照。

## 7. 后续阶段

| 能力 | 状态 |
|---|---|
| Stage 4 Memory Applicability Gate | 未实现；Legacy RAG 仅为 baseline |
| Stage 5 版本迁移 | 未实现 |
| authorized auto model pool | 未实现 |
| RTL cosim 主路径和专项预算 | 未实现 |
| 多版本/多器件验证 | 部分基础，未形成正式矩阵 |
| 固定 benchmark 统计评测 | Stage 6 |

## 8. 不能外推的结论

- 单个 DFS smoke 不代表任意 kernel；
- Vitis 2023.2 单环境不代表任意版本；
- 一次真实模型成功不代表稳定成功率；
- 确定性测试数量不代表真实 kernel 数量；
- Legacy `simple_iter` 不代表 Stage 3；
- Legacy RAG 不代表 Stage 4；
- TaskSpec 字段不代表 Stage 5。
