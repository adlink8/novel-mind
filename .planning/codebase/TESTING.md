# Testing Conventions — NovelMind Backend

## Running Tests

```bash
cd backend
pytest tests/ -x -q
```

- `-x` — stop on first failure (fail-fast).
- `-q` — quiet output; use `-v` for verbose mode when debugging a single file.
- Run a single file: `pytest tests/test_knowledge_gates.py -x -v`
- Run by marker: `pytest tests/ -m unit` or `pytest tests/ -m e2e`

---

## pytest Configuration

Located in `backend/tests/conftest.py`. Key setup:

- **Database**: SQLite in-memory (`sqlite+aiosqlite:///:memory:`) — no external PostgreSQL required for the test suite.
- **Engine**: `create_async_engine` with `echo=False`.
- **Session factory**: `async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)`.
- **HTTP client**: `httpx.AsyncClient` with `ASGITransport(app=fastapi_app)` for API-layer tests.
- **Model registration**: `import app.models  # noqa: F401` ensures all ORM tables are present in `Base.metadata` before `create_all`.
- **Fixtures** are `pytest_asyncio` async fixtures, scoped per function by default.

---

## Test File Inventory

### Infrastructure & Core

| File | Responsibility |
|------|----------------|
| `conftest.py` | DB engine, session fixtures, AsyncClient, model import |
| `test_health.py` | `/api/health` endpoint smoke test |
| `test_security.py` | JWT auth, token validation, `require_user` dependency |
| `test_models.py` | ORM model field constraints and relationship traversal |

### Novel Domain (Unit + Integration)

| File | Responsibility |
|------|----------------|
| `test_novels.py` | Novel CRUD, upload, chapter split, import status polling |
| `test_import_job.py` | `ImportJob` state machine transitions |
| `test_chunking.py` | `ChunkingService` — chunk boundaries, overlap, word-count |
| `test_characters.py` | Character CRUD and novel ownership |
| `test_timeline.py` | Timeline event CRUD |
| `test_analysis.py` | Analysis run creation and result retrieval |
| `test_fanfiction.py` | Fanfiction generation flow |

### AI / Search (Unit + Integration)

| File | Responsibility |
|------|----------------|
| `test_services.py` | `NovelService`, `ImportService` business logic |
| `test_vector_store.py` | Vector upsert, cosine-similarity retrieval |
| `test_indexing.py` | `IndexingService` pipeline — chunk → embed → store |
| `test_rag.py` | RAG unit: retriever, context assembly, prompt build |
| `test_rag_e2e.py` | End-to-end RAG: question → retrieved context → LLM answer |
| `test_hybrid_search.py` | BM25 + vector hybrid ranking |

### Eval Pipeline

| File | Responsibility |
|------|----------------|
| `test_eval_models.py` | Eval ORM models and schema contracts |
| `test_eval_api.py` | Eval REST endpoints |
| `test_eval_service.py` | `EvalService` logic — scoring, aggregation |
| `test_eval_candidates.py` | Eval candidate selection and filtering |

### Knowledge Graph (Phase 01 gate)

| File | Responsibility |
|------|----------------|
| `test_knowledge_models.py` | Knowledge ORM models, DOMAIN_PROFILES, RELATION_TYPES |
| `test_knowledge_candidates.py` | Deterministic recall — `RelationCandidateDraft` construction |
| `test_knowledge_llm_judge.py` | LLM judgment parsing, confidence normalisation |
| `test_knowledge_gates.py` | Gate routing logic, terminal-status idempotency |
| `test_knowledge_projection.py` | Graph projection write and conflict handling |
| `test_knowledge_api.py` | Knowledge REST endpoints |
| `test_knowledge_eval.py` | Knowledge eval scoring contracts |

---

## Test Classification

### Unit tests

Test a single function or class in isolation. External dependencies (DB, LLM, vector store) are replaced with in-memory fakes or pytest monkeypatching. Fast — run in milliseconds.

Examples: `test_chunking.py`, `test_knowledge_gates.py` (fixture-based DB), `test_eval_models.py`.

### Integration tests

Test a service + real async SQLite session end-to-end, without a live HTTP server. Verify that ORM writes, queries, and business logic compose correctly.

Examples: `test_knowledge_candidates.py`, `test_import_job.py`, `test_services.py`.

### e2e RAG tests

Full stack: `AsyncClient` → FastAPI router → service → SQLite → (mocked or live) LLM. Marked `@pytest.mark.e2e`. Skipped by default in CI if `OPENAI_API_KEY` is absent.

Examples: `test_rag_e2e.py`.

---

## Baseline Coverage

The suite contains **239 passing tests** as of the Phase 01 baseline. The breakdown is approximately:

- Core / infra: ~20 tests
- Novel domain: ~60 tests
- AI / search: ~50 tests
- Eval pipeline: ~40 tests
- Knowledge graph: ~69 tests

---

## Phase 01 Zero-Regression Gate

Before merging any Phase 01 branch:

1. Run the full suite: `cd backend && pytest tests/ -x -q`
2. All **239 pre-existing tests** must remain green. No skips allowed on previously-passing tests.
3. New tests added by Phase 01 must also pass.
4. No `# type: ignore` or `# noqa` additions without a comment justifying the suppression.
5. The knowledge-graph sub-suite is the primary regression target — run it first: `pytest tests/test_knowledge_*.py -x -v`
