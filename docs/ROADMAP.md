# AgRefactor++ Development Roadmap

> **权威范围文档。** 后续开发、新对话、阶段验收与论文表述均以本文档为准。任何核心目标不得在没有明确决策记录、代码证据与文档更新的情况下被删除、弱化或偷换概念。

## 1. 项目使命

AgRefactor++ 是一个**目标环境条件化、模型可插拔、证据驱动、预算约束**的 HLS 自动修复、优化与迁移智能体。

用户指定模型/API、Memory 模式、目标 Vitis HLS 环境、器件/时钟/资源约束、验证方式与调用预算。系统必须依据真实编译、测试、csim、csynth 和报告证据，在预算内完成修复与优化，返回 `best_correct`，并保留完整可追溯轨迹。

项目必须同时覆盖两类任务：

### 模式 A：普通 C/C++ → 目标 HLS

```text
普通 C/C++ 或不可综合程序
+ TargetProfile
→ 目标环境下功能正确、可综合并经过优化的 HLS
```

### 模式 B：已有 HLS → 目标版本迁移

```text
已有 HLS
+ optional SourceProfile
+ TargetProfile
→ 目标版本下经过修复、验证、优化与 PPA 比较的 HLS
```

**版本感知迁移是不可删除的核心目标。**

## 2. 八项不可删除的核心能力

| # | 核心能力 | 最终完成含义 |
|---|---|---|
| 1 | TargetProfile | Vitis 版本、settings、工具命令、part、platform、clock、资源、flags、Tcl 和 report parser 真正控制执行，而不是只出现在 JSON 中。 |
| 2 | 双模式目标版本处理 | 同时支持普通 C/C++→目标 HLS，以及已有 HLS→目标版本迁移。source version 可选，不要求自动识别。 |
| 3 | Model API Registry | Provider-neutral；模型由用户授权选择；API key 只通过环境变量；默认 fixed policy。 |
| 4 | 分层 Prompt 适配 | 公共任务契约 + 当前阶段 + TargetProfile + 模型家族适配 + 当前证据 + gated Memory + 输出契约。 |
| 5 | 结构化反馈与证据状态机 | compile、public test、csim、csynth、timing、resource、tool error 等证据决定合法下一步。 |
| 6 | 假设驱动三级安全优化器 | Structural → Bottleneck → Pragma；cheap gate、checkpoint、rollback、cache、候选树与 `best_correct`。 |
| 7 | Memory Applicability Gate | `off/gated/always`、正负经验、适用性评分、拒绝原因、禁用条件与 Retrieval Abstention。 |
| 8 | BudgetManager | 记录并硬限制 LLM、token、cost、compile、test、csim、csynth、cosim 与 wall time；预算耗尽返回 `best_correct`。 |

详细追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 3. 全局不可违反的规则

### 3.1 用户决定模型

默认 `model_policy=fixed`。系统不得擅自换模型。只有用户显式提供 `allowed_models` 并启用 `auto`，系统才可在授权范围内选择。

### 3.2 用户决定 Memory 模式

正式保留：

```text
memory_mode=off
memory_mode=gated
memory_mode=always
```

系统不能把 `off` 静默改成 `gated`，也不能把 `gated` 退化成“始终注入”。

### 3.3 Correctness First

```text
功能未通过
→ 禁止进入 PPA 优化
```

任何 compile/public test/csim 失败的候选都不能因为某项综合指标好看而被接受。

### 3.4 best_correct 不可破坏

必须维护：

```text
baseline
current_candidate
best_correct
best_ppa
```

失败候选、异常候选、PPA 退化候选都不能覆盖 `best_correct`。

### 3.5 工具调用也属于预算

预算不仅包括 token，还包括 compile、public test、csim、csynth、cosim 和 wall time。

### 3.6 TargetProfile 必须真实驱动工具

下面这种情况不算完成：

```text
TaskSpec 显示 2023.2 / xcu200 / 5 ns
但 Tcl 和实际工具仍使用固定旧值
```

必须持久化 effective profile、实际命令、Tcl、part、clock 与 parser 证据。

