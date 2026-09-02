# AgRefactor++ Research Roadmap V2.3（全链路代码追踪与证据治理路线）

> 日期：2026-08-29
> 状态：**V2.3 当前路线；R2 shadow diagnostic 已通过外部验证与独立审计并接受**
> 审计分支：`research-roadmap-v2.3`
> R2 实施基线 HEAD 前缀：`2bc253a`
> 实现谱系 HEAD：`52d7d0097627ff1f92c3f384170bd1fd4771ada7`
> R1 实现基线：`4d4cfdb92c8d181cf607cdf79368a9585ec4ca0e`；行为父基线：`5ef7fa9a6011534362a2094e159eee75c672619c`
> 服务器确定性回归：R1 外部回执声明 `2342/2342`；该归档需独立复核，不能由本文自我验收
> 当前主要实证环境：Vitis HLS 2023.2
> 当前项目名：暂用 AgRefactor++；方法和结果稳定后再决定论文名/系统名
> 本文定位：V2.2 的完整继承版和执行治理修订版；保留 V2.2 的项目状态、八项能力、论文定位、相关工作、代码追踪和 R0-R6 有效内容，并在原章节中直接改写冲突条款。本文是 standalone 路线，不依赖另一个补丁文档。产品成功权威仍由真实验证与独立证据审计掌握。
>
> R0 独立审计：`agrefactor_r0_document_authority_sync_v1_20260824T172726Z_2546041.tar.gz`，SHA256 `7608be4b21ff2ceade20040caee255024a56666b2711b4a186b4c42360c13674`。

> **V2.3 阅读规则：** 本文完整包含 V2.2 的有效背景和历史追踪；同名章节中的 V2.3 表述是当前有效条款，历史快照只用于解释变更原因。V2.3 可脱离 V2.2 独立使用。

---

## 0. 为什么必须再写 V2.3

你对上一版提出的质疑是正确的：路线不能只在概念上合理，还必须知道每个目标将接到哪段现有代码、会影响哪些真实执行路径、由什么证据裁决、已有测试能保护什么，以及哪些看似“已有”的东西其实只是 schema、hook、Legacy 实现或过期文档。

因此，本版不再以“想新增哪些模块”为起点，而以以下问题为起点：

1. 普通用户从 CLI 发起一次 `refactor`、`optimize` 或 `full` 时，真实执行链是什么；
2. 原八项能力中，每一项在当前分支到底属于“产品闭环、局部实现、基础设施、Legacy baseline、未实现”中的哪一种；
3. 开放世界诊断、持续记忆和安全修复应插入现有链的哪个位置，能复用什么，不能绕开什么；
4. 现有 repair 到底有几条 lane、各自的 owner、阶段、次数、验证前缀和最终权威是什么；
5. 固定 FSM、开放错误类别、知识可信生命周期三者如何分工；
6. 论文主张如何避开 AgRefactor、ChatHLS、HLSmith、HLSRewriter、C2HLSC、HLSDebugger 已经覆盖的贡献；
7. R0–R6 的每项工作是否有明确代码落点、测试落点、真实工具证据和停止条件。

本版的基本纪律是：

- 类存在，不等于产品能力已完成；
- CLI 参数存在，不等于真实消费者已接通；
- prompt 中写了规则，不等于有确定性语义保护；
- deterministic regression 不等于真实 Vitis/真实模型验证；
- 历史 acceptance 不自动等于当前 head 的事实；
- proposed 类型名和现有类型名必须显式区分；
- AI 的判断不等于成功权威；
- 任何新增机制必须复用现有预算、身份、证据和 Hidden 边界。

---

## 1. 审计范围、方法与证据等级

### 1.1 本次实际核对的代码域

本版逐项核对了以下真实代码域，而不是只看 roadmap：

| 代码域 | 重点核对内容 |
|---|---|
| `agrefactor/product/source_bootstrap.py` | 普通 source-only 入口、Legacy generation-only bridge、suite 物化、AUTO Public TB 准备性修复、formal handoff、full handoff、CLI consume-or-reject |
| `flow/new.py`、`agrefactor/compat/legacy_refactor.py` | 初始 Public TB、Candidate、Hidden TB 的实际生成顺序与旧多智能体生成职责 |
| `agrefactor/config/` | `TaskSpec`、`TargetProfile`、test source、test generation、repair budget |
| `agrefactor/models/` | registry、effective config、family profile、provider、usage/cost、固定模型解析 |
| `agrefactor/prompts/` | repair layered prompt、optimization prompt、Hidden source isolation、memory snippet hook |
| `agrefactor/runtime/*_stage.py` | Preflight、Public CSIM、CSYNTH、Public COSIM、Hidden 的真实 handler 和反馈出口 |
| `agrefactor/evaluation/` | typed feedback、owner、router、fixed FSM、qualification、stage adapters/composers |
| `agrefactor/repair/` | Candidate repair loop、response contract、attempt 语义、artifact |
| `agrefactor/recovery/` | conservative policy、ledger、timeout classification、DiagnosticAdvisory schema |
| `agrefactor/testing/` | Testbench preflight、repair loop、模型 repairer、结构合同 |
| `agrefactor/optimization/` | Structural/Bottleneck/Pragma、safe-v1、qualification、PPA、cache、checkpoint、best pointers |
| `agrefactor/runtime/budget*.py` | 硬预算、软预算、mode profile、Full phase reserve、共享计数 |
| `agrefactor/runtime/execution_identity.py`、`trace.py` | 运行身份、hash、prompt/tool/suite/candidate/budget/repository 证据、Hidden 视图 |
| `agrefactor/evidence/auditor.py` | 独立 false-success/terminal-conflict/identity 审计 |
| `agrefactor/campaign/runner.py` | durable campaign、case timeout、heartbeat、fail-soft、eligibility |
| `flow/rag/` | AgRefactor Legacy successful/failed trial、embedding retrieval、prompt 增强 |
| `tests/` | 上述能力的代表性回归保护和当前测试边界 |
| `docs/roadmap/`、`docs/acceptance/`、Git history | 旧状态、设计承诺、真实实现时间线、文档漂移 |
| 2026-08-24 服务器重放归档 | P0 修补、三案例重放、真实 Vitis 2023.2、发布回执、非自验收边界 |

当前仓库规模的只读统计为：

```text
tests files=166
Python modules under agrefactor/ and flow/=137
docs files=133
```

这些数字只用于说明审计面，不作为“能力已完成”的证据。

### 1.2 本文使用的实现状态

| 状态 | 严格含义 |
|---|---|
| **产品闭环** | 普通产品入口可触发；有真实 producer 和 consumer；共享预算/trace/identity；能到明确终态 |
| **产品局部闭环** | 只在特定来源、阶段、模式或条件下有完整执行器，不能泛化成全局能力 |
| **已实现但非主线** | 代码与测试真实存在，并可由特定入口触发，但当前不再扩张为论文主贡献 |
| **基础设施 / hook** | 类型、协议或插槽存在，但普通产品运行时缺生产者、消费者或控制闭环 |
| **Legacy baseline** | `flow/` 旧路径真实可用，可作为对照/迁移源，但不等于现代产品机制 |
| **未实现** | 当前分支没有满足所述合同的实现 |
| **文档承诺** | 只出现在 roadmap/acceptance/backlog，代码并未兑现或已经漂移 |
| **Proposed** | 本路线建议的新类型/模块；不得在论文现状中写成已实现 |

### 1.3 证据等级

后续所有“完成”声明至少注明证据等级：

| 等级 | 含义 |
|---|---|
| E0 | 设计或文档 |
| E1 | schema/unit test |
| E2 | 入口接线和确定性集成测试 |
| E3 | 真实网络模型或真实 Vitis 的单点/小规模 smoke |
| E4 | 冻结协议、多案例、重复、独立审计 |
| E5 | 时间外推/跨分布/跨环境实证 |

当前 `2335/2335` 是强 E2 回归证据；重放包中的真实 Vitis 2023.2 是针对指定案例和阶段的 E3 证据；它们都不是 E4/E5 泛化结论。

---

## 2. 当前权威基线与已经发现的状态债务

### 2.1 当前真实行为基线

本节保留 V2.2 的行为父基线作为历史证据；当前实现 HEAD 和当前状态以本文第 12 节快照为准。

```text
branch=stage2-general-feedback
implementation_head=52d7d0097627ff1f92c3f384170bd1fd4771ada7
r1_implementation_base=4d4cfdb92c8d181cf607cdf79368a9585ec4ca0e
behavior_parent_head=5ef7fa9a6011534362a2094e159eee75c672619c
r1_external_regression_declared=2342/2342
external_archive_independently_reproduced_here=false
source_hashes_verified=true
primary_vitis=2023.2
remote_branch_pushed=true
```

### 2.2 2026-08-24 修补与重放能证明什么

综合包在基线 `f65d83d` 上隔离应用并发布了三处修改：

- `flow/tools/general.py`：命令执行/进程组与超时硬化；
- `agrefactor/runtime/cosim_stage.py`：COSIM runtime contract v2 归一化；
- `tests/test_p0_consolidated_runtime_hardening.py`：新保护测试。

真实重放结果：

| 案例 | 阶段 | 结果 | 正确解读 |
|---|---|---|---|
| Strassen | CSYNTH | 成功 | 真实 Vitis 2023.2 CSYNTH 成功；不证明全流程或修复质量 |
| Aho-Corasick | COSIM | 通过 | 真实 COSIM 通过，typed outcome 被归一化 |
| LinkedList | COSIM | 失败、owner unknown | 系统没有误判成功，也没有伪造 owner；仍缺开放世界诊断/可执行修复证据 |

归档还明确写有：

```text
P4_0F_COMPLETE=false
PRE_STAGE4_COMPLETE=false
STAGE4_ALLOWED=false
causal_root_cause_proven=false
package_self_acceptance=false
```

因此，P0 修补关闭了两个基础设施风险，但没有自动关闭论文主线，也没有证明 LinkedList 根因已被学习或修复。

### 2.3 V2.2 历史文档漂移与 V2.3 修订

