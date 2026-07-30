# 变更记录

本文档记录 AgRefactor++ 相对于原始 AgRefactor 的主要代码与文档修改。原项目已有但仅在本地完成复现的功能，统一记录在 `docs/guides/REPRODUCTION_STATUS.md`，避免把“复现验证”误写成“新增功能”。

## 未发布

### Stage 3.2 Qualification and PPA Evidence

- 新增独立 Stage 3 qualification orchestration，严格执行 source → Preflight → Public → CSYNTH → Hidden → PPA → feasibility，不改写 Stage 2 已验收顺序。
- 新增 typed Vitis HLS PPA evidence、XML-first/text-fallback report adapter、resource feasibility 与冻结 latency comparator。
- 新增包含 source/Target/实际 toolchain/Public-Hidden suite/flags/clock/device/parser/schema 的 exact validation cache identity，以及原子、不可变、Hidden-safe evidence cache。
- 修复 S3.1 `CandidateRecord` budget snapshot 对正常 `tokens` 字段的误拒绝，改为明确 BudgetUsage allowlist 并继续拒绝未知字段。
- 新增 85 个 S3.2 focused tests；optimizer regression 135/135，全量确定性回归 1643/1643。
- 完成一次既有 accepted baseline 的真实 g++ Preflight → Public CSIM → Vitis HLS 2023.2 CSYNTH → Hidden CSIM → PPA replay；随后 exact cache hit 的真实工具计数增量为 0。
- 本包未调用模型，未实现 S3.3 状态机、三级搜索或正式 `optimize/full`；下一包为 S3.3。

### Stage 3.1 Candidate State Foundation

- 新增 typed `HypothesisRecord`、`CandidateRecord` 与 `OptimizerState`，执行严格 schema、确定性序列化和单向候选状态约束。
- baseline qualification 成功后的 baseline 可初始化为 `best_correct`；S3.1 不实现 qualification 本身。
- 新增原子 optimizer checkpoint writer、不可变 checkpoint marker、projection 恢复、source hash/path/symlink 防护。
- 新增 50 个 S3.1 focused tests；全量确定性回归达到 1558/1558。
- 本包未调用模型、Vitis、真实 CSIM 或真实 CSYNTH，未实现 PPA comparator、cache、三级策略或 `optimize/full`。
- 下一包为 S3.2 Qualification and PPA Evidence。

### Current authority cleanup and Stage 3 planning freeze

- 最新 CLI 后完成一次真实 DeepSeek + Vitis HLS 2023.2 source-only accepted smoke。
- 删除一次性交接、过期 Bridge 和单次 retry reference；长期经验合并进入 history。
- `PROJECT_STATE`、`GOAL_TRACEABILITY` 和复现状态收敛为当前权威。
- Legacy baseline 与当前产品验证分离。
- 冻结 Stage 3 Candidate、Hypothesis、best_correct、checkpoint、cache、预算、CLI 和验收合同。
- Stage 3 功能实现仍未开始。

<!-- AGREFPP_STAGE1_STAGE2_CHANGELOG:START -->
### Stage 1 共享架构

- 新增 TaskSpec、TargetProfile、模型抽象/Registry/Provider、Evaluator/Evidence、Budget/Trace、UnifiedRunner 与 CLI。
- 新增 Legacy Refactor Adapter 与 module-entrypoint regression。
- 合并 AutoGen 与 testbench repair known usage，并按 artifact 去重。

### Stage 1 TargetProfile 本地执行核心

- 新增 default target profile 与 partial override resolver。
- 支持 clock period/frequency、compile flags replace/append。
- 将 TargetProfile 下传到 legacy `flow.new` 与 csynth。
- 从 TargetProfile 生成 part、clock 和 cflags 对应 Tcl。
- 新增 `AGREFACTOR_VITIS_RUN` 可选 executable 覆盖。
- 新增 actual executable resolution 与 `vitis-run --version` probe。
- requested/actual mismatch、probe failure、timeout、unparseable 会在 csynth 前阻断。
- 新增 `effective_target_profile.json` 与 `csynth_invocation.json`。
- remote non-default target 显式拒绝，避免静默丢配置。
- 确定性测试达到 153/153。
- Vitis 2023.2 真实 csynth smoke 通过：
  `/data/agrefactor_runs/stage1_target_profile_real_vitis_20260715_141118`。
- 新增多版本显式指定文档：`target.toolchain_version` + `AGREFACTOR_VITIS_RUN`。
- 新增 `stage1_target_profile_acceptance.md`。

### Stage 1 csynth Hard Budget

