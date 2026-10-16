# P4 逐文件实施清单与冻结验收命令

- 版本：逐文件评审稿 v1.0
- 日期：2026-10-16
- 状态：P4 实施计划 v1.1 与本清单已获用户认可；P4 已实施并完成冻结验收，结果见第 10 节。
- 服务器：`agrefactor-server`，仓库 `/data/AgRefactor`
- 分支：`backup/server-current-20261013`
- 冻结代码基线：`e740f83cd7f39cbec952ef88bdb0726962b29397`
- 上位计划：[P4 实施计划 v1.1](P4_EXPERIENCE_RETRIEVAL_AND_AI_DIAGNOSTIC_ASSISTANT_PLAN.md)
- 上位大纲：`docs/roadmap/后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md`

## 1. 本清单要交付什么

把已确认的 P4 设计落实为文件、接线位置和可重复执行的验收命令。完成后的行为是：开启 `--diagnostic-assist` 时，每个合法语义修复请求按最新证据检索经验，在完整消息预算内注入适用的摘要与真实改动；本地常规路径仍无法安全决定下一步时，AI 可提供带引用的诊断建议。两个开关关闭时保持原流程。

本清单遵守已确认的规则：当前证据优先；经验允许为空；正负经验分区；最多 3 条正向和 2 条负向；经验预算上限为上下文的 15%；未知 owner 的历史经验只能用于诊断；失败尝试只提示避免原样重复，不禁止后续补全同一方向；同一故障链保持一个 Case。P5 的专项采集器、P6 的统一组件扣额和 P7 的真实 HLS 效果实验仍按原阶段推进。

本轮已实际核对服务器 HEAD、现有函数和 40 个既有测试模块。未修改 Python 业务代码、测试断言、环境配置或经验库，未安装依赖，未调用付费模型，也未执行真实 Vitis 案例。

## 2. 当前基线和实施前约束

### 2.1 已核实的复用点

| 文件与当前入口 | 已有能力 | 本次接线用途 |
|---|---|---|
| `agrefactor/evidence/case_record.py:414`，`ExperienceRecord` | closed/resolved 来源约束、正负极性、验证引用和稳定身份 | 向后兼容读取，增加检索所需的可验证字段。 |
| `agrefactor/evidence/case_store.py:208`，`ExperienceStore` | 追加式写入 `records/`、`changes/`，实际 patch hash 校验 | 增加有范围限制的读取与稳定快照。 |
| `agrefactor/evidence/experience_derivation.py:36`，`derive_experiences()` | 结案后整理与去重 | 收紧负向准入，从动作前证据取得适用条件。 |
| `agrefactor/evidence/case_capture.py`，`CaseRecorder`、`capture_candidate_recovery()` | 事件、授权快照、组件差异及结案关联 | 保存检索特征和验证版本绑定，继续由记录开关控制。 |
| `agrefactor/repair/candidate_loop.py:623`，`BoundedCandidateRepairLoop.run()` | 每轮维护最新 feedback、candidate、prior summaries | 每轮 provider 调用前重新查询，不冻结首次结果。 |
| `agrefactor/prompts/candidate_repair.py`、`agrefactor/prompts/layered.py` | `approved_memory_snippets` 和分层提示词区块 | 复用展示位置；新经验经过本地准入和预算后进入。 |
| `agrefactor/testing/model_testbench_repairer.py:601`，`repair()` | 完整 `LayeredPrompt` 和有效模型调用参数 | 正式 Public Testbench 使用同一注入器。 |
| `flow/tools/tb_optimizer.py:2048`，`run_trajectory()` | 生成型 Testbench/Stub 的真实请求、验证和 trajectory 状态 | 在语义 repair 请求前接入检索，保持初次生成与格式重试的区分。 |
| `agrefactor/models/candidate_adapter.py:604`，`generate()` | 最终 `ModelRequest`、按调用角色参数化的模型配置 | 核验最终发送消息和本次有效输出预留。 |
| `agrefactor/runtime/prompt_evidence.py` | prompt 身份 hash 和无正文调用审计 | 关联检索/注入 ID、计数与实际 provider 调用。 |
| `agrefactor/cpp_interface.py:431/1209` | `extract_top_interface()`、`extract_top_type_contract()` | 从已定位的当前编译单元获取有限符号/类型特征。 |

行号均指冻结基线，仅帮助评审定位，不作为后续补丁偏移量。

服务器已安装 `tokenizers 0.22.2`、`transformers 4.57.6`、`ag2 0.11.5`、`openai 1.109.1`。优先复用现有 `tokenizers`，不安装新解析框架或向量检索服务。服务器没有 `rg`，只读定位可用 `git ls-files`、`find`、`grep`。

### 2.2 已发现的基线测试问题

R1 的 35 个既有模块实际运行 591 项：590 项通过、1 项失败。R2 的 5 个保护模块 42 项全部通过；两组不重叠，共运行 633 项，632 项通过、1 项失败。