`docs/roadmap/PROJECT_STATE.md` 和 `pre_stage4_current_state.json` 仍停留在：

```text
behavior_head=0ca5dd9...
regression=2268/2268
NEXT_STEP=P4-0F-R5-E-R1
```

这是 V2.2 时代的文档漂移。V2.3 将其作为 historical evidence，并要求 authority index 标记 current/superseded；旧 backlog 把 `dynamic-v1` 写成 Stage 4 前硬要求的表述不再是当前执行指针。

结论：当前首先存在的是**权威状态债务**，不是缺一个新算法。V2.3 的 R0 必须把代码、服务器证据、PROJECT_STATE、JSON state、ROADMAP、GOAL_TRACEABILITY 和 backlog 的权威关系重新冻结。

---

## 3. 真实产品拓扑：从入口到裁决

### 3.1 普通 `refactor` 的实际主链

当前普通 source-only refactor 并不是完全由现代 runtime 从零生成。真实结构是：

```text
CLI / SourceBootstrapRequest
→ resolve EffectiveModelConfig / TargetProfile / EffectiveRunBudget
→ LegacyRefactorAdapter 以 generation_only 调用 flow.new
   → Public Testbench 生成或读取 provided test
   → identification / planning
   → initial Candidate 生成
   → Candidate 之后才生成 Hidden Testbench（若启用）
→ SourceBootstrap 物化 Candidate 与 suite provenance
→ AUTO Public Testbench 独立准备性 preflight/repair
→ CandidateRepairOrchestrationRequest（llm_advisory_mode=off）
→ modern formal validation
   → Preflight
   → Public native Vitis CSIM
   → CSYNTH
   → Public RTL COSIM
   → Hidden terminal evaluation
→ deterministic Router/FSM 请求 Candidate 或 Testbench recovery
→ full prefix revalidation
→ final candidate / summary / trace / identity / budget / audit artifacts
```

关键事实：

1. `flow.new` 的 generation-only 返回值不是最终成功权威；代码明确写 `legacy_success_is_final_verdict=false`。
2. 初始 Candidate/Public/Hidden 生成仍依赖 Legacy bridge，因此不能说“所有 prompt 已统一到 `SharedLayeredPromptBuilder`”。
3. formal request 当前明确设置 `llm_advisory_mode="off"`，所以 `DiagnosticAdvisory` 不是当前普通产品能力。
4. original top 与 candidate top 必须不同，才能做正式 differential comparison。
5. AUTO Public Testbench 的准备性 repair 在 formal FSM 之前；它与 formal runtime TB recovery 不是同一条 lane。

### 3.2 Public/Hidden 生成与可见性边界

`flow/new.py` 记录并检查生成顺序：

```text
public_generation
→ candidate_generation
→ hidden_generation（若启用）
```

`model_data_boundary` 要求：

- Public generation 不读取 Hidden；
- Candidate generation 不读取 Hidden；
- Public/Candidate repair 不读取 Hidden；
- Hidden 只在 Candidate 生成完成后产生；
- Hidden generation 输入是 Original 和固定 Public ABI declaration，不把 Hidden 再反馈给 Candidate；
- `assert_hidden_test_sources_absent` 进一步禁止 Hidden path、digest、完整源码进入模型可见消息或 artifact。

这是一项已经实现的重要安全能力，应进入论文系统描述，但它不是 Memory Gate。

### 3.3 `full` 与 `optimize`

`full`：先完成 refactor 并取得 accepted material，再把 final candidate、reference、Target、Public/Hidden suites 和 provenance 交给 Stage 3 optimizer；同一 `UnifiedRunner` 共享 BudgetManager 和 TraceRecorder，并为后续 optimize 预留预算。

direct `optimize`：要求独立 reference source、provided Public 和 provided Hidden；先完整 qualification baseline，之后才允许模型分析。当前 direct optimize 明确拒绝若干只属于 generation 的 CLI 控制，且尚不消费 `--public-test-contract`。

### 3.4 Advanced/Legacy 路径的边界

`agrefactor.cli` 和 `flow.new` 仍保留许多历史/高级入口。它们可以作为兼容与 baseline，但路线不得用这些入口的能力替普通产品入口代言。例如：

- Legacy RAG 能写 successful/failed trial，不等于现代 Memory Gate；
- Legacy iteration 能根据错误继续改，不等于 conservative RecoveryPolicy 已全面接线；
- Legacy prompt/YAML agent 不能自动算作 shared layered prompt consumer；
- advanced 参数能被构造，不等于 source product CLI 已真实消费。

---

## 4. 原八项能力：逐项代码依赖与正式处置

### 4.1 总表

| 原能力 | 当前真实状态 | 主要 producer → consumer | 真实缺口 | 新定位 |
|---|---|---|---|---|
| TargetProfile | 产品闭环，限已声明字段/当前环境 | CLI/profile resolver → CSIM/CSYNTH/COSIM/optimizer/identity | 无 typed `platform`；跨版本/多设备未证；resource limits 多用于 optimizer feasibility | 必要实证基础 |
| 双模式版本处理 | 未实现为 migrate 产品闭环 | 只有 target profile 与 roadmap 预留 | 无 SourceProfile、migrate mode、迁移报告、跨版本验证 | 未来工作 |
| Model API Registry | 产品闭环工程能力 | runtime resolver → generation/repair/optimizer/provider/identity | 无 authorized auto pool；默认只有一个具体 DeepSeek 配置，其余多为 family 推断 | 工程支撑 |
| 分层 Prompt | repair 产品闭环 + optimization 独立 builders | typed feedback/Target/model → repair prompts；PPA evidence → optimizer prompts | 不是初始生成统一入口；未知 owner advisory 不能直接套当前 repair purpose | 复用并窄扩展 |
| 结构化反馈与 FSM | 产品闭环 | stage artifact → adapter/composer → FeedbackReport → Router → fixed FSM | regex/open-world coverage 不全；部分合法 route 无 executor；AI advisory 未接线 | 论文安全核心 |
| 三级安全优化器 | 产品闭环但仅 safe-v1 | accepted baseline → 3 levels → qualification/PPA → checkpoint/best pointers | stable PPA/generalization 未证；dynamic-v1 只是旧文档承诺 | 次要能力，冻结扩张 |
| Memory Applicability Gate | 未实现；仅 hook + Legacy baseline | `approved_memory_snippets` 可进入 repair prompt；Legacy Chroma 可检索 | 无 episode/pattern/gate/lifecycle/conflict/time split/现代产品 store | 论文方法核心 |
| BudgetManager | 产品闭环工程能力 | mode profile → shared manager → every counted call → identity/summary | token/cost observed-only；repair 有多层配额，缺统一 effective quota 解释 artifact | 公平与安全基础 |

### 4.2 TargetProfile：保留，但纠正旧路线的过度表述

真实字段见 `agrefactor/config/target.py`：

```text
name
toolchain
toolchain_version
device
clock_period_ns
compile_flags
executable
settings_path
parser_profile
resource_limits
per-field provenance
```

真实消费者：

- `flow/tools/csynth.py`：resolve executable/settings/version，Tcl 写 top/device/clock/flags，运行 `csynth_design`；
- `flow/tools/vitis_csim.py`：真实 Vitis CSIM Tcl、target/version/invocation evidence；
- `flow/tools/vitis_cosim.py`：CSIM→CSYNTH→COSIM，interface depth、target/version/evidence；
- `runtime/*_stage.py`：把 task target 交给工具；
- `optimization/`：parser profile、clock、device、resource feasibility、toolchain fingerprint；
- `execution_identity.py`：记录请求 profile、实际版本、executable/settings identity 和 fingerprint。

必须纠正：当前类型中没有通用 `platform` 字段；旧路线的 “part/platform 都已真实控制”不准确。`device` 可承载 part/device；platform 不是当前 typed capability。Resource limits 主要用于 PPA feasibility，不等于向 Vitis invocation 施加所有资源约束。

决策：保留 TargetProfile，不为论文创新；当前只对 Vitis 2023.2 已验证环境主张。R0 核对文档，R1 确保所有新 episode 绑定 effective target/tool fingerprint。

### 4.3 双模式版本处理：明确退出当前关键路径

搜索当前代码没有发现完整的：

```text
RunMode.MIGRATE
SourceProfile
source_toolchain_identity
source→target compatibility contract
migration report
cross-version qualification
```

文档中的“migration”多数是内部 API/配置兼容迁移，不能与 HLS 版本迁移混为一谈。

决策：版本迁移进入未来工作。当前论文不能把 TargetProfile 当作版本迁移；未来只有在 2023.2 主线稳定后，再选择至少一个新版本/环境做外部验证或新研究分支。

### 4.4 Model API Registry：已是工程底座，不再追逐模型路由创新

`ModelRegistry` 真实支持：

- logical model/provider/family profile；
- immutable `EffectiveModelConfig`；
- fixed 用户选择；
- OpenAI-compatible provider；
- api key env，仅保存变量名而不保存 secret；
- family-specific reasoning/output policy；
- role-specific effective parameters；
- token usage、pricing snapshot 和 estimated cost。

真实消费者包括：initial source/test generation bridge、Candidate repair、Testbench repair、Structural/Bottleneck/Pragma analysis/rewrite、Optimize recovery、real network smoke。

边界：当前没有产品级 authorized auto pool、fallback router 或动态择模；默认 concrete profile 主要是 DeepSeek V4 Flash，其他模型多通过 family/generic inference。决策：保持 fixed/provider-neutral，不把自动路由放入当前论文主线。

### 4.5 分层 Prompt：不是“一套 builder 覆盖所有角色”

`agrefactor/prompts/layered.py` 的 `PromptPurpose` 当前只覆盖 repair：

- Testbench repair；
- Candidate compile repair；
- Candidate CSYNTH repair；
- Candidate Public CSIM repair；
- Candidate Public COSIM repair。

它强制：blocking feedback、owner 与 purpose 匹配、stage 匹配、Public 可见性、scope/output contract、Target/model family、hash/manifest、prior summaries、可选 `approved_memory_snippets`。

但：

