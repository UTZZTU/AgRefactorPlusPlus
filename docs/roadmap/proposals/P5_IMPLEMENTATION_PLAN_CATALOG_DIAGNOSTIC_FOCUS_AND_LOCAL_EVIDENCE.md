# P5 实施计划：目录诊断焦点与本地证据补查

- 版本：实施记录 v1.1（基于评审稿 v1.0）
- 日期：2026-10-16
- 状态：已按审阅通过的范围实施；冻结验收通过（2026-10-16）
- 代码服务器：`/data/AgRefactor`
- 分支：`backup/server-current-20261013`
- 计划基线：`1790901`（P4 已提交并推送）
- 上位计划：[推进计划 v0.3](../后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md)
- 前置成果：[P4 实施计划](P4_EXPERIENCE_RETRIEVAL_AND_AI_DIAGNOSTIC_ASSISTANT_PLAN.md)、[P4 逐文件清单](P4_IMPLEMENTATION_CHECKLIST_AND_FROZEN_VALIDATION.md)

## 1. P5 要解决的问题

当前 CSYNTH 解析器能把已知 HLS 错误映射到 `diagnostic_id` 和类别，目录也保存了证据要求和允许动作；但生产路由没有消费这些字段来选择针对性的证据检查。当前 `extract_top_type_contract()` 已在 preflight 中使用，却还没有连接到 CSYNTH 的目录命中路径。

P5 以 `HLS 214-298` 为唯一试点，完成一条可验证链路：当前运行命中的目录规则选择一个本地、只读的 libclang 检查；检查结果成为当前诊断上下文，并在案例记录开启时写入既有 `EvidenceCheckResult`；后续仍交给现有 owner、资格、额度和验证逻辑。P5 不以目录结果判定 owner 或授予修复权限。

**完成后的行为：**

- 已知 `HLS 214-298` 命中时，系统核验本次 CSYNTH top 参数关联类型是否确有指针成员，向诊断和修复提示提供这个事实。
- 检查为 `missing` 或 `contradicted` 时，不套用该规则的专项诊断重点，回到既有基础证据和 P4 AI 辅助路径。
- 未命中目录时不运行这项专项检查，原有基础证据、owner/资格路径和 P4 行为照常工作。
- 证据不来自当前运行、来源身份不完整或编译上下文不可验证时，检查结果为 `missing`；实现不得通过扫描别的目录、运行记录或源码来猜补。

## 2. 代码与案例依据

| 依据 | 已核实事实 | 对 P5 的影响 |
|---|---|---|
| `configs/diagnostic_catalog.json` 的 `hls_214_298_struct_pointer_argument` | 规则有 HLS code、消息模式、类别、摘要、owner policy 和优先级；没有专项检查 ID。 | 只给这条规则增加一个受校验的 `diagnostic_checks` 字段。 |
| `agrefactor/evaluation/csynth_diagnostics.py` | `CsynthDiagnosticParser` 已把匹配到的 `diagnostic_id`、`evidence_requirements` 和 `allowed_actions` 放入结构化反馈。 | 复用解析器的匹配结果，不另写日志匹配器。 |
| `agrefactor/evaluation/csynth_artifact_feedback.py`、`flow/tools/csynth.py` | 当前 evaluator 读取 invocation/log；invocation 记录 top kernel、compile units、source files、source provenance 和 SHA-256，并保存 target profile/toolchain。 | 复用当前运行证据；若 invocation 未显式保留 libclang 所需的有效编译器/include roots，则在本阶段补记最小、可复现的 parse context，不从任意进程环境猜路径。 |
| `agrefactor/cpp_interface.py:1209` | `extract_top_type_contract()` 复用 libclang，可返回完整性、类型布局、字段层级和类型来源；目前输出汇总函数可达类型，没有明确逐个 top 参数的根类型关联。 | 需要一个最小的输出扩展，让专项检查能证明 pointer member 属于哪个 top 参数类型，避免把返回类型或无关类型误当命中。 |
| `agrefactor/evaluation/staged_preflight.py:1005` | preflight 已使用 `extract_top_type_contract()` 并保存实际类型合同。 | 复用解析能力、类型合同和测试模式，不复制一套 C++ 解析器。 |
| `agrefactor/evidence/case_record.py` | `EvidenceCheckResult` 已支持 `satisfied`、`missing`、`contradicted` 三态，`DiagnosticCase.evidence_checks` 可保存结果。 | 复用 P2 合同，不另建 P5 结果格式。 |
| `agrefactor/runtime/candidate_repair_integration.py` / P4 | P4 已有证据驱动的 AI 诊断和提示上下文；AI 输出只建议，不改 owner/权限，也不执行命令。 | 将本地检查摘要作为带引用的当前证据提供给既有路径；P5 不增加 AI 路由或工具调用面。 |

