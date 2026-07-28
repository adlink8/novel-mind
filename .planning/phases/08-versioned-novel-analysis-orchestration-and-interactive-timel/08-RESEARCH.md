# Phase 08: Versioned Novel Analysis Orchestration and Interactive Timeline - Research

**Researched:** 2026-07-13
**Domain:** 持久化 AI 分析编排、不可变版本、证据约束时间语义、剧透隔离、渐进式横向时间轴
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

### Product surface
- **D-01:** The primary entry is one global `/analysis` workspace that selects a novel; `/search` remains an internal evidence lookup route.
- **D-02:** Phase 08 exposes only the timeline. Plot summaries, beats, character/theme clues, pace/scene length and chapter summaries remain backend intermediates.
- **D-03:** The timeline is a zoomable horizontal axis with a full-book overview and interval zoom; mobile preserves horizontal pan/zoom rather than becoming a different data model.

### Timeline semantics
- **D-04:** One unified timeline defaults to the main plot; selecting a person filters the same events into that person's timeline.
- **D-05:** Story chronology and chapter narrative order are both persisted and switchable.
- **D-06:** Time supports exact, relative, fuzzy and unknown precision. The system must not invent exact dates.
- **D-07:** Causal edges are hidden by default and toggled as an overlay with `causes`, `triggers`, `responds_to` and `blocks` types.

### Publication and corrections
- **D-08:** LLM-extracted timeline events publish automatically; no mandatory review queue is part of Phase 08.
- **D-09:** Every event must retain chapter/source offsets, evidence refs, confidence, extraction model/prompt/schema lineage and creation time.
- **D-10:** User edits form a protected manual override layer; reanalysis cannot overwrite manually corrected fields.
- **D-11:** Analysis versions are immutable candidates. A validated candidate moves the active pointer; old versions remain comparable and rollbackable.

### Execution and cost
- **D-12:** Import runs only deterministic hierarchy and low-cost structural preparation. First entry to analysis idempotently starts deep timeline analysis.
- **D-13:** Results publish chapter by chapter while work continues; progress, partial status, failure and last update are visible.
- **D-14:** Chapter extraction uses the low-cost/balanced model tier; cross-chapter ordering/conflict reconciliation uses the quality tier.
- **D-15:** Per-novel token, cost and call budgets pause deterministically and preserve resumable checkpoints; cache identity includes source/prompt/schema/model lineage.

### Spoiler policy
- **D-16:** Spoiler protection is on by default and filters events after persisted reading progress at the API boundary, not only in the browser.
- **D-17:** Users may explicitly enable full-book analysis per novel; this preference is persisted.

### Scope
- **D-18:** Product scope is fiction only. Do not add new history contracts, prompts, fixtures or UI.
- **D-19:** Person relationship graph is Phase 09; reader selected-text AI is Phase 10; clue/foreshadow tracking is Phase 11.

### the agent's Discretion
- Exact timeline visualization library, provided it supports accessible keyboard interaction, responsive horizontal zoom and deterministic browser tests.
- Exact numeric budget defaults and polling/SSE transport, provided the locked pause/resume and progressive behavior is preserved.
- Internal table/module naming consistent with current SQLAlchemy/Alembic patterns.

### Deferred Ideas (OUT OF SCOPE)
- Phase 09: dynamic person relationship graph with chapter/time slider and evidence side panel.
- Phase 10: reader text-selection toolbar, evidence-backed AI side panel and multiple per-novel conversations.
- Phase 11: automatic/manual clue and foreshadow tracking with five-state lifecycle.
- History support is removed rather than deferred.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| REQ-TIME-01 | 可恢复、可取消、渐进展示的持久任务 | `AnalysisRun`/chapter stage checkpoint、租约、幂等 stage key、轮询 API [VERIFIED: codebase grep] |
| REQ-TIME-02 | 版本化产物、验证后 active、覆盖层与回滚 | immutable version + manifest + singleton pointer + field override [VERIFIED: codebase grep] |
| REQ-TIME-03 | 导入只做低成本准备，首次进入启动深分析 | idempotent ensure-run API 与部分唯一索引 [CITED: postgresql.org/docs/17/indexes-partial.html] |
| REQ-TIME-04 | 双顺序与四类时间精度 | narrative ordinal + story constraint/rank + precision union，禁止伪造日期 [ASSUMED] |
| REQ-TIME-05 | 自动发布、证据/offset/lineage、人工修正保护 | evidence FK/范围校验、machine event 与 override 分层 [VERIFIED: codebase grep] |
| REQ-TIME-06 | 人物筛选、顺序切换、可选因果层 | 结构化 participant/edge 表与 scope constraints [ASSUMED] |
| REQ-TIME-07 | 全局横向时间轴与渐进状态 | 现有 ECharts custom series + dataZoom + HTML 控件/列表 [CITED: echarts.apache.org/handbook/en/how-to/custom-series/] |
| REQ-TIME-08 | 阅读进度默认剧透门 | owner-scoped query 先计算可见章节上界，事件和边同时裁剪 [VERIFIED: codebase grep] |
| REQ-TIME-09 | 分级模型与单书预算 | 现有 `extraction→balanced`、`deep_analysis→quality`，新增预留式 ledger [VERIFIED: codebase grep] |
| REQ-TIME-10 | 仅小说时间线 | API schema/fixtures/UI 不接受 history 或中间分析类型 [VERIFIED: codebase grep] |
</phase_requirements>