- 新增 `max_csynth_calls/csynth_calls`，并保留 aggregate `max_tool_calls/tool_calls`。
- 在 Vitis version probe 前执行 prospective hard check。
- 版本匹配后、真实 launcher 前执行 exact-once consume。
- success、failure、timeout、launch exception 均计一次真实尝试。
- version mismatch/probe failure 不消耗真实 csynth 次数。
- 将同一个 `BudgetManager` 从 UnifiedRunner/RunContext 贯通到 legacy `run_csynth()`。
- 普通与 HeteroRF csynth 路径均完成预算下传。
- 有硬工具预算时显式拒绝无法共享本地 manager 的 remote 路径。
- 新增底层、legacy plumbing、UnifiedRunner 完整链路测试。
- 确定性测试达到 169/169。
- Vitis 2023.2 真实 budget smoke 通过：
  `/data/agrefactor_runs/stage1_real_vitis_csynth_budget_smoke_20260715_184955`。
- 第一次真实综合成功，第二次在 version probe 前阻断，final usage 为 1/1。
- 新增 `stage1_csynth_budget_acceptance.md`。

### Stage 1 Hardening 状态

- compile、csynth、csim 与 aggregate tool hard budget 已完成；
- Batch A 已完成 stable named profile、per-profile executable/settings、
  parser identity、basic resources、per-field provenance 和无 secret 模板；
- Batch B 的更多版本、器件、platform、版本特定 parser 与多 kernel 交叉验证
  留到 Stage 5 前，不阻塞 Stage 3；
- cosim 仍不属于当前活跃主路径。

### Stage 2 结构化证据闭环

早期 Testbench Reliability：

- 新增 testbench preflight、failure stage/kind/owner/next-action。
- 新增 testbench-only bounded repair、ABI/linkage evidence 与 private-global gate。
- provider 异常、空/未修改回复消耗 repair budget。
- 新增 repair artifact、统一 usage 与真实 unified CLI + DeepSeek + Vitis 验收。

Public/Hidden 与通用反馈：

- 新增 TestSuiteSpec、Public/Hidden split、agent-safe/operator-full evidence 与 trace。
- 新增通用 Feedback Schema、Preflight/CSYNTH/Test adapters。
- 新增确定性 CSYNTH parser、feedback views/composers、router、state machine 与 coordinator。
- 新增 Public/Hidden 多 suite feedback composition 和 Hidden suppression。

真实运行时证据链：

- 新增 generic ValidationOrchestrator。
- 新增 real Preflight、CSYNTH、Public CSIM 与 Hidden CSIM handlers。
- 所有阶段共享同一 RunContext/BudgetManager/TraceRecorder。
- Public 收集非终止反馈；Hidden 首个 blocking result fail-fast。
- 新增 runtime lazy integration exports，消除 evaluation/runtime package cycle。
- 新增 suite work-directory executor contract 与回归测试。
- 确定性测试达到 531/531。
- 真实 Vitis 2023.2 全链通过：
  `/data/agrefactor_runs/stage2_real_csim_handler_resume5_20260717_184240`。
- 精确预算：`6 tool / 3 compile / 1 csynth / 2 csim`。
- Hidden-only mismatch 终止且不泄漏；zero CSIM budget 在 compile 前阻断。
- Stage 2 仍缺 Shared Layered Prompt Builder、多类型 smoke 与最终 closure。
### Stage 2 最终关闭

- 完成 Shared Layered Prompt Builder 与 Testbench/Candidate consumers。
- 完成 provider-neutral CandidateModelAdapter、strict complete-replacement contract
  和 bounded Candidate Repair Loop。
- 完成正式 `--repair-aware` UnifiedRunner/CLI，写入 versioned run、phase 和
  repair artifacts；legacy、dry-run 与 repair-aware 模式显式互斥。
- 完成七类 baseline、`7/7` Vitis 2023.2 full chains、九场景 fault matrix 与
  `16/16` independent ground-truth labels。
- 完成 shared Repair Protocol/artifact schema、Minimal ModelFamilyProfile 和
  Stage 1 Hardening Batch A。
- 完成一次真实 DeepSeek network-model request/response/usage smoke；成功修复
  不是关闭条件，可信 terminal result 被完整记录。
- Evidence gate 未证明 CandidateResponseContract 或 CSYNTH parser 缺口，因此
  未做猜测式代码扩张。
- 完成 B-01～B-05 `5/5`、8 个跨阶段 evidence nodes、8 个 manifests 与
  34 个 entries 的完整性审计。
