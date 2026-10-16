# P4 实施计划：经验检索与 AI 辅助诊断

- 版本：评审稿 v1.1
- 日期：2026-10-16
- 状态：v1.1 设计与逐文件清单已获用户认可；P4 已实施并通过 R1/R2/N1/D1/T1/S1，详见第 10 节和配套清单
- 服务器仓库：`/data/AgRefactor`
- 计划起草基线：`backup/server-current-20261013`，提交 `35bbead8ac4059d00c776d6564140800463bd02c`；实施前应基于已推送的四项修正后分支 HEAD 重新核对文件和验收清单
- 逐文件核对基线（2026-10-16）：同一分支，提交 `e740f83cd7f39cbec952ef88bdb0726962b29397`
- 配套清单：[P4 逐文件实施清单与冻结验收命令](P4_IMPLEMENTATION_CHECKLIST_AND_FROZEN_VALIDATION.md)
- 上位设计：`docs/roadmap/后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md`

## 1. P4 要解决的问题

P1–P3 已建立诊断数据结构、案例记录和结案后经验整理的基础，但运行中的修复还不能跨案例检索这些经验，也没有在每次修复前根据最新状态重新查询的接线。AI 辅助诊断目前只有旧的事后 shadow 路径，不能接收新经验来辅助当前仍未解决的问题。

P4 将把新设计接入运行时：本地从当前阶段证据构造查询；先按适用性筛选并排序历史经验；每次合法修复模型调用前重新检索；只把少量、完整且预算允许的经验注入提示词；常规路线仍不能安全选定下一步时，再让 AI 基于当前证据和合格经验提出带引用的诊断建议。

经验只影响已获准动作的实现参考和优先顺序。当前证据、本地 owner/资格判断、规则、组件额度及验证合同仍是最终依据。AI 建议不改 owner、不授予权限、不生成或执行任意服务器命令，也不直接修改源码。

## 2. 当前基线与 P4 前置发现

P1 的代码覆盖审计已完成；v0.3 仍将代表性端到端基线标为待补齐。P2 历史记录称 7 个模块、87 项通过，但原模块清单未找到；按 P2 计划中明确列出的 5 个模块复跑为 71 项。P3 补充计划原记录 227 项；四项审计问题修正后，按同一 12 个模块复跑为 230 项。以上均为定向测试，不是代表性端到端基线。四项修正已实现默认关闭的 `--record-diagnostic-cases`，并统一 Candidate/Public generation 的 `RUN_DIR/diagnostic_experiences` 根目录；P2 采集器清单归属已移至 P5。项目正常 CLI 默认模型为 `deepseek-v4-flash`，使用 DeepSeek Chat Completions API；当前实现能读取调用后返回的 `prompt_tokens`，但没有调用前的 tokenizer/context-window 登记或计数器。

代码盘点确认以下可复用组件：

- `agrefactor/evidence/case_record.py` 已有 `DiagnosticCase`、`CaseAttempt`、`ExperienceRecord`、正负极性和闭案/解决状态合同。
- `agrefactor/evidence/case_store.py` 已有原子、追加式 Case/Experience 存储，以及带 hash 的实际修改片段存储。
- `agrefactor/evidence/experience_derivation.py` 已从已结案案例整理正向/负向记录，并以稳定身份避免重复写入。
- Candidate 提示词已有 `approved_memory_snippets` 接口；`SharedLayeredPromptBuilder` 已有统一的经验片段区块，Testbench prompt 也使用该 builder。
- `agrefactor/evidence/diagnostic_event.py` 和 `agrefactor/recovery/shadow_advisor.py` 已有 agent-safe 诊断事件、引用校验、严格 JSON 输出、provider 及预算边界的实现经验。
- `agrefactor/cpp_interface.py` 已有 libclang 接口，可供后续有限源码上下文提取使用；P4 不实现 HLS 214-298 专项检查。

四项修正包已补齐记录开关和共享经验根目录，因此它们不再是 P4 的待实施项。P4 仍需补齐：
- `ExperienceStore` 能写入记录，但没有枚举/读取检索候选及修改片段的 API。
- `ExperienceRecord` 当前只有较粗的 `stage.*`、`owner.*` 条件和验证引用，缺少检索所需的规范化错误特征、修改组件、可见性兼容标记、工具/源码特征等结构化字段。
- Candidate 修复循环当前拿到的是固定 `approved_memory_snippets`，不能在每次失败、证据更新后重新查询。Testbench prompt 也没有从修复状态传入经验片段。
- `run_shadow_diagnostics()` 在主流程结束后运行，且当前 advisor 要求 Public、owner unknown/review、证据完整等条件；它是 shadow-only 结果，不能直接作为本次 live 路由输入。P4 可复用其输入净化、严格解析及 provider/预算模式，不把其 shadow owner 或 scope 输出当作授权。
- 仓库内没有发现可用于提示词完整长度预算的统一 tokenizer/context-window 查询接口。P4 必须明确采用的实际模型 token 计数来源；没有可靠计数时不得猜算并超额注入。