## Summary

Phase 08 应建立一个独立的 timeline bounded context，而不是继续扩展当前无版本的 `analysis_results` JSON 或单浮点排序的 `timeline_events`。当前代码已经提供三个可复用先例：Phase 06 的 PostgreSQL durable job（lease/checkpoint/stage cache/cancel）、Phase 05/07 的 immutable candidate + active pointer + rollback，以及 Phase 07 的 active hierarchy/evidence offsets。它们应被组合，但旧表只作为迁移/兼容输入，不再作为 Phase 08 权威状态。[VERIFIED: codebase grep]

任务粒度必须是“章节抽取 stage 独立提交，跨章 reconciliation 后验证 candidate，再原子移动 active pointer”。每个模型调用前先持久化预算预留和稳定 stage key；完成后在同一事务写 usage、事件、证据与 checkpoint。恢复时按 completed stage keys 跳过，而不是按百分比猜测。首进 `/analysis` 的 ensure 操作由数据库唯一性保证并发下只产生一个活动 run。[CITED: postgresql.org/docs/17/indexes-partial.html] [CITED: postgresql.org/docs/19/sql-insert.html]

横向时间轴无需新依赖。仓库已经安装 ECharts 5.5 与 `echarts-for-react` 3.0.2；ECharts custom series 足以画事件、区间和可选因果线，dataZoom 支持 overview/interval 交互。[VERIFIED: codebase grep] ECharts 的 ARIA 主要提供图表描述且默认关闭，不能替代可聚焦操作，因此键盘顺序切换、缩放、人物筛选、因果开关和事件导航必须由原生 HTML 控件及同步事件列表承担。[CITED: echarts.apache.org/handbook/en/best-practices/aria/]

**Primary recommendation:** 先交付 PostgreSQL job/version/event/evidence/override/pointer 契约与轮询式渐进 API，再用现有 ECharts 做纯投影视图；不新增时间轴库，不用 SSE 作为首版正确性的前提。[HIGH]

## Project Constraints (from AGENTS.md)

- `.planning/` 是唯一 AI planning/execution 工作区；本研究不得修改 human-facing docs。[VERIFIED: AGENTS.md]
- substantial work 前必须读取 `.planning/config.json`、`STATE.md`、`ROADMAP.md` 和 active plan；当前 `auto_start=null` 且无 active plan。[VERIFIED: codebase grep]
- 后续每个计划必须含 `Steps`、`Must-Haves`、`Verification`；每个实现 slice 最后一步必须是 `Test, Fix, and Confirm`。[VERIFIED: AGENTS.md]
- `.planning/STATE.md` 是单一执行游标；系统结构以 `docs/architecture/` 为准，模块边界以各 `README.md` 为准。[VERIFIED: AGENTS.md]
- 默认中文、结论先行、本地事实优先、最小正确改动、有意义改动必须验证；不得覆盖无关或他人改动。[VERIFIED: AGENTS.md]
- 安全/用户明确指令优先，不得泄露凭据，不得未经确认删除、重置、发布或执行破坏性 Git 操作。[VERIFIED: AGENTS.md]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| run 创建、租约、恢复、取消、预算 | API / Backend | Database / Storage | 状态机由脚本执行，PostgreSQL 保存权威状态和并发约束。[VERIFIED: codebase grep] |
| candidate/version/active/rollback | Database / Storage | API / Backend | 不可变性、唯一 pointer 和事务边界必须由数据库保证。[CITED: postgresql.org/docs/16/ddl-constraints.html] |
| chapter extraction/reconciliation | API / Backend worker | Database / Storage | LLM 只返回 strict schema；worker 校验并持久化。[VERIFIED: codebase grep] |
| mixed precision 与双顺序 | API / Backend | Database / Storage | 语义标准化和拓扑约束不能由图表推断。[ASSUMED] |
| evidence/causal/participant scope | Database / Storage | API / Backend | FK/复合 scope 校验 fail closed，API 仅返回已验证投影。[ASSUMED] |
| spoiler gate | API / Backend | Database / Storage | 默认可见上界必须在查询边界应用，浏览器不可成为安全边界。[VERIFIED: 08-CONTEXT.md] |
| progressive workspace | Browser / Client | API / Backend | 客户端轮询状态和活动版本增量；服务端提供一致 cursor/watermark。[ASSUMED] |
| 横向绘制、缩放、过滤控件 | Browser / Client | — | ECharts 负责画布，原生控件负责可访问操作。[CITED: echarts.apache.org/handbook/en/best-practices/aria/] |

