# Stage 1 TargetProfile Acceptance Record

## 1. Scope

本文档验收 **Stage 1 TargetProfile 本地执行核心**：

- TaskSpec target 默认值与局部覆盖；
- legacy flow target propagation；
- target-aware Vitis Tcl；
- actual `vitis-run` executable resolution；
- requested/actual Vitis version verification；
- mismatch-before-csynth failure；
- effective profile 与 invocation evidence；
- 一次真实 Vitis 2023.2 csynth。

不声明：

- 任意 Vitis 版本全面支持；
- 任意 source→target 版本迁移；
- 任意器件或任意 kernel；
- Stage 1 整体关闭。

## 2. Accepted code revision

- Branch: `stage1-target-profile-forwarding`
- TargetProfile code commit: `717fdef83a2ac96d3636461df7c733a85998ad3b`
- Validation date: `2026-07-15`
- Deterministic suite: **153/153 passed**

## 3. Real Vitis validation

- Run directory: `/data/agrefactor_runs/stage1_target_profile_real_vitis_20260715_141118`
- Final result: `REAL_VITIS_SMOKE_PASSED=1`
- Actual executable: `/data/Xilinx/Vitis/2023.2/bin/vitis-run`
- Requested version: `2023.2`
- Actual version: `2023.2`
- Version status: `matched`
- Device: `xcu200-fsgd2104-2-e`
- Requested clock: `4.0 ns`
- Estimated clock: `2.920 ns`
- Estimated Fmax: `342.47 MHz`
- Compile flag: `-D STAGE1_TARGET_PROFILE_REAL_SMOKE=1`
- csynth return code: `0`
- timeout: `false`

测试 kernel 包含：

```cpp
#ifndef STAGE1_TARGET_PROFILE_REAL_SMOKE
#error "TargetProfile compile flag was not forwarded"
#endif
```

因此真实综合成功同时证明 compile flag 到达 Vitis 编译阶段，而不只是存在于 JSON 或 Tcl 文本。

## 4. Generated Tcl evidence

```tcl
open_project csynth
set_top "target_profile_smoke"
add_files "target_profile_smoke.cpp" -cflags "-D XILINX -D STAGE1_TARGET_PROFILE_REAL_SMOKE=1"
open_solution -flow_target vitis solution
set_part "xcu200-fsgd2104-2-e"
create_clock -period 4.0 -name default
csynth_design
close_project
exit
```

## 5. Persisted evidence

- `vitis.tcl`
- `effective_target_profile.json`
- `csynth_invocation.json`
- `real_vitis_smoke_result.json`
- `real_vitis_validation.json`
- `real_vitis_smoke.log`
- `csynth/solution/solution.log`
- `csynth/solution/syn/report/target_profile_smoke_csynth.rpt`

## 6. Multi-version explicit selection

多版本机器不要只依赖 PATH 顺序。必须同时声明 requested version 和实际 launcher。

Vitis 2024.1 示例：

```bash
source /data/Xilinx/Vitis/2024.1/settings64.sh
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run
python -m agrefactor.cli run task-2024.1.json --legacy
```

任务片段：

```json
{
  "target": {
    "toolchain_version": "2024.1",
    "device": "xcu200-fsgd2104-2-e",
    "clock_period_ns": 4.0
  }
}
```

系统探测：

```bash
/data/Xilinx/Vitis/2024.1/bin/vitis-run --version
```

requested 与 actual 不一致时，csynth 前阻断。

不同版本顺序运行也可以使用单命令环境变量：

```bash
AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2023.2/bin/vitis-run python -m agrefactor.cli run task-2023.2.json --legacy

AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run python -m agrefactor.cli run task-2024.1.json --legacy
```

## 7. Explicit limitations

1. 本记录只有 Vitis 2023.2 真实 csynth。
2. `AGREFACTOR_VITIS_RUN` 当前是进程级环境变量。
3. executable/settings 尚未成为 stable named profile 的自包含字段。
4. 旧版仅有 `vitis_hls` launcher 的情况尚未适配。
5. platform、resource limits、report parser profile 尚未完整控制。
6. effective values 已持久化，但 per-field provenance 尚未完成。
7. remote synthesis 只允许 default profile，非默认覆盖会被拒绝。
8. 一个构造 smoke kernel 不代表普适兼容性。

## 8. Stage status

- TargetProfile local execution core: **accepted**
- Stage 1 overall: **not closed**
- Next blocking item: **hard tool budgeting**