P4 在已完成的记录控制和共享根目录上增加读取、检索、提示注入及受限 AI 诊断；不回头重做 P2/P3。还需修正不满足 v0.3 负向经验准入条件的部分。

## 3. 10 月 7 日至 16 日的运行证据盘点

已检查 `/data/agrefactor_runs` 下当前可用的日期批次。实际案例批次最新到 2026-10-12；本次未发现 10 月 13–16 日的新一批任务运行目录。P3 是 10 月 16 日落地，因此下表是 P3 之前的运行日志、运行结果和独立审计文件，不是 P3 格式的 CaseStore/ExperienceStore，也不能直接视为已经审核的经验。

| 日期与批次 | 观察到的运行结果 | 对 P4 的用途 |
|---|---|---|
| 10-07 `plan_p6_20261007_rerun3` | 9 个 `run_result.json`：3 个 succeeded、6 个 failed。失败摘要既有 held-out qualification，也有 ABI/result-mapping contract 不完整。 | 区分相同顶层 failed 状态下的不同阶段与错误特征；不能只按任务名或最终状态检索。 |
| 10-08 `app_generation_contract_20261008_v1/v2` | v1 9 项中 5 项 succeeded、4 项 failed；v2 中 `idct_generic` 与 `median_cut` 均 succeeded。v1 的失败包括 result-mapping JSON 不完整、held-out qualification 失败和 repair exhausted。 | 查询/经验须记录运行合同、工具环境和验证范围；相同算法名称的不同批次不能自动合并成一个 Case。 |
| 10-09 `bounded_validation_repair_20261009` | `av1_apply_temporal_filter`、`av1_compound_type_rd` 两个 attempt 目录均以 `validation_terminal` 结束。 | 结案失败与单次 validation failure 不能混为一谈；没有合格的已解决来源时，不生成可跨案例检索的负向经验。 |
| 10-10 `app_current_default_serial_20261010` | 13 项中 8 项 succeeded、4 项 failed、1 项 error。`ctc_tb_qr_repair_20261010` 的 6 项均 succeeded。 | 检索应能返回空结果或分阶段结果，不因同一批有多个成功记录就将全部当作相似修复。 |
| 10-11 `app_type_decl_timeout_repair_20261011` 与 `feedback_precision_repair_20261011` | 有成功、失败和 error 混合；`av1_apply_temporal_filter` 在新运行中 succeeded，`av1_compound_type_rd` 仍有失败，`argon2_fill_segment` 有 error。 | 具体失败改动必须与后续验证及源码版本绑定；跨运行的同名任务不是同一个 Case。 |
| 10-12 `leetcode_serial_current_20261012` | 10 项中 8 项 succeeded、2 项 failed。 | 提供功能验证成功/失败混合的候选回放数据，不能仅凭运行总结推断根因。 |
| 10-12 `contract_observation_repair_20261012` | 8 项：5 succeeded、2 failed、1 error。`blake2b_compress` 初始目录为 error，另一个 `blake2b_compress_retry` 运行 succeeded；`av1_compound_type_rd` 及 `av1_compound_type_rd_parser_fix` 仍 failed。 | 用于验证版本/尝试身份隔离及“独立重跑不等于恢复同一未结案 Case”。不可把前次失败自动整理成后次成功的负向经验。 |

该批次的 `final_verification_summary.json` 记录 579 项回归测试通过，但其 `scope` 明确是静态检查和回归测试、不含真实案例重跑。计划不会把 579 项单元/集成回归误报为真实运行样本。

### 历史运行数据的使用边界

10 月 7–12 日的产物用于：识别当前日志/报告字段、整理检索测试场景、构造带稳定来源身份的离线回放清单，以及发现相似任务跨批次结果不一致的泄漏风险。它们暂不自动导入正式 ExperienceStore。每个旧运行目录通常只有独立运行结果和部分诊断/审计材料，不具备 P3 的统一 Case/Attempt、实际修改关联、授权快照和经验准入标记；只有补齐来源、改动与验证关联后，才可单独评估是否手工转换为带来源标记的测试 fixture。