## Standard Stack

### Core

| Library/Platform | Version | Purpose | Why Standard |
|---|---:|---|---|
| PostgreSQL | 16 target | job/version/event/pointer 权威存储 | 项目已锁定；部分唯一索引可限制每 owner+novel 同类活动任务为一条。[VERIFIED: 08-SPEC.md] [CITED: postgresql.org/docs/17/indexes-partial.html] |
| SQLAlchemy async | 项目锁定版本 | ORM、事务、`FOR UPDATE`/CAS | 当前全后端使用 `AsyncSession`；官方 Session API支持带 `FOR UPDATE` 刷新/锁定。[VERIFIED: codebase grep] [CITED: docs.sqlalchemy.org/en/20/orm/session_api.html] |
| FastAPI + Pydantic v2 | FastAPI 0.115.9 / Pydantic 2.12.5 (当前环境) | owner-scoped API 与 strict schemas | 与现有 API、security dependency、schema validation 一致。[VERIFIED: local environment] |
| ECharts + echarts-for-react | 5.5.x / 3.0.2 (package lock intent) | custom horizontal timeline、dataZoom、overlay | 已在项目依赖中，不需要新增时间轴库；custom series 是官方扩展路径。[VERIFIED: codebase grep] [CITED: echarts.apache.org/handbook/en/how-to/custom-series/] |
| React Query | 5.50.x | job/status/events 轮询、失效与缓存 | 已安装并适合渐进 API；首版无需 SSE 客户端依赖。[VERIFIED: codebase grep] |
| Playwright | 1.61.1 registry/current project range | desktop + 390px browser gates | 项目已有两个 viewport project 和失败制品配置。[VERIFIED: npm registry] [VERIFIED: codebase grep] |

### Supporting

| Component | Purpose | When to Use |
|---|---|---|
| Phase 06 `QualityRun`/worker patterns | lease、checkpoint、stage cache、cancel、恢复 | 复制模式到 timeline 专用服务，不让 timeline 依赖 eval domain。[VERIFIED: codebase grep] |
| Phase 05/07 pointer/promotion patterns | candidate 验证、pointer CAS、rollback journal | 每次 timeline version 激活或回滚。[VERIFIED: codebase grep] |
| Phase 07 `ChunkHierarchyNode`/`pg_store` | active build、chapter/scene/evidence、source offsets | 生成 extraction package 和验证 evidence refs。[VERIFIED: codebase grep] |
| `ai_router` | extraction→balanced，deep_analysis→quality | 章节抽取和跨章 reconciliation；禁止透明 fallback 改变 lineage。[VERIFIED: codebase grep] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|---|---|---|
| ECharts custom series | vis-timeline / react-calendar-timeline | 专用时间轴可能更快起步，但新增供应链、样式和测试面；当前 ECharts 已覆盖核心绘制/缩放，新增包收益不足。[ASSUMED] |
| Polling | SSE | SSE 降低重复请求，但增加断线恢复、代理缓冲、认证和 cursor 语义；轮询已满足 chapter-granularity progressive requirement。[ASSUMED] |
| Dedicated normalized tables | 扩展 `AnalysisResult.result_data` JSON | JSON 改动更少，但无法稳健表达 FK、scope、participant/edge、field override 和 pointer invariants。[ASSUMED] |

**Installation:** 不安装新包。继续使用现有依赖；禁止在 Phase 08 计划中加入时间轴包安装任务。[HIGH]

## Package Legitimacy Audit

Phase 08 推荐不安装外部包，因此 Package Legitimacy Gate 不触发。[HIGH] 现有 `echarts` registry 当前为 6.1.0、`echarts-for-react` 为 3.0.6，但本阶段不应顺带升级：仓库声明 ECharts 5.5.x，升级属于额外兼容性工作。[VERIFIED: npm registry] [VERIFIED: codebase grep]

**Packages removed due to slopcheck [SLOP] verdict:** none  
**Packages flagged as suspicious [SUS]:** none  

## Architecture Patterns

### System Architecture Diagram

```text
GET /analysis workspace
  -> select owned novel
  -> POST ensure-run (idempotency identity + partial unique active-run index)
       -> existing run? return it
       -> otherwise freeze source snapshot + active hierarchy + config lineage
  -> bounded worker claims lease
       -> for each chapter in narrative order
            -> reserve budget -> extract strict schema -> validate evidence/scope
            -> commit chapter events + participants + checkpoint + usage
            -> progressive API watermark advances
       -> reconcile cross-chapter story constraints (quality tier)
       -> validate manifest/events/edges/budget/evidence
            -> fail: candidate retained, active pointer unchanged
            -> pass: transactionally move active pointer + append journal
  -> GET status/events?cursor=...
       -> owner/novel scope
       -> active-or-running version selection
       -> spoiler chapter cutoff
       -> participant filter/order mode/causal flag
       -> ECharts visual projection + accessible HTML event navigator
```

### Recommended Project Structure

