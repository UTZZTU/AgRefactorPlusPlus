# AgRefactor++

AgRefactor++ 是一个基于原始 AgRefactor 扩展的 **Vitis HLS 智能体实验仓库**。当前工作重点是稳定复现 HLS 代码重构、RAG 记忆、批量实验和反馈驱动优化流程，并在此基础上继续研究 Vitis HLS 版本感知迁移。

> 当前版本已经能够完成单 kernel 重构和基础优化实验；“跨版本迁移”仍是后续建设方向，不应理解为已经完整实现。

## 当前已验证能力

| 模块 | 状态 | 说明 |
|---|---|---|
| `flow.new` 单 kernel 重构 | 已验证 | DFS 最小样例可完成测试生成、问题识别、规划、重构、csim/csynth 与反馈修复 |
| DeepSeek V4 Flash / Pro | 已验证 | 已完成 OpenAI-compatible 接入和端到端运行 |
| Token / Cost 汇总 | 已验证 | 运行结束后输出本次 agent 调用统计 |
| RAG 检索与经验写入 | 已验证 | 可记录成功/失败 trial，并在后续运行中检索相关经验 |
| `flow.parallel_kernel` | 部分验证 | 批量调度、隔离目录和结果汇总可运行；最终成功率仍会受 LLM 与测试代码质量影响 |
| `opt.simple_iter` | 已验证 | 已完成多轮综合反馈优化，并可保存满足资源约束的最佳设计 |
| HeteroRefactor | 暂停 | 外部 ROSE/EDG 依赖当前不可稳定获取，不属于主验证路径 |

更详细的验证范围和限制见 [`docs/REPRODUCTION_STATUS.md`](docs/REPRODUCTION_STATUS.md)。

<!-- AGREFPP_PROJECT_CONTINUITY:START -->
## 开发接续与权威路线

为避免新对话或局部开发导致方向漂移，后续开发按以下文档接续：

1. [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)：当前真实状态与下一任务；
2. [`docs/ROADMAP.md`](docs/ROADMAP.md)：八项不可删除目标、Stage 0–6 与完成标准；
3. [`docs/STAGE0_BASELINE.md`](docs/STAGE0_BASELINE.md)；
4. [`docs/STAGE1_INFRASTRUCTURE.md`](docs/STAGE1_INFRASTRUCTURE.md)；
5. [`docs/STAGE2_EVIDENCE_LOOP.md`](docs/STAGE2_EVIDENCE_LOOP.md)；
6. [`docs/stage2_acceptance.md`](docs/stage2_acceptance.md)。

当前主线是：用户指定模型、Memory、目标 Vitis 与预算 → 证据驱动修复 → 安全三级优化 → Memory Gate → Stage 5 真实版本迁移 → 返回 best_correct 与完整轨迹。版本迁移不得删除。
<!-- AGREFPP_PROJECT_CONTINUITY:END -->

<!-- AGREFPP_DETAILED_DOCS:START -->
## 详细开发文档

- [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)：新对话首先阅读；
- [`docs/ROADMAP.md`](docs/ROADMAP.md)：Stage 0–6 权威路线与完成标准；
- [`docs/GOAL_TRACEABILITY.md`](docs/GOAL_TRACEABILITY.md)：八项目标的实现、缺口与证据追踪；
- [`docs/STAGE0_BASELINE.md`](docs/STAGE0_BASELINE.md)：基线冻结；
- [`docs/STAGE1_INFRASTRUCTURE.md`](docs/STAGE1_INFRASTRUCTURE.md)：共享基础设施；
- [`docs/stage1_target_profile_acceptance.md`](docs/stage1_target_profile_acceptance.md)：TargetProfile 真实工具验收；
- [`docs/stage1_csynth_budget_acceptance.md`](docs/stage1_csynth_budget_acceptance.md)：csynth 硬预算真实工具验收；
- [`docs/stage1_compile_csim_budget_acceptance.md`](docs/stage1_compile_csim_budget_acceptance.md)：compile/csim 硬预算与真实本地 csim 验收；
- [`docs/stage1_core_acceptance.md`](docs/stage1_core_acceptance.md)：真实 DFS 全链路与 Stage 1 Core 关闭验收；
- [`docs/STAGE2_EVIDENCE_LOOP.md`](docs/STAGE2_EVIDENCE_LOOP.md)：证据闭环；
- [`docs/STAGE3_SAFE_OPTIMIZER.md`](docs/STAGE3_SAFE_OPTIMIZER.md)：安全三级优化器；
- [`docs/STAGE4_MEMORY_GATE.md`](docs/STAGE4_MEMORY_GATE.md)：Memory Applicability Gate；
- [`docs/STAGE5_VERSION_MIGRATION.md`](docs/STAGE5_VERSION_MIGRATION.md)：真实版本迁移；
- [`docs/STAGE6_EVALUATION.md`](docs/STAGE6_EVALUATION.md)：系统评测与最终交付；
- [`docs/stage2_acceptance.md`](docs/stage2_acceptance.md)：Testbench Reliability 验收。
<!-- AGREFPP_DETAILED_DOCS:END -->