### 近期运行证据

优先查看了服务器 `/data/agrefactor_runs` 中 2026-10-07 至 2026-10-16 的运行资料，最近且与本试点相关的是 2026-10-12 的 `contract_observation_repair_20261012` 批次：

- `work_break_ii_evidence_audit.json` 记录 ABI qualification 第一轮出现 `HLS 200-1715` 与 `HLS 214-298`，当时 `failure_owner` 为 `unknown`；一次 ABI replacement 后第二轮状态为 `ok`。该文件是事后审计摘要，`work_break_ii` 目录没有原始 `csynth_invocation.json`、`solution.log` 或 `diagnostic_events.json`，因此只能作为错误类别和“unknown 后可继续资格诊断”的历史案例说明，不能当作 P5 原始日志回放输入。
- `remove_invalid_parentheses_evidence_audit.json` 记录 Public ABI 初始资格遇到 `HLS 214-298`，`std::string` top 参数含 pointer，现有资格逻辑要求改为 disaggregated fixed-array declaration。它同样是审计摘要，不证明旧流程已执行 libclang 专项目录检查。
- `av1_compound_type_rd/bootstrap/public_testbench_repair/attempt_00/candidate_type_contract.json` 是同批次真实工件，记录 `authority=public_candidate_compiler_type_facts`、完整翻译单元、confirmed 类型合同和编译/include 上下文。它证明 P5 可复用的 libclang 类型合同已经在项目运行中生成；该候选接口已 disaggregate pointer-bearing 顶层结构，不能当作“含指针成员”的正例。

因此，P5 使用这批审计资料校验场景选择和边界；真实规则命中解析使用明确标记来源的脱敏日志 fixture；类型正例、矛盾例和缺失例使用局部 libclang 测试源码。不得把摘要文件包装成原始运行证据，也不要求为 P5 重新运行 HLS 案例。

## 3. P5 诊断合同

### 目录命中

`CsynthDiagnosticParser` 必须在当前 Public CSYNTH 报告中匹配到 `diagnostic_id=hls_214_298_struct_pointer_argument`，代码与已审核消息模式均符合，且当前 execution/log identity 已由现有 evaluator 核验，才可读取规则上的检查 ID。仅有 `HLS 214-298` 数字但消息模式不符、其他阶段同名文字、旧工件或运行身份不完整，均不得启动专项检查。

建议给该规则配置一个固定 ID：`csynth_top_argument_pointer_member_contract`。它是本地代码中的唯一 allowlisted 检查；P5 不建设可加载插件、任意文件读取或通用 shell 命令框架。

### 检查输入范围

检查器只接收当前 `CsynthValidationStageHandler` 的 `work_dir` 和 operator report，并按以下来源取输入：

