# Stage 4 — Memory Applicability Gate

## 1. 目标

解决历史经验检索造成的噪声、错误迁移和上下文污染，使系统能够判断一条经验是否适用于当前错误、kernel、接口、版本和预算。

## 2. 进入条件

- Stage 2 已有结构化错误和状态；
- Stage 3 已有候选、动作、PPA 与成本 artifact；
- Memory schema 能关联真实验证结果；
- 用户 Memory 控制边界已固定。

## 3. Memory 模式

```text
off
gated
always
```

`off` 完全禁用；`gated` 检索后评估；`always` 直接注入，主要用于对照。

## 4. 经验字段

至少包括 stage、source_profile、target_profile、code_features、interface_features、error_signature、action、hypothesis、preconditions、avoid_when、verification、ppa_delta、cost 和 outcome。

## 5. Gate 输入

当前 stage、error signature、kernel/loop/memory/interface features、source/target version、part、clock、resource limits、历史验证强度、历史 PPA、历史失败和当前预算。

## 6. Gate 输出

至少包括 applicability_score、decision、reasons、risk、expected_value 和 selected_memory_ids。

## 7. Retrieval Abstention

置信度不足、证据冲突、版本不匹配或预算价值太低时，不注入任何经验。Abstention 是正常结果，不是检索失败。

## 8. 负面经验

保存已验证失败动作、禁用条件、负迁移案例、PPA 明显退化、correctness 被破坏和预算成本过高但无收益的案例。

## 9. 消融

固定其他条件，比较 off、always、gated 的 success rate、negative transfer rate、abstention rate、accepted memory success、token/tool cost 和 PPA。

## 10. 完成标准

- schema；
- off/gated/always；
- score/reasons；
- abstention；
- 正负经验；
- 多任务实验；
- 证明 Gate 在至少部分案例减少负迁移。
