---
phase: 15-adaptive-hierarchical-retrieval-and-leaf-evidence-safety
source: approved-v0.8-requirements
requirements: [V08-RETR-01, V08-RETR-02, V08-RETR-03, V08-RETR-04, V08-RETR-05]
planning_mode: non-interactive
execution_authorized: false
---

# Phase 15 Context

## Outcome

提供一个默认关闭、显式 candidate-version、只读的离线分层检索实验。冻结问题由确定性 router 选择 local、arc、global 或 mixed 起始层，检索可逐层下钻或在上层缺失/partial/误路由时折叠到可见下层及 Phase 07 leaf/raw evidence；最终 citation 只能来自服务端重新切片并重验 offset/hash 的原文。

## Locked Decisions

- Router 完全确定性，不调用模型，不从未读候选或未来元数据推断 intent；相同 query、selection/cutoff、policy 和 candidate version 必须得到相同 route 与 reason codes。
- 起始层只有 `local`、`arc`、`global`、`mixed`。route 必须实际改变候选集合和 traversal path，不能只是把上层摘要附加到同一个 leaf top-k。
- 所有数据库读取均先应用 owner、novel、显式 candidate version、source snapshot、hierarchy build 和 persisted reading cutoff，再排序、计数、打分、下钻、缓存或生成 trace；禁止“先全量检索再隐藏”。
- Global/Arc/Volume 只能通过合法 edge 下钻到 Chapter State，再由 claim source links 到 Phase 07 evidence leaf。上层缺失、partial、不可见或下钻失败时使用 collapsed visible-level 或 raw/leaf fallback，并记录稳定、非泄漏的 reason code。
- 上层 typed claim、display label、summary、similarity/routing score、stored excerpt、聊天文本都不是 citation。最终 citation 必须重新加载 authoritative `Chapter.content`，用 Unicode code-point offsets 重切，并同时验证 leaf、source link、source snapshot、content hash 和 candidate manifest lineage。
- 每一步都重复执行 scope 与 spoiler gate：candidate selection、routing feature extraction、level candidate query、edge traversal、claim expansion、leaf resolution、rerank、cache lookup/write、trace/manifest serialization。
- 外部可见 trace 只包含可见集合派生的 route、稳定 reason codes、可见候选/预算 omitted counts、fallback 和安全 source status。未读节点标题、ID、数量、分数、存在性、内部 trace、cache key 或“因未来内容被隐藏”的状态不得出现在结果或日志。
- 实验使用显式 `version_id`，不创建/解析 active pointer，不执行 promotion，不注册生产 Reader Chat consumer，不改变 Reader Chat 请求、上下文 manifest、citation 或响应。
- Phase 15 是离线 retrieval experiment，不新增产品 UI，不把 candidate 连接到生产 API。入口默认关闭，只有固定命令和显式 feature flag 才可运行。

## Scope

### In scope

- Strict retrieval request/result/trace/citation/manifest contracts and deterministic canonical hashing.
- Deterministic query router and cutoff-first visible candidate loaders for local/arc/global/mixed.
- Multi-level descent, collapsed-level recovery, Phase 07 raw fallback and deterministic rerank/budget accounting.
- Server-side Unicode code-point re-slice/hash validation for every final citation.
- Offline CLI/experiment seam, audit-safe trace and cache-key isolation.
- Unit, PostgreSQL integration and adversarial tests for route determinism, scope, spoiler metadata, citation integrity and Reader Chat no-cutover.

### Out of scope

- 构建或重新分析候选内容、provider calls、Chapter State/Arc/Global 生成（Phase 14）。
- dirty closure/carry-forward（Phase 16）与单书质量资格/阈值结论（Phase 17）。
- Reader Chat、timeline、relationship、clue 或 search production consumer cutover。
- promotion、active pointer、自动 current-version resolution、产品 UI、新的向量数据库或编排框架。
- 用上层摘要直接回答，或把上层主张/相似度当作最终证据。

## Verification Standard

- 固定 route matrix 证明四种起始层及 stable reason codes，并证明不同 route 加载不同的 visible node sets/traversal paths。
- PostgreSQL 测试证明所有 SQL 在 cutoff/scope 之后才计数/排序，跨 owner/novel/version/build/snapshot 行在任何 trace、cache 或结果中均不可观察。
- 缺失/partial upper layer、断 edge、空 visible set、预算耗尽和误路由均产生确定性 collapsed/raw fallback，而不是空结果假成功或越界扩张。
- 每条 citation 通过 fresh session 重新读取 Chapter 和 frozen leaf/link，重算 Unicode offsets/hash；任何 stale/tampered/wide/summary-only evidence 都被丢弃或使运行 blocked。
- 对抗测试比较 future-data before/after 结果，要求 route、trace、counts、scores、status、manifest 和公开日志 byte-identical。
- feature flag 默认关闭；真实 Reader Chat API/context/output 在 Phase 15 文件存在和实验运行前后 byte-equivalent，且无新 production route/pointer/provider capability。

## Execution Gate

这些文件仅为规划资产。本轮不得执行 Phase 15；只有用户另行授权，且 Phase 13/14 已完成并独立验证通过后，才能开始实现或运行离线实验。

---

*Context derived non-interactively from the user-approved v0.8 A.A requirements and explicit planning-only instruction.*
