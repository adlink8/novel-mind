# Testing Architecture

Generated: 2026-06-12

## Test Numbers

| Suite | Count | Passed | Framework |
|---|---|---|---|
| Backend | 172 | 100% | pytest + pytest-asyncio |
| Frontend | 22 | 100% | Vitest + React Testing Library |

## Backend Tests

```bash
cd backend
source venv/Scripts/activate
pytest tests/ -v                          # All 172
pytest tests/test_auth.py -v              # Auth: register/login/logout/isolation
pytest tests/test_novels.py -v            # Novels: upload/list/detail/delete/encoding
pytest tests/test_security.py -v          # Security: SSRF/owner isolation/upload safety
pytest tests/test_crypto.py -v            # Crypto: Fernet encrypt/decrypt/key rotation
pytest tests/test_chunking.py -v          # Chunking: semantic split/chunk types
pytest tests/test_vector_store.py -v      # Vector: ChromaDB write/search/delete
pytest tests/test_indexing.py -v          # Indexing: pipeline end-to-end
pytest tests/test_rag.py -v               # RAG: search/trigger/status
pytest tests/test_ai_models.py -v         # AI: config CRUD/test/set-default
```

## Frontend Tests

```bash
cd frontend
npm test                                  # All 22
npm test -- --coverage                    # With coverage
npm run test:watch                        # Watch mode
```

## Static Analysis

```bash
# Backend
cd backend
.\.venv\Scripts\python.exe -m ruff check app tests migrations    # Python lint
.\.venv\Scripts\python.exe -m bandit -r app -ll -q               # Security scan
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable  # Dependency audit

# Frontend
cd frontend
npm run lint                              # ESLint
npm run build                             # Production build
npm audit                                 # Dependency audit
```

## Migration Verification

```bash
cd backend
.\.venv\Scripts\python.exe -m alembic current    # Show current head
.\.venv\Scripts\python.exe -m alembic check      # Verify no drift
```

## CI Workflows

| Workflow | File | Triggers |
|---|---|---|
| Backend Tests | `.github/workflows/backend-tests.yml` | push/PR on main/master/develop |
| Frontend Tests | `.github/workflows/frontend-tests.yml` | push/PR on main/master/develop |

## VERIFIED Standard

```
VERIFIED = code exists + wired into main path + test coverage + CI passes
```
