# 使用说明

本文档描述当前正式普通入口和保留的高级复现入口。所有命令默认从仓库根目录执行。

## 1. 正式普通入口

```bash
python -m agrefactor.cli refactor \
  SOURCE \
  --top TOP \
  --model MODEL
```

最小示例：

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash
```

默认行为：

```text
reasoning_effort=medium
target=vitis-2023.2-default
public_tests=auto
hidden_tests=auto
test_generation_profile=lightweight
max_testbench_repairs=3
max_candidate_repairs=3
csim_timeout_s=120
csynth_timeout_s=600
```

完整参数见 [CLI 参数参考](CLI_PARAMETER_REFERENCE.md)。

## 2. Public 与 Hidden 来源

自动生成：

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

提供多个 Public suite，并自动生成 Hidden：

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-test tests/public_basic.cpp \
  --public-test tests/public_edges.cpp \
  --hidden-tests auto
```

关闭 Hidden：

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests none
```

普通 `refactor` 必须拥有至少一个 Public suite，因此不提供 `--public-tests none`。

## 3. Testbench 生成模式

### Lightweight

默认：

```bash
--test-generation-profile lightweight
```

实际固定为：

```text
Public rounds=1
Hidden rounds=1
Public trajectories=1
Hidden trajectories=1
Public coverage loop=false
```

### Coverage-enhanced

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --test-generation-profile coverage-enhanced \
  --public-coverage-rounds 3 \
  --hidden-coverage-rounds 6 \
  --public-generation-trajectories 3 \
  --hidden-generation-trajectories 3
```

四项值均允许 `1..20`。Public/Hidden coverage target 仍是内部固定策略。

## 4. Target 与编译参数

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --target vitis-2023.2-default \
  --part xcu200-fsgd2104-2-e \
  --clock-period 5.0
```

`--replace-compile-flag` 会替换 TargetProfile 的完整 compile flag 列表：

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --replace-compile-flag="-D XILINX" \
  --replace-compile-flag="-I include"
```

旧 `--compile-flag` 仅作为隐藏兼容别名保留。

## 5. Timeout 与预算

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --csim-timeout-s 120 \
  --csynth-timeout-s 600 \
  --max-llm-calls 64 \
  --max-tool-calls 128 \
  --max-wall-time-s 7200
```

调用次数和 wall time 是硬预算。Token/Cost 是 observed-only，不会中止执行。

## 6. 指定持久输出目录

```bash
python -m agrefactor.cli refactor \
  kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --output-dir /data/my_runs/dfs_trial
```

目录必须不存在或为空。该参数只改变持久 artifacts；临时工具工作仍由 `WORK_DIR` 或 `AGREFACTOR_WORK_ROOT` 管理。

## 7. 输出等级

```text
默认       简洁摘要
--json     稳定机器可读摘要
--verbose  阶段级进度
--debug    安全诊断 tee；完整日志仍进入 artifacts
```

三者互斥。

## 8. 高级入口

### validate-task

```bash
python -m agrefactor.cli validate-task task.json
```

只读取、校验并规范化 TaskSpec JSON，不调用模型或工具。

### run

```bash
python -m agrefactor.cli run task.json --dry-run
```

保留给高级复现和兼容迁移。隐藏的 `--legacy`、`--repair-aware` 是 deprecated compatibility selectors，不是普通产品入口。

## 9. Stage 3 边界

当前：

- `refactor` 可执行；
- `optimize/full` 仍明确拒绝；
- Stage 3 实施合同已冻结，但功能实现尚未开始；
- Stage 3 首包只允许实现 candidate state、checkpoint 和 best-correct 基础；
- Memory、迁移、自动模型池和 cosim 不进入 Stage 3 首包。

详见 [Stage 3 Frozen Implementation Contract](../roadmap/STAGE3_IMPLEMENTATION_CONTRACT.md)。