唯一失败是 `tests.test_r2_shadow_advisor.R2ShadowAdvisorTests.test_orchestrator_shadow_mode_preserves_main_result`。单项复跑仍失败：测试期望 `static.calls == 1`，实际为 0。捕获到的旧 shadow 审计为 `input_status=rejected:physical_tool_not_launched`，主流程结果仍一致。该测试复用了 `feedback_item(owner=UNKNOWN)`，而此 helper 只为 Candidate/Testbench 写入工具运行与证据完整标记；unknown 场景没有合格的工具执行证据，因此不能直接假定 advisor 应调用。

P4 接线本就需要旧 shadow 兼容测试；本清单将这一个测试文件的合格夹具校正纳入范围：使用本地构造且身份完整的合格事件测试一次 advisor 调用，同时保留工具未运行/证据不完整时零调用的拒绝断言。不能放宽生产 `build_shadow_request()` 门槛来消除失败；不能仅把断言从 1 改成 0 后丢失合格输入覆盖。完整夹具还须满足其余既有引用、身份和可见性要求。

COSIM 保护使用已确认存在的 `tests.test_p4_0d_public_rtl_cosim` 与 `tests.test_p4_0d_r1_cosim_contract`。

基线日志：

- `/tmp/p4_frozen_existing_baseline_corrected_20261016.log`：R1，591 项、1 项失败。
- `/tmp/p4_frozen_guard_baseline_20261016.log`：R2，42 项通过。

这些是定向回归数，不是 HLS 案例数，也不替代 P2 的 71 项或 P3 的 230 项历史验证记录。

## 3. 逐文件代码修改清单

以下清单保留实施前冻结的职责与边界；实际变更文件及验收结果见第 10 节。实现复用模块内少量数据类和函数，没有为单次调用建立插件框架。

### A. 开关与入口贯通

| 文件 | 拟修改的入口与具体内容 | 对应验收 |
|---|---|---|
| `agrefactor/cli.py` | 在现有记录开关相邻处为相关 refactor/source 子命令添加 `--diagnostic-assist`，默认 false；传入实际 settings/request，不只增加 parser。 | 四种开关组合、默认值和实际传递。 |
| `agrefactor/compat/legacy_refactor.py` | `LegacyRefactorSettings` 及传往 flow 的参数增加独立布尔字段；不复用旧 R5/enable_rag 字段。 | adapter 收到 true/false，旧调用保持兼容。 |
| `agrefactor/product/source_bootstrap.py` | `SourceBootstrapRequest`、manifest、请求构造、`_prepare_public_testbench()`、formal phase factory 传递辅助配置和共享经验根目录；覆盖正式验证前的 Public Testbench 准备修复。 | source CLI 到 Public preparation、生成与正式修复入口均可追踪；记录关闭时仍可读库但不写新案例。 |
| `agrefactor/runtime/repair_phase.py` | `CandidateRepairPhaseConfig`、`build_candidate_repair_phase()` 和 phase 创建 orchestrator 时传递辅助服务；经验根目录复用现有 `RUN_DIR/diagnostic_experiences` 解析。 | 双开关独立；不开启辅助时不创建检索/AI 实例或外网依赖。 |
| `flow/new.py` | `refactor_code()`、独立 CLI 及 `gen_tb_with_coverage()`/`make_golden_hidden_tb()` 调用传递辅助配置；按可见性创建每条 trajectory 的临时上下文。 | Public/Hidden 生成配置不串用；关闭时保留原调用行为。 |

不新增第三个用户开关。记录与结案整理继续由 `--record-diagnostic-cases` 一起控制；AI 诊断和逐次检索由 `--diagnostic-assist` 一起控制。

### B. 经验读取、来源合同和负向准入

| 文件 | 拟修改的内容 | 对应验收 |
|---|---|---|
| `agrefactor/evidence/case_record.py` | 为新经验定义 schema v2：动作前阶段/owner、实际组件、可见性投影、规范化诊断特征、工具/合同适用信息、来源与验证版本、真实改动引用。v1 仍能读取作历史记录；缺少安全字段时默认不可检索。 | v1 兼容、v2 roundtrip、未知字段/版本拒绝、字段不足不猜补。 |
| `agrefactor/evidence/case_store.py` | 增加 `ExperienceStore` 枚举、按 ID 读取、读取改动及 revision 的接口。仅访问自身 `records/` 和 `changes/`；校验 ID、路径归属、符号链接越界、schema、hash 及记录/改动对应。坏条目按原因排除，单条损坏不拖垮全部查询。 | 篡改、路径越界、半写入、丢失 change、稳定顺序和空库。 |
| `agrefactor/evidence/case_capture.py` | 在当前受控事件和内存中的 before/after 已可用时记录检索特征、验证所针对的版本与结果种类。按原 Case/Attempt 身份写入，不添加另一套案例。 | 保存的适用条件来自动作前问题；post-pass owner unknown 不覆盖动作责任；关闭记录时无新增案例写入。 |
| `agrefactor/evidence/experience_derivation.py` | 正向经验要求实际差异与成功验证版本可关联；负向经验要求 closed/resolved Case 中某个具体动作被本地验证明确否定。`accepted=False` 本身不够。工具错误、timeout、预算不足、拒绝、未验证及不完整的累计修改都只留 Attempt。 | 连续三次补全后成功不能产生“修 Candidate 无效”；明确违背已冻结接口合同的特定修改可形成窄负例。 |

