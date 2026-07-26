# P0 生成与修复稳定化执行计划

> **状态：** 已完成的 P0 稳定化与关闭记录
> **冻结日期：** 2026-07-24
> **基线提交：** `f3e76347ac780cfdf77ea4b8adfe0c9db8d22f80`
> **基线回归：** 1406 tests passed
> **P0：** accepted
> **Pre-Stage-3：** 已关闭
> **Stage 3：** 未开始

本文冻结 P0 真实 DFS 验收之后的后续路线。除非新的真实工具证据推翻某项结论，
后续不得跳步，也不得以扩大默认 LLM 预算代替流程修复。

## 1. 已确认事实

1. DFS 单 kernel 端到端能力已经被真实证明过：DeepSeek 生成 Candidate，Vitis
   HLS 2023.2 CSYNTH、Public、Hidden 均通过并 accepted。
2. 当前路径不稳定，Legacy/AG2 在正式 Candidate repair 前经常消耗约 29–32 次
   LLM provider calls。
3. 原版 AgRefactor 的基础 Testbench 路径主要是“生成 Testbench → 生成简短重构
   约束”；当前默认路径额外自动打开 Public coverage loop 与 Hidden 多 trajectory。
4. 历史提交 `4b7d96202b6bb6e28eb1806b5805fd4df1f1a9b0`
   已证明静态启发式硬门禁会误杀，真实 compile/link/run/coverage/Vitis 应当裁决。
5. 当前新增风险包括：
   - `_REPEATED_FAILURE_LIMIT` 导致提前退出；
   - `forbidden_internal_dependency` 在工具前直接拒绝；
   - 广义 Testbench preservation contract 把错误旧结构当成必须保留；
   - Hidden-derived signature/macros/types 进入 Public-facing Prompt。

## 2. 冻结原则

### 2.1 真实工具拥有最终裁决权

优先级最高的证据：

```text
真实 compile/link
真实程序执行
真实 coverage
真实 Vitis CSIM
真实 Vitis CSYNTH
独立 Public evaluation
独立 Hidden evaluation
```

启发式只能用于日志、debug、artifact metadata 和未来研究，不得阻断真实工具：

```text
blocking=false
decision_authority=false
```

### 2.2 Hidden 是单向评测边界

允许：

```text
Public ABI / Candidate interface → Hidden evaluator
```

禁止任何 Hidden 或 Hidden-derived 内容进入：

```text
Public Testbench Prompt
Candidate Prompt
Testbench repair Prompt
Candidate repair Prompt
```

### 2.3 两种生成模式都必须跑通

#### 默认：`lightweight`

目标：

```text
低调用
职责单一
接近原版基础路径
优先得到可真实验证的 Testbench/Candidate
```

#### 用户显式开启：`coverage-enhanced`

必须完整支持，而不是只保留开关。应支持用户配置 coverage rounds / trajectories，
保留 coverage artifacts 和 best-Testbench selection，同时满足：

```text
Hidden 不反向约束 Public
第一轮后冻结 ABI
可复用时复用 matching stub
不因重复失败 fingerprint 提前退出
正确性优先于 coverage
```

### 2.4 Repair 参数

冻结目标：

```text
Testbench repair default = 3
Testbench repair safety ceiling = 10

Candidate repair default = 3
Candidate repair safety ceiling = 10
```

用户可在 `1..10` 内设置。当前不实现 no-progress early stop，不因重复错误、修改量小
或回答相似提前停止。

### 2.5 暂不提高默认 LLM 预算

A–E 完成并重新真实运行 DFS 前：

```text
max_llm_calls system default = 32
```

此前拟议的 `32 → 40` 暂停。

## 3. 严格实施顺序

每项独立提交、独立测试、独立审查。

---

## A. 撤销启发式裁决权

### A1. 取消重复失败提前退出

处理：

```text
_REPEATED_FAILURE_LIMIT
_repeated_failure(...)
failure fingerprint based break
```

Fingerprint 可以记录，但不能影响控制流。用户设置多少 rounds，就最多执行多少；
达到 coverage target 时允许成功提前结束。

### A2. 取消 `forbidden_internal_dependency` 硬门禁

移除工具前直接失败路径。静态发现只可作为 advisory，必须继续真实 compile/link，
再根据真实错误路由 Testbench repair。

### A3. 缩小 Testbench repair 合同

取消硬性要求：

