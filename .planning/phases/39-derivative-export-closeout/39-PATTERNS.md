# Phase 39 Patterns — File-to-Analog Map

| 拟改/新增文件 | 当前代码 analog | 应复用的模式 |
|---|---|---|
| `backend/app/services/derivative_export/snapshot.py` | `reader_chat/context.py`, `narrative_memory/retrieval_manifests.py` | one frozen scope/manifest before rendering |
| `backend/app/services/derivative_export/manifest.py` | `narrative_memory/manifests.py`, quality reports | canonical JSON/hash, explicit status |
| `backend/app/services/derivative_export/markdown.py` | existing chapter/content serializers (none authoritative) | deterministic ordering/escaping; new pure serializer |
| `backend/app/services/derivative_export/epub.py` | none; `storage/` and file handling are analogs | bounded archive entries, deterministic metadata |
| `backend/app/api/derivative_export.py` | `api/narrative_memory.py`, `api/novels.py` | owner scope, authenticated download, honest errors |
| `backend/tests/adversarial/test_derivative_export_isolation.py` | `test_reader_chat_boundaries.py`, `test_narrative_memory_retrieval_safety.py` | no future/original/cross-owner leakage |
| `frontend/e2e/derivative-export.spec.ts` | `frontend/e2e/core-flow.spec.ts`, `error-and-isolation.spec.ts` | full browser workflow and failure evidence |

Both serializers must consume the same snapshot DTO; never re-query live rows separately.[CITED: existing manifest patterns]