新的 v2 来源描述必须自包含足够的安全筛选事实和引用 hash；检索不为追溯来源而扫描其他运行目录。自动整理仍只陈述修改与验证事实，不推断根因，不新增生命周期或目录晋升。

**修改片段的完整性：** 复用 `_patch()` 与 `StoredChange` 的大小/hash 限制。单步成功使用该步真实 diff；依赖多个累积步骤时，关联完整 Attempt 链及已验证终态，优先从当前仍在内存中的初始/最终组件内容生成累计 diff，同时保留逐次引用。正式 Testbench 的 before/after、Public generation 各 trajectory 的版本均按组件独立关联，不能把不同 trajectory 的 patch 串成一条源码链。缺少连续来源或累计 diff 超出限制时不伪造、不截断；该条不作为完整成功写法注入，原始 Attempt 仍保留。已有 v1 记录不批量回填、不覆写；同一来源仍用稳定逻辑身份去重，不能通过换 schema/policy 版本反复生成逻辑重复经验。已有同 ID 的旧记录需要转换时仅记待单独迁移，不自动制造第二份。

### C. 查询与检索

| 文件 | 拟修改的内容 | 对应验收 |
|---|---|---|
| 新增 `agrefactor/evidence/experience_retrieval.py` | 定义 `CurrentCaseQuery`、候选及排除原因；只从本次 allowlist 证据/源码窗口构造 query，读取 B 的经验快照，执行硬过滤、相关性排序、正负分区与空结果策略。 | 硬过滤、确定排序、相关跨阶段优先、unknown 诊断检索与空结果。 |
| `agrefactor/cpp_interface.py` | 原则上直接调用既有公开函数；仅在确有必要时增加有限特征投影函数，复用 libclang 与当前已验证 compile context，不重写解析器。 | 真实有限源码样例可取得接口/类型；解析失败是特征缺失，不等于不相似或归责证据。 |

query 包含阶段、视图、精确错误码/类别、已有/缺失/矛盾证据、已确认 owner 与可选线索、目标组件、工具/合同、有限符号/类型，以及本案例已尝试的具体动作和验证结果。当前上下文来自本次运行已定位的编译单元；调用 libclang 时可使用该单元必要包含依赖，不递归扫描项目或其他运行目录。不要截取不完整的几行 C++ 强行当完整翻译单元解析。

排序先检查适用性，再比较错误特征、证据和源码重合；owner/阶段相同本身不足以入选。第一版采用可解释的相关性分层和规范化词项/符号集合重合，不训练权重、不引入 embedding。相关性接近时同阶段优先；跨阶段超过同阶段候选须具有明确适用标记，并能说明更强的当前错误/证据匹配。

实施时先用开发回放集确定分层门槛和近似相关区间，将算法版本、规范化规则、门槛和排序键固化进回放 manifest 后再跑留出集；不看留出结果反复调参。数据不足时只报告可解释基线和合同通过情况，不包装成已经校准的相似度模型。候选审计集与实际注入集保持分离，未入选原因不得省略。

### D. 统一注入与模型计数

| 文件 | 拟修改的内容 | 对应验收 |
|---|---|---|
| 新增 `agrefactor/models/model_token_counting.py` | 使用已有 tokenizer 读取固定本地工件；按 provider、endpoint、effective model、请求模式选择已核实的文本/消息计数规则及上下文配置。单独标识文本精确计数与消息封装计数的验证状态。 | 默认 DeepSeek 的真实 tokenizer 可用，别名/自定义端点不误套配置，缺失配置有明确原因。 |
| 新增 `agrefactor/models/tokenization_sources/deepseek_v4_flash.json` | 登记官方仓库 URL、固定 revision、tokenizer 文件 SHA-256、库版本要求、API 模型对应关系、API 上下文/消息封装来源和校验状态。未核实的值不填成 verified。 | 本地配置可审计，工件被替换时拒绝使用；不把权重配置长度冒充 API 服务承诺。 |
| 新增 `agrefactor/prompts/experience_context.py` | 从候选集中构造“历史参考”区块，正负分区、适用条件、验证范围、来源、真实 diff；最多 3+/2-，整体装不下就跳过；构造后再数最终消息。 | 15% 公式、完整请求约束、长基础提示、非 ASCII/C++、条目整体跳过。 |
| `agrefactor/prompts/candidate_repair.py` | 在既有 snippets 位置接收本轮通过校验的经验；明确当前证据/规则优先与禁止盲目复制。 | 有经验与空经验模板，不改变 Candidate 完整代码输出合同。 |
| `agrefactor/prompts/layered.py` | 复用已有区块组织；manifest 增加经验 ID、计数配置身份和筛选理由等安全字段，不写入查询正文。 | 模板兼容、Hidden 检查保持、旧调用不传经验时输出兼容。 |
| `agrefactor/models/candidate_adapter.py` | 最终角色参数化之后将实际 model/endpoint、`max_tokens` 等输出预留和最终 messages 与预算器对齐，避免按过时默认值计数。 | 计数消息与 provider 收到消息相同；实际输出预留被采用。 |
| `agrefactor/runtime/prompt_evidence.py` | 在已有调用证据中关联 query digest、经验库 revision、候选/注入 ID、过滤原因、token 计数与模型身份；仅安全元数据。 | 实际派发与计划选择能区分，未派发不冒充注入成功，无原始查询/源码/Hidden 内容。 |

