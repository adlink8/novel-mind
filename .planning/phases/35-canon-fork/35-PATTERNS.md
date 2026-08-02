# Phase 35 Patterns — File-to-Analog Map

| 拟改/新增文件 | 当前代码 analog | 应复用的模式 | 硬门 |
|---|---|---|---|
| `backend/app/models/canon_fork.py` | `backend/app/models/narrative_memory.py` | composite owner/novel/version scope、immutable checksum、source links | space/fork/version 必须进入 identity/FK |
| `backend/app/services/canon_fork/contracts.py` | `backend/app/services/narrative_memory/retrieval_contracts.py` | strict frozen Pydantic、cutoff snapshot、policy hash | unknown space/fork fail closed |
| `backend/app/services/canon_fork/retrieval.py` | `backend/app/services/narrative_memory/retrieval_manifests.py` | visible-set-first、leaf citation revalidation | branch-aware scope 先于 ranking |
| `backend/app/services/canon_fork/authority.py` | `backend/app/api/dependencies.py` + `narrative_memory/authority.py` | owner lookup、candidate-only authority | 原作只读，无 active cutover |
| `backend/app/services/canon_fork/contamination.py` | `backend/tests/adversarial/test_narrative_memory_retrieval_safety.py` | negative assertions and no-side-effect checks | derivative 不得进 Original index/eval/facet |
| `backend/app/api/canon_fork.py` | `backend/app/api/narrative_memory.py` | authenticated typed routes and honest blocked status | 不返回假空数组 |
| `frontend/src/lib/canon-fork-api.ts` | `frontend/src/lib/narrative-memory-api.ts` | typed envelopes/badges | UI 不自行判 authority |

**Do not mirror:** `backend/app/api/fanfiction.py` 的 501 placeholder 只能作为 deferred baseline，不能作为 domain contract。[CITED: repository files]
