# CLI 参数参考

本文描述当前普通 source-only 产品入口的正式参数合同。高级 `run` 入口用于复现和兼容迁移，其 legacy 参数语义不等于普通产品入口。

## 顶层命令

| 命令 | 定位 |
|---|---|
| `validate-task` | 校验并规范化 TaskSpec JSON；不调用模型或工具 |
| `run` | 高级复现、dry-run 和兼容迁移入口 |
| `refactor` | 正式 source-only 重构入口 |
| `optimize` | 正式 Stage 3 `safe-v1` 优化入口 |
| `full` | 完成并接受 Refactor 后进入 `safe-v1` Optimize |

## 普通命令格式

默认模型可以省略：

```bash
python -m agrefactor.cli refactor SOURCE --top TOP
python -m agrefactor.cli optimize CANDIDATE --top TOP \
  --reference-source ORIGINAL --reference-top ORIGINAL_TOP \
  --public-test PUBLIC.cpp --hidden-test HIDDEN.cpp
python -m agrefactor.cli full SOURCE --top TOP
```

显式模型覆盖仍可用：

```bash
python -m agrefactor.cli full SOURCE --top TOP \
  --model MODEL_ID \
  --model-family FAMILY \
  --base-url URL \
  --api-key-env ENV_NAME
```

Direct `optimize` 不生成自己的 correctness oracle，必须提供独立 reference 和至少一个 Public/Hidden suite。`full` 仅在 Refactor accepted 后使用 `refactor/final_candidate.cpp` 作为 Optimize baseline；不会直接优化未被接受的原始非综合源码。

## 输入、模型与 Target

| 参数 | 当前默认 | 可选值/范围 | 说明 |
|---|---|---|---|
| `SOURCE` | 必填 | 已存在 C/C++ 文件 | 输入源码 |
| `--top` | 必填 | 非空标识符 | 原始 top function |
| `--model` | `deepseek-v4-flash` | 非空模型 ID | 精确 fixed model；不自动路由 |
| `--model-family` | 静态推断 | 已注册 Profile | 显式模型家族覆盖 |
| `--base-url` | 模型默认 | URL | Provider endpoint 覆盖 |
| `--api-key-env` | `DEEPSEEK_API_KEY`（默认模型） | 环境变量名 | 只指定凭证变量名，不接受 raw key |
| `--reasoning-effort` | `auto` | `auto/medium/high` | `auto` 按调用角色选择 project medium/high |
| `--target` | `vitis-2023.2-default` | 已提交 TargetProfile | 目标工具合同 |
| `--part` | Profile 默认 | 非空字符串 | 器件覆盖 |
| `--clock-period` | Profile 默认 | 有限正数 | ns |
| `--replace-compile-flag` | Profile 默认 | 可重复 | 替换完整 compile flag 列表 |
| `--reference-source` | direct optimize 必填 | 独立 C/C++ 文件 | correctness reference，不得与 candidate 同一路径 |
| `--reference-top` | `--top` | 非空字符串 | reference top function |
| `--optimizer-profile` | `safe-v1` | `safe-v1` | `dynamic-v1` 尚未实现 |
| `--optimization-objective` | `latency` | `latency` | 当前唯一正式 objective |

### `.env` 与凭证

普通产品入口从**调用命令时的工作目录**读取 `.env`，并使用 `override=False`：

```text
已导出的进程变量 > 当前工作目录 .env > 缺失凭证
```

缺失所选环境变量会在 provider launch 前 typed reject。Artifacts 只可记录环境变量名和 presence，不可记录 secret 值或 `.env` 内容。

### Thinking 与 reasoning

`auto` 的 medium roles 包括非综合结构识别、Public/Hidden Testbench 生成、去重和简单分类；Refactor planning/source、Testbench/Candidate repair、Bottleneck、action selection 和优化 Candidate generation 使用 project high。

对于 `deepseek-v4-flash`：

```text
project medium → provider high
project high   → provider max
Thinking       → enabled
```

其他 family 使用自身 typed map/omit/reject policy；不猜测不受支持的 provider 字段。普通 CLI 不接受 `low`；legacy `run` 的兼容参数不改变这一普通产品合同。

