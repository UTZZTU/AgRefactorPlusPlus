<!-- V2.3_SUPERSESSION_NOTICE:BEGIN -->
> **V2.3 状态说明（2026-08-26）：** 本文件保留为 V2.2 R0 决策历史证据。当前执行路线已升级为 `docs/roadmap/RESEARCH_ROADMAP_V2_3.md`，当前分支为 `research-roadmap-v2.3`。本文件中的 governing-route、R1 未开始和 R0-R6 执行指针均不再作为当前执行命令；请以 V2.3 standalone 路线和其 authority index 为准。
<!-- V2.3_SUPERSESSION_NOTICE:END -->

# R0 V2.2 Route Decision

> Decision date: 2026-08-25  
> Repository branch: `stage2-general-feedback`  
> Behavior baseline: `5ef7fa9a6011534362a2094e159eee75c672619c`  
> Server deterministic regression: `2335/2335`  
> Status: project-owner approved; R0 document synchronization independently accepted; effective with the R0 documentation commit.

## Decision

`RESEARCH_ROADMAP_V2_2.md` is the governing research route after this R0
document synchronization is independently reviewed and committed. Historical
roadmaps and acceptance records remain in the repository as evidence, but they
must not override the V2.2 capability classification or R0–R6 order.

## Frozen direction

- The paper line is evidence-gated open-world diagnosis, verified continual
  diagnostic memory, and safe repair.
- The validation FSM, recovery policy, real validators, and evidence auditor
  remain deterministic authorities. AI may advise; it may not alter the FSM or
  declare success.
- Vitis HLS 2023.2 is the primary empirical environment. No cross-version
  generalization is claimed.
- TargetProfile is retained as empirical infrastructure. Model Registry,
  layered prompts, BudgetManager, trace, and execution identity are engineering
  infrastructure rather than standalone paper contributions.
- The current three-level optimizer remains the implemented `safe-v1`
  secondary capability. `dynamic-v1` is not a current prerequisite.
- HLS version migration, repository migration, automatic model routing,
  model-weight continual learning, and broader toolchain generalization are
  future work.
- Existing deterministic Candidate and Testbench repair paths are retained.
  Any new LLM-advisory automatic repair authority is Candidate-only in v1.
- Hidden evidence is terminal and is never exposed to diagnosis, repair, or
  memory content.
- Negative results, false repair, negative transfer, abstention, inconclusive
  infrastructure outcomes, and invalid evidence must be reported.

## Logical route

```text
R0  authority and document reconciliation
R1  deterministic repair/evidence closure and real failure corpus
R2  provider-backed shadow diagnostic advisor
R3  conditional positive/negative memory and shadow applicability gate
R4  gate-authorized bounded Candidate repair
R5  continual governance, temporal evaluation, and ablation
R6  frozen experiments, paper, and release
```

R0 is document-only. This decision does not authorize R1 implementation.

## R0 acceptance boundary

The execution package may write documentation and state files only. It may not
modify product Python code, run a model provider, invoke Vitis, commit, push, or
self-accept. The result archive must be reviewed independently before the
documentation changes are committed.

## Independent acceptance record

The R0 execution archive
`agrefactor_r0_document_authority_sync_v1_20260824T172726Z_2546041.tar.gz`
was independently reviewed on 2026-08-25. Its SHA256 is
`7608be4b21ff2ceade20040caee255024a56666b2711b4a186b4c42360c13674`.

The review verified the sidecar, archive path safety, clean `5ef7fa9` preflight,
all baseline and publication hashes, the exact seven-path boundary, isolated
patch replay, no product-source mutation, no provider or Vitis execution, no
Git commit/push, and `R1_STARTED=false`. The R0 document synchronization is
accepted. This external decision does not authorize or implement R1.
