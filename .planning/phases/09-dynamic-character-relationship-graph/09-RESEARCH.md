---
phase: 09-dynamic-character-relationship-graph
status: complete
researched: 2026-07-13
confidence: high
---

# Phase 09 Research: Dynamic Character Relationship Graph

## Executive Recommendation

在 Phase 08 的 `AnalysisVersion` 上新增独立的 append-only relationship observation authority。输入从 Phase 04 accepted judgments 和其 evidence refs 开始，脚本构造 versioned candidate/evidence package，LLM 只输出 strict semantic judgment，脚本执行 fiction/scope/evidence/interval/conflict/threshold gates 后写入 PostgreSQL accepted observations。图 API 对 selected version 做 visible-set-first spoiler filtering，再派生节点、边、filters、counts 与 evidence。前端在 `/analysis` 使用 Cytoscape.js，ECharts 时间线保持原样。

## Current-Code Findings

| Area | Actual code | Planning consequence |
|---|---|---|
| Knowledge authority | `KnowledgeRelationJudgment` 保存 `status`、`gate_status`、relation type、confidence、evidence refs、model/prompt lineage | Phase 09 必须从 `accepted/accepted` rows 开始，不能读取 raw candidate 或 recall score |
| Graph projection | `projection.py` 对 fiction accepted judgment 写 legacy `CharacterRelation`；`graph_sync.py` 的 Neo4j 路径默认 disabled 且无 driver | 新 observation authority 不能把 legacy snapshot 当真值；Neo4j 保持派生 adapter |
| Character model | `CharacterRelation` 只有 relation type/strength/first seen，无 owner、version、evidence、interval、override | 需要新表，不能原地扩展为动态历史 |
| Character API | `/api/characters/{novel_id}`、`/relations` 返回空数组，extract 返回 501 | Phase 09 可新增严格 scoped graph API，并明确废弃空数组契约 |
| Analysis versions | `AnalysisVersion` 已冻结 source/hierarchy/prompt/schema/model/config；active pointer 与 running candidate 已验证 | observation 必须外键绑定 version；查询不能跨 version fallback |
| Spoiler | `timeline/query.py` 从 `Novel.reading_progress` 解析 cutoff，并要求 `timeline_full_book` 持久开关 | 关系图直接复用相同规则和 preference，不复制配置 |
| Overrides | Timeline override 是 append-only field overlay，promotion 做 stable evidence relink/needs_relink | 人物 merge、relation type、interval correction 沿用 supersession + explicit relink |
| Frontend | `/analysis` 已有 source tabs、full-book confirmation、person/order controls；ECharts canvas 有键盘同源列表 | 新增 workspace mode 与共享 selected version/cutoff；不替换时间线 |

代码库映射文档仍把 analysis/characters/timeline 标记为占位，已被实际 Phase 08 代码超越；本研究以当前模型、API、测试和 Phase 08 verification 为准。

## Why Cytoscape.js

Cytoscape.js 是专门的图论模型与网络可视化库，原生支持 nodes/edges collection、选择、事件冒泡、pan/zoom、布局生命周期和样式选择器；这比把 ECharts timeline series 扭成关系图更贴合关系工作区。官方文档也给出大图成本来源和具体优化：降低 pixel ratio、避免 compound/edge selectors、限制 overlay、必要时交互期间隐藏边。官方内置 `grid/circle/concentric/breadthfirst/cose` 等布局，Phase 09 可只安装 core 包，不引入额外 layout extension。

| Candidate | Strength | Limitation | Fit |
|---|---|---|---|
| Cytoscape.js | 图模型、selection/events、布局、style、pan/zoom 一体；成熟官方文档；TypeScript declarations 内置 | 大图需控制样式、边数量和 layout 成本 | **Selected per D-19** |
| ECharts graph series | 项目已有依赖，适合图表与时间轴 | 图编辑/selection/query API 不如 Cytoscape 面向图模型；会耦合 timeline 与 graph | 保留给 Phase 08 时间线，不用于关系图 |
| D3 force | 控制自由度高 | 需要手写图状态、选择、布局生命周期和更多可访问性胶水 | 不选 |

官方来源：

- Cytoscape.js API、layouts、events、performance: https://js.cytoscape.org/
- 官方仓库: https://github.com/cytoscape/cytoscape.js
- npm package: https://www.npmjs.com/package/cytoscape

## Package Legitimacy Audit

| Package | Status | Evidence | Plan |
|---|---|---|---|
| `cytoscape@3.34.0` | VERIFIED | npm package links to `cytoscape/cytoscape.js`, MIT, zero dependencies, built-in TypeScript declarations, current npm release observed 2026-07-13 | Pin exact version and commit `package-lock.json` |
| `@types/cytoscape` | FORBIDDEN | npm marks it deprecated because Cytoscape ships its own definitions | Do not install |

No other npm/pip/cargo package is required. Existing ECharts dependencies remain unchanged. Optional Neo4j projection uses the existing adapter boundary; no Neo4j driver install is planned.

## Recommended Data Architecture

```text
Phase 04 accepted KnowledgeRelationJudgment + evidence refs
  -> deterministic fiction/person/relation candidate selector
  -> version-bound evidence package (Phase 08 AnalysisVersion/hierarchy)
  -> strict LLM semantic judgment
  -> schema + owner/novel/version + evidence + interval + conflict + threshold gates
  -> append-only RelationshipObservation (PostgreSQL truth)
  -> protective overrides overlay
  -> spoiler/version-scoped fold
  -> API graph envelope
       -> Cytoscape.js workspace
       -> optional replayable Neo4j projection manifest
```

