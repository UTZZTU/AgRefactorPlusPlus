# AgRefactor 通用证据、诊断目录与归责机制修复计划

状态：已完成（2026-10-05）。P0-P9 已按本计划执行；通用修复、聚焦回归、三个案例真实重跑和文档收尾均有 artifacts 证据。

执行基线：服务器 `/data/AgRefactor`，分支 `research-roadmap-v2.3`；实施代码截至 `2b543c3`，计划收尾文档提交已推送 GitHub。本轮还保留服务器既有 `.orig/.rej`、备份和历史运行 artifacts，不清理它们。

| 阶段 | 当前状态 | 已有证据 / 待完成 |
| --- | --- | --- |
| P0 | 已完成 | 工作树清单、历史 artifact 索引和正式/legacy 路径已核对 |
| P1 | 核心完成 | JSON 目录可校验，支持新增细分类映射现有 FeedbackCategory，禁止直接 owner/route |
| P2 | 核心完成 | CSYNTH、compile/link/coverage 的证据归责和历史回放已核验；未知 owner 不再被固定文本强制覆盖 |
| P3 | 核心完成 | 超时、非零退出、旧报告/旧日志、Hidden 缺覆盖证据回归通过 |
| P4 | 核心完成 | 编译单元/provenance 归责、Candidate/Original 语言 linkage 冻结和 typed execution evidence 已接入 |
| P5 | 核心完成 | 新编号和裸 ERROR 保留 unknown 事件，诊断 ID / 指纹进入现有 safe projection |
| P6 | 已完成 | `src/info.json`、继承清单、stage3 protocol 和 R5.1 registry 已统一为真实入口 `chacah20_stream`；未改源码函数名 |
| P7 | 已完成 | 聚焦回归 103 项通过；R5.1 registry/inheritance 审计无失败；历史回放 issues=0 |
| P8 | 已完成 | `chacah20_stream`、`encode_one_block` accepted；`mm_chain_dp_orig` 在 Hidden generation qualification 阶段耗尽，保持 `unknown/review_unknown` |
| P9 | 已完成 | 计划、诊断目录维护说明和三例真实运行结论已按最终 artifacts 收尾 |

本文是后续工作的主计划。它承接 `APP_FAILURE_GENERAL_MECHANISM_REPAIR_DESIGN_2026-10-03.md`，将其中的通用机制问题扩展到整个主流程的诊断解析、执行状态、责任归属和规则维护基础。后续修复和验证应按本文顺序推进；如果执行中发现新的问题，先判断它是否属于本文定义的通用机制，再决定是否扩大范围。

## 1. 目标、范围和边界

本次工作的目标是让现有 AgRefactor 流程继续使用当前的生成、Preflight、CSIM、CSYNTH、COSIM、Hidden evaluation、FeedbackRouter、ValidationStateMachine 和 bounded repair 机制，同时做到：

1. 执行状态由真实完成证据决定，日志中的单个成功标志不能覆盖 timeout、非零返回或缺少报告。
2. 诊断类别、阶段和责任归属分开保存。
3. HLS 编号或固定文本可以帮助识别类别，但不能单独决定 Candidate、Testbench 或 Toolchain owner。
4. 编译、链接、覆盖率和 Hidden 结果在组件 provenance、执行记录和差分证据不足时保持 `unknown`，但有充分证据时仍然进入原有修复循环。
5. 将声明性的诊断规则迁移到集中式、可人工编辑的目录，减少分散的 `if/elif` 规则。
6. 为未来的记忆系统保留稳定的诊断 ID、类别、阶段、证据指纹和人工提升入口，但本次不实现记忆模块。
7. 在通用机制修复后，重新运行 `chacha20_stream`、`encode_one_block` 和 `mm_chain_dp_orig`，并用真实 artifacts 判定结果。

本次明确不做以下事情：