保留已确认公式：

```text
B_exp = min(floor(0.15 * C), max(0, C - R_out - P_base - R_safe))
```

`P_base` 必须覆盖无本轮经验的完整消息，包括 system、基础源码/反馈和生成路径的历史对话；`R_out` 来自最终调用角色参数，`R_safe` 必须有消息封装与校验偏差依据。加入经验后再对完整请求复核，不能简单相加各条文本 token 数来忽略边界变化。模板标题、条件和来源也占经验预算。基础提示已超可用空间时本轮不加经验，沿用原有请求处理，不把 P4 变成无关的全局提示裁剪器。

### E. 每个修复入口接线

| 文件与入口 | 拟修改的内容 | 对应验收 |
|---|---|---|
| `agrefactor/repair/candidate_loop.py`，`run()`、`_build_prompt()` | 增加可选本轮上下文回调。在当前 route/输入合同及现有派发前可用性检查通过后、provider 调用前，使用最新 feedback/current_candidate/prior attempts 检索并构造提示。 | 第一次及后续语义修复均重新查询；本地拒绝/预算前置阻断不误记为已注入；失败后可补全同方向。 |
| `agrefactor/runtime/candidate_repair_integration.py`，`run()`、`_repair_public_testbench_once()`、`_refresh_public_runtime_contract()` | 构造并传入同一检索服务；覆盖正式 Candidate、Public Testbench 及既有 runtime contract 修正路线。由本地直接更新 metadata 的分支仍检索，但只消费 allowlist 结构化参考；无 prompt 时不伪造文本注入。 | CSIM/CSYNTH/COSIM 保持各自资格与验证合同；目录是否命中不关闭检索；确定性动作不读取任意历史指令。 |
| `agrefactor/testing/testbench_repair.py`，`TestbenchRepairRequest`、`TestbenchRepairLoop.run()` | 为每次修复传递当前 preflight/runtime feedback、组件状态和 prior attempts，保持 Public 原有资格门。 | 准备期与正式 Public 修复每轮都更新；Hidden 正式评测无入口。 |
| `agrefactor/testing/model_testbench_repairer.py`，prompt builder、`repair()` | 在本次 effective call parameters 确定后运行统一注入器，用最终 `LayeredPrompt.messages` 调用 provider。 | Testbench 与 Candidate 共用计数/条数规则，提示实际包含本轮经验。 |
| `flow/tools/tb_optimizer.py`，`run_trajectory()`、内层 `request_testbench()`/`request_stub()`、`generate_public_runtime_contract()` | 语义 repair 请求前按实际组件/trajectory 最新状态查询；初始生成和纯格式重试不新增语义 Attempt。兼容转发 optimize/public/seeded/hidden 生成入口参数。使用 AG2 完整消息 hook 做临时注入与最终计数。 | 并发 trajectory 不串上下文；Testbench/Stub 分组件查询；初次生成、格式重试、语义修复区分；历史经验不逐轮积累。 |

现有 Candidate 的 RecoveryLedger reserve 位于模型返回之后；P3 已明确不移动它。P4 不把后置 ledger 拒绝倒推成“先前模型调用未发生”，也不提前完成 P6 的统一原子扣额改造。只在已有派发前门实际拒绝时保证零检索/零派发；仍保持当前账本顺序和记录事实。

生成路径已确认 AG2 0.11.5 提供 `register_hook("process_all_messages_before_reply", ...)`。实现将 hook 绑定到本条 trajectory 的 agent，只修改发送副本，不把临时历史经验写入持久对话。完整计数还需计入 system 以及 transport 最终展开的消息；验收必须用 fake client 捕获最终 `create(messages=...)`，证明计数对象与实际请求一致，不能只测 `_agent_run_once()` 的新增字符串。若工具 schema、历史消息模式或最终序列化形式未登记，按模型/模式缺少计数配置处理，不猜数。

Hidden-generation 仅以专用投影查询明确标记可用于该视图的 agent-safe 公共经验；不持久化 Hidden query、源码、诊断或可关联的敏感内容摘要。投影不足或无适用经验返回空，不影响已有生成资格流程。正式 Hidden 评测反馈不进入检索/AI。

### F. 受限 live AI 诊断

