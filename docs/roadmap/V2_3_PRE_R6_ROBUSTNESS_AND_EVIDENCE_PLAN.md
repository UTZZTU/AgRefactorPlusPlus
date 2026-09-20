# V2.3 R6 前系统加固、数据扩展与记忆有效性路线

日期：2026-09-19  
状态：待项目所有者确认后执行  
适用分支：`research-roadmap-v2.3`  
当前基线：`a4a3899528608c70ee1eb2c042e24204f7b3f995`  
当前状态：`R4_ACCEPTED=true`、`R5_READY_FOR_USER_ACCEPTANCE=true`、
`R5_ACCEPTED=false`、`R6_STARTED=false`

## 1. 文档地位与决策

本文是
`docs/roadmap/V2_3_COMPLETION_EXECUTION_PLAN.md` 的增量前置路线，不替换、
不删除原 V2.3 路线，也不重写既有 R4/R5 证据。原计划仍定义 V2.3 的核心研究
问题、三个正式入口、安全边界和最终 R6 方向；本文只回答一个新问题：在正式接受
R5、进入 R6 的最终实验与论文阶段以前，怎样把系统做得更牢固、更完整，并让持续
记忆真正获得足够且公平的考察机会。

当前不进入 R6。原因不是 R5 的 ledger、Gate、R4、snapshot、payload 或 auditor
没有接通，而是现有正式证据的案例覆盖不足，且尚未建立持续记忆对未来修复率的
增益。现阶段应新增一个 **R5.1：Pre-R6 Robustness and Evidence Completion** 阶段。

本文不会自动改变任何接受状态。只有项目所有者在本文全部硬门槛完成后明确接受，
才能设置 `R5_ACCEPTED=true`；此前 `R6_STARTED` 必须保持 `false`。

## 2. 已确认的事实与不足

### 2.1 已经成立的部分

- R2 结构化诊断、R3/R5 持久 ledger 和 lifecycle、R4 单次受控修复、Trusted
  snapshot、Candidate-only payload、预算、身份、provenance 和独立审计已经接通。
- R5 功能默认关闭，没有新增正式 CLI，也没有建立第二套 Vitis 流程。
- 正式入口仍只有 `refactor`、`optimize`、`full`；本阶段继续以 `refactor` 为主，
  `optimize` 保留 `safe-v1`，`full` 只验证两者衔接。
- 全仓 2628 项确定性测试和 R5 专项测试已经通过；已有真实 Vitis、Provider 和
  独立审计证据，未发现 false repair 或 negative transfer。

### 2.2 不能被夸大的部分

- “6 个真实 benchmark”实际是 2 个案例各重复 3 次，不是 6 个独立程序。
- 这 6 次是普通 `refactor` 生成的首个 Candidate 直接通过完整验证；没有合法失败
  进入 R2/R4 和记忆修复臂，因此不能作为记忆有效性证据。
- 当前没有对每个被评分案例执行统一、冻结的 raw source baseline，因而不能总是
  区分“原始源码本来就能通过”与“经过 refactor 才通过”。
- 现有正式 campaign 不足以说明对常见 HLS 不兼容问题的覆盖度，也没有证明持续
  记忆提高 future repair rate。

### 2.3 仓库现有数据资产

`src/info.json` 当前登记 57 个案例：49 个 `useful`、8 个 `not_useful`，来源包括
HeteroRefactor、C2HLSC、HLSRewriter、LeetCode 和真实应用内核。仓库中有 35 个
`kernel.cpp` 和 116 个 C/C++ 源文件。初步扫描还发现至少一组空白归一化后相同的
内核，说明正式实验前必须做代码级与语义级去重。

因此，下一阶段首先不是盲目下载更多数据，而是把现有 57 个案例变成一个有来源、
许可、oracle、top、目标、重复关系和资格状态的可审计 registry；然后再引入外部
数据补足真正缺失的错误族与任务类型。

## 3. 本阶段目标与非目标

### 3.1 目标

1. 继承并复核 AgRefactor 已有能力：所有过去有明确成功声明且具备可执行 oracle
   的案例必须重放；其余案例必须完成登记、资格筛选或给出可审计排除原因。
