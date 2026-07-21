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

### 3.6 高级复现入口

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
- 高级 task-file 复现入口保留。

### Step 4：Execution Identity

- 必需非敏感字段齐全；
- 真实工具版本和 effective 值记录；
- materially different execution 不共享同一 cache identity。

### Step 5：P5

- 默认输出简洁；
- JSON schema 稳定；
- verbose/debug 边界明确；
- 完整证据仍在 artifact；
- 普通输出不含 Hidden 信息。

### Step 6：P0

- 真实 source-only DFS 由 Stage 2 返回 accepted；
- 使用真实模型与真实 Vitis；
- Public/Hidden 通过；
- 预算和 repair 次数有界；
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