| 文件 | 拟修改的内容 | 对应验收 |
|---|---|---|
| 新增 `agrefactor/recovery/diagnostic_assistant.py` | 定义独立诊断请求/结果，复用 provider 和常规模型预算；输入仅本次安全证据与实际提供的历史经验，输出严格 JSON：引用、可能责任线索、不确定性、登记 check ID、abstain。 | 缺失证据仍可分析已有内容；空经验可分析；非法引用/额外权限字段/任意命令/补丁被拒绝。 |
| `agrefactor/runtime/candidate_repair_integration.py` | 在当前本地路径不能安全决定下一步的位置调用新 advisor；以当前证据身份去重，避免同一停留状态循环调用；更新后状态可再次诊断。Candidate 内循环终止时也覆盖，不能只接最初失败。 | 确定性路线不额外调用归责模型；建议无法改 owner、repair eligibility、额度、验证合同或停止条件。 |
| `agrefactor/recovery/shadow_advisor.py` | 优先调用或复用其中可独立使用的投影/引用校验思路；仅在确需抽取共用纯函数时修改。旧 `run_shadow_diagnostics()` 保持事后行为。 | 旧 shadow 合格/拒绝路径和主结果等价检查；不将旧 suspected_owner/scope 当新授权。 |

当前没有统一 collector registry。P4 的可推荐 check allowlist 从已登记的既有只读入口显式构造，没有可用项时为空；不把任意 Python 函数自动登记。实际专项补查 dispatcher 归 P5。因而 P4 中 unknown 可获得有用建议，但若当前证据仍不足以满足本地资格，它继续保持 unknown/复核状态；不能为了演示“AI 接管后能修”增加越权兜底。

新 advisor 对本地已安全选定路线不重复归责；每次修复前检索仍照常执行。AI 消耗常规调用/token 预算，没有额外组件修复次数。按证据身份去重只控制重复诊断调用，不作为“重复报错就停止修复”的启发式条件。

## 4. DeepSeek tokenizer 来源与 Clash 前置步骤

### 4.1 已实际核实的来源

上轮已通过服务器 Clash 代理访问并核实：

- 官方仓库：[deepseek-ai/DeepSeek-V4-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash)。
- 核实 revision：`60d8d70770c6776ff598c94bb586a859a38244f1`。
- `tokenizer.json`：BPE，128,000 个词表项、1,283 个 added tokens。
- `tokenizer_config.json`：`PreTrainedTokenizerFast`；该次读取没有 `chat_template`。
- tokenizer 配置中的 `model_max_length=1048576` 是仓库配置事实，尚不能直接当作当前 API endpoint 的可用上下文限制。

**这些仓库信息作为 P4 实施时确认 tokenizer 来源的依据。** 必须固定 revision 和实际文件 SHA-256，不能使用会变化的 `main` 后声称实验工件不变。只下载 tokenizer/必要配置，不下载模型权重，不启用远端自定义 Python 代码。

### 4.2 外网不可达时使用现有 Clash

已确认服务器运行 Clash Verge，混合代理监听 `127.0.0.1:7897`；未设置代理的 SSH shell 直连 Hugging Face 曾超时，显式指定该代理后返回 HTTP 200。实施时先 source 环境，再检查端口；直连失败可通过现有代理访问，不要把单次超时解释为官方 tokenizer 不存在。

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
ss -ltnp | grep '127.0.0.1:7897'
curl --fail --silent --show-error --location \
  --proxy http://127.0.0.1:7897 \
  --connect-timeout 10 --max-time 30 \
  https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Flash