- 初始 generation 仍由 Legacy YAML/agents/prompt 路径完成；
- optimizer 使用 `agrefactor/prompts/optimization.py` 中独立的 typed builders；
- 当前 repair builder 要求 owner 已匹配，不能直接处理 Unknown-owner advisory；
- memory hook 只是 caller-approved 文本入口，不是检索和 gate。

决策：不重写现有 repair builder。R2 新增相邻的 advisory-safe builder，复用公共 sanitization、Target、identity、evidence refs 和输出合同；R3 让 Gate 只输出受审计 snippet manifest，再送入已有 hook。若未来统一抽公共 layer，只抽没有改变 authority 的稳定部分。

### 4.6 结构化反馈与状态机：核心是“固定骨架 + 开放诊断”，不是 AI 写 FSM

真实固定状态：

```text
PREFLIGHT
PUBLIC_EVALUATION（有 Public 时）
CSYNTH
PUBLIC_COSIM（有 Public 时）
HIDDEN_EVALUATION（有 Hidden 时）
REPAIR_PENDING
REVIEW_REQUIRED / BLOCKED / ACCEPTED / REJECTED
```

成功顺序由 `_advance` 和 `_required_states` 决定。Hidden 是 terminal：失败不进入 agent repair，report id 和 selected items 不保留给 agent。

`FeedbackRouter` 的真实动作包括：continue、repair candidate/testbench/original、fix toolchain/config/task input、review unknown/mixed、stop budget。Unknown 不默认归 Candidate；多个 blocking 方向进入 review。

历史核对表明：工作流状态主要在 2026-07-17 作为架构一次性建立。之后随真实故障增加的主要是 diagnostic category、owner 规则、timeout class 和 parser pattern，例如 `c4227e9` 在真实问题后补 HLS 214-133 global variable 诊断。这与“每个错误新增一个 FSM 状态”不同。

决策：FSM/层级冻结。AI 可补开放词表诊断、owner hypothesis、repair hypothesis 和 condition，但不能新增状态、改 transition 或宣布 success。

### 4.7 三级安全优化器：保留已有正确性边界，不再作为当前论文主创新

当前真实 `safe-v1`：

| Level | 最大轮数 | 每轮 hypotheses | 每轮执行 branch |
|---|---:|---:|---:|
| Structural | 2 | 3 | 1 |
| Bottleneck | 2 | 3 | 1 |
| Pragma | 3 | 3 | 1 |

总执行候选最多 7；目标固定 latency；candidate correctness repair policy 原本为 0，但 Pre-Stage-4 另有每 root 一次的 bounded optimize recovery，限 Preflight/CSYNTH legality。

真实安全能力：baseline-first qualification、Public→CSYNTH→Public COSIM→Hidden→PPA/feasibility、complete-source contract、typed abstention、checkpoint/recover、cache identity、candidate lineage、rollback、`best_correct`、`best_ppa`、budget exhaustion 保留 best correct。

真实边界：产品只接受 `safe-v1`；stable PPA improvement、跨 kernel 优越性没有被当前回归证明。旧文档的 `dynamic-v1` 是未兑现承诺。

决策：优化器保持可用，作为系统完整性和潜在 memory consumer；不在当前主线继续发明 dynamic policy 或追 PPA SOTA。若论文版面有限，可把优化器降为 supporting subsystem/secondary evaluation。

### 4.8 Memory Applicability Gate：当前真正缺失的研究层

当前只有两类可复用基础：

1. 现代 prompt hook：`approved_memory_snippets`；
2. Legacy RAG：Chroma successful/failed trials，基于 code/items embedding 检索并把成功 plan 注入 planner。

当前没有：

- modern `MemoryMode` 产品合同；
- context-bound `DiagnosticEpisode` store；
- positive/negative application outcome；
- versioned pattern revision；
- target/stage/owner/interface condition；
- gate reject/abstain；
- lifecycle/promotion/deprecation；
- time-ordered evaluation；
- negative-transfer auditing；
- product-level retrieval manifest。

决策：Memory Gate 是当前论文核心，但必须建在 typed evidence/identity/revalidation 上，不能把 Legacy 相似度检索换个名字。

### 4.9 BudgetManager：当前已硬控制的范围与真实边界

硬预算字段：

```text
LLM calls
tool calls
compile calls
CSIM calls
CSYNTH calls
COSIM calls
wall time
```

Token 和 estimated cost 在当前产品 profile 中明确是 `observed_only`，不阻塞运行。默认 mode profile：

| 模式 | LLM | Tool | Compile | CSIM | CSYNTH | COSIM | Wall time |
|---|---:|---:|---:|---:|---:|---:|---:|
| Refactor | 96 | 192 | 96 | 64 | 32 | 32 | 10800s |
| Optimize | 24 | 160 | 72 | 24 | 12 | 12 | 1800s |
| Full | 120 | 352 | 168 | 88 | 44 | 44 | 12600s |
| Safety ceiling | 256 | 512 | 192 | 128 | 64 | 64 | 14400s |

Full 在 refactor 阶段为后续 optimize 保留 optimize 默认硬容量；同一 runner 使用同一 manager。

决策：不重做 BudgetManager。R1 只新增 proposed `EffectiveRepairQuotaSummary`，把 CLI max、lane local max、RecoveryPolicy、run total actions、restart budget 和 shared hard budget共同决定的实际窗口解释清楚。

---

## 5. 除八项之外，当前已经实现的能力

| 能力 | 当前代码事实 | 论文/路线定位 |
|---|---|---|
| Source-only bootstrap | 普通 `refactor SOURCE --top TOP`；request、model、target、budget、suite 全物化 | 产品底座 |
| Legacy generation-only bridge | Public→Candidate→Hidden 生成，legacy success 非最终权威 | 需诚实描述的架构现实；未来可渐进迁移 |
| Test source plan/provenance | AUTO/PROVIDED/NONE、Public/Hidden 独立来源、hash/version/trajectory | 安全与可复现底座 |
| Refactor eligibility | deterministic lexical structure，private mutable global dependency、Original CSYNTH identity-bound primary sample | 数据集与 campaign 质量控制 |
| Native Vitis CSIM | Public 使用真实 `csim_design` 和 version/invocation evidence | 实证权威 |
| Public RTL COSIM | typed outcome、interface depth、timeout/process linger、runtime contract v2 | 实证权威；LinkedList 研究语料来源 |
| Hidden terminal suppression | operator-full，agent 不见 source/report/items；repair=0 | 论文安全约束 |
| Timeout ownership | candidate deadlock/stream mismatch、TB protocol wait、toolchain stall、infra launch、unknown | 开放诊断 baseline 特征 |
| RecoveryPolicy/Ledger | conservative-v1、role/stage/action/lineage/total/restart limits | 修复权限内核 |
| DiagnosticAdvisory schema | evidence refs、owner、failure class、scope、confidence、abstain、accepted=false | R2 直接扩展，不另造重复 schema |
| Execution Identity | source/task/model/prompt/target/tool/suite/candidate/budget/repo hash | episode context 和可复现 identity 来源 |
| TraceRecorder | append-only、agent-safe/operator-full | 诊断/repair/learning trajectory 来源 |
| Independent evidence auditor | false success、process/full-result conflict、terminal conflict、identity conflict | R4/R5 独立安全审计扩展点 |
| Product concise/full output | summary、artifacts manifest、captured streams | 论文实验数据出口 |
| Durable campaign | serial fail-soft、heartbeat、case timeout、manifest、eligibility | R5 实验 runner 基础；尚无 memory arms/time split |
| Model usage/cost | native usage normalization、pricing snapshot、currency-aware estimate | 公平成本报告 |
| Optimizer artifact governance | immutable checkpoints、candidate index、PPA evidence、cache fingerprint | 次要能力和可复用治理样板 |

---

## 6. Repair 全量审计：不是一个循环，而是四条不同 lane

### 6.1 Lane A：AUTO Public Testbench 准备性修复

| 属性 | 当前事实 |
|---|---|
| 入口 | `SourceBootstrap` |
| 来源条件 | 仅 `public.mode=AUTO` |
| 时机 | formal FSM 之前 |
| owner | deterministic Testbench owner |
| 默认次数 | 3；CLI 范围 1..20 |
| attempt 消耗 | provider error、空/非法/不变输出同样消耗 |
| 成功后 | 新 TB 物化为 `DERIVED` provenance，记录 model/profile/prompt/trajectory/hash |
| Hidden | 不读取、不修改 |
| 主要缺口 | prompt/结构合同不能证明测试语义完全不变 |

### 6.2 Lane B：Formal Candidate repair

| 属性 | 当前事实 |
|---|---|
| 入口 | `CandidateRepairOrchestrator` / `BoundedCandidateRepairLoop` |
| 合法阶段 | Preflight、Public CSIM、CSYNTH、Public COSIM |
| owner | blocking items 必须一致为 Candidate 且 stage match |
| evidence | agent-safe；Public repair 需 Public 可见 TB/feedback |
| 默认次数 | 3；CLI 范围 1..20 |
| attempt 消耗 | provider/response/empty/unchanged 都计次 |
| 重验证 | 从合法最早前缀完整重跑，而非只重跑失败命令 |
| 失败 proposal | 可作为下一轮输入；不能作为 final valid candidate |
| final | 只输出最后验证通过 candidate 或初始 candidate |

### 6.3 Lane C：Formal Public runtime Testbench recovery

| 属性 | 当前事实 |
|---|---|
| 入口 | `_recover_public_testbench` |
| 合法阶段 | Public CSIM、Public COSIM |
| 条件 | exactly one deterministic Public suite；Testbench owner；agent-safe |
| 当前次数 | 代码硬编码每次 recovery 最多 1 |
| 重验证 | 修改 TB 后重新执行相关 validation chain |
| 不覆盖 | Hidden；formal Preflight；multi-suite ambiguous case |

### 6.4 Lane D：Optimize Candidate recovery

| 属性 | 当前事实 |
|---|---|
| 入口 | `BoundedOptimizeCandidateRecoveryCoordinator` |
| 合法阶段 | Preflight、CSYNTH legality |
| 次数 | 每 root candidate 最多 1 |
| lineage | 失败 candidate 保留；repair 是新 contiguous descendant |
| 重验证 | 完整 optimize qualification |
| best pointer | 只有完全 qualification/PPA 后才可能改变 `best_correct` |
| 禁止 | Public mismatch repair、Hidden repair、PPA-only repair、nested recovery |