2. 建立覆盖内部与外部来源的去重数据集，覆盖常见而非按日志编号定义的 HLS
   不兼容家族。
3. 为所有进入能力统计的案例补齐 raw source baseline，测量真正的 refactor lift。
4. 先通过更多真实案例寻找合法、有区分度的 R2-R5 机会；只有聚合证据显示机制过窄
   时，才修改通用证据合同、校准范围或 Gate 规则。
5. 让普通 `refactor` 成功轨迹以安全、非晋级的形式进入长期观察，并设计基于消融
   或跨案例重放的因果晋级机制。
6. 在保持 fail-closed、安全拒答和证据边界的同时，提高常见问题上的实际可用性。
7. 形成足以决定“接受 R5”或“继续加固/收窄论文主张”的独立审计证据。

### 3.2 非目标

- 不开始 R6 的最终论文实验、统计定稿或论文写作。
- 不声称处理所有真实世界 HLS 错误。
- 不为单个 benchmark、文件名或某个 `HLS xxx-yyy` 编号写修复特判。
- 不通过降低 confidence 阈值、绕过 provenance 或相信模型自报置信度来制造成功。
- 不把 raw-source 直接通过、普通 refactor 直接通过或事后人工修正伪装成 R4/R5
  `verified_positive`。
- 不新增第四个正式入口，不改变 `refactor/optimize/full` 的产品边界。

## 4. 2024-2026 文献与外部数据侦察

以下工作构成第一轮经过元数据和开源仓库核对的候选池。论文提供方法参考，仓库是否
能进入正式数据集还要通过许可、commit 固定、oracle 可执行性和去重门槛。

| 工作 | 年份 | 可借鉴内容 | 数据用途与限制 |
|---|---:|---|---|
| AgRefactor, DOI `10.1145/3748173.3779566` / arXiv `2606.30949` | 2026 | 自演化 agentic HLS refactor，是本项目继承能力的直接基线 | 优先复核当前仓库已有案例与原论文声称的成功边界 |
| HLSPilot, DOI `10.1145/3676536.3676781` / arXiv `2408.06810` | 2024 | profiling、kernel identification、task pipeline、strategy retrieval、DSE | 官方仓库含约 20 类应用目录，但面向 Vitis 2019.1/U280；需把目标迁移混杂因素单列，GitHub 元数据未识别许可，暂不 vendor |
| HLS-Eval, DOI `10.1109/ICLAD65226.2025.00021` | 2025 | HLS code generation/editing benchmark，含描述、testbench 和 reference HLS | 适合 repair/editing 与外部泛化；先核实各子数据许可，不能默认全部属于 ordinary-C refactor |
| C2HLSC, DOI `10.1145/3734524` / arXiv `2412.00214` | 2024/2025 | 工具反馈驱动的 C 到 synthesizable C 迭代重构 | 官方仓库 GPL-3.0；本仓库已含一部分案例，必须按 upstream commit/path 和代码指纹去重后只选新增部分 |
| HLSFactory, DOI `10.1109/MLCAD62225.2024.10740213` | 2024 | 统一数据构建与工具流；内置 PolyBench、MachSuite、CHStone、PP4FPGA、Vitis Examples 等 | 适合大规模资格筛选、HLS-ready 控制组和后续 optimize；项目 AGPL-3.0，默认外部 checkout 使用，不直接拷入主仓库 |
| Automated C/C++ Program Repair for HLS via LLMs, arXiv `2407.03889` | 2024 | 面向综合错误的工具反馈修复 | 用于比较错误族、停止规则和修复循环，不照搬按错误文本写规则的方法 |
| Exploring Code Language Models for Automated HLS-based Hardware Generation, arXiv `2502.13921` | 2025 | benchmark、基础设施和模型能力分析 | 用于补充任务定义与模型评估维度；数据可得性需继续核实 |
| TimelyHLS, DOI `10.1109/COINS65080.2025.11125726` / arXiv `2507.17962` | 2025 | timing-aware、architecture-specific、RAG 和迭代优化 | 主要作为目标条件化和检索设计参考；未找到可确认的官方数据仓库前不纳入正式数据 |
| Agent Workflow Memory, arXiv `2409.07429` | 2024 | 从成功轨迹归纳可复用 workflow memory | 只借鉴“轨迹抽象而非原始对话复制”；本项目新增 Vitis 证据、因果晋级、负例和 fail-closed Gate，不能直接把其 workflow 当可信修复 |

