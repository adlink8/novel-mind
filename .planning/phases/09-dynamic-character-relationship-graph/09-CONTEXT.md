---
phase: 09-dynamic-character-relationship-graph
status: ready-for-planning
gathered: 2026-07-13
source: user-locked-direction-and-local-code
---

# Phase 09: Dynamic Character Relationship Graph — Context

<domain>
## Phase Boundary

交付 fiction-only、证据门控、版本隔离、随叙事位置演化且默认防剧透的人物关系图。关系图属于现有 `/analysis` 工作台，与 Phase 08 时间线协作，但不暴露分析中间摘要，不提前实现 Phase 10 会话或 Phase 11 线索。

</domain>

<decisions>
## Locked Decisions

### Authority and history

- **D-01:** PostgreSQL 是 candidate、judgment、evidence、accepted observation、override、projection audit 与 query 的唯一事实源。
- **D-02:** Neo4j 是可选派生投影，只能从 PostgreSQL accepted observations 按 manifest 重放；投影失败或缺席不得改变 PostgreSQL 状态、API truth 或 release judgment。
- **D-03:** 动态关系必须使用 append-only observations + narrative valid interval + analysis version；关系变化通过新增 observation 表示，绝不覆盖过去状态。
- **D-04:** `CharacterRelation` 旧表不是 Phase 09 事实源；只能在 migration/compatibility 检查中作为 legacy 数据被明确忽略，图 API 不读取它生成事实。

### Product scope

- **D-05:** 只支持 fiction 小说人物。Phase 04 的 history profile 和 fixtures 不进入 Phase 09 pipeline、API、UI 或 qualification；不得恢复历史支持。
- **D-06:** 图中只出现 visible accepted observations 的人物端点；人物名、关系类型、筛选值、统计和 evidence preview 都先经过同一 visible-set spoiler filter。
- **D-07:** Phase 09 的 canonical edge types 为 `ally`、`enemy`、`family`、`mentor`、`romantic`。`same_entity` 只可作为人物合并候选信号，`causes/precedes` 属于时间线语义，不是人物关系边。
- **D-08:** 不新增关系图独立顶级路由；在 `/analysis` 增加“时间线 / 人物关系”工作区切换。plot/theme/style/pace/chapter summaries 保持后端中间产物。

### Spoiler and version isolation

- **D-09:** 默认 cutoff 由 API 从持久化 reading progress 解析；缺失/非法进度只显示第一章，无章节则空图。
- **D-10:** 全书披露只复用 Phase 08 已持久化的 per-novel `timeline_full_book` 明确开关与确认流程；不得新增第二个 graph full-book preference。
- **D-11:** API 必须 owner + novel + analysis version + spoiler scoped。active 与 running candidate 独立返回或独立选择，禁止跨版本合并或以旧版本图静默填充新版本空缺。
- **D-12:** 客户端提供的 `version_id` 只用于选择；服务端必须重新证明该 version 属于当前 owner/novel 且是允许访问的 active、running candidate 或显式历史版本。

### AI and deterministic control

- **D-13:** LLM 只判断关系语义、方向、状态变化和证据支持度；脚本负责 accepted source selection、candidate recall、evidence package、schema/evidence/scope/interval/conflict gates、阈值、状态机、写库、fold、cache 与 projection。
- **D-14:** 向量/BM25/邻近/共现/alias 相似度只可召回 candidate；LLM output、聊天内容、模型 rationale 和相似度不能直接成为图事实。
- **D-15:** 自动接受阈值冻结为 `confidence >= 0.85` 且所有 critical gates 通过；`0.65 <= confidence < 0.85` 或可解释冲突进入 `needs_human_review`；低于 `0.65`、scope/evidence/schema/interval 失败直接 rejected。阈值改变必须形成新 policy hash 与新 version lineage。
- **D-16:** observation 状态机为 `candidate -> judged -> gated -> accepted | needs_human_review | rejected`；accepted observation 不可变，重跑通过 idempotency key 复用或新增版本记录。