离线评测按完整运行目录或源码家族留出。同一算法的重复运行，例如 `av1_compound_type_rd`、`av1_apply_temporal_filter`、`blake2b_compress`，必须放在同一数据分区，避免把相同问题的后续复跑泄漏到检索库中。Hidden 内容、Hidden 标签和可能反推出 Hidden 用例的信息一律不提取进 fixture、索引或提示词。

## 4. 设计与行为合同

### 4.1 两个默认关闭的开关

下表名称随 v1.1 设计获认可；功能边界与默认关闭已由 v0.3 确认。记录开关已经实现，辅助开关在 P4 实施。

| 建议开关 | 默认值 | 开启效果 |
|---|---:|---|
| `--record-diagnostic-cases` | false | 写入 agent-safe 案例记录；案例结案后幂等整理经验。不开启时不新建案例或经验。 |
| `--diagnostic-assist` | false | 每次合法修复模型调用前查询经验；常规本地路线仍不能安全决定责任/下一项时，调用 AI 做受限诊断。不开启时不查询经验、不调用新 advisor。 |

两个开关相互独立。AI 辅助开启但库为空时，检索返回空，系统仍可只基于当前证据分析。案例记录开启但 AI 辅助关闭时，继续记录和整理，不做跨案例检索。两个开关均关闭时，默认产品流程保持原行为，不写 CaseStore、不触发 provider 诊断调用。

### 4.2 查询输入

每次修复前由本地代码从“当前状态”生成临时 `CurrentCaseQuery`，而不是用运行开始时的冻结快照。字段包括：

- 阶段、可见性、错误类别/错误码及规范化词项；
- 当前确认 owner、可选责任线索、证据满足/缺失/矛盾状态；
- 工具/编译环境/运行模式、已验证的源码角色和编译单元；
- 报错函数/接口/符号/类型等有限上下文；
- 当前 Case 已尝试动作、真实改动指纹与每次验证结果。

受限上下文仅从当前运行的 allowlist 证据、相关日志片段和报错/修改附近源码窗口构造。不扫描全仓、不读取其他运行目录、不将整份项目源码上传。历史经验里的源码片段只来自 P3 保存并 hash 校验的真实修改，不用模型生成的解释代替实际 diff。

### 4.3 候选硬过滤与可解释排序

先硬过滤，再排序。过滤理由使用稳定 reason code 记录。

1. 仅接受 ExperienceStore 中可解析、来源 Case 已 closed/resolved、来源证据和变更身份完整的记录。开放案例和最终未解决案例不作为跨案例经验。
2. 当前禁止的可见性、Hidden 评测来源、Hidden-generation 私有内容、来源/差异 hash 不匹配以及验证合同明确不兼容的经验直接排除。
3. 当前 owner 已确认时，owner/component 不匹配的专用改动不注入当前修复提示；通用诊断信息可在明确标记为通用且证据兼容时仅供诊断参考。
4. 当前 owner 为 `unknown` 时可以检索历史 owner 经验用于诊断比较；历史 owner 不能改写当前 owner。该情况下检索结果不带入历史组件修改代码片段，也不授予修复权限。
5. 负向经验须有稳定 `change_fingerprint`、明确的组件/阶段/执行条件，以及本地验证器明确记录的失败结果；provider error、timeout、预算耗尽、授权拒绝、未运行验证、单纯 Case unresolved 不形成负向经验。

通过硬过滤的候选按可解释字段排序：错误码/诊断类别和规范化错误词项、证据特征重合、有限源码符号/类型重合、owner/component 兼容度、工具/合同适用性、历史验证范围。相关性优先于阶段：相关性接近时同阶段优先；跨阶段经验只有在错误特征、证据、工具/源码条件更贴近且适用条件有明确支持时，才可越过相关性较弱的同阶段经验。

第一版不引入 embedding、向量数据库或训练权重。检索结果必须附上每个排序特征和原因码。排序阈值在已审计回放集上选取，按整批运行/源码家族留出，不用同一任务的重复运行同时训练和验收。若无候选越过阈值，返回空，不降低门槛凑数。

### 4.4 正向/负向经验形成与检索