### 6.5 RecoveryPolicy 的全局限制

`RecoveryLimits` 默认：

```text
provider_retries=1
response_regenerations=1
llm_advisories=1
tool_retries_per_stage=1
testbench_preflight_repairs=3
refactor_candidate_repairs_total=3
candidate_public_csim_repairs=1
candidate_public_cosim_repairs=1
testbench_public_csim_repairs=1
testbench_public_cosim_repairs=1
optimize_recoveries_per_root=1
hidden_repairs=0
total_recovery_actions=5
validation_restarts=4
```

注意：policy 中存在 retry/regeneration/advisory action 和计数，但没有发现所有这些 action 在普通产品中都具有完整 producer→ledger→executor→consumer 接线。因此只能说策略合同存在，不能说“统一 recovery 已覆盖所有动作”。

### 6.6 当前真实缺口

以下条目是 R1 前的历史缺口清单。V2.3 不把远端 R1 文档中的 `accepted=true` 视为自动关闭；每项必须由 R1-Safety/R1-Data 的四轴证据重新对照。

1. **Formal Preflight Testbench route 无完整执行器（R1 前）**：FSM/policy 可授权 deterministic TB Preflight repair；AUTO TB 有独立 pre-FSM lane；但 PROVIDED TB 在 formal Preflight 失败时没有对应 runtime executor，最终可能 `repair_not_applicable/review`。R1-Safety 必须证明其已闭合，或明确转 review。
2. **Testbench semantic integrity 不足**：现有 deterministic contract 能保护 main、top call、禁止 stub/wrapper/private helper，但不能证明 inputs、case count、expected values、tolerance、comparison/failure semantics 未变。Prompt 甚至允许在保留“meaningful comparison”时删除/替换 tests。
3. **有效次数难解释**：CLI max、lane max、RecoveryPolicy stage max、run total、validation restart、BudgetManager 共同生效，用户只看一个 `max_repair` 会误解。
4. **Unknown error 没有 provider-backed advisory runtime**：`DiagnosticAdvisory` 只有 schema/protocol，formal request 仍 off。
5. **Original route 没有自动执行器**：router 可产生 `repair_original`，policy 实际拒绝；正确处置应是 task correction/operator review。
6. **Memory 没有进入授权链**：只有 prompt hook，尚无检索、gate、application record 和 verified outcome。
7. **部分 provenance 不统一**：AUTO TB prep 的 DERIVED provenance 较完整；runtime TB recovery 需要同样严格的 revision/semantic manifest/identity。R1-Data 还必须提供可读取的 E3 corpus records，而不是只提供 writer/schema。

### 6.7 修订后的 Repair Authorization Matrix

| 对象 | 证据权威 | v1 自动权限 | 条件 | 失败后 |
|---|---|---|---|---|
| Candidate | deterministic owner | 保留 | Preflight/Public CSIM/CSYNTH/Public COSIM；agent-safe；全前缀重验 | 原/最后验证 candidate 保留 |
| Candidate | LLM advisory | **R4 才可新增** | high confidence；candidate-only；Gate/Policy/预算通过；无 Hidden；一次探索 | 未通过即 negative/inconclusive episode，不得成功 |
| AUTO Public TB | deterministic owner | 保留 | semantic manifest 不变量通过；bounded | 停在失败/review |
| PROVIDED Public TB | deterministic owner | 默认仅窄修 | 只允许可证明的 compile-shell/ABI 等价变更；否则需用户授权生成新 suite revision | review/新 revision |
| Testbench | LLM advisory | v1 禁止自动执行 | 可以给 operator hypothesis，不可自动改 TB | review |
| Hidden TB | 任意 | 永久禁止 | 无 | terminal reject/block/review |
| Original | 任意 | 禁止自动 repair | 原任务/输入需人工修正 | review/fix task input |
| Toolchain/config/environment | 任意 | 不改源码 | 独立 retry 或人工纠正需 deterministic policy | blocked/retry/review |

“v1 只修 Candidate”的准确含义是：**仅新增的 AI advisory 自动权限限 Candidate；现有确定性 Testbench repair 不删除。**

---

## 7. Testbench 安全：必须把“可编译”与“语义未被弱化”分开

### 7.1 Proposed `TestbenchSemanticManifest`

建议新增的不是一个自由文本说明，而是可 hash、可 diff、可审计的 manifest：

```text
suite_id / split / source_mode / revision_id / parent_revision_id
reference_top / candidate_top / required_call_sites
input generator identity / fixed vectors identity / seed policy
case count lower bound and observed count
expected-value oracle identity
comparison operators / tolerance / output fields
failure signaling contract / pass signaling contract
runtime protocol / stream or interface expectations
allowed_edit_classes
forbidden_edit_classes
manifest_sha256 / source_sha256
```

### 7.2 三种来源策略

**AUTO_GENERATED Public**：允许有界修复，但必须证明 oracle、case strength、comparison、failure signaling、Candidate/Original 调用结构未弱化；无法证明时生成新 revision 并重新 qualification，而不是原地覆盖。

**PROVIDED Public**：默认把用户测试当作语义权威。只自动允许可证明的 include、declaration、ABI shell、namespace/linkage 等窄修；输入、expected、tolerance、case 删除/替换属于语义变更，必须显式用户授权并形成新 revision。

**Hidden**：不修、不暴露、不进 memory content。Hidden 只可贡献 terminal aggregate outcome；不得反向生成修复提示。

### 7.3 R1 的实现落点

- 扩展 `TestSuite`/source provenance 或新增相邻 manifest artifact，而不是破坏旧 schema；
- 在 `_prepare_public_testbench` 和 `_recover_public_testbench` 前后做 deterministic semantic diff；
- 把 revision/hash 写入 Execution Identity；
- evidence auditor 检查 accepted run 是否引用已 qualification 的 semantic manifest；
- 为 AUTO、PROVIDED、Hidden 分别写 positive/negative contract tests。

V2.3 增加 API 级 fail-closed 要求：任何 semantic manifest/revision builder、DiagnosticEvent projector 或 corpus writer 收到 `split=hidden`、`hidden_input_count>0`、Hidden path/digest 或 operator-full evidence 时必须拒绝，不能只依赖调用方预先过滤。Hidden literal/oracle fingerprint、路径和可逆摘要不得进入 agent-safe artifact。

---

## 8. 三类研究状态与四轴执行状态必须严格分离

### 8.1 工作流状态：固定、有限、确定性

它回答“现在执行哪一步、下一步是否合法”。由 `ValidationStateMachine` 和 `RecoveryPolicy` 掌握。AI 不得新增/删除/改写。

### 8.2 诊断状态/类别：开放词表、可扩展

它回答“这次失败可能是什么、谁负责、证据是什么”。当前 regex/typed classifiers 是 deterministic baseline；AI 可在 Unknown 或不完整证据上产生 advisory hypothesis。

示例：

```text
candidate_hls_language_restriction
candidate_stream_protocol_mismatch
candidate_nontermination
testbench_abi_mismatch
testbench_protocol_wait
toolchain_internal_failure
environment_launch_failure
ownership_unknown
```

这些是诊断类别，不是 FSM 节点。

### 8.3 诊断知识状态：经验的可信治理

它回答“某个 repair pattern 在什么条件下被验证过，是否可用于新问题”。建议 lifecycle：

```text
Quarantined
→ Provisional
→ Trusted
→ Deprecated

Rejected revision（终止）
```

为什么需要 lifecycle：

- 一次成功可能是偶然或只适用于窄环境；
- 同一 pattern 在另一类接口/阶段/版本上可能造成 negative transfer；
- 经验会随工具版本、prompt/model、test contract 漂移；
- 不应把失败经验删除，否则无法学会 `avoid_when`；
- 自动权限应与证据强度分离：存储不等于信任，检索不等于注入，注入不等于执行。

### 8.4 对老师所说“AI 辅助持续学习”的精确解释

合理落点是：

```text
固定 FSM / Policy / Validator
+ AI 对开放世界错误做受约束解释
+ 真实工具重验证每次 repair application
+ 结果沉淀为条件化 episode
+ Gate 根据正负证据决定 accept/reject/abstain
+ 生命周期治理 pattern revision
```

不是：

- AI 自己增加 `xxxx_layer`；
- AI 看到新字符串就写新 FSM 状态；
- AI 把一次修复成功写成全局规则；
- AI 读取 Hidden 失败再修；
- AI 自己决定 accepted。

### 8.5 V2.3 四轴执行状态

除上述工作流状态、诊断类别和知识生命周期外，每个阶段还必须分别记录：

| 状态轴 | 含义 |
|---|---|
| `implementation_state` | producer、consumer、schema 和权限链是否存在 |
| `deterministic_state` | 合同、负例、降级和重放测试是否通过 |
| `real_evidence_state` | 是否实际运行 provider/Vitis/tool 并保存 counter/artifact |
| `independent_audit_state` | 是否独立核对 archive、hash、identity、Hidden 和 authority |

`implemented` 只能表示第一轴；`verified` 至少需要前两轴；`real_validated` 需要前三轴；`accepted` 必须满足该阶段所需四轴和独立审计。任何单一 `accepted=true` 字段不能替代四轴证据。

---

## 9. Proposed 开放世界诊断层：复用现有 typed evidence

### 9.1 不新建第二套工具事实管线

当前各阶段已经产生：

```text
physical invocation/result
→ stage-specific typed artifact
→ feedback adapter/composer
→ FeedbackReport / FeedbackItem
→ agent-safe or operator-full view
→ Router / FSM
→ trace / identity / product summary
```

因此，V2.1 中若把 `ToolEvent` 写成新的权威来源，会造成双 parser、双 owner、双 success authority。V2.2 改为 proposed `DiagnosticEventProjector`：它只从已有 typed artifact、FeedbackReport、ExecutionIdentity 和 safe trace 中投影研究输入，不重新解释物理成功。