```text
旧 Testbench 中所有 helper declaration 必须保留
所有 macro 必须逐字保留
所有 helper call count 不得降低
```

只保留最小结构/安全合同：

```text
完整 C/C++ Testbench
存在 main
不能定义 Candidate top
不能修改 Original/Candidate
不能把测试改成无条件成功
保持真实 golden-vs-candidate 比较目标
```

### A 验收

```text
启发式 blocking=false
重复失败不提前结束
私有状态猜测不阻止 compile/link
合理删除 insert/dfs_traverse 不被静态合同拒绝
完整回归通过
MODEL_API_CALLED=false
VITIS_RUN=false
```

---

## B. 修复 Hidden 边界

移除：

```text
hidden_sig_spec → Public generator
hidden_hls_decl_verbatim → Public generator
canonical hidden macros/types → Public Prompt
```

修正证据字段，使 `hidden_testbench_exposed_to_model=false` 覆盖整个 Legacy + formal
路径，而不是只覆盖某个 repair 子阶段。

### B 验收

```text
所有模型 Prompt 无 Hidden-derived 内容
Hidden 可读取冻结 Public/Candidate interface
Public/Candidate 不读取 Hidden-derived 信息
完整回归通过
```

---

## C. 建立双生成模式

### C1. `lightweight` 默认

预期接口：

```text
--test-generation-profile lightweight
```

默认可省略。目标流程：

```text
基础 Public Testbench generation
→ 简短 Public-derived instruction
→ Candidate generation
→ 正式 Preflight / repair / CSYNTH / Public / Hidden
```

### C2. `coverage-enhanced` 显式

预期接口：

```text
--test-generation-profile coverage-enhanced
--public-coverage-rounds N
--test-generation-trajectories N
```

确切参数在 C 的代码审计中最终冻结，但能力必须真实可用。

### C3. ABI/stub

第一轮合格 Testbench 后冻结：

```text
_hls name
return type
argument types/order
linkage
public macros
```

后续 coverage rounds 不得无理由改变 ABI；matching stub 可复用时复用。

---

## D. 精炼 Testbench/stub Prompt

### Testbench

```text
只 forward-declare Original/Candidate top
不得定义、stub、wrap Candidate top
不得依赖实现私有 globals/types/helpers
正确性优先于 coverage
```

### Stub

```text
stub 是临时 Candidate top 的唯一实现
不得包含 main
必须匹配冻结 ABI
stub error 只修 stub
```

### 错误归属

```text
Testbench error → Testbench generation/repair
Stub error → stub regeneration
ABI CSYNTH error → ABI/Testbench/stub 协同修正
Coverage 不足 → 只扩充输入，ABI 不变
Candidate formal failure → Candidate repair
```

---

## E. Repair 参数化

普通 CLI：

```text
--max-testbench-repairs
--max-candidate-repairs
```

合同：

```text
default = 3
valid user range = 1..10
>10 在 provider call 前拒绝
```

第 N 轮必须看到 1..N-1 的安全摘要；不加入 Hidden、凭据、operator-only 完整日志，
也不实现 no-progress early stop。

---

## F. 双模式真实 DFS 验收

A–E 完成后，保持：

```text
max_llm_calls = 32
Vitis HLS = 2023.2
kernel = DFS / process_top
```

依次运行：

1. Lightweight DFS：DeepSeek、CSYNTH、Public、Hidden、Prompt Identity、Hidden
   isolation、worktree clean 全部通过。
2. Coverage-enhanced DFS：用户 rounds/trajectories 真正执行，coverage artifacts
   完整，CSYNTH/Public/Hidden 通过，Hidden isolation 通过。
3. 完成后再决定是否仍需调整默认 LLM 预算。

---

## G. P0 记录与 Pre-Stage-3 Closure

两种模式通过后：

```text
记录 P0 真实证据
更新 PROJECT_STATE
cleanup/deprecation audit
完整回归
local=remote
worktree clean
PRE_STAGE3_CLOSED=true
```

之后才允许进入 Stage 3。

## 4. 固定推进方式

每一步：

```text
只读审计
→ 最小修改范围
→ 隔离验证
→ 专项测试
→ 完整回归
→ scope check
→ commit/push
→ 共同审查输出
→ 下一步
```

禁止：

```text
一次混改多个步骤
失败后盲目扩大预算
失败后新增未经证实的启发式
静态猜测替代真实工具
无证据宣布 P0/Pre-Stage-3 关闭
```

