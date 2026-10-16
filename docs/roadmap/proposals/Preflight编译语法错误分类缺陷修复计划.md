# Preflight 与 CSIM 编译诊断缺陷合并修复计划

- 版本：评审稿 v1.1
- 日期：2026-10-17
- 状态：已按审阅通过的 v1.1 范围实施；三项定向验收与 P5 冻结回归通过（2026-10-17）；已提交并推送
- 代码服务器：`/data/AgRefactor`
- 分支：`backup/server-current-20261013`
- 实施提交：`147727f`（`v1.1_diagnostic_routing_P5`），已推送至 `origin/backup/server-current-20261013`
- 实施前基线：`1790901`；服务器仍保留未跟踪备份、运行产物和其他历史文件，未被清理
- 已确认与本计划文件重叠的未提交改动：`configs/diagnostic_catalog.json`、`tests/test_diagnostic_catalog.py`；实施前先检查并在其现状上增量修改，不得恢复或覆盖 P5 内容
- 关联路线：[推进计划 v0.3](后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md)
- 关联阶段：[P5 实施记录](P5实施计划_目录诊断焦点与本地证据补查.md)

## 1. 修复目标与边界

将两个彼此独立、但都属于编译诊断链的既有缺口放进同一修复计划，分别实现、分别验收：

1. **Preflight 分类缺口：** 编译器明确报告 expected-token 语法错误时，分类为 `syntax_error`；无法确认类别的编译错误继续为 `unknown`。
2. **CSIM fallback 归责缺口：** 普通 host-differential CSIM 的 `tb_compile_failed` 分支，只有在本次编译来源、编译单元、工作目录和执行状态可核验时，才解析 Candidate、Testbench 等 owner；证据不全仍为 `unknown`。
3. **已有修复路由接线：** 当 Public CSIM 编译错误被完整证据唯一归到 Candidate 时，允许现有 Candidate 修复循环接收该 `COMPILE` 阶段反馈。仍使用已有 Public CSIM 权限、预算和验证流程；不新增未知 owner 试修、不降低 owner 证据门槛。

两项诊断缺口可以共用一次代码修复和回归周期，但不共用根因或测试断言。目录分类只决定错误类别，不决定 owner 或修复权限。owner 来自当前 invocation 的源码 provenance；修复仍需通过现有路由、policy、预算和验证合同。

本计划不扩展 CSIM/COSIM 运行错误的文本目录匹配，不改 Public CSYNTH bounded trial，不改 P5 `HLS 214-298` 检查，不统一 P6 额度，不启用或重构记忆/AI 模块，不运行真实 HLS 案例。

## 2. 已核实的代码事实

服务器命令均先执行 `source /data/agrefactorpp_env.sh`，再进入 `/data/AgRefactor`。本计划基于服务器实际代码、v0.3 文档和 P5 记录编写。

### 2.1 Preflight 语法分类

`tests/test_preflight_stage.py` 的两个既有断言要求 `FeedbackCategory.SYNTAX_ERROR`：

- `test_candidate_failure_routes_candidate`：Candidate 源码使用 `return x + ;`，g++ 报告类似 `expected primary-expression before ...`。
- `test_testbench_failure_routes_repair`：Testbench 的 `return` 语句缺少分号，g++ 报告类似 `expected ';' before '}'`。

两条反馈的 owner 已分别正确为 Candidate 和 Testbench，category 却是 `unknown`。根因是 `classify_compile_failure()` 先查询 `link` 再查询 `compile`；现有 `expected_token` 规则属于 `csynth` 阶段，compile 通用规则 `compile_syntax_error` 则以 `unknown` 作为兜底。两项测试在 P5 基线 `1790901` 和备份分支快照 `5721150` 中均以相同方式失败，属于 P5 之前的分类合同缺口。

### 2.2 CSIM fallback 的 owner 归属

`agrefactor/runtime/csim_stage.py` 的普通 `tb_compile_failed` 分支解析编译器诊断后仅调用 `infer_failure_owner(diagnostics)`。`agrefactor/evaluation/testbench_preflight.py` 中该函数在缺少 `source_roles` 或 `work_dir` 时直接返回 `UNKNOWN`；它还需要本次编译的 `execution`，并应限制在实际 `compile_units` 内判断。

普通 `flow/tools/csim.py::run_csim()` 已将三个主要输入写为 `testbench.cpp`、`orig_code.cpp`、`refactor_code.cpp`，并在 `csim_invocation.json` 记录 `work_dir`、`source_files` 和编译执行结果；但没有写 `source_roles`、明确的 `compile_units` 或源码 hash。`CsimStageInputs` 也将辅助源码作为 `extra_sources` 传入。