### 9.2 Proposed `DiagnosticEvent`

最小字段：

```text
event_id / schema_version
run_id / task_id / lineage_id / attempt_id
stage / suite_id / split / evidence_view
source_report_id / selected_feedback_ids / artifact_refs
deterministic_category / deterministic_owner / deterministic_route
timeout_class / tool_launched / returncode / timed_out
target_identity / toolchain_fingerprint / parser_profile
candidate_hash / testbench_revision_hash / interface_contract_hash
evidence_completeness / ambiguity_codes
raw_text_digest + bounded safe excerpt（可选）
hidden_input_count=0 for model-visible events
```

它不是 success record；它只为 advisor、memory 和 evaluation 提供稳定上下文。

### 9.3 复用现有 `DiagnosticAdvisory`

当前 schema 已有：

```text
suspected_owner
suspected_failure_class
evidence_refs
repair_scope
confidence
abstain_reason
owner_authority=llm_advisory
accepted=false
```

且 validator 要求完整 run identity、真实 physical tool launched、引用证据必须是 request 子集、无 Hidden/secret/private reasoning。

R2 应新增：

- provider-backed adapter；
- strict JSON/output normalization；
- advisory-specific safe prompt builder；
- shadow runtime producer；
- advisory artifact/usage/cost/identity；
- 与 deterministic owner 的离线/在线比较；
- 不一致时不改变 FSM。

不得重复造另一个 `AIErrorDiagnosis` 类族，也不得让 advisory 直接变成 FeedbackReport 的 success owner。

### 9.4 Shadow-first

R2 默认：

```text
advisor reads safe DiagnosticEvent
→ emits advisory or abstain
→ artifact records prediction
→ deterministic product path proceeds unchanged
→ later verified outcome labels usefulness
```

只有在 R2 的 owner/failure-class/abstention 审计完成后，R4 才允许 candidate-only exploratory repair。

R2 shadow 必须与主路径预算和时间隔离。若使用同一 `BudgetManager`，必须在调用前保留独立 shadow reserve，并在 artifact 中分开记录 main/shadow consumption。provider error、timeout、invalid JSON、evidence ref 越界或 scope 越权只能产生 shadow failure/abstain，不得改变 deterministic route、status、ledger、repair count 或 `best_correct`。`confidence` 必须在预先冻结的 calibration split 上评估，不能直接采用模型自报 high。

---

## 10. Proposed 验证式持续记忆

### 10.1 正负不是 pattern 的永久标签

用户提出的关键情况——“同一经验在 A 有效，在 B 有害”——必须原生支持。

所以：

- 正/负属于一次 **application episode outcome**；
- pattern 记录 `supported_when` 和 `avoid_when`；
- gate 对新 context 比较相似正例与冲突负例；
- 更精确的强负证据覆盖模糊正证据；
- 同强度冲突时必须 abstain/review；
- 过宽 pattern 不删除，创建收窄的 child revision，并 deprecated/reject 旧 revision。

### 10.2 一级：不可变 `DiagnosticEpisode`

建议字段：

```text
episode_id / created_at / sequence
diagnostic_event_id / execution_id / request_identity
context_signature
deterministic diagnosis / advisory diagnosis
retrieved_pattern_revisions / gate decision
repair authorization / repair action / changed scope
candidate/testbench before-after hashes
full revalidation steps and evidence refs
outcome
cost/budget delta / latency
false_repair / negative_transfer / abstention flags
```

Outcome 至少：

```text
verified_positive
verified_negative
abstained
inconclusive
invalid_evidence
```

解释：

- `verified_positive`：修复后完整规定链通过，且不违反 semantic/authority contract；
- `verified_negative`：只有在 Gate/Policy 已授权、before/after identity 完整、失败可归因于该 pattern application 且排除 toolchain/environment/infrastructure 后，才表示失败、退化、错误 owner、语义弱化或 negative transfer；
- `abstained`：Gate/Advisor 明确拒答，没有修复；
- `inconclusive`：预算/基础设施/未知阻塞、identity 不全或证据冲突，不能归因 pattern；
- `invalid_evidence`：身份不全、Hidden 泄漏、artifact 冲突等，不得用于 promotion。

### 10.3 二级：版本化 `RepairPatternRevision`

建议字段：

```text
pattern_id / revision_id / parent_revision_id
failure_family / owner / allowed_stage / repair_scope
supported_when / avoid_when
target/toolchain/parser constraints
interface/testbench/runtime constraints
required_evidence predicates
repair intent/template（不是完整私有源码）
positive_episode_refs / negative_episode_refs
support counts by context bucket
lifecycle / promotion reason / deprecation reason
created_by / reviewed_by / schema/prompt/model identities
```

### 10.4 Applicability Gate 的决策顺序

1. 权限硬拒绝：Hidden、secret、identity incomplete、unsupported role/stage；
2. exact context exclusions：target/tool/parser/interface/testbench provenance；
3. deterministic owner/route 与 pattern owner 是否兼容；
4. required evidence 是否完整；
5. `avoid_when` 是否命中；
6. 条件化 positive/negative episode 支持强度；
7. 冲突、样本稀疏和分布外检测；
8. 输出 `accept / reject / abstain`，并写 reason codes 和引用；
9. accept 只代表“允许把 memory 作为建议输入”，不代表允许成功；
10. RepairAuthorization 还要经过 RecoveryPolicy、lane、预算和完整重验证。

### 10.5 生命周期

| 状态 | 进入条件 | 权限 |
|---|---|---|
| Quarantined | 新 episode/自动抽取/证据未审 | 只存储，不注入 |
| Provisional | identity 完整、至少一条合格证据、scope 明确 | shadow retrieval；默认不自动授权 |
| Trusted | 冻结阈值、跨独立 context 通过、negative rate/false repair 达标 | 可被 Gate 接受，仍需 Policy/验证 |
| Deprecated | 漂移、被更窄 revision 替代、负证据上升 | 不再用于自动授权，保留研究 |
| Rejected revision | 安全违例、错误因果、不可修复过宽 | 永不自动使用，保留审计 |

阈值不在本路线凭空填写。R1 corpus audit 后冻结，且在正式实验前不得调参追测试集。

V2.3 要求在 R3 实现前冻结：最小 positive episode 数、最小独立 context 数、最大 attributable negative rate、最大 false-repair rate、最大 unsafe-scope rate、最小 citation validity、calibration requirement 和 deprecation window。样本不足时只能停留在 Quarantined/Provisional。

### 10.6 Legacy RAG 的迁移位置

Legacy `KnowledgeDB` 只有 successful/failed trial 和 embedding distance；failed trial 主要用于统计 missing items，successful plan 可直接增强 planner。它没有现代 identity、适用条件、gate、lifecycle 和 negative-transfer authority。

因此它用于：

- baseline arm；
- 导入候选历史 episode 时的原始数据源；
- 对比 `similarity-only memory` 与 `evidence-gated memory`；
- 不能直接 promotion 为 Trusted。

---

## 11. 论文定位：不能只说“加 AI、加 RAG、加错误库”

### 11.1 与 AgRefactor 的关系

AgRefactor 已经主张 multi-agent refactoring、自演化 memory、trial-and-error 和 automated tools。AgRefactor++ 若只做“更多错误经验/更多 RAG”会与基线重叠。

本项目应把差异收束为：

- 真实工具证据和 owner-aware authority；
- 固定安全控制内核与 AI advisory 分权；
- 条件化正负 application memory；
- lifecycle、abstention、false repair、negative transfer；
- Testbench/Hidden 边界；
- 时间顺序和独立审计。

### 11.2 与 HLSmith 的高重合部分

HLSmith 已包含 guarded transformation recipes、applicability/prerequisite、unsafe cases、staged feedback-driven orchestration、tool-grounded trajectories。它与“专家规则 + 条件 + 避免项 + 分阶段工具反馈”的重合很大。

所以不能把下列内容单独写成创新：

- 给规则加 applicability condition；
- 收集 HLS 专家经验；
- 分阶段 synthesis/bottleneck/optimization；
- 把工具轨迹用于模型适配。

可研究差异：HLSmith 主要面向高性能 C/C++→HLS 翻译/优化；本项目聚焦真实失败下的 owner uncertainty、开放世界诊断、安全修复授权、验证式负记忆和拒答治理，尤其区分 Candidate/Testbench/Toolchain/Configuration 并保护 Hidden/Testbench 权威。

### 11.3 与 ChatHLS 的差异

ChatHLS 已有 specialized agents、adaptive error case expansion、reasoning-to-instruction analysis、debugging 和 directive tuning。仅“自动扩错误案例库”不新。

差异应通过指标证明：

- unknown owner 下的安全 abstention；
- false repair 和错误对象修改率；
- negative transfer；
- evidence completeness 与 authority violation；
- time-ordered memory evaluation；
- deterministic kernel 掌握 success。

### 11.4 与 HLSRewriter/C2HLSC 的差异

两者都使用 compiler/HLS feedback 做迭代改写；HLSRewriter还强调 step-wise reasoning、library、pipeline-aware decomposition。只把真实错误日志反馈给 LLM 不够新。

本项目要回答它们通常不作为主问题处理的内容：反馈的 owner 是否可靠、什么时候不该修源码、经验何时不适用、Testbench 是否被弱化、AI 建议怎样在固定权限内被验证、错误经验如何因负结果而缩窄/废弃。

### 11.5 与 HLSDebugger 的差异

HLSDebugger 聚焦 HLS logic bug identification/type/correction 和大规模标注训练。本项目不把训练专用模型作为当前目标；研究对象是 agent-level evidence/authority/memory governance，并包含 tool/testbench/config/unknown ownership。

### 11.6 候选论文问题

**RQ1**：在真实 Vitis failure 中，typed deterministic baseline + constrained AI advisory 是否能提高 unknown/open-set owner 和 failure-class 诊断，同时保持低错误自动行动率？

**RQ2**：条件化正负 episode 与 Applicability Gate 是否比 no-memory 和 similarity-only memory 减少 false repair/negative transfer，并提高 verified repair success？