文献检索使用 OpenAlex/CrossRef 可核验元数据，并按 DOI 优先、标题+首作者回退进行去重。
GitHub 仓库只作为 artifact 候选；论文同名 fork、无许可镜像和非作者仓库不得成为正式
数据源。

首轮可追溯入口：

- HLSPilot：`https://doi.org/10.1145/3676536.3676781`，
  `https://github.com/xcw-1010/HLSPilot`
- HLS-Eval：`https://doi.org/10.1109/ICLAD65226.2025.00021`，
  `https://github.com/sharc-lab/hls-eval`
- C2HLSC：`https://doi.org/10.1145/3734524`，
  `https://github.com/Lucaz97/c2hlsc`
- HLSFactory：`https://doi.org/10.1109/MLCAD62225.2024.10740213`，
  `https://github.com/sharc-lab/HLSFactory`
- TimelyHLS：`https://doi.org/10.1109/COINS65080.2025.11125726`
- Automated C/C++ Program Repair for HLS via LLMs：
  `https://doi.org/10.48550/arXiv.2407.03889`
- Exploring Code Language Models for Automated HLS-based Hardware Generation：
  `https://doi.org/10.48550/arXiv.2502.13921`
- Agent Workflow Memory：`https://doi.org/10.48550/arXiv.2409.07429`

## 5. 不可破坏的工程与研究约束

1. **入口约束**：正式产品入口保持 `refactor`、`optimize`、`full`。
2. **执行约束**：所有新实验复用现有产品 orchestrator 和 Vitis runner；adapter
   只能对接接口与 oracle，不得成为第二套重构或验证流程。
3. **默认关闭**：R2-R5 和记忆扩展继续默认关闭，通过内部实验配置开启。
4. **证据约束**：Hidden source、future outcome、raw provider response、private
   reasoning 和 secret 不得进入 prompt memory 或持久经验。
5. **身份约束**：source、Candidate、tests、TargetProfile、model response identity、
   prompt、toolchain、budget 和 validation 必须内容寻址并可重建。
6. **因果约束**：普通成功不能直接升级为 repair success；`verified_positive` 的现有
   定义保持不变。
7. **通用性约束**：允许解析稳定的结构化工具字段或日志代码作为证据，但 repair
   policy 必须绑定语义 failure family、owner、stage、scope、required evidence 和
   exclusions，不能绑定单个日志编号或案例名。
8. **变化约束**：改变 R2 schema、calibration、Gate、memory lifecycle 或实验 arm
   时必须新建版本、设计说明和回归证据；旧 campaign 不回写、不重新贴标签。
9. **预算约束**：旧 R5 campaign 的累计 500 Provider / 500 Vitis 上限是其不可变
   历史身份；项目所有者于 2026-09-20 为 R5.1 前瞻性授权累计 650/650。新调用计入
   同一累计 ledger，但不得回写旧 manifest 或 reconciliation。任何计划都必须先做
   worst-case reserve 再运行。

## 6. 数据治理与去重协议

### 6.1 统一 Case Registry

每个原始案例建立不可变记录，至少包含：

```text
case_id / upstream_repo / upstream_commit / upstream_path / license
source_sha256 / normalized_token_sha256 / ast_fingerprint
algorithm_family / source_family / task_type / language_subset
top / interface / target / public_oracle / hidden_oracle
raw_source_baseline_status / eligibility / exclusion_reason
history_future_partition / nearest_duplicate_refs
```

`case_id` 不使用论文给出的名字作为唯一身份；名字只作 alias，内容和来源共同决定
身份。

### 6.2 四级去重

1. **D0 exact**：字节 SHA-256 相同，保留一个 canonical case。
2. **D1 normalized**：去注释、统一空白和稳定预处理后的 token hash 相同，视为同一
   实现的格式变体。
