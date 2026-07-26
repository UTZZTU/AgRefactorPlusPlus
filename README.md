# AgRefactor++

AgRefactor++ 是一个面向 Vitis HLS 的模型可插拔、证据驱动、预算约束的自动重构与验证系统。用户提供 C/C++ 源码、top function 和模型，系统生成候选实现，并通过真实编译、综合、Public/Hidden 测试和有限修复给出可审计结果。

> 当前正式普通入口是 `refactor`。`optimize` 和 `full` 命令已经保留，但在安全三级优化器实现前会明确拒绝执行，不会伪造优化成功。

## 快速开始

### 1. 获取代码

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus
```

### 2. 创建环境

```bash
conda create -n agrefactor python=3.10
conda activate agrefactor
pip install -r requirements.txt
```

仓库当前从根目录直接运行，不需要 `pip install -e .`。

### 3. 配置模型与输出目录

```bash
cp .env.example .env
```

至少填写：

```bash
OPENAI_API_KEY=your-api-key
# OPENAI_BASE_URL=https://api.deepseek.com

RUN_DIR=/absolute/path/to/agrefactor_runs
WORK_DIR=/absolute/path/to/agrefactor_work
```

也可以通过 CLI 的 `--base-url` 和 `--api-key-env` 覆盖模型传输配置。凭证只应保存在本地环境变量或 `.env` 中。

### 4. 加载 Vitis HLS

当前真实验收环境使用 Vitis HLS 2023.2：

```bash
source /path/to/Xilinx/Vitis/2023.2/settings64.sh
which vitis-run
vitis-run --version
```

多版本环境可以显式指定：

```bash
export AGREFACTOR_VITIS_RUN=/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
export AGREFACTOR_VITIS_SETTINGS=/path/to/Xilinx/Vitis/2023.2/settings64.sh
```

### 5. 运行 source-only 重构

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

查看完整参数：

```bash
python -m agrefactor.cli refactor --help
```

## 常用参数

| 参数 | 作用 |
|---|---|
| `--top` | 必填，明确指定 top function；系统不会自动猜测 |
| `--model` | 必填，选择固定模型 |
| `--model-family` | 可选，显式选择静态模型家族 Profile |
| `--base-url` | 覆盖 OpenAI-compatible API endpoint |
| `--api-key-env` | 指定保存 API key 的环境变量名 |
| `--reasoning-effort low\|medium\|high` | 模型家族统一 reasoning 语义 |
| `--target` | 选择 TargetProfile，默认 `vitis-2023.2-default` |
| `--part` | 覆盖器件/part |
| `--clock-period` | 覆盖目标时钟周期，单位 ns |
| `--compile-flag` | 可重复提供编译参数 |
| `--public-tests auto` / `--public-test FILE` | 自动生成或提供 Public suite，可重复提供文件 |
| `--hidden-tests auto` / `--hidden-test FILE` | 自动生成或提供 Hidden suite，可重复提供文件 |
| `--test-generation-profile` | `lightweight` 或显式 `coverage-enhanced` |
| `--public-coverage-rounds` | coverage-enhanced 的 Public coverage 轮数 |
| `--test-generation-trajectories` | coverage-enhanced 的独立生成 trajectory 数 |
| `--max-testbench-repairs` | Testbench 有限修复次数 |
| `--max-candidate-repairs` | Candidate 有限修复次数 |
| `--max-llm-calls` | LLM 调用硬预算 |
| `--max-tool-calls` | 聚合工具调用硬预算 |
| `--max-compile-calls` | 编译调用硬预算 |
| `--max-csim-calls` | CSIM 调用硬预算 |
| `--max-csynth-calls` | CSYNTH 调用硬预算 |
| `--max-wall-time-s` | 运行时间硬边界 |
| `--token-budget` | Token 软预算，只统计和告警 |
| `--cost-budget` | 费用软预算，使用定价快照的原生币种 |
| `--json` | 输出稳定机器可读摘要 |
| `--verbose` | 输出阶段级进度 |
| `--debug` | 显示安全诊断流，完整日志仍写入 artifacts |

用户显式预算必须位于系统安全上限内；Token 和 Estimated cost 当前是 observed-only 软预算，不会冒充硬限制或最终账单。

## 测试来源示例

自动生成 Public 和 Hidden：

```bash
python -m agrefactor.cli refactor kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

混合来源：

```bash
python -m agrefactor.cli refactor kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-test tests/public_basic.cpp \
  --public-test tests/public_edges.cpp \
  --hidden-tests auto
```

系统会根据 Public/Hidden 的实际来源自动推导 `provided`、`auto` 或 `hybrid`，无需手动声明 hybrid。

## 输出与复现

默认终端只显示简洁摘要。完整运行证据写入 artifacts，包括：

```text
full_result.json
trace.jsonl
model_calls.json
tool_calls.json
stdout.log
stderr.log
execution_identity.json
run_artifact_manifest.json
```

高级复现仍支持：

```bash
python -m agrefactor.cli run task.json
```

普通使用优先选择 source-only `refactor`，不需要手写 TaskSpec、candidate、work dir 或 artifact dir。

## 仓库结构

```text
agrefactor/            正式 CLI、配置、运行时、模型与验证核心
flow/                  Legacy 生成能力、测试生成和 RAG 兼容组件
opt/                   现有优化实验与后续优化器实现位置
src/                   示例 kernel、benchmark 与基线代码
tests/                 确定性回归测试
scripts/               工具和实验辅助脚本
docs/                  使用指南、路线、验收、审计和历史文档
```

## 文档

- [文档总览](docs/README.md)
- [使用指南](docs/guides/USAGE.md)
- [环境配置](docs/guides/ENVIRONMENT.md)
- [项目路线](docs/roadmap/ROADMAP.md)

## 项目来源

AgRefactor++ 基于原始 AgRefactor 扩展：

```text
https://github.com/Williamzou0123/AgRefactor
```

使用、引用或分发本仓库时，请同时尊重原项目的论文、许可证和作者贡献。
