---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
spec_version: novel-timeline-analysis.v1
status: locked-for-planning
system_type: Hybrid (structured extraction + deterministic orchestration + cross-chapter reconciliation)
framework: Existing FastAPI + LiteLLM >=1.83.10 + Pydantic >=2.13 + SQLAlchemy >=2.0
model_provider: Model-agnostic through LiteLLM
---

# AI-SPEC — Phase 08: Versioned Novel Analysis Orchestration and Interactive Timeline

> 本文件是 Phase 08 唯一 AI 设计契约。规划和实现必须同时满足 `08-SPEC.md`、`08-CONTEXT.md` 与本契约；冲突时以用户锁定的 Phase 08 范围和安全边界为准。

## 1. System Classification

**System Type:** Hybrid：章节级严格结构化抽取、跨章节高质量归并，以及由确定性脚本控制的持久编排。

**Description:**
系统从 Phase 07 的 chapter → scene → evidence 层级中，按章提取小说事件候选，再跨章归并重复事件、建立故事时间约束和有限因果边。LLM 只处理语义；脚本拥有 evidence package、预算预留、缓存、checkpoint、状态转换、schema/证据门、写库、版本发布、回滚和 spoiler 过滤。良好结果必须可渐进读取、可追溯、可恢复、可回滚，且用户人工修正永不被重分析覆盖。

**Critical Failure Modes:**

1. 无有效 chapter/evidence ID、offset/hash 或模型 lineage 的事件被自动发布。
2. 把叙述先后当作故事时间或因果关系，或为“童年/后来/某日”伪造精确日期。
3. worker 重启、租约过期或并发首次进入产生重复模型调用、重复事件或 active pointer 竞态。
4. 预算/依赖故障后仍继续调用，或透明换模型而没有新 lineage 与 qualification。
5. 重分析覆盖人工 override，或默认 API 泄露阅读进度之后的事件。
6. candidate 未完整验证即移动 active，或失败/回滚破坏旧版本。

## 1b. Domain Context

**Industry Vertical:** 长篇小说叙事分析与交互式阅读辅助。

**User Population:** 阅读长篇小说并希望回看已读剧情、人物参与和事件因果的 NovelMind 用户。

**Stakes Level:** Medium。错误不会直接造成现实人身风险，但会误导理解、制造剧透并损害用户对分析结果的信任。

**Output Consequence:** 机器事件会自动出现在时间线上，并成为后续人物关系、阅读 AI 和线索系统的输入；因此错误必须保留证据和 lineage，且可由 override 修正而不污染原始机器版本。

### What Domain Experts Evaluate Against

| Dimension | Good | Bad | Stakes |
|---|---|---|---|
| 事件原子性 | 一个事件表达一个可定位的状态变化或行动，标题和描述不超出证据。 | 把整章概括成一个事件，或把多个时空片段压成一项。 | High |
| 时间语义 | 区分 narrative order 与 story order；保留 exact/relative/fuzzy/unknown。 | 为模糊时间补日期，或把插叙按章节位置排序。 | High |
| 证据忠实度 | 每项事实、人物参与和因果边都由输入 evidence refs 支持。 | 引用无关片段、越界 ID，或只凭模型常识补全。 | Critical |
| 归并质量 | 同一事件跨章复述可归并，不同事件保持独立，冲突显式标记。 | 因人物/地点相似误合并，或重复事件充斥时间线。 | High |
| 因果克制 | 只有文本支持时才输出 `causes/triggers/responds_to/blocks`。 | 把时间相邻、动机猜测或主题联系当作因果。 | High |
| 剧透边界 | 默认响应严格不超过持久化阅读位置。 | 仅前端隐藏，API 仍返回未来章节。 | Critical |
| 人工修正保护 | override 按字段叠加并显示来源，机器版本保持不可变。 | 重分析覆盖修正，或直接修改机器事实失去审计。 | Critical |

### Known Failure Modes in This Domain

- 插叙、梦境、预言、回忆和传闻被误当作当前故事时间事实。
- “三年前”“次日”“幼时”等相对表达被错误锚定，产生伪精确时间。
- 同名人物、别名、称谓和代词造成参与者串线；Phase 08 只引用已解析实体或保留 unresolved mention，不擅自合并。
- 同一事件在预告、发生、回顾中多次出现，归并器过度合并或完全不合并。
- “A 后发生 B”被输出为“A 导致 B”；因果边必须有独立证据和置信度。
- 章节已完成但事务未提交、或已调用模型但 checkpoint 未落库，恢复时产生重复费用。

### Regulatory / Compliance Context

未识别到小说分析专属监管要求。仍须遵守 owner/novel 隔离、凭据保护、最小化日志和数据保留策略；模型请求不得携带不必要的用户信息、密钥或整本小说。