3. **D2 structural**：AST、调用图、top 签名和常量结构高度相似，标记为 clone
   group；自动工具只负责召回，最终排除需记录人工裁决。
4. **D3 semantic family**：例如多个 AES、FFT、排序或 linked-list 实现即使代码
   不同，也必须放在同一 semantic group。train/history 与 future/holdout 不能跨组
   泄漏；同组可作为鲁棒性重复，但不能冒充独立来源。

论文、仓库和当前 `src/` 之间先去重，再进行随机或时间划分。不能先看到运行结果再
决定某个重复项属于 history 还是 future。

### 6.3 数据来源分层

- **T0 继承集**：AgRefactor 过去明确使用或声称成功的案例。最高优先级。
- **T1 内部扩展集**：当前 `src/info.json` 其余可执行且非重复案例。
- **T2 外部 refactor/repair 集**：HLS-Eval、C2HLSC 新案例、HLSPilot 中可适配的
  ordinary-C 或 failure-bearing 案例。
- **T3 HLS-ready 控制集**：HLSFactory 的 MachSuite、CHStone、PolyBench、PP4FPGA、
  Vitis Examples 等。它们主要评估 unnecessary rewrite、negative transfer、
  target compatibility 和后续 optimize，不可夸大为 refactor lift。

任何无明确许可、无稳定来源 commit、无可执行 oracle 或需要泄露 Hidden 才能运行的
案例只能进入 quarantine registry，不能进入能力统计。

## 7. Source-baseline 资格实验

每个被纳入能力统计的案例必须先执行冻结的 source baseline。该阶段不调用 Provider，
不生成 Candidate，也不修改源代码。

```text
S0  host/reference compile + Public oracle sanity
S1  raw source Public CSIM（若接口允许）
S2  raw source CSYNTH
S3  raw source Public COSIM（仅在 S2 成功且合同要求时）
S4  保存 stage、typed failure family、tool identity 和完整日志哈希
```

只允许最小、内容寻址的 ABI wrapper；wrapper 不得改写算法、替换不支持结构或加入
优化 pragma。raw source 与 refactored Candidate 使用同一 TargetProfile、同一功能
oracle 和等价接口合同。

结果分类：

- `raw_pass_all`：原始代码已经通过，是 refactor-unnecessary 控制；不计入修复成功。
- `raw_fail_actionable`：原始代码在 Candidate 可归因阶段失败，是有效 refactor 机会。
- `raw_fail_ambiguous`：失败真实但 owner/scope 不清，进入证据改进审计。
- `oracle_or_adapter_invalid`：先修数据基础设施，不允许归因给模型或 Candidate。
- `infrastructure_failure`：工具链、许可、环境或预算问题，与算法能力分开报告。

主要能力指标必须报告：

```text
refactor_lift = raw_fail_actionable -> final formally accepted
unnecessary_rewrite_rate = raw_pass_all 且系统仍修改代码
regression_rate = raw_pass_all -> refactor final failure
stage_reach = compile / CSIM / CSYNTH / COSIM / Hidden
```

## 8. 两层真实执行策略

### 8.1 广覆盖继承与发现层

目标是覆盖尽量多的非重复案例，而不是对两个案例重复很多次。

1. 对 T0 全量登记并复核过去明确成功声明。
2. 对 T0/T1 中所有具备 oracle 的非重复案例执行 source baseline。
3. 从每个 semantic failure family 选择代表案例，各执行一次普通 `refactor`；R2-R5
   保持关闭，只测产品基本能力。
4. 对外部 T2/T3 先做小批量 adapter smoke，再按来源和错误族分层扩展。
5. 单次发现结果不做显著性结论；它只决定哪些案例进入下一层。

这层回答：旧能力有没有丢、普通 refactor 能覆盖哪些常见问题、原始输入是否真的需要
重构、失败主要集中在哪些阶段。

### 8.2 合法机会与记忆效果层

只有满足以下条件的案例进入昂贵的 paired campaign：

