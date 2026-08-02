# Phase 24: 存储一致性与检索统一 - Context

**Gathered:** 2026-07-27
**Status:** Complete for the authorized local scope; PR #23 remains unmerged and was not used as a merge source.

<domain>
## Phase Boundary

完成 raw chunk / Narrative Unit / Narrative Memory 的生产检索边界与降级契约收口，
补齐 Reader Chat 的来源优先级共享契约，并证明 Neo4j 仅作为 accepted PostgreSQL
事实的可选只读投影；不启用 Narrative Memory production routing、promotion 或 active pointer。

</domain>

<decisions>
## Implementation Decisions

### Production retrieval boundary
- `chunks` 与 `units` 保持 enabled；`narrative_memory` 保持 disabled，直到明确的新授权阶段。
- Reader Chat 不消费 NM；所有最终证据继续回落到带 lineage 的原文叶子证据。
- Units 不可用时必须返回 raw chunk fallback 与明确原因，不能伪造“无结果”。

### Shared source-priority contract
- `selection > hierarchy > knowledge > timeline > relationship_observation` 的既有优先级保持不变。
- Reader Chat 与生产检索层注册表使用共享契约，避免两个消费者各自复制并漂移。

### Projection safety
- Neo4j/其他 graph projection 只能读取 accepted PostgreSQL rows，写入失败不得改变 PostgreSQL acceptance/status。
- Projection manifest 必须稳定、可重放、绑定 owner/novel/version，并保存审计 checkpoint。

### the agent's Discretion
- 采用现有 Python service/contract 模式，不新增运行时依赖。
- 使用定向单测与既有 relationships projection integration suite 验证。

</decisions>

<canonical_refs>
## Canonical References

- `docs/adr/0001-layer-registry.md` — S/D/R/A 命名空间与 Serving/Active 边界。
- `docs/adr/0002-narrative-unit-vs-narrative-memory.md` — NU/NM 消费顺序、NM candidate-only 红线与 Reader Chat 优先级。
- `.planning/ROADMAP.md` — Phase 24 success criteria 与计划波次。
- `.planning/STATE.md` — 当前 v1.1 进度、未授权边界和 PR #23 状态。

</canonical_refs>

<specifics>
## Specific Ideas

优先实施不改变公共 API 的契约收敛；24-01/02 已在当前分支完成本地等价实现，PR #23 保持独立，不执行 merge/cherry-pick/push。

</specifics>

<deferred>
## Deferred Ideas

- Narrative Memory production routing、promotion、active pointer 与 Reader Chat cutover：Phase 30 及新授权。
- 真实 Neo4j driver 接入：当前仅保留可选投影边界，不新增依赖。

</deferred>
