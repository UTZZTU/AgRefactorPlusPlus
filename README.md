
# AgRefactor++

AgRefactor++ 是一个面向 Vitis HLS 的模型可插拔、证据驱动、预算约束的自动重构与验证系统。用户提供 C/C++ 源码、top function 和模型，系统生成候选实现，并通过编译、综合、Public/Hidden 测试和有限修复给出可审计结果。

> 当前正式普通入口是 `refactor`。`optimize` 和 `full` 已预留，但在 Stage 3 优化器完成前会明确拒绝执行。

## 快速开始

### 1. 获取代码与环境

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus

conda create -n agrefactor python=3.10
conda activate agrefactor
pip install -r requirements.txt
```

仓库当前从根目录直接运行，不需要 `pip install -e .`。

### 2. 配置 API 与目录

```bash
cp .env.example .env
```

使用已提交默认记录 `deepseek-v4-flash` 时：

```bash
DEEPSEEK_API_KEY=your-deepseek-api-key
RUN_DIR=/absolute/path/to/agrefactor_runs
WORK_DIR=/absolute/path/to/agrefactor_work
```

其他 OpenAI-compatible 模型默认读取 `OPENAI_API_KEY`；也可通过 `--base-url` 和 `--api-key-env` 显式覆盖。不要把真实凭证提交到仓库。

### 3. 加载 Vitis HLS

当前真实验收环境使用 Vitis HLS 2023.2：

```bash
source /path/to/Xilinx/Vitis/2023.2/settings64.sh
which vitis-run
vitis-run --version
```

多版本环境可显式设置：

```bash
export AGREFACTOR_VITIS_RUN=/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
export AGREFACTOR_VITIS_SETTINGS=/path/to/Xilinx/Vitis/2023.2/settings64.sh
```

### 4. 运行 source-only 重构

```bash
python -m agrefactor.cli refactor   src/heterorefactor/dfs/kernel.cpp   --top process_top   --model deepseek-v4-flash   --public-tests auto   --hidden-tests auto
```

完整参数：

```bash
python -m agrefactor.cli refactor --help
```

## 常用参数

| 参数 | 默认/范围 | 作用 |
|---|---|---|
| `--top` | 必填 | 明确指定 top function |
| `--model` | 必填 | 固定选择模型，不自动换模型 |
| `--model-family` | 自动推断 | 显式选择静态模型家族 Profile |
| `--base-url` | 模型/Provider 默认 | 覆盖 OpenAI-compatible endpoint |
| `--api-key-env` | 模型默认 | 指定 API key 环境变量名 |
| `--reasoning-effort` | `medium`; `low/medium/high` | 统一请求语义；具体映射仍由当前 Profile 决定 |
| `--target` | `vitis-2023.2-default` | 选择 TargetProfile |
| `--part` | Profile 默认 | 覆盖器件/part |
| `--clock-period` | Profile 默认 | 覆盖目标时钟周期，单位 ns |
| `--replace-compile-flag` | Profile 默认 | 可重复；替换 Profile 的完整编译参数列表 |
| `--public-tests auto` / `--public-test FILE` | `auto` | 自动生成或提供一个或多个 Public suite |
| `--hidden-tests auto/none` / `--hidden-test FILE` | `auto` | 自动生成、关闭或提供 Hidden suite |
| `--test-generation-profile` | `lightweight` | `lightweight` 或 `coverage-enhanced` |
| `--public-coverage-rounds` | `3`; `1..20` | coverage-enhanced 的 Public coverage 轮数 |
| `--hidden-coverage-rounds` | `6`; `1..20` | coverage-enhanced 的 Hidden coverage 轮数 |
| `--public-generation-trajectories` | `3`; `1..20` | coverage-enhanced 的 Public 独立 trajectory 数 |
| `--hidden-generation-trajectories` | `3`; `1..20` | coverage-enhanced 的 Hidden 独立 trajectory 数 |
| `--max-testbench-repairs` | `3`; `1..20` | Public Testbench 有限修复次数 |
| `--max-candidate-repairs` | `3`; `1..20` | Candidate 有限修复次数 |
| `--csim-timeout-s` | `120`; `1..600` | 单次 CSIM timeout |
| `--csynth-timeout-s` | `600`; `1..3600` | 单次 CSYNTH timeout |
| `--output-dir` | 自动目录 | 为单次运行指定精确持久 artifact 目录 |
| `--run-id` | 自动 UUID | 稳定运行标识 |
| `--json` / `--verbose` / `--debug` | 默认简洁输出 | 三者互斥的输出等级 |

`lightweight` 模式无论用户填写什么 coverage-enhanced 数值，实际 Public/Hidden rounds 和 trajectories 都固定为 `1`。

## 运行预算

| 参数 | 系统默认 | 安全上限 | 语义 |
|---|---:|---:|---|
| `--max-llm-calls` | 64 | 256 | 硬预算 |
| `--max-tool-calls` | 128 | 512 | 硬预算 |
| `--max-compile-calls` | 48 | 192 | 硬预算 |
| `--max-csim-calls` | 32 | 128 | 硬预算 |
| `--max-csynth-calls` | 16 | 64 | 硬预算 |
| `--max-wall-time-s` | 7200 | 14400 | 硬预算，秒 |
| `--token-budget` | 未设置 | 无硬上限 | observed-only；does not stop execution（不会中止执行） |
| `--cost-budget` | 未设置 | 无硬上限 | observed-only；does not stop execution（不会中止执行） |

用户显式硬预算必须不超过系统安全上限。Token 和 Estimated cost 只记录、展示和比较，不是最终账单，也不会触发硬停止。

## 输出与复现

默认终端只显示简洁摘要。持久 artifacts 包括：

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

指定独立输出目录：

```bash
python -m agrefactor.cli refactor kernel.cpp   --top process_top   --model deepseek-v4-flash   --output-dir /data/my_runs/dfs_trial
```

该目录必须不存在或为空；临时编译与工具工作目录仍位于 `WORK_DIR`。

高级 TaskSpec 校验与复现入口仍保留：

```bash
python -m agrefactor.cli validate-task task.json
python -m agrefactor.cli run task.json --dry-run
```

普通使用优先选择 source-only `refactor`。

## 文档

- [文档总览](docs/README.md)
- [使用指南](docs/guides/USAGE.md)
- [完整 CLI 参数参考](docs/guides/CLI_PARAMETER_REFERENCE.md)
- [环境配置](docs/guides/ENVIRONMENT.md)
- [项目路线](docs/roadmap/ROADMAP.md)

## 项目来源

AgRefactor++ 基于原始 AgRefactor 扩展：

```text
https://github.com/Williamzou0123/AgRefactor
```

使用、引用或分发本仓库时，请同时尊重原项目的论文、许可证和作者贡献。
