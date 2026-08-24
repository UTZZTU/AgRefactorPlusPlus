# AgRefactor++ Research Roadmap V2.2（全链路代码追踪路线）

> 日期：2026-08-25  
> 状态：**项目负责人已批准；R0 执行归档已通过独立复核，本路线随 R0 文档提交成为后续开发权威**  
> 审计分支：`stage2-general-feedback`  
> 审计提交：`5ef7fa9a6011534362a2094e159eee75c672619c`  
> 服务器确定性回归：`2335/2335` tests passed  
> 当前主要实证环境：Vitis HLS 2023.2  
> 当前项目名：暂用 AgRefactor++；方法和结果稳定后再决定论文名/系统名  
> 本文定位：V2.1 的全面校正版；写入仓库后成为当前研究路线，产品成功权威仍由真实验证与独立证据审计掌握。
>
> R0 独立审计：`agrefactor_r0_document_authority_sync_v1_20260824T172726Z_2546041.tar.gz`，SHA256 `7608be4b21ff2ceade20040caee255024a56666b2711b4a186b4c42360c13674`。

---

## 0. 为什么必须再写 V2.2

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

```text
branch=stage2-general-feedback
head=5ef7fa9a6011534362a2094e159eee75c672619c
head_subject=fix: harden command execution and COSIM v2 normalization
server_full_regression=2335/2335
source_hashes_verified=true
worktree_unchanged_by_tests=true
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

### 2.3 当前文档漂移

`docs/roadmap/PROJECT_STATE.md` 和 `pre_stage4_current_state.json` 仍停留在：

```text
behavior_head=0ca5dd9...
regression=2268/2268
NEXT_STEP=P4-0F-R5-E-R1
```

这与当前 `5ef7fa9 / 2335` 不一致。旧 backlog 又把 `dynamic-v1` 写成 Stage 4 前硬要求，但产品代码中的 `ProductOptimizerRequest`、`OptimizerState`、`SafeOptimizerPolicy` 都只接受 `safe-v1`。

结论：当前首先存在的是**权威状态债务**，不是缺一个新算法。R0 必须把代码、服务器证据、PROJECT_STATE、JSON state、ROADMAP、GOAL_TRACEABILITY 和 backlog 的权威关系重新冻结。

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

1. **Formal Preflight Testbench route 无完整执行器**：FSM/policy 可授权 deterministic TB Preflight repair；AUTO TB 有独立 pre-FSM lane；但 PROVIDED TB 在 formal Preflight 失败时没有对应 runtime executor，最终可能 `repair_not_applicable/review`。
2. **Testbench semantic integrity 不足**：现有 deterministic contract 能保护 main、top call、禁止 stub/wrapper/private helper，但不能证明 inputs、case count、expected values、tolerance、comparison/failure semantics 未变。Prompt 甚至允许在保留“meaningful comparison”时删除/替换 tests。
3. **有效次数难解释**：CLI max、lane max、RecoveryPolicy stage max、run total、validation restart、BudgetManager 共同生效，用户只看一个 `max_repair` 会误解。
4. **Unknown error 没有 provider-backed advisory runtime**：`DiagnosticAdvisory` 只有 schema/protocol，formal request 仍 off。
5. **Original route 没有自动执行器**：router 可产生 `repair_original`，policy 实际拒绝；正确处置应是 task correction/operator review。
6. **Memory 没有进入授权链**：只有 prompt hook，尚无检索、gate、application record 和 verified outcome。
7. **部分 provenance 不统一**：AUTO TB prep 的 DERIVED provenance 较完整；runtime TB recovery 需要同样严格的 revision/semantic manifest/identity。

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

---

## 8. 三类“状态”必须严格分离

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
- `verified_negative`：应用该 pattern 导致失败、退化、错误 owner、语义弱化或 negative transfer；
- `abstained`：Gate/Advisor 明确拒答，没有修复；
- `inconclusive`：预算/基础设施/未知阻塞，不能归因 pattern；
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

## 12. 新路线 R0–R6：每项都绑定真实代码

## R0——权威状态、范围和实验基线对齐

### R0.1 目标

先把“当前是什么”冻结，避免在过期 P4-0F/dynamic-v1/Stage 4 文档上继续开发。

### R0.2 现有依赖

- 当前 head `5ef7fa9`；
- 服务器 `2335/2335`；
- P0 replay archive 和 publication receipt；
- `PROJECT_STATE.md`、`pre_stage4_current_state.json`、`ROADMAP.md`、`GOAL_TRACEABILITY.md`、`PRODUCT_CAPABILITY_BACKLOG.md`；
- 当前 `safe-v1` only 代码合同；
- 当前 product CLI consume-or-reject 行为。

### R0.3 工作内容与代码/文档落点

| 工作 | 落点 | 原因 |
|---|---|---|
| 写新路线与 Decision Record | `docs/roadmap/RESEARCH_ROADMAP_V2_2.md`、`docs/roadmap/R0_V2_2_ROUTE_DECISION.md` | 正式 supersede “八项不可删除”和旧 Stage 4 顺序 |
| 同步当前 state | `PROJECT_STATE.md`、JSON state | 5ef/2335/P0 事实一致 |
| 生成 capability truth table | GOAL_TRACEABILITY | 区分产品/局部/hook/Legacy/未实现 |
| 冻结 `safe-v1` 处置 | roadmap/backlog | 删除 dynamic-v1 当前硬要求，不删除历史记录 |
| 冻结论文主线和非目标 | Decision Record | 版本迁移/auto routing/PPA expansion future work |
| 冻结 artifact schema strategy | 新 contract | 新类型必须相邻扩展，不能双 success authority |

### R0.4 测试和证据

- 文档中的 branch/head/test count 与 `git rev-parse`、server evidence 对齐；
- old authoritative pointers 全部标 superseded 或历史；
- CLI help/consume-reject 与文档一致；
- 不调用 provider，不跑真实 Vitis，不改产品行为；
- repo clean，artifact manifest 和 source hash 完整。

### R0.5 完成门

只有当后续开发者打开唯一入口能得到相同当前状态、相同 R0–R6 路线和相同非目标时，R0 才完成。

---

## R1——补齐当前 repair/evidence 缺口并建立真实失败语料

### R1.1 目标

在 AI 获得任何控制权前，先让当前确定性系统的 repair 权限、Testbench 语义、Preflight executor、有效配额和 episode 输入证据完整。

### R1.2 现有依赖

- 四条 repair lane；
- `RecoveryPolicy`/`RecoveryLedger`；
- `FeedbackReport`/Router/FSM；
- stage typed artifacts；
- suite provenance；
- Execution Identity/Trace/auditor；
- campaign runner/eligibility；
- LinkedList、Aho-Corasick、Strassen 及历史五案例证据。

### R1.3 实现项

#### R1-A Formal Preflight Testbench recovery closure

- 为 formal Preflight deterministic Testbench-owner route 提供明确 executor，或明确取消该 route 并 fail to review；不能保持“policy 允许但 executor 不存在”；
- PROVIDED vs AUTO 使用不同授权；
- 完整前缀重验证；
- 共享 RecoveryLedger/BudgetManager/Trace。

#### R1-B Testbench semantic manifest/revision

- 引入 proposed `TestbenchSemanticManifest`；
- AUTO/PROVIDED/Hidden 三策略；
- repair 前后 deterministic diff；
- revision provenance、hash、identity；
- auditor 阻断 weak-oracle false success。

#### R1-C Effective repair quota artifact

建议 `effective_repair_quota.json` 记录：

```text
CLI requested max
lane local max
RecoveryPolicy stage/role max
run total recovery max
validation restart max
shared hard budget remaining
actual attempts/denials/reasons
```

它只解释和审计，不再创造一个独立预算权威。

#### R1-D DiagnosticEvent projector

- 从现有 typed evidence/feedback/identity 投影；
- 不解析 Hidden 正文；
- 不改变 Router/FSM；
- 给每个真实失败稳定 context signature。

#### R1-E 真实失败 corpus v1

至少覆盖：

- Candidate compile/link/ABI；
- Testbench compile/link/stub/protocol；
- CSYNTH HLS language/legality；
- Public CSIM functional/timeout；
- Public COSIM interface/protocol/timeout/unknown；
- Toolchain/config/environment；
- mixed/unknown；
- no-repair/abstain 正确案例。

数据必须 time-stamped、identity-bound、source/test provenance 完整；invalid evidence 不进入后续 promotion。

### R1.4 代表性测试落点

- `tests/test_testbench_repair.py`
- `tests/test_p0_public_testbench_repair_routing.py`
- `tests/test_candidate_repair_loop.py`
- `tests/test_unified_candidate_repair_phase.py`
- `tests/test_validation_state.py`
- `tests/test_validation_orchestrator.py`
- `tests/test_p4_0b_typed_preflight.py`
- `tests/test_p4_0d_public_rtl_cosim.py`
- `tests/test_p4_0f_r5_d_validation_recovery.py`
- 新 semantic/episode/projector/auditor tests。

### R1.5 真实门

- deterministic full regression；
- focused owner/repair/semantic tests；
- Vitis 2023.2 真实正/负/unknown cases；
- no provider calls；
- no Hidden read；
- no false acceptance；
- independent auditor clean；
- corpus manifest/hash/identity 完整。

### R1.6 不做

- 不启用 AI advisory；
- 不启用 memory retrieval；
- 不改 FSM；
- 不做版本迁移/dynamic optimizer。

---

## R2——Provider-backed Shadow Diagnostic Advisor

### R2.1 目标

让 AI 在 Unknown/open-set failure 上给出结构化、有证据引用、可 abstain 的诊断，但不改变任何实际 route 或 repair。

### R2.2 现有依赖

- `DiagnosticAdvisoryRequest`/`DiagnosticAdvisory`/validator；
- EffectiveModelConfig/provider/usage/cost；
- DiagnosticEvent projector；
- Execution Identity/Hidden isolation；
- RecoveryPolicy 中 advisory stage/physical/evidence gates；
- prompt sanitization 和 model artifact writer 模式。

### R2.3 实现落点

| Proposed 组件 | 建议位置 | 复用/约束 |
|---|---|---|
| Advisory prompt builder | `agrefactor/prompts/` 相邻模块 | 复用 safe layers；不强迫 owner match |
| Provider advisor adapter | `agrefactor/recovery/` 或 runtime adapter | strict JSON；一次 bounded call；无自动 retry 泛化 |
| Shadow producer | formal validation terminal unknown path | 不替换 deterministic decision |
| Advisory artifact | run artifacts/recovery subtree | request/response digest、usage、model/prompt identity、accepted=false |
| Evaluation reducer | 新 evaluation module | owner/failure class/abstain/calibration；不重用 advisor 自己打分 |

### R2.4 输出约束

AI 只能输出：

```text
suspected_owner
suspected_failure_class
evidence_refs
repair_scope
confidence
abstain_reason
bounded repair intent（可选，不执行）
```

AI 不可输出/控制：transition、accepted、hidden detail、raw secret、unbounded source edit、testbench auto authorization。

### R2.5 评测

- known-set：与 deterministic owner/failure class 对比；
- open-set：未知错误识别/abstain；
- owner accuracy/F1；
- high-confidence error rate；
- unsafe scope proposal rate；
- evidence citation validity；
- abstention coverage-risk curve；
- cost/latency；
- shadow disagreement analysis。

### R2.6 完成门

- 普通产品 route 与 R1 baseline bit-for-bit/semantic-equivalent；
- provider physical call、usage、identity 完整；
- Hidden/secret/private reasoning zero；
- advisory accepted=false；
- advisor error/invalid output 安全降级，不摧毁 deterministic result；
- 真实 Vitis unknown cases shadow 运行完成。

---

## R3——条件化正负记忆与 Applicability Gate（Shadow）

### R3.1 目标

建立 episode、pattern revision、gate 和 lifecycle，但 Gate 暂不改变 repair。

### R3.2 现有依赖

- DiagnosticEvent/Advisory；
- full validation outcome；
- repair artifacts/lineage；
- Execution Identity/Target/tool fingerprint/test manifest；
- `approved_memory_snippets` hook；
- Legacy RAG baseline；
- campaign runner。

### R3.3 实现项

- immutable append-only episode store；
- strict schema/version/hash；
- pattern extractor 只产生 Quarantined revision；
- deterministic promotion evaluator；
- applicability feature projector；
- accept/reject/abstain gate；
- conflict resolver；
- negative evidence override/narrowing；
- retrieval manifest；
- no-memory / similarity-only / gated shadow arms；
- time-based train-history/test-future split。

### R3.4 Gate 不能做的事

- 不能把 `Trusted` 当 success；
- 不能读取当前测试未来产生的 episode；
- 不能把 Hidden content 作为 feature；
- 不能通过修改阈值看测试集；
- 不能自动 promotion 自己生成的 pattern；
- 不能用 embedding 相似度替代 exact exclusions。

### R3.5 完成门

- episode round-trip/immutability/lineage；
- 同一 pattern 在 A positive、B negative 可正确表达；
- exact negative/context exclusion 能 reject；
- equal conflict 能 abstain；
- deprecated/rejected revision 不被自动授权；
- time leakage 检查；
- shadow decisions 对真实 outcomes 可审计；
- Legacy RAG 作为独立 baseline，不共用结果缓存污染。

---

## R4——Gate 授权的安全 Candidate Repair 闭环

### R4.1 目标

只为原本 deterministic Unknown/Review 的少数 eligible case 开启 AI advisory + Gate 的 candidate-only exploratory repair。

### R4.2 权限链

```text
typed physical failure
→ DiagnosticEvent complete
→ Advisor high-confidence candidate-only（或 abstain）
→ Gate accept Trusted/eligible pattern（或 reject/abstain）
→ RecoveryPolicy candidate-only/stage/physical/evidence gate
→ RecoveryLedger + Effective repair quota + BudgetManager reserve
→ existing Candidate repair loop / strict complete-source contract
→ full validation prefix and terminal chain
→ independent evidence audit
→ episode outcome
```

链中任何一项失败都不得自动修。

### R4.3 复用现有代码

- 不另造 candidate loop；复用 `BoundedCandidateRepairLoop` 和 adapter；
- memory 通过 Gate-approved manifest/snippets 进入现有 repair prompt hook；
- owner authority 明确为 `llm_advisory`，RecoveryPolicy 已限制 candidate-only；
- 继续使用 full prefix restart；
- 继续使用 source hash/lineage/final candidate safety；
- Testbench advisory 自动修复仍禁止。

### R4.4 False repair 定义

至少包括：

- 修错对象；
- proposal 无改变/非法/越权；
- earlier stage 通过但完整链失败；
- 测试语义被弱化；
- Hidden 泄漏；
- 接受了 evidence conflict；
- 成本耗尽却覆盖 best valid candidate；
- pattern 在不适用 context 引起 negative transfer。

### R4.5 完成门

- 与 no-advisory baseline 公平预算对照；
- verified repair success 提升或在失败时展示安全 abstention；
- false repair/unsafe action 不超过 R1 冻结阈值；
- negative transfer 显式报告；
- no Hidden repair；
- no Testbench advisory auto-edit；
- no AI success authority；
- auditor 无 critical finding。

---

## R5——持续治理、时间序列和消融

### R5.1 目标

证明系统不是在固定错误表上调 prompt，而能在错误流增长时安全积累、缩窄和废弃经验。

### R5.2 扩展 campaign runner，而不是假定它已支持

当前 CampaignRunner 有 durable progress、timeout、heartbeat、fail-soft 和 eligibility，但没有：

- experimental arm；
- memory snapshot id；
- time split；
- leakage guard；
- episode/pattern snapshot；
- negative-transfer reducer；
- paired budget comparison。

R5 要扩展 manifest/schema 或增加相邻 research campaign layer，保留 shell=false、case isolation 和 failure continuation。

### R5.3 冻结 arms

建议至少：

```text
A0 deterministic current baseline, no advisor, no memory
A1 advisor shadow/no memory
A2 advisor + similarity-only Legacy-style memory
A3 advisor + gated positive-only memory
A4 advisor + gated positive+negative memory
A5 full gated lifecycle + candidate repair
```

可根据成本合并，但论文必须能隔离：AI 诊断、negative memory、Gate、lifecycle 的作用。

### R5.4 时间顺序

```text
history window T0..Tk → build memory snapshot K
future window Tk+1..Tn → frozen evaluation
```

未来 case 的 outcome、Hidden、repair result 不得回写到当前评测 snapshot；评测后才可成为下个 period 的 Quarantined episode。

### R5.5 指标

诊断：owner/failure-class、unknown detection、coverage-risk、evidence citation。  
修复：attempted、verified positive、verified negative、false repair、time-to-repair、full-chain pass。  
记忆：retrieval accept/reject/abstain、positive/negative support、conflict、negative transfer、pattern churn/lifecycle。  
安全：Hidden leak、secret leak、authority violation、semantic weakening、false success。  
成本：LLM/tool calls、tokens/cost estimate、wall time、Vitis phases、budget blocks。  

### R5.6 持续学习术语

当前应写成 **inference-time continual diagnostic memory / experience governance**，不是 model-weight continual learning。若未来做 fine-tuning，应作为独立未来工作和新实验，不与当前 memory store 混称。

---

## R6——正式实验、论文与发布冻结

### R6.1 目标

在机制、阈值、数据 split 和负结果都冻结后，执行正式 E4 实验并写论文。

### R6.2 数据集分层

- committed deterministic fixtures：回归和反例；
- real Vitis diagnostic corpus：Candidate/Testbench/Toolchain/Config/Unknown；
- time-held-out cases：持续记忆；
- source-level holdout：避免同源相似泄漏；
- 可选外部 HLS benchmark：只在 adapter/contract 合格时加入；
- Vitis 2023.2 为主；其他版本仅在后期作为 E5 external validation，不改变主 claim。

### R6.3 报告纪律

必须报告：

- 所有 negative results；
- false repair；
- negative transfer；
- abstention；
- inconclusive/infrastructure；
- excluded/invalid evidence；
- model/tool/cost budgets；
- prompt/model/target/toolchain identity；
- Testbench source/provenance/revision；
- non-claims。

### R6.4 论文主张边界

若只有 2023.2，写“在 Vitis HLS 2023.2 环境评估”，不写 cross-version generalization。  
若样本量不足，写 bounded empirical study，不写 universal HLS repair。  
若优化器没有稳定收益，不把 PPA 作为主贡献。  
若 Advisor 诊断提升但 repair 无提升，诚实报告并把安全 abstention 作为结果。  

### R6.5 完成门

- protocol/hash/snapshot frozen；
- all arms 完整或按预定义 stop rule 终止；
- independent auditor；
- artifact archive 可复现；
- statistical method 和 sample exclusions 预先定义；
- 论文表格可从 machine-readable artifacts 重建；
- repo/document state 与 release tag 一致。

---

## 13. Claim→Code→Test→Evidence 追踪矩阵

| 路线主张 | 当前代码依据 | 需要修改/新增 | 代表性测试 | 最终证据 |
|---|---|---|---|---|
| Target 真实驱动 Vitis | `config/target.py`、`flow/tools/vitis_*`、`csynth.py` | episode 绑定 fingerprint；文档纠正 platform | target/csynth/vitis csim/cosim tests | effective target + Tcl + version + invocation |
| AI 不改 FSM | `validation_state.py`、`validation_orchestrator.py` | 只新增 projector/advisor shadow | validation state/orchestrator/advisory tests | route baseline unchanged |
| Unknown 不强归 Candidate | `feedback_routing.py` | advisory output仍可 abstain | feedback routing/open-set tests | unknown/review + shadow artifact |
| AI 不宣布成功 | `DiagnosticAdvisory.accepted=false` | R2/R4 end-to-end enforcement | advisory/recovery/auditor tests | success 只来自 typed validation |
| Hidden 不反馈 | trace/evidence/source isolation/FSM | episode/gate exclusion tests | hidden isolation/cosim/identity tests | hidden_input_count=0, no content/path/digest |
| Candidate-only advisory auto-repair | RecoveryPolicy 已有 gate | R4 wiring | recovery policy/validation recovery | policy + ledger + full revalidation |
| deterministic TB repair 保留 | testing + source bootstrap + runtime recovery | semantic manifest/revision、Preflight closure | TB repair/routing/preflight | semantic invariants + qualification |
| 同一经验可正可负 | 当前未实现 | Episode + PatternRevision | conflict/context tests | positive A/negative B/time-held-out |
| Gate 可拒绝 | 当前只有 prompt hook | ApplicabilityGate | reject/abstain/leakage tests | retrieval manifest + outcomes |
| 生命周期治理 | 未实现 | store/promotion/deprecation | immutable/promotion tests | revision lineage/audit |
| repair 次数可解释 | repair budgets + policy + manager 分散 | EffectiveRepairQuotaSummary | budget/repair/restart tests | requested/effective/actual artifact |
| Testbench 不被弱化 | 现有结构 contract 不足 | SemanticManifest/auditor | mutation/negative tests | accepted run has strong manifest |
| optimizer 保留 best | optimization state/checkpoint/PPA/recovery | 仅兼容 memory consumer（可选） | optimizer state/recovery/budget tests | best_correct immutable under failure |
| time-order evaluation | CampaignRunner 无此语义 | research campaign snapshot layer | manifest/leakage/resume tests | frozen K snapshot + future outcomes |
| false success 可独立发现 | `evidence/auditor.py` | episode/semantic/memory findings | auditor tests | independent report clean/contradiction |

---

## 14. 旧路线如何迁移

### 14.1 保留

- TargetProfile；
- fixed user model/provider-neutral registry；
- Public/Hidden boundary；
- typed evidence/feedback/FSM；
- existing deterministic Candidate/Testbench repair；
- conservative RecoveryPolicy/Ledger；
- Budget/Trace/Identity/auditor；
- safe-v1 optimizer、checkpoint、best_correct；
- real Vitis 2023.2 validation。

### 14.2 降级为工程支撑

- Model Registry 扩展；
- general prompt abstraction；
- BudgetManager 新功能；
- concise output；
- broader CLI surface。

### 14.3 暂停/未来工作

- Vitis source→target version migration；
- repository-level migration；
- platform/runtime migration；
- authorized auto model routing；
- dynamic-v1 optimizer；
- 模型权重持续学习；
- 多工具链/多版本泛化；
- arbitrary formal equivalence。

### 14.4 替换

| 旧概念 | 新概念 |
|---|---|
| “8 项不可删除” | 论文核心 / 实证基础 / 工程支撑 / 次要能力 / future work 分层 |
| “新错误就新增状态” | 固定 FSM + 开放诊断类别 + 知识 lifecycle |
| “正/负 memory” | application episode outcome + condition-bound pattern revision |
| “MemoryMode off/gated/always” | 研究 arms 可保留；产品自动权限以 Gate/Policy 为准，`always` 不能绕过安全门 |
| “统一 Tool Event” | 在现有 typed evidence 上投影 DiagnosticEvent |
| “v1 只修 Candidate” | 新 AI advisory 自动权限 candidate-only；deterministic TB repair 保留 |
| “dynamic-v1 必做” | 当前取消；safe-v1 冻结为次要能力 |

---

## 15. 执行包策略

用户不喜欢把一件事拆成过多包，因此路线只定义逻辑门，不强制一门一包。

建议：

| 包 | 可合并内容 | 不能合并的条件 |
|---|---|---|
| Package A | R0 文档同步（本次单独执行） | 只允许文档和状态文件变化，必须独立复核后提交 |
| Package B | R2 shadow advisor + R3 shadow memory/gate | 若 provider 证据或数据 schema 未稳定，先停在 R2 |
| Package C | R4 candidate-only闭环 + R5 campaign extensions | 自动控制权限未通过 shadow gate 时不能合并启用 |
| Package D | R6 formal experiment/release | 必须使用冻结代码/数据/prompt/model/target，不夹带实现修改 |

也允许每阶段一个包。拆包触发条件：

- 前一步真实 Vitis/provider 失败；
- 需改变 authority/schema；
- rollback 边界不同；
- evidence 太大或运行时间太长；
- 自动 repair 权限首次开启。

每包继续遵守：checksum、manifest、read-first、preflight、isolated clone、no hidden read、no self-acceptance、result archive、sidecar、rollback、source publication receipt。

---

## 16. 风险与停止规则

| 风险 | 监测 | 停止规则 |
|---|---|---|
| Advisor 把 Unknown 错判 Candidate | owner confusion/high-confidence error | R2 只 shadow；超过冻结风险阈值不得进 R4 |
| Testbench 被弱化 | semantic manifest diff/auditor | 任何 oracle/case/failure weakening 阻断 acceptance |
| Memory negative transfer | condition buckets/verified negative | pattern deprecated/narrow child；Gate reject/abstain |
| 数据泄漏 | time/source split、snapshot hash、Hidden isolation | 任一泄漏使实验 invalid_evidence，重建 split |
| 双 success authority | auditor/architecture review | AI/memory 字段出现 accepted 立即阻断 |
| 文档再次漂移 | state validation script | head/test/next-step 不一致不开始下阶段 |
| 预算绕过 | BudgetManager + ledger + effective quota | prospective reserve 失败不启动修复 |
| 基础设施误归因 | typed owner/physical launch/evidence complete | inconclusive，不写 positive/negative |
| 过拟合单一错误字符串 | open-set/time-held-out | 不允许以新增 regex 数量作为主结果 |
| 与 HLSmith/ChatHLS 创新重合 | related-work/ablation review | 贡献必须落在 authority/negative memory/abstention/safety evaluation |

---

## 17. 已冻结决定

### 17.1 已冻结（来自前几轮确认）

- 暂用 AgRefactor++ 名称，成果稳定后可改名；
- 当前论文主线：证据门控开放世界诊断 + 验证式持续记忆 + 安全修复；
- AI 不修改 FSM/层级，不宣布成功；
- Vitis 2023.2 是当前主要实证环境；
- 版本迁移放未来工作；
- Model Registry、BudgetManager 是工程支撑；
- safe optimizer 是已有次要能力，不继续抢占主线；
- R0–R6 是逻辑路线，包数量灵活；
- 报告负结果、false repair、negative transfer、abstention；
- 新 AI 自动修复 v1 限 Candidate；现有 deterministic Testbench repair 保留；
- Hidden 永不修复/反馈。

### 17.2 V2.2 新增且已确认

1. 以 `5ef7fa9 / 2335` 取代旧 `0ca5dd9 / 2268` 作为 R0 同步基线；
2. 正式取消 `dynamic-v1` 作为 Stage 4/当前论文前置要求；
3. 承认初始 generation 仍是 Legacy generation-only bridge，不声称全 prompt 现代化；
4. TargetProfile 当前没有 typed platform，不再作 platform 已支持主张；
5. R1 先关闭 formal Preflight TB executor 与 Testbench semantic integrity；
6. 新 DiagnosticEvent 只做现有 evidence 的 projection，不成为第二权威；
7. 复用现有 DiagnosticAdvisory schema，不重造；
8. episode outcome 与 pattern lifecycle 分离；
9. token/cost 继续 observed-only，不虚构硬 reservation；
10. campaign runner 只是 R5 基础，time split/arms/leakage guard 仍需实现；
11. 论文主差异对准 owner-aware authority、negative memory、abstention、Testbench/Hidden safety 和 time-order evaluation。

---

## 18. 批准后的第一步

V2.2 已由项目负责人确认。第一执行包只完成 **R0 权威文档同步**，不夹带 R1 源码实现：

```text
authority/document sync
+ capability truth table
+ remove dynamic-v1 current mandate
+ independent audit
```

R0 已通过独立复核；本次文档收口提交完成后，才设计和执行 R1。R1 将处理 formal Preflight TB route/executor、Testbench semantic manifest/revision、effective repair quota、DiagnosticEvent projector、真实失败语料与 deterministic/real-Vitis replay；这些内容不属于 R0。

---

## 19. 相关工作（当前检索到 2026-08-25）

- HLSmith: <https://arxiv.org/abs/2608.06791>
- ChatHLS (ACL 2026): <https://aclanthology.org/2026.acl-long.962/>
- AgRefactor: <https://arxiv.org/abs/2606.30949>
- HLSDebugger: <https://arxiv.org/abs/2507.21485>
- HLSRewriter: <https://arxiv.org/html/2504.14641v2>
- C2HLSC: <https://dl.acm.org/doi/10.1145/3734524>
- HLS-Eval: <https://arxiv.org/abs/2504.12268>

这些工作用于定位，不替代后续正式 systematic related-work review。论文写作前仍需按最终方法、数据和发表状态重新核对。

---

## 20. 审阅结论

经过全链路追踪后，项目的真实状况可以更准确地概括为：

> AgRefactor++ 已经不是一个只有旧 AgRefactor prompt/RAG 的原型。它具备真实 Vitis 2023.2 验证、Public/Hidden 边界、owner-aware typed feedback、固定 FSM、有界 Candidate/Testbench repair、RecoveryPolicy、预算、身份、独立证据审计和 safe optimizer 等强安全底座；但初始生成仍通过 Legacy bridge，Memory Gate 尚未实现，AI advisory 只有 schema，Testbench 语义保护和部分 repair executor 仍有缺口，权威文档也落后于当前 head。

所以正确的下一路线既不是继续完成旧八项清单，也不是推倒重写，而是：

1. 先对齐权威状态并关闭 deterministic repair/evidence 缺口；
2. 在现有 typed evidence 上做 shadow open-world diagnosis；
3. 用真实重验证结果建立条件化正负 episode 和可治理 pattern；
4. 让 Gate/Policy 只授权 Candidate 的有界探索修复；
5. 用 false repair、negative transfer、abstention、time-order 和独立审计证明安全性与研究价值。

这条路线与当前真实代码兼容，也比“再加一种错误 regex”“再做一个 RAG”“再扩一个优化器 profile”更有论文辨识度。