### Observation instead of snapshot

`CharacterRelation` represents one current row and cannot answer “what was the relationship at chapter 7 under version 42?”. A new observation row needs:

- owner/novel/analysis_version
- source judgment and candidate lineage
- source/target Character IDs and canonical relation type
- observation operation and narrative interval boundaries
- evidence links and confidence/policy/schema hashes
- immutable acceptance status/checksum/idempotency key

The query service folds accepted rows whose interval covers the selected narrative position. An ended/changed relationship is represented by later rows or bounded intervals, never by updating the earlier row.

### Visible-set-first spoiler discipline

The query order must be fixed:

1. prove owner/novel/version access;
2. resolve API cutoff from persisted progress/full-book preference;
3. select accepted observations/evidence within cutoff and interval;
4. apply eligible overrides;
5. derive endpoint Character rows;
6. derive relation filters, counts, evidence previews and degradation metadata.

Deriving nodes or filters before step 3 leaks future names and relation labels even if edges are later hidden.

## AI Boundary

LLM is useful only for semantic questions that deterministic code cannot answer reliably: whether evidence establishes/changes/ends one of five allowed relationships, direction, and which in-package evidence supports interval anchors. It must not choose owner, novel, version, DB IDs, publication status, threshold, active pointer or projection action.

Recommended frozen policy:

- accepted: confidence `>=0.85`, no risk flags, all critical gates pass;
- review: `0.65..0.849` or non-critical interval conflict;
- rejected: `<0.65` or any schema/evidence/scope/version/fiction failure;
- one persisted same-deployment schema repair at most; no provider fallback inside lineage;
- exact cache only when source judgment set, evidence package, prompt/schema/model/config and policy hashes match.

## API Shape

Recommended endpoint family: `/api/relationships/{novel_id}`.

- `GET /graph`: `source=active|running_candidate`, optional proven `version_id`, `through_chapter`, `character_ids`, `relation_types`, `full_book`.
- `GET /observations/{id}/evidence`: returns only evidence already visible under the same cutoff/version.
- `POST /overrides/character-merge`, `/overrides/relationship`, `/overrides/{id}/supersede`: owner-only append operations.
- `GET /versions/{version_id}/projection-manifest`: operator/replay path, not the browser fact source.

The response should include `version_id`, `cutoff`, `nodes`, `edges`, visible filters/counts, provenance and `degradation`. Above the hard cap it returns no elements and `mode=filters_required` rather than silently sampling.

## Frontend Architecture

- Keep `AnalysisPage` as the novel/version/full-book owner.
- Add `workspace: timeline | relationships`; no new top-level route.
- Relationship controls own person IDs, relation types and `through_chapter`; source/version/full-book remain shared.
- Cytoscape component is client-only and destroys/reuses its instance on version/visible-set changes.
- Edge selection opens an evidence sidebar; node/edge companion list uses the same API array for keyboard access.
- Timeline-to-graph linkage uses a selected narrative chapter/position callback. It does not import ECharts internals into Cytoscape or replace `timeline-chart.tsx`.

## Performance and Degradation

| Tier | Contract | Rendering |
|---|---|---|
| normal | ≤200 nodes and ≤600 edges | Cytoscape `cose`, labels, selection, straight/simple curves, no continuous animation |
| large | ≤500 nodes and ≤1500 edges | `pixelRatio: 1`, no animation, `concentric` or preset low-cost layout, labels only selected/hovered, simplified straight/haystack edges |
| filters-required | above either hard cap | API returns zero elements plus spoiler-safe counts and required-filter guidance; UI prompts person/type/chapter narrowing |

Performance qualification should seed at least 10,000 accepted observations and assert indexed API p95, payload caps, no future-label leakage, Cytoscape first usable render, pan/zoom responsiveness, instance cleanup and bounded heap growth.

## Security / Failure Pre-Mortem

1. **Spoiler leak via metadata:** filters/counts built before cutoff. Mitigation: one visible CTE/service result drives every response field and adversarial snapshots scan serialized JSON.
2. **Legacy snapshot contamination:** API reads `character_relations`. Mitigation: source assertion and integration seed proving legacy rows do not affect graph.
3. **Version mixing:** active and candidate observations folded together. Mitigation: version is mandatory in every query and unique/idempotency key; source-isolation tests.
4. **Override loss:** reanalysis mutates rows or guesses relink. Mitigation: append-only supersession and exact unique evidence-signature relink only.
5. **Large graph browser lockup:** force layout receives unbounded payload. Mitigation: server hard cap and deterministic large mode before Cytoscape initialization.

## Validation Architecture

- Unit: strict schemas, candidate selector, threshold/state machine, interval fold, conflict rules, override supersession/relink.
- PostgreSQL integration: migration, immutable constraints, idempotency, active/running version isolation, projection replay checksum.
- Contract/API: OpenAPI and TS consumer, owner/novel/version/spoiler/full-book, visible filters/counts/evidence.
- Adversarial: forged evidence, history profile, prompt injection, vector-only/chat-only facts, future labels, cross-version IDs.
- Frontend: Vitest component/contracts and real desktop/mobile Playwright.
- Performance: seeded observation query and 200/600, 500/1500, over-cap browser degradation.
- Release: one fail-closed report binding DB observations, package lock, commands and fixture/policy/schema/version hashes.

## Explicit Phase 10/11 Dependencies

- Phase 10 may call the read-only visible graph query with the same owner/version/spoiler context. It must not submit chat text as observation evidence.
- Phase 11 may reference accepted observation IDs and evidence refs. It owns clue candidates/statuses and cannot mutate relationship truth.

