# 使用说明

本文档集中记录 AgRefactor++ 的常用命令。所有命令默认从仓库根目录运行。

## 1. 每次运行前

```bash
conda activate agrefactor
source "$VITIS_HLS_SETTINGS"

which python
which vitis_hls
```

确保 `.env` 中已经设置：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/your/path/to/agrefactor_runs
WORK_DIR=/your/path/to/agrefactor_work
```

## 2. 单 kernel 重构

### Flash 基础实验

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

### Pro 重构实验

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-pro \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --max_retry_attempts 8 \
  --debug
```

`--reasoning_effort` 是否支持更高档位取决于模型服务端。基础重构优先从 `low` 开始，避免不必要的成本和延迟。

## 3. RAG 数据库

### 3.1 首次创建并写入 trial

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --knowledge_db_path knowledge_db/dfs_demo \
  --enable_rag_update \
  --reset_knowledge_db \
  --debug
```

`--enable_rag_update` 会记录每轮 trial 的综合/仿真结果，包括成功和失败尝试。

### 3.2 检索已有经验并继续更新

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --knowledge_db_path knowledge_db/dfs_demo \
  --enable_rag \
  --enable_rag_update \
  --debug
```

不要在希望保留历史经验时再次使用 `--reset_knowledge_db`。

## 4. 多 kernel 并行实验

先创建 kernel 列表，例如 `my_kernels.json`：

```json
[
  ["heterorefactor/dfs/kernel.cpp", "process_top", "dfs"]
]
```

路径相对于仓库的 `src/` 目录。

运行：

```bash
python -m flow.parallel_kernel \
  --exp_name dfs_batch_demo \
  --kernels_file my_kernels.json \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --repeat 1 \
  --max_workers 1 \
  --max_retry_attempts 3 \
  --debug
```

建议先用 `--max_workers 1` 验证，再逐步提高并发。并发过高会同时增加 API 请求、Vitis HLS 进程、内存和磁盘压力。

批量结果会写入：

```text
$RUN_DIR/<exp_name>/
```

其中包含每个 kernel 的隔离目录和整体 JSON 汇总。

## 5. 多轮性能优化

```bash
python -m opt.simple_iter.main \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --top_name process_top \
  --model deepseek-v4-pro \
  --iterations 8 \
  --reasoning_effort high
```

主要参数：

| 参数 | 作用 |
|---|---|
| `--kernel_path` | 待优化代码路径 |
| `--top_name` | 原始 top function 名称 |
| `--iterations` | 模型—综合—反馈循环次数 |
| `--model` | 优化模型 |
| `--reasoning_effort` | 推理强度，具体值需由服务端支持 |
| `--output_dir` | 可选，指定独立输出目录 |
| `--no-gen_bench_prior` | 不自动生成 testbench；通常需配合已有配置使用 |

流程会对通过 csynth 和 testbench 的设计解析延迟与资源利用率，并将满足资源约束的最优候选写入最佳设计记录。

## 6. 查看最近一次输出

对于按日期创建目录的单次运行：

```bash
latest=$(ls -td "$RUN_DIR"/$(date +%Y%m%d)/* 2>/dev/null | head -n 1)
echo "$latest"
tail -n 120 "$latest/output.txt"
```

常见文件：

```text
output.txt
context_final.json
context_planning.json
context_refactoring.json
csim_*/refactor_code.cpp
csim_*/testbench.cpp
csynth_*/csynth/solution/syn/report/*_csynth.rpt
```

优化流程通常还会生成：

```text
round_0/
round_1/
...
best_design.json
```

具体文件名以当前代码输出为准。

## 7. 可选测试强度参数

仓库中还提供：

```text
--enable_tb_coverage_loop
--public_tb_rounds
--public_tb_target
--enable_hidden_tb_eval
--hidden_tb_rounds
--hidden_tb_trajectories
--hidden_tb_target
--golden_tb_cache_dir
--use_cached_tb_as_public
```

