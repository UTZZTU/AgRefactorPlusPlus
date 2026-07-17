# AgRefactor++ Next-Chat Handoff

你现在要继续长期开发和研究 AgRefactor++。请把下面内容视为本次新对话的项目交接基线。在采取任何修改之前，必须先核对仓库、分支、HEAD、远端、工作区、文档和代码，不能只凭这段提示词猜测。

# 一、项目目标

AgRefactor++ 是一个面向 HLS 程序重构、验证、修复和后续优化研究的通用智能体系统。

当前阶段优先满足既定 HLS 任务、工具、预算和评测条件，但项目绝不能写成一次性竞赛系统。长期原则如下：

1. 核心包、类名、接口、目录、配置和主流程保持通用。
2. 不在核心代码和命名中出现赛事绑定词。
3. 规则抽象为 TaskSpec、TargetProfile、预算、评测角色、反馈、状态和策略。
4. 当前场景只是第一个可执行配置，不是系统唯一用途。
5. 所有设计要支持长期维护、扩展和后续论文研究。

严禁在新代码、核心测试和主流程文档中引入：

```text
fpt26
competition
track_a
```

# 二、仓库和环境

```text
本地仓库：/data/AgRefactor
GitHub：UTZZTU/AgRefactorPlusPlus
origin：git@github.com:UTZZTU/AgRefactorPlusPlus.git
开发分支：stage2-general-feedback
最新功能提交：
dc44be344f9bf9bae3eb8e43675fb49f0c017708
提交信息：
feat: add candidate repair prompt policies
```

环境：

```text
Ubuntu 22.04
Python 3.10
Conda 环境：agrefactor
Vitis HLS 2023.2
vitis-run=/data/Xilinx/Vitis/2023.2/bin/vitis-run
运行目录：/data/agrefactor_runs
工作目录：/data/agrefactor_work
```

开始工作前必须执行并核对：

```bash
cd /data/AgRefactor
git fetch origin
git branch --show-current
git rev-parse HEAD
git rev-parse origin/stage2-general-feedback
git status --short
git log -15 --oneline
```

要求：

- branch 必须是 `stage2-general-feedback`；
- local 与 remote 应一致；
- worktree 应干净；
- Git 历史中必须存在 `ec9802c` 和 `dc44be3`；
- 如果 HEAD 是后续纯文档提交，功能父提交应为 `dc44be3`；
- 如状态不一致，先停止修改并解释差异。

# 三、不可改变的工程原则

## 1. 四个核心原则

1. 证据可信：结论来自源码、编译、测试、CSIM、CSYNTH、trace 或结构化 evidence。
2. 错误可理解：失败尽量结构化为 stage、category、severity、owner、next action。
3. 信息隔离：operator-full 与 agent-safe 分离；Hidden 内容不得进入模型 Prompt、普通结果或普通 trace。
4. 流程可控：状态、预算、修复次数、工具调用、停止条件必须可审计、可阻断、可恢复。

## 2. 正确性优先顺序

```text
correctness
→ trustworthy evidence
→ legal repair action
→ bounded iteration
→ optimization
```

不得：

- 弱化测试；
- 删除 required calls、assertions、comparisons、seeds、macros；
- 伪造工具成功；
- 将 unknown failure 猜成 candidate failure；
- 将 Hidden evidence 发送给模型；
- 用 deterministic unit tests 冒充真实 kernel 验收；
- 为通过验收而随意修改运行时公共接口。

## 3. 预算语义

只使用真实物理计数：

```text
llm_calls
tool_calls
compile_calls
csynth_calls
csim_calls
tokens
cost_usd
```

不创建：

```text
public_test_calls
hidden_test_calls
```

Public/Hidden 是评测角色，不是物理工具类型。

预算语义：

- `None` / omitted：unlimited；
- zero：disabled；
- positive：hard bound；
- 真实 launch 前检查；
- 紧邻 launch 前 exact-once consume；
- success、failure、timeout、launch exception 均按真实尝试计数；
- 版本探测不计 CSYNTH；
- `BudgetUsage` 是 dataclass，没有 `to_dict()`，需要序列化时使用 `dataclasses.asdict()`。

# 四、当前已完成状态

## Stage 0