- 不新建候选生成或验证架构；
- 不改变现有正式验证状态机的基本顺序；
- 不根据案例名、目录名、函数名或类型名添加放行、阻断或修复分支；
- 不把所有未知错误一律改成 Candidate 修复；
- 不把所有不确定性一律改成 Toolchain；
- 不为了让某个案例通过而添加单案例重写、静态绿灯或绕过测试；
- 不在本阶段实现自动记忆、经验库或模型持续学习服务。

服务器基线以 `/data/AgRefactor` 为准。实施代码提交为 `2b543c3`（已推送 GitHub）；生产代码无未提交修改；历史 `.orig/.rej`、备份与一次性运行脚本均保留。此前草稿经重新审查后才逐项采纳，不能将草稿视为已经完成。

## 2. 当前主流程和本次工作的接入点

正常 `refactor`/`full` 的正式链路仍然是：

```text
CLI
→ source eligibility / 安全上限 / credential
→ UnifiedRunner
→ SourceBootstrapPhase
→ generation-only LegacyRefactorAdapter
→ Public/Hidden 测试资源 materialize
→ Public Testbench preparation
→ CandidateRepairPhase
→ Preflight
→ Public CSIM
→ CSYNTH
→ Public COSIM
→ Hidden final evaluation
→ accepted / review / blocked / rejected
```

当前正式验证顺序必须保持为：

```text
Preflight → Public CSIM → CSYNTH → Public COSIM → Hidden
```

没有 Public 时跳过 Public CSIM 和 Public COSIM；没有 Hidden 时在最后一个公开阶段通过后结束。

正常 product flow 使用 `generation_only=True` 和 `max_retry_attempts=0` 调用旧生成后端，因此旧 `flow/new.py` 的 CSIM/CSYNTH retry 不是正常 source product 的正式修复主循环。`run --legacy` 仍然保留旧路径，必须单独审查，但不能把旧路径的静态归责继续复制到正式路径。

本次机制修复主要接入以下位置：

- `agrefactor/evaluation/csynth_diagnostics.py`
- `agrefactor/evaluation/csynth_feedback.py`
- `agrefactor/evaluation/csynth_feedback_view.py`
- `agrefactor/evaluation/testbench_preflight.py`
- `agrefactor/evaluation/staged_preflight.py`
- `agrefactor/runtime/preflight_stage.py`
- `agrefactor/runtime/csynth_stage.py`
- `agrefactor/runtime/csim_stage.py`
- `agrefactor/runtime/cosim_stage.py`
- `agrefactor/evaluation/feedback_routing.py`
- `flow/tools/csynth.py`
- `flow/tools/csim.py`
- `flow/tools/tb_coverage.py`
- `flow/tools/tb_hidden_eval.py`
- `flow/tools/testbench.py`
- `flow/tools/tb_optimizer.py`

这些文件继续复用现有的 FeedbackReport、FeedbackItem、FeedbackRouter、ValidationStateMachine、RecoveryPolicy 和 CandidateRepairLoop，不另建一条平行执行链。

## 3. 需要迁移和需要保留的逻辑

### 3.1 应迁移到集中式目录的声明性规则

以下内容适合放入集中式诊断目录：

- HLS 编号和文本模式；
- 诊断类别；
- 发生阶段；
- 规则优先级；
- 规则需要哪些证据；
- owner 应该使用哪种证据策略；
- 允许的后续动作；
- 规则说明、来源和人工备注。

例如 `HLS 214-134` 可以在目录中声明为 `unsupported_construct`，但目录不能写成 `214-134 → toolchain` 或 `214-134 → candidate`。

### 3.2 必须保留在程序代码中的机制

以下内容不是静态分类，不能只靠 JSON 配置完成：

- 读取日志、invocation、return code 和 timeout；
- 判断工具是否真正启动和完成；
- 检查报告、覆盖文件和执行身份；
- 解析编译单元、源码 provenance、符号和 ABI；
- 进行 Original/Candidate isolation；
- 校验 runtime contract；
- 根据证据解析最终 owner；
- 生成 `owner_authority`、`evidence_complete` 和 `repair_eligible`；
- 执行 FeedbackRouter、ValidationStateMachine、预算和修复循环；
- 决定最终状态是 accepted、review_required、blocked 还是 rejected。

