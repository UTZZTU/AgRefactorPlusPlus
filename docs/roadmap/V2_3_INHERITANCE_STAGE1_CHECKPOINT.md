# V2.3 Inheritance-First Stage I Checkpoint

日期：2026-09-21
状态：完成，等待项目所有者审阅后才能进入阶段 II
分支：`research-roadmap-v2.3`

## 范围

本 checkpoint 只完成零调用 `src/` 盘点、重复关系审计和 HeteroRefactor 首批 cohort 冻结。没有运行普通 `refactor`，没有生成或执行 testbench，没有启动 R2-R4，也没有使用经验系统。

服务器环境由以下命令初始化：

```bash
source /data/agrefactorpp_env.sh
```

## 盘点结果

- C/C++/头文件：135；
- 覆盖目录：45；
- 独立且 `inheritance_testable` 的设计：62；
- support 文件：65；
- 明确 copy：6；
- blocked：2；
- 当前 `strict_efficacy_eligible`：0。严格资格为 0 是因为 oracle、TargetProfile 和正式验证尚未冻结，不表示这些源码不能进入普通 refactor 测试。

独立设计按内部来源分布：

- `app`：13；
- `c2hlsc`：9；
- `heterorefactor`：6；
- `hlsrewritter`：20；
- `leetcode`：10；
- `opt`：4。

两个 blocked 文件是：

- `src/c2hlsc/streaming_example.c`：源码包含异常 Unicode 运算符、非法声明和两个同名 `fir`，无法确定一个合法 top；
- `src/hlsrewritter/DA_E2_binary_tree/kernel.cpp`：没有可确定的 top 定义。该目录登记的实际设计源码是 `kernel.h`，它已单独列为可测试样例。

旧 `src/info.json` 还登记了不存在的 `src/c2hlsc/runs/kernel.cpp`。新清单保留该事实作为 warning，不创建虚假样例。

## 去重与隔离

- D0 完全相同关系：7 对；
- D1 词法相同关系：9 对；
- D2 结构近重复关系：0 对；
- D3 同族样例继续保留，仅用于报告和后续 history/future 隔离。

独立审计重新计算了 D0-D3，没有仅信任生成器输出。缺少 testbench 或 Tcl 没有导致任何独立设计被排除。

## 首批 HeteroRefactor cohort

冻结但尚未授权执行的四个样例：

1. `ahocorasick`：动态 trie 和状态机遍历；
2. `dfs`：动态二叉树构造和递归；
3. `mergesort`：递归链表排序；
4. `strassen`：递归矩阵分配与乘法。

`linkedlist` 和 `strassen_break` 仍是可测试样例，留到后续批次。四个首批样例都没有已有 testbench/Tcl，阶段 II 必须先通过现有产品能力生成并冻结 public testbench、输入和 source-reference oracle，审核通过后才能运行。

## 身份与审计

- inventory SHA-256：`135bc61e6277e738a6aa3d61b96406bf5718d83416666187ecbf30629103b081`；
- cohort SHA-256：`b808132a1fbf4f3b55e5b098722ae5ea95290b68c9133c13e2c0732aa570cb92`；
- audit SHA-256：`95c642afb93b3498b6f1ffb035e13e27cfffd6873612639457e754ff42514d5c`；
- audit：passed，0 failure，1 warning；
- Provider calls：0；
- Vitis launches：0；
- R2-R4：关闭；
- memory：关闭。

下一步只能在项目所有者审阅后进入阶段 II；本 checkpoint 本身不授权任何真实运行。