- source baseline 失败或普通 refactor Candidate 失败；
- oracle、adapter、toolchain 和身份完整；
- 失败不是预算、网络或基础设施造成；
- 存在可被 R2 观察的 agent-safe 公共证据；
- case、source 和 semantic group 未跨 history/future 泄漏。

继续复用现有冻结 A0-A6 因果边界；如加入下文的 observation memory，必须发布新的
`protocol_version`，不能修改旧 A0-A6 结果。广覆盖层不需要对每个案例跑全部 arm；
只有预登记的 opportunity cohort 运行配对、counterbalance 和重复实验。

## 9. R2 拒答审计与机制改进顺序

“证据不足时安全拒答”继续成立，但每一次拒答都必须归入以下互斥类别：

1. `true_ambiguity_or_ood`：证据确实不能确定 owner/scope，保持拒答。
2. `unsafe_or_forbidden`：涉及 Hidden、越权范围、身份缺失或不可逆修改，保持拒答。
3. `parser_or_projector_gap`：日志已有通用可用证据，但解析/投影丢失；修数据链。
4. `evidence_contract_gap`：执行器没有产生本应存在的结构化证据；修通用合同。
5. `calibration_coverage_gap`：语义错误族稳定、跨来源复现，但证书未覆盖；扩展校准。
6. `granularity_or_cardinality_gap`：多个事件无法唯一归因；改进事件聚合、因果切片或
   owner 分离，不靠选择性忽略错误。
7. `model_contract_failure`：输入充分但输出不合法；改进 prompt/schema/parser，仍由
   实际证据校准，不接受模型自报 `high`。

机制修改必须满足至少一个条件：

- 同一通用缺陷在两个独立 source 或 context 上复现；或
- 违反已写明的 schema、身份、预算、provenance 或执行不变量；或
- 是会导致错误接受/安全问题的关键缺陷，即使只观察到一次也必须修复。

普通的单案例失败不足以新增 repair rule。每次修正都必须写：问题类别、通用不变量、
最小反例、相邻链审计、负例、全量回归和旧证据不回写声明。

## 10. 普通成功轨迹的双轨记忆设计

### 10.1 为什么不能直接写入现有 repair ledger

普通 `refactor` 首个 Candidate 成功，只说明“整条生成轨迹与最终验证共同相关”，并
不能证明某项变换修复了某个失败。直接把它记成 `verified_positive` 会污染
failure-conditioned memory，并可能在不适用案例上造成负迁移。

### 10.2 新增 Transformation Observation Ledger

在不改变现有 Repair Episode Ledger 的前提下，新增第二条 append-only 轨道：

```text
observation_id / source_identity / candidate_identity
typed_ast_diff / transformation_families / affected_symbols
preconditions / exclusions / target_identity
source_baseline_outcome / candidate_validation_outcome
public_evidence_refs / cost / observed_at
causal_level / lifecycle / auditor_status
```

禁止保存 raw provider response、private reasoning、Hidden 内容或模型自述理由。变换
从 source/Candidate 的可重建 AST 差异、编译与 Vitis 证据中提取，而不是要求模型
解释“为什么成功”。

### 10.3 因果晋级阶梯

- **O0 Captured**：普通成功轨迹完整、身份正确；仅供统计，不进入 prompt。
- **O1 Observed**：AST diff 可归类，语义与 formal validation 通过；可作为检索候选，
  但只显示为低权重 observation，不授权自动修改。
- **O2 Ablation-supported**：在同一案例中撤销/隔离目标变换导致相关失败恢复，且其他
  变量保持不变；或在预登记的独立匹配案例上重放该变换后由失败转为通过。
- **O3 Replicated**：至少两个独立 source、context 和完整审计支持相同的语义变换，
  且无 false repair、unsafe scope 或冲突负例，才可生成 Provisional pattern。
- **Trusted**：继续使用现有 lifecycle 的跨来源、校准、负例率和 citation validity
  门槛；不能因为来自 ordinary success 而降低标准。

这形成本文的新增研究点：**双轨、证据分级、因果晋级的 HLS 经验记忆**。Agent
Workflow Memory 的成功轨迹抽象只提供启发；本项目的创新是用 raw source baseline、
AST 变换、消融/重放、真实 Vitis 和负例 Gate 决定经验能否晋级。

