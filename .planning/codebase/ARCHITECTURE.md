# Codebase Architecture

Generated: 2026-06-12 | Source: actual code

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + SQLAlchemy async + Pydantic v2 |
| Database | PostgreSQL 16 + pgvector |
| Vector Store | ChromaDB (HTTP API) |
| AI | LiteLLM 1.83.10+ |
| Frontend | Next.js 16.3.0 (App Router) + React 19 + TypeScript + Tailwind |
| State | Zustand 5.x |
| HTTP Client | Axios (withCredentials) |
| Testing | pytest 172 / Vitest 22 |
| CI | GitHub Actions (3 workflows) |
| Migrations | Alembic (head: f3c8b7b2dbf7) |

## Runtime Topology

```
Browser → Next.js 16 → Axios (Cookie) → FastAPI → Service → ORM → PostgreSQL
                                                    → ChromaDB
                                                    → AI Providers (LiteLLM)
```

## Module Map

| Module | Dir | Status |
|---|---|---|
| API Routes | backend/app/api/ | VERIFIED (auth/novels/models/rag); 501 (analysis/timeline/characters/fanfiction) |
| Core Infrastructure | backend/app/core/ | VERIFIED |
| ORM Models | backend/app/models/ | VERIFIED (13 tables) |
| Schemas | backend/app/schemas/ | PARTIAL |
| Services | backend/app/services/ | PARTIAL (import/rag VERIFIED; ai_router PARTIAL) |
| Tests | backend/tests/ | VERIFIED (172 passed) |
| Migrations | backend/migrations/ | VERIFIED (head: f3c8b7b2dbf7) |
| Frontend Pages | frontend/src/app/ | PARTIAL (novels/settings VERIFIED; writing SKELETON) |
| Components | frontend/src/components/ | PARTIAL |
| API Client | frontend/src/lib/api.ts | VERIFIED |
| Stores | frontend/src/stores/ | VERIFIED |
| Hooks | frontend/src/hooks/ | VERIFIED |
| Frontend Tests | frontend/src/__tests__/ | VERIFIED (22 passed) |

## Integrations

| Integration | Status | Notes |
|---|---|---|
| ChromaDB | VERIFIED | HTTP API, per-novel collections |
| Neo4j | PLANNED | Docker service, no code integration |
| AI Providers | PARTIAL | OpenAI-compatible; config CRUD VERIFIED, business endpoints MISSING |

## Trust Boundaries

1. All non-public APIs require authentication (JWT Cookie / Bearer)
2. All resources filtered by owner_id
3. API keys encrypted at rest (Fernet enc:v1)
4. Provider URLs validated for SSRF (protocol/DNS/IP)
5. Upload files use random names + directory containment
6. DB/file dual-write has failure compensation
