# Pre-Stage-3 产品化与关闭计划

> **状态：** Stage 3 前冻结实施合同  
> **决策日期：** 2026-07-22  
> **权威关系：** `ROADMAP.md` 继续定义长期使命和八项核心能力；本文完整定义进入 Stage 3 前仍必须完成的 P0–P5、Execution Identity、弃用清理和验收顺序。
>
> Stage 2 已关闭，Stage 3 尚未开始。在本文关闭条件满足前，不得写入 `PRE_STAGE3_CLOSED=true`，也不得开始 Stage 3。

## 1. 当前结论与边界

Stage 2 已建立正式验证与有限修复后端：

```text
Candidate
→ Preflight compile/link
→ Vitis HLS CSYNTH
→ Public CSIM
→ Hidden CSIM
→ bounded candidate repair
→ accepted / rejected / blocked / review_required
```

Pre-Stage-3 的真实 Testbench 实验还证明：三个静态启发式硬门禁存在严重误杀。它们已经停用，真实编译、运行、coverage 和 Vitis 综合继续作为最终资格依据。

当前不再扩建 Testbench 大型子系统。剩余主线是把已有研究组件整理成统一、易用、可复现的产品入口，再通过真实 DFS source-only 端到端验收。

## 2. 冻结的用户接口原则

1. 普通用户必须提供：源 C/C++ 文件、`--top`、模型。
2. 普通入口不做 top function 自动猜测，也不存在省略 `--top` 的“最简模式”。
3. 普通用户不需要手写 `task.json`。
4. `TaskSpec` 保留为系统内部规范化合同和可复现实验产物。
5. 未显式指定的参数使用经过验证的默认值。
6. 用户显式参数优先于默认值。
7. 普通用户只看到三种任务命令：

   ```text
   refactor
   optimize
   full
   ```

8. 普通用户不选择 `--legacy` 或 `--repair-aware`。
9. Legacy AgRefactor 代码可以提供可复用的初始生成能力，但其自身成功结果不能代替 Stage 2 正式裁决。
10. Public 和 Hidden 测试来源独立配置，并支持多个 suite。
11. 默认终端输出简洁；完整模型与工具证据写入 artifacts。
12. 每次 accepted 必须带有足够的 Execution Identity，能够复现和审计。

<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:BEGIN -->
## 2.1 三层硬预算与软用量目标

预算必须区分三个概念，不能混为一个“最大值”：

```text
system_default
system_safety_ceiling
user_requested
```

### 硬预算

当前阶段真正参与流程阻断的是可在动作启动前可靠计数的资源：

```text
LLM calls
aggregate tool calls
compile calls
CSIM calls
CSYNTH calls
wall-time boundary checks
```

每项硬预算的有效值按以下规则生成：

```text
用户未指定
→ effective_limit = system_default

用户指定且 0 <= user_requested <= system_safety_ceiling
→ effective_limit = user_requested

用户指定超过 system_safety_ceiling
→ 启动前明确拒绝，不静默截断

system_default 本身必须 <= system_safety_ceiling
```

因此用户既可以把默认值调低，也可以在安全区间内调高。例如系统可以定义
LLM 调用默认值为 3、安全上限为 20；用户可以选择 0–20 内的值。这里的
`3/20` 只是语义示例，实际默认值和安全上限必须根据完整流程预算另行验收后
写入通用 Budget Profile。

用户不能通过 CLI 突破系统安全上限。赛事或实验规则可以映射成通用
Budget Profile，但核心名称、字段和执行逻辑不得绑定具体赛事。

### Token 与 Cost 软预算

当前 Pre-Stage-3 不把总 Token 或估算 Cost 作为流程硬阻断条件。原因是一次
模型调用的最终输出 Token 和费用通常只能在响应返回后确认。

普通用户可以声明：

```text
token_budget
cost_budget
```

它们当前只用于统计、比较、告警和最终展示：

```text
Tokens: actual / user_budget
Estimated cost: actual / user_budget
```

即使实际值超过软预算，当前流程也不因此中断。artifact 必须明确记录：

```text
enforcement = observed_only
blocking = false
```

当前主要通过 `max_llm_calls` 控制模型调用规模。未来只有在形成可靠的
reservation/reconcile 机制并单独验收后，Token/Cost 才能升级为硬预算。
<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:END -->

## 3. 最终普通 CLI 形态

### 3.1 重构

```bash
python -m agrefactor.cli refactor \
  path/to/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash
```