```text
backend/app/
├── models/timeline.py              # run/version/pointer/event/evidence/participant/edge/override
├── schemas/timeline.py             # strict API + extraction/reconciliation contracts
├── services/timeline/
│   ├── identity.py                 # normalized hashes and stage/cache keys
│   ├── jobs.py                     # ensure/lease/cancel/resume/checkpoint
│   ├── extraction.py               # chapter package + balanced-tier call
│   ├── ordering.py                 # mixed precision constraints + deterministic ranks
│   ├── evidence.py                 # owner/novel/chapter/build/offset gates
│   ├── budget.py                   # reservation/settlement/pause
│   ├── versions.py                 # validate/promote/rollback journal
│   └── queries.py                  # spoiler-safe progressive read model
└── api/timeline.py                 # thin owner-scoped endpoints
frontend/src/
├── app/analysis/page.tsx           # global selector/workspace state
├── components/timeline/            # chart, controls, progress, event navigator
└── lib/api.ts                      # typed run/version/event contracts
```

### Pattern 1: Durable stage ledger, not percentage resume

**What:** `AnalysisRun` 持有 lease/status/checkpoint，另用稳定 stage identity（建议 `(version_id, stage_kind, chapter_id, input_hash)` 唯一）记录 `pending/running/completed/failed`、attempt、usage 和 output hash。checkpoint 是导航优化，stage row/unique key 才是防重复调用依据。[ASSUMED]

**When to use:** 每个章节 extraction 和全书 reconciliation。

```python
# Source pattern: existing Phase 06 worker [VERIFIED: codebase grep]
stage_key = sha256(canonical_json({
    "source_snapshot": version.source_snapshot_hash,
    "hierarchy_build": version.hierarchy_build_id,
    "chapter": chapter.id,
    "prompt": version.prompt_hash,
    "schema": version.schema_hash,
    "model": resolved_model_revision,
    "config": version.config_hash,
})).hexdigest()

if await stages.is_completed(stage_key):
    return await stages.load_output(stage_key)
await budget.reserve_before_call(run_id, stage_key, worst_case_tokens)
```

### Pattern 2: Immutable version + singleton active pointer

**What:** version manifest 一经 candidate 创建后只允许状态/验证记录推进，lineage 与 machine outputs 不原地改写；`TimelineActivePointer(owner_id, novel_id)` 唯一，promotion 在锁定 pointer 后重验 candidate checksum，再写 pointer 和 append-only journal。[VERIFIED: codebase grep]

**When to use:** candidate 验证成功、显式 rollback。

```python
# Source: PostgreSQL partial/unique constraints + SQLAlchemy locking docs
pointer = await db.scalar(
    select(TimelineActivePointer)
    .where(TimelineActivePointer.owner_id == owner_id,
           TimelineActivePointer.novel_id == novel_id)
    .with_for_update()
)
assert await validator.is_promotable(candidate)
before = pointer.version_id if pointer else None
# update/insert pointer and journal in one transaction
```

### Pattern 3: Dual ordering via facts, constraints, and deterministic projection

**What:** narrative order保存为 `(chapter_number, source_start, local_event_index, event_id)`；story order 不直接让 LLM 生成任意 float，而保存 precision、normalized value/anchor、relation constraints、reason、confidence，再由脚本稳定拓扑排序得到 `story_rank`。矛盾/环必须标记 unresolved，使用 narrative order + stable ID 作为 tie-break，不伪造日期。[ASSUMED]

**Precision contract:** `exact` 保存合法日期/纪元值；`relative` 保存 anchor event + relation + magnitude/unit（可缺 magnitude）；`fuzzy` 保存 label/range/era；`unknown` 只保存原文 time expression/原因。四类共用 discriminated union，禁止拿 `1970-01-01`、章节号或 float 冒充未知时间。[ASSUMED]

### Pattern 4: Machine fields and user override overlay

**What:** machine event 永远属于 version；用户编辑写独立 override，按 `(owner_id, novel_id, logical_event_id, field_name)` 唯一，包含 value、base machine hash、editor、timestamp。读取时 overlay；重分析可更新 machine event，但不得写 override。删除语义使用 user suppression/tombstone override，不物理删除旧版本事件。[ASSUMED]

### Pattern 5: Spoiler-safe read model

**What:** API 从持久 `Novel.reading_progress.chapter_id` 解析同 novel 的 `chapter_number`，默认查询 `event.chapter_number <= cutoff`。无进度时采用明确的产品默认（建议仅第 1 章或空结果，需在实现前锁定）；full-book preference 必须 owner+novel scoped。边查询必须要求 source/target 都已可见，聚合 count、characters、last event 等也必须从裁剪后集合计算，防止 side-channel。[VERIFIED: codebase grep] [ASSUMED]

### Pattern 6: Progressive polling with monotonic cursor

**What:** `GET run/status` 返回 status、completed/total chapters、last_update、error category、active/candidate IDs；`GET timeline/events?after=<publication_seq>` 返回单调 publication sequence。React Query 在 running/partial 时轮询，terminal 后停止。不要用 `updated_at` 作为唯一 cursor；同一事务多行时间戳可能相同。[ASSUMED]

