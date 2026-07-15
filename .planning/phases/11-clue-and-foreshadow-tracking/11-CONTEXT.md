# Phase 11: Clue and Foreshadow Tracking — Context

**Gathered:** 2026-07-13  
**Status:** Ready for planning  
**Source:** User-locked direction + verified Phase 04/05/07/08 artifacts + current code

<domain>
## Phase Boundary

交付小说专用、证据化、版本化、人工可控的线索与伏笔追踪能力。Phase 11 复用 Phase 07 的 evidence offsets 与 Phase 08 的版本/剧透惯例，沿用 Phase 04 的“召回信号不是事实”边界。

Phase 09/10 尚未实施。规划不得假定人物关系观察表、关系 API、会话表或聊天 API 已存在，也不得修改这些业务。Phase 11 仅定义 Phase 09 的可选只读 source protocol；来源缺失必须显式报告。
</domain>

<spec_lock>
## Locked Requirements

REQ-CLUE-01..07 的可证伪验收、fiction-only 范围和发布门见 11-SPEC.md。下游执行者必须同时读取 11-SPEC.md、11-CONTEXT.md、11-AI-SPEC.md、11-VALIDATION.md。
</spec_lock>

<decisions>
## Decisions

- **D-01 — Fiction only.** 只支持小说；不得恢复历史 domain/profile/prompt/fixture/UI。
- **D-02 — LLM minimum authority.** LLM 只输出 strict schema 的线索语义候选/判定。脚本拥有候选召回、跨章节证据包、schema/evidence/threshold/conflict gates、版本、状态机、写入、exact cache、预算和发布。
- **D-03 — Append-only lifecycle.** 五状态固定为 candidate、active、reinforced、paid_off、dismissed。允许转换严格按 11-SPEC.md；paid_off/dismissed 终态，旧生命周期事件不可更新或删除。
- **D-04 — Evidence requirements.** active、reinforced、paid_off 都必须有真实 evidence；reinforced 要新增证据；paid_off 必须同时有更早 cue 与更晚 payoff，不能由语义相似度、共现或单段模型判断直接得出。
- **D-05 — Protected human decisions.** 确认、驳回、注释、关联调整都是追加式 override/audit。重分析创建可比较的新版本和 machine diff，不能覆盖人工决定；歧义 relink 标记 needs_relink。
- **D-06 — Evidence-only links and cross-phase boundaries.** 人物、Phase 08 时间事件、已完成 Phase 09 的关系观察只作为证据化引用。关系 reader 缺失或运行不可用必须记录 `source_unavailable`，不得伪装为零信号。Phase 10 聊天不提供任何线索来源；聊天内容和向量相似度永不进入事实或 lifecycle evidence。
- **D-07 — Spoiler boundary.** API 在派生计数、筛选、关联、证据和状态前先裁剪阅读进度。全书只复用 Phase 08 的 timeline_full_book per-novel 明确开关，不创建第二个全书偏好。
- **D-08 — Analysis workspace UI.** 前端位于 /analysis，展示线索时间带、状态/人物筛选、证据面板、回收链、版本对比和人工动作；不增加顶级路由，不暴露摘要中间件。
- **D-09 — Versioned durable execution.** PostgreSQL 是 run/version/candidate/lifecycle/evidence/link/override/call/budget/pointer/journal 真值；candidate version 不可变，失败不移动 active，worker 可恢复/取消，未知价格或预算不足在 provider call 前 fail closed。
- **D-10 — Qualification and release.** 冻结 fiction fixture、假阳性/剧透/跨版本/owner 对抗、成本延迟、API、双 viewport browser、真实 PostgreSQL authority 和固定命令 release gate 缺一不可。
- **D-11 — No adjacent implementation.** 不实施写作/续写，不修改人物关系图或聊天业务实现，不修改 Phase 09/10 规划，不把聊天回答或图投影当成线索真值。
- **D-12 — Existing stack only.** 沿用 FastAPI、SQLAlchemy async、PostgreSQL、Pydantic、LiteLLM、Next.js、Axios、ECharts、Vitest、Playwright、pytest；不新增包或 agent/RAG/workflow 框架。