```

下载使用同样的显式 `--proxy`，固定到 `.../resolve/60d8d70770c6776ff598c94bb586a859a38244f1/tokenizer.json` 及必要配置。通过 Python 下载时仅在该下载进程设置对应代理；不修改服务器全局网络、VPN 节点、系统代理或 `/data/agrefactorpp_env.sh`。端口变化时先核实实际 Clash 监听端口，不能盲目假设固定端口永远有效。

tokenizer 文件存入项目运行根目录下独立缓存，例如 `/data/agrefactor_runs/model_tokenizers/deepseek-v4-flash/<revision>/`，与 Case/ExperienceStore 分离；仓库仅保留小型来源 manifest。正常修复从经过 hash 核验的本地缓存计数，不在每次修复前联网。

### 4.3 默认模型必须通过的计数验收

先核实有效 provider、endpoint、实际 API 模型 ID、推理/非推理请求模式、输出参数和官方 API 上下文承诺，再建立对应配置。不能因为开源仓库名称相似就默认 API alias 一定完全相同。

文本 token 使用官方 tokenizer；完整请求还须取得可信的消息封装方式。使用官方序列化资料，并以少量独立的调用前/服务端 usage 对照校验。新增的 `scripts/verify_model_token_counting.py` 默认离线检查缓存和来源，只有显式 `--online` 才对当前已配置 endpoint 发出合成短消息探针；在线模式最多 12 个请求，逐请求输出上限 8 tokens，不运行 HLS，不发送真实项目源码或 Hidden 内容，不打印凭据。使用既有 provider/预算，不另造 API 客户端。

在线校验与真实 HLS 效果实验分开报告。测试覆盖单轮、多角色、多轮、中文、C++ 与项目实际会用的请求模式，记录本地计数、服务端 `prompt_tokens`、偏差、模型身份和保留余量。官方来源若能完整证明某模式精确计数，可据此登记；仅有限样本一致不能宣称所有可能请求数学精确。不能用某次低估后扩大一个随意常数来替代可信消息格式依据。

若默认 DeepSeek 仍缺少可信的完整请求计数方案，应报告具体缺口并提出替代方案供用户审阅，不把默认模型静默改为永远零注入并宣布 P4 完成。未登记的自定义模型/endpoint/mode 可以零注入并记录 `tokenizer_unavailable`，原修复继续。

## 5. 新增与扩展测试的冻结范围

### 5.1 六个拟新增模块

| 模块 | 必须覆盖的合同 |
|---|---|
| `tests.test_experience_retrieval` | v2 适用性、schema/hash 拒绝、owner/组件/可见性、跨阶段条件、正负分区、排序稳定、空结果、未知 owner 不给专用 diff。 |
| `tests.test_experience_prompt_budget` | 3+/2-、15%、完整消息上限、实际输出预留、整体条目跳过、标题/来源成本、生成历史不积累、未登记模型回退。 |
| `tests.test_model_token_counting` | 固定官方 tokenizer 本地工件与已登记消息规则、SHA-256 核验、有效 endpoint/model/mode 映射、中文/C++/多轮计数和离线缓存缺失。 |
| `tests.test_diagnostic_assistant` | 严格 JSON、引用和 check 白名单、证据不完整时不虚构、空经验、provider/预算失败、不得修改 owner/权限/预算。 |
| `tests.test_diagnostic_assist_integration` | 四种开关组合；Candidate、准备期/正式 Public Testbench、Public Testbench/Stub 生成、runtime metadata 修正逐轮接线；Hidden 投影；AG2 实际发送消息捕获；后续失败也可诊断。 |
| `tests.test_experience_replay` | 冻结脱敏回放、整家族隔离、低相关/冲突空结果、负面中间步骤排除、原因码、无正式经验库写入。 |

现有 40 模块中按职责扩展必要断言，重点修改 `test_case_record`、`test_case_store`、`test_experience_derivation`、`test_case_capture`、Candidate prompt/loop/adapter/integration、Testbench prompt/loop/factory、Public generation、CLI/bootstrap/adapter、分层 prompt，以及上文明确的旧 shadow 夹具。未新增可观测行为的文件不为凑数补测试。

### 5.2 回放与冻结产物

新增 `tests/fixtures/diagnostic_experience_replay/`：`manifest.json`、经过审计的 agent-safe 场景 JSON、必要的微型源码/diff；新增 `scripts/replay_diagnostic_experiences.py`。脚本复用检索函数，只读取清单内 fixture，不自动扫描 `/data/agrefactor_runs` 或向正式库导入数据。

以 10 月 7–12 日多个批次为来源参照，优先较新批次；至少涵盖不同错误、同任务不同版本、空库/不相似、证据缺失、三次补全后成功与具体动作明确失败。只选可核对的 Public 材料；真实数据不足的场景用明确标记为 synthetic 的最小样例，不能冒充历史运行证据。每项清单记录来源批次/相对路径、被允许提取的字段、源工件 hash、源码家族、fixture hash、人工审阅标签及 synthetic 标记。不把原始完整运行 JSON、完整源码、隐藏内容或 prompt 拷入 fixture。

生成/筛选开发集与留出集按完整源码家族隔离；同名算法的多次独立重跑进同一分区。先冻结来源与标签，再定开发门槛，最后只在留出集报告结果。审计报告列出逐项候选、硬过滤、排序、空结果原因和实际注入结果；不以有限回放宣称真实修复率提升。

## 6. 冻结验收命令

每个命令块都在同一 shell 先 source 环境文件。R1/R2 已于本轮在冻结基线执行；N1/D1/T1 是实施后验收，当前尚未执行且部分文件尚未创建。新增的六模块必须有真实用例，不能用空文件让 import 通过代替实现。

### R1：35 个既有主要回归模块

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_case_record \
  tests.test_case_evidence \
  tests.test_case_store \
  tests.test_experience_derivation \
  tests.test_case_capture \
  tests.test_public_tb_generation_case_capture \
  tests.test_candidate_repair_loop \
  tests.test_candidate_repair_integration \
  tests.test_candidate_repair_prompts \
  tests.test_candidate_model_adapter \
  tests.test_unified_candidate_repair_phase \
  tests.test_testbench_repair \
  tests.test_testbench_repair_factory \
  tests.test_model_testbench_repairer \
  tests.test_layered_prompt_builder \
  tests.test_cli_parameter_contract \
  tests.test_legacy_refactor_adapter \
  tests.test_p2_source_only_bootstrap \
  tests.test_p0_dual_generation_profiles \
  tests.test_p0_public_testbench_repair_routing \
  tests.test_tb_optimizer_hardening \
  tests.test_hidden_generated_differential_recovery \
  tests.test_lightweight_hidden_testbench_contract_recovery \
  tests.test_lightweight_hidden_stub_recovery \
  tests.test_lightweight_hidden_tool_recovery \
  tests.test_r2_shadow_advisor \
  tests.test_effective_model_config \
  tests.test_openai_compatible_provider \
  tests.test_recovery_action_charge \
  tests.test_recovery_authority \
  tests.test_repair_protocol \
  tests.test_feedback_routing \
  tests.test_test_generation_exhaustion_contract \
  tests.test_p4_0d_public_rtl_cosim \
  tests.test_p4_0d_r1_cosim_contract
```