## Testbench 来源与生成

| 参数 | 默认 | 范围 | 说明 |
|---|---:|---:|---|
| `--public-tests` | `auto` | `auto` | 无 provided Public 时自动生成 |
| `--public-test FILE` | 无 | 可重复 | provided Public suites |
| `--hidden-tests` | `auto` | `auto/none` | 自动生成或关闭 Hidden |
| `--hidden-test FILE` | 无 | 可重复 | provided Hidden suites |
| `--test-generation-profile` | `lightweight` | `lightweight/coverage-enhanced` | 生成策略 |
| `--public-coverage-rounds` | 3 | 1..20 | 仅 coverage-enhanced |
| `--hidden-coverage-rounds` | 6 | 1..20 | 仅 coverage-enhanced |
| `--public-generation-trajectories` | 3 | 1..20 | 仅 coverage-enhanced |
| `--hidden-generation-trajectories` | 3 | 1..20 | 仅 coverage-enhanced |

`lightweight` 的实际 generation counts 固定为 1。旧 `--test-generation-trajectories` 仅作为隐藏兼容别名保留。

## Repair、验证与 timeout

| 参数 | 默认 | 范围/上限 | 说明 |
|---|---:|---:|---|
| `--max-testbench-repairs` | 3 | 1..20 | Public Testbench repair |
| `--max-candidate-repairs` | 3 | 1..20 | Refactor Candidate repair |
| `--csim-timeout-s` | 120 | 1..600 | 单次 CSIM |
| `--csynth-timeout-s` | 600 | 1..3600 | 单次 CSYNTH |
| `--cosim-timeout-s` | 900 | 1..7200 | 单次 Public RTL COSIM |
| `--cosim-policy` | `required` | `required/off` | `off` 仅用于开发，表示未完成完整硬件验证 |

当前统一资格顺序为：

```text
Source integrity
→ typed Preflight
→ Public native Vitis CSIM
→ CSYNTH
→ Public RTL COSIM
→ Hidden differential
→ final decision
```

## 当前统一 hard budgets

P4-0F 尚未引入 mode-specific profile；当前三个普通命令仍共享 `source-run-default`：

| 参数 | 系统默认 | 安全上限 |
|---|---:|---:|
| `--max-llm-calls` | 64 | 256 |
| `--max-tool-calls` | 128 | 512 |
| `--max-compile-calls` | 48 | 192 |
| `--max-csim-calls` | 32 | 128 |
| `--max-csynth-calls` | 16 | 64 |
| `--max-cosim-calls` | 16 | 64 |
| `--max-wall-time-s` | 7200 | 14400 |

用户值可低于或高于默认值，但不能超过安全上限；`0` 是合法硬限制。P4-0F 将基于真实测量决定 `refactor-default/optimize-default/full-default` 和 Full Optimize reserves，当前文档不预先虚构这些值。

## Observed-only budgets

| 参数 | 默认 | 语义 |
|---|---|---|
| `--token-budget` | 未设置 | 仅观察和报告，不阻止已完成调用 |
| `--cost-budget` | 未设置 | 仅观察和报告；币种来自 pricing snapshot |

## 输出与标识

| 参数 | 默认 | 说明 |
|---|---|---|
| `--output-dir` | 自动 | 必须不存在或为空；持久 artifacts 根目录 |
| `--run-id` | 自动 UUID | 稳定运行 ID |
| `--json` | false | 单一机器可读摘要 |
| `--verbose` | false | 阶段级进度 |
| `--debug` | false | 安全诊断 tee；完整日志仍留在 artifacts |

`--json/--verbose/--debug` 互斥。模型、Target、测试来源、timeout、预算、COSIM policy 和有效参数必须进入 Execution Identity；未被命令真实消费的参数必须隐藏或显式拒绝。

## 当前未开放

- `dynamic-v1` 及其 rounds/candidates/hypotheses 控制；
- mode-specific budget profiles 和 Full Optimize reserves；
- authorized auto model routing；
- 每个内部角色单独选择模型；
- 一般 sampling 参数；
- Stage 4 Memory Applicability Gate；
- Stage 5 SourceProfile/版本迁移 runtime。
