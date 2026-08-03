
# CLI 参数参考

本文档描述当前普通 source-only CLI 的正式参数合同。

## 顶层命令

| 命令 | 定位 |
|---|---|
| `validate-task` | 校验并规范化 TaskSpec JSON；不调用模型或工具 |
| `run` | 高级复现、dry-run 和兼容迁移入口 |
| `refactor` | 正式 source-only refactor 入口 |
| `optimize` | 正式 Stage 3 safe-v1 优化入口 |
| `full` | refactor correctness accepted 后进入 Stage 3 safe-v1 |

## 普通命令格式

```bash
python -m agrefactor.cli refactor SOURCE --top TOP --model MODEL
python -m agrefactor.cli optimize CANDIDATE --top TOP --model MODEL \
  --reference-source ORIGINAL --reference-top ORIGINAL_TOP \
  --public-test PUBLIC.cpp --hidden-test HIDDEN.cpp
python -m agrefactor.cli full SOURCE --top TOP --model MODEL
```

Direct `optimize` 不生成自己的 correctness oracle，必须提供独立 reference 和至少一个 Public/Hidden suite。`full` 从 accepted refactor phase 获取同一组 typed evidence；refactor 未 accepted 时不会进入优化。

## 输入、模型与 Target

| 参数 | 默认 | 可选值/范围 | 说明 |
|---|---|---|---|
| `SOURCE` | 必填 | 已存在的 C/C++ 文件 | 输入源码 |
| `--top` | 必填 | 非空字符串 | 原始 top function |
| `--model` | 必填 | 非空模型 ID | 固定模型 |
| `--model-family` | 自动推断 | 已注册 Profile | 显式模型家族 |
| `--base-url` | 模型默认 | URL 字符串 | Provider endpoint |
| `--api-key-env` | 模型默认 | 环境变量名 | 凭证来源 |
| `--reasoning-effort` | `medium` | `low/medium/high` | 统一请求语义；真实映射暂按当前 Profile |
| `--target` | `vitis-2023.2-default` | 已提交 TargetProfile | 目标工具合同 |
| `--part` | Profile 默认 | 非空字符串 | 器件覆盖 |
| `--clock-period` | Profile 默认 | 有限正数 | ns |
| `--replace-compile-flag` | Profile 默认 | 可重复字符串 | 替换完整 compile flag 列表 |
| `--reference-source` | direct optimize 必填 | 独立 C/C++ 文件 | correctness reference；不得与 candidate 同一路径 |
| `--reference-top` | `--top` | 非空字符串 | reference top function |
| `--optimizer-profile` | `safe-v1` | `safe-v1` | 冻结 2/2/3 策略 |
| `--optimization-objective` | `latency` | `latency` | v1 唯一正式 objective |

对于未验证统一 effort 参数的 Generic OpenAI-compatible endpoint，隐式默认 `medium` 不会阻断模型；具体模型/部署映射属于后续工作。用户显式传入不受支持的 effort 时，当前 typed Profile 仍可拒绝。

## Testbench 来源与生成

| 参数 | 默认 | 范围 | 说明 |
|---|---:|---:|---|
| `--public-tests` | `auto` | `auto` | 无 provided Public 时自动生成 |
| `--public-test FILE` | 无 | 可重复 | 提供 Public suites |
| `--hidden-tests` | `auto` | `auto/none` | 自动生成或关闭 Hidden |
| `--hidden-test FILE` | 无 | 可重复 | 提供 Hidden suites |
| `--test-generation-profile` | `lightweight` | `lightweight/coverage-enhanced` | 生成策略 |
| `--public-coverage-rounds` | 3 | 1..20 | coverage-enhanced |
| `--hidden-coverage-rounds` | 6 | 1..20 | coverage-enhanced |
| `--public-generation-trajectories` | 3 | 1..20 | coverage-enhanced |
| `--hidden-generation-trajectories` | 3 | 1..20 | coverage-enhanced |

`lightweight` 的四项实际值始终为 1。Public coverage target=80%、Hidden coverage target=90% 继续由内部策略固定。

旧 `--test-generation-trajectories` 作为隐藏兼容别名保留；不能和两个独立 trajectory 参数组合。

## Repair 与 timeout

| 参数 | 默认 | 范围/上限 | 说明 |
|---|---:|---:|---|
| `--max-testbench-repairs` | 3 | 1..20 | 自动 Public Testbench repair |
| `--max-candidate-repairs` | 3 | 1..20 | Candidate repair |
| `--csim-timeout-s` | 120 | 1..600 | 单次 CSIM |
| `--csynth-timeout-s` | 600 | 1..3600 | 单次 CSYNTH |

高级 `run --repair-aware` 的 `--csim-timelimit` 与 `--csynth-timelimit` 使用相同默认值和上限。

## Hard budgets

| 参数 | 默认 | 安全上限 |
|---|---:|---:|
| `--max-llm-calls` | 64 | 256 |
| `--max-tool-calls` | 128 | 512 |
| `--max-compile-calls` | 48 | 192 |
| `--max-csim-calls` | 32 | 128 |
| `--max-csynth-calls` | 16 | 64 |
| `--max-wall-time-s` | 7200 | 14400 |

用户值可以低于、等于或高于默认值，但不能超过安全上限。`0` 是合法硬预算，表示相关操作无法启动。

## Observed-only budgets

| 参数 | 默认 | 语义 |
|---|---|---|
| `--token-budget` | 未设置 | 仅记录比较；does not stop execution |
| `--cost-budget` | 未设置 | 仅记录比较；does not stop execution |

Cost 币种来自已验证 pricing snapshot；没有可用币种时不能填写 `--cost-budget`。

## 输出与标识

| 参数 | 默认 | 说明 |
|---|---|---|
| `--output-dir` | 自动 | 精确持久 artifact 目录；必须不存在或为空 |
| `--run-id` | 自动 UUID | 稳定运行 ID |
| `--json` | false | 机器可读摘要 |
| `--verbose` | false | 阶段级进度 |
| `--debug` | false | 安全诊断 tee |

`--json/--verbose/--debug` 互斥。

<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:BEGIN -->
## Pre-Stage-4 planned parameter contract

The future parameter changes are frozen, but are **not current behavior** until
their implementation packages are accepted. See
[`PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`](../roadmap/PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md).

Planned normal defaults and controls include:

```text
--model deepseek-v4-flash
--api-key-env ENV_NAME
--reasoning-effort auto|medium|high
--optimizer-profile safe-v1|dynamic-v1
--max-optimization-rounds N
--max-executed-candidates N
--hypotheses-per-round N
--cosim-policy off|required
--max-cosim-calls N
--cosim-timeout-s N
--budget-profile refactor-default|optimize-default|full-default
```

A visible parameter must be consumed and recorded in Execution Identity, or be
hidden/rejected for that command.
<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:END -->

## 当前未开放

模型采样参数、完整 TargetProfile 覆盖、资源限制、Memory/RAG、自动模型路由、level-specific rounds/beam/cache path、迁移参数和 cosim budget 当前不属于普通 source-only CLI。详见 [产品能力待办](../roadmap/PRODUCT_CAPABILITY_BACKLOG.md)。