1. 当前 `csynth_invocation.json` 中的 `top_kernel`、`compile_units`、`source_files`、`source_provenance`、`target_profile`、toolchain identity 和当前工件身份。
2. 一个唯一且 role 为 `candidate` 的 compile unit。相对路径必须留在当前运行目录；源码不是 symlink；路径与 provenance 一致；SHA-256 与本次 invocation provenance 相同。
3. 本次运行记录的编译参数、compiler identity 和明确 include roots。若 invocation 当前未分别记录 libclang 所需的 effective parse context，P5 在 `flow/tools/csynth.py` 从当前 `TargetProfile`、已解析 Vitis 安装和 source package 生成可审计字段；只记录诊断所需的值，不枚举环境变量。libclang 仅解析该翻译单元实际包含且属于当前 compile context 的依赖，不遍历目录。源码或用户头文件无法与当前 source package/provenance 对应时，结果为 `missing`。
4. `top_kernel` 直接来自当前 invocation。缺失、冲突、重载歧义或无法唯一解析时，不猜测函数名。

不得读取项目全仓、工作目录其他案例、旧运行目录、Hidden 评测内容、未登记路径或网络内容。检查本身只读，不运行编译器命令，不安装工具，不写源码。

### 三态结果

P5 复用 `EvidenceCheckResult` 的状态，并固定 `check_id`：

| 结果 | 条件 | 后续行为 |
|---|---|---|
| `satisfied` | 当前 invocation、Candidate compile unit 和编译上下文均可信；libclang translation unit 完整；可将该 top 参数关联到其 aggregate 类型；该参数类型含 pointer member，且字段路径可指出。 | 将类型/字段事实及当前证据引用加入诊断上下文；仍由既有 owner 和 repair eligibility 决定路线。 |
| `contradicted` | 当前源码和上下文完整、top 参数集合唯一，libclang 完整解析相关 aggregate 后确认没有 pointer member。 | 说明目录摘要与本次类型合同不一致；不执行该规则专项策略，退回基础证据分析，必要时使用现有 AI 辅助。 |
| `missing` | invocation、源码哈希、provenance、compile unit、include 来源、top 函数或 libclang 结果缺失/不完整/歧义。 | 不猜补、不扩大读取范围；退回基础证据分析，必要时使用现有 AI 辅助。 |

`satisfied` 仅证明 HLS 214-298 所述接口形态在当前 Candidate 中存在，不表示 `FeedbackOwner.CANDIDATE` 已确定。检查结果不能改 owner、`repair_eligible`、`public_csynth_bounded_trial` 条件、RecoveryLedger、修复额度或验证合同。

### 返回主流程与 Case

- 检查在 `CsynthValidationStageHandler` 拿到已验证的 operator report 后、生成 agent-safe report 前运行；其结果作为有限、非阻塞诊断证据附加到同一诊断项。不得新增 blocking feedback item，避免改变 `FeedbackRouter` 的路线选择。
- Safe projection 只保留 `check_id`、三态、类型/字段短摘要和不含绝对路径的证据引用；完整源码、绝对路径、完整 libclang dump 留在当前运行工件，不进入提示词或跨案例存储。
- P4 AI 和 repair prompt 可看到 `satisfied` 的类型/字段事实及引用；`missing/contradicted` 只携带状态和原因码，避免把猜测包装成证据。AI 仍只给建议，不能发起采集。
- 案例记录开关开启时，用已有 `CaseRecorder` 将 `EvidenceCheckResult` 追加到当前 `DiagnosticCase.evidence_checks`，其 `evidence_refs` 指向本次诊断事件/来源工件的稳定引用。记录开关关闭时不写共享 Case/Experience Store，但本次运行仍可把检查结果用于当前诊断。
- 经验检索开关、原有 `--diagnostic-assist` 和 `--record-diagnostic-cases` 语义不改变。这个确定性只读检查不需要额外开关。

## 4. 实施文件与步骤