目标结构是：

```text
集中式目录
→ 识别 category / stage / 所需证据
→ 现有执行代码收集事实
→ 统一 evidence resolver 决定 owner 和 authority
→ 现有 router/state machine 决定动作
```

## 4. 当前需要处理的静态规则风险

### 4.1 HLS 诊断规则

`csynth_diagnostics.py` 当前识别的 HLS 和固定文本类别包括：

- `214-133`：全局变量定义；
- `214-194`：动态内存分配；
- `214-298`：顶层结构体指针；
- `214-134`：二级指针；
- `214-139`：递归调用；
- `200-880`：pipeline carried dependence；
- `200-878`：loop exit scheduling；
- `214-135` 或 source synthesis failure；
- 未声明标识符、缺失 token、非法 `goto`、AXI-Lite bundle 等文本模式。

真正危险的不是识别类别，而是 `_to_item()` 中把结构体指针、二级指针和递归无条件设置为 `TOOLCHAIN`。这些类别应保留，但 owner 必须由执行和差分证据决定。

另外，`CsynthValidationStageHandler` 默认 owner 为 Candidate，可能使没有充分证据的普通 CSYNTH 错误继承 Candidate owner。该默认值必须纳入迁移和回归审查。

### 4.2 执行状态和伪成功风险

以下风险必须优先修复：

- `flow/tools/csynth.py` 中 `Finished Checking Synthesizability:` 覆盖 timeout、非零返回或缺报告；
- CSYNTH 已有 `.rpt` 就把 timeout 当成 succeeded；
- `tb_hidden_eval.py` 中返回码为 0 但缺少 `.gcda`/gcov 证据仍判 Hidden pass；
- `csim.py` 旧路径把 timeout 的 `None` return code 粗略地变成普通 `csim_failed`；
- Hidden unknown execution 被统一写成 mismatch。

修复原则是：

```text
process completion + returncode + timeout + report identity + expected artifacts
```

共同决定执行状态。日志中的“完成检查”只能作为一个事实字段，不能覆盖执行合同。

### 4.3 编译、链接和 coverage 归责风险

需要逐项审查以下规则：

- `testbench_preflight.py` 通过固定文本和 basename 推 owner；
- `staged_preflight.py` 将所有 launch error/timeout 直接归 Toolchain；
- `tb_coverage.py` 将 `undefined reference`、`multiple definition`、`collect2`、缺 gcda、gcov 失败、coverage timeout 直接映射到固定 owner；
- `testbench.py` 将 Empty Stub CSYNTH 耗尽直接标记为 Toolchain；
- `tb_optimizer.py` 在 Original 通过但运行 owner unknown 时强制选择 `repair_testbench_stub`；
- Original-only timeout/crash 的 owner 和 next_action 互相矛盾；
- 旧 legacy fixer 根据 `failed_task` 直接选择 Candidate 修复。

这些文本模式可以继续作为类别候选，但最终 owner 必须依赖组件 provenance、执行记录、符号/ABI 证据和差分结果。

## 5. Owner 和 route 的统一证据矩阵

### 5.1 Candidate

只有以下证据之一成立时，才允许 Candidate repair：

- Candidate 自身编译、符号或 ABI 错误有明确组件证据；
- Public CSIM/COSIM 有 typed candidate mismatch、合法 return code、preflight identity 和 runtime contract；
- Original isolation 通过，Candidate 出现有证据支持的异常终止；
- Candidate-only 外部符号冲突有 `nm` 或链接组件证据；
- Public unknown 满足 `public_reference_qualified`、`repair_eligible`、`evidence_complete` 和 `tool_launched`。

### 5.2 Testbench

只有以下条件成立时，才允许自动 Testbench repair：

- 失败发生在 Public Preflight、Public CSIM 或 Public COSIM；
- suite 是唯一可定位的 Public suite；
- owner 明确为 Testbench；
- provenance 是允许修改的 AUTO Public；
- 修复后通过 semantic non-weakening audit；
- 更新后完整重新验证。

