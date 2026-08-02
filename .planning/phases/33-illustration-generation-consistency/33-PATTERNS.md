# Phase 33: Illustration Generation and Consistency - Patterns

|拟改/新增边界|当前代码 analog|映射规则|
|---|---|---|
|`backend/app/models/illustration.py`|`models/reader_chat.py`, `models/ai_model.py`|owner/novel, immutable revision, approval, hashes, provider metadata.|
|`backend/app/models/illustration_job.py`|`ReaderGenerationJob`, `ReaderModelCallAttempt`|lease, cancel, retry, explicit failure/unknown, unique nonterminal idempotency.|
|`backend/app/services/illustrations/gateway.py`|`reader_chat/gateway.py`, `ai_service.py`|provider-neutral request/response, redacted errors, usage/cost.|
|`backend/app/services/illustrations/worker.py`|`reader_chat/worker.py`|claim/heartbeat/settle/retry/reconcile; no silent success.|
|`backend/app/services/illustrations/storage.py`|`novel_service.py` upload containment|content-hash bytes, owner containment, MIME/size checks, quarantine cleanup.|
|`backend/app/services/illustrations/consistency.py`|`narrative_memory/qualification_*`, `rag_quality.py`|frozen fixture evaluator and versioned report.|
|`frontend/src/components/illustrations/*`|`components/reader/reader-chat-panel.tsx`, `components/structure/*`|candidate gallery, lineage drawer, compare, approval.|

## Candidate Artifact flow

`PromptRevision → IllustrationJob → Attempt(s) → AssetRevision(candidate) → ConsistencyReport → HumanReview → proposal_ready for Phase 34`

## Anti-patterns

- Use `Novel.cover_url` or raw `backend/storage/images` as job authority.
- Store provider response only in a file with no DB lineage.
- Retry a timeout blindly without outcome reconciliation.
- Let consistency score auto-approve or rewrite Visual Bible.