### 3.7 Memory 必须能够拒绝

Memory 是可选证据，不是强制 Prompt 装饰。低置信度时必须允许 Retrieval Abstention。

### 3.8 所有候选必须可追溯

每个候选至少记录：

```text
parent
生成模型
Prompt profile
因果假设
修改范围
使用的 Memory
工具证据
PPA
预算
接受/拒绝/回滚原因
```

### 3.9 只对真实验证能力标记“支持”

- 类存在，不等于完成；
- 参数存在，不等于真正下传；
- 单 kernel 成功，不等于普适；
- 一次迁移成功，不等于支持任意版本对；
- 单次 PPA 改善，不等于稳定优化收益。

### 3.10 远期研究不挤占当前关键路径

以下方向保留接口，但不作为当前 Stage 0–6 的核心交付：

- XRT→AVED 等平台/运行时迁移；
- repository-level HLS migration；
- Host/Kernel/Build/Runtime/Platform 联合迁移；
- AST/Clang/MLIR 技能自动生成；
- 完整版本知识图谱；
- 大规模自动模型路由；
- 任意程序形式化等价证明。

## 4. Stage 0 — 基线冻结

### 4.1 目标

证明原始 AgRefactor 与当前 AgRefactor++ 基础能力在真实环境中可运行，并留下可重复基线。

### 4.2 应完成内容

- 固定可运行 commit；
- 固定 Ubuntu、Python、Vitis HLS 环境；
- 固定模型/API 配置；
- 复现 `flow.new`；
- 验证基础 RAG；
- 验证 `flow.parallel_kernel` 框架；
- 验证 `opt.simple_iter`；
- 保存日志、上下文、代码、token、cost、csim、csynth 和报告；
- 明确区分原项目已有、AgRefactor++ 新增、当前环境真实验证、仅存在代码但未验证。

### 4.3 当前已完成

- Ubuntu 22.04、Python 3.10、Vitis HLS 2023.2；
- DeepSeek V4 Flash/Pro OpenAI-compatible；
- 单 kernel 真实重构；
- RAG 成功/失败 trial 写入与检索；
- `flow.parallel_kernel` 调度框架部分验证；
- `opt.simple_iter` 真实多轮 baseline；
- 基础 token/cost；
- HeteroRefactor 因 ROSE/EDG 外部依赖暂停。

### 4.4 当前限制

- 真实端到端样例仍主要集中于 DFS `process_top`；
- parallel 的有限成功率不是正式 benchmark；
- `simple_iter` 只是线性 baseline；
- coverage/hidden TB 尚未成为稳定主验证路径；
- 版本迁移尚未实现。

### 4.5 完成判定

Stage 0 作为复现基线**基本完成**。后续可追加证据，但不能把未验证模块回写成“已验证”。

详细文档：[`STAGE0_BASELINE.md`](STAGE0_BASELINE.md)。

## 5. Stage 1 — 共享基础设施

### 5.1 目标

建立修复、优化、Memory 与迁移共同使用的共享底座，避免在 legacy `flow/`、`opt/` 中继续形成不一致逻辑。

### 5.2 必须包含

#### TargetProfile

最终控制：

```text
Vitis version
settings64.sh
tool command
part/device
platform
clock period
resource limits
compile flags
Tcl generation
report parser
```

#### Model Registry

- provider-neutral；
- API key 只通过环境变量；
- fixed policy 默认；
- auto 仅在用户授权模型池中工作。

#### TaskSpec 与统一 CLI

```text
refactor
optimize
full
后续 migrate
```

#### Trace

统一记录阶段、证据、模型调用、候选、决策与结果。

#### BudgetManager

记录并硬限制：

```text
LLM calls
input/output tokens
cost
compile
public test
csim
csynth
cosim
wall time
```

### 5.3 当前已完成

