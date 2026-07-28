# Coding Conventions — NovelMind Backend

## Python Style

- Python 3.12+. All service and model files open with `from __future__ import annotations`.
- Type hints are mandatory on all function signatures. Use `X | Y` union syntax, not `Optional[X]`.
- `logging.getLogger(__name__)` at module level; never print to stdout in library code.
- Module docstrings describe the purpose, responsibilities, and data-flow of the file.
- Line length follows Black defaults (88 chars). No trailing whitespace.

---

## FastAPI Patterns

### Router declaration

Each API module lives in `app/api/<domain>.py` and declares a bare `APIRouter()`:

```python
router = APIRouter()
```

Routers are mounted in `app/main.py` with a prefix and tags:

```python
app.include_router(novels.router, prefix="/api/novels", tags=["novels"])
```

### Route handler signature

All handlers are `async def`. Parameters come in this order:
1. Path/query params with `Query(...)` validators
2. `db: AsyncSession = Depends(get_db)`
3. `current_user: User = Depends(require_user)` (or `require_owned_novel` for ownership checks)

```python
@router.get("")
async def list_novels(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
```

### Response serialisation

Use Pydantic v2 methods: `Model.model_validate(orm_obj)` and `.model_dump()`. Do not use the deprecated `.from_orm()` or `.dict()`.

---

## SQLAlchemy ORM Conventions

### Model base

All ORM models inherit from `app.models.base.Base` (a `DeclarativeBase` subclass) and usually mix in `TimestampMixin` for `created_at` / `updated_at` columns.

### Column declaration

Use the modern `Mapped` + `mapped_column` style exclusively:

```python
from sqlalchemy.orm import Mapped, mapped_column

class Novel(Base, TimestampMixin):
    __tablename__ = "novels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
```

Do not use the legacy `Column(...)` syntax in new code.

### Relationships and TYPE_CHECKING

Circular import resolution: wrap forward-reference model imports inside `if TYPE_CHECKING:` blocks. Use string-quoted annotations when needed.

### Async session

The session factory is `async_sessionmaker` with `expire_on_commit=False`. The FastAPI dependency injects an `AsyncSession` per request and auto-commits or rolls back:

```python
# app/core/database.py
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

All queries must use `await session.execute(select(...))` — no synchronous ORM calls.

---

## Service Layer Conventions

### Service class pattern

Business logic lives in a class under `app/services/`. The class is instantiated once as a module-level singleton and imported by name:

```python
# app/services/novel_service.py
class NovelService:
    async def get_novels(self, db: AsyncSession, ...) -> tuple[list[Novel], int]:
        ...

novel_service = NovelService()  # singleton
```

Routers import the singleton: `from app.services.novel_service import novel_service`.

### Knowledge service sub-package

Complex service domains use a sub-package (`app/services/knowledge/`). Each file has a single responsibility:

| File | Responsibility |
|------|----------------|
| `candidates.py` | Deterministic recall — build `RelationCandidateDraft` objects |
| `evidence.py` | Assemble evidence packages for LLM prompts |
| `llm_judge.py` | Call LLM, parse structured judgment responses |
| `gates.py` | Deterministic gate routing before graph projection |
| `projection.py` | Write accepted judgments to the graph projection store |
| `graph_sync.py` | Sync graph projection with downstream consumers |

### Data-transfer objects in services

Internal DTOs that never reach the DB use `@dataclass(slots=True)`:

```python
@dataclass(slots=True)
class GateDecision:
    judgment_id: int
    status: str
    gate_status: str
```

---

## Error Handling

Raise `HTTPException` directly in route handlers. Services raise domain exceptions or let DB exceptions propagate; the route layer catches and converts:

```python
from fastapi import HTTPException

raise HTTPException(status_code=404, detail="Novel not found")
raise HTTPException(status_code=403, detail="Not authorised")
raise HTTPException(status_code=409, detail="Conflict: resource already exists")
```

A global 500 handler in `app/main.py` catches unhandled exceptions and returns a JSON error without leaking stack traces to clients.

---

## GSD Phase 01 Additions

### NarrativeUnit ORM pattern

`NarrativeUnit` follows the same `Mapped` + `mapped_column` conventions as other models. It represents a structured narrative segment derived from a `TextChunk` and links back to `Chapter` and `Novel` via foreign keys. Index on `(novel_id, chapter_id, unit_type)` for efficient filtering.

### Promote — idempotent write pattern

The `promote` operation (moving a candidate into an accepted graph fact) is guarded by terminal-status checks before any write:

```python
TERMINAL_JUDGMENT_STATUSES = {"accepted", "rejected", "needs_human_review"}

# Guard at the top of any promote method:
if judgment.status in TERMINAL_JUDGMENT_STATUSES:
    return  # already terminal — no-op, do not re-write
```

This ensures promote is idempotent: calling it twice on the same judgment is safe and has no side effects.