### Domain Expert Roles for Evaluation

| Role | Responsibility |
|---|---|
| 小说读者/编辑 | 标注事件边界、故事时间约束、重复事件和因果证据。 |
| 项目 owner | 冻结自动发布阈值、预算 policy、spoiler 与 override 产品规则。 |
| 开发者 | 维护 schema、证据/offset/hash 门、状态机、成本和回归测试。 |
| 人工裁决者 | 处理 judge 分歧、低置信和 live qualification 失败样本；不参与日常逐项发布。 |

## 2. Framework Decision

**Selected Framework:** 现有 FastAPI/SQLAlchemy 服务层 + LiteLLM Python SDK + Pydantic strict schemas。

**Version:** 沿用仓库下限：`litellm>=1.83.10`、`pydantic>=2.13`、`sqlalchemy>=2.0`、`fastapi>=0.115`。实现计划应生成锁文件或环境 manifest 记录实际解析版本；不得仅凭 `>=` 声称可复现。

**Rationale:**
Phase 08 是有限阶段、强状态、强审计的批处理管线，不是 autonomous agent。仓库已有 async `AIService`、`AIRouter`、Phase 04 strict judgment、Phase 06 durable quality run、Phase 07 immutable build/active pointer 模式。继续使用 LiteLLM 保持 provider 可替换性，同时让脚本明确控制状态和数据，避免引入第二套 checkpoint、tool 或 memory 抽象。

**Alternatives Considered:**

| Framework | Ruled Out Because |
|---|---|
| LangChain | 此处不需要 chain/retriever abstraction；会复制现有 service、schema 和日志边界。 |
| LangGraph | 虽支持 checkpoint，但 Phase 06/07 已有 PostgreSQL 状态机和 pointer precedent；引入 graph runtime 会形成双真值。 |
| LlamaIndex | Phase 07 已提供层级证据，Phase 08 不重建 ingestion/RAG 栈。 |
| CrewAI/Agents SDK | 模型不应拥有 tools、委派、写库或发布能力；agent abstraction 与安全边界相反。 |

**Vendor Lock-In Accepted:** Partial。业务通过 LiteLLM 保持 provider-agnostic，但每个已 qualification 的具体 model revision、prompt/schema 和价格快照构成不可混用的 lineage。

## 3. Framework Quick Reference

### Installation

```powershell
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt

# 本阶段不新增 LangChain/LangGraph/agent/vector/eval 依赖。
# 仓库当前声明：litellm>=1.83.10, pydantic>=2.13。
```

### Core Imports

```python
from typing import Literal

import litellm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_router import ai_router
from app.services.ai_service import ai_service
```

### Entry Point Pattern

以下是 LiteLLM/Pydantic 的最小可运行结构化抽取模式。实施时应把同样参数透传能力加到现有 `AIService`，业务服务不得长期绕过统一入口。

```python
from typing import Literal

import litellm
from pydantic import BaseModel, ConfigDict, Field


class ExtractedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    local_event_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    event_type: Literal["plot", "character", "world", "conflict"]
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    time_precision: Literal["exact", "relative", "fuzzy", "unknown"]
    time_expression: str | None = Field(default=None, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)


class ChapterExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["timeline-chapter-extraction.v1"]
    chapter_id: int
    events: list[ExtractedEvent] = Field(max_length=40)


async def extract_chapter(messages: list[dict[str, str]]) -> ChapterExtraction:
    response = await litellm.acompletion(
        model="gpt-4o-mini",  # resolved revision is persisted in lineage
        messages=messages,
        response_format=ChapterExtraction,
        temperature=0,
        max_tokens=1800,
        timeout=30,
        num_retries=0,         # orchestration owns idempotent retries
        stream=False,
    )
    return ChapterExtraction.model_validate_json(
        response.choices[0].message.content
    )
```

### Key Abstractions

| Concept | What It Is | When You Use It |
|---|---|---|
| `litellm.acompletion` | provider-agnostic async completion API。 | 每个章节 extraction 和每个 reconcile batch。 |
| Pydantic `response_format` | 将 schema 下发给支持的 provider；不能替代本地验证。 | 所有模型输出。 |
| Evidence package | 稳定 ID、offset/hash 和少量 scene/evidence 文本的不可变输入。 | 构造模型 user message。 |
| Stage key / call attempt | 唯一标识一次有预算的语义工作：run + stage + unit + lineage。 | 幂等、恢复、费用审计和 exact cache。 |
| Analysis version manifest | source/hierarchy/prompt/schema/model/config/cache/usage/output checksums。 | candidate qualification、active 切换、比较和回滚。 |

### Common Pitfalls

