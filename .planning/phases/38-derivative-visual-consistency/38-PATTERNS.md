# Phase 38 Patterns — File-to-Analog Map

| 拟改/新增文件 | 当前代码 analog | 应复用的模式 |
|---|---|---|
| `backend/app/models/derivative_visual.py` | Phase 33 `AssetRevision` contract, `narrative_memory.py`, `chunk_build.py` | immutable version/build, content checksum, source links |
| `backend/app/services/derivative_visual/fork.py` | `narrative_memory/authority.py` | read-only source snapshot, explicit fork |
| `backend/app/services/derivative_visual/scene_spec.py` | Phase 32 Scene Spec/prompt compiler, `chunking_service.py`, timeline extraction | deterministic structured spec before provider |
| `backend/app/services/derivative_visual/assets.py` | `novel_service.py` storage/compensation | safe generated path, hash, cleanup on failure |
| `backend/app/services/derivative_visual/gates.py` | `clues/gates.py`, qualification verifier | namespace/identity/provenance/review gate |
| `frontend/src/components/writing/visual-review-panel.tsx` | Phase 34 anchor/review flow, `clue-evidence-panel.tsx`, `relationship-evidence-panel.tsx` | evidence/source panel and explicit review action |
| `frontend/e2e/derivative-visual.spec.ts` | `clue-real.spec.ts`, `error-and-isolation.spec.ts` | cross-owner and mixed-original/derivative scenarios |

Phase 30–34 planning artifacts define upstream contracts; planner must reconcile proposed derivative paths with their eventual implementation before creating tasks.[CITED: `.planning/phases/30-visual-bible` through `34-illustration-anchor-export`]