- `TaskSpec`、`TargetProfile`、`RunMode`；
- Model Registry；
- OpenAI-compatible Provider；
- Evaluator/Evidence 基础接口；
- `UnifiedRunner`；
- CLI；
- `TraceRecorder`；
- Budget core；
- Legacy Refactor Adapter；
- AutoGen 与 repair known usage 合并；
- TargetProfile legacy propagation；
- default profile 与 partial override；
- part/device、clock、compile flags；
- target-aware Tcl；
- `AGREFACTOR_VITIS_RUN` executable override；
- actual executable resolution；
- `vitis-run --version` verification；
- mismatch-before-csynth failure；
- `effective_target_profile.json`；
- `csynth_invocation.json`；
- remote non-default target rejection；
- 153/153 确定性测试；
- Vitis 2023.2 真实 csynth 验收。

验收记录：[`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md)。

### 5.4 尚未完成

#### BudgetManager 工具硬控制

必须补齐：

- compile count/limit；
- public test count/limit；
- csim count/limit；
- csynth count/limit；
- cosim count/limit；
- 工具调用前 hard check；
- 工具调用后真实 accounting；
- exhaustion evidence；
- 预算耗尽后的安全停止；
- Stage 3 中返回 `best_correct`。

#### TargetProfile 后续完整配置

本地执行核心已验收，但仍需：

1. stable named target profiles；
2. per-profile executable；
3. per-profile settings script；
4. platform；
5. resource limits；
6. report parser profile；
7. per-field provenance；
8. 更多 Vitis 版本、器件和 kernel 验证。

当前多版本显式方式：

```text
TaskSpec.target.toolchain_version
+
AGREFACTOR_VITIS_RUN=/path/to/matching/vitis-run
```

详细用法见 [`USAGE.md`](USAGE.md)。

#### 稳定配置模板

需要 target profiles、model registry examples 和不含 secret 的配置模板。

### 5.5 完成判定

```text
TargetProfile 真正控制一次真实 Vitis run
+
BudgetManager 真正控制工具调用
```

第一项已经通过 commit `717fdef` 和真实运行：

```text
/data/agrefactor_runs/stage1_target_profile_real_vitis_20260715_141118
```

完成验收。

当前 Stage 1 唯一主阻塞项是 **工具硬预算**。

详细文档：[`STAGE1_INFRASTRUCTURE.md`](STAGE1_INFRASTRUCTURE.md)。

## 6. Stage 2 — 结构化证据闭环

### 6.1 原始目标

Stage 2 不等于 testbench repair。完整范围是：

```text
General VitisFeedbackParser
+
Evidence-driven State Machine
+
Stage Prompt Builder
+
Testbench Reliability
+
Multi-type Kernel Smoke
```

### 6.2 已完成核心子项目：Testbench Reliability

- testbench preflight；
- failure stage/kind/owner/next-action；
- testbench-owned 与 candidate-owned 分离；
- 保守的实现私有 file-scope global 依赖门禁；
- ABI/linkage 分类；
- bounded testbench-only repair；
- repair output contract；
- 空回复、未修改、provider error 使用剩余预算；
- public-interface-only 原则；
- 当前状态型 kernel 的 POSIX process isolation；
- repair artifact；
- combined usage accounting；
- 110 个确定性测试；
- 一个真实统一 CLI + DeepSeek + Vitis HLS 状态型 kernel 验收。

### 6.3 Stage 2 仍必须完成

#### 1. 通用 VitisFeedbackParser

至少结构化解析：

```text
input/config error
compile error
public-test failure
csim mismatch
csim crash
csim timeout
unsupported construct
unknown loop bound
dependency / II bottleneck
memory port conflict
timing failure
resource overflow
report parser failure
tool internal error
```

证据字段至少包括：

```text
stage
status
failure_class
owner
evidence
locations
metrics
resources
recommended_next_action
```

#### 2. Evidence State Machine

至少覆盖：

```text
INPUT_CHECK
COMPILE_CHECK
PUBLIC_TEST
CSIM
CSYNTH
READY_FOR_OPTIMIZATION
STOP
```

状态机决定合法下一步，不让模型自由改写整个流程。

#### 3. Shared Layered Prompt Builder

最终 Prompt：

```text
公共任务契约
+ 当前阶段
+ effective TargetProfile
+ 模型家族适配
+ 当前工具证据
+ gated Memory
+ 输出格式/禁止事项
```

#### 4. 多类型 kernel smoke matrix

最低覆盖：

- array map；
- reduction；
- stencil/嵌套循环；
- multi-output；
- `ap_int` 或 struct；
- `hls::stream`；
- stateful kernel。

重点不是要求全部成功，而是验证：

```text
不崩溃
不误归因
不假成功
不越权修改
失败证据有用
成功时真实通过 csim/csynth
```

#### 5. 文档与复现同步

README、USAGE、REPRODUCTION_STATUS、CHANGELOG、ROADMAP、acceptance 与 smoke 结果必须同步。

### 6.4 Stage 2 完成标准

```text
Testbench Reliability 完成
+
General Feedback Parser 完成
+
Evidence State Machine 完成
+
Layered Prompt Builder 接入
+
Multi-type Smoke 完成
+
文档同步完成
```

当前只能说：**Stage 2 Testbench Reliability 核心完成；整个 Stage 2 尚未关闭。**

详细文档：

- [`STAGE2_EVIDENCE_LOOP.md`](STAGE2_EVIDENCE_LOOP.md)
- [`stage2_acceptance.md`](stage2_acceptance.md)

## 7. Stage 3 — 安全的三级优化器

### 7.1 目标

重新实现比公开 `simple_iter` 更安全、可解释、可回滚、预算受控的优化闭环。

不能退化为：

```text
把综合报告发给模型
→ 生成整份新代码
→ 继续综合
```

### 7.2 三级顺序

#### Level 1：Structural Optimization

- 算法结构；
- 循环重写；
- 函数边界；
- 数据布局；
- 数据局部性；
- 访存结构；
- 局部缓存；
- 流水数据路径。

#### Level 2：Bottleneck Repair

基于综合证据处理：

- II；
- loop-carried dependency；
- memory port contention；
- critical path；
- resource bottleneck；
- unknown loop bound；
- dataflow stall/deadlock risk。

#### Level 3：Pragma Tuning

最后才处理：

```text
PIPELINE
UNROLL
ARRAY_PARTITION
DATAFLOW
INLINE
RESOURCE / BIND
```

### 7.3 必须实现的机制

#### 假设驱动

一次可生成多个假设，每个假设记录 hypothesis、supporting evidence、expected benefit、risk、modification scope 和 verification plan。

一个分支围绕一个因果假设，但允许一组相互依赖的修改。

#### 廉价筛选优先

```text
static check
→ compile
→ public test
→ fast csim
→ csynth
→ optional cosim
```

#### 候选管理

每个候选记录 parent、hypothesis、patch/change summary、correctness evidence、PPA、budget、accept/reject reason。

#### Checkpoint / Rollback / Cache

- 功能失败立即回滚；
- PPA 退化不更新最佳；
- 相同 code + TargetProfile 复用结果；
- 预算耗尽返回 `best_correct`。

#### UnifiedRunner 接入

真正支持 `mode=optimize` 和 `mode=full`。`full` 必须严格执行 refactor correctness → optimization。

### 7.4 完成标准

- correctness gate 不可绕过；
- 每个候选可追溯；
- 可回滚；
- 有硬预算；
- 始终保存 `best_correct`；
- 多类型 kernel 真实优化；
- 与 `simple_iter` 对照。

详细文档：[`STAGE3_SAFE_OPTIMIZER.md`](STAGE3_SAFE_OPTIMIZER.md)。

## 8. Stage 4 — Memory Applicability Gate

### 8.1 目标

保留 AgRefactor 的跨任务经验，同时控制不相似经验带来的噪声与负迁移。

不能再是：

```text
检索到经验
→ 一定注入 Prompt
```

而必须是：

```text
检索
→ 适用性判断
→ 接受 / 拒绝 / 弃权
```

### 8.2 三种模式

```text
memory_mode=off
memory_mode=gated
memory_mode=always
```

三种模式必须保留，用于正式消融。

### 8.3 Memory Schema

至少保存 stage、target profile、source profile/version、code features、interface features、error signature、action、preconditions、avoid conditions、verification、PPA delta、token/tool cost 和 positive/negative outcome。

### 8.4 Gate 判断维度

- 错误阶段；
- 错误签名；
- kernel 结构；
- 接口类型；
- source/target version；
- part/clock/resource；
- 历史验证证据；
- 负面案例；
- 预期收益；
- 预算价值。

### 8.5 Retrieval Abstention

低置信度时允许不注入任何 Memory，同时记录 applicability score、accepted/rejected 和 rejection reason。

### 8.6 完成标准

- 正负经验都能保存；
- Gate 决策可解释；
- off/gated/always 可运行；
- 部分案例能正确弃权；
- 有负迁移率、成功率与成本对比；
- 证明 Gate 的收益，而不是只增加复杂度。

详细文档：[`STAGE4_MEMORY_GATE.md`](STAGE4_MEMORY_GATE.md)。

## 9. Stage 5 — 目标版本扩展与真实迁移

### 9.1 目标

正式实现：

```text
旧版本/源版本 Vitis HLS
→ 目标版本 Vitis HLS
```

这个目标不可删除。

### 9.2 双模式

#### Mode A

```text
普通 C/C++
+ target profile
→ 目标 HLS
```

#### Mode B

```text
已有 HLS
+ optional source profile
+ target profile
→ 迁移、修复、验证、优化与 PPA 比较
```

### 9.3 数据结构扩展

至少增加：

```text
source_profile
target_profile
mode=migrate
source baseline
target baseline
migration constraints
migration evidence
```

### 9.4 正式迁移流程

```text
源版本环境检查
→ 源版本 csim/csynth baseline
→ 保存源接口、pragma、行为和 PPA
→ 目标版本 direct run
→ 区分版本相关错误与普通错误
→ 检索迁移经验
→ Memory Applicability Gate
→ rollback-safe transformation
→ target correctness gate
→ target csynth
→ source/target behavior/PPA comparison
→ migration report
```

### 9.5 版本相关错误分类

至少包括 deprecated pragma、removed API、directive syntax change、library/type incompatibility、interface semantics change、Tcl command change、report schema change、default scheduling change、device/clock difference 和 ordinary synthesis error。

### 9.6 明确不做

- 自动识别源版本；
- 支持任意版本对；
- 一次覆盖所有历史版本；
- 任意程序形式化等价证明；
- 平台/运行时迁移；
- 仓库级迁移。

### 9.7 完成标准

至少：

- 第二个真实 Vitis Profile；
- 双版本环境实际可调用；
- 一个真实 source→target 样例；
- source baseline 通过；
- target migration correctness 通过；
- 输出修改规则与 PPA 比较；
- 能区分版本问题与普通代码问题。

**没有真实双版本实验，Stage 5 不得标记完成。**

详细文档：[`STAGE5_VERSION_MIGRATION.md`](STAGE5_VERSION_MIGRATION.md)。

## 10. Stage 6 — 系统评测、消融与最终交付

### 10.1 目标

Stage 6 不再新增主要模块，而是证明 Stage 0–5 的功能真实有效、稳定且可复现。

### 10.2 固定任务类别

| 类别 | 任务 |
|---|---|
| A | 动态内存、递归、容器、复杂指针等不可综合 C/C++ |
| B | 已有 HLS compile/csynth 失败 |
| C | public test/csim 功能错误或接口不一致 |
| D | 功能正确但 latency、II 或资源较差 |
| E | Memory 容易误检索或负迁移 |
| F | 源版本可运行、目标版本失败或 PPA 漂移 |

### 10.3 基线

至少比较 current AgRefactor++ baseline、Memory off/always/gated、统一/分层 Prompt、simple_iter/安全优化器、固定 Flash/Pro 和完整系统。

### 10.4 消融链

```text
A0 当前 baseline
A1 + TargetProfile + Trace
A2 + Structured Feedback + State Machine
A3 + Layered Prompt
A4 + Safe Optimizer + Checkpoint + Rollback
A5 + Memory Gate
A6 + Hard Budget
A7 Full System
```

### 10.5 指标

#### 正确性

compile、public test、hidden test、csim、optional cosim、false success rate。

#### 综合/PPA

csynth success、timing、latency、II、LUT、FF、BRAM、DSP、resource legality。

#### 预算

LLM calls、input/output tokens、cost、compile/test/csim/csynth/cosim、wall time。

#### 搜索质量

best candidate round、rollback、invalid synthesis ratio、cache hit、candidate acceptance rate。

#### Memory

Gate acceptance、abstention、rejection、negative transfer、retrieval 后成功率变化。

#### 稳定性

多次重复均值、标准差、最坏结果和成功率。

### 10.6 最终交付

- 稳定 CLI；
- TargetProfile/Model/Prompt/Memory 配置；
- 安全优化器；
- Feedback Parser 与 State Machine；
- benchmark 和迁移样例；
- baseline、消融与重复实验；
- 运行报告；
- README、USAGE、配置与复现说明；
- 竞赛报告；
- 论文材料；
- 可复现脚本；
- release/tag。

论文整理与发布属于 Stage 6 后半段，不再增加 Stage 7。

详细文档：[`STAGE6_EVALUATION.md`](STAGE6_EVALUATION.md)。

## 11. 八项目标与 Stage 对应关系

| 初始目标 | 主要阶段 | 后续使用阶段 |
|---|---|---|
| TargetProfile | Stage 1 | Stage 2–6 |
| 双模式与版本迁移 | Stage 5 | Stage 1 预留、Stage 4 Gate、Stage 6 评测 |
| Model API Registry | Stage 1 | Stage 2–6 |
| 分层 Prompt | Stage 2 | Stage 3–6 |
| 结构化反馈与状态机 | Stage 2 | Stage 3–6 |
| 三级安全优化器 | Stage 3 | Stage 5–6 |
| Memory Applicability Gate | Stage 4 | Stage 5–6 |
| BudgetManager | Stage 1 基础、Stage 3 硬控制 | Stage 2–6 |

没有一项被删除。

## 12. 当前权威状态

| Stage | 当前状态 |
|---|---|
| Stage 0 | 基本完成 |
| Stage 1 | 主体完成；TargetProfile 真正下传和完整工具硬预算未完成 |
| Stage 2 | Testbench Reliability 完成；通用 parser、状态机、阶段 Prompt、多类型 kernel 未完成 |
| Stage 3 | 未开始；`simple_iter` 仅为 baseline |
| Stage 4 | 未开始；当前 RAG 不等于 Memory Gate |
| Stage 5 | 未开始；当前 TargetProfile 字段不等于版本迁移 |
| Stage 6 | 未开始；只有零散 baseline 证据 |

## 13. 文档冻结后的执行顺序

暂不启动 Stage 3，按以下顺序补齐：

1. Stage 1：TargetProfile 真正下传；
2. Stage 1：tool-call accounting 与 hard budget；
3. Stage 2：general feedback schema/parser；
4. Stage 2：evidence state machine；
5. Stage 2：layered Prompt builder；
6. Stage 2：multi-type kernel smoke matrix；
7. 正式关闭 Stage 2；
8. 开始 Stage 3。

## 14. 新对话接续协议

新对话或长时间中断后，依次阅读：

1. `docs/PROJECT_STATE.md`
2. `docs/ROADMAP.md`
3. `docs/GOAL_TRACEABILITY.md`
4. 当前 Stage 文档
5. `docs/REPRODUCTION_STATUS.md`
6. `docs/USAGE.md`
7. 最新 acceptance
8. Git history

不得仅根据类名、参数名或一次成功运行推断完成状态。

## 15. 路线变更规则

任何涉及核心目标、阶段边界、完成标准或下一工作顺序的变更，必须同时更新：

- `ROADMAP.md`
- `GOAL_TRACEABILITY.md`
- `PROJECT_STATE.md`
- 对应 Stage 文档
- `CHANGELOG.md`
- 必要时更新原方案补充说明

禁止只在聊天中改变核心目标。
