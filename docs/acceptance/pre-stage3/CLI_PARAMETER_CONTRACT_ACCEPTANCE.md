
# Pre-Stage-3 CLI Parameter Contract Acceptance

## Scope

本包只完善 Stage 3 前普通 CLI 参数合同、兼容边界和文档，不开始 Stage 3 优化器。

## Accepted changes

- normal source reasoning default=`medium`；
- Generic implicit default 不阻断模型，具体 reasoning 映射延期；
- Public `none` 从普通 CLI 移除；
- Public/Hidden rounds 与 trajectories 独立，范围 `1..20`；
- repair ceiling=`20`；
- source hard-budget defaults/ceilings 更新；
- model request timeout=`240s`；
- normal CSIM/CSYNTH timeout=`120s/600s`；
- advanced repair-aware timeout 同步；
- exact persistent `--output-dir`；
- `--replace-compile-flag` 替代公开的旧名称；
- Token/Cost 明确为 observed-only、does not stop execution；
- DeepSeek credential 默认与文档统一；
- deferred capability backlog 已建立。

## Safety boundaries

```text
MODEL_API_CALLED=false
REAL_VITIS_RUN=false
STAGE3_STARTED=false
```

验证使用确定性单元测试和 CLI/文档静态审计。测试中的模拟 synthesis 输出不代表真实 Vitis 调用。

## Validation

```text
FULL_UNITTEST_COUNT=1500
VALIDATED_AT_UTC=2026-07-26T18:38:22Z
CLI_PARAMETER_CONTRACT=passed
PRE_STAGE3_CLOSED=true
STAGE3_STARTED=false
NEXT_STEP=STAGE3_PLANNING
```