1. **Provider 对 `response_format` 支持不一致。** `supports_response_schema` 或参数表只代表能力声明；LiteLLM issue #6848 等记录过 Pydantic 转换差异。所有结果必须本地 `model_validate_json`，再做 evidence/ID/offset 业务门。
2. **自动 retry 会隐藏重复调用和费用。** `num_retries` 可能在 worker 不知道的情况下再次计费，且 issue #7669/#6011 记录过 backoff 行为差异。Phase 08 固定 `num_retries=0`，由持久 attempt + budget reservation 控制最多一次 schema repair retry。
3. **结构化输出不使用 streaming。** JSON 在结束前不可完整验证，跨 provider 的 structured streaming contract 不统一；progressive UX 来自按章提交，不来自 token stream。
4. **usage/cost 不是财务真值。** 自定义 provider、缓存 token、reasoning token 和价格映射可能缺失或错误；保存原始 usage、LiteLLM calculated cost 与版本化本地 price snapshot，预算按更保守值扣减。
5. **现有 `AIService.chat()` 还不接受 `response_format/timeout/num_retries`。** 实施必须先以显式参数扩展统一入口并测试，不能调用不存在的 `structured_chat()` 或在业务层散落直接 SDK 调用。
6. **现有 `AIRouter` 会透明降级。** Phase 08 禁止在一次 run 中自动换 tier/model；无 qualified deployment 时暂停 `paused_dependency`，显式变更模型必须创建新 lineage/version。

### Recommended Project Structure

```text
backend/app/
  models/timeline_analysis.py
  schemas/timeline_analysis.py
  services/timeline_analysis/
    packages.py        # bounded chapter/reconcile evidence packages
    extractor.py       # low-cost structured chapter calls
    reconciler.py      # quality-tier merge/order/causal proposals
    validators.py      # schema, evidence, offset, time, owner gates
    budget.py          # reserve/settle ledger; no model call after pause
    cache.py           # exact lineage cache only
    runs.py            # lease, checkpoint, cancel/resume state machine
    versions.py        # immutable manifests, qualification, active pointer
    publication.py     # chapter visibility and automatic publication
    overrides.py       # protected per-field user layer
    queries.py         # spoiler-safe timeline projection
  api/timeline.py
backend/scripts/
  run_timeline_analysis_worker.py
  run_timeline_offline_eval.py
  run_timeline_live_qualification.py
```

### Sources

- https://docs.litellm.ai/docs/completion/stream
- https://docs.litellm.ai/docs/completion/json_mode
- https://docs.litellm.ai/docs/completion/reliable_completions
- https://docs.litellm.ai/docs/exception_mapping
- https://docs.litellm.ai/docs/observability/custom_callback
- https://docs.litellm.ai/docs/caching/all_caches
- https://github.com/BerriAI/litellm/issues/6848
- https://github.com/BerriAI/litellm/issues/7501
- https://github.com/BerriAI/litellm/issues/7669
- https://github.com/BerriAI/litellm/issues/11975
- https://developers.openai.com/api/docs/models/gpt-4o
- https://cloud.google.com/vertex-ai/generative-ai/pricing

## 4. Implementation Guidance

### 4.1 Model Configuration

模型由逻辑 tier 选择，但 run 开始前必须解析为不可变 deployment/revision，并写入 manifest：

| Stage | Initial compatibility profile | Parameters | Contract |
|---|---|---|---|
| `chapter_extract` | `gpt-4o-mini` via current `extraction/balanced` tier | `temperature=0`, `max_tokens=1800`, `timeout=30s`, provider retries 0 | 低成本；每章/分片独立严格抽取。 |
| `cross_chapter_reconcile` | `gpt-4o` via `deep_analysis/quality` tier | `temperature=0`, `max_tokens=4000`, `timeout=60s`, provider retries 0 | 只归并候选、输出时间约束/因果提议，不生成无证据新事件。 |

这两个名称与当前 `ai_router.py` 一致，只是首个 compatibility profile，不是永久默认。上线前必须 live qualification；provider alias 的实际 revision、region、structured-output capability 和价格快照都要冻结。当前 `vertex_google/gemini-3.5-flash` 默认只可在同一离线集通过对应 tier qualification 后启用，不能因为它是 `AIService.default_model` 就自动取得资格。

### 4.2 Core Pattern