**RQ3**：固定 FSM/Policy、candidate-only AI authority、Hidden/Testbench boundaries 是否能在开放世界修复中保持 fail-closed，同时控制成本？

**RQ4**：生命周期和 time-ordered governance 是否能在错误分布增长时保留收益，避免污染和过度泛化？

### 11.7 候选贡献

1. 面向 HLS 工具链的 owner-aware open-world Diagnostic Event/Advisory 协议；
2. 固定 deterministic safety kernel 与 AI advisory/repair authority 分离；
3. 真实重验证驱动的条件化正负 episode + versioned pattern lifecycle；
4. Applicability Gate 的 accept/reject/abstain 与 conflict/negative-transfer 机制；
5. 包含 Candidate/Testbench/Toolchain/Configuration/Unknown 的真实 Vitis 失败语料与安全指标；
6. 独立 evidence auditor 和时间顺序评测协议。

以上是候选贡献，不应在实现和实验完成前写成最终论文 claim。

---

---

## 12. 当前状态快照

| 项目 | 当前值 | 解释 |
|---|---|---|
| repository | UTZZTU/AgRefactorPlusPlus | 当前仓库 |
| branch | research-roadmap-v2.3 | 当前检出分支 |
| repository checkout HEAD | 5f1de2ed48fe176670851b206a7b2c7bc6af6f25 | 服务器当前检出提交；R0 文档同步基线 |
| implementation lineage HEAD | 52d7d0097627ff1f92c3f384170bd1fd4771ada7 | R1 实现谱系提交，不等同于当前检出 HEAD |
| R1 implementation base | 4d4cfdb92c8d181cf607cdf79368a9585ec4ca0e | R1 实现基线 |
| behavior parent | 5ef7fa9a6011534362a2094e159eee75c672619c | R1 行为父基线 |
| research route | V2.3 | 本稿批准固化后生效 |
| primary empirical target | Vitis HLS 2023.2 | 不外推到其他版本 |
| R0 | documentation sync applied; pending independent audit | 仅文档同步 |
| R1-Safety | implementation exists; closure must be retained | 安全实现门 |
| R1-Data | corpus/evidence closure must be evidenced | 数据证据门 |
| R2-R6 | not started | 按新路线逐门推进 |

仓库中的 R1 external acceptance receipt 可以作为声明性证据，但不能代替对归档、manifest、identity、Hidden 边界和真实证据的独立复核。V2.3 的状态必须区分 repository declared state、implementation state、deterministic state、real evidence state 和 independent audit state。

## 13. 研究方向与八项能力

V2.2 的论文定位、相关工作分析和八项能力处置全部保留。当前论文主线保持为：

~~~text
evidence-gated open-world diagnosis
+ verified continual diagnostic memory
+ safe Candidate repair
~~~

更精确的研究问题为：

> 在 Vitis HLS 2023.2 的开放世界失败中，evidence-safe diagnosis 与条件化正负经验治理，能否在不授予 AI 成功权威的前提下，降低 false repair 和 negative transfer，并在证据不足时安全 abstain？

八项能力在 V2.3 中解释如下：

| 能力 | V2.3 处置 | 论文地位 |
|---|---|---|
| TargetProfile | 保留；实际驱动 executable、settings、device、clock、Tcl、parser 和 identity | 实证基础 |
| 双模式版本处理 | 保留为长期目标；当前不宣称 migration runtime 完成 | 未来工作 |
| Model API Registry | 保留 provider-neutral 和 credential-safe 行为 | 工程底座 |
| Layered Prompt | 复用并窄扩展；初始 generation 仍有 Legacy bridge | 工程/复用 |
| Structured feedback + fixed FSM | 强化 owner、route、evidence、abstain 和 auditor | 安全核心 |
| Safe three-level optimizer | safe-v1 保留；dynamic-v1 非当前前置 | 次要能力/baseline |
| Memory Applicability Gate | R3 shadow；R4 才可进入 Candidate 权限链 | 论文核心 |
| BudgetManager | 共享硬预算和 observed-only token/cost | 工程底座 |

禁止把“能力存在”直接写成“能力完成”。每项能力都必须回答 producer、consumer、真实执行、负例、artifact、identity、独立审计和不可外推边界。

## 14. 权威和四轴状态

每个阶段必须有四个独立字段：

| 字段 | 含义 |
|---|---|
| implementation_state | 代码、schema、producer、consumer 和权限链是否存在 |
| deterministic_state | 合同、负例、降级和重放测试是否通过 |
| real_evidence_state | 是否实际运行 provider/Vitis/tool，并保存计数和 artifact |
| independent_audit_state | 是否独立核对 archive、hash、identity、Hidden 和 authority |

状态映射：

~~~text
implemented    = implementation_state
verified       = implementation_state + deterministic_state
real_validated = implementation_state + deterministic_state + real_evidence_state
accepted       = required axes + independent_audit_state
~~~

R0 必须新增机器可读 authority index。每份路线、decision、acceptance、history 和 package 标记 current、historical、superseded 或 evidence_only，并记录 document path、effective_from、superseded_by、repository head、behavior baseline、scope 和 evidence reference。

成功只能来自：

~~~text
real validator
→ typed evidence
→ fixed FSM/router
→ RecoveryPolicy/Ledger
→ full qualification
→ independent auditor
~~~

AI、DiagnosticEvent、Advisory、Memory、Gate 和 package script 均不能写入 success authority。

## R0——权威状态、范围和实验基线对齐

R0 继续是文档和状态同步阶段，不修改产品 Python、不调用 provider/Vitis、不 commit/push。

R0 新增完成门：

- authority index 可解析、可 hash、可独立复核；
- implementation HEAD、behavior parent 和 rollback target 明确；
- V2.2 有效章节继承关系明确；
- superseded 历史文档不会覆盖 current route；
- package self-acceptance=false；
- R0 archive 经独立审计后才可 accepted。

## R1——Safety/Data 双门

### E.1 R1-Safety

R1-Safety 覆盖 V2.2 R1-A 到 R1-D：

- Formal Preflight Testbench-owned route 有明确 executor，或显式 review；
- AUTO、PROVIDED、FILESYSTEM/EXTERNAL、Hidden 授权分离；
- repair 后完整前缀重验证；
- shared RecoveryLedger、BudgetManager、TraceRecorder、ExecutionIdentity；
- TestbenchSemanticManifest/revision 和 non-weakening auditor；
- EffectiveRepairQuotaSummary 只读解释层；
- DiagnosticEvent 只投影 typed、agent-safe、Public evidence。

以下 API 级别必须 fail closed，不能只依赖调用方过滤：

- Hidden semantic manifest/revision；
- Hidden literal/oracle fingerprint；
- Hidden path/digest projection；
- Hidden aggregate 转 owner/failure-class feature；
- Hidden record 写入 memory/corpus content。

### E.2 R1-Data

R1-Data 覆盖真实 failure corpus v1：

~~~text
Candidate compile/link/ABI
Testbench compile/link/stub/protocol
CSYNTH language/legality
Public CSIM functional/timeout
Public COSIM interface/protocol/timeout/unknown
toolchain/configuration/environment
mixed/unknown/owner unresolved
no-repair/abstain/inconclusive/invalid evidence
~~~

每条 record 至少包含 record id、timestamp、run/validation/attempt identity、DiagnosticEvent identity、target/toolchain/parser identity、source/test provenance、candidate/suite hashes、evidence refs、evidence level、outcome、promotion eligibility、invalid reason 和 record hash。

E2 只能表示 deterministic fixture，promotion eligibility 必须为 false。E3 必须满足：

~~~text
physical_tool_launched=true
evidence_complete=true
target_identity_complete=true
toolchain_identity_complete=true
source_and_test_provenance_complete=true
independent_archive_reference=true
outcome not in {inconclusive, invalid_evidence}
~~~

缺少任一项不得用于 pattern promotion。

### E.3 R1 reconciliation gate

只有以下条件全部满足，R1 才能作为 R2 前置事实：

- R1-Safety accepted；
- R1-Data accepted；
- Hidden leakage audit clean；
- R1 provider calls=0；
- real tool calls 被明确计数；
- external archive 独立复核；
- repository state 与 archive identity 一致。

否则状态必须写成：

~~~text
R1-Safety-accepted / R1-Data-pending
~~~

R1 不做 provider-backed advisor、memory retrieval、AI Candidate repair、FSM mutation、version migration 或 dynamic optimizer。

## R2——Provider-backed Shadow Diagnostic Advisor
> R2 设计、shadow diagnostic 实现和真实 provider/Vitis 外部实证均已通过独立外部审计，当前状态为 `accepted_independent_external_review`；`R2_ACCEPTED=true`。R3 设计尚未开始。


### F.1 目标和触发

R2 只对已有 deterministic Unknown/Review 的 Public physical failure 产生 shadow advisory。触发必须同时满足：

- terminal stage 为 Public CSIM、CSYNTH 或 Public COSIM；
- deterministic owner unresolved/unknown 或 review；
- DiagnosticEvent 完整且 agent-safe；
- physical tool launch、run identity 和 R1-Data 证据完整；
- shadow reserve 可用。

Preflight Testbench failure、Hidden terminal、PROVIDED semantic edit、identity incomplete 和 infrastructure-only failure 不得直接触发 provider。

### F.2 Shadow 隔离

shadow 必须区分 main deterministic consumption、shadow provider consumption、shadow token/cost、shadow wall time 和 shadow error/timeout。

开启 shadow 不得改变主路径 route、status、Candidate hash、RecoveryLedger count、repair count 或 best_correct。若物理上共享一个 BudgetManager，必须在调用前设置独立 reserve，并在 artifact 中保存两类消费边界。

provider error、timeout、invalid JSON、evidence ref 越界或 scope 越权统一降级为 shadow failure/abstain，deterministic result unchanged，repair action none。

### F.3 Advisor 合同

允许输出：

~~~text
suspected_owner
suspected_failure_class
evidence_refs
repair_scope
confidence
abstain_reason
bounded_repair_intent (optional, not executed)
~~~

禁止输出或控制 transition、accepted、FSM node、Hidden detail、secret、private reasoning、raw source patch 和 Testbench authorization。accepted 必须恒为 false。