### Anti-Patterns to Avoid

- **BackgroundTasks/进程内 asyncio task 作为任务真值：** 进程重启后丢失，无法满足 restart acceptance；请求只 ensure DB run，独立 worker claim。[ASSUMED]
- **仅靠“先查再插”防重复任务：** 并发请求会竞态；必须数据库唯一索引 + conflict handling。[CITED: postgresql.org/docs/19/sql-insert.html]
- **每章完成就移动 active pointer：** 会让 active manifest 不完整；partial 结果属于 running candidate read model，active 只在全版验证后切换。[ASSUMED]
- **把 story time 压成 float/date：** 会丢掉相对、模糊、未知语义并诱发伪精确。[ASSUMED]
- **浏览器过滤剧透：** 网络响应已经泄露；API 查询先裁剪。[VERIFIED: 08-CONTEXT.md]
- **把手工编辑写回 machine row：** 无法重分析、比较或 byte-identical rollback；使用 overlay。[ASSUMED]
- **图表 canvas 是唯一交互面：** ARIA 描述不等于键盘导航；提供同步 HTML 控件和事件列表。[CITED: echarts.apache.org/handbook/en/best-practices/aria/]
- **复用 history projection：** 当前 knowledge projection 的 history timeline 测试与 Phase 08 fiction-only 冲突；新服务不得调用 history timeline 投影路径。[VERIFIED: codebase grep]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| 横向缩放/绘制 | 新 canvas engine 或新 timeline package | 现有 ECharts custom series/dataZoom | 已有依赖和官方扩展机制，减少供应链和浏览器差异。[CITED: echarts.apache.org/handbook/en/how-to/custom-series/] |
| 并发唯一活动 run | 应用内 mutex | PostgreSQL partial unique index + conflict handling | 跨进程、跨重启仍生效。[CITED: postgresql.org/docs/17/indexes-partial.html] |
| schema validation | 手写 dict 检查 | Pydantic v2 discriminated unions/strict models | 与项目 strict-output 模式一致。[VERIFIED: codebase grep] |
| owner isolation | 前端隐藏或散落检查 | 现有 `require_user` + `_owned_novel`/repository scope | 当前约定 inaccessible resource 返回 404。[VERIFIED: codebase grep] |
| active promotion/rollback | 直接 UPDATE 和覆盖旧行 | Phase 05/07 pointer + journal 模式 | 保留审计和精确回滚。[VERIFIED: codebase grep] |
| 模型路由 | 在 timeline 中硬编码 provider | 现有 `ai_router`，显式 tier + frozen resolved model lineage | 配置与密钥边界已存在。[VERIFIED: codebase grep] |

**Key insight:** 本阶段真正复杂的是数据库不变量、恢复幂等、语义不确定性和泄露边界；图形本身不是需要引入新框架的问题。[HIGH]

## Common Pitfalls

### Pitfall 1: checkpoint 已写但模型结果未原子落库
**What goes wrong:** 恢复跳过该章但事件缺失，或相反重复调用。[ASSUMED]  
**How to avoid:** 模型调用前 reservation，调用后以 stage key 在一个 DB 事务内写 events/evidence/usage/completed stage/checkpoint；若外部调用成功而 DB commit 失败，exact cache 或 provider request id 需允许重放而不再次计费（不能保证 provider 幂等时，记录 ambiguous attempt 并人工/策略恢复）。[ASSUMED]  
**Warning signs:** checkpoint chapter=N，但该章 publication count=0；同 stage key 多条 billed attempts。[ASSUMED]

### Pitfall 2: budget 并发超支
**What goes wrong:** 两个 worker 都在调用前看到余额，合计超过 ceiling。[ASSUMED]  
**How to avoid:** 锁 ledger/run row，按 worst-case 原子预留 calls/tokens/cost；settle 实际 usage 后释放差额。价格未知时不得按 0 费用继续，token/call ceiling 仍 fail closed。[ASSUMED]

### Pitfall 3: partial publication 与 immutable candidate 混淆
**What goes wrong:** 用户看到一半的 candidate 被当作 active，失败后 pointer/manifest 不一致。[ASSUMED]  
**How to avoid:** API 明确 `source=active|running_candidate` 与 `is_partial`；旧 active 始终可查询，running candidate 只用于当前进度视图。[ASSUMED]

### Pitfall 4: 因果边泄露隐藏事件
**What goes wrong:** 未来事件本身被过滤，但边的 target ID/label 暴露剧透。[ASSUMED]  
**How to avoid:** visible event CTE/ID set 先生成，edge 查询 inner join 两端；hidden edge 不计数、不返回 dangling stub。[ASSUMED]

### Pitfall 5: 只保存 story rank，丢失推断依据
**What goes wrong:** 新版本无法解释排序差异，人工修正和冲突重算困难。[ASSUMED]  
**How to avoid:** rank 是派生投影；保留 time expression、precision、anchor/constraint、reason、confidence 和 evidence refs。[ASSUMED]