复现环境和原项目基线已建立。

## Stage 1 Core

已关闭。完成：

- TaskSpec、TargetProfile、RunMode；
- Model Registry 和 OpenAI-compatible Provider；
- Evaluator / Evidence 基础接口；
- BudgetManager；
- TraceRecorder；
- UnifiedRunner 和 CLI 基础；
- Legacy Refactor Adapter；
- TargetProfile 真实传入 Vitis；
- requested / actual Vitis version verification；
- compile、CSIM、CSYNTH hard budget；
- 真实 DFS 工具链验收。

## Stage 2.1 Public/Hidden Roles and Evidence

核心完成：

- TestSuiteSpec、EvaluationSplit；
- Public feedback 可见，Hidden feedback 不可见；
- operator-full / agent-safe evidence；
- split-aware trace；
- 多 suite composition；
- Hidden operator-only composition；
- Public candidate failure可以进入合法 candidate repair 路由。

## Stage 2.2 General Feedback and Validation Strategy

核心完成：

- FeedbackItem / FeedbackReport；
- Preflight、CSYNTH、Test Evaluation adapters；
- deterministic CSYNTH parser；
- operator-full / agent-safe views；
- composers；
- deterministic router；
- validation states / transitions；
- feedback coordinator；
- Unknown 不被猜成 candidate repair；
- Hidden blocking failure 终止且不向 agent 暴露。

## Stage 2.3 Runtime Evidence-loop Integration

核心完成：

```text
real Preflight
→ real CSYNTH
→ real Public CSIM
→ real Hidden CSIM
→ accepted / rejected / blocked / review_required / repair_pending
```

完成：

- generic ValidationOrchestrator；
- real Preflight handler；
- real CSYNTH handler；
- split-aware Public/Hidden CSIM handler；
- shared RunContext / BudgetManager / TraceRecorder；
- Public 收集非终止反馈；
- Hidden fail-fast；
- safe trace；
- runtime lazy exports；
- zero-budget pre-launch block。

真实 Vitis 2023.2 完整链预算：

```text
tool_calls=6
compile_calls=3
csynth_calls=1
csim_calls=2
```

验收目录：

```text
/data/agrefactor_runs/stage2_real_csim_handler_resume5_20260717_184240
```

功能提交：

```text
a354eb085700e2240dd4ace0d53fdb394d3e0e1a
feat: add split-aware csim validation handler
```

## Stage 2.4.1 Shared Layered Prompt Core

已完成。

提交：

```text
c4467067542b214bda86ee37839276a3fa58cd89
feat: add shared layered prompt core
```

完整测试：

```text
550/550 passed
```

核心类型：

```text
PromptPurpose
PromptArtifact
ModificationScope
PromptOutputContract
LayeredPromptRequest
LayeredPrompt
SharedLayeredPromptBuilder
```

已有 Purpose：

```text
testbench_repair
candidate_compile_repair
candidate_csynth_repair
candidate_public_csim_repair
```

已验证：

- provider-neutral；
- tool-free；
- deterministic；
- TargetProfile layer；
- modification scope；
- output contract；
- family instruction；
- prior attempts；
- caller-approved memory snippets；
- agent-safe feedback only；
- Hidden rejection；
- operator evidence omission；
- absolute path redaction；
- TaskSpec host path omission；
- output artifact 必须属于 editable scope；
- 此阶段不调用模型或工具。

验收目录：

```text
/data/agrefactor_runs/stage2_layered_prompt_core_resume2_20260717_195229
```

## Stage 2.4.2 Testbench Repair Migration

已完成。

提交：

```text
ec9802c12c9612ad8652ec35afd664a82c9d726f
refactor: migrate testbench repair to layered prompts
```

测试：

```text
99/99 targeted passed
554/554 full passed
```

完成：

- 删除旧 `_BASE_SYSTEM_PROMPT`；
- ModelTestbenchRepairer 委托 SharedLayeredPromptBuilder；
- Preflight operator report 转为 agent-safe report 后进入 Prompt；
- TestbenchRepairRequest 正式携带 TaskSpec；
- Legacy ContextVariables.target_profile 解析为真实 TargetProfile；
- testbench 是唯一 editable artifact；
- original program 与 candidate kernel 只读；
- ABI/linkage、private dependency、state isolation 和 preservation contract 保留；
- repairer.prompts / last_prompt 提供 manifest 审计；
- model parameters、token/cost usage、bounded repair 行为保持。

