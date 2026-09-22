# V2.3 Inheritance-First Stage II-B Checkpoint

日期：2026-09-22  
状态：完成并通过独立审计；进入阶段 III 前的干净 checkpoint  
分支：`research-roadmap-v2.3`

## 范围

阶段 II-B 独立覆盖 `linkedlist` 和 `strassen_break`，未回写阶段 II 四案例证据。
两例都使用正式 `refactor` 入口、Vitis HLS 2023.2、provided public differential oracle、
Hidden `none`，并关闭 R2-R4、经验系统、`optimize` 和 `full`。

## 结果

两例 source oracle 和 public oracle 均通过两次稳定性运行，mismatch stub 被正确拒绝。
两例 source baseline 都是 host oracle 与 CSIM 通过、CSYNTH 因递归失败、COSIM 未运行。

| 案例 | source baseline | 普通 refactor | 正式验证 |
| --- | --- | --- | --- |
| `linkedlist` | CSYNTH failed | succeeded | CSIM/CSYNTH/COSIM passed |
| `strassen_break` | CSYNTH failed | succeeded | CSIM/CSYNTH/COSIM passed |

两例 Candidate 均与 source hash 不同，execution identity 为 accepted-ready，repair attempt 为 0。
最终统计为 success 2、regression 0、blocked 0。

## 保留的失败证据

- preflight v1 暴露 `linkedlist` oracle 只覆盖排序前缀而未覆盖原 top 四段输出；修订后的完整
  reference oracle 在 v2/v3/v6 通过，旧结果保留。
- `strassen_break` 首次命令在 Provider/Vitis 调用前由 COSIM ABI 合同拒绝，原因是 depth key
  写成 `n/l/m` 而正式 ABI 为 `np/lp/mp`；修订后 v2 正式运行通过。拒绝记录保存在
  `/data/agrefactor_runs/inheritance_stage2b_refactor_strassen_break_v1_contract_rejected`。
- 一次 Windows-to-SSH CRLF 导致输出目录尾部出现 `\r`；该无效命令工件不进入成功证据，
  未产生 Provider/Vitis 调用。

这些修订只涉及 oracle 完整性、route namespace 和 ABI 合同绑定，没有按算法、路径或 HLS
错误编号修改产品 refactor 行为。

## 预算和证据

- 本轮 Provider：`18 / 600`；
- 本轮 Vitis launches：`14 / 600`，包含两次完整 source baseline；
- 历史阶段 II：Provider 60、Vitis 23，单独报告；
- seal/audit：Provider 0、Vitis 0、git history mutation 0；
- campaign manifest SHA-256：`78979c2d3dd438a98f91c830eef2f143b4f5a97010eba86513eeb514c45af2e8`；
- audit result SHA-256：`b2122a8dd2b1c7be2f9f9fb875b3b8b9745b3e12f65d911cc6f3f724af9cb18e`；
- checkpoint root：`/data/agrefactor_runs/inheritance_stage2b_checkpoint_v1`；
- independent audit：passed，critical findings 0。

阶段 II-B 到此冻结。下一步按既定路线进入阶段 III 内部 `src/` 继承测试；仍不进入阶段 IV，
不启用 R2-R4 或经验系统。