### Pitfall 6: ECharts 测试依赖像素坐标
**What goes wrong:** 字体、DPR、动画导致 Playwright flaky。[ASSUMED]  
**How to avoid:** 关闭测试动画，固定容器尺寸，给 HTML 控件/列表稳定 role/test id；断言状态、过滤结果、ARIA/可见标签和 chart option adapter，而非 canvas 像素。[ASSUMED]

## Code Examples

### Mixed precision strict schema

```python
# Project pattern: Pydantic v2 strict schemas [VERIFIED: codebase grep]
class RelativeTime(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    precision: Literal["relative"]
    anchor_event_key: str | None
    relation: Literal["before", "after", "same_time", "next_morning"]
    magnitude: Decimal | None = Field(default=None, ge=0)
    unit: Literal["minute", "hour", "day", "month", "year"] | None
    source_expression: str
    inference_reason: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)
```

### Spoiler-safe event and edge query shape

```python
# Recommended SQLAlchemy shape; owner/novel/cutoff are mandatory [ASSUMED]
visible = (
    select(TimelineEvent.id)
    .where(
        TimelineEvent.owner_id == owner_id,
        TimelineEvent.novel_id == novel_id,
        TimelineEvent.version_id == selected_version_id,
        or_(full_book, TimelineEvent.chapter_number <= cutoff_chapter_number),
    )
    .cte("visible_events")
)
edges = select(TimelineCausalEdge).where(
    TimelineCausalEdge.source_event_id.in_(select(visible.c.id)),
    TimelineCausalEdge.target_event_id.in_(select(visible.c.id)),
)
```

### ECharts projection boundary

```tsx
// Source capability: official ECharts custom series and ARIA docs
const option = {
  animation: !isTest,
  aria: { show: true, description: accessibleSummary },
  dataZoom: [{ type: "inside", xAxisIndex: 0 }, { type: "slider", xAxisIndex: 0 }],
  series: [{
    id: "timeline-events",
    type: "custom",
    renderItem: renderTimelineEvent,
    encode: { x: ["start", "end"], y: "lane", tooltip: ["title", "timeLabel"] },
    data: projectedEvents,
  }],
};
// Native buttons/list remain the keyboard-operable source of interaction.
```

## State of the Art

| Old Approach in repository | Current Phase 08 Approach | Impact |
|---|---|---|
| synchronous request + `Novel.status` | persistent leased run + stage ledger | restart/resume and cancel become testable。[VERIFIED: codebase grep] |
| unversioned `AnalysisResult.result_data` | immutable manifest/version + active pointer | failed candidate cannot corrupt active。[VERIFIED: codebase grep] |
| one float `sort_order` | narrative tuple + story constraints/rank + precision union | flashback/relative/fuzzy/unknown remain honest。[ASSUMED] |
| characters JSON string | normalized participant rows | indexed person filtering and scope constraints。[ASSUMED] |
| free-text time | typed exact/relative/fuzzy/unknown | prevents invented exact dates。[ASSUMED] |
| empty/501 timeline endpoints | owner-scoped progressive query/ensure/edit/rollback API | real product vertical slice。[VERIFIED: codebase grep] |
| browser-only potential filtering | API-bound spoiler cutoff | future chapters never enter default response。[VERIFIED: 08-CONTEXT.md] |

**Deprecated/outdated:** `TimelineEvent.sort_order`, `characters_involved` string and `time_reference` free text may remain only for migration/compatibility, not as Phase 08 authority。[VERIFIED: codebase grep] `AnalysisResult` remains usable for backend intermediates but must not own timeline version lifecycle。[VERIFIED: codebase grep]

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | 约束图 + deterministic topological rank 是 mixed precision story ordering 的首选表示 | Architecture Patterns | 可能需要不同排序模型或 UX 表达 |
| A2 | 首版 polling 足以满足 chapter-granularity progressive UX | Standard Stack | 高频/大规模时可能需 SSE |
| A3 | machine event + field-level override 是保护人工修正的最小正确模型 | Architecture Patterns | 产品若要求 event merge UI，logical identity 需更复杂 |
| A4 | ECharts 5.5 custom series 能满足项目具体性能规模 | Standard Stack | 超大事件量需实测虚拟化/采样 |
| A5 | 无阅读进度时默认仅首章或空结果 | Spoiler-safe read model | 必须由产品锁定，错误默认可能泄露或体验差 |
| A6 | publication sequence 比 timestamp cursor 更安全 | Progressive polling | 若 API 采用 snapshot pagination，cursor 形式会不同 |

## Open Questions — RESOLVED

1. **RESOLVED（D-20）：没有 reading progress 时默认可见范围是什么？**
   - 决定：spoiler cutoff 为第一章；若无章节则为空，API 绝不默认全书可见。[VERIFIED: 08-CONTEXT.md D-20]