## the agent's Discretion

- clue 表、服务类和 endpoint 的精确名称，只要 owner/novel/version/evidence/override 边界不弱化。
- 候选召回特征与阈值的数值，必须版本化并由 dev fixture 调参，不能用 frozen test 反向调参。
- /analysis 内部视图切换的组件布局，只要保留单一小说选择器、同一全书开关、可访问列表和双 viewport 验收。
</decisions>

<canonical_refs>
## Canonical References

- .planning/REQUIREMENTS.md — REQ-CLUE-01..07
- .planning/phases/04-llm/04-CONTEXT.md, 04-AI-SPEC.md, 04-VERIFICATION.md — recall/evidence/gate/LLM boundary
- .planning/phases/05-narrative-knowledge-unit-layer/05-CONTEXT.md, 05-AI-SPEC.md, 05-VERIFICATION.md — immutable publication, rollback and shared production/eval strategy
- .planning/phases/07-semantic-hierarchical-chunking/07-CONTEXT.md, 07-AI-SPEC.md, 07-VERIFICATION.md — chapter/scene/evidence offsets and hierarchy lineage
- .planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-SPEC.md, 08-CONTEXT.md, 08-AI-SPEC.md, 08-VERIFICATION.md — durable versions, budget, overrides, spoiler and analysis workspace
- backend/app/models/knowledge.py and backend/app/services/knowledge/{candidates,evidence,llm_judge,gates}.py
- backend/app/models/analysis.py, backend/app/models/timeline.py, backend/app/services/timeline/{evidence,budget,overrides,query,worker,reconcile}.py
- backend/app/models/chunk_build.py and backend/app/services/chunking/{schemas,pg_store,hierarchy}.py
- frontend/src/app/analysis/page.tsx, frontend/src/components/timeline/*, frontend/src/lib/api.ts
</canonical_refs>

<current_facts>
## Current Code Facts

- Phase 08 is verified complete; MachineTimelineEvent, TimelineEvidenceRef, TimelineOverride, TimelineActivePointer and visible-set-first query behavior exist.
- Phase 07 hierarchy is persisted in chunk_builds/chunk_hierarchy_nodes with source_start/source_end/content_hash.
- Existing AnalysisRun/AnalysisVersion are timeline-specific in practice and have a single owner/novel active_key uniqueness boundary. Phase 11 must not reuse them in a way that blocks concurrent timeline and clue runs; use clue-owned durable tables or an explicitly domain-safe extension.
- Novel.reading_progress stores timeline_full_book. Phase 11 reads this existing preference; it does not add clue_full_book.
- frontend/src/lib/api.ts and several timeline runtime files currently contain user changes. Phase 11 planning avoids editing timeline worker/API and uses a dedicated clue API adapter file on the frontend.
- Codebase map documents contain stale “timeline unimplemented” descriptions; current code and 08-VERIFICATION.md are authoritative.
- Phase 09/10 currently only have planning artifacts; neither has业务实现。Phase 11 execution must bind Phase 09 已完成的公共 reader，不能读取或导入 Phase 10 聊天业务。
</current_facts>

<deferred>
## Deferred Ideas

- 历史文本线索追踪。
- 人物关系图实现、关系观察生产器或 Neo4j 修改。
- 阅读器选区聊天、多会话业务或聊天写事实。
- 写作、续写、同人文生成。
- 将摘要、主题、节奏、plot beats 暴露为分析菜单。
- semantic cache 或以向量相似度直接决定状态。
</deferred>

<scope_fence>
## Scope Fence

执行 Phase 11 时不得修改 Phase 09/10 规划或关系图/聊天业务文件。只允许新增 Phase 11 source protocol，并在未来来源缺席时 fail closed/返回空信号。
</scope_fence>

---

*Phase: 11-clue-and-foreshadow-tracking*
