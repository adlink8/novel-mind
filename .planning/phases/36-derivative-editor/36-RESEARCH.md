# Phase 36: Derivative Editor — Research

**Researched:** 2026-08-01
**Domain:** owner-scoped derivative projects, Markdown drafts, optimistic concurrency and revision history
**Confidence:** HIGH for repository analogs; MEDIUM for final editor UX

<user_constraints>
## User Constraints

负责 Phase 35–39，唯一写入五个 phase 目录。三空间隔离、branch-aware retrieval、Canon 污染负向测试为硬门；Agent 只产候选，确定性代码掌握发布。Issue #29 是范围权威；Phase 22 仍 0/3 nightly。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| REQ-FORK-02 | owner-scoped planning/Markdown/autosave/history/diff/rollback | Reader Chat owner/sequence/idempotency and Clue version/override analogs |
| REQ-CRE-03 | project CRUD, chapter planning, Markdown editor, autosave | existing FastAPI + SQLAlchemy + React Query pattern |
| REQ-CRE-04 | traceable diff and rollback | immutable revision rows with parent/checksum; deterministic rollback pointer |

## Summary

当前 `frontend/src/app/writing/page.tsx` 是建设中占位页，`FanFiction`/`FanFictionChapter` 只有早期内容字段；Phase 36 应新增 derivative project/chapter/revision contracts，而不是把 `content` 原地覆盖。[CITED: frontend/src/app/writing/page.tsx; backend/app/models/fanfiction.py; backend/app/models/fanfiction_chapter.py]

使用 append-only revisions：draft autosave 创建新 revision，客户端带 `base_revision_id`/ETag，服务端用数据库条件更新拒绝 stale write；rollback 只创建一个新 revision，其 parent 指向目标 revision，保留完整历史。[CITED: backend/app/models/reader_chat.py; frontend/src/lib/api.ts]