## 5. 当前执行指针

```text
ACTIVE_STEP=F
ACTIVE_SUBSTEP=F dual-mode real DFS acceptance
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
DEFAULT_LLM_CALLS=32
LIGHTWEIGHT_STATUS=implemented_default
COVERAGE_ENHANCED_STATUS=implemented_explicit
TESTBENCH_REPAIR_DEFAULT=3
CANDIDATE_REPAIR_DEFAULT=3
REPAIR_SAFETY_CEILING=10
```


<!-- P0_STEP_A_HEURISTIC_AUTHORITY_REMOVAL -->
## Step A completion

Repeated failure fingerprints no longer terminate trajectories;
private-dependency guesses no longer skip the compiler; and Testbench
repair no longer preserves every helper, macro and helper call count.
See [`P0_HEURISTIC_AUTHORITY_REMOVAL.md`](P0_HEURISTIC_AUTHORITY_REMOVAL.md).

```text
STEP_A=completed
ACTIVE_STEP=B
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```


<!-- P0_STEP_B_HIDDEN_BOUNDARY_CORRECTION -->
## Step B completion: one-way Hidden boundary

Public and Candidate generation no longer accept Hidden-derived inputs. The
Public ABI is frozen before Candidate generation, held-out generation runs only
after Candidate generation, and exposure metadata is derived from fail-closed
boundary evidence. See
[`P0_HIDDEN_BOUNDARY_CORRECTION.md`](P0_HIDDEN_BOUNDARY_CORRECTION.md).

```text
STEP_B=completed
ACTIVE_STEP=C
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```



<!-- P0_STEP_C_DUAL_GENERATION_PROFILES -->
## Step C completion: dual Testbench generation profiles

The normal source-only command now defaults to `lightweight`. The existing
coverage loop is available only through explicit `coverage-enhanced` selection,
with configurable Public rounds and independent trajectories. Coverage-enhanced
runs retain trajectory/round artifacts and select only a qualified best result.
See [`P0_DUAL_GENERATION_PROFILES.md`](P0_DUAL_GENERATION_PROFILES.md).

```text
STEP_C=completed
ACTIVE_STEP=D
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_STEP_D_TESTBENCH_STUB_PROMPT_REFINEMENT -->
## Step D completion: Testbench/Stub Prompt and error ownership

Testbench generation and repair now use the same black-box top-function contract.
The first qualified coverage round freezes the Candidate ABI and Public macros;
coverage-only rounds preserve that contract and reuse the matching Stub. Real
compiler/link/runtime evidence routes Testbench, Stub and ABI failures to their
own correction paths. See
[`P0_TESTBENCH_STUB_PROMPT_REFINEMENT.md`](P0_TESTBENCH_STUB_PROMPT_REFINEMENT.md).

```text
STEP_D=completed
ACTIVE_STEP=E
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_STEP_E_REPAIR_BUDGET_PARAMETERIZATION -->
## Step E completion: repair budget parameterization

The active source-only, repair-aware and compatibility entrypoints now share
typed Testbench/Candidate repair-attempt defaults and one safety ceiling.

```text
Testbench default=3
Candidate default=3
valid user range=1..10
safety ceiling=10
attempt N sees safe summaries 1..N-1
no no-progress early stop
```

See
[`P0_REPAIR_BUDGET_PARAMETERIZATION.md`](P0_REPAIR_BUDGET_PARAMETERIZATION.md).

```text
STEP_E=completed
ACTIVE_STEP=F
NEXT_STEP=F_DUAL_MODE_REAL_DFS_ACCEPTANCE
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```


<!-- P0_FINAL_CLOSURE -->
## Final completion

```text
STEP_F_DUAL_MODE_REAL_DFS=passed
STEP_G_CLEANUP_DEPRECATION_CLOSURE=passed
FINAL_POST_STABILIZATION_P0_SMOKE=accepted
P0_STATUS=accepted
PRE_STAGE3_CLOSED=true
STAGE3_STARTED=false
NEXT_STEP=STAGE3_PLANNING
```

Evidence:

- [`P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md`](P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md)
- [`PRE_STAGE3_CLEANUP_AND_CLOSURE_ACCEPTANCE.md`](PRE_STAGE3_CLEANUP_AND_CLOSURE_ACCEPTANCE.md)