Provided Public Testbench 和 Hidden Testbench不自动进入同一修复权限。

### 5.3 Toolchain / Configuration

Toolchain 或 Configuration owner 需要执行级证据，例如：

- 工具没有启动；
- Vitis 版本验证失败；
- 启动脚本或设备配置失败；
- 明确的工具进程错误；
- 缺少 source package、头文件或库依赖；
- 预算或安全上限阻断。

Vitis 报告“某种代码构造不支持”不自动等价于 Toolchain owner。若 Candidate 可以通过通用代码重写避开该构造，只有 Candidate-only 证据成立时才进入 Candidate repair；若 Original 和 Candidate 都失败，则记录为能力边界或待复核结论。

### 5.4 Unknown

以下情况保持 `owner=unknown`：

- 日志有类别但没有组件证据；
- timeout 但没有确认失败组件；
- 只有非零返回，没有 evaluated case 或 typed mismatch；
- link 错误无法映射到具体编译单元；
- toolchain、Testbench、Candidate 的证据互相矛盾；
- 新错误没有目录匹配且没有充分差分证据。

Unknown 默认进入现有 `review_unknown`，而不是自动进入 `repair_candidate`。

## 6. 集中式诊断目录设计

建议新增：

```text
configs/diagnostic_catalog.json
```

它是机器读取和人工编辑的事实来源，Markdown 只用于说明维护方式，不作为运行时规则来源。

建议结构：

```json
{
  "schema_version": 1,
  "categories": {
    "unsupported_construct": {
      "description": "工具或候选源码涉及不支持的代码构造",
      "evidence_requirements": [
        "tool_launched",
        "execution_completed"
      ],
      "allowed_actions": [
        "review_unknown",
        "repair_candidate_if_proven"
      ]
    }
  },
  "rules": [
    {
      "id": "hls_214_134",
      "match": {
        "hls_codes": ["214-134"]
      },
      "stage": "csynth",
      "category": "unsupported_construct",
      "owner_policy": "resolve_from_evidence",
      "priority": 100,
      "notes": "编号只识别类别，不直接决定 owner"
    }
  ]
}
```

人工维护时分三种情况：

1. 已有类别增加新 HLS 编号或文本模式：增加一条 rule，不改 Python。
2. 新增语义类别但复用现有阶段、owner policy 和 route：增加 category 和 rule，并增加测试。
3. 新增一种责任判断机制、修复动作或状态：需要修改代码、目录 schema 和测试，不能只添加一个类别词。

目录校验器必须拒绝以下危险写法：

- 诊断编号直接映射 `candidate` 或 `toolchain`；
- 不存在的 stage、category 或 route；
- Hidden rule 允许把反馈发送给 Candidate；
- 规则要求的证据与允许的动作不一致；
- 相同优先级下互相冲突的规则。

未来记忆系统可以引用 `diagnostic_id`、`category`、`stage`、`evidence_fingerprint` 和人工确认状态，但本次不实现记忆写入或自动学习。

## 7. 分阶段执行顺序

### P0：保存基线并盘点规则

工作内容：

- 保存服务器 commit 和 `git status --short`；
- 汇总已有 run artifacts 和历史判定；
- 全仓列出所有影响 category、owner、route、success、termination 的规则；
- 区分 active formal path、generation path、legacy path 和 dormant helper；
- 确认 `chacha20_stream` 的真实入口、linkage、source-root、依赖和案例配置。

验收：形成规则清单和案例入口清单，不修改代码、不重跑案例。

### P1：定义目录 schema 和证据字段

工作内容：

- 建立 `diagnostic_catalog.json` 的 schema；
- 明确 category、stage、owner_policy、evidence_requirements、allowed_actions；
- 复用现有 `FeedbackStage`、`FeedbackCategory`、`FeedbackOwner` 和 `FeedbackRouteAction`；
- 设计未知诊断的 fallback 记录；
- 增加目录校验器和最小单元测试。