### 3.2 优化

```bash
python -m agrefactor.cli optimize \
  path/to/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash
```

### 3.3 全套流程

```bash
python -m agrefactor.cli full \
  path/to/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash
```

`full` 表示先重构，再优化。只有已经通过正确性和可综合性门禁的 baseline 才允许进入优化。

### 3.4 用户可覆盖常用参数

```bash
python -m agrefactor.cli refactor \
  path/to/kernel.cpp \
  --top process_top \
  --model qwen-code \
  --reasoning-effort high \
  --target vitis-2023.2-default \
  --part xcu200-fsgd2104-2-e \
  --clock-period 4.0 \
  --compile-flag=-DUSER_CONFIG \
  --max-candidate-repairs 2 \
  --public-tests auto \
  --hidden-tests auto
```

稳定公开参数应保持精简。内部实现细节不能变成普通用户必填项。

### 3.5 配置优先级

```text
CLI 显式参数
> 项目配置
> 用户级配置
> Model/Target Profile 默认
> 系统默认
```

最终生效值必须写入运行产物。

### 3.6 用户可选预算

普通用户可以覆盖硬预算，也可以声明 Token/Cost 软预算：

```bash
python -m agrefactor.cli refactor \
  path/to/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --max-llm-calls 8 \
  --max-compile-calls 10 \
  --max-csim-calls 6 \
  --max-csynth-calls 3 \
  --token-budget 50000 \
  --cost-budget 1.00
```

建议的普通 CLI 字段：

```text
硬预算：
--max-llm-calls
--max-tool-calls
--max-compile-calls
--max-csim-calls
--max-csynth-calls
--max-wall-time-s

软预算：
--token-budget
--cost-budget
```

`--cost-budget` 使用所选模型 Profile 的官方定价币种。当前阶段不做动态汇率
换算。若一次运行涉及多币种，必须分币种展示，不能直接错误求和。

`--max-candidate-repairs` 等策略轮数约束与硬物理预算同时生效，但不能替代
run-level `max_llm_calls`、CSIM 或 CSYNTH 预算。

### 3.7 高级复现入口

精确实验和 CI 继续支持：

```bash
python -m agrefactor.cli run task.json
```

这是高级入口，不替代普通 source-based CLI。

## 4. P0–P5 冻结决策

| 项目 | Stage 3 前决定 |
|---|---|
| P0 真实 DFS 端到端 | 必须完成，是最终功能验收门槛；必须从新的 source-only 普通入口进入 Stage 2 正式后端。 |
| P1 Model Profile/Config | 当前实现已知模型静态兼容 Profile；动态识别后置。 |
| P2 Source-only Bootstrap | 包装已有可复用后端，内部构建 TaskSpec、测试计划、目录和正式验证请求，不重写整个系统。 |
| P3 轻量静态门禁 | 不再作为活跃工作项；三个误杀严重的硬门禁保持停用并进入清理审计。 |
| P4 Provided/Auto/Hybrid | Public 与 Hidden 独立选择来源，支持多个 suite，由系统推导总体模式并记录 provenance。 |
| P5 简洁输出 | 实现 default / `--json` / `--verbose` / `--debug` 四级输出。 |

## 5. P1：已知模型兼容与 Prompt 适配基础

<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:BEGIN -->
### 5.0 审计与人工复核依据

Step 0 的只读 consumer 审计与人工复核已经完成。权威决策账本见
[`P1_MODEL_RUNTIME_AUDIT_DECISIONS.md`](P1_MODEL_RUNTIME_AUDIT_DECISIONS.md)。

该账本：

- 保存 F01–F14 自动发现；
- 将 F03 人工复核为 confirmed；
- 修正 F14 的证据定位但保留结论；
- 新增 F15 原生币种与 `cost_usd` 结构冲突；
- 将 P1 拆分为 P1-A 静态兼容、P1-B pricing、P1-C 接线、P1-D 验收；
- 冻结当前唯一活跃实现项为 P1-A。

P1-A 不提前实现 pricing 数值、CLI 迁移、Budget resolver、P5 或 P0。
<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:END -->

### 5.1 与八项核心能力的关系

P1 同时服务于：

```text
核心能力 3：Model API Registry
核心能力 4：分层 Prompt 适配
```

P1 管理模型身份、Provider 兼容、有效请求参数、能力标签和 Prompt Builder 使用的 family/profile 身份。

P1 不为每个模型复制一整套 Prompt。长期目标仍是：