- 正向经验只描述“哪些实际改动与哪些本地验证通过结果相关”，携带适用条件、组件、验证阶段/合同和真实源码 diff；不声称只靠单一改动已证明根因。
- 负向经验只描述在相似条件下某个明确改动/动作未通过哪项已执行验证；只在最终 resolved Case 中考虑，且必须证明该失败与具体 diff 和验证事件的关联。若它只是后续成功修改前的不完整中间步骤、证据不足、验证未执行或结果为 timeout/error，则只保留 Attempt，不可检索。
- 当前 `derive_experiences()` 对 `changed=True`、`validation_failed` 且存在 `accepted=False` 事件即可生成负向记录，条件目前只有 stage/owner。此门槛比 v0.3 的“具体动作、条件和失败结果明确关联”宽。P4 必须先收紧该准入，或将未满足强关联合同的现有记录标为不可检索；不能直接把这些广义负向记录注入提示词。
- 一次 Candidate 修复连续尝试后成功，不得被泛化为 Candidate 无效。不同根运行的失败/成功也不能自动合并成同一案例；只有 P3 稳定 Case 身份证明为同一未结案案例恢复时，才可沿用其 Attempt 链。

### 4.5 候选与注入集分离

检索模块返回完整候选审计集；注入模块再依条数、owner 与 token 预算择取。最多注入 3 条正向和 2 条负向。每条展示历史阶段/owner、适用条件、验证范围、来源引用及限制；正向与负向分区呈现。摘要和真实关键 diff 均须完整放入；放不下时跳过整条，不截断 diff。未注入的候选仍记录原因。

检索与注入事件至少记录 query digest、experience store revision、候选 ID、硬过滤/排序/未注入原因、最终注入 ID、token 计数、当前阶段、owner 状态、开关状态与模型身份。默认不持久化查询中的原始日志、源码窗口、Hidden 信息或完整提示词内容。

### 4.6 提示词 token 预算

保持 v0.3 已确认的 15% 上限，不改变整个 prompt 的预算公式：

```text
B_exp = min(floor(0.15 * C), max(0, C - R_out - P_base - R_safe))
```

其中 `C` 为当前有效模型上下文窗口，`R_out` 为本次请求的修复输出预留，`P_base` 为不含经验的完整请求消息 token 数，`R_safe` 为覆盖消息封装和计数偏差的安全余量。P4 必须为项目当前默认模型 `deepseek-v4-flash` 建立调用前计数支持，不能因缺少 tokenizer 映射而让默认配置长期跳过全部经验注入。实施前须核实该 API 模型对应的 tokenizer、上下文窗口和请求消息格式，并以明确的 `provider + endpoint + effective model id` 映射固定下来；对完整渲染的 message 列表计数，而不是只数正文文本。DeepSeek 响应中的 `prompt_tokens` 是调用后的观测值，可用于校验本地计数和安全余量，不能替代调用前计数。

对无法识别或没有经验证计数配置的自定义模型/endpoint，经验注入量为 0，记录 `tokenizer_unavailable`，但既有修复与只基于当前证据的 AI 分析继续。该回退只适用于未登记配置；若 `deepseek-v4-flash` 无法找到可信的调用前计数方法，P4 应先提出替代方案并经审阅，不能把默认模型静默归入此回退并宣称完成。测试验证默认 DeepSeek 配置有可用计数路径，经验总量不超过 15%，且完整 prompt 不超过 `C - R_out - R_safe`。该预算是最大值，不是填满目标。

**已核实的 tokenizer 来源（2026-10-16）：** 官方仓库为 [deepseek-ai/DeepSeek-V4-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash)，核实 revision 为 `60d8d70770c6776ff598c94bb586a859a38244f1`；`tokenizer.json` 使用 BPE，`tokenizer_config.json` 声明 `PreTrainedTokenizerFast`，未提供 `chat_template`。这些仓库信息作为 P4 实施时确认 tokenizer 来源的依据；固定 revision 和实际文件 SHA-256 后从本地缓存加载。仓库的 `model_max_length=1048576` 不直接视作 API 上下文承诺；API 模型对应关系、完整消息封装与计数仍须按配套清单核验。

**外网访问：** 服务器已确认有 Clash 混合代理 `http://127.0.0.1:7897`。命令仍须先 `source /data/agrefactorpp_env.sh`；直连 Hugging Face 失败时可在该下载进程显式使用现有代理。先核实实际监听端口，不修改全局代理或环境脚本，不在每轮修复时联网。具体命令及默认模型计数完成条件见配套清单第四节。

### 4.7 AI 辅助诊断边界

触发条件是本地解析、适用的确定性路线及已登记的本地证据检查后，系统仍不能安全判断责任/下一步；`owner=unknown` 是常见情况，但不是唯一条件。AI 可在没有历史经验时仅分析当前证据。AI 输出为独立建议，不覆盖 Case 中的原始 `unknown`。

新 advisor 的输出 schema 建议限制为：