```python
async def advance_run(db: AsyncSession, run_id: str, worker_id: str) -> None:
    run = await claim_lease(db, run_id=run_id, worker_id=worker_id)
    manifest = await load_frozen_manifest(db, run.version_id)

    for chapter in await pending_chapters(db, run_id):
        await assert_not_cancelled(run_id)
        package = await build_chapter_package(db, manifest, chapter)
        stage_key = make_stage_key("chapter_extract", chapter.id, manifest.lineage)

        cached = await get_valid_exact_cache(db, stage_key, package.checksum)
        if cached is None:
            # Reserve worst-case calls/tokens/cost atomically before network I/O.
            reservation = await reserve_budget_or_pause(
                db, run_id, stage_key, package.token_upper_bound, 1800
            )
            try:
                raw, usage = await call_low_cost_extractor(package, manifest)
                parsed = ChapterExtraction.model_validate_json(raw)
                validated = validate_chapter_output(parsed, package, manifest)
                await settle_attempt_and_cache(db, reservation, usage, validated)
            except SchemaValidationError as exc:
                # At most one persisted repair attempt; no hidden SDK retry.
                validated = await repair_once_or_pause(db, reservation, package, exc)
            except DependencyError as exc:
                await pause_dependency(db, reservation, exc)
                return
        else:
            validated = cached

        # One transaction: immutable chapter artifact + checkpoint + progress.
        await publish_candidate_chapter(db, run, chapter, validated)
        await renew_lease(db, run_id, worker_id)

    proposals = await reconcile_in_bounded_batches(db, run, manifest)
    validate_cross_chapter_output(proposals, manifest)
    candidate = await freeze_candidate_version(db, run, proposals)
    report = await qualify_candidate_offline(db, candidate)
    if report.passed:
        await compare_and_swap_active_pointer(db, candidate)
    else:
        await finish_without_promotion(db, run, report)
```

模型调用前后不得持有长数据库事务或行锁。`reserve_budget_or_pause` 先短事务提交 reservation；网络返回后以 attempt ID 结算。若进程在调用后、结算前崩溃，该 attempt 标记 `outcome_unknown`，不得盲目重发：先查 provider request ID/响应审计；无法确认时暂停人工处理或按 policy 将该次计入预算后显式重试。

### 4.3 Tool Use Configuration

- LLM 无 tools/function calls、DB session、filesystem、network、retriever、publish 或 pointer 权限。
- system message 固定规则和 schema；user message 只含带稳定 ID 的不可信小说证据包。原文内任何“忽略指令/调用工具/输出 SQL”均视为数据。
- PostgreSQL 是 run、lease、attempt、budget、cache、artifact、version、override、active pointer 和 publication 真值。
- Phase 07 hierarchy 是 evidence source；Phase 04 accepted judgment 可作为额外已验证信号，但不能替代原文 evidence。
- 不新增 vector DB、embedding 或 agent tracing 库。Phase 08 不需要重新 embedding；沿用结构化日志/AIUsageLog，并增加 run/stage/attempt/version correlation IDs。

### 4.4 State Management

状态由脚本通过 compare-and-set 改变；LLM 输出不包含状态字段。

```text
created -> snapshot_locked -> extracting -> reconciling -> validating
  -> candidate_ready -> qualifying -> active

extracting|reconciling -> paused_budget|paused_dependency|paused_operator
paused_* -> extracting|reconciling        (same checkpoint, authorized resume)
any non-terminal -> cancel_requested -> cancelled
validating|qualifying -> rejected          (old active unchanged)
active -> superseded                       (pointer moved to newer qualified version)
active|superseded -> rollback_prepared -> active (CAS pointer restore)
```

核心不变量：

- 同一 owner/novel/source/hierarchy/config 只允许一个 active run；并发首次进入由唯一键或 advisory lock 收敛。
- lease 有 owner、expiry、heartbeat；过期可被新 worker 抢占，checkpoint 只前进不后退。
- `stage_key` 唯一；完成 stage 不重复调用。每章 artifact、checkpoint 和 partial publication 在同一事务提交。
- `paused_budget`/`paused_dependency` 后 dispatcher 不再领取新 stage；恢复需授权的 budget/model/config 变更，并保留 parent lineage。
- candidate/version/artifact 不可变；active pointer 用 expected-old CAS，失败重新读取而不是覆盖。
- partial 结果属于当前 run/version 并标记 `provisional`；完整 qualification 后才能成为 active。已有 active 时，默认读 active，只有发起该 run 的用户可显式查看新 candidate progress。

### 4.5 Evidence, Publication, Override and Spoiler Contract