### 10.4 使用边界

- O0 不可被检索。
- O1 只能作为 advisory context，不得单独通过 R4 authorization。
- O2 可进入受控实验臂，但必须同时满足 R2、Gate、budget 和 provenance。
- O3/Trusted 才能成为正式 memory payload 的候选。
- observation memory 与 repair memory 的效果必须分开报告；不能合并计算成功率。

## 11. 评价矩阵与指标

每个正式评分案例至少产生以下对照：

| 轨道 | 作用 |
|---|---|
| `S` raw source baseline | 判断输入本来能否通过，建立 refactor lift 分母 |
| `B` ordinary refactor, R2-R5 off | 继承旧能力和普通产品基线 |
| `D` R2-R4 on, memory none | 测诊断与单次受控修复本身 |
| `M-R` Trusted repair memory | 测既有 repair episode 的增益和负迁移 |
| `M-O` qualified observation memory | 仅在 O2/O3 形成后测试新增双轨记忆 |

主要指标：

- raw source formal pass rate；
- ordinary refactor lift、最终 formal acceptance、各 stage reach；
- R2 actionable diagnostic precision/coverage 与分类拒答率；
- R4 authorization、mutation、verified-positive/negative/abstained/inconclusive；
- memory-assisted future repair rate 相对 no-memory 的 paired difference；
- false repair、negative transfer、unsafe scope 和 unnecessary rewrite；
- Provider/Vitis/时间成本及每个 verified repair 的边际成本；
- 按 failure family、dataset source、代码规模和 target 条件分层的结果。

必须同时报告分母。仅报告“成功数”或从成功案例中挑选样例不具有效力。

## 12. 分阶段执行路线

### P0：状态冻结与协议建档

动作：冻结当前 HEAD、R5 证据归档、旧 500/500 ledger、R5.1 的 650/650 前瞻性
预算授权、模型/工具身份和本文版本；确认 R5/R6 状态不变。

退出条件：零调用独立审计通过，旧证据 hash 未改变。

### P1：文献、仓库和许可清单

动作：完成上述论文全文/附录/仓库核对；记录官方 URL、commit、license、benchmark
来源、工具版本、top、tests 和可复现实验。无许可项目只做外部引用，不复制代码。

退出条件：每个候选源均有 `admit/external-only/quarantine/reject` 决定及理由。

### P2：内部 57 案例 registry 与全局去重

动作：建立统一 manifest；完成 D0-D3 去重；把过去声称成功的 T0 案例映射到当前入口；
识别缺失 testbench、错误 top、重复代码和不可重建 provenance。

退出条件：100% 内部案例有身份和状态；不存在未解释重复；history/future 以 family
为单位冻结。

### P3：外部数据适配与小规模 smoke

动作：优先接入 HLS-Eval 和 C2HLSC 的非重复可许可案例；以 HLSFactory/HLSPilot
补充任务与控制组。adapter 只放在实验配置层，不修改上游 benchmark。

退出条件：每个来源至少有一个零 Provider oracle smoke；失败能区分 dataset、adapter、
toolchain 与 Candidate 原因。

### P4：全量 source-baseline 资格筛选

动作：对 T0/T1 所有合格非重复案例和已批准外部样本执行 S0-S4；产生 stage/family
分布和 raw-pass 控制集。

退出条件：所有后续评分案例均有冻结 source baseline；无 source-baseline 的案例不得
进入 refactor lift 或 memory efficacy 统计。

### P5：广覆盖 ordinary-refactor 继承 campaign

动作：对分层代表案例单次运行普通 `refactor`；优先覆盖每个常见语义错误族和过去
成功案例，而不是先重复少数样例。失败保持原样，禁止事后手工改变输入。

退出条件：形成 legacy capability matrix；每个失败有 typed stage 和可审计证据；
raw-pass、lift、regression 和 infrastructure 分开统计。

### P6：合法机会发现与拒答根因审计