2. **RESOLVED（D-21）：running candidate 与旧 active 同时存在时如何展示？**
   - 决定：两者分离展示并可明确切换；事件与 aggregates 永不合并。[VERIFIED: 08-CONTEXT.md D-21]

3. **RESOLVED（D-22）：价格未知时如何执行预算？**
   - 决定：无法证明 worst-case cost reservation 时立即暂停为 `paused_budget`；token/call limits 不能替代未知价格的成本预留。数值 defaults 仍按 agent discretion 配置化。[VERIFIED: 08-CONTEXT.md D-22]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---:|---|---|
| Node.js | frontend build/test | ✓ | 24.13.0 | — [VERIFIED: local environment] |
| npm | frontend dependency/test | ✓ | 11.6.2 | — [VERIFIED: local environment] |
| Python | backend tests | ✓ | 3.14.2 system | 使用项目 venv；system 环境缺部分 backend packages。[VERIFIED: local environment] |
| Docker | PostgreSQL integration | ✓ | 29.6.1 | — [VERIFIED: local environment] |
| `psql` CLI | direct DB diagnostics | ✗ | — | Docker PostgreSQL + SQLAlchemy integration tests。[VERIFIED: local environment] |
| ECharts | timeline UI | ✓ | package.json 5.5.x | 无需新增包。[VERIFIED: codebase grep] |
| Playwright | browser gates | ✓ | 1.61.1 registry/project | existing desktop/mobile config。[VERIFIED: npm registry] |

**Missing dependencies with no fallback:** none identified。[HIGH]  
**Missing dependencies with fallback:** `psql` CLI 缺失，可通过 Docker/SQLAlchemy test path 验证；执行前应确认 backend venv 而非 system Python。[VERIFIED: local environment]

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Backend | pytest + pytest-asyncio；markers 已覆盖 unit/integration/contract/live。[VERIFIED: codebase grep] |
| Frontend | Vitest 4.1.10 + Testing Library。[VERIFIED: codebase grep] |
| Browser | Playwright 1.61.1，`chromium-desktop` + `chromium-mobile-390`。[VERIFIED: codebase grep] |
| Quick backend | `cd backend; venv\Scripts\python.exe -m pytest tests/unit/timeline -q`（Wave 0 创建）。[ASSUMED] |
| Full targeted | `cd backend; venv\Scripts\python.exe -m pytest tests/unit/timeline tests/integration/timeline tests/test_timeline.py tests/test_analysis.py -q`。[ASSUMED] |
| Frontend | `cd frontend; npm test -- --run`；`npm run test:e2e`。[VERIFIED: codebase grep] |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| TIME-01 | restart N→N+1、零重复 stage call、cancel/resume | integration | `pytest tests/integration/timeline/test_job_resume.py -q` | ❌ Wave 0 |
| TIME-02 | candidate/active/failure/byte-identical rollback | PostgreSQL integration | `pytest tests/integration/timeline/test_version_lifecycle.py -q` | ❌ Wave 0 |
| TIME-03 | import zero deep calls、并发 ensure 只一 run | integration | `pytest tests/integration/timeline/test_trigger_idempotency.py -q` | ❌ Wave 0 |
| TIME-04 | flashback/relative/fuzzy/unknown 双排序 | unit/property | `pytest tests/unit/timeline/test_ordering.py -q` | ❌ Wave 0 |
| TIME-05 | evidence mismatch fail closed、override survives | integration | `pytest tests/integration/timeline/test_evidence_overrides.py -q` | ❌ Wave 0 |
| TIME-06 | participant filter、edge types/scope/toggle | API contract | `pytest tests/test_timeline_api.py -q` | ❌ Wave 0 |
| TIME-07 | empty/running/partial/completed/failed desktop+390 | browser | `npm run test:e2e -- timeline.spec.ts` | ❌ Wave 0 |
| TIME-08 | chapter 5 默认绝不返回 6+，edge/metadata 同样不泄露 | security integration | `pytest tests/integration/timeline/test_spoiler_gate.py -q` | ❌ Wave 0 |
| TIME-09 | budget reserve/pause/zero further calls/resume | unit+integration | `pytest tests/unit/timeline/test_budget.py tests/integration/timeline/test_budget_pause.py -q` | ❌ Wave 0 |
| TIME-10 | 无 history/中间类型/Phase 09-11 routes | contract/browser | `pytest tests/test_timeline_api.py -q` + Playwright | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** 最小相关 unit/contract command，目标 <30 秒。[ASSUMED]
- **Per wave merge:** backend timeline targeted suite + frontend Vitest。[ASSUMED]
- **Phase gate:** PostgreSQL integration、frontend build/lint/test、双 viewport Playwright 全绿，再运行 `$gsd-verify-work`。[VERIFIED: AGENTS.md]

### Wave 0 Gaps