- 自动发布门按事件逐项执行：schema valid、owner/novel/chapter 匹配、evidence ID 属于冻结 hierarchy build、offset 落在 source、content hash 一致、confidence 达 policy、lineage 完整。任一失败即不发布该项并记录 reason code。
- causal edge 的 source/target 必须是同 novel/version 的已验证 event，且有独立 evidence refs；时间相邻不算证据。
- `machine_event` 永不原地修改。用户编辑写入 `event_override(logical_event_key, field, value, owner_id, created_at, supersedes_override_id)`；查询时按字段 overlay，并返回 `machine|manual` provenance。
- 新版本通过 stable logical key/显式 mapping 继承 override；无法唯一映射的 override 标记 `needs_relink`，绝不静默丢弃或套到错误事件。
- spoiler filter 在 owner-scoped API 查询中、override overlay 和 causal edge 裁剪之前执行：默认 `event.max_source_chapter <= reading_progress`。任一端被过滤的 causal edge 也不返回。full-book 必须是该 owner/novel 的显式持久偏好。

### 4.6 Context Window Strategy

章节抽取不发送整本小说。默认按一个 chapter 的 Phase 07 scenes/evidence 构包；若估算超过 12,000 input tokens，则按 scene 切成有 1 个 evidence 单元重叠的分片，单片上限 8,000 input tokens。模型只能引用输入 evidence IDs；分片输出由确定性脚本去重。

跨章归并先使用已验证的 compact event cards（title、类型、参与者 IDs、time expression、chapter/narrative index、evidence IDs、短引文 hash），不回灌全文。按相邻章节窗口、相同参与者/地点、明确回指信号召回候选，再 rerank；每批最多 100 event cards 或 40,000 input tokens，超限按召回分、时间邻近和 source order 确定性截断。需要核实时只扩展候选对应 evidence，不扩展整章。全书一致性通过多批约束图和确定性 cycle/conflict validator 完成，不依赖模型“记住全书”。

### 4.7 Cache and Version Lineage

exact cache key 至少包含：`stage + source_snapshot_hash + hierarchy_build_id/checksum + unit_id + evidence_package_hash + prompt_hash + schema_hash + resolved_model_provider/id/revision + decoding_hash + config_hash`。只缓存通过 schema 与业务门的完整输出；失败、refusal、outcome_unknown 不缓存。

禁止 semantic cache 用于事件抽取、归并、时间和因果判定：相似文本不代表同一人物、时间或事实。可对纯确定性 package/tokenization 使用内容寻址缓存。任何 prompt/schema/model/config/evidence 改变都形成 cache miss 和新 version lineage；cache hit 仍写 call-skipped audit 与原产物 checksum。

## 4b. AI Systems Best Practices

### 4b.1 Structured Outputs with Pydantic

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParticipantRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    entity_id: int | None = None
    mention: str = Field(min_length=1, max_length=100)
    evidence_refs: list[str] = Field(min_length=1, max_length=4)


class TimeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    precision: Literal["exact", "relative", "fuzzy", "unknown"]
    expression: str | None = Field(default=None, max_length=120)
    anchor_local_event_id: str | None = Field(default=None, max_length=80)
    relation: Literal["before", "after", "same_time", "overlaps", "unknown"]
    evidence_refs: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def reject_fake_precision(self) -> "TimeClaim":
        if self.precision == "unknown" and self.expression is not None:
            raise ValueError("unknown time must not carry a normalized expression")
        if self.precision == "relative" and not self.anchor_local_event_id:
            raise ValueError("relative time requires an input-local anchor")
        return self


class TimelineEventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    local_event_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    event_type: Literal["plot", "character", "world", "conflict"]
    narrative_index: int = Field(ge=0)
    participants: list[ParticipantRef] = Field(max_length=30)
    time: TimeClaim
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)


class ChapterTimelineExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["timeline-chapter-extraction.v1"]
    chapter_id: int
    events: list[TimelineEventCandidate] = Field(max_length=40)