本轮基线：591 项，1 项旧 shadow 测试失败；P4 验收目标为此组全通过，不能把已知项长期排除。实施后因新增有效断言导致数量增加时报告真实数量。

### R2：5 个基础合同与阶段保护模块

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_p0_hidden_boundary \
  tests.test_csynth_bounded_trial_integration \
  tests.test_csim_stage \
  tests.test_r4_episode_foundation \
  tests.test_catalog_diagnostic_projection
```

本轮基线：42 项全部通过。R1/R2 合计 40 个既有模块，保留 P2/P3 已覆盖的必要合同及阶段资格，不把新辅助建议当作授权。

### N1：6 个新增模块

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_experience_retrieval \
  tests.test_experience_prompt_budget \
  tests.test_model_token_counting \
  tests.test_diagnostic_assistant \
  tests.test_diagnostic_assist_integration \
  tests.test_experience_replay
```

### D1：固定 fixture 的离线回放

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
python scripts/replay_diagnostic_experiences.py \
  --manifest tests/fixtures/diagnostic_experience_replay/manifest.json \
  --output /tmp/agrefactor_p4_retrieval_replay.json
```

脚本若遇到路径越界、hash 不符、跨分区家族泄漏或固定合同断言失败，应非零退出。完成报告保留 manifest 与输出 hash。阈值、开发/留出分区和标签都不能在此命令中临时猜测。

### T1：真实 tokenizer 与消息计数来源核验

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
python scripts/verify_model_token_counting.py \
  --model deepseek-v4-flash \
  --output /tmp/agrefactor_p4_token_counting_offline.json
```

离线模式检查真实缓存、来源 manifest 和消息模式，不能仅返回 mock 计数。需要服务端 usage 对照时，在本清单获认可后执行显式在线模式，限额由脚本硬约束：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
python scripts/verify_model_token_counting.py \
  --model deepseek-v4-flash --online \
  --output /tmp/agrefactor_p4_token_counting_online.json
```

工具不得自动寻找或输出新凭据，仅使用项目已有有效模型配置。离线合同测试通过、真实 tokenizer 可加载、消息格式有可信来源及实际模式计数验证均满足，才可把默认模型计数支持标为完成；仅 mock 通过不足。

### S1：语法和差异检查

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
python - <<'PY'
import ast
import pathlib
import subprocess

changed = subprocess.check_output(
    ["git", "diff", "--name-only", "e740f83cd7f39cbec952ef88bdb0726962b29397", "--"],
    text=True,
).splitlines()
added = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard", "--",
     "agrefactor/evidence/experience_retrieval.py",
     "agrefactor/models/model_token_counting.py",
     "agrefactor/prompts/experience_context.py",
     "agrefactor/recovery/diagnostic_assistant.py",
     "scripts/replay_diagnostic_experiences.py",
     "scripts/verify_model_token_counting.py",
     "tests/test_experience_retrieval.py",
     "tests/test_experience_prompt_budget.py",
     "tests/test_model_token_counting.py",
     "tests/test_diagnostic_assistant.py",
     "tests/test_diagnostic_assist_integration.py",
     "tests/test_experience_replay.py"],
    text=True,
).splitlines()
paths = sorted({p for p in changed + added if p.endswith(".py")})
if not paths:
    raise SystemExit("No P4 Python files found; this is not implementation acceptance")
for name in paths:
    path = pathlib.Path(name)
    ast.parse(path.read_text(encoding="utf-8"), filename=name)
print(f"Parsed {len(paths)} changed/new Python files")
PY
git diff --check
git status --short
```

不对服务器所有未跟踪文件执行 compile/discover；已有 `.orig/.rej`、backups、native_csim 和临时目录保持原样。每组结果记录命令、HEAD/工作树差异、实际数量、失败/跳过及日志位置。R1/R2/N1 的普通测试不得调用外部模型或真实 Vitis；T1 的显式在线探针另列。

## 7. 实施顺序与阶段完成门

1. **先冻结基线与来源。** 确认 HEAD 未变化，保存仅本阶段相关文件的可恢复快照；按第四节准备官方 tokenizer 依据并解决 API 消息计数来源。若默认模型计数无法建立，先报告替代方案，不让代码接线掩盖缺口。
2. **再完成 A–C。** 开关贯通、v2 安全来源、幂等读取、负向准入和确定性检索；在临时目录测试，正式经验库不做迁移或回填。
3. **完成 D–E。** 先用 mock 与真实本地 tokenizer 检查预算，再把同一服务接到所有列明入口；捕获 provider 实际消息核对，确认失败后状态更新和并发隔离。
4. **完成 F。** 引用受限的 AI 诊断接入；保留主流程授权和已有账本时序，完成旧 shadow 合格夹具与拒绝场景覆盖。
5. **按 R1/R2/N1/D1/T1/S1 验收并更新状态。** 逐项报告覆盖、实际结果及未完成项；不因单元测试通过就宣称真实 HLS 修复收益。