- [ ] `backend/tests/unit/timeline/`：identity、state machine、ordering、budget、schema。
- [ ] `backend/tests/integration/timeline/`：PostgreSQL concurrency、resume、pointer、evidence、spoiler。
- [ ] `backend/tests/fixtures/timeline/`：flashback、“three years earlier”、“next morning”、childhood fuzzy、unknown。
- [ ] `frontend/src/__tests__/timeline-*.test.tsx`：adapter、progress states、filters、accessible navigator。
- [ ] `frontend/e2e/timeline.spec.ts`：empty/running/partial/completed/failed × desktop/390。
- [ ] frozen fake-model transcripts；live model qualification 单独标记，不进入 secretless PR 默认链路。[VERIFIED: codebase grep]

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | yes | existing `require_user` on every timeline/run/version endpoint。[VERIFIED: codebase grep] |
| V3 Session Management | yes | 复用现有 cookie/token session，不在 timeline 建新认证机制。[VERIFIED: codebase grep] |
| V4 Access Control | yes | owner+novel+version scope on every query/mutation；inaccessible 返回 404。[VERIFIED: codebase grep] |
| V5 Input Validation | yes | strict Pydantic enums/unions、field allowlist、evidence/offset business gates。[VERIFIED: codebase grep] |
| V6 Cryptography | limited | 复用现有 provider key encryption；manifest hashing 用标准 hashlib，不自制加密。[VERIFIED: codebase grep] |
| V8 Data Protection | yes | spoiler cutoff、error redaction、raw model output 最小化/限期策略。[ASSUMED] |
| V10 Malicious Code | yes | novel text 是不可信 prompt data；无 tools/DB/filesystem capability。[VERIFIED: 07-AI-SPEC.md] |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| cross-owner run/version/event IDOR | Information Disclosure | scope every lookup by authenticated owner + novel，404 fail closed。[VERIFIED: codebase grep] |
| spoiler leakage via edges/counts/errors | Information Disclosure | visible-event set first，所有派生数据从裁剪后集合计算。[ASSUMED] |
| duplicate concurrent run / budget overspend | Tampering/DoS | partial unique index、row lock、atomic reservation。[CITED: postgresql.org/docs/17/indexes-partial.html] |
| prompt injection in novel text | Elevation/Tampering | strict schema、bounded evidence package、no tools、本地二次 validation。[VERIFIED: 07-AI-SPEC.md] |
| forged evidence IDs/offsets | Tampering | active hierarchy FK/scope/hash/range checks；mismatch rejects publication。[VERIFIED: codebase grep] |
| manual override clobber | Tampering | separate append-audited override layer + field allowlist。[ASSUMED] |
| raw model/error data leaks secrets | Information Disclosure | hash/minimize raw outputs；public errors use category/code, server logs redact。[VERIFIED: AGENTS.md] |

## Sources

### Primary (HIGH confidence)

- Local code: `backend/app/models/{timeline,analysis,eval,chunk_build,novel}.py`, `services/{rag_quality_worker,ai_router,analysis_service}.py`, `services/chunking/pg_store.py`, `services/knowledge/projection.py` — current contracts and precedents.[VERIFIED: codebase grep]
- Local frontend: `frontend/package.json`, `playwright.config.ts`, reader page, app shell, API adapter — installed stack and test matrix.[VERIFIED: codebase grep]
- Phase contracts: `08-SPEC.md`, `08-CONTEXT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, Phase 04/07 AI specs and Phase 07 verification.[VERIFIED: codebase grep]
- https://echarts.apache.org/handbook/en/how-to/custom-series/ — official custom-series extension path.[CITED: echarts.apache.org/handbook/en/how-to/custom-series/]
- https://echarts.apache.org/handbook/en/best-practices/aria/ — ARIA import, description and default-off behavior.[CITED: echarts.apache.org/handbook/en/best-practices/aria/]
- https://www.postgresql.org/docs/17/indexes-partial.html — partial unique index pattern.[CITED: postgresql.org/docs/17/indexes-partial.html]
- https://www.postgresql.org/docs/19/sql-insert.html — atomic conflict handling semantics.[CITED: postgresql.org/docs/19/sql-insert.html]
- https://docs.sqlalchemy.org/en/20/orm/session_api.html — locking/refresh transaction API.[CITED: docs.sqlalchemy.org/en/20/orm/session_api.html]

### Secondary (MEDIUM confidence)

- npm registry metadata for `echarts`, `echarts-for-react`, `@playwright/test`; used only to verify existence/current registry versions, not to recommend new packages.[VERIFIED: npm registry]

### Tertiary (LOW confidence)

- None. Unverified design judgments are explicitly `[ASSUMED]` and listed in the Assumptions Log.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — current code/package manifests plus official ECharts/PostgreSQL/SQLAlchemy docs.
- Architecture: HIGH for reuse boundaries and persistence patterns; MEDIUM for proposed normalized schema details pending implementation design.
- Pitfalls: HIGH for observed current gaps and locked acceptance failures; MEDIUM for operational edge cases not yet exercised by Phase 08 tests.

**Research date:** 2026-07-13  
**Valid until:** 2026-08-12 for project architecture; re-check npm/docs before any dependency upgrade.
