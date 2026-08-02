# Phase 30: Visual Bible - Patterns

## File-to-analog map

|拟改/新增边界|当前代码 analog|复用方式|
|---|---|---|
|`backend/app/models/visual_bible.py`|`backend/app/models/knowledge_unit.py`, `reader_chat.py`|owner/novel/version 外键、hash、JSON lineage、唯一约束；不要复用 cover 字段。|
|`backend/app/schemas/visual_bible.py`|`backend/app/schemas/reader_chat.py`, `clue.py`|strict schema、闭合集合、authority/review enum、offset/hash 校验。|
|`backend/app/services/visual_bible/authority.py`|`narrative_memory/authority.py`, `audit.py`|candidate revision、来源快照、只读/显式 review boundary。|
|`backend/app/services/visual_bible/evidence.py`|`narrative_memory/citations.py`, `retrieval_manifests.py`|EvidenceRef 物化、source snapshot 与 content hash 重验。|
|`backend/app/api/visual_bible.py`|`api/narrative_memory.py`, `api/asset_audit.py`|owned novel dependency、只读查询与显式 review action 分离。|
|`frontend/src/lib/visual-bible-api.ts`|`narrative-memory-api.ts`, `clue-api.ts`|typed envelopes、候选 badge、错误/partial 状态。|
|`frontend/src/components/visual-bible/*`|`components/structure/*`, `components/reader/*`|workspace shell、evidence panel、review action、responsive layout。|
|`backend/tests/unit/visual_bible/*`|`tests/unit/narrative_memory/*`, `tests/unit/reader_chat/*`|纯契约/authority/review fixture。|

## Artifact flow

`source snapshot → typed claim candidate → evidence recheck → VisualBibleVersion(candidate) → human review event → review envelope`

每个箭头都要保留 `owner_id`, `novel_id`, `version_id`, `source_snapshot`, `cutoff`, `content_hash`；review 结果不得改写源章节。 [VERIFIED: existing lineage analogs]

## Candidate Artifact envelope

```text
artifact_kind = visual_bible
artifact_id / revision_id
owner_id / novel_id
source_snapshot_id + manifest_hash
schema_version + policy_hash
entities[] / style_profile / constraints[] / reference_assets[]
authority_labels + evidence_refs[]
review_status + review_events[]
```

## Anti-patterns

- 把 `Novel.cover_url` 迁移成插图资产表。
- 用 embedding 相似度或模型描述直接写 `canon_fact`。
- 复用 Phase 12 的 `optional_unavailable` 作为“用户已拒绝”；这是不同状态。
- 在前端保存 review truth；浏览器只提交 action，服务端决定合法 transition。