<!-- AGREFPP_STAGE1_TARGET_PROFILE_STATUS:START -->
## Stage 1 TargetProfile 最新状态

TargetProfile 本地执行核心已经在 Vitis 2023.2 上完成真实 csynth 验收：

```text
TaskSpec.target
→ legacy flow
→ target-aware Tcl
→ selected vitis-run
→ version match
→ real csynth
```

已验证：

- device `xcu200-fsgd2104-2-e`；
- target clock `4.0 ns`；
- compile flag 真实到达编译器；
- requested/actual version `2023.2` matched；
- mismatch 在 csynth 前阻断；
- effective profile 与 invocation evidence；
- 153/153 确定性测试；
- `REAL_VITIS_SMOKE_PASSED=1`。

多版本机器必须显式协调：

```bash
export AGREFACTOR_VITIS_RUN=/path/to/Vitis/<version>/bin/vitis-run
```

与 TaskSpec 的：

```json
{
  "target": {
    "toolchain_version": "<version>"
  }
}
```

完整用法见 [`docs/USAGE.md`](docs/USAGE.md)，验收证据见 [`docs/stage1_target_profile_acceptance.md`](docs/stage1_target_profile_acceptance.md)。

Stage 1 Core 已通过真实 DFS 全链路验收并关闭；TargetProfile 配置化、多版本/多器件等继续作为 Hardening 和后续 Stage 建设。
<!-- AGREFPP_STAGE1_TARGET_PROFILE_STATUS:END -->

<!-- AGREFPP_STAGE1_CSYNTH_BUDGET_STATUS:START -->
## Stage 1 csynth Hard Budget 最新状态

真实链路已经验收：

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ flow.new / csynth_and_csim
→ run_csynth
→ real Vitis 2023.2
```

已验证：

- `max_tool_calls` 与 `max_csynth_calls` 双重限制；
- 预算检查发生在 version probe 前；
- 通过版本检查后、真实启动前精确计数一次；
- success/failure/timeout/launch exception 的 exact-once 语义；
- version mismatch 不消耗真实 csynth 次数；
- `limit=0` 不执行 probe 或 Vitis；
- `limit=1` 第一次真实综合成功，第二次在 probe 前阻断；
- final usage：`tool_calls=1`、`csynth_calls=1`；
- `169/169` 确定性测试；
- `REAL_VITIS_CSYNTH_BUDGET_SMOKE_READY=1`。

验收证据见 [`docs/stage1_csynth_budget_acceptance.md`](docs/stage1_csynth_budget_acceptance.md)。

Stage 1 Core 已关闭；public test 保留为 Stage 2 评测语义，cosim 不属于当前活跃范围，TargetProfile 后续配置化作为 Hardening。
<!-- AGREFPP_STAGE1_CSYNTH_BUDGET_STATUS:END -->


<!-- AGREFPP_STAGE1_COMPILE_CSIM_BUDGET_STATUS:START -->
## Stage 1 Compile 与 C Simulation Hard Budget 最新状态

完整预算链路已经完成确定性验收：

```text
UnifiedRunner
→ shared BudgetManager
→ Testbench Preflight g++
→ run_csynth
→ run_csim g++
→ generated ./csim
```

已验证：

- `max_tool_calls`、`max_compile_calls`、`max_csynth_calls`、`max_csim_calls`；
- Preflight 与 csim 编译共享 `compile_calls`；
- csim 在完整两进程计划不足时于 `g++` 前阻断；
- compile/csim success、failure、timeout、launcher exception 的 exact-once 语义；
- 完整联合成功预算：`tool_calls=4`、`compile_calls=2`、`csynth_calls=1`、`csim_calls=1`；
- `204/204` 确定性测试；
- 真实本地 csim 首次 `g++ + ./csim` 成功；
- 第二次调用在 `g++` 前阻断；
- final usage：`tool_calls=2`、`compile_calls=1`、`csim_calls=1`；
- `REAL_LOCAL_CSIM_BUDGET_SMOKE_READY=1`。

验收证据见 [`docs/stage1_compile_csim_budget_acceptance.md`](docs/stage1_compile_csim_budget_acceptance.md)。

Stage 1 Core 已完成真实 DFS Preflight → Vitis csynth → csim 全链路验收。public test 不新增独立预算；cosim 与原项目一致，不在当前活跃范围。
<!-- AGREFPP_STAGE1_COMPILE_CSIM_BUDGET_STATUS:END -->


<!-- AGREFPP_STAGE1_CORE_ACCEPTANCE:START -->
## Stage 1 Core 已关闭

真实代表性 kernel 验收：

```text
src/heterorefactor/dfs/kernel.cpp
→ UnifiedRunner
→ shared BudgetManager
→ real Preflight g++
→ real Vitis HLS 2023.2 csynth
→ real csim g++
→ real ./csim
```

结果：

- `RESULT_STATUS=succeeded`；
- `tool_calls=4`；
- `compile_calls=2`；
- `csynth_calls=1`；
- `csim_calls=1`；
- 额度耗尽后的额外 Preflight 在 `g++` 前阻断；
- `REAL_DFS_FULL_CHAIN_BUDGET_READY=1`。

该验收没有调用 LLM API。综合候选是确定性的 synthesis-safe reference，用于验证基础设施，不代表智能体已经自动重构 DFS。

范围决策：

- public test 保留为 Stage 2/3 的 test-suite role 和反馈策略，不新增独立 `public_test_calls`；
- cosim 在原始 AgRefactor 中没有活跃实现，不属于 Stage 1 Core；
- TargetProfile named profiles、per-profile executable/settings、platform/resources/parser/provenance 属于 Hardening。

完整证据见 [`docs/stage1_core_acceptance.md`](docs/stage1_core_acceptance.md)。
<!-- AGREFPP_STAGE1_CORE_ACCEPTANCE:END -->

## 快速开始

### 1. 获取代码

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus
```