验收：目录可以加载、校验和报告错误，但尚未改变正式路由。

### P2：迁移诊断分类规则

工作内容：

- 将 `csynth_diagnostics.py` 中的 HLS 编号和通用文本匹配迁移到目录；
- 将编译、链接和 coverage 的声明性匹配迁移到目录；
- parser 返回匹配到的 diagnostic ID、category、stage、原始文本和匹配证据；
- 删除重复的分类 `if/elif`，但暂时保留旧 owner 结果作为 shadow comparison；
- 不能把旧的静态 owner 和 success override 一起迁移。

验收：历史诊断回放时，新旧 category 结果可比较；没有案例名、函数名或目录名分支。

### P3：修复执行状态真实性

工作内容：

- 修复 CSYNTH success override；
- 将 timeout 设为一等执行事实；
- 修复 Hidden 缺少 coverage evidence 仍判通过的问题；
- 修复 Hidden unknown 被伪装成 mismatch；
- 保留 operator evidence 和 agent-safe projection 的边界；
- 为每次结果写出 completion、returncode、timeout、report identity 和 evidence completeness。

验收：伪成功 fixture 全部失败；真正通过且证据完整的 fixture 仍然通过。

### P4：统一 owner/evidence resolver

工作内容：

- 将 component provenance、invocation、符号、ABI、Original isolation 和 runtime contract 汇总到统一 resolver；
- 清除 `214-298/214-134/214-139` 的无条件 Toolchain 覆盖；
- 清除 CSYNTH 默认 Candidate 对未证实错误的影响；
- 修复 basename、固定错误文本和 coverage 缺失造成的强制 owner；
- 让证据不足的结果保持 unknown；
- 让证据充分的 Candidate/Testbench 结果继续进入原有 route 和修复循环。

验收：

- Candidate-only 证据仍可进入 Candidate repair；
- AUTO Public Testbench 证据仍可进入 Testbench repair；
- Toolchain 启动/版本/预算错误仍可阻断；
- Unknown 不会自动变成 Candidate；
- Hidden 不会进入自动修复。

### P5：统一未知错误和操作员证据

工作内容：

- 新 HLS 编号没有匹配时保留完整安全诊断；
- 保存原始文本、HLS code、stage、fingerprint、evidence completeness 和 owner authority；
- agent-safe 视图不能泄露 Hidden 源码，但不能丢掉诊断类别和证据摘要；
- operator-full 视图保留可复核的执行事实；
- 不以“没有匹配规则”为理由静默丢弃诊断。

验收：新 HLS 编号和只有 `ERROR: new failure` 的日志都能形成可审计的 unknown 事件。

### P6：处理 `chacha20_stream` 入口配置

工作内容：

- 使用现有入口检查和 preflight 证据确认实际 top、linkage 和依赖；
- 如果是案例映射错误，只修改案例配置；
- 如果是函数确实具有 internal linkage，则记录入口合同/能力结论，不伪造外部原型；
- 不增加 `chacha20_stream` 专用 parser 或 repair route。

验收：入口配置有明确证据，且不会再反复消耗无意义的 Testbench 修复次数。

### P7：回归测试和历史结果回放

必须增加或补齐以下测试：

1. CSYNTH timeout 加结束日志不能判成功；
2. CSYNTH 非零返回加结束日志不能判成功；
3. Hidden return code 为 0 但无 gcda/gcov 不能判通过；
4. 新 HLS 编号可以保留 unknown 诊断；
5. `214-134/214-139/214-298` 只产生类别，不直接覆盖 owner；
6. Original 和 Candidate 同时失败时不能自动判 Candidate；
7. Original 通过、Candidate-only 失败且证据完整时仍可 Candidate repair；
8. 缺失符号来自额外依赖时不能仅按 basename 归责；
9. launch error、timeout 和 ownership unknown 不产生互相矛盾的最终状态；
10. Hidden 失败不进入 Candidate prompt；
11. 目录中禁止直接把错误编号映射为 Candidate/Toolchain；
12. 正常已有通过 fixture 的最终状态不发生无证据变化。