- 当前 allowlist 中的 `evidence_refs`；
- 本次检索候选中实际提供的 `experience_refs`；
- 归因假设/可能责任线索及不确定性；
- 现有登记检查 ID 的建议列表；
- abstain reason。

任何引用 ID 不在本次请求 allowlist、建议检查 ID 未登记、输出格式无效或证据不支持时，拒收并 abstain。advisor 不提供 shell 命令、文件路径扫描指令、修复补丁或权限字段。即使假设指向 Candidate/Testbench，仍由当前本地 evidence/owner/policy/visibility/budget 决定是否有修复资格。P4 只允许调用已经登记且只读的现有证据读取器；HLS 214-298 专项合同与补查 dispatcher 仍归 P5。

旧 `shadow_advisor.py` 可复用 agent-safe 事件投影、引用白名单、严格 JSON 解析和 provider/预算失败处理模式；不能直接把其事后 `suspected_owner`、`repair_scope` 或 `bounded_repair_intent` 当作实时路由决定，也不把 `run_shadow_diagnostics()` 改造成新的权限门。

## 5. 实施文件清单与顺序

本节保留已认可的 P4 设计边界；具体接线位置与冻结命令见配套清单。实施结果与验收实绩见第 10 节。

### P4-A：AI 开关、经验可读取性与准入

已完成的前置项：

- 四项审计问题修正已添加并传递默认关闭的 `--record-diagnostic-cases`；Candidate 与 Public generation 记录器服从此开关。
- Candidate 和 Public generation 已统一从 `RUN_DIR/diagnostic_experiences` 使用共享经验根目录；Case 工件仍留在各自运行工件中。

P4-A 待实施修改：

- `agrefactor/cli.py`、`agrefactor/product/source_bootstrap.py` 及运行时入口：添加独立、默认关闭的 `--diagnostic-assist`，不改变案例记录开关的行为。
- `agrefactor/evidence/case_store.py`：为 ExperienceStore 增加安全、稳定的记录/修改读取与枚举接口；只读取其自身 `records/` 和 `changes/` 子目录，不遍历整个运行目录。
- `agrefactor/evidence/experience_derivation.py`：收紧负向经验准入，未满足明确 action/diff/validation 关联时只存 Attempt，不存可检索负向记录。

完成条件：两个开关均默认 false 且相互独立；P4 新增读取 API 仅访问经验存储自身子目录；启用记录后 Candidate 和 Public generation 的经验进入同一共享库；重复整理幂等；未解决 Case、Hidden 来源、弱关联失败不会成为可检索经验。

### P4-B：查询合同与确定性检索基线

新增建议文件：`agrefactor/evidence/experience_retrieval.py`。

主要职责：定义 `CurrentCaseQuery`、检索候选/过滤原因和可解释结果；从当前诊断事件与有限源码特征组装 query；读取 P3 `ExperienceRecord` 和 `StoredChange`；执行硬过滤、可解释排序与阈值；将全部候选原因交给审计记录。必要时以向后兼容方式扩展 `ExperienceRecord`，添加阶段、可见性、组件、诊断特征、来源身份、工具/源码特征和可检索状态。若旧 schema 不具备安全筛选需要的字段，缺字段记录默认拒绝检索，不猜补。

复用 `DiagnosticEvent`、P3 `ExperienceRecord`、`ExperienceStore` 和真实 stored diff。当前运行源码特征复用 `agrefactor/cpp_interface.py`：在已验证的本次编译单元和必要 include 上调用 libclang，仅投影相关符号/类型及有限源码窗口，不把截断窗口冒充完整翻译单元，也不扫描全仓。libclang 失败时保留词项检索，不能把失败解释为不相似或责任结论。

完成条件：相同输入产生稳定排序和理由；unknown owner 可以得到诊断候选但不能收到组件专用 diff；不适用/矛盾/Hidden/低相关经验会被解释性排除；检索可返回空；不引入 embedding/外部索引服务。

### P4-C：每次修复前动态检索与提示注入

修改：

- `agrefactor/repair/candidate_loop.py` 和 `agrefactor/runtime/candidate_repair_integration.py`：传递查询工厂/回调，在每次 repair provider call 前基于当前 feedback、当前 candidate、owner/资格状态和 prior attempts 重新检索，不复用整个 Case 初次查询的冻结结果。不得因一次旧 Attempt 失败而硬禁止同方向改进。
- `agrefactor/prompts/candidate_repair.py`、`agrefactor/prompts/layered.py`：复用现有 snippets 渲染区，明确标注“历史参考”，注入前由本地策略完成候选筛选和预算检查。
- `agrefactor/testing/model_testbench_repairer.py`、`agrefactor/testing/testbench_repair.py`：把同一经验上下文接到 Testbench repair prompt；候选集仍按组件/可见性筛选。
- `flow/tools/tb_optimizer.py`、`flow/new.py`：对每个已准入的 Public Testbench/Stub 生成资格修复请求，在对应 trajectory 的最新诊断、修改和验证状态上重新检索；不让并发 trajectory 共用过期查询快照。
- 建议新增 `agrefactor/prompts/experience_context.py`：集中做正负模板、条数限制、实际 tokenizer 计数和全项跳过；Candidate 与 Testbench 共用，避免两套预算逻辑。

