# Stage 1 — Shared Infrastructure

## 目标

为 refactor、optimizer、Memory、budget 与 migration 建立统一、可测试、可追踪的共享架构。

## 已实现

- TaskSpec、TargetProfile、RunMode；
- provider-neutral model request/response、Registry、OpenAI-compatible Provider；
- UnifiedRunner、Phase/RunResult、RunContext；
- TraceRecorder、BudgetManager core；
- `python -m agrefactor.cli validate-task/run`；
- Legacy Refactor Adapter；
- known AutoGen + repair usage 合并与 artifact 去重；
- CLI/adapter/module regression tests。

## 尚未封口

### TargetProfile

字段已存在，但尚未完整下传实际 legacy Vitis flow。必须完成 settings/tool、part、clock、flags、Tcl、effective profile evidence 与 mismatch tests。

### Tool budget

必须分别记录并限制 compile、public test、csim、csynth、cosim；在调用前检查预算，在未来优化流程中预算耗尽时安全返回 best_correct。

### 配置文件

添加稳定 target/model 示例，不提交 API secret。

## 完成标准

TargetProfile 必须控制一次真实 Vitis 运行，BudgetManager 必须控制真实工具调用；类或字段存在不等于 Stage 1 完成。
