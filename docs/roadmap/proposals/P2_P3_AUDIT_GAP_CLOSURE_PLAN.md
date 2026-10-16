# P2/P3 四项审计问题修正计划

> - 版本：评审稿 v0.1
> - 日期：2026-10-16
> - 状态：已实施并完成服务器文档同步与复核；提交/推送按后续单独授权执行
> - 代码基线：服务器 `/data/AgRefactor`，分支 `backup/server-current-20261013`，HEAD `35bbead8ac4059d00c776d6564140800463bd02c`
> - 上位设计：`docs/roadmap/后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md`
> - 相关计划：P2 实施计划、P3 主计划、P3 补充计划、P4 实施计划

## 1. 目标与边界

本计划只关闭审计中确认的四项问题：案例/经验开关默认关闭、Candidate 经验库使用共享运行目录、P2 本地采集器清单的阶段归属、P2 测试记录的可追溯性。代码和定向验证已完成；本次同步只修正文档事实与交叉引用。

P2 的 Case/Attempt/Experience 数据合同与额度 API、P3 的 Case 捕获和经验整理主体不重做。经验检索、AI 辅助诊断、目录专项检查实现、统一 RecoveryLedger 生产接线仍分别属于后续 P4/P5/P6，不在本计划内。现有 Case/Experience 数据不删除；本计划实施范围不要求提交或推送代码，后续版本归档需另行授权。

P4 计划已把前两项列在 P4-A。为避免重复，本计划将它们作为 P4 检索实现前的独立基础修正包；P4 的其他部分保持原计划状态。

## 2. 当前事实

| 项目 | 已核实事实 | 影响 |
|---|---|---|
| 案例记录开关 | v0.3 第 107–116 行规定案例/经验整理与 AI 辅助诊断是两个独立开关组，均默认关闭。`repair_phase.py` 当前无条件创建 `CaseRecorder`，并在修复阶段结束时调用 `capture_candidate_recovery`。 | Candidate 诊断满足条件时可能在没有显式启用记录功能的情况下写 Case/Experience。 |
| 共享经验库 | P3 约定 ExperienceStore 位于 `${AGREFACTOR_RUN_DIR}/diagnostic_experiences`。服务器环境脚本实际导出 `RUN_DIR`；Candidate 路径读取 `AGREFACTOR_RUN_DIR`，未设置时回退到阶段工件目录。Public 生成路径已使用 `RUN_DIR/diagnostic_experiences`。 | Candidate 与 Public 生成经验可能进入不同位置，后续跨案例检索无法稳定读取 Candidate 经验。 |
| 采集器清单 | v0.3 的 P2 条目写有“首批采集器清单”；获批 P2 详细计划将 P2 限定为数据合同和额度 API，并明确不接入目录专项检查。P5 已负责本地采集器与目录诊断焦点接入。 | 属于阶段归属和文档措辞不一致，不是已发现的 P2 数据合同代码故障。 |
| P2 测试记录 | P2 计划列出的命令包含 5 个模块，但执行结果写为 7 个模块、87 项。按该计划当前列出的 5 个模块复跑为 71 项通过。 | 87 项无法从当前计划命令直接复现；需要找到原始 7 模块清单，或明确标成历史记录并追加可复现的当前结果。 |

代码依据：`agrefactor/runtime/repair_phase.py` 的 `_experience_root_for_artifact`、`capture_candidate_recovery` 调用及 `CaseRecorder` 构造；`agrefactor/evidence/case_capture.py` 的经验目录回退；`flow/new.py` 的 Public generation recorder。服务器命令均按项目约定先 source `/data/agrefactorpp_env.sh`。

## 3. 建议实施方案

### 3.1 案例与经验整理开关

沿用现有 P4 计划建议的 `--record-diagnostic-cases`，默认值为 `false`。该开关只控制 P3 的 Case 记录与结案后经验整理，不承载 AI 诊断或检索功能；`--diagnostic-assist` 仍是独立的 P4 开关，本计划不实现它。

开关关闭时，Candidate、正式 Public Testbench 和 Public `tb_optimizer` 路径不得创建新 Case 或 ExperienceStore 记录；已有记录不删除、不重写。开关打开时，继续使用现有 agent-safe 投影、授权快照、额度事件和 Hidden 边界，并按现有 Case 生命周期整理经验。Hidden-generation 不接入跨案例记录器。

### 3.2 Candidate 与 Public generation 共用经验根目录

统一从项目环境提供的 `RUN_DIR` 派生 `RUN_DIR/diagnostic_experiences`，复用 Public generation 已有目录约定，不增加新的环境变量或存储服务。Candidate 的 Case 工件仍保存在当前运行工件目录；只将经验记录与改动片段写入共享 ExperienceStore。

产品运行必须验证 Case 工件属于配置的 `RUN_DIR`。若产品配置缺失或工件不在该运行根目录内，不应静默写到单次阶段工件旁；应跳过经验整理并留下不含源码/Hidden 内容的本地原因记录。单元测试可显式传入临时 run root。