正式 Hidden 评测反馈后的修复仍不可进入。对 Hidden Testbench/Stub 的每次合法生成期资格修正，使用专用 visibility projection 构造本轮查询；当前记录默认不包含 Hidden 生成经验，只有明确标为可跨该视图使用的 agent-safe 公共经验才可能命中。query/prompt 审计不落盘 Hidden 诊断、源码或可关联的敏感摘要；没有通过投影合同的请求返回空经验。P4 不把正式 Hidden 评测数据交给此路径。

完成条件：所有合法 Candidate、正式 Public Testbench 及 Public `tb_optimizer` 修复模型调用前均发生动态检索；有新证据后候选排序变化可测试；隐藏正式评测仍无修复入口；Hidden-generation 的不安全 query 不进入共享 Case/ExperienceStore 或 Candidate prompt。

### P4-D：受限 live AI 辅助诊断

新增建议文件：`agrefactor/recovery/diagnostic_assistant.py`。

修改：

- `agrefactor/runtime/candidate_repair_integration.py`：仅在确定性本地路径无法安全决定下一步时调用 advisor；其余确定性路线直接使用规则允许动作，但仍执行 P4-C 的修复前检索。
- `agrefactor/recovery/shadow_advisor.py`：优先提取可复用的纯校验/序列化逻辑；保持当前事后 shadow contract 行为不变，除非可兼容测试证明无行为变化。
- 复用已有 `ModelProvider`、有效模型配置和 `BudgetManager`；AI 诊断消耗常规模型调用/token 预算，不新增修复额度或“AI 额外修复次数”。provider 失败、token 预算不足或输出无效均返回 abstain，主流程按当前证据和既有安全条件继续/转人工。

advisor 仅接收本次 allowlist 证据和本次实际注入的经验 ID/内容；输出严格 JSON。P4 只允许从本地固定 allowlist 返回已有证据读取器的 check ID；模型不能创建 ID 或命令，P4 不执行新命令。当前没有统一 collector registry，P5 再接入实际的本地补查 dispatcher 和专项 collector；P4 没有可推荐的登记检查时就返回空列表。

完成条件：确定性路线可继续运行且不调用 advisor；unknown/证据不足时可得到引用当前 evidence/experience 的建议；输出不能改变 `FeedbackOwner`、repair eligibility、quota 或验证要求；无经验时可仅用当前证据；任何边界异常均安全 abstain。

### P4-E：回放评估、测试与文档

P4 验收测试覆盖：

- `tests/test_experience_retrieval.py`：硬过滤、同阶段优先/跨阶段强相关、unknown owner、正负分区、阈值拒绝、空结果和稳定理由码。
- `tests/test_experience_prompt_budget.py`：token 预算公式、DeepSeek 默认模型的计数映射、完整消息计数、上限 3+/2-、item 整体跳过、未登记自定义模型 fail closed、不同长度 base prompt；可用时将本地计数与服务端 usage 对照。
- `tests/test_diagnostic_assistant.py`：仅触发条件、证据/经验/check ID 引用校验、abstain、owner 不变、未注册 ID 拒绝、provider 失败无权限副作用。
- 扩展 `tests/test_candidate_repair_loop.py`、`tests/test_testbench_repair.py`、`tests/test_public_tb_generation_case_capture.py`：每次 attempt 重新查询、prompt 收到该次结果、非 repair 或拒绝派发不误检索/误计，以及 Public/Hidden 投影隔离。
- 扩展 CLI/source bootstrap 测试：两个参数默认 false、单独开启和组合开启的合同。

使用 mock provider/tokenizer 和 `/data/agrefactor_runs` 的审计后字段 fixture 做离线回放；常规验收不调用真实 LLM/Vitis，不写入真实全局 ExperienceStore。P4 可先报告检索命中、空结果、过滤原因、正负错误注入、跨批次隔离和 token 合规，不宣称样本不足以支持的修复率提升。

## 6. P4 验收矩阵