```text
约 90% 公共任务/阶段/证据/输出合同
+ 少量模型家族适配
+ 极少数具体模型覆盖
```

真正的模型权重微调不属于当前 P1；Prompt 适配与模型训练不得混为一谈。

### 5.2 首批模型家族

当前静态 Profile 范围冻结为：

```text
DeepSeek
Kimi
GLM
MiniMax
Qwen
Generic OpenAI-compatible
```

“接口看起来兼容 OpenAI”不能自动等同于“请求参数和响应行为完全兼容”。

### 5.3 Profile 至少表达

```text
逻辑模型名
真实 API model 名
Provider
模型 family
base URL 来源
API key 环境变量名
支持参数
拒绝参数
参数别名
reasoning level 映射/省略/拒绝策略
不同 artifact 的默认参数
最大输出策略
请求 timeout
能力标签
Prompt 适配 profile
验证状态
```

验证状态区分：

```text
declared
deterministically_tested
network_smoke_verified
```

### 5.4 reasoning 统一语义

用户接口统一为：

```text
low
medium
high
```

每个 Profile 自己决定映射、忽略还是拒绝。某个框架里的 `max → xhigh` 不能成为所有模型的全局规则。

### 5.5 现有模型组件的保留原则

当前组件：

```text
ModelSpec
ModelFamilyProfile
ModelRegistry
OpenAICompatibleProvider
```

仅在有真实 consumer 时保留：

- `ModelSpec`：描述一个逻辑模型与模型级默认值；
- `ModelFamilyProfile`：描述家族能力与安全兼容策略；
- `ModelRegistry`：解析用户固定选择的模型、Provider 与 Profile；
- `OpenAICompatibleProvider`：负责传输与响应规范化，不负责模型特有兼容策略。

P1 完成后审计重复参数入口。仍无 consumer 的包装层应合并或弃用，不能因为已经实现就强行保留。

### 5.6 后续动态识别

后续可演进为：

```text
endpoint/model metadata
→ 有界、非破坏性 capability probe
→ verified profile cache
→ static profile fallback
```

动态能力可进一步支持 Prompt 校准和用户授权范围内的模型路由，但不属于 Pre-Stage-3 交付。

默认仍是用户固定模型，系统不得静默换模型。

### 5.7 官方价格元数据与费用估算

P1 必须从每个模型提供方的官方文档或官方控制台公开计费页收集价格，不能把
第三方聚合站作为正式事实来源。

静态 Profile 中的价格不能只保存一个 `input_price/output_price`。至少需要：

```text
model id/version
provider
service region/deployment scope
currency
billing unit
input cache-hit price
input cache-miss price
output price
thinking/non-thinking price difference
context-length or token-tier rules
batch/real-time distinction
temporary discount flag
official source identity
source retrieval date
source effective/update date（若官方提供）
pricing verification status
```

价格验证状态至少区分：

```text
official_verified
official_page_unreadable
not_published
stale
unknown
```

价格可能变化，因此每次 run 的 Execution Identity 必须保存本次实际使用的
pricing snapshot/hash，而不是只引用一个会变化的网页。

费用输出只能称为 `Estimated cost`，不能冒充最终账单。估算规则：

```text
Provider 返回 cache-hit/cache-miss 明细
→ 使用对应官方价格

Provider 只返回总 input/output tokens
→ 使用明确记录的保守规则，并标记 approximate

Profile 没有已验证价格
→ 显示 unavailable / unverified
```

不同币种不在当前阶段自动换算。费用估算服务于 P5 展示和实验分析，不参与
当前流程的硬停止。

## 6. P4：Public/Hidden 测试来源合同

### 6.1 独立来源选择

不设计模糊的：

```bash
--tests hybrid
```

自动生成：

```bash
--public-tests auto
--hidden-tests auto
```

用户提供：

```bash
--public-test public.cpp
--hidden-test hidden.cpp
```

混合：

```bash
--public-test public_user.cpp
--hidden-tests auto
```

或：

```bash
--public-tests auto
--hidden-test hidden_user.cpp
```

### 6.2 多 suite

```bash
--public-test public_basic.cpp
--public-test public_edges.cpp
--hidden-test hidden_operator.cpp
--hidden-test hidden_stress.cpp
```

### 6.3 总体模式由系统推导

| Public | Hidden | 总体模式 |
|---|---|---|
| provided | provided | provided |
| auto | auto | auto |
| provided | auto | hybrid |
| auto | provided | hybrid |