| 顺序 | 文件 | 改动范围 |
|---|---|---|
| P5-A | `configs/diagnostic_catalog.json`、`agrefactor/evaluation/diagnostic_catalog.py` | 给 `DiagnosticRule` 增加可选 `diagnostic_checks`；严格校验 ID、去重和已登记 allowlist；仅 214-298 规则声明该 ID。禁止规则字段直接设置 owner 或修复路由。 |
| P5-B | `agrefactor/cpp_interface.py`、`tests/test_cpp_interface.py` | 为 `extract_top_type_contract()` 增加每个 top 参数的类型根、层级和可追溯 pointer-member 路径；保持现有返回字段向后兼容。只扩展 libclang 已有 AST 输出，不引入 tree-sitter/pycparser。 |
| P5-C | 新增 `agrefactor/evaluation/catalog_diagnostic_checks.py` | 实现单一固定检查及三态输出；接受已验证的 invocation/source context；不提供任意路径 API，不做目录遍历。 |
| P5-D | `flow/tools/csynth.py`、`agrefactor/evaluation/csynth_artifact_feedback.py`、`agrefactor/runtime/csynth_stage.py`、`agrefactor/evaluation/csynth_feedback_view.py` | 从当前执行合同保存或读取最小有效 parse context；在 operator→safe projection 之间运行检查；复用现有 artifact/source hash 核验；将允许的有限结果加入当前反馈。路由仍使用原来的 blocking/category/owner。 |
| P5-E | `agrefactor/evidence/case_capture.py` 及对应测试 | 增加最窄的 `EvidenceCheckResult` 记录入口，追加到当前 Case；不改 Case 边界、Experience 派生条件或共享经验开关。 |
| P5-F | 新增 `tests/test_catalog_diagnostic_checks.py`、`tests/test_csynth_catalog_diagnostic_plan.py`，并扩展目录、CSYNTH、safe projection、Case 测试 | 覆盖命中、未命中、三态、路径/provenance 拒绝、提示上下文、路由不变、案例持久化和 Hidden 隔离。 |

执行顺序是先完成目录字段和 libclang 参数关联单测，再完成只读检查器及三态测试，随后接到 CSYNTH operator→safe-view 边界，最后接入 Case 留痕并冻结全部验收。每步均可在 mock/frozen fixture 下独立验证，不运行真实 HLS。

## 5. 冻结验收

服务器命令必须在同一个 shell 先加载环境：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_diagnostic_catalog \
  tests.test_csynth_diagnostic_parser \
  tests.test_csynth_artifact_feedback \
  tests.test_csynth_feedback_view \
  tests.test_catalog_diagnostic_projection \
  tests.test_cpp_interface \
  tests.test_case_record \
  tests.test_case_capture \
  tests.test_catalog_diagnostic_checks \
  tests.test_csynth_catalog_diagnostic_plan
