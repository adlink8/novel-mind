# Phase 30: Visual Bible - Research

**Researched:** 2026-08-01
**Domain:** Evidence-linked visual identity candidates
**Confidence:** MEDIUM

## Summary

Issue #29 requires a Visual Bible for characters, places, items, factions, and style, with evidence links, interpretation labels, reusable IDs, and no silent canon promotion. [CITED: https://github.com/adlink8/novel-mind/issues/29] The roadmap repeats the candidate-only and provenance boundary. [CITED: `.planning/ROADMAP.md` Phase 30]

The current repository has the correct architectural analogs but not the feature: Phase 12 supplies read-only asset eligibility/reason codes; Narrative Memory supplies immutable candidate versions, manifests, source links, and provenance; Reader Chat supplies strict Pydantic contracts and immutable source manifests. [VERIFIED: codebase grep] `Novel.cover_url` and `backend/storage/images/` are cover/upload artifacts, not a versioned illustration authority. [VERIFIED: codebase grep]

**Primary recommendation:** build a sidecar Visual Bible candidate contract over `owner_id + novel_id + source_snapshot`, with typed claims and evidence links, append-only review events, and a derived review envelope; keep approved visual revisions separate from Original Canon and do not add provider calls in Phase 30. [ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Visual entity/style schema and version | Database / Storage | API / Backend | Version and lineage must survive process restarts and support exact review replay. [VERIFIED: codebase grep] |
| Evidence extraction/claim normalization | API / Backend | Database / Storage | Existing evidence and candidate services centralize deterministic gates before persistence. [VERIFIED: codebase grep] |
| Human review/editor | Browser / Client | API / Backend | The browser presents edits; the server validates owner, evidence, version, and transition. [ASSUMED] |
| Reference asset metadata | Database / Storage | CDN / Static | Binary storage and rights metadata have different lifecycle concerns from visual claims. [ASSUMED] |

## User Constraints

- Honor `D-30-01` through `D-30-04` in `30-CONTEXT.md`.
- Phase 22 remains blocked and 0/3 Nightly; no phase-30 work may claim qualification. [CITED: `.planning/STATE.md`]

## Standard Stack

| Layer | Version | Use | Provenance |
|---|---|---|---|
| FastAPI | `>=0.115` | owner-scoped API and dependency injection | [VERIFIED: `backend/requirements.txt`] |
| Pydantic | `>=2.13` | strict request/response and candidate contracts | [VERIFIED: `backend/requirements.txt`] |
| SQLAlchemy | `>=2.0` | async PostgreSQL persistence and constraints | [VERIFIED: `backend/requirements.txt`] |
| PostgreSQL | 16 in project architecture | durable version/lineage/review rows | [CITED: `docs/architecture/03-data-model.md`] |
| Next.js/React/TypeScript | Next `16.3.0-canary.6`, React `19.2.7` | review workspace and API client | [VERIFIED: `frontend/package.json`] |

No new package is justified for the Visual Bible contract. [ASSUMED]

## Architecture Patterns

1. Use strict `extra="forbid"` schemas, closed enums, canonical ordering, and content hashes, following `backend/app/services/narrative_memory/audit_contracts.py` and `backend/app/schemas/reader_chat.py`. [VERIFIED: codebase grep]
2. Persist a parent `VisualBibleVersion` plus typed child entities/claims/evidence links; retain source snapshot and parent version instead of overwriting rows. [ASSUMED]
3. Model human actions as append-only review events; derive current review state from the event stream or a transactionally updated projection. [VERIFIED: codebase grep; `backend/app/schemas/clue.py`]
4. Separate binary reference metadata (`asset_id`, MIME, content hash, rights/provenance) from visual claims; Phase 30 can reference an existing asset without making it a canon fact. [ASSUMED]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Owner/version/evidence scoping | ad-hoc caller checks | existing `require_owned_novel` + composite scoped constraints | Existing services prove scope before accepting IDs. [VERIFIED: codebase grep] |
| Canonical serialization/hashes | per-route JSON formatting | existing canonical/hash helpers in Narrative Memory and Reader Chat | Stable hashes are needed for lineage and review diffs. [VERIFIED: codebase grep] |
| Asset eligibility | custom “file exists” check | Phase 12 audit status/reason-code pattern | Presence is not evidence of exact reusable lineage. [VERIFIED: `.planning/phases/12.../12-RESEARCH.md`] |

## Common Pitfalls

- **Cover-as-illustration confusion:** `cover_url` is a novel field; it does not provide revision, rights, evidence, or anchor semantics. [VERIFIED: codebase grep]
- **Interpretation laundering:** free-text visual prose must not be persisted as `canon_fact` without evidence and cutoff. [CITED: `REQ-VIS-01`; ASSUMED implementation rule]
- **Mutable approved rows:** editing an approved row destroys auditability; create a child revision/review event. [VERIFIED: codebase grep]
- **Cross-owner asset references:** every referenced entity and binary must be scoped through the owning novel, not only a caller-supplied ID. [VERIFIED: `.planning/phases/12.../12-RESEARCH.md`]
- **Rights blind spot:** generated/reference assets need source, license/permission status, and takedown/review state before browser exposure. [ASSUMED]

## Code Examples

```python
class VisualClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authority: Literal["canon_fact", "probable_inference", "literary_interpretation", "user_interpretation"]
    evidence_refs: list[EvidenceRef]
    cutoff_chapter: int
    claim_hash: str
```

This follows the strict contract and evidence-ref shape used by Reader Chat/Narrative Memory. [VERIFIED: codebase grep]

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Framework | pytest + pytest-asyncio; Vitest + React Testing Library | [VERIFIED: `docs/architecture/10-testing-ci.md`] |
| Config | `backend/pytest.ini`/existing test config; `frontend/vitest.config.ts` | [VERIFIED: codebase grep] |
| Quick run | `cd backend; pytest tests/unit/narrative_memory tests/unit/visual_bible -q` | [ASSUMED future path] |
| Full phase run | `cd backend; pytest tests/ -q` plus `cd frontend; npm test` | [CITED: `docs/architecture/10-testing-ci.md`] |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated command | File Exists? |
|---|---|---|---|---|
| REQ-VIS-01 | typed entity/style revision preserves evidence, authority, owner, novel, cutoff | unit/contract | `pytest tests/unit/visual_bible/test_contracts.py -q` | Wave 0 |
| REQ-VIS-01 | review cannot promote unsupported/generated claim to canon | unit/adversarial | `pytest tests/unit/visual_bible/test_review_gates.py -q` | Wave 0 |
| REQ-VIS-01 | cross-owner/version access fails closed | integration | `pytest tests/integration/visual_bible/test_scope.py -q` | Wave 0 |
| REQ-VIS-01 | review UI shows evidence and revision state | browser/manual | `npm run test:e2e -- visual-bible` | Wave 0 |

### Sampling Rate

- Per task: targeted pytest/Vitest command.
- Per wave: backend contract + frontend component suite.
- Phase gate: full relevant suite green; Phase 22 status unchanged.

### Wave 0 Gaps

- [ ] strict schema and hash fixtures for one character, place, item, faction, style, and unsupported claim.
- [ ] owner/version/review integration fixture.
- [ ] browser fixture with `canon_fact` and `user_interpretation` visibly distinct.

## Security Domain

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | yes | existing auth dependency and owner-scoped route |
| V3 Session Management | yes | existing JWT/cookie session boundary |
| V4 Access Control | yes | `require_owned_novel`, scoped joins, no ID-only lookup |
| V5 Input Validation | yes | strict Pydantic enums, evidence/range/hash checks |
| V6 Cryptography | yes | existing hash/content integrity helpers; no custom crypto |

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python backend environment | contracts/API | ✓ | repository venv present by project convention | use existing backend interpreter |
| PostgreSQL 16 | durable revisions | project-supported | 16 in architecture baseline | Wave 0 can use existing test DB/SQLite contract fixtures |
| Node/npm | review UI | ✓ | manifest-managed | none |
| Image provider | not Phase 30 | intentionally not required | — | deterministic fixtures; no provider call |

This is an availability record only; no implementation test or dependency installation was run. [VERIFIED: manifests/config; ASSUMED runtime availability]

## Sources

### Primary (HIGH confidence)

- Issue #29 — v1.3 scope and visual plans: `https://github.com/adlink8/novel-mind/issues/29`
- `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`
- `backend/app/services/narrative_memory/*`, `backend/app/models/reader_chat.py`, `backend/app/schemas/reader_chat.py`

### Secondary (MEDIUM confidence)

- `docs/architecture/02-module-map.md`, `03-data-model.md`, `09-frontend-architecture.md`, `10-testing-ci.md`

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | Phase 30 should add a sidecar Visual Bible rather than alter existing Novel/cover fields. | Summary | Wrong boundary could cause schema churn or loss of cover compatibility. |
| A2 | A derived review projection is acceptable alongside append-only review events. | Architecture Patterns | Planner may need a different query strategy. |
| A3 | Rights metadata belongs in the asset contract even though current code has no rights model. | Pitfalls | Missing rights gate could expose unreviewed references. |

## Open Questions (RESOLVED)

1. Existing Novel/Asset/version entities and their owner/novel/version lineage are the authoritative boundary for character/place/item/faction claims; Visual Bible introduces only candidate revisions and references those entities.
2. Reference binaries use the existing local/object-storage seam selected by deployment configuration; the Visual Bible contract remains provider-neutral and stores immutable asset metadata, bytes hash, MIME, rights, and provenance.

## Metadata

- Standard stack: HIGH for repository versions; MEDIUM for new Visual Bible boundary.
- Architecture: MEDIUM; analogs are verified but the feature is not implemented.
- Pitfalls: MEDIUM/LOW where rights and UI behavior are assumptions.
- Research date: 2026-08-01; valid until 2026-09-01 for this codebase snapshot.