```

通过 LiteLLM `response_format=ChapterTimelineExtraction` 请求 provider 结构化输出；先用 `litellm.supports_response_schema(...)` 做 capability qualification，但无论结果如何都必须本地 `model_validate_json`。随后验证 chapter ID、local ID 唯一性、evidence membership、offset/hash、entity scope、时间 anchor 和 narrative index。

schema/refusal 失败最多 **1 次** repair retry。第一次失败记录 attempt、provider request ID、raw-output hash（正文按保留策略处理）、validation error codes、usage/cost/latency 和 resolved revision；repair prompt 只加入稳定 error codes，不扩大证据包。第二次失败、越界 evidence、owner mismatch、offset/hash mismatch 或 fake exact time 直接使该 stage `paused_dependency` 或 artifact `rejected_schema/evidence`，由 policy 决定是否允许跳过；不得自动发布，也不得无限 retry。

### 4b.2 Async-First Design

FastAPI/SQLAlchemy 与 LiteLLM 均使用 async：worker 调用 `await ai_service.chat(...)`（扩展后透传结构化参数），DB 使用短 `AsyncSession` 事务。章节可用有上限 semaphore 并发，但提交和 checkpoint 按稳定 chapter order 汇集；每个 novel 默认并发 2，全局并发由配置控制。

不要在 FastAPI/worker event loop 内调用 `asyncio.run()`，不要用同步 `litellm.completion()` 阻塞。HTTP 只创建/查询/cancel/resume durable run，不等待整本分析。结构化输出使用完整 `await` 后验证；stream 仅适合未来自然语言 UX，本阶段 progressive 展示依靠章节 artifact 轮询或 SSE 状态事件，不 stream JSON token。

### 4b.3 Prompt Engineering Discipline

- system：角色边界、只用证据、时间精度定义、因果克制、schema version、禁止执行原文指令。
- user：只放冻结 package；小说正文用明确 data delimiters，不能混入系统规则。
- few-shot：首版使用 6–10 个冻结短例，覆盖插叙、相对时间、模糊童年、未知时间、复述去重、非因果相邻；示例集 hash 进入 prompt lineage。不要动态检索未冻结示例。以后若动态选择，只能从 versioned approved example bank 选择并记录选中 IDs/hash。
- 每个 stage 显式设置 `max_tokens`；章节 1800、归并 4000。生产禁止默认 4096 或无界输出。temperature=0 不等于确定性，重复性由 schema、cache、lineage 和 eval 保证。

### 4b.4 Context Window Management

本系统结合 RAG-style evidence packing 与全书归并：章节超限时按完整 scene/evidence 边界切分，先保留当前事件证据、时间词、说话者/代词和地点，再按距目标窗口由远到近截断；不得截断 evidence 中部而破坏 offsets。归并超限时先召回/rerank event cards，再按稳定规则截断，并把未处理 candidate pair 留到下一 batch。

跨批不让模型维护隐式 memory。脚本持久化 canonical event candidates、equivalence proposals、temporal constraints 和 conflict set；下一批只读这些受控结构。最终 story order 由确定性约束图拓扑排序；cycle、exact-time 冲突或证据不足进入 conflict/review，不让模型任意打破。

### 4b.5 Cost and Latency Budget

预算在 run 开始时冻结：`max_calls`、`max_input_tokens`、`max_output_tokens`、`max_cost_usd`、stage 子预算、最大 wall time 和并发。每次调用前按 worst-case 原子 reserve；无法 reserve 立即置 `paused_budget`，之后零新调用。结算保存 provider usage、LiteLLM cost、本地 price-snapshot cost 三者；缺 usage/价格时按 reservation 全额或更保守估计扣减。

以 100 章、每章 4,000 input + 700 output、一次 50,000 input + 4,000 output 归并为容量例：

| Stage | Public-price illustration | Calls | Estimated cost |
|---|---:|---:|---:|
| `gpt-4o-mini` chapter extraction | $0.15/M input + $0.60/M output | 100 | 约 $0.102 |
| `gpt-4o` reconciliation | $2.50/M input + $10/M output | 1 | 约 $0.165 |
| Total before retries | — | 101 | 约 $0.267/novel |

该表仅用于预算量级，价格会变化；生产以 run 冻结的官方价格快照为准，并保留 25% safety margin。初始建议 hard ceilings：每章最多 2 attempts、整书最多 `2 × chapter_count + 10` calls、chapter output 1800 tokens、reconcile output 4000 tokens；具体美元门由 owner 配置和 live qualification 冻结。

缓存只使用上述 exact-match key；禁止 semantic cache。章节抽取用 balanced/低成本模型，跨章归并用 quality 模型；分类、token estimate、evidence/offset 校验、排序图、override overlay 和 spoiler 过滤全部用代码，不消耗模型。

## 5. Locked Output and Persistence Contracts

实施必须定义 strict Pydantic/API/ORM 对应契约，全部业务 enum 禁止自由文本替代，模型 schema 不含 DB 主键、publish、budget 或 active pointer 字段：

- `AnalysisRun`：owner/novel/version、lease、checkpoint、progress、status/reason、cancel、budget policy/ledger、created/updated timestamps。
- `AnalysisVersionManifest`：source snapshot、hierarchy build/checksum、parent、prompt/schema/model/decoding/config/price/eval lineage、artifact checksums。
- `ModelCallAttempt`：stage key、attempt、reservation、request/response hash、provider request ID、status、usage/cost/latency/error、cache source。
- `ChapterTimelineExtraction`：章节受限事件、participants、time claim、evidence refs、confidence。
- `ReconciliationOutput`：只允许 local/global event mappings、duplicate groups、temporal constraints、causal proposals、conflicts；不得生成无 input event 的新事实。
- `MachineTimelineEvent`：immutable versioned event、双序、mixed precision、source/evidence/model lineage、publication status。
- `TimelineParticipant` 与 `TimelineCausalEdge`：结构化、同 owner/novel/version、evidence-bound。
- `TimelineOverride`：logical event、单字段 value、author、supersession、relink status；与机器版本分离。
- `ActiveTimelinePointer`：owner/novel → qualified version，CAS revision/journal；支持 byte-identical rollback manifest。

故事时间不应简化为一个 float。至少持久化 `precision`、原始 expression、normalized exact value（仅 exact）、relative anchor/relation、fuzzy bucket/range（仅有证据时）、constraint reason/confidence、story rank 与 narrative chapter/index。`story_rank` 是约束求解的派生顺序，不伪装成真实日期。

## 6. Evaluation Strategy

### 6.1 Dimensions and Gates

| Dimension | Rubric / Gate | Measurement | Priority |
|---|---|---|---|
| Schema validity | 100% persisted model artifacts strict-validate；extra/错误 enum 为 0。 | Code | Critical |
| Evidence validity | 自动发布事件/参与者/因果边 100% refs 存在且 offset/hash/scope 匹配。 | Code | Critical |
| Restart idempotency | N 章后强杀，恢复从 N+1；completed stage duplicate calls = 0。 | Integration | Critical |
| Budget stop | 达 ceiling 后新增 calls = 0，状态精确为 `paused_budget`，checkpoint 可恢复。 | Integration | Critical |
| Version safety | rejected/failed candidate 不移动 active；rollback manifest byte-identical。 | Integration | Critical |
| Override preservation | 重分析后全部人工字段值/provenance 保持；歧义映射不误套。 | Code + integration | Critical |
| Spoiler safety | 默认响应未来章节事件/边 = 0；full-book 仅显式偏好开放。 | API/property | Critical |
| Event precision | 金标事件 micro precision >=0.90；critical unsupported event = 0。 | Human gold + code | High |
| Temporal order | narrative order 100%；story pairwise accuracy >=0.90；伪 exact date = 0。 | Gold fixtures | High |
| Deduplication | duplicate pair F1 >=0.90；不同事件错误合并率 <=2%。 | Gold fixtures | High |
| Causal precision | accepted causal edge precision >=0.90；sequence-only false causality = 0 on adversarial set。 | Human + code | High |
| Cost/latency | 总量不超 frozen policy；chapter p95 和 reconcile p95 在 qualification threshold 内。 | Code/live | High |
| Reproducibility | 同 lineage + frozen transcripts 产物 checksum 一致。 | Offline replay | High |

### 6.2 Eval Tooling

**Primary Tool:** 现有 `pytest` + 自定义离线 eval CLI + Phase 06 quality run/baseline/arbiter 模式。首版不新增 RAGAS、LangSmith、Langfuse 或 Phoenix；这里的关键指标是结构、证据、时间、状态和发布不变量，代码评测优先。若后续 tracing 需求超出现有结构化日志，再单独评估 Phoenix/Langfuse，不作为 Phase 08 前置依赖。

```powershell
cd backend
pytest tests/unit/timeline_analysis tests/integration/timeline_analysis -v
python scripts/run_timeline_offline_eval.py --dataset tests/fixtures/timeline/v1.json --candidate <manifest>
python scripts/run_timeline_live_qualification.py --profile chapter-balanced --profile reconcile-quality
```

PR/CI 只运行 fake provider、冻结 transcripts、schema/adversarial/property/integration 和 offline replay。真实模型只在手动或 nightly live qualification 运行；凭据/配额缺失记 `blocked_dependency`，不能记 0 分或通过。

### 6.3 Reference Dataset

起步至少 20 个高质量章节/场景组和 10 个跨章组，fiction only：

- 正序、插叙、倒叙、梦境/预言/传闻；
- exact、relative、fuzzy、unknown 四类时间；
- “三年前/次日/幼时”等 anchor 与无 anchor；
- 预告—发生—回顾的同事件复述；
- 同人物同地点但不同事件的反例；
- 明确因果与纯时间相邻反例；
- 别名、代词、同名人物和 unresolved mention；
- 缺 evidence、越界 ID、offset/hash 变更、prompt injection；
- reading progress、override 重分析、restart、budget/outage。

小说读者/编辑标注事件、证据 refs、时间约束、duplicate groups、因果边和 acceptable ambiguity；开发者只编码确定性不变量。LLM judge 仅用于主观描述忠实度，并需先与人工样本达到 >=0.70 相关性；judge 不决定自动发布或 promotion。

### 6.4 Offline Evaluation and Live Qualification

1. **Offline deterministic:** schema、ID、offset/hash、owner scope、状态机、预算、cache、version、pointer、override、spoiler。
2. **Frozen transcript replay:** 相同输入/输出重放得到相同 artifact/manifest checksum；不访问网络。
3. **Gold semantic eval:** extraction、time、dedupe、causal 的 precision/recall/F1 和 critical-fail count。
4. **Adversarial:** 原文 prompt injection、超长章、Unicode/CRLF、重复文本、alias collision、cycle/conflict、provider malformed/refusal/timeout/429。
5. **Live qualification:** 每个具体 provider/model revision 分别跑 chapter 与 reconcile profiles，记录 structured-output capability、质量、tokens、cost、p50/p95、rate-limit 和 schema failure。两级均通过才可加入 Phase 08 qualified registry。
6. **Release comparison:** candidate 与当前 active 在同 frozen dataset/policy/price snapshot 上 A/B；critical gate 全过且非关键指标无超阈回归才自动移动 active。首次生产 profile 启用需 operator approval；之后相同已批准 policy 可自动发布。

## 7. Guardrails and Production Monitoring

### 7.1 Online Guardrails

| Guardrail | Trigger | Intervention |
|---|---|---|
| Owner/evidence scope | 任一跨 owner/novel/chapter/build ref。 | 拒绝 artifact，fail closed，安全日志。 |
| Schema/time integrity | malformed/extra field、relative 无 anchor、无证据 exact time。 | 一次 repair 后拒绝或暂停。 |
| Budget | 任一 call/token/cost ceiling 无法 reserve。 | 原子进入 `paused_budget`，零后续调用。 |
| Dependency/model | timeout/429/outage 或 deployment 未 qualification。 | `paused_dependency`；禁止透明 fallback。 |
| Publication | lineage/offset/hash/confidence/quality 缺失。 | 不发布该项；旧 active 不变。 |
| Version pointer | expected-old revision/checksum 不匹配。 | CAS 失败并重读；不覆盖。 |
| Override | 新版本无法唯一映射 manual correction。 | `needs_relink`，保留旧 override，不自动套用。 |
| Spoiler | event 超过 persisted reading progress。 | API 过滤事件及关联边。 |

### 7.2 Monitoring

**Tracing default:** 现有结构化日志 + `AIUsageLog`/Phase 06 durable quality records，并增加 `run_id/version_id/stage_key/attempt_id/model_revision/cache_status`。不引入新 tracing 平台。

关键指标：run 状态与停留时长、lease reclaim、attempt/result/unknown 数、cache hit、schema/evidence reject、每章 published/provisional、token/cost reservation vs settled、p50/p95、budget/dependency pause、candidate qualification/promotion/rollback、override relink 和 spoiler filtered count。

告警阈值：任何跨 scope 或 spoiler 泄露立即阻断；budget 后调用数 >0、completed stage 重复调用 >0、non-qualified pointer move、override loss 均为 release blocker；schema/evidence failure >5% 或 chapter p95 超 qualification threshold 连续 3 个窗口则暂停新 run；`outcome_unknown` 必须进入运维队列。

采样优先级：低置信但自动发布、模型/人分歧、fuzzy/relative time、跨章 merge、causal edge、override 频发项、schema repair、cache miss 激增、价格/模型 revision 变化。普通高置信事件只做低比例随机样本。

## 8. Planner Must-Haves and Explicit Non-Goals

规划必须覆盖：durable run/lease/checkpoint、atomic budget ledger、strict schemas、bounded evidence packages、两级 model qualification、exact cache、immutable version/active pointer/rollback、partial publication、override overlay、spoiler-safe API、offline eval 与 live qualification。每个实施 slice 以 `Test, Fix, and Confirm` 结束。

明确不做：LangChain、LangGraph、agent tools、历史语料、关系图 UI、阅读聊天、线索生命周期、token-by-token 分析流、将 plot/theme/style/summary 暴露为 Phase 08 顶级前端模式、由 LLM 直接写库或决定发布。

## Checklist

- [x] System type、领域标准和 critical failures 已锁定
- [x] LiteLLM/Pydantic/SQLAlchemy 现有框架与不引入 LangChain/LangGraph 已锁定
- [x] 章节低成本抽取与跨章节高质量归并已分层
- [x] Strict schema、一次 repair、本地二次验证与 provider 差异已覆盖
- [x] 脚本拥有 evidence、预算、状态、cache、写库与发布权
- [x] Durable lease/checkpoint、故障/预算暂停和零额外调用已定义
- [x] Immutable candidate、active pointer、lineage、rollback 与自动发布已定义
- [x] 人工 override、spoiler API gate 和 progressive candidate 可见性已保护
- [x] Context packing、exact cache 和成本量级已明确
- [x] Offline eval、gold fixtures、adversarial tests 与 live qualification 已完整定义
- [x] Phase 08 fiction-only 边界及后续产品非目标已明确