用户不需要手动声明 `hybrid`。

### 6.4 每个 suite 的 provenance

```text
suite id/version
Public 或 Hidden split
source kind: provided/generated/derived/cached
source hash
operator artifact path
生成模型/Profile
Prompt hash
trajectory 和 round
coverage
qualification status
feedback visibility
```

### 6.5 Hidden 隔离

无论来源如何：

- Hidden 源码不进入 Candidate 生成或 repair Prompt；
- Hidden 路径不进入普通输出；
- Hidden 详细诊断保持 operator-only；
- 模型只得到允许公开的脱敏聚合结果；
- 完整 Hidden 证据保存在受保护 artifacts。

## 7. P2：Source-only Bootstrap 与统一执行链

### 7.1 内部自动转换

普通 source 命令内部自动构建并持久化：

```text
normalized TaskSpec
effective model config
effective TargetProfile
test-source plan
work directory
artifact directory
initial candidate-generation request
Stage 2 formal-validation request
```

`task.json` 从普通用户必填输入变成系统自动生成的复现产物。

### 7.2 统一主链

```text
source + explicit --top + 用户选项/默认值
→ 测试来源解析/生成与资格验证
→ 初始 Candidate 生成
→ Stage 2 Preflight
→ real CSYNTH
→ Public CSIM
→ Hidden CSIM
→ 合法且有界的 Candidate repair
→ accepted / rejected
```

Legacy 代码可以被包装来提供初始生成能力，但 Legacy 完整流程及其 success bool 不是最终裁决者。

### 7.3 取消普通用户模式分裂

当前：

```text
--legacy
--repair-aware
```

属于内部实现边界，不保留为普通用户产品选项。

迁移策略：

1. 新增 `refactor / optimize / full` 普通命令；
2. 普通重构通过 Bootstrap 进入 Stage 2 正式后端；
3. 旧 task-file/legacy 入口暂时保留给高级复现和迁移测试；
4. P0 成功后把旧公开 flag 标记为 deprecated 并从普通 help 隐藏；
5. consumer 全部迁移后再决定删除或内部保留。

### 7.4 Budget Profile 与内部转换

P2 必须把以下三类值转换成一份共享的 run-level budget contract：

```text
system_defaults
system_safety_ceilings
user_requested_limits
```

内部持久化：

```text
effective_hard_limits
soft_usage_budgets
actual_usage
remaining_hard_budget
budget_source_per_field
budget_exhaustion_resource
budget_exhaustion_stage
```

所有 Testbench 生成、初始 Candidate 生成、Candidate repair、compile、CSIM
和 CSYNTH 必须共享同一个 `BudgetManager`。不得让不同阶段各自创建互不相干
的调用计数器。

硬预算耗尽时：

```text
阻止新的对应动作启动
→ 保存现有 artifacts
→ 若已有 accepted/best_correct 则返回它
→ 否则返回结构化 budget_exhausted
```

Token/Cost 软预算超出时只记录：

```text
soft_budget_exceeded=true
```

当前不终止运行。

## 8. Execution Identity 与可复现性

每次运行必须回答：

```text
哪个 source 和 top？
哪个 normalized TaskSpec？
哪个模型/Profile/Provider 与最终参数？
哪个 Prompt 版本/hash？
哪个 Target 与真实工具链？
哪些 Public/Hidden suite 与来源？
哪些 Candidate 与 hash？
预算上限和实际使用是多少？
```

最小 identity bundle：

```text
run id
source path/hash
top function
normalized TaskSpec 与 hash
model/profile/provider identity
effective 非敏感模型参数
Prompt hashes
effective TargetProfile 与 provenance
Vitis executable/version fingerprint
suite hashes 与 provenance
initial/final Candidate hashes
budget limits/usage
artifact schema version
```

密钥不得进入任何 identity artifact。

Execution Identity 同时作为 cache identity 和后续 Memory Applicability 实验的基础。

### 8.1 Budget 与 Pricing Identity

Execution Identity 必须保存：

```text
system defaults
system safety ceilings
user requested hard limits
effective hard limits
user Token/Cost soft budgets
actual BudgetUsage
soft-budget exceeded flags
hard-budget exhaustion reason/stage
pricing snapshot/hash
pricing source status
cost estimation quality
currency
```

必须能区分：

```text
用户未设置
系统使用默认值
用户主动覆盖
用户请求超过安全上限而被拒绝
```

不能只保存一个最终数字并丢失来源。

