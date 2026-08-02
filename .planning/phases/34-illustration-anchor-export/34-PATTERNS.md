# Phase 34: Illustration Anchors, Reader and Export - Patterns

|拟改/新增边界|当前代码 analog|映射规则|
|---|---|---|
|`backend/app/models/illustration_anchor.py`|`models/reader_chat.py` selection/evidence|immutable offsets/hash/source snapshot, proposal asset FK and published asset FK.|
|`backend/app/schemas/illustration_anchor.py`|`schemas/reader_chat.py` Selection/Citation|strict range, status, caption, repair action, owner scope.|
|`backend/app/services/illustration_anchors/repair.py`|`reader_chat/retrieval.py`, `novel_service.py`|revalidate exact hash; propose candidates; never auto-mutate.|
|`backend/app/services/export/manifest.py`|`reader_chat/context.py`, `narrative_memory/manifests.py`|freeze text/assets/citations/version hashes once per export.|
|`frontend/src/components/reader/illustration-block.tsx`|`reader/reader-content.tsx`, `progress-bar.tsx`|flow-layout figure, lazy image, fallback, keyboard/focus/accessibility.|
|`frontend/src/lib/illustration-anchor.ts`|`lib/reader-selection.ts`|reuse UTF-16/code-point and hash helpers.|
|`backend/app/services/export/markdown.py`, `epub.py`|`backend/scripts/export_openapi.py` only as script boundary analog|deterministic adapters consume one manifest; no independent DB reads.|

## Flow

`proposal_ready AssetRevision + AnchorProposal → Web Approval → deterministic published asset + valid anchor → reader/export`

## Avoid

- placing images by raw DOM index;
- auto-repairing to the nearest text without human review;
- using `dangerouslySetInnerHTML` for captions or export preview;
- dropping missing assets from export without a manifest error.
