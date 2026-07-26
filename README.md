# AgRefactor++

AgRefactor++ 是一个面向 Vitis HLS 的模型可插拔、证据驱动、预算约束的自动重构、验证与后续安全优化系统。

当前状态：

```text
PRE_STAGE3_CLOSED=true
STAGE3_PLANNING_FROZEN=true
STAGE3_IMPLEMENTATION_STARTED=false
NEXT_STEP=STAGE3_IMPLEMENTATION_STEP_1
```

当前正式普通入口是 `refactor`。`optimize` 和 `full` 已保留；只有在 Stage 3 实现按照冻结合同逐步完成后才会开放真实执行。

## 快速开始

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus

conda create -n agrefactor python=3.10
conda activate agrefactor
pip install -r requirements.txt
cp .env.example .env
```

使用已提交默认模型记录 `deepseek-v4-flash` 时，至少配置：

```bash
DEEPSEEK_API_KEY=your-deepseek-api-key
RUN_DIR=/absolute/path/to/agrefactor_runs
WORK_DIR=/absolute/path/to/agrefactor_work
```

加载 Vitis HLS 2023.2：

```bash
source /path/to/Xilinx/Vitis/2023.2/settings64.sh
export AGREFACTOR_VITIS_RUN=/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
vitis-run --version
```

运行普通 source-only 重构：

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

指定单次持久输出目录：

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --output-dir /data/my_runs/dfs_trial
```

完整参数：

```bash
python -m agrefactor.cli refactor --help
```

## 当前正式参数摘要

| 类别 | 当前合同 |
|---|---|
| Reasoning | 用户语义默认 `medium`；真实 Provider 映射仍由静态 Profile 决定 |
| Public tests | 必须为 `auto` 或一个/多个 provided suites |
| Hidden tests | `auto`、`none` 或一个/多个 provided suites |
| Test generation | `lightweight` 默认；`coverage-enhanced` 可独立设置 Public/Hidden rounds 与 trajectories |
| Repair | Testbench/Candidate 默认 3，安全上限 20 |
| Timeout | 模型请求 240 秒；CSIM 120 秒；CSYNTH 600 秒 |
| Hard budgets | LLM 64、Tool 128、Compile 48、CSIM 32、CSYNTH 16、Wall 7200 秒 |
| Token/Cost | observed-only；does not stop execution |
| Compile flags | 使用 `--replace-compile-flag` 替换 TargetProfile 默认列表 |
| Output | 默认自动目录；`--output-dir` 可指定精确持久 artifact 目录 |

完整、可执行的参数定义见 [CLI 参数参考](docs/guides/CLI_PARAMETER_REFERENCE.md)。

## 当前证据边界

已验证：

- Ubuntu 22.04、Python 3.10、Vitis HLS 2023.2；
- source-only `refactor` 正式入口；
- 真实 DeepSeek、Preflight、CSYNTH、Public/Hidden CSIM；
- 结构化反馈、有限 Candidate/Testbench repair；
- Execution Identity、预算、trace 和安全 artifacts；
- 最新确定性回归与最新 CLI 后真实 smoke。

尚未实现：

- Stage 3 安全三级优化器；
- Stage 4 Memory Applicability Gate；
- Stage 5 版本迁移；
- 自动模型池与动态路由；
- 多版本/多设备广泛支持；
- RTL cosim 主路径。

## 权威文档

1. [当前项目状态](docs/roadmap/PROJECT_STATE.md)
2. [长期路线](docs/roadmap/ROADMAP.md)
3. [目标追踪](docs/roadmap/GOAL_TRACEABILITY.md)
4. [Stage 3 冻结实施合同](docs/roadmap/STAGE3_IMPLEMENTATION_CONTRACT.md)
5. [使用指南](docs/guides/USAGE.md)
6. [复现与验证状态](docs/guides/REPRODUCTION_STATUS.md)
7. [文档总览](docs/README.md)

## 项目来源

AgRefactor++ 基于原始 AgRefactor 扩展：

```text
https://github.com/Williamzou0123/AgRefactor
```

使用、引用或分发本仓库时，请同时尊重原项目的论文、许可证和作者贡献。