这些参数默认关闭。正式实验前应先阅读对应实现，并在小样例上单独验证覆盖率工具、缓存路径和 hidden TB 行为。

## 8. 当前不建议启用的功能

暂时不要给 `flow.new` 增加：

```text
--hetero_enabled
```

该选项依赖外部 HeteroRefactor、ROSE 和 EDG binary，当前不属于稳定主流程。

<!-- AGREFPP_UNIFIED_CLI:START -->
## 统一 CLI（Stage 1/2）

共享入口：

```text
python -m agrefactor.cli
```

### 最小 TaskSpec

`target` 和 `mode` 可以省略。当前默认：

```text
mode=refactor
profile=vitis-2023.2-default
toolchain=vitis_hls
toolchain_version=2023.2
device=xcu200-fsgd2104-2-e
clock_period_ns=5.0
compile_flags=-D XILINX
```

```json
{
  "task_id": "dfs-refactor",
  "kernel_path": "src/heterorefactor/dfs/kernel.cpp",
  "kernel_name": "process_top"
}
```

### TargetProfile 局部覆盖

```json
{
  "task_id": "dfs-refactor-250mhz",
  "kernel_path": "src/heterorefactor/dfs/kernel.cpp",
  "kernel_name": "process_top",
  "mode": "refactor",
  "target": {
    "toolchain_version": "2023.2",
    "device": "xcu200-fsgd2104-2-e",
    "clock_frequency_mhz": 250,
    "append_compile_flags": [
      "-I include"
    ]
  }
}
```

解析结果：

```text
clock_frequency_mhz=250 → clock_period_ns=4.0
compile_flags → 替换默认 flags
append_compile_flags → 在默认 flags 后追加
```

校验：

```bash
python -m agrefactor.cli validate-task task.json
```

Dry run：

```bash
python -m agrefactor.cli run task.json   --dry-run   --trace /tmp/trace.jsonl
```

真实 legacy refactor：

```bash
python -m agrefactor.cli run task.json   --legacy   --model deepseek-v4-flash   --base-url https://api.deepseek.com   --reasoning-effort low   --enable-testbench-repair   --max-testbench-repair-attempts 2   --max-retry-attempts 3   --output-dir "$RUN_DIR/my_run/legacy"   --trace "$RUN_DIR/my_run/trace.jsonl"   --run-id my-run   --debug
```

### 多 Vitis 版本：必须显式指定

机器只安装一个版本时，可以加载该版本环境并使用 PATH 中的 `vitis-run`。

机器安装多个版本时，必须协调：

1. `target.toolchain_version`；
2. `AGREFACTOR_VITIS_RUN`；
3. 建议 source 同一版本的 `settings64.sh`。

#### Vitis 2023.2

```bash
source /data/Xilinx/Vitis/2023.2/settings64.sh
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2023.2/bin/vitis-run
python -m agrefactor.cli run task-2023.2.json --legacy
```

#### Vitis 2024.1

```bash
source /data/Xilinx/Vitis/2024.1/settings64.sh
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run
python -m agrefactor.cli run task-2024.1.json --legacy
```

对应任务：

```json
{
  "task_id": "example-2024.1",
  "kernel_path": "kernel.cpp",
  "kernel_name": "kernel_top",
  "target": {
    "toolchain_version": "2024.1",
    "device": "xcu200-fsgd2104-2-e",
    "clock_period_ns": 4.0
  }
}
```

系统会对 selected executable 执行：

```bash
/path/to/vitis-run --version
```

任务要求 `2023.2`、实际选择 `2024.1` 时：

```text
status=mismatch
execution=blocked_before_csynth
```

不会静默使用错误版本。

不同版本顺序运行可使用单命令环境变量：

```bash
AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2023.2/bin/vitis-run python -m agrefactor.cli run task-2023.2.json --legacy

AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run python -m agrefactor.cli run task-2024.1.json --legacy
```

当前只有 Vitis 2023.2 经过真实 csynth 验收。其他版本必须单独验证 launcher、Tcl、器件和 report parser 兼容性。