confidence 必须在预先冻结的 calibration split 上评估。模型自报 high 不能直接进入 Gate。

### F.4 R2 评测和完成门

必须报告 owner/failure-class calibration、high-confidence error rate、abstention coverage-risk、evidence citation validity、unsafe scope proposal rate、invalid output/timeout/provider error 降级率和 shadow/deterministic decision equivalence。

R2 只有在 provider artifact、shadow reserve、real Vitis unknown cases、calibration、equivalence 和 independent audit 全部通过后才能 accepted。R2 不做 memory retrieval、pattern promotion、Candidate repair 或 Testbench repair。

## R3——条件化正负记忆与 Applicability Gate（Shadow）

### G.1 Episode 合同

DiagnosticEpisode 表示一次 pattern revision 在一个具体 context 的 application。正负属于 episode，不属于 pattern 永久属性。

Episode 必须保留 event、execution、request、context signature、deterministic diagnosis、advisory、retrieved revisions、Gate decision、repair authorization、before/after hashes、full revalidation、budget delta 和 outcome。

### G.2 Outcome 归因

- verified_positive：合法 Candidate change、完整真实验证链通过、semantic contract 未削弱、identity 完整、独立 auditor clean；
- verified_negative：已获 Gate/Policy 授权，失败可归因于该 revision，且排除 toolchain/environment/identity/infrastructure；
- abstained：Advisor 或 Gate 拒答/不授权，未执行 repair；
- inconclusive：预算、工具、超时、identity 或证据冲突使结果无法归因；
- invalid_evidence：Hidden、secret、hash、provenance、authority 或 artifact contract 违规。

模型声明、单个 return code、compile pass 或 Hidden aggregate 不能单独产生 verified_positive。

### G.3 Aggregate 和 feature firewall

Unknown aggregate 只能表达 terminal unresolved/review。Hidden aggregate 只能表达 operator-only overall correctness。两者都不能成为 owner、failure class、repair scope 或 applicability feature。

允许的 feature 包括 target/toolchain/parser identity、stage、deterministic owner/failure class、agent-safe contract/hash 和历史窗口内 episode statistics。

禁止 Hidden content/oracle/path、future outcome、private reasoning、similarity-only authorization 以及由 aggregate 推断 owner/failure class。

### G.4 Promotion 和 lifecycle

revision 必须记录 supported_when、avoid_when、target/toolchain/interface/test constraints、required evidence predicates、positive/negative refs、parent lineage、lifecycle 和 revision hash。

promotion 前必须冻结：

~~~text
min_positive_episode_count
min_independent_context_count
max_attributable_negative_rate
max_false_repair_rate
max_unsafe_scope_rate
min_evidence_citation_validity
min_calibration_requirement
deprecation_window
~~~

数值只能来自历史窗口或预注册 calibration split，不能查看 future evaluation window。样本不足时只能停留 Quarantined/Provisional。

### G.5 Gate

Gate 只能输出 accept/reject/abstain，顺序固定：

1. Hidden/secret/identity incomplete hard reject；
2. unsupported role/stage/scope reject；
3. exact target/toolchain/parser/interface/test exclusions；
4. evidence completeness；
5. avoid_when；
6. positive/negative support；
7. conflict and sparsity；
8. calibrated risk threshold；
9. decision、reasons、evidence refs。

embedding similarity 不能替代 exact exclusion。同强度正负冲突必须 abstain。

R3 完成门还必须包含 episode immutability、lineage、A-positive/B-negative、action attribution、deprecated/rejected、sample sparsity、time leakage、source-level holdout 和 Legacy cache isolation 测试。Gate 在 R3 不得改变 repair 或 FSM。

## R4——Gate 授权的安全 Candidate Repair 闭环

R4 只开放新增的 LLM-advisory Candidate-only 权限；已有 deterministic Testbench repair 保留。

固定链：

~~~text
typed physical failure
→ complete DiagnosticEvent
→ calibrated advisory or abstain
→ Gate accept Trusted revision or reject/abstain
→ RecoveryPolicy candidate-only
→ isolated budget reserve
→ RecoveryLedger
→ bounded Candidate loop
→ full prefix revalidation
→ independent audit
→ immutable episode outcome
~~~

R4 必须增加预注册 canary set、kill switch、pattern revision quarantine、repair authorization id、before/after Candidate hash 和 full revalidation id。

kill switch 触发后可以继续 shadow，但必须停止所有 LLM Candidate mutation。false repair、semantic weakening、Hidden leak、identity conflict 或 authority violation 自动 quarantine revision。Candidate repair 失败不得覆盖 best_correct。LLM-advisory 不得获得 Testbench 自动修改权。

R4 完成门：

- no-advisory/no-memory baseline 公平对照；
- Candidate-only 权限由 Policy、Ledger 和 budget 同时证明；
- canary、kill switch、quarantine 负例通过；
- verified positive/negative、abstained、inconclusive、invalid evidence 可区分；
- no Hidden repair；
- no Testbench advisory auto-edit；
- no AI success authority；
- best_correct failure protection；
- independent auditor 无 critical finding。

## R5——持续治理、时间顺序和因果可识别消融

### I.1 目标

R5 不是简单增加案例数，而是证明系统能在错误流变化时安全积累、缩窄和废弃经验，并能把 advisor、memory、Gate、Candidate repair 的作用分开识别。

### I.2 Campaign layer

现有 CampaignRunner 的 durable progress、heartbeat、timeout、fail-soft 和 eligibility 只能作为基础。R5 必须增加相邻 research campaign layer 或扩展 manifest，包含：

- arm id；
- memory snapshot id；
- episode/pattern snapshot hash；
- history/future split；
- case/source leakage guard；
- paired budget contract；
- case isolation；
- negative-transfer reducer；
- deterministic stop reason。

保留 shell=false、隔离工作区、heartbeat、timeout 和 fail-soft。

### I.3 最低实验臂

~~~text
A0 deterministic baseline
   no advisor / no memory / no automatic LLM repair

A1 advisor shadow
   advisor on / no memory / no automatic repair

A2 Candidate-only advisor repair
   advisor on / no memory / bounded Candidate repair

A3 similarity-only memory
   advisor on / Legacy-style similarity retrieval

A4 positive-only gated memory
   advisor on / positive support / applicability Gate

A5 positive+negative gated memory
   advisor on / positive and negative support / conflict abstention

A6 full gated lifecycle
   advisor + lifecycle + Candidate-only repair
~~~

A2 是不可删除的关键对照。所有 arm 固定 source/test、TargetProfile、model/provider、prompt、seed、timeout、parallelism、budget 和 repeats，只改变机制开关。

### I.4 时间顺序

~~~text
history T0..Tk
→ freeze snapshot K
→ future evaluation Tk+1..Tn
→ append episodes for next period
~~~

future outcome、Hidden、repair result 和 post-hoc label 不得回写 K。必须同时报告 case-level 和 source-level holdout。

### I.5 指标和统计

必须报告：

- owner/failure-class、unknown detection、citation validity、calibration；
- attempted、verified positive/negative、false repair、negative transfer、time-to-repair、full-chain pass；
- Gate accept/reject/abstain、support、conflict、promotion、deprecation、churn；
- Hidden/secret/private reasoning leak、authority violation、semantic weakening、false success；
- provider/LLM/tool/compile/CSIM/CSYNTH/COSIM/wall time、tokens/cost、budget blocks；
- repeated-run distribution、worst case、infrastructure failure 和 stop reason。

实验前冻结 primary/secondary endpoint、样本排除、invalid evidence、paired comparison、repeats、置信区间和 stop rule。统计显著性不能覆盖 critical safety violation。

### I.6 R5 完成门

- 所有 arm 的 protocol/hash/snapshot 可重建；
- A0-A6 的机制差异可解释；
- time leakage、source leakage 和 cache contamination 为零；
- negative transfer、false repair、abstention 和 invalid evidence 单独报告；
- budget/cost/wall-time 公平且可审计；
- independent auditor clean。

## R6——正式实验、论文与发布冻结

R6 只能在 R1-Data、R2、R3、R4 和 R5 所需门全部通过后开始。

### J.1 冻结内容

- code commit/tag；
- authority index；
- corpus、episode、pattern、memory snapshot；
- source/test/target/toolchain/model/prompt identity；
- arms、budget、seed、timeout、repeats；
- inclusion/exclusion/invalid evidence/stop rules；
- statistical method；
- machine-readable artifact 到 table/plot 的重建脚本。

### J.2 数据分层

- committed deterministic fixtures：合同和反例；
- real Vitis 2023.2 diagnostic corpus；
- time-held-out future cases；
- source-level holdout；
- 可选外部 benchmark，但必须通过 adapter/contract/identity 审核；
- 其他 Vitis 版本只能作为 E5 external validation，不改变主 claim。

### J.3 报告纪律

正式结果必须报告：

- 所有 negative results；
- false repair 和 negative transfer；
- abstention、inconclusive、infrastructure failure；
- invalid/excluded evidence 及理由；
- provider/model/prompt/target/toolchain identity；
- Testbench provenance/revision；
- Hidden boundary checks；
- budget、token、cost、tool and wall-time；
- non-claims 和外推限制。

论文主张限制为 Vitis HLS 2023.2 上的 bounded empirical study，不声称 universal HLS repair、跨版本泛化、稳定 PPA 优势或 model-weight continual learning。

### J.4 R6 完成门

- 论文表格和图可由 machine-readable artifacts 重建；
- release tag 与 authority index 一致；
- auditor/archive 可独立复现；
- 不将新增实验性代码混入冻结实验；
- 论文主张不超过 Vitis 2023.2 bounded evidence。

## 15. Claim→Code→Test→Evidence 追踪矩阵