回放范围至少包括此前的 DNN、AES、FFT、Matrix QR、Binary Tree、select_colors 和 app 七例中的代表性 artifacts。回放只是验证解析和归责，不替代真实工具运行。

### P8：真实重跑三个案例

只有 P0-P7 通过后才进行真实重跑。运行前使用既定环境初始化方式，例如：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
```

重跑顺序：

1. `chacha20_stream`：使用已确认的入口配置，先观察入口/preflight 结果；
2. `encode_one_block`：使用通用机制修复后的代码和原有调用合同；
3. `mm_chain_dp_orig`：使用同一套修复后的代码和原有调用合同。

三例都必须使用真实 API 和真实 Vitis 执行结果。不能在运行前为某个案例临时改 Candidate、Testbench 或 parser。每个案例单独保存：

- source/config identity；
- generation 和 formal request；
- 每个阶段的 execution facts；
- route decision；
- 修复次数和每轮历史证据；
- 最终 Candidate hash；
- 最终 accepted、review、blocked 或 rejected 结论。

如果重跑出现新问题，先回到 P2-P5 判断是否属于通用机制；只有无法形成通用修复时，才记录为能力边界、外部依赖或待复核结论。

### P9：完成文档和后续维护说明

完成后更新：

- 本文的执行状态和实际变更；
- `diagnostic_catalog.json` 维护说明；
- 新增类别的判定标准；
- stage/category/owner/route 字段说明；
- unknown 和人工 review 的处理方式；
- 三个案例的真实运行结论；
- 未修复的工具能力和外部依赖边界。

### 本轮实施记录（2026-10-04）

本轮已推送的相关提交为 `7b98c15`、`3f6653e` 和 `2b543c3`。`7b98c15` 将描述性 evidence authority 通过显式白名单归一化为现有 `RecoveryAuthority`，未知字符串保持 `unknown`，修复了 Candidate loop 将合法证据字符串直接构造为 enum 而导致的流程崩溃。`3f6653e` 完成了两项通用修复：

- ABI 提取保留 C/C++ language linkage，冻结 Candidate/Original 声明时纳入 linkage 比较；Hidden qualification 不再用丢失 `extern "C"` 的声明去验证真实 Candidate。
- CSIM adapter 在构造 `FeedbackItem` 前投影 typed execution evidence，保留 `tool_launched`、`physical_tool_launched`、`evidence_complete` 和 `repair_eligible`，因此证据充分的 Candidate CSIM 失败仍会进入原有 Candidate repair。该提交同时同步了入口配置和由 `src/info.json` 生成的 registry/inheritance 元数据。

聚焦回归共 103 项通过；R5.1 registry audit 报告 `AUDIT_FAILURES=0`，inheritance audit 报告 `FAILURES=0`。未把完整历史 `unittest discover` 的旧 fixture 失败误报成计划通过。

当前真实运行 artifacts：

- `chacah20_stream`：`/data/agrefactor_runs/general_evidence_catalog_p8_20261004_r4/chacah20_stream`。入口使用真实源函数 `chacah20_stream`；CSIM、CSYNTH、COSIM 和 Hidden 均完成，最终 `accepted=true`。预算为 CSIM 900 秒、CSYNTH 2700 秒、COSIM 4500 秒；未发生 API 或工具超时。
- `mm_chain_dp_orig`：`/data/agrefactor_runs/general_evidence_catalog_p8_20261004_r2/mm_chain_dp_orig` 在正式验证前的 Hidden generation qualification 阶段失败，`failure_kind=testbench_generation_exhausted`，`failure_owner=unknown`、`next_action=review_unknown`；没有进入 Candidate repair。该 artifact 的详细诊断保留在 operator-only 证据中，当前证据不足以归责 Candidate 或 Toolchain。较早的 `/data/agrefactor_runs/app_general_mechanism_post_p4_20261003/mm_chain_dp_orig` 曾进入 CSYNTH 并记录多条 `HLS 214-134`，最终 owner 仍为 unknown/review_required；它只能作为未解决的通用能力边界证据，不能与本次 r2 的生成阶段失败混写。
- `encode_one_block`：`/data/agrefactor_runs/evidence_catalog_p8_20261004/encode_one_block`。初始 Preflight 的 16 个 Candidate 编译错误均有 `tool_launched=true`、`evidence_complete=true`，进入既有 `repair_candidate`；第 1 次 Candidate 修复后，Public CSIM、CSYNTH、Public COSIM、Hidden 全部完成，最终 `accepted=true`。CSYNTH 的 138 条 `HLS 200-880` 仅为非阻塞 warning，保持 `owner=unknown`，没有被错误升级为修复动作。预算为 CSIM 900 秒、CSYNTH 2700 秒、COSIM 4500 秒；实际 elapsed 2374.48 秒，compile/csim/csynth/cosim/LLM/tool 调用分别为 17/6/4/1/21/34。此前 `/data/agrefactor_runs/general_evidence_catalog_p8_20261004_r2/encode_one_block` 暴露的 CSIM typed evidence 丢失问题已由通用投影修复，本次真实重跑验证了修复生效。


### P8/P9 最终收尾记录（2026-10-05）

- `chacah20_stream`：`/data/agrefactor_runs/general_evidence_catalog_p8_20261004_r4/chacah20_stream`，五阶段均完成，`accepted=true`；入口、C/C++ linkage 和 Hidden ABI qualification 使用同一份冻结合同，没有案例专用放行。
- `encode_one_block`：`/data/agrefactor_runs/evidence_catalog_p8_20261004/encode_one_block`，首轮 Candidate Preflight 证据充分而进入既有 Candidate repair；一次修复后五阶段完成并 `accepted=true`。真实 COSIM 的 Vitis HLS 总耗时约 13 分 41 秒，未发生 API 或工具超时。
- `mm_chain_dp_orig`：`/data/agrefactor_runs/general_evidence_catalog_p8_20261004_r2/mm_chain_dp_orig` 在 Hidden generation qualification 阶段耗尽后终止，最终 `failure_kind=testbench_generation_exhausted`、`owner=unknown`、`next_action=review_unknown`，未进入 formal Candidate repair。此前 post-P4 artifact `/data/agrefactor_runs/app_general_mechanism_post_p4_20261003/mm_chain_dp_orig` 的 CSYNTH `HLS 214-134` 诊断也保持 unknown/review_required；两者都没有足够证据强行归责 Candidate 或 Toolchain。
- 服务器 HEAD 仍为 `2b543c3`，工作树只保留历史 `.orig/.rej`、备份和一次性运行 artifacts；`git diff --check` 无输出。目录/投影/CSIM 回归共 28 项再次通过；此前聚焦回归 103 项、R5.1 registry `AUDIT_FAILURES=0`、inheritance `FAILURES=0` 仍有效。

本次收尾没有新增案例名、函数名、HLS 编号或错误文本特判；诊断目录仍只负责 category/stage/证据需求，owner/route 由现有证据解析和状态机决定。
## 8. 最终完成标准

本计划只有在以下条件全部满足后才算完成：

- 声明性诊断规则已经集中到目录；
- owner 和 route 不再由 HLS 编号或固定文本单独决定；
- CSYNTH、CSIM、Hidden 不再产生已知伪成功；
- 证据不足时保留 unknown，但充分证据仍可触发既有修复循环；
- stage 字段在每个诊断事件中存在并参与修复边界判断；
- 没有新增案例名、函数名、类型名或目录名特判；
- 现有 accepted fixture 没有因无证据的过度收紧而被误判；
- `chacha20_stream` 的入口合同有明确结论；
- `encode_one_block` 和 `mm_chain_dp_orig` 在通用修复后完成真实重跑；
- 每个最终结论都有对应的执行证据、owner authority 和 route 记录；
- 未来人工添加已有类别的新错误时只需编辑集中式目录；
- 只有新增语义类别、责任策略或修复流程时才需要修改代码和测试。