实施期间若范围需要实质扩大，或需要改变当前权限/账本时序、采用默认模型计数的替代路线，应先说明实际需要及影响，更新清单再请用户审阅；为完成已批准合同所需的局部辅助文件、常规函数命名和实现选择由执行时决定，并在完成报告列明。

## 8. 文档同步、交付与回滚

本轮已同步本清单、P4 v1.1 实施计划和 v0.3 执行状态。P2/P3 计划与已完成实现未重写；shadow 基线问题按本清单追踪，历史通过数字保留。

核验确认 v0.3 引用的 2026-10-14 服务器代码审计报告已存在于服务器，且与本地副本 SHA-256 一致；无需再次复制或改写报告正文。

P4 实施结果、验收命令、tokenizer 来源/校验与离线回放 manifest 已追加至本清单和 P4 计划，v0.3 执行状态同步更新。权威路线仅更新已验证状态，不把 P5/P6 标为完成。

运行中检索审计保存候选/实际注入身份、原因和计数；普通产物不复制完整 prompt/query，Hidden-generation 不落盘敏感查询及其可关联摘要。需要评审具体模板时使用本地合成样例或明确允许的 Public 内容。

回滚以本基线和阶段快照为锚点，仅回滚本阶段代码；追加式 Case/Experience 数据保留。两个开关默认关闭，为默认流程提供原路径。不得用 `git clean`、整体 reset 或删除历史经验作为省事的恢复手段。

用户已于 2026-10-16 审阅认可本清单并授权 P4 实施。P4 已完成；本阶段没有 commit/push，也没有执行真实 HLS 实验或清理旧 R3/R5。P5/P6 仍需分别提交实施计划并经用户评审。

## 9. 审阅重点（已确认）

用户已确认以下实现边界并授权按本清单实施：

- 逐文件接线是否覆盖你希望的“每次修复前检索”，包括准备期 Public Testbench、生成型 Testbench/Stub 和已有 metadata 修正。
- 是否接受上述来源完整性处理：旧经验不猜补；连续补全不误作负例；完整改动依据不足时保留案例、不给出虚假的完整成功写法。
- DeepSeek 默认模型计数验收、固定官方 tokenizer 工件，以及 Hugging Face 连接失败时使用现有 Clash 的操作是否清楚。
- 冻结的 40 个既有模块、6 个新增模块、离线回放与单独 token 核验，以及旧 shadow 测试夹具校正是否足够。

这些审阅点已反映在本次实施中；不重新打开已确认的方向决策。

## 10. P4 实施结果

服务器分支和冻结 HEAD 保持为 `backup/server-current-20261013` / `e740f83cd7f39cbec952ef88bdb0726962b29397`。没有 commit/push，也没有运行真实 HLS 案例。

| 验收项 | 实际结果 |
|---|---|
| R1：35 个既有模块 | 594 项通过；原 shadow 用例通过合格输入和拒绝输入覆盖修正。 |
| R2：5 个基础合同/阶段保护模块 | 42 项通过。 |
| N1：6 个新增模块 | 30 项通过。 |
| D1：固定离线回放 | 4 个场景通过；manifest SHA-256：`bbc003352fb41153d83ad009a4af098c4a744cbef098dcf0a1a613ba508825b0`；输出 SHA-256：`8bc95441f2e11bd6e28a7dee7421e690b8d1b0eb0d2dec665f4facca4f346b58`。回放使用合成 fixture，不把历史运行元数据当作正式经验。 |
| T1：tokenizer 离线核验 | `deepseek-ai/DeepSeek-V4-Flash` 固定 revision `60d8d70770c6776ff598c94bb586a859a38244f1` 的真实 tokenizer 通过 12 个样例；来源 manifest SHA-256：`ff997703414afb9a28498887c70a3d88a8194a3f4da028f2ffe1628c68572e1d`，实际 tokenizer 与配置文件哈希记录于 manifest。 |
| T1：API usage 对照 | 先前已完成 12 个合成请求，本轮未重复；服务端与本地 prompt token 差值为 +2 至 +3，安全余量为 3。输出 SHA-256：`bfc1439bf561d8a40f393e60b8a2bba34cf90f424f6049992d9f05e190ba02cc`。这验证了测试消息模式的计数余量，不声称普遍精确相等。 |
| S1：语法/差异检查 | AST 解析 34 个变更/新增 Python 文件，`git diff --check` 通过。 |

实施中修正了 Case 事件首次落盘未保存 `visibility` 的问题；公共事件在写入时显式保存 `public` 或 `public_generation`，Hidden 和非公共事件仍被拒绝。R1/R2/N1 未调用外部模型或真实 Vitis。在线 tokenizer 探针只使用合成短消息，未发送项目源码或 Hidden 内容，也未下载模型权重。

回放与 tokenizer 结果文件位于服务器 `/tmp/agrefactor_p4_retrieval_replay.json`、`/tmp/agrefactor_p4_token_counting_offline.json` 和 `/tmp/agrefactor_p4_token_counting_online.json`。结果不代表真实 HLS 修复成功率提升。