| 场景 | 预期结果 |
|---|---|
| 默认运行 | 两个开关均关闭；不写新 Case/Experience、不查询历史、不调用 AI advisor；原路径结果不变。 |
| 只开案例记录 | 仅记录 agent-safe Case；结案后按条件幂等整理；没有检索/provider 调用。 |
| 只开 AI 辅助且库为空 | 每次合法 repair 前查询并返回空；必要时 AI 仍可只用当前证据；运行不因空库失败。 |
| 已确认 owner 的确定性路线 | 不调用 AI 归责；repair 前经验只安排当前已允许动作中的实现参考，不增加动作或权限。 |
| owner unknown | 可取回历史 owner 经验作诊断参考，但不把历史 owner 变成本次 owner；专用 diff 不进入修复上下文；无授权仍不修。 |
| 当前 evidence 更新 | 前次修复失败后用新日志、源码与验证状态重新 query；前次失败可提示避免原样重复，但改进版本仍可尝试。 |
| 正向经验 | 只有成功验证的真实改动片段以正向分区注入，并标明验证范围/限制。 |
| 负向经验 | 只有已解决 Case 中 action/diff/条件/明确失败验证对应的负向经验可提示；不能禁止修复方向。 |
| 不相似或冲突经验 | 通过 hard filter 或阈值返回空/弃用，不为满足 Top-K 注入。 |
| token 空间不足 | 不超过 3 条正向、2 条负向、15% 经验区块上限；不截断条目，完整 prompt 不超上下文约束。 |
| 项目默认 DeepSeek 模型 | `deepseek-v4-flash` 在 P4 完成前必须有验证过的调用前计数和上下文窗口配置；服务端 `prompt_tokens` 用于事后校验。 |
| 未登记的 tokenizer/context | 该自定义模型的经验条目不注入并记 reason code；不使用字符数估算，不阻断原修复。 |
| AI 返回非法证据/经验/检查 ID | advisor abstain；原始 owner、权限、预算和验证合同不变。 |
| Hidden generation | Hidden-generated 资料不入跨案例库或 Candidate prompt；只有合规 visibility projection 可查询；无合适经验允许空。 |
| 正式 Hidden feedback | 不建立新修复或 AI 归责路线；现有 Hidden 禁止边界持续通过。 |
| 历史重复任务 | 10/7–12 同名任务按 run/source identity 分隔，holdout 按源码家族，不能把独立复跑合并成 Case。 |

## 7. 验收命令草案

本节保留初稿的命令示例；实际验收以配套清单第六节的 R1/R2/N1/D1/T1/S1 为准，不以此示例替代完整清单。实施前的历史基线为 40 个既有模块、633 项中 632 项通过及 1 项旧 shadow 测试夹具失败；P4 最终验收结果见第 10 节。服务器执行命令必须在同一个 shell 先 source 环境文件：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_case_record \
  tests.test_case_store \
  tests.test_case_capture \
  tests.test_experience_derivation \
  tests.test_experience_retrieval \
  tests.test_experience_prompt_budget \
  tests.test_diagnostic_assistant \
  tests.test_candidate_repair_loop \
  tests.test_testbench_repair \
  tests.test_public_tb_generation_case_capture \
  tests.test_unified_candidate_repair_phase