`SourcePackageSpec` 明确不按文件名或源码文本推断角色。因此辅助源码和不能核验的 include 文件不得被默认标成 Candidate 或 Testbench。没有明确角色的来源应作为 `unknown`，或者因不在 compile-unit 映射内而无法归责。

native Vitis CSIM 有另一条现存路径：读取 `compiler_failure`，核验 build manifest、源码 hash、source roles 和 compile units，再调用 `resolve_source_owner()`。本次只修普通 host-differential fallback，不重写 native Vitis 归责路径。

### 2.3 owner 修复后的下游合同

- `FeedbackRouter` 对 Candidate 反馈要求 `owner_authority`、`evidence_complete` 和 `tool_launched` 均满足；Testbench Public CSIM 已有对应的正式修复资格路径。
- Candidate 修复循环的 `_STAGE_BY_REPAIR_STATE` 当前允许 `ValidationState.PUBLIC_EVALUATION` 接收 `TEST/CSIM` 阶段，不接收 `COMPILE`。因此只把 owner 改成 Candidate 仍不能完成 CSIM 编译错误的现有修复路线。
- `RecoveryPolicy` 已允许 Public CSIM 的 Candidate 和 Testbench 修复。需要补的是经 owner 证据证明的 CSIM 编译反馈接入，不是新增 owner 例外或放宽 unknown-owner 权限。
- Hidden 正式评测反馈不得进入修复；Hidden 报告不得因这次接线变成 agent-visible 或 repair-eligible。

## 3. 实施设计

### 3.1 缺口 A：窄范围 compile expected-token 分类

在 `configs/diagnostic_catalog.json` 增加 compile 阶段规则，具体形式如下；若服务器已有实际 stderr 同义格式，只能依据已记录文本增补：

```json
{
  "id": "compile_expected_token",
  "match": {
    "patterns": ["\\bexpected\\b\\s+.+\\s+\\b(?:before|after|at end of input)\\b"]
  },
  "stage": "compile",
  "category": "syntax_error",
  "summary": "Compiler reports an expected-token syntax error",
  "parser_rule": "expected_token",
  "confidence": "high",
  "owner_policy": "resolve_from_evidence",
  "priority": 280
}
```

该规则覆盖已观测的 `expected primary-expression before ...` 与 `expected ';' before ...`。保留现有通用 compile `error:` 规则的 `unknown` 语义，不把所有编译失败升级为语法错误。分类结果不更改 owner。

### 3.2 缺口 B：普通 CSIM 的可核验源码 provenance

继续复用 `resolve_source_owner()`，扩展 `infer_failure_owner()` 接口以接收并传递 `compile_units`，而不是另写 owner 判定逻辑。

在普通 `flow/tools/csim.py` 生成的本次 `csim_invocation.json` 中，记录：

- 与实际工作目录对应的 `work_dir`；
- 实际编译命令中的 `compile_units`；
- 仅对运行时明确生成的主要文件登记角色：`testbench.cpp` → `testbench`、`orig_code.cpp` → `original`、`refactor_code.cpp` → `candidate`；
- `extra_sources` 不从文件名推断角色，登记为 `unknown`；不属于明确编译单元的 include/header 也不能借 basename 归属；
- 每个显式 `compile_unit` 文件的 SHA-256，供消费端验证编译单元工件未被替换或修改；未登记的 include/header 不参与 owner 推断。

在 `csim_stage.py` 增加窄范围的 host-CSIM compile-owner 读取/校验逻辑：只从本 suite 的 `csim_invocation.json` 取证，核对 invocation 路径与 `work_dir` 一致、编译确已完成且退出码非零、未超时、compile units 和 source-role map 结构有效、相关源码 hash 匹配，再把 `source_roles`、`compile_units`、`work_dir` 和 invocation 中的 `compile_execution` 传给 `infer_failure_owner()`。不能确认任一条件时返回 `unknown`。

不得用目录分类、错误文本、basename、其他 suite/run 的工件，或未登记的项目源码来补 owner。多文件/多角色诊断、辅助源码、路径越界、hash 不符、execution 未完成或超时均 fail closed 为 `unknown`。

只有当前 split 为 Public、owner 唯一为 Candidate 或 Testbench、且 provenance 校验完整时，才填入与现有确定性来源合同相符的 `owner_authority`、`evidence_complete`、`tool_launched` 和 `repair_eligible` 元数据。`original/configuration/unknown` 不获得 Candidate/Testbench 修复资格。Hidden 继续保持 operator-only，repair eligibility 为 false。

### 3.3 缺口 C：将已证明的 Candidate COMPILE 反馈接到现有 CSIM 修复状态

