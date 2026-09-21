# V2.3 Inheritance-First Execution Route

日期：2026-09-21
状态：项目所有者提出，待确认后执行
适用分支：`research-roadmap-v2.3`

## 1. 目的与范围调整

当前暂停继续执行原 R5.1 P0-P9 路线。已完成的 P0-P5 证据不删除、不重写、不重新解释；它们保留为严格可审计 cohort 的历史证据。本路线解决一个不同且更基础的问题：

> AgRefactor++ 的普通 `refactor` 是否真正继承了原 AgRefactor 已有的广泛案例能力？

本轮优先测量产品继承覆盖和普通 refactor 可用性，不先做论文级 memory efficacy campaign。R2-R5 在第一阶段关闭；只有普通 refactor 基线建立后，才对同一批案例开启内部 R2-R5 实验。正式产品入口仍只有 `refactor`、`optimize`、`full`，不新增 CLI、Candidate repair 或 Vitis runner。

## 2. 数据范围：优先使用仓库现有 src/

本轮不先继续引入 HLS-Eval、HLSPilot、HLSFactory 或其他外部数据。第一数据源是当前仓库 `src/` 下的 AgRefactor/原项目遗留案例；外部论文或 GitHub 案例推迟到内部案例完成一轮后。

### 2.1 样例判定

一个目录中的 C/C++ 文件只要能够识别为独立设计源码，就应进入普通 `refactor` 测试
候选。最低条件是：

- 存在可解析的 C/C++ design source；
- 存在明确 top，或能够由确定性分析可靠识别 top；
- 源码依赖能够从案例目录、仓库公共依赖或已记录的 include path 中解析。

预先存在的 public/hidden testbench 和 `vitis.tcl` 都不是进入候选池的必要条件。当前
source-only 产品流程应使用已有测试生成能力生成 testbench，并由统一 Vitis 2023.2
runner 生成和执行所需命令；不得因缺少数据集自带 Tcl 而排除案例。

以下文件不能仅凭扩展名直接当作独立样例：公共头文件、辅助库、模板、生成代码；只承担
host/test 驱动作用且没有独立设计 top 的 `main.cpp` 或 test utility；第三方依赖、工具
脚本和文档示例；同一实现的复制、格式变体或仅改变 top 名称的副本。缺少预置 testbench
或 `vitis.tcl` 本身不是排除理由。

分类器必须输出每个目录的 `sample / support / generated / duplicate / blocked` 状态、来源路径、source/test/top/Tcl 文件、许可和排除理由。分类阶段零 Provider、零 Vitis。

### 2.2 不把“可严格统计”与“可先运行”混为一谈

候选分为两个状态：

- `inheritance_testable`：具备 design source、top 和可解析依赖，能够通过当前 source-only
  `refactor` 入口尝试运行；允许由产品生成 tests，不要求预置 harness 或 Tcl；
- `strict_efficacy_eligible`：身份、oracle、TargetProfile、history/future 和审计合同全部完整，可进入正式 efficacy 统计。

缺少预置 testbench、Tcl 或严格实验合同不等于不测试；它只限制最终结论类型。生成的
testbench 必须在各实验臂运行前冻结并经过 oracle/preflight 审核，才能使案例进一步成为
`strict_efficacy_eligible`。测试生成失败、adapter 或工具链阻塞必须单独报告，不能记为
Candidate 或模型修复失败。

## 3. 阶段 I：内部样例盘点与冻结

1. 扫描 `src/`，建立目录级和文件级 manifest。
2. 识别 HeteroRefactor、HLSRewriter、C2HLSC 等目录及其真实样例边界。
3. 按 D0-D3 去重，但不因语义同族而删除所有重复：同族样例保留为覆盖样本，报告时不冒充独立来源。
4. 为每个候选绑定 source、top、依赖、许可证和 source-only 运行入口；若存在原始
   testbench/Tcl 则作为 provenance 记录，若不存在则记录产品生成 testbench 的冻结身份。
5. 生成零调用独立审计报告后冻结第一批 HeteroRefactor cohort。

退出条件：每个 `src/` 目录都有明确状态；没有把支持文件误当样例；所有排除都有理由。

## 4. 阶段 II：HeteroRefactor 普通 refactor 基线

第一批只使用 `src/heterorefactor/` 中确认的真实样例，R2-R5 全部关闭。每个案例按相同入口、相同 target、相同 public oracle 执行普通 `refactor`，记录：