### Overrides

- **D-17:** 人工人物合并、关系类型和有效区间修正都是 append-only protective overrides，带 author、reason、supersedes、evidence signature 与 provenance；重分析不得静默覆盖。
- **D-18:** override 跨版本 relink 只在 stable evidence signature 唯一匹配时自动完成；0 或多匹配一律 `needs_relink`，不猜测。

### Frontend and performance

- **D-19:** 关系图实现候选锁定 Cytoscape.js；使用其 graph model、selection/event、pan/zoom 与内置 layout。不得安装已弃用的 `@types/cytoscape`。
- **D-20:** Phase 08 `timeline-chart.tsx` 继续使用 ECharts，不替换、不重写；联动只通过 selected narrative position/version/filter state 和现有 timeline events。
- **D-21:** 关系工作区必须有人物、关系类型、叙事位置筛选，zoom/pan，证据侧栏，键盘同源列表，并支持从 evidence 跳转章节。
- **D-22:** 大图分级固定：正常 `<=200 nodes && <=600 edges`；large `<=500 nodes && <=1500 edges`，关闭动画、隐藏非选中标签、使用低成本布局/样式；超过硬上限返回 `filters_required` 和 spoiler-safe counts，不返回截断元素。

### Downstream dependencies

- **D-23:** Phase 10 只依赖 owner/version/spoiler-filtered relationship query contract；Phase 09 不创建会话、消息、选区或聊天事实写入。
- **D-24:** Phase 11 只依赖 accepted observation IDs、人物 IDs、analysis version 与 evidence refs；Phase 09 不创建 clue 状态机、候选或 UI。

### the agent's Discretion

- SQLAlchemy 表名、service class 名与 endpoint 具体前缀，只要完整保留上述 authority、scope、version、spoiler 和 append-only 语义。
- Cytoscape 正常图的具体颜色、节点尺寸和内置 layout 参数；大图阈值和行为不可变。
- API 使用单 envelope 或 source 参数，只要 active/running candidate 不混合且历史 version 访问经过服务端证明。

</decisions>

<canonical_refs>
## Canonical References

- `.planning/phases/09-dynamic-character-relationship-graph/09-SPEC.md` — 可证伪范围和验收
- `.planning/REQUIREMENTS.md` — REQ-REL-01..06
- `.planning/phases/04-llm/04-CONTEXT.md` — LLM/脚本职责与 PostgreSQL/Neo4j 边界
- `.planning/phases/04-llm/04-AI-SPEC.md` — evidence package、judgment、gate 规范
- `.planning/phases/04-llm/04-VERIFICATION.md` — accepted judgment 真实入口
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-SPEC.md` — version/spoiler/fiction-only 产品边界
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-CONTEXT.md` — active/candidate、full-book 与 workspace 决策
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-AI-SPEC.md` — strict schema、evidence、override、budget/state patterns
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-VERIFICATION.md` — 已验证 API/browser/PG authority
- `backend/app/models/knowledge.py` — accepted judgment 与 evidence refs
- `backend/app/models/character.py` — legacy Character/CharacterRelation baseline
- `backend/app/models/analysis.py` — AnalysisVersion/Run lineage
- `backend/app/models/timeline.py` — active pointer、evidence、override baseline
- `backend/app/services/knowledge/gates.py` — accepted gate authority
- `backend/app/services/knowledge/graph_sync.py` — disabled-by-default Neo4j boundary
- `backend/app/services/timeline/query.py` — visible-set-first spoiler query precedent
- `frontend/src/app/analysis/page.tsx` — global workspace
- `frontend/src/components/timeline/timeline-chart.tsx` — ECharts timeline to preserve

</canonical_refs>

<deferred>
## Deferred / Forbidden in Phase 09

- Phase 10 reader selection AI and multi-session conversations
- Phase 11 clues and foreshadow lifecycle
- history domain/product support
- graph algorithms, recommendations, community detection, centrality ranking
- exposing backend intermediate summaries

</deferred>