## 9. P5：简洁输出

### 9.1 默认输出

```text
Status: accepted
Mode: refactor
Kernel: process_top
Candidate: <artifact>/best_candidate.cpp
CSYNTH: passed
Public tests: passed
Hidden tests: passed
Repairs: 2/2
Artifacts: <artifact>/
```

失败示例：

```text
Status: rejected
Failed stage: csynth
Reason: dynamic allocation remains
Repairs: 2/2
Details: <artifact>/report.json
```

### 9.2 输出等级

```text
default     简洁人类可读摘要
--json      稳定机器可读 summary
--verbose   phase 级进度与摘要
--debug     完整模型/工具诊断流
```

默认终端不输出完整 Prompt、完整 Agent 对话、内部资格检查、全量 `RunResult` JSON、原始 Vitis 日志或 operator-only Hidden 信息。

### 9.3 完整 artifacts

```text
full_result.json
trace.jsonl
model_calls.json
tool_calls.json
stdout.log
stderr.log
run_artifact_manifest.json
```

Legacy 后端输出默认捕获到 artifact，只在对应 verbose/debug 模式转发终端。

### 9.4 默认输出中的预算与用量

默认简洁输出增加：

```text
Usage:
  Tokens: 32,418 / 50,000 (soft, observed only)
  LLM calls: 6 / 8
  Compile calls: 5 / 10
  CSIM calls: 4 / 6
  CSYNTH calls: 2 / 3
  Estimated cost: ¥0.42 / ¥1.00 (soft, approximate)
  Wall time: 18m 32s / 30m
```

其中：

- Token 和 Cost 分母是用户声明的软预算，不参与当前流程阻断；
- 调用次数和 wall-time 是 effective 硬预算；
- 用户未设置硬预算时，显示系统默认值，并可在 JSON 中查看安全上限；
- 用户未设置 Token/Cost 软预算时，显示实际值，不强行显示分母；
- 价格未验证时显示 `Estimated cost: unavailable`；
- 超过软预算时显示 `soft budget exceeded`，但不把结果改成失败。

`--json` 必须分别输出：

```text
system_defaults
system_safety_ceilings
user_requested
effective_hard_limits
soft_budgets
usage
remaining
hard_budget_exhausted
soft_budget_exceeded
pricing
cost_estimation_quality
```

## 10. P0：真实 DFS Source-only 验收

P0 必须使用最终普通入口，并明确提供 top：

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

必须真实证明：

```text
source-only normal CLI
→ internally generated TaskSpec
→ real model-generated tests and initial Candidate
→ qualified Public/Hidden source contract
→ Stage 2 formal Preflight
→ real Vitis HLS 2023.2 CSYNTH
→ Public CSIM
→ Hidden CSIM
→ bounded legal repair
→ accepted
```

附加要求：

- Hidden 不泄漏到模型可见内容；
- credential 不泄漏；
- 运行不修改仓库源码；
- Execution Identity 完整；
- 默认输出简洁；
- deterministic regression 通过；
- local=remote，worktree clean。

Legacy AgRefactor 自己的成功不能满足 P0，除非 Candidate 随后被 Stage 2 正式后端 accepted。

### 10.1 P0 Budget 与 Cost 验收

P0 必须额外证明：

- 普通 CLI 可以设置 LLM、compile、CSIM、CSYNTH 等硬预算；
- 未设置时使用系统默认值，而不是直接使用安全上限；
- 用户可在 `[0, system_safety_ceiling]` 内覆盖默认值；
- 超过安全上限的请求在运行前明确拒绝；
- 所有阶段共享同一个 run-level BudgetManager；
- LLM/tool 调用次数与最终 summary 一致；
- Token 只做 observed usage 与软预算展示，不被宣称为硬限制；
- Estimated cost 使用官方 pricing snapshot，或明确显示 unavailable；
- 默认输出和 JSON 中的预算、用量、币种及估算质量一致。

## 11. 清理与弃用审计

P0 成功后进行最终清理；明显独立死代码可更早删除。

每个对象分类为：

```text
keep
wrap
deprecate
delete_after_P0
needs_evidence
```

已知审计对象：

```text
三个已停用的静态 Testbench hard-blocker helper
只保护退休启发式的测试
重复 real-DFS acceptance/recovery runner
普通用户 --legacy / --repair-aware
默认全量终端 JSON
TaskSpec.testbench_path 与 test_suites 重叠
重复模型参数解释入口
无 consumer 的 LegacyRefactorSettings 字段
重复原始日志
临时 acceptance-only 配置和脚手架
```