真实本地验收：

```text
real initial g++ Preflight
→ agent-safe FeedbackReport
→ SharedLayeredPromptBuilder
→ deterministic local FakeProvider
→ TestbenchRepairContract
→ real repaired g++ Preflight
→ passed
```

预算：

```text
tool_calls=2
compile_calls=2
csim_calls=0
csynth_calls=0
```

验收目录：

```text
/data/agrefactor_runs/stage2_testbench_layered_prompt_migration_resume3_20260717_210348
```

# 五、Stage 2.4.3.1 Candidate Prompt Policies 已完成

功能提交：

```text
dc44be344f9bf9bae3eb8e43675fb49f0c017708
feat: add candidate repair prompt policies
```

测试：

```text
20/20 targeted passed
574/574 full passed
```

实现边界：

- `CandidateRepairPromptInputs` 是唯一公开输入对象；
- 公开 API 只有三个构建函数：
  `build_candidate_compile_repair_prompt(...)`、
  `build_candidate_csynth_repair_prompt(...)`、
  `build_candidate_public_csim_repair_prompt(...)`；
- 三个函数委托同一个私有 policy implementation；
- 最终渲染继续唯一复用 `SharedLayeredPromptBuilder`；
- candidate 是唯一 editable artifact；
- original 始终只读；
- Compile：Public testbench 可选，存在时只读；
- CSYNTH：Public testbench 可选，存在时只读；
- Public CSIM：Public testbench 必须存在且只读；
- CSYNTH Policy 只接受 stage=csynth、owner=candidate、
  blocking=true、agent-safe 的反馈；
- toolchain、configuration、evaluator、unknown 等 CSYNTH
  failure 不得进入 Candidate Repair Prompt；
- Public CSIM 要求 explicit public split 和
  `feedback_visible_to_agent=true`；
- Hidden、operator-full、wrong owner 和 wrong stage 均拒绝；
- 不调用模型、网络或工具，不执行 repair loop。

确定性验收目录：

```text
/data/agrefactor_runs/stage2_candidate_prompt_policies_acceptance_recovery_20260718_035345
```

该验收不是 CandidateGenerator、真实模型 API、自动 repair loop、
ValidationOrchestrator 模型接入或真实 Vitis candidate repair。

# 六、当前唯一主任务：Candidate Model Adapter / Response Contract

当前只做：

```text
provider-neutral Candidate Model Adapter
complete candidate C++ response extraction
response rejection contract
model usage / prompt manifest audit boundary
```

当前绝对不要做：

- 不创建自动 candidate repair loop；
- 不把模型 repair 接入 ValidationOrchestrator；
- 不修改状态机自动调用模型；
- 不接入 Hidden feedback；
- 不开始 Stage 3 optimizer；
- 不实现 Memory retrieval 或 applicability scoring；
- 不开始 multi-kernel smoke；
- 不声称 Candidate Repair 闭环已完成。

# 七、下一任务设计边界

下一里程碑应消费已经构建好的 `LayeredPrompt`，负责：

- 通过既有 Model Registry / provider 进行一次受控调用；
- 解析且只接受一个完整 candidate C++ replacement；
- 拒绝 commentary、空输出、多代码块、patch/diff 和非法格式；
- 保存 prompt manifest、normalized response 和 usage；
- 不执行 compile、CSIM、CSYNTH；
- 不负责 repair loop、预算迭代或状态机转移；
- 不接触 Hidden evidence。

# 八、后续路线

```text
Stage 2.4.3.1 Candidate Prompt Policies（已完成）
→ Candidate Model Adapter / Response Contract
→ bounded Candidate Repair Loop
→ ValidationOrchestrator 接入
→ Stage 2.5 Multi-type Kernel Smoke Matrix
→ Stage 2.6 Final Documentation and Stage 2 Closure
→ Stage 3 Safe Three-Level Optimizer
→ Stage 4 Memory Applicability Gate
→ Stage 5 Target Version Extension / Migration
→ Stage 6 System Evaluation
```