### 2. 准备 Python 环境

```bash
conda create -n agrefactor python=3.10
conda activate agrefactor
pip install -r requirements.txt
```

本仓库当前没有 `setup.py` 或 `pyproject.toml`，因此不需要执行 `pip install -e .`。请从仓库根目录运行命令。

### 3. 加载 Vitis HLS

当前真实验收版本是 **Vitis 2023.2**：

```bash
source /your/path/to/Xilinx/Vitis/2023.2/settings64.sh

which vitis-run
vitis-run --version
```

多版本机器需要显式指定本次使用的 launcher：

```bash
export AGREFACTOR_VITIS_RUN=/your/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
```

该 launcher 必须与 TaskSpec 的 `target.toolchain_version` 一致。完整说明见 [`docs/USAGE.md`](docs/USAGE.md)。

### 4. 配置 API 与输出目录

```bash
cp .env.example .env
```

在 `.env` 中至少设置：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/your/path/to/agrefactor_runs
WORK_DIR=/your/path/to/agrefactor_work
```

### 5. 运行最小示例

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

成功时，日志末尾会出现：

```text
HLS refactoring with RAG completed successfully.
```

这里的 `with RAG` 是原流程保留的固定输出文字。只有显式传入 `--enable_rag` 时才会启用检索。

## 常用工作流

完整命令见 [`docs/USAGE.md`](docs/USAGE.md)，其中包括：

- 单 kernel 重构
- RAG 数据库初始化、写入与检索
- 多 kernel 并行实验
- `opt.simple_iter` 多轮性能优化
- 输出文件与结果检查

## 模型后端

当前已验证：

- `deepseek-v4-flash`：适合基础重构和流程调试
- `deepseek-v4-pro`：适合更复杂的重构与优化实验
- 其他 OpenAI-compatible API：可通过 `OPENAI_BASE_URL` 接入

代码仍保留 OpenAI 与 Gemini 的配置路径，但本仓库当前的主要复现结果来自 DeepSeek 后端。

## 仓库结构

```text
flow/                  重构主流程、RAG、测试生成与批量实验
opt/                   综合反馈驱动的性能优化流程
src/                   示例 kernel、benchmark 与基线代码
scripts/               实验分析、HLS 服务和辅助脚本
knowledge_db/          本地长期记忆数据库
containers/            可选外部工具容器配置
docs/                  环境、用法、验证状态与变更记录
```

## 文档

- [`docs/USAGE.md`](docs/USAGE.md)：常用命令和运行方式
- [`docs/REPRODUCTION_STATUS.md`](docs/REPRODUCTION_STATUS.md)：已验证功能、实验结果与限制
- [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)：环境和依赖说明
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md)：AgRefactor++ 相对原项目的主要修改

## 当前研究方向

- Vitis HLS 多版本知识库与版本约束建模
- 编译、仿真和综合反馈驱动的自动修复
- 可复用的 AST/Clang 迁移规则
- 跨版本 HLS 迁移测试集与评价方法
- 更稳定的测试代码生成和批量评测

## 项目来源与致谢

AgRefactor++ 基于原始 AgRefactor 修改和扩展：

```text
https://github.com/Williamzou0123/AgRefactor
```

使用、引用或分发本仓库时，请同时尊重原项目的论文、许可证和作者贡献。