动作：从 P5 失败中按预登记规则选 opportunity cohort，开启 R2-R4、memory none；
按照第 9 节分类所有拒答。先扩数据，只有聚合证据支持时才修改机制。

退出条件：每个拒答均有互斥根因；真实歧义与系统缺陷不混在一起；没有通过降低安全
阈值制造可行动事件。

### P7：通用合同、校准与 Gate 加固循环

动作：按 `parser -> evidence contract -> event ownership -> calibration -> Gate` 顺序修复
经证实的通用缺陷；每轮只改变一个协议版本，运行相邻链和全仓回归。

退出条件：常见错误族能够在证据充分时进入受控修复；证据不足、OOD、越权和身份
缺失仍然稳定拒答；不存在 benchmark 名称或单个日志编号修复分支。

### P8：Transformation Observation Ledger 与因果晋级

动作：实现 O0-O3 schema、append-only writer、AST diff miner、ablation/replay runner、
snapshot 和 auditor；先跑零调用 fixture，再从 P5 普通成功中导入非晋级 observation。

退出条件：普通成功能被保存但不能伪装成 repair；只有消融/跨案例重放后才能晋级；
future/Hidden 泄漏和 retroactive promotion 为零。

### P9：扩展 paired memory campaign

动作：冻结新的 history/future split、protocol、模型、prompt、seed、repeats、预算和停止
规则；分别比较 B、D、M-R、M-O。旧 A0-A6 结果只作前序证据，不重新解释。

退出条件：获得真实 memory-assisted future opportunity；所有 arm 共享公平 source/
Candidate 基线；独立 auditor 能重建计数、身份和最终判断。

### P10：Pre-R6 reconciliation 与所有者验收

动作：生成数据清单、去重报告、能力矩阵、拒答矩阵、记忆因果证据、失败清单、预算
ledger、独立审计和可重建归档。

退出条件见第 14 节。未满足时继续 R5.1，不进入 R6。

## 13. 预算计划

截至 R5 结束时的不可变累计消耗为 Provider `248`、Vitis `111`。项目所有者于
2026-09-20 将 R5.1 及后续 V2.3 加固工作的累计硬上限前瞻性提高为 Provider
`650`、Vitis `650`，因此当前可用余额为 Provider `402`、Vitis `539`。旧 R5 文件中
的 `500/500` 继续作为历史事实保留，只有新 R5.1 协议读取 `650/650` 授权。本文第一轮
上限如下，实际执行前必须由 machine-readable preflight 重新计算：

| 工作 | Provider 上限 | Vitis 上限 |
|---|---:|---:|
| P0-P3 inventory、去重、许可、静态/host smoke | 0 | 10 |
| P4 source-baseline 资格筛选 | 0 | 170 |
| P5 广覆盖 ordinary refactor | 180 | 110 |
| P6-P8 机会诊断、通用修正和 observation 因果验证 | 130 | 130 |
| P9/P10 独立复现与审计 | 50 | 60 |
| **第一轮新增上限** | **360** | **480** |

该分配保留 Provider 42 次、Vitis 59 次未分配恢复缓冲，并且不超过累计 650/650。
真实调用以 ledger 为准，未用额度不能自动跨阶段挪用。如果合格案例数量超过剩余
预算，先完成零 Provider 的资格与去重，提交覆盖/功效分析和新增预算申请；不得静默
超限，也不得为了省预算挑选已知容易成功的案例。

## 14. R5 接受与 R6 准入硬门槛

以下条件全部成立后，才向项目所有者请求正式接受 R5：

1. **继承完整**：所有过去明确成功且可重建的 AgRefactor 案例已在当前
   `refactor` 入口重放；未运行项均有不可执行或许可原因，不能笼统声称继承。
2. **数据完整**：内部 57 案例完成 registry；外部数据完成许可和 D0-D3 去重；所有
   正式评分案例有 source baseline、oracle 和 family-level split。
3. **覆盖充分**：广覆盖 campaign 不再只依赖两个案例，覆盖至少三个独立数据来源、
   四个语义 failure family，并明确报告每个 family 的样本数和 stage reach。
