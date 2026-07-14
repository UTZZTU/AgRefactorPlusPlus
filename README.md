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
- [`docs/STAGE2_EVIDENCE_LOOP.md`](docs/STAGE2_EVIDENCE_LOOP.md)：证据闭环；
- [`docs/STAGE3_SAFE_OPTIMIZER.md`](docs/STAGE3_SAFE_OPTIMIZER.md)：安全三级优化器；
- [`docs/STAGE4_MEMORY_GATE.md`](docs/STAGE4_MEMORY_GATE.md)：Memory Applicability Gate；
- [`docs/STAGE5_VERSION_MIGRATION.md`](docs/STAGE5_VERSION_MIGRATION.md)：真实版本迁移；
- [`docs/STAGE6_EVALUATION.md`](docs/STAGE6_EVALUATION.md)：系统评测与最终交付；
- [`docs/stage2_acceptance.md`](docs/stage2_acceptance.md)：Testbench Reliability 验收。
<!-- AGREFPP_DETAILED_DOCS:END -->

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

当前已验证版本是 **Vitis HLS 2023.2**：

```bash
export VITIS_HLS_SETTINGS=/your/path/to/Xilinx/Vitis_HLS/2023.2/settings64.sh
source "$VITIS_HLS_SETTINGS"

which vitis_hls
vitis_hls -version
```

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