仅在 `ValidationState.PUBLIC_EVALUATION` 的 Candidate 修复阶段允许 `FeedbackStage.COMPILE`。该修改只让同一 Public CSIM 运行中、通过上述 provenance 验证且 owner 为 Candidate 的编译错误进入已有修复循环；Unknown、来源不明或混合来源仍无法通过 `FeedbackRouter` 与 Candidate repair 的证据门。

Testbench compile owner 继续使用现有 Public CSIM Testbench 修复路线。两者均使用当前恢复预算、额度和验证流程，不在本计划调整 `RecoveryLedger` 或任何上限。修复后照常从现有验证序列重新验证。为消费该反馈，仅同步扩展同一 Public CSIM Candidate prompt 合同的允许 stage，不重构 prompt 或修复引擎。

### 3.4 保护既有路径

- 原有 Preflight compile/link owner 判断保持不变。
- native Vitis `compiler_failure` manifest/hash 路径保持不变。
- Public unknown-owner qualification、Public CSYNTH bounded trial、P5 检查器和 Hidden 边界保持不变。
- 不因诊断 category 为 `syntax_error` 单独授予修复权限；不因历史/目录匹配改变 owner。

## 4. 预期文件变更

### 生产代码

- `configs/diagnostic_catalog.json`：新增 compile expected-token 窄规则。
- `agrefactor/evaluation/testbench_preflight.py`：为 `infer_failure_owner()` 增加可选 `compile_units` 参数并传给现有 resolver；不改变缺省参数下返回 unknown 的兼容行为。
- `flow/tools/csim.py`：为普通 CSIM invocation 写入显式编译单元、已知源码角色和源码 hash；辅助源码不猜 owner。
- `agrefactor/runtime/csim_stage.py`：校验 host invocation provenance，并用 compile execution 调用 owner resolver、填充完整 owner 证据；保持 native Vitis helper 不变。
- `agrefactor/repair/candidate_loop.py`：仅把 `FeedbackStage.COMPILE` 加入 `PUBLIC_EVALUATION` 可接受阶段；现有 Candidate owner/evidence/public split/policy/budget 门槛不放宽。
- `agrefactor/prompts/layered.py`：让同一 Public CSIM Candidate prompt 合同接受已证明的 `COMPILE` 反馈；保留 agent-safe、Candidate-only 和 Public 可见性约束。

### 测试

- `tests/test_diagnostic_catalog.py`、`tests/test_testbench_preflight.py`、`tests/test_preflight_stage.py`：覆盖 compile 分类、阶段隔离和 unknown 兜底。
- `tests/test_csim_stage.py`：覆盖 Candidate/Testbench 唯一来源、unknown/混合来源/辅助源码/超时/hash mismatch，以及 Hidden 保持 operator-only 且不具备修复资格。
- `tests/test_owner_evidence.py`：覆盖 `compile_units` 对已登记角色的限制与现有保守行为。
- `tests/test_feedback_routing.py`、`tests/test_candidate_repair_integration.py`：覆盖 Public CSIM 已证明 Candidate 的 COMPILE 反馈能走现有 Candidate 修复路线；未经证明或 Hidden 情形不能进入该路线。
- `tests/test_candidate_repair_prompts.py`：更新 Public CSIM prompt stage 合同，并保留非法 stage 的回归断言。
- native Vitis 归责现有测试仅作回归，不应要求其工件合同变化。

除上述生产文件和直接回归测试外，不改 P2/P3/P4/P5 实现，不清理服务器工作树中的其他改动、备份、`.orig`、`.rej` 或未跟踪文件。

## 5. 验收与命令

所有服务器命令必须在同一 shell 先加载环境：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
```

### 定向验收

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_diagnostic_catalog \
  tests.test_testbench_preflight \
  tests.test_preflight_stage \
  tests.test_owner_evidence \
  tests.test_csim_stage \
  tests.test_feedback_routing \
  tests.test_candidate_repair_integration \
  tests.test_p4_0c_native_vitis_csim
```

必须证明：

- 两项原 Preflight 语法断言通过，Candidate/Testbench owner 仍正确。
- expected-token compile 规则命中 `syntax_error`；普通未知 compile 错误仍为 `unknown`；link 和 CSYNTH 分类不变。
- 普通 CSIM 的 Candidate、Testbench 来源仅在当前 invocation、execution、compile-unit map 和 hash 全部吻合时被唯一归属。
- 缺少/损坏映射、超时、非完成执行、来源混合、路径越界、未登记辅助源码或 hash mismatch 都返回 `unknown`，不产生修复资格。
- 经证明的 Public Candidate/Testbench compile 反馈分别到达现有对应修复路线；Candidate 的 `COMPILE` 阶段只在 `PUBLIC_EVALUATION` 允许。
- Hidden 仍无 agent-visible 反馈和修复资格；native Vitis 路径保持现状。