4. **机会真实**：至少 8 个独立、非重复、证据完整的 future failure opportunities
   实际进入 R2；若客观数据仍无法提供这一数量，则不能声称广泛 memory efficacy，
   必须扩大数据或收窄论文主张。
5. **记忆可用**：至少两个独立 future 案例形成 memory-assisted
   `verified_positive`，且至少一个在公平 no-memory 配对臂中失败或成本更高；否则
   只能接受“机制存在”，不能接受“持续记忆有效”。
6. **普通经验安全**：O0-O3 双轨机制通过因果晋级、泄漏、冲突、负例、回滚和独立
   审计测试；普通成功不能直接晋级 Trusted。
7. **安全不退化**：false repair、unsafe scope、Hidden/future leakage、身份错配和
   git history mutation 均为 0；negative transfer 单独报告并在阈值内。
8. **通用实现**：不存在按 benchmark 名称、路径或单个 HLS 日志编号触发的修复；
   所有新增规则绑定语义 family 和可验证证据。
9. **产品不退化**：`refactor` 主流程、`optimize safe-v1` 和 `full` 衔接回归通过，
   默认关闭路径与现有行为兼容。
10. **可重建**：数据 commit、manifest、split、source baseline、prompt、model、Vitis、
    budget、seed、结果和 auditor 全部冻结并归档。

若第 5 条无法在真实数据上满足，有两条诚实路径：继续 R5.1 改进通用机制；或者由项目
所有者明确把 V2.3 贡献收窄为“可审计持续记忆机制与安全负结果”，再进入 R6。不得
用合成缺陷、事后挑样例或旧 episode 冒充 future efficacy。

## 15. 第一执行步

项目所有者确认本文后，第一步只执行 P0-P2，不运行 Provider 或 Vitis：

1. 将本文纳入权威路线索引，但保持 `R5_ACCEPTED=false`、`R6_STARTED=false`；
2. 建立 `configs/r5_1/dataset_registry.schema.json` 和初始 registry；
3. 完成内部 57 案例来源、许可、top、oracle、D0-D3 和 legacy-claim 映射；
4. 建立外部候选源 manifest，固定官方 URL/commit/license，不立即批量下载到主仓库；
5. 由独立零调用 auditor 审核数据划分和预算后，再允许 P3/P4。

这一步的输出应先让我们知道“到底有多少真正独立、可运行、需要重构的案例”，再决定
Provider 和 Vitis 如何花；避免再次用少量方便样例代替项目能力评估。

## 16. 预期产物

```text
docs/roadmap/V2_3_PRE_R6_ROBUSTNESS_AND_EVIDENCE_PLAN.md
configs/r5_1/dataset_registry.schema.json
configs/r5_1/dataset_registry.json
configs/r5_1/source_baseline_protocol.json
configs/r5_1/opportunity_selection_protocol.json
configs/r5_1/transformation_observation_protocol.json
docs/roadmap/R5_1_DATASET_AND_DEDUP_AUDIT.json
docs/roadmap/R5_1_SOURCE_BASELINE_RECONCILIATION.json
docs/roadmap/R5_1_LEGACY_CAPABILITY_MATRIX.json
docs/roadmap/R5_1_R2_ABSTENTION_ROOT_CAUSE_MATRIX.json
docs/roadmap/R5_1_MEMORY_EFFECT_RECONCILIATION.json
```

所有运行产物进入新的内容寻址 evidence root；代码仓库只保存协议、manifest、摘要和
hash，不提交 Vitis 临时工程、模型原始输出、密钥或无许可第三方数据。

## 17. 结论

R5 当前已经具备机制骨架，但还没有达到本项目希望的“在足够广泛真实案例上，常见问题
可用、证据不足安全拒答、经验能够受控积累并在未来案例中产生可验证收益”的成熟度。

本路线不以放宽 Gate 或增加错误编号为捷径。它先扩大并治理数据，用 source baseline
确认问题真实存在，再用广覆盖实验发现系统性缺口，最后通过双轨、因果晋级的记忆机制
把 ordinary success 与 verified repair 都转化为不会污染证据的长期能力。完成这些
以后，R6 的正式实验和论文才有稳固、可辩护的基础。