python -m compileall -q agrefactor/evaluation agrefactor/evidence agrefactor/runtime agrefactor/cpp_interface.py
git diff --check
```

验收需记录测试基线 commit、每个模块实际通过数、脱敏 fixture/source hash、三态数量、未命中时 P4 fallback 仍可用、`FeedbackRouter` 输入/输出不变，以及 Case 记录开关开/关两种结果。验收不以真实 HLS 案例数作指标。

### 冻结场景

1. Public CSYNTH 的精确 HLS 214-298 匹配，当前 Candidate top 参数的 aggregate 含指针字段：返回 `satisfied` 并引用当前类型字段证据。
2. 完整类型合同显示 top 参数没有指针成员：返回 `contradicted`，不生成专用反馈，不更改 route。
3. invocation、源码 provenance/hash、include 来源、top 声明或 libclang 结果不完整：返回 `missing`，不读其他运行目录。
4. 未命中目录、HLS code 相同但消息模式不匹配、非 CSYNTH/Hidden 视图：不运行该检查；基础诊断与既有边界保持。
5. `satisfied` 时 owner 仍为 unknown：检查事实进入诊断提示，但 `FeedbackOwner`、资格及修复权不变。
6. `satisfied`/`missing`/`contradicted` 均不新增 blocking item；原 `FeedbackRouter` action 与 repair budget 不变。
7. 案例记录开启时写入同一个 Case 的 `EvidenceCheckResult`；关闭时不写共享案例库；Hidden 内容永不进入 Case、经验库或 Candidate prompt。

## 6. 风险、回滚与不做事项

- **参数关联误报：** 不能只在函数所有可达类型中搜索 `pointer`；结果必须能指到具体 top 参数和字段路径。关联无法证明就标 `missing`。
- **不完整编译上下文：** libclang 有诊断、include 未解析、宏/类型歧义时不能给 `satisfied/contradicted`；按 `missing` 回退。
- **目录越权：** 规则只选择检查 ID，不设置 owner/route；检查输出只补充诊断上下文，不增加权限。
- **路径/旧工件串用：** 当前 invocation、报告、源码哈希或 compile unit 任一不一致都 fail closed，不按 basename 寻找替代文件。
- **回滚：** 移除 CSYNTH handler 对检查器的调用即可回到 P4；目录匹配和旧修复路线仍工作。P5 不改现有 Case/Experience 数据含义，不清理历史记录。
- **明确不做：** 不扩展到其他目录规则或 CSIM/COSIM 错误码匹配；不做通用全仓扫描器；不让 AI 生成或执行 shell；不改 owner/repair eligibility/budget/validation；不处理 P6 统一额度；不跑真实 HLS 试验；不改 P4 检索算法或提示词预算。

## 7. 完成标准与后续

P5 只有在精确目录命中能够触发本次运行绑定的专项检查，三态结果安全回退，确认事实进入当前诊断上下文和可选 Case 留痕，同时所有既有 owner/资格/额度/验证路径与 Hidden 边界通过冻结验收时才算完成。

P5 已按获批范围完成。下一步准备 P6「统一修复额度账本接线」实施计划；P5 获批不代表 P6 获得实施授权。

## 8. 实施结果与验收（2026-10-16）

- 在 `backup/server-current-20261013`、基线 `1790901` 上完成目录规则 allowlist、top 参数到指针字段路径的 libclang 合同、固定专项检查器、CSYNTH 诊断上下文接线，以及可选的 `EvidenceCheckResult` 案例留痕。
- 冻结验收命令运行 10 个测试模块，共 **134 项通过**；`compileall`、`flow/tools/csynth.py` 的 `py_compile` 和 `git diff --check` 均通过。验收覆盖规则命中、三态、未命中/不适用回退、owner 与路由不变、案例记录开关和 Hidden 隔离。
- 另核对两项 Preflight 语法错误断言：`test_testbench_failure_routes_repair` 与 `test_candidate_failure_routes_candidate` 当前均得到 `unknown`，而测试预期 `syntax_error`。将同样两项测试在 P5 基线 `1790901` 的干净导出中复跑，结果完全相同；因此这是 P5 前已有的测试/行为差异，不属于本阶段回归，本阶段未改动该流程。

## 后置修正：Preflight 与 CSIM 编译诊断缺口（2026-10-17）

P5 冻结后，按获批的 v1.1 合并修复计划补齐了三个独立缺口：

1. compile expected-token 窄规则将明确的编译器语法错误分类为 `syntax_error`，未知 compile 错误继续为 `unknown`。
2. 普通 host CSIM invocation 记录并校验 `source_roles`、`compile_units`、`work_dir`、执行状态和源文件 SHA-256；证据缺失、混合、越界或不一致时保持 `unknown`。
3. 完整 provenance 且唯一归责为 Candidate 的 Public CSIM `COMPILE` 反馈接入现有 Candidate 修复循环，并同步放行对应 Public CSIM prompt stage；Unknown、Hidden 和 Testbench 权限边界不变。

三项新增定向回归共 135 项通过；P5 原冻结 134 项仍全部通过。该后置修正不改写 P5 原始 134 项结果，也不构成真实 HLS 端到端验收。
- 未运行真实 HLS 案例。服务器代码和文档尚未提交或推送；工作树中的其他既有改动与未跟踪文件均保留。
