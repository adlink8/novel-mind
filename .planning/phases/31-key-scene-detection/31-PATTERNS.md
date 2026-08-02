# Phase 31: Key Scene Detection - Patterns

|拟改/新增边界|当前代码 analog|映射规则|
|---|---|---|
|`backend/app/models/key_scene.py`|`knowledge.py`, `narrative_memory/*`|candidate/version/source snapshot and append-only review lineage.|
|`backend/app/schemas/key_scene.py`|`reader_chat.py`, `timeline.py`|strict coordinates, cutoff, evidence refs, score breakdown.|
|`backend/app/services/key_scenes/boundaries.py`|`chunking/hierarchy.py`, `segmentation.py`|reuse persisted chapter/scene evidence boundaries; preserve malformed-state reasons.|
|`backend/app/services/key_scenes/scoring.py`|`rag_quality.py`, `clues/eval.py`|pure deterministic score, policy hash, canonical ordering.|
|`backend/app/services/key_scenes/review.py`|`clues/gates.py`, `clues/overrides.py`|explicit human action and immutable decision history.|
|`frontend/src/components/key-scenes/*`|`components/structure/*`, `components/reader/*`|candidate cards, reason drawer, source jump, mobile review.|

## Candidate Artifact

`SceneCandidateSet` contains source snapshot, cutoff, detector/policy versions, ordered candidates, diversity groups, and review decisions. It is an input to Phase 32, not an image job.

## Avoid

- `embedding_score` as the sole rank or a canon field.
- Recomputing candidates in the browser.
- Discarding rejected candidates or treating missing signal as score zero.