### 实施结果（2026-10-17）

- 缺口 A、B、C 分别补充了独立回归断言；三项接线的定向测试共 **135 项通过**。
- 含 invocation provenance 生成合同在内的受影响模块回归共 **158 项通过**。
- P5 冻结的原 **134 项**测试仍全部通过；新增测试不改写该冻结基线。
- `compileall`、`py_compile` 和 `git diff --check` 通过；未运行真实 HLS 案例。
- 全量 `unittest` 在实施提交上运行 **3339 项：55 failures、21 errors**；基线 `1790901` 同一命令运行 **3325 项：57 failures、21 errors**。两次结果和逐项分类见 [v1.1 全量测试差异记录](v1.1全量测试差异记录_2026-10-17.md)。
- 当前 76 个未通过条目全部在 `1790901` 基线中出现，归为历史遗留；本次新增的 14 项测试全部通过，未发现当前工作树引入或 v1.1 回归。基线中的 `test_candidate_failure_routes_candidate` 与 `test_testbench_failure_routes_repair` 已在当前提交通过。
- 全量未通过项主要集中在既有 R4/R5、CLI compatibility fixture、优化 fixture 和其他旧合同；它们不属于本计划新增接线，保留到后续独立盘点，不把它们表述为 v1.1 验收失败。

### P5 冻结回归

复跑 P5 冻结的十个模块，原有 134 项断言必须全部通过；新增测试会增加总数，结果需分别报告“原冻结测试全通过”和“新增测试数量/总数”，不能再把新总数写成 134：

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
```

再运行：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
python -m compileall -q agrefactor/evaluation agrefactor/evidence agrefactor/runtime agrefactor/repair
python -m py_compile flow/tools/csim.py flow/tools/csynth.py
git diff --check
```

不得将单元测试结果表述为真实 HLS 端到端验证；本计划不要求运行真实 HLS 案例。

### 基线记录

实施记录保留以下对照：

- `1790901` 与备份分支快照 `5721150` 上，两项既有 Preflight 分类断言均失败。
- 修复后两项通过，且通用 unknown 负例仍通过。
- 普通 CSIM fallback 的缺少 provenance、owner unknown 行为是修复前基线。
- native Vitis compiler failure 已有 manifest/hash 校验，本次仅作回归。
- P5 原冻结 134 项全部通过，新增测试的增量单独列明。

## 6. 风险与控制

- **expected-token 规则过宽：** 只接收已观测或可复现的编译器格式；保留 unknown 兜底和非语法负例。
- **把文件名当 owner：** 仅对本地执行器明确生成的三个主输入赋角色；extra sources 和非编译单元不按 basename 推断。
- **陈旧或被改写的 invocation/source：** suite 路径、work_dir、execution、compile units、roles、hash 任一不匹配即 unknown。
- **目录分类越权：** category 和 owner 分开计算；catalog 不提供 owner 或权限。
- **误开放 Candidate 修复：** 必须同时满足 Public split、唯一 Candidate 来源、完整 provenance、实际工具执行及现有 Candidate loop / RecoveryPolicy 门槛；unknown 不走兜底。
- **Hidden 信息泄漏：** 不改变 operator-only 投影；Hidden 不设置 repair eligibility，不把其源码/诊断送入 Public 修复循环。
- **P5 工作树损坏：** 服务器已有未提交 P5 变更和其他用户文件；实施只碰本计划列出的文件，禁止清理/覆盖其他工作。
- **与 P5 文件重叠：** catalog 和 catalog 测试已有未提交内容；先保存其现状并做增量修改。以实施前 `git diff --check` 输出作比较，只处理本次新引入的问题。

回滚时仅撤销本次新增 catalog 规则、CSIM provenance/路由接线及相关测试，不覆盖 `backup/server-current-20261013` 上 P5 的既有修改。

## 7. 完成后的文档处理

代码实施并验收后再：

1. 将本计划状态保持为“已实施”，记录实际改动、两套全量测试计数和冻结测试通过数；实施提交 `147727f` 已推送。
2. 更新 v0.3 当前执行状态：记录 Preflight 分类缺陷与 CSIM fallback owner 缺口的实施结果，并说明它们不属于 P5 原始冻结验收。
3. 在 P5 记录中只补一条后置说明：两项 Preflight 失败断言后续已修复，不改写 P5 原先的 134 项结果；CSIM fallback 修复引用 v0.3/本计划，不将其归成 P5 功能。

不要求改写 P2/P3/P4 计划，也不要求运行真实 HLS 案例。全量历史失败项需在后续单独盘点，不作为本次 v1.1 回归处理。
