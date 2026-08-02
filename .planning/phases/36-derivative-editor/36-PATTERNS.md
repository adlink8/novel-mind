# Phase 36 Patterns — File-to-Analog Map

| 拟改/新增文件 | 当前代码 analog | 应复用的模式 |
|---|---|---|
| `backend/app/models/derivative.py` | `backend/app/models/reader_chat.py`, `fanfiction.py` | owner scope, timestamp, FK cascade only inside derivative space |
| `backend/app/schemas/derivative.py` | `backend/app/schemas/reader_chat.py` | strict patch DTO, explicit base revision and fork |
| `backend/app/services/derivative_editor/projects.py` | `backend/app/api/dependencies.py`, `novel_service.py` | owner lookup and transaction boundary |
| `backend/app/services/derivative_editor/revisions.py` | `reader_chat/conversations.py`, `clues/versions.py` | sequence/idempotency, append-only lineage, conflict response |
| `frontend/src/lib/derivative-api.ts` | `frontend/src/lib/api.ts`, `clue-api.ts` | typed API and status discriminants |
| `frontend/src/components/writing/markdown-editor.tsx` | `frontend/src/app/writing/page.tsx`, `reader/reader-content.tsx` | preserve writing visual shell; replace placeholder only after API contract |
| `frontend/e2e/derivative-editor.spec.ts` | `frontend/e2e/reader-chat.spec.ts`, `helpers.ts` | register/login, owner fixture, desktop/mobile flows |

Revision writes must be server-arbitrated; no client-only localStorage history.[CITED: current reader chat model/service patterns]