规则：

1. 先迁移 active consumer，再删除；
2. 历史 acceptance 文档作为证据保留；
3. `LegacyRefactorAdapter` 在有用生成 consumer 被抽取/包装前保留；
4. 公开 task-file 字段经过兼容/弃用期；
5. 不因为某个抽象开发成本高就永久保留无用实现。

## 12. 冻结实施顺序

```text
Step 0  文档冻结与只读 consumer 审计
Step 1  P1 已知模型静态兼容 Profile
Step 2  P4 Public/Hidden 来源与 provenance
Step 3  P2 source-only bootstrap 与普通 CLI
Step 4  Execution Identity
Step 5  P5 简洁输出与日志捕获
Step 6  P0 真实 DFS source-only accepted
Step 7  清理、弃用与 Pre-Stage-3 Closure
Step 8  开始 Stage 3
```

强耦合的小改动可以在一个 commit 完成，但验收边界必须清楚，P0 不能跳过。

## 13. 每一步验收边界

### Step 1：P1

- 已声明家族都有静态 Profile；
- Provider launch 前完成参数验证/映射/拒绝；
- effective 非敏感参数写入 artifact；
- deterministic Profile 测试通过；
- 当前实际使用模型至少一次真实 network smoke；
- 不自动换模型。

### Step 2：P4

- provided/auto/hybrid 推导正确；
- 多 suite 支持；
- provenance 落盘；
- Hidden isolation 测试通过；
- generated test 仍必须经真实 compile/run/coverage qualification。

### Step 3：P2

- 普通命令要求 `--top`；
- 普通用户不提供 task.json、candidate、work dir、artifact dir；
- 内部 TaskSpec 落盘；
- 初始生成接入 Stage 2 后端；
- 高级 task-file 复现入口保留；
- 硬预算按 system default / safety ceiling / user override 生成；
- Token/Cost 作为 observed-only 软预算进入内部合同。

### Step 4：Execution Identity

- 必需非敏感字段齐全；
- 真实工具版本和 effective 值记录；
- materially different execution 不共享同一 cache identity；
- Budget Profile 与 pricing snapshot 变化进入 identity。

### Step 5：P5

- 默认输出简洁；
- JSON schema 稳定；
- verbose/debug 边界明确；
- 完整证据仍在 artifact；
- 普通输出不含 Hidden 信息；
- 默认输出显示 Token、LLM、compile、CSIM、CSYNTH、cost 和 wall time；
- 软预算与硬预算的标签不得混淆。

### Step 6：P0

- 真实 source-only DFS 由 Stage 2 返回 accepted；
- 使用真实模型与真实 Vitis；
- Public/Hidden 通过；
- 硬预算和 repair 次数有界；
- Token/Cost 只做统计与软预算展示；
- 官方 pricing snapshot 与估算质量经过验收；
- leakage、源码变更和 identity 检查通过。

### Step 7：Closure

- dead/duplicate code 审计完成；
- deprecation 文档完成；
- full deterministic regression 通过；
- 最终 P0 smoke 仍 accepted；
- local=remote；
- worktree clean；
- 文档写入：

  ```text
  PRE_STAGE3_CLOSED=true
  STAGE3_STARTED=false
  ```

## 14. Stage 3 入口条件

只有同时满足以下条件才开始 Stage 3：

- P1、P2、P4、P5 和 Execution Identity 最小产品合同完成；
- P0 真实 DFS 通过最终普通入口 accepted；
- 无高优先级 correctness/leakage 问题；
- 清理与弃用审计完成；
- regression、local=remote、clean 通过。

Stage 3 从 Safe Three-Level Optimizer 合同开始：

```text
Structural
→ Bottleneck
→ Pragma
```

同时冻结 Candidate/Checkpoint identity、rollback、`best_correct`、`best_ppa`、hypothesis/evidence、cache identity 和 budget-exhaustion semantics。

## 15. 明确推迟

```text
动态未知模型识别与持久化 capability probe
自动模型路由
完整 per-model Prompt 校准/消融系统
模型权重微调
大型测试发现系统
约束求解/InputDomain 框架
Mutation Testing
通用进程隔离框架
Memory Applicability Gate 实现
Safe Three-Level Optimizer 实现
repository-level migration
```

这些能力可以预留扩展点，但不得扩张当前 Pre-Stage-3 收尾范围。