### 3.3 明确采集器清单归属

保留 P2 已完成的 `EvidenceCheckResult` 三态数据合同。将“首批具体本地采集器清单”从 P2 完成条件移到 P5 的计划和验收范围；P5 在盘点现有 parser/报告读取器后，逐项登记可复用采集器及其输入、输出和路径约束。

只对 v0.3 与 P2 计划增加阶段归属说明，不改写 P2 已批准的历史范围，不在本修正包中实现 collector registry 或目录专项检查。

### 3.4 对齐 P2 测试记录

先查找 2026-10-15 P2 执行时的原始模块清单和命令记录，检查服务器审计/测试日志、P2 实施对话记录及提交附近的验证材料。

- 若找到原始 7 个模块，按原命令复跑，并同时记录原始结果与本次复跑结果、代码提交和完整模块清单。
- 未找到原始 7 模块清单，因此保留“当时报告 87 项”的历史记载，并明确标注为缺少可复现模块清单；P2 计划中的可复现命令更新为当前 5 模块、71 项通过。当前 71 项不冒充对原 87 项的复现。P3 补充计划原记录 227 项；同一 12 模块在本修正后的复跑结果为 230 项。

## 4. 预计改动范围

代码范围限于 P4-A 已盘点的现有入口与测试：

- `agrefactor/cli.py`、`agrefactor/product/source_bootstrap.py`：添加/传递默认关闭的 Case 记录配置。
- `agrefactor/runtime/repair_phase.py`：仅在记录开关启用时创建 Candidate `CaseRecorder`；从 `RUN_DIR` 计算共享 ExperienceStore 根目录。
- `agrefactor/evidence/case_capture.py`：确保产品调用使用传入的共享根目录，避免 Candidate 静默回退到阶段级目录。
- `flow/new.py`：使 Public generation recorder 服从同一 Case 记录开关，并继续使用现有 `RUN_DIR`。
- 相关 CLI、source bootstrap、RepairPhase、Case capture 和 Public-generation 测试：覆盖默认关闭、显式开启、统一目录和 Hidden 隔离。
- `/data/AgRefactor/docs/roadmap/后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md`、`P2实施计划_案例证据与组件额度合同.md`：记录采集器清单移至 P5，并校准 P2 测试记录。
- P3/P4 计划只追加实际修正状态和验证引用，不改写已获批的其余范围。P4 的检索、AI、排序、提示注入部分保持未实施。

开始修改前仍需按服务器当前 HEAD 查看实际差异，并检查所有目标文件；不触碰工作树中既有的未跟踪 `.orig/.rej` 文件、运行产物、备份或 P4 计划文件之外的无关内容。

## 5. 验收条件

1. 默认配置下两个开关均为关闭；没有新 Case、Experience、历史检索或 AI provider 调用，既有修复结果和权限行为不变。
2. 只打开 `--record-diagnostic-cases` 时，Candidate 和 Public generation 可记录 agent-safe Case，并按原规则幂等整理；不触发经验检索或 AI 诊断。
3. Candidate 与 Public generation 的新 ExperienceRecord 和改动片段进入同一个 `RUN_DIR/diagnostic_experiences`；Case 原始工件仍在对应运行目录。
4. 正式 Hidden evaluation 和 Hidden-generation 内容仍不能进入跨案例 Case/ExperienceStore；已有 Hidden 权限与修复行为没有放宽。
5. v0.3 明确 P2 已完成的合同范围与 P5 采集器清单的归属；P2 测试结果有可复现模块清单，历史 87 项记录不被错误替换或冒充复现。
6. 新增开关/目录测试通过；P2 当前列出的合同测试集和 P3 补充计划的 12 模块回归通过；`git diff --check` 通过。此计划不要求真实 LLM 或 Vitis 运行。

服务器验证的每条命令均须在同一 shell 先执行：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
```

P2 当前计划列出的五模块复跑结果为 71 项通过；P3 补充计划 12 模块修正后结果为 230 项通过（227 项为修正前记录）。这些均为定向测试，不将其称为真实 HLS 案例基线。完整模块清单、`git diff --check` 和工作树状态在服务器同步后复核。

## 6. 执行结果与停止点

1. 已按服务器现状确认原始 7 模块清单无法恢复，并保留 87 项作为历史记录；P2 计划改记当前 5 模块、71 项可复跑结果。
2. 已实现默认关闭的 `--record-diagnostic-cases`，Candidate/Public generation 共用 `RUN_DIR/diagnostic_experiences`；未实现检索或 AI advisor。
3. 已按相同 12 个模块复跑 P3 补充回归，230 项通过；P3 原 227 项作为修正前记录保留。
4. 已同步 v0.3、P2、P3 主/补充计划及 P4 计划的状态与交叉引用；`git diff --check` 和工作树复核待服务器文档写入后完成。
5. 本计划到此停止；后续经验检索/AI、目录专项采集器和统一账本接线仍需各自计划和用户授权。

本计划的执行不代表 P4 其余部分获准实施。