### TargetProfile 运行证据

每个本地 csynth 目录保存：

```text
vitis.tcl
effective_target_profile.json
csynth_invocation.json
```

其中记录：

- effective profile；
- actual command；
- command source；
- resolved executable；
- requested/actual version；
- probe result；
- Tcl path；
- timeout；
- return code；
- execution status。

验收记录：[`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md)。

### 当前统一 CLI 边界

- `--dry-run`、`--legacy`、`--repair-aware` 三种执行模式显式互斥；
- `--legacy` 与 `--repair-aware` 当前正式支持 `mode=refactor`；
- `optimize/full` 的真实 Stage 3 adapter 尚未实现；
- compile、csynth、csim 和 aggregate tool hard budget 已接入共享 `BudgetManager`；
- 网络模型由用户固定选择，credential 只从指定环境变量读取；
- unknown token/cost 或 ownership 不能解释为零或自动归属。
<!-- AGREFPP_UNIFIED_CLI:END -->


<!-- AGREFPP_STAGE2_RUNTIME_API_STATUS:START -->
## 正式 Repair-aware CLI（Stage 2 已关闭）

### TaskSpec 输入

Repair-aware 模式要求原始代码、初始 candidate、Preflight testbench 和可选
Public/Hidden suites。相对路径按 TaskSpec 文件所在目录解析。

```json
{
  "task_id": "repair-example",
  "kernel_path": "original.cpp",
  "kernel_name": "top",
  "mode": "refactor",
  "testbench_path": "preflight.cpp",
  "test_suites": [
    {
      "suite_id": "public-main",
      "split": "public",
      "testbench_path": "public.cpp"
    },
    {
      "suite_id": "hidden-final",
      "split": "hidden",
      "testbench_path": "hidden.cpp"
    }
  ]
}
```

### 执行

```bash
export DEEPSEEK_API_KEY=...
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2023.2/bin/vitis-run

python -m agrefactor.cli run task.json \
  --repair-aware \
  --model deepseek-v4-flash \
  --model-family deepseek-v4 \
  --base-url https://api.deepseek.com \
  --api-key-env DEEPSEEK_API_KEY \
  --candidate-file candidate.cpp \
  --repair-work-dir "$WORK_DIR/repair-example" \
  --artifact-dir "$RUN_DIR/repair-example" \
  --max-candidate-repair-attempts 2 \
  --csynth-timelimit 300 \
  --csim-timelimit 60 \
  --run-id repair-example
```

多个 Public suite 存在时，必须显式提供唯一 prompt-facing public testbench：

```text
--prompt-public-testbench-file public_prompt.cpp
```

Hidden suite 参与最终 operator-side 验证，但 Hidden source、diagnostic 和 sentinel
不得进入模型 Prompt、普通 result 或 agent-safe trace。

### 输出 artifacts

```text
artifact-dir/
├── run_result.json
├── run_artifact_manifest.json
├── trace.jsonl
└── refactor/
    ├── orchestration_result.json
    ├── final_candidate.cpp
    ├── artifact_manifest.json
    └── repair_artifacts/
        ├── repair_run.json
        ├── attempts/
        └── artifact_manifest.json
```

一次 `UnifiedRunner` run 共享同一个 `BudgetManager` 和 `TraceRecorder`。
Changed candidate 必须从 Preflight 重新进入验证。模型回复通过结构 contract
不代表编译、链接、功能或综合必然通过；这些仍由真实工具验证。

### 已验证边界

```text
Vitis HLS=2023.2
formal CLI=true
real network-model smoke=1
multi-type baseline full chains=7/7
fault/ownership/Hidden matrix=9/9
independent labels=16/16
full unittest=836/836
```

这些结果不代表任意 kernel、任意 Vitis 版本、自动模型路由或 Stage 3 优化已经完成。
关闭证据见
[`stage2_closure_acceptance.md`](stage2_closure_acceptance.md)。
<!-- AGREFPP_STAGE2_RUNTIME_API_STATUS:END -->