- 最终确定性回归 `836/836`，Stage 2 closure checklist `10/10`。
- 新增 `stage2_hardening_acceptance.md` 与 `stage2_closure_acceptance.md`。
- Stage 2 正式关闭；下一阶段为 Stage 3 Safe Three-Level Optimizer。

<!-- AGREFPP_STAGE1_STAGE2_CHANGELOG:END -->

### 文档

- 精简主 README，使其只保留项目定位、已验证能力、快速开始和文档入口。
- 新增 `docs/guides/USAGE.md`，集中维护单 kernel、RAG、批量实验和 optimization 命令。
- 新增 `docs/guides/REPRODUCTION_STATUS.md`，区分已验证、部分验证、暂未验证和暂停功能。
- 修正安装说明：仓库当前没有 `setup.py` 或 `pyproject.toml`，不再建议执行 `pip install -e .`。
- 明确版本感知迁移仍是研究方向，而不是当前已经完整实现的能力。
- 明确 HeteroRefactor 不是主流程必需依赖，当前因外部 EDG binary 不可用而暂停。

### 复现状态同步

- 补充 RAG 成功/失败 trial 写入与检索的验证状态。
- 补充 `flow.parallel_kernel` 的小规模框架验证和稳定性限制。
- 补充 `opt.simple_iter` 的多轮综合反馈优化与最佳设计保存验证。
- 将 coverage/hidden TB、remote HLS/MCP 等代码路径标记为尚未纳入稳定主验证基线。

## DeepSeek 与 OpenAI-compatible 适配

- 修复 AG2/AutoGen 0.11.x 下 `LLMConfig` 初始化方式变化导致的兼容问题。
- 保留 OpenAI-compatible provider 的 `api_type="openai"` 配置路径。
- 支持通过 `OPENAI_BASE_URL` 接入 DeepSeek 等兼容服务。
- 保留 Gemini 的 `api_type="google"` 配置路径。
- 使用 DeepSeek V4 Flash 和 Pro 完成 DFS 最小样例端到端验证。

## Structured output 与 token 配置

- 将不适用于 DeepSeek 的 Python/Pydantic `response_format` 转换为 JSON mode：

  ```json
  {"type": "json_object"}
  ```

- 为 thinking 模型设置更大的默认 token 预算，减少只返回 reasoning、没有最终 content 的情况。
- 增加 DeepSeek V4 Flash / Pro 的价格元数据，用于消除 unknown-model warning 和提供粗略成本统计。
- 价格元数据不应视为长期准确报价。

## Identifier JSON 解析增强

- 新增 `_parse_identified_items()`。
- 同时兼容：

  ```json
  {"identified_items": []}
  ```

  和：

  ```json
  []
  ```

- 对异常 JSON 类型提供更明确的错误信息。

## RAG memory 解析增强

- 增强 RAG agent 对模型输出的解析能力。
- 兼容代码块、对象、列表等更灵活的 JSON 包装形式。
- 忽略本地实验生成的临时 RAG store，避免把运行数据库误提交到 Git。

## Token / Cost 汇总

- 在 `flow.new` 开始时重置本次 usage registry。
- agent 创建后注册到统一 usage registry。
- 在流程成功或失败退出前打印 `Token / Cost Summary`。
- 优先使用 AG2 聚合接口；失败时回退到单 agent usage。
- 已在 DeepSeek Flash 和 Pro 的最小重构实验中验证输出。

## 文档中文化与结构调整

- 将项目名统一为 AgRefactor++。
- README 主体改为中文并删除重复的中英文说明。
- 将环境说明拆分到 `docs/guides/ENVIRONMENT.md`。
- 将详细代码修改拆分到 `docs/history/CHANGELOG.md`。
- README 聚焦新用户第一次运行所需内容。

<!-- AGREFPP_ROADMAP_EXPANSION:START -->
### Roadmap 与开发接续文档扩展

- 将 Stage 0–6 路线扩展为详细目标、实现机制、未完成项、完成标准和评测要求。
- 新增 `GOAL_TRACEABILITY.md`，追踪最初八项目标的当前实现、缺口和完成证据。
- 新增 Stage 3–6 专项设计文档，避免安全优化、Memory Gate、版本迁移和评测目标被遗忘或弱化。
- 明确 `PROJECT_STATE.md` 保持简洁，详细依据由 ROADMAP 和各 Stage 文档承载。
- 明确任何核心路线变更必须同步更新 Roadmap、Goal Traceability、Project State、Stage 文档和 Changelog。
<!-- AGREFPP_ROADMAP_EXPANSION:END -->