| 主张 | 代码落点 | 必需测试 | 最终证据 |
|---|---|---|---|
| Target 真驱动物理工具 | config/target.py、flow/tools/vitis_* | target/version/invocation | Tcl、实际版本、tool artifact |
| AI 不改 FSM | validation_state.py、validation_orchestrator.py | decision equivalence、mutation | paired route diff |
| Unknown 不强归 Candidate | feedback_routing.py、R2 reducer | open-set/abstain | shadow artifact |
| AI 不宣布成功 | recovery/advisory.py | accepted=true rejection | accepted=false + typed result |
| Hidden 不反馈 | evidence、trace、source isolation | API-level fail-closed | zero content/path/fingerprint |
| deterministic TB repair 保留 | testing、source_bootstrap.py | AUTO/PROVIDED/Hidden | semantic audit + revalidation |
| Testbench 不被弱化 | testbench_semantics.py、auditor.py | mutation corpus | blocked weakening artifact |
| quota 不创造第二权威 | recovery/quota.py | counter/budget authority | explanation-only summary |
| R1 corpus 可 promotion | evidence/corpus.py | E2/E3/provenance | corpus manifest + reader |
| R2 shadow 不污染主路径 | recovery/runtime adapter | budget/time/equivalence | main/shadow split |
| confidence 可校准 | R2 reducer | frozen calibration | calibration report |
| 同一经验可正可负 | R3 episode/pattern | context A/B | positive/negative episodes |
| negative 可归因 | R3 reducer | attribution/inconclusive | verified_negative refs |
| Gate 可拒绝 | R3 Gate | exact exclusion/conflict | decision + reasons |
| Candidate-only 权限 | R4 integration | canary/kill/policy | authorization + full revalidation |
| best_correct 受保护 | repair/optimizer state | budget/failure | immutable best pointer |
| 时间顺序无泄漏 | R5 campaign layer | snapshot/future | frozen K manifest |
| 消融可识别 | R5 protocol | paired arm tests | arm manifest/reducer |
| 结果可重建 | R6 artifact layer | hash/rebuild | release archive |

## 16. 阶段总验收矩阵

| 阶段 | 实现门 | 确定性门 | 真实证据门 | 独立审计门 | 后续授权 |
|---|---|---|---|---|---|
| R0 | authority index/route | 文档一致性 | provider/Vitis=0 | archive clean | R1-Safety |
| R1-Safety | A-D safety closure | owner/semantic/Hidden/budget negatives | 按合同引用 | safety audit | R1-Data |
| R1-Data | records/reader/verifier | E2/E3 hash tests | real Vitis refs | corpus audit | R2 |
| R2 | shadow adapter/reducer | parser/abstain/equivalence | provider + Vitis unknown | shadow audit | R3 |
| R3 | episode/pattern/Gate | conflict/promotion/leakage | time-ordered shadow | memory audit | R4 |
| R4 | Candidate-only repair | canary/kill/best_correct | bounded real repair | permission audit | R5 |
| R5 | arms/time layer | paired/leakage/statistics | repeated real matrix | campaign audit | R6 |
| R6 | freeze/rebuild/release | rebuild tests | selected evidence | release audit | publication |

## 17. 风险与停止规则

| 风险 | 触发 | 处理 | 是否继续 |
|---|---|---|---|
| R1 corpus 只有 schema | 无可读 E3 records | R1-Data-pending | 不进 R2 |
| Hidden API 可投影指纹 | Hidden fail-closed test 失败 | quarantine/fix | 不进 R2 |
| shadow 污染主预算 | route/status/ledger 改变 | isolate reserve/disable | 只做设计 |
| provider 非法输出 | parser/越权字段 | shadow abstain | 不进 R4 |
| confidence 未校准 | 只有模型自报 high | 重做 calibration | 不进 Gate |
| positive/negative 不可归因 | infrastructure/unknown | inconclusive | 不 promotion |
| negative transfer 上升 | 超过冻结阈值 | deprecate/quarantine | 关闭 revision |
| time leakage | future 进入 snapshot | invalid evidence，整期重跑 | 不做结论 |
| arm 预算不公平 | calls/time 不匹配 | paired correction | 不做因果结论 |
| auditor critical finding | authority/identity/semantic conflict | fail closed，保留归档 | 不 accepted |
| Windows 缺依赖 | import/test 无法复现 | 记录环境限制，服务器验证 | 不伪称全绿 |

任何 stop rule 都必须保留原始日志、artifact、hash 和失败分类，不得通过删除失败 unit 让包变绿。

## 18. 执行包协议

每个服务器执行包必须包含 README、精确 repository HEAD、behavior baseline、route version、authority index、scope、non-goals、preflight、可逆隔离工作区、rollback target、manifest/SHA256、验证脚本、日志和归档说明、PACKAGE_SELF_ACCEPTANCE=false，以及 provider/Vitis/Git action counters 和 stop rules。

执行包不得 commit、push、reset、rebase、clean，不得修改环境/代理/凭据或无关 dirty work，不得读取、上传或持久化 Hidden 正文，不得用脚本 exit code 宣布阶段 accepted，不得覆盖既有运行产物。

服务器返回归档至少包含 raw command output、exit code、run/artifact root、manifest/hash、provider/tool/Vitis counters、identity/budget/trace、auditor report 和 negative/inconclusive/invalid evidence。归档必须由独立审计读取后才能更新仓库状态；手动 commit/push 由用户单独确认。

## 19. V2.3 最终路线判断

V2.3 保留 V2.2 的研究主线、八项能力分类、论文定位、相关工作边界、真实产品拓扑、四条 repair lane、Testbench/Hidden 约束和历史证据。V2.3 只改变以下执行规则：

1. R1 拆为 Safety/Data 两个独立门；
2. R2 必须 shadow-only、budget-isolated、calibrated、fail-safe；
3. R3 必须冻结 outcome attribution、promotion threshold、feature firewall 和 time leakage；
4. R4 只允许 canary-scoped Candidate-only repair；
5. R5 必须通过 A0-A6 配对可识别消融；
6. R6 才能形成正式论文和发布结论；
7. V2.2 固化后标记为 historical/superseded evidence，不与 V2.3 并行作为执行指针。

项目负责人已批准 V2.3 作为当前研究路线。R0 文档同步包只执行权威对账；包自身不验收，独立审计通过后才将 `R0_ACCEPTED=true` 并进入 R1-Safety/R1-Data。

<!-- V2_3_R1_EXTERNAL_ACCEPTANCE:BEGIN -->
R1-Safety/R1-Data external acceptance archive: `agrefactor_v23_r1_safety_data_comprehensive_v1_20260829T044722Z_2189779.tar.gz`
archive_sha256=41337c668f049e533d2a12ad627cbb2fe5bbeeabb12e693992ccf155f3dd7732
R1_ACCEPTED=true
R2_STARTED=true
R2_IMPLEMENTATION_STATUS=accepted_independent_external_review
R2_ACCEPTED=true
NEXT_STEP=V2.3-R3-design-only
<!-- V2_3_R1_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R2_EXTERNAL_ACCEPTANCE:BEGIN -->
R2 external validation archive: `agrefactor_v23_r2_shadow_diagnostic_external_validation_correction_v7_20260830T143607Z_614600.tar.gz`
archive_sha256=d0b1147596bc8e14695608ca74ce4f719e67f0419279e27eb4f745cc1dabea6c
R2_EXTERNAL_VALIDATION=true
R2_ACCEPTED=true
NEXT_STEP=V2.3-R3-design-only
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R2_EXTERNAL_ACCEPTANCE:END -->

> R3 设计冻结：`docs/roadmap/R3_CONDITIONED_MEMORY_GATE_DESIGN.md`；机器契约：`docs/roadmap/V2_3_R3_DESIGN.json`；R3 尚未实现或验收。

<!-- V2_3_R3_EXTERNAL_ACCEPTANCE:BEGIN -->
R3_DESIGN_STATUS=accepted_independent_external_review
R3_ACCEPTED=true
R3_STARTED=false
NEXT_STEP=V2.3-R3-implementation
acceptance_run_id=agrefactor_v23_r3_conditioned_memory_gate_external_acceptance_20260831T103638Z_2061801
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R3_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R3_IMPLEMENTATION:BEGIN -->
R3_IMPLEMENTATION_STATUS=accepted_independent_external_review
R3_ACCEPTED=true
R3_STARTED=true
NEXT_STEP=V2.3-R4-design-only
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R3_IMPLEMENTATION:END -->

<!-- V2_3_R3_IMPLEMENTATION_EXTERNAL_ACCEPTANCE:BEGIN -->
R3_EXTERNAL_VALIDATION=clean
R3_ACCEPTED=true
R3_IMPLEMENTATION_STATUS=accepted_independent_external_review
R3_STARTED=true
NEXT_STEP=V2.3-R4-design-only
validation_archive=agrefactor_v23_r3_conditioned_memory_gate_external_validation_20260831T130943Z_3145805.tar.gz
validation_archive_sha256=401b7369f8f3125018ffee17fbcbb2fad9fe26a034cd1c8aab1829d865e42ca8
acceptance_run_id=agrefactor_v23_r3_conditioned_memory_gate_independent_external_acceptance_20260831T134704Z_3407698
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R3_IMPLEMENTATION_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R4_DESIGN_STATUS:BEGIN -->
V2.3 R4 Gate-authorized Candidate repair design has been applied.
R4_DESIGN_STATUS=accepted_independent_external_review
R4_ACCEPTED=true
R4_STARTED=false
R4_DESIGN_BASE_HEAD=54e422989c6bff962312efc468200c28dd7b4276
R3_PREDECESSOR_EVIDENCE_VERIFIED=true
R3_TEMPORAL_MEMORY_EFFICACY_ESTABLISHED=false
NEXT_STEP=V2.3-R4-implementation
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R4_DESIGN_STATUS:END -->

<!-- V2_3_R4_DESIGN_EXTERNAL_ACCEPTANCE:BEGIN -->
R4_DESIGN_EXTERNAL_ACCEPTANCE=clean
R4_ACCEPTED=true
R4_DESIGN_STATUS=accepted_independent_external_review
R4_STARTED=false
NEXT_STEP=V2.3-R4-implementation
acceptance_run_id=agrefactor_v23_r4_gate_authorized_candidate_repair_design_external_acceptance_v1_20260902T151509Z_3192001
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R4_DESIGN_EXTERNAL_ACCEPTANCE:END -->