Stage 2 未关闭前，不进入 Stage 3。

# 九、新对话工作方式

先阅读：

```text
1. docs/NEXT_CHAT_HANDOFF.md（若已提交）
2. docs/PROJECT_STATE.md
3. docs/ROADMAP.md
4. docs/GOAL_TRACEABILITY.md
5. docs/STAGE2_EVIDENCE_LOOP.md
6. docs/stage2_acceptance.md
7. docs/stage2_runtime_evidence_acceptance.md
8. agrefactor/prompts/layered.py
9. agrefactor/prompts/candidate_repair.py
10. agrefactor/prompts/__init__.py
11. tests/test_candidate_repair_prompts.py
12. agrefactor/testing/model_testbench_repairer.py
13. agrefactor/testing/testbench_repair.py
14. Preflight / CSYNTH / Test Evaluation feedback adapters 和 views
15. 相关 tests
16. git log -15 --oneline
```

事实由以下共同决定：

```text
Git history
+ current code
+ tests
+ real acceptance artifacts
```

每个里程碑：

1. 检查 branch / HEAD / origin / remote / worktree；
2. 明确预期文件；
3. 使用唯一结构锚点；
4. py_compile；
5. 定向测试；
6. 全量 unittest；
7. acceptance；
8. git diff --check；
9. exact changed-file set；
10. 成功后 commit / push；
11. local = remote；
12. worktree clean；
13. 删除一次性脚本。

用户偏好：

- 中文；
- 复杂修改生成可下载 Bash 脚本；
- 脚本放 `/mnt/data`；
- 用户从 `/data` 执行；
- 不给大量零散命令；
- 脚本包含安全检查、修改、测试、验收、commit、push；
- 先 bash -n；
- 内嵌 Python 先编译；
- 失败时不回滚、不自动生成通用 recovery；保留现场、输出完整诊断和日志后停止；
- 不盲目回滚；
- 不重复运行会重复插入代码的旧脚本。

已知脚本陷阱：

- git status 会折叠未跟踪目录；使用 git ls-files --others --exclude-standard。
- 重复 marker 误判；使用完整唯一结构。
- re.sub replacement 会二次解释 `\n`。
- JSON 二次编码后子串匹配会误判。
- BudgetUsage 无 to_dict()，使用 dataclasses.asdict()。
- 失败脚本可能已完成部分修改，先识别 dirty file set。

# 十、回答用户时必须说明

- 做了什么；
- 解决了什么；
- 如何验证；
- real 与 deterministic 的区别；
- 还缺什么；
- 为什么这样拆下一步。

不得夸大：

- Prompt policy 不等于 CandidateGenerator；
- handler 不等于自动 repair 闭环；
- FakeProvider 不等于真实 API；
- 574 tests 不等于 574 个真实 kernel；
- 单一 Vitis 2023.2 不等于任意版本支持。

# 十一、下一对话第一项任务

请先：

1. 核对 branch、HEAD、origin、remote 和 worktree；
2. 确认功能提交 `dc44be344f9bf9bae3eb8e43675fb49f0c017708` 与后续文档提交都存在；
3. 阅读 Candidate Prompt Policy 代码、测试和验收产物；
4. 检查是否已有 Candidate Model Adapter / Response Contract；
5. 设计下一子里程碑的最小文件、API、不变量、测试和 acceptance；
6. 暂不实现 repair loop 或 orchestrator 模型接入。

第一条回复应明确：

```text
Stage 2.4.3.1 Candidate Compile / CSYNTH / Public CSIM Prompt
Policies 已完成，功能提交是 dc44be3，完整测试
574/574 通过。下一步只做 Candidate Model
Adapter / Response Contract；暂不启动自动 repair loop、
ValidationOrchestrator 模型接入、Hidden feedback 或 Stage 3。
```

# 十二、一句话状态

AgRefactor++ 已完成共享分层 Prompt Core、Testbench Repair 迁移，
以及 Candidate Compile、CSYNTH、Public CSIM 的确定性 Prompt
Policy；下一步是在不执行验证工具和不建立自动循环的前提下，
实现 provider-neutral Candidate Model Adapter 与严格响应契约。