**Primary recommendation:** project/chapter/revision 三层独立建模，所有行带 owner + derivative project + fork/version scope；Markdown 原文是 canonical editing representation，UI 仅编辑和展示，发布/回滚由确定性事务完成。[CITED: ROADMAP.md#Phase 36; REQUIREMENTS.md#REQ-CRE-03/04]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| project/chapter/revision persistence | Database / Storage | API / Backend | transaction and FK scope own history correctness |
| autosave/concurrency | API / Backend | Browser / Client | client sends base revision; server arbitrates conflicts |
| Markdown editing | Browser / Client | API / Backend | editor state is client-side, canonical content persists server-side |
| diff/rollback | API / Backend | Browser / Client | server chooses revision lineage; UI renders deterministic diff |

## Standard Stack

| Library | Version | Purpose | Evidence |
|---|---|---|---|
| FastAPI | `>=0.115` | CRUD/autosave endpoints | [CITED: backend/requirements.txt] |
| SQLAlchemy | `>=2.0` | project/revision transaction and constraints | [CITED: backend/requirements.txt; backend/app/models/reader_chat.py] |
| Pydantic | `>=2.13` | strict patch/concurrency DTOs | [CITED: backend/requirements.txt; backend/app/schemas/reader_chat.py] |
| React + React Query | `19.2.7`, `@tanstack/react-query ^5.50.0` | editor cache/mutation/refetch | [CITED: frontend/package.json; frontend/src/hooks] |
| Vitest/RTL + Playwright | `vitest 4.1.10`, `@playwright/test ^1.61.1` | editor unit/browser UAT | [CITED: frontend/package.json; frontend/e2e] |

No new package is locked. Do not add a rich-text editor without a separate legitimacy checkpoint; Markdown textarea is the current lowest-risk contract.[ASSUMED]

## Package Legitimacy Audit

No new external package installation is proposed; audit not applicable. Existing manifest versions above are repository facts, not registry verification.

## Architecture Patterns

```text
owner request -> project/fork scope resolver -> transaction
  -> chapter plan or Markdown draft -> new Revision(parent, checksum, base)
  -> conflict? 409 + current revision : autosave acknowledged
  -> explicit publish/rollback action -> new immutable revision
```

### Recommended Project Structure

```text
backend/app/models/derivative.py
backend/app/schemas/derivative.py
backend/app/services/derivative_editor/{projects,revisions,markdown}.py
backend/app/api/derivative.py
backend/tests/integration/test_derivative_editor.py
frontend/src/lib/derivative-api.ts
frontend/src/app/writing/[projectId]/page.tsx
frontend/src/components/writing/markdown-editor.tsx
```

### Pattern: optimistic revision write

Persist `base_revision_id`, compare it in the same transaction, and return conflict metadata without overwriting the newer row. This follows Reader Chat's ordered/idempotent persistence pattern.[CITED: backend/app/services/reader_chat/conversations.py; backend/app/models/reader_chat.py]

### Anti-Patterns

- Mutable `content` as history: destroys diff/rollback.
- Client-selected owner/project IDs without server scope lookup: cross-user access risk.
- Autosave as a timer that ignores HTTP failure: use explicit pending/conflict/retry UI.[ASSUMED]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| ownership | ad hoc per-endpoint checks | dependency/service scope helper | consistent 404/owner semantics [CITED: backend/app/api/dependencies.py] |
| revision identity | timestamps only | SHA-256 canonical content + parent revision | deterministic parity and rollback audit [CITED: narrative memory hashes] |
| conflict handling | last-write-wins | conditional update + 409 | protects user edits [CITED: reader chat sequence constraints] |

## Common Pitfalls

- Autosave creates revisions too frequently without debounce/semantic no-op detection.[ASSUMED]
- Rollback changes a pointer in place and erases the fact of rollback; create a new revision.[ASSUMED]
- Markdown parser/render path allows raw HTML or unsafe links; sanitize/allowlist at render boundary.[ASSUMED]
- Derivative project lists accidentally query by novel only and omit owner/fork.[CITED: backend/app/api/dependencies.py]

## Code Examples

```python
if draft.base_revision_id != current.id:
    raise RevisionConflict(current_revision_id=current.id, current_checksum=current.checksum)
new_revision = Revision(parent_revision_id=current.id, content=draft.markdown,
                        content_checksum=sha256_text(draft.markdown))
```

Pattern is an implementation sketch derived from existing hash/lineage contracts, not a current symbol.[CITED: backend/app/services/narrative_memory/retrieval_manifests.py]

## State of the Art

The repository's current mature pattern is append-only, checksum-sealed candidate/version rows; the writing page and fanfiction endpoints remain placeholders.[CITED: backend/app/models/narrative_memory.py; frontend/src/app/writing/page.tsx; backend/app/api/fanfiction.py]

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Markdown textarea is sufficient for Phase 36 and no rich-text package is required. | UX scope may expand; package/install gate needed. |
| A2 | UI will use a new project route under `/writing`; no locked route was found. | Existing navigation/API contract could differ. |

## Open Questions (RESOLVED)

1. **Markdown dialect and sanitization — RESOLVED:** use CommonMark-compatible parsing with the server-side sanitizer as the authority; canonical Markdown/checksum is computed after the same deterministic sanitization policy.
2. **Published state — RESOLVED:** `published` is a deterministic revision-service state produced by an immutable revision transition with owner approval and manifest/hash lineage; Phase 39 consumes that state and does not infer publication from editor UI or live rows.

## Environment Availability

| Dependency | Available | Version | Fallback |
|---|---|---|---|
| Node/npm | ✓ | 24.13.0 / 11.6.2 | use repo-pinned lockfile when present |
| Python | ✓ | 3.14.2 | project backend venv must be used for actual execution |
| PostgreSQL | not probed | — | SQLite unit tests do not replace integration constraints |

## Validation Architecture

| Property | Value |
|---|---|
| Backend | pytest + pytest-asyncio/httpx; `backend/pytest.ini` |
| Frontend | Vitest/RTL; Playwright desktop + 390px mobile |
| Quick command | `pytest backend/tests/integration/test_derivative_editor.py -q` (planned) |
| Full command | backend relevant suite + `cd frontend; npm test` + targeted Playwright |

| Req | Behavior | Test | File |
|---|---|---|---|
| REQ-CRE-03 | CRUD and owner isolation | integration/API | ❌ `backend/tests/integration/test_derivative_editor.py` |
| REQ-FORK-02 | autosave recovery and explicit fork | integration/browser | ❌ Wave 0 |
| REQ-CRE-04 | diff and rollback preserve lineage | unit/integration | ❌ Wave 0 |

Wave 0 must add fixtures for two owners, two forks, stale base revision, crash-before-ack and rollback. Manual UAT: edit, refresh, reopen, conflict, compare, rollback, verify other owner cannot see it.

## Security Domain

V2/V3 apply via existing auth; V4 is primary (owner/project/fork); V5 validates Markdown size, revision IDs and patch payload; V6 uses existing hash primitives, never custom encryption.[CITED: docs/architecture/07-auth-security.md; backend/app/api/dependencies.py]

Threats: IDOR (access-control), stale overwrite (tampering), unsafe Markdown HTML/link (XSS), autosave body exhaustion (DoS). Mitigate with scope lookup, conditional writes, sanitization/allowlist, size/budget limits.[ASSUMED]

## Sources

- HIGH: `ROADMAP.md`, `REQUIREMENTS.md`, `backend/app/models/reader_chat.py`, `backend/app/services/reader_chat/conversations.py`.
- HIGH: `backend/app/api/dependencies.py`, `frontend/package.json`, `frontend/src/app/writing/page.tsx`.
- MEDIUM: `.planning/phases/18-*/18-RESEARCH.md`, `docs/architecture/10-testing-ci.md`.

## Metadata

Standard stack HIGH; architecture HIGH for backend lineage and MEDIUM for editor UX; pitfalls MEDIUM/LOW where sanitizer and debounce choices are not locked. Valid until 2026-09-01.