- source baseline 是否通过；
- Candidate 是否发生变化；
- compile、CSIM、CSYNTH、COSIM 和最终正式验证状态；
- Provider/Vitis 调用数、失败阶段和工具身份；
- 是否属于 adapter、环境、oracle、模型生成或产品流程问题。

必须保留 source baseline，避免把“原代码本来就能过”计为 refactor 成功。普通 refactor 的成功定义是：原始 source 确有需要处理的失败，Candidate 经过完整正式验证、语义保持、身份和审计全部通过。

第一批不追求一次跑完所有目录；先运行小批量并查看通过率。若简单 HeteroRefactor 案例通过率明显低于预期，先停止扩展，修复通用产品缺陷并用相邻案例回归。

## 5. 阶段 III：按 src/目录扩展内部继承测试

HeteroRefactor 基线稳定后，依次扩展其他内部目录。每一批都保持：R2-R5 关闭；使用现有正式入口和验证器；不修改原始 testbench、target 或 hidden 资产；不按目录名、文件名或 HLS 错误编号添加特判；失败先分类，只有通用缺陷在多个独立案例复现后才修复。

批次顺序由样例可运行性和覆盖面决定，而不是由预计容易成功的案例决定。每批输出继承覆盖率、成功率、回归率、blocked/infrastructure 比例和 failure-family 分布。

## 6. 阶段 IV：原版 AgRefactor 对照

从仓库早期提交中定位仅包含原 AgRefactor 的可复现实验版本，固定 commit、环境、模型配置、prompt、预算和工具链。不得把当前 AgRefactor++ 的修改带入原版对照。

对同一内部 cohort、同一输入和同一 public oracle 分别运行：

- 原版 AgRefactor，R2-R5/持续记忆关闭；
- AgRefactor++ 普通 `refactor`，R2-R5/持续记忆关闭。

比较必须至少包括：完整验证通过率、真正 refactor lift、regression、失败阶段、成本和可复现性。AgRefactor++ 的目标不是在不公平条件下“必然高于”原版，而是不能在相同合同和环境下无故显著退化；任何差异都要定位到产品流程、模型配置、适配器或工具链。

## 7. 阶段 V：R2-R5 开启后的配对重跑

只对阶段 II/III 中已经完成普通 baseline 的案例开启内部 R2-R5。使用相同 source、Candidate 起点、testbench、target、model identity 和预算，至少形成：

- `B`：普通 refactor，R2-R5 关闭；
- `D`：R2-R4 开启，memory none；
- `M-R`：已有 Trusted repair memory 时开启；没有合格 memory 时明确记为 unavailable，不伪造 memory 结果。

对普通 baseline 失败案例重点观察：R2 是否产生合法 agent-safe 证据、R4 是否安全授权、Candidate-only 修复是否通过正式验证、失败是否属于真实 ambiguity。任何拒答都必须区分真实证据不足与 parser/evidence contract/product 缺陷。

原版 AgRefactor 的“记忆开启”对照只有在原版确实存在可重建的 memory 开关和相同语义时才执行；否则报告 `not comparable`，不人为给原版补一套新机制。

## 8. 阶段 VI：内部完成后再扩展外部案例

只有内部 `src/` 样例完成一轮继承测试、失败分类和通用修复后，才引入其他论文或开源 GitHub 项目案例。外部案例仍需许可、commit、去重、oracle 和 adapter 审计；不得用外部新样例替代内部继承覆盖的缺口。

## 9. 失败修复与验收原则

- 普通 refactor 失败不能直接归因于 R2-R5；先看 R2-R5 是否关闭及失败阶段。
- adapter/oracle/toolchain 失败不计入模型能力失败，但必须修复或明确排除。
- raw source 已通过而 Candidate 失败，记为 regression/unnecessary rewrite，并要求回退或保留原始正确 Candidate 的通用策略。
- 只修复跨至少两个独立案例复现的通用缺陷；不为单个 benchmark 或日志编号硬编码。
- 所有实验保存不可变 manifest、调用计数、日志哈希和独立审计；旧 P0-P5 证据不回写。

## 10. 暂停点与下一步

当前状态：原 R5.1 P0-P9 暂停；R5 未接受；R6 未启动；现有 P0-P5 证据保留。

下一实际动作不是调用 Provider/Vitis，而是执行阶段 I 的零调用 `src/` 样例分类和 inheritance manifest 审计。阶段 I 通过后，才授权 HeteroRefactor 小批量普通 refactor 运行。预算必须为新路线重新预留；不得静默把旧 P5 剩余预算当作无限额度。