git diff --check
```

验收还需记录 Python compile 检查、实际通过数量、工作树差异摘要、测试基线 commit、离线回放输入清单、每类检索 reason code 计数和 Hidden 投影检查。不得把单元测试数表述为真实 HLS 样本数。

## 8. 风险、回滚与明确不做的事

- **低相关经验污染提示词：** 硬过滤、空结果和离线 holdout；禁止凑数。
- **历史 owner 越权：** owner mismatch 过滤；unknown 只允许诊断线索，advisor 不写回 `FeedbackOwner`。
- **负向经验泛化：** 修正 P3 宽松准入，无法证明动作与验证关联时只留 Attempt。
- **prompt 超上下文：** 真实 tokenizer、15% 公式、整体跳过；tokenizer 不可用时注入零条。
- **Hidden 泄漏：** 非 Hidden ExperienceStore；Hidden query 与日志走单独投影，正式 Hidden feedback 永不送入检索/AI；边界测试必须覆盖。
- **全局存储并发/损坏：** 复用原子文件写入和稳定身份；检索读文件失败只排除该候选并审计，不修改 Case/Experience 数据；P4 不引入数据库。
- **旧模块误复用：** 不沿用旧生命周期、A0–A6、snapshot-bound memory payload 或旧 R5 产品接线。候选 snippets 接口、P3 追加存储和 R2 的安全解析模式按职责复用；不清理旧 R3/R5 代码。
- **回滚：** 两开关默认关闭，P4 新行为只在显式开关启用时生效。回滚代码时保留追加式 Case/Experience 数据，不删除目录、不重写历史记录；旧 prompt 和旧 shadow 测试保持通过。
- **不在 P4 范围：** HLS 214-298 目录专项检查/本地补查 dispatcher（P5）；RecoveryLedger 统一接入所有修复入口及 Testbench/Stub 总额度（P6）；自动生命周期、embedding/向量库、旧记忆系统清理、目录晋升和真实 Hidden 评测反馈修复。

## 9. 已获认可的设计与下一步

以下合同已随 v1.1 获用户认可，保留作实施依据；本轮不重新逐项提问：

1. `--record-diagnostic-cases` 已按确认名称实现并默认关闭；P4 仍需实现独立、默认关闭的 `--diagnostic-assist`。
2. P4 修正 `derive_experiences()` 的负向准入；弱关联失败仍只留在 Case/Attempt，不进入检索。
3. Hidden-generation 的边界：每个允许的资格修复前仍执行 visibility-aware 查询；只有明确标记可跨该视图使用的 agent-safe 公共经验可以进入生成器，查询失败/无经验时为空；不持久化 Hidden query 或源码。正式 Hidden 评测反馈一律不进入该模块。
4. 默认模型 `deepseek-v4-flash` 必须有可验证的调用前 tokenizer/context-window 计数支持；只有未登记的自定义模型才允许跳过经验注入，同时继续原修复流程。若默认模型找不到可信计数来源，先提出替代方案，不以“fail closed”直接结案。
5. 10 月 7–12 日运行目录仅用于审计和脱敏回放 fixture，不自动把旧运行结果回填成正式经验。

P4 已按获批清单实施并完成冻结验收；后续转入 P5 的专项证据与本地采集器计划。P5/P6 仍须分别提供具体计划并经用户评审，不由本计划授予实施权限。

## 10. P4 实施与验收记录（2026-10-16）

P4 按用户审阅认可的逐文件清单在 `/data/AgRefactor` 实施。运行时检索、完整提示预算注入、Public Testbench/Stub 生成路径和受引用约束的 AI 诊断均接入现有组件；两个功能开关仍默认关闭。修复前检索只影响当前规则已允许动作的实现参考，不改变 owner、修复资格、组件额度或验证合同。

验收中修复了一个首次落盘缺少 `visibility` 的 Case 事件问题：`CaseRecorder` 现在在持久化前为 agent-safe 公共事件补齐可见性，保留 `public_generation` 语义，并拒绝 Hidden 或非公共事件。单项回归及 Hidden/非公共边界测试均通过。

冻结验收结果：

- R1：35 个既有模块，594 项通过；原 shadow 夹具失败已通过合格/拒绝输入两类测试覆盖修正。
- R2：5 个模块，42 项通过。
- N1：6 个新增模块，30 项通过。
- D1：4 个固定离线回放场景通过。manifest SHA-256 为 `bbc003352fb41153d83ad009a4af098c4a744cbef098dcf0a1a613ba508825b0`；输出 SHA-256 为 `8bc95441f2e11bd6e28a7dee7421e690b8d1b0eb0d2dec665f4facca4f346b58`。输入为合成回放 fixture；历史运行目录仅提供已审计的来源元数据，不被伪装成正式经验。
- T1 离线：DeepSeek V4 Flash tokenizer 从固定缓存加载，12 个样例计数通过；官方来源 manifest SHA-256 为 `ff997703414afb9a28498887c70a3d88a8194a3f4da028f2ffe1628c68572e1d`，tokenizer 与配置文件哈希记录在该 manifest。
- T1 在线 usage 对照：此前已完成 12 个合成 Chat Completions 请求，本次未重复。服务端与本地 prompt token 差值为 +2 至 +3，安全余量为 3；结果不是“所有请求精确同数”的声明。输出 SHA-256 为 `bfc1439bf561d8a40f393e60b8a2bba34cf90f424f6049992d9f05e190ba02cc`。未下载模型权重。
- S1：AST 检查解析 34 个变更/新增 Python 文件，`git diff --check` 通过。R1/R2/N1 均未调用外部模型或真实 Vitis；没有执行真实 HLS 案例。

服务器仍位于分支 `backup/server-current-20261013`、冻结 HEAD `e740f83cd7f39cbec952ef88bdb0726962b29397`；本次没有 commit 或 push。P4 阶段备份和 Case 可见性补丁前文件保留在服务器 `backups/` 下。
