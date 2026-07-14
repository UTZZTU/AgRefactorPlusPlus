# Stage 5 — Version-Aware Vitis HLS Migration

## 1. 不可删除目标

正式支持：

```text
旧版本/源版本 Vitis HLS
→ 目标版本 Vitis HLS
```

当前普通 C/C++→2023.2 重构不能替代本阶段。

## 2. 双模式

Mode A：普通 C/C++ 或不可综合代码在 TargetProfile 下修复、验证和优化。

Mode B：已有 HLS 代码在 SourceProfile 下建立 baseline，再迁移到 TargetProfile。

## 3. TaskSpec 扩展

建议增加 `mode=migrate`、`source_profile`、`target_profile`、testbench 和 migration constraints。

## 4. Source baseline

必须先证明输入在源环境成立，包括 source compile、source public test/csim、source csynth、interface、directives、latency/II/resources 和 source reports。

没有 source baseline，不能把目标失败可靠归因为版本变化。

## 5. Target direct run

先不修改代码，直接在 TargetProfile 下执行，得到真实差异证据。

## 6. 错误分类

至少区分 deprecated pragma、removed API、directive syntax、library/type incompatibility、interface semantic change、Tcl change、report schema、scheduling/default behavior、device/clock difference、ordinary synthesis error 和 testbench/environment error。

## 7. 迁移知识

经验需带 source version、target version、code/interface pattern、precondition、transformation、avoid condition、verification、PPA delta 和 cost，并经过 Stage 4 Gate。

## 8. 安全转换

使用 Stage 3 的 hypothesis、checkpoint、rollback、best_correct、budget 和 candidate lineage。

## 9. 双版本验证

迁移成功至少要求 source behavior ≈ target migrated behavior，并比较 latency、II、clock、LUT、FF、BRAM 和 DSP。

## 10. Migration Report

至少输出 source/target profile、source baseline、target direct failure、version-related classification、applied/rejected rules、correctness、PPA comparison、budget 和 limitations。

## 11. 不做

- 自动识别源版本；
- 任意版本对；
- 一次覆盖全部历史；
- 平台/运行时迁移；
- repository-level migration；
- 任意程序形式证明。

## 12. 完成标准

- 第二个真实 Vitis Profile；
- 双版本实际可运行；
- 至少一组真实 source→target；
- source baseline 成立；
- target correctness 成立；
- migration report；
- 普通错误与版本错误可区分。
