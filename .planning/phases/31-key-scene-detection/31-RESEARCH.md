# Phase 31: Key Scene Detection - Research

**Researched:** 2026-08-01
**Domain:** Evidence-backed scene candidate ranking
**Confidence:** MEDIUM

## Summary

Issue #29 defines candidates by plot turn, emotion, character importance, visual expressiveness, arc impact, repetition penalty, and spoiler risk; it explicitly requires human confirmation. [CITED: https://github.com/adlink8/novel-mind/issues/29] The roadmap requires range, cast, place, time, POV, diversity, and reasons. [CITED: `.planning/ROADMAP.md` Phase 31]

The repository already persists chapter content and has a chapter→scene→evidence hierarchy plus candidate/manifests and cutoff-aware Reader retrieval. [VERIFIED: codebase grep] The safest design is a deterministic `SceneCandidate` Artifact that references existing source ranges and stores score components, rather than a new free-text summary authority. [ASSUMED]

**Primary recommendation:** generate candidates from source hierarchy and Phase 26–29 typed facts, score with versioned multi-signal policy, deduplicate by evidence overlap plus narrative diversity, then freeze only human-confirmed candidates. [ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Boundary/range identity | Database / Storage | API / Backend | source hash and offsets must be replayable. [VERIFIED: codebase grep] |
| Salience/diversity scoring | API / Backend | Database / Storage | scoring is deterministic policy over candidate inputs. [ASSUMED] |
| Candidate review | Browser / Client | API / Backend | UI collects decisions; server owns transition and scope. [ASSUMED] |

## User Constraints

- Honor `D-31-01` through `D-31-04` in `31-CONTEXT.md`.
- Preserve Phase 22 blocked/0-of-3 status. [CITED: `.planning/STATE.md`]

## Standard Stack

| Component | Version | Use | Provenance |
|---|---|---|---|
| Python/FastAPI/Pydantic/SQLAlchemy | requirements ranges in `backend/requirements.txt` | contracts, scoring service, API | [VERIFIED: `backend/requirements.txt`] |
| PostgreSQL 16 | project baseline | candidate/review persistence | [CITED: `docs/architecture/03-data-model.md`] |
| Existing chunk hierarchy | current code | boundary/evidence source | [VERIFIED: `backend/app/services/chunking/`] |
| Vitest/Playwright | current frontend scripts | component/browser validation | [VERIFIED: `frontend/package.json`] |

No ranking package should be added for the first implementation; deterministic Python scoring is sufficient for the contract. [ASSUMED]

## Architecture Patterns

- `SceneCandidate` should carry `candidate_id`, `scene_id`, source range/hash, narrative coordinates, score breakdown, diversity key, cutoff, policy/schema hash, and review state. [ASSUMED]
- Candidate input must be an evidence package, analogous to `ReaderContextManifest`; the scorer cannot invent cast/place/time details outside that package. [VERIFIED: codebase grep]
- Use stable canonical sorting and an explicit score policy hash, analogous to RAG fixture/evaluation contracts. [VERIFIED: codebase grep]
- Freeze a set manifest with candidate IDs and source hashes; later prompt compilation consumes the set, not an unstable query. [ASSUMED]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---|---|---|
| Source ranges | new text splitter | existing chunking hierarchy and chapter offsets |
| Spoiler filtering | UI-only chapter filter | server-side cutoff patterns from timeline/Reader Chat |
| Candidate truth | model output directly | typed candidate + evidence gate + human review |

## Common Pitfalls

- **Embedding-only importance:** semantically similar quiet scenes can crowd out diverse emotional or visual scenes. [CITED: Issue #29]
- **Range drift:** storing chapter number without source hash makes later anchor/prompt use unsafe. [VERIFIED: reader selection and manifest code]
- **Duplicate density:** adjacent overlapping candidates can produce repeated illustrations; require overlap/diversity penalties. [ASSUMED]
- **Spoiler metadata leak:** review lists must filter future cast/place/reason fields at the API boundary, not just hide thumbnails. [VERIFIED: codebase grep]
- **Unexplained scores:** score components and reasons must be rendered for human review. [CITED: `.planning/ROADMAP.md` Phase 31]

## Code Examples

```python
candidate = SceneCandidate(
    evidence_range=EvidenceRange(chapter_id=7, start=120, end=980, content_hash=chapter_hash),
    coordinates=SceneCoordinates(cast=[...], place=..., time=..., pov=...),
    score=ScoreBreakdown(plot_turn=..., emotion=..., diversity_penalty=...),
    policy_hash=policy_hash,
    review_status="candidate",
)
```

The shape follows existing strict evidence/range contracts; exact field names are a planning decision, not current code. [VERIFIED: codebase grep; ASSUMED field names]

## Validation Architecture

### Test Framework

pytest/pytest-asyncio, Vitest/RTL, and Playwright are the repository test layers. [VERIFIED: `docs/architecture/10-testing-ci.md`]

### Phase Requirements → Test Map

| Req | Behavior | Type | Command | File |
|---|---|---|---|---|
| REQ-VIS-02 | candidate preserves range/cast/place/time/POV/cutoff | unit | `pytest tests/unit/key_scenes -q` | Wave 0 |
| REQ-VIS-02 | score changes only with declared policy inputs; stable ordering | unit/property | `pytest tests/unit/key_scenes/test_scoring.py -q` | Wave 0 |
| REQ-VIS-02 | action/quiet/ambiguous fixtures retain diversity | fixture eval | `pytest tests/integration/key_scenes/test_fixture_quality.py -q` | Wave 0 |
| REQ-VIS-02 | review set freeze and spoiler-safe browser review | browser/manual | `npm run test:e2e -- key-scenes` | Wave 0 |

### Wave 0 Gaps

- frozen 3-bucket scene fixture: action, quiet-emotional, visually ambiguous;
- labeled expected diversity set and overlap thresholds;
- adversarial future-scene and missing-evidence fixtures.

## Security Domain

V2/V3 session, V4 owner/cutoff authorization, V5 strict range/hash validation, and V6 existing hash helpers apply. [ASSUMED based on stack]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python backend environment | boundary/scoring contracts | ✓ by repository setup | requirements-managed | deterministic pure fixtures |
| PostgreSQL 16 | candidate/review persistence | project-supported | 16 baseline | test database/contract fixtures for Wave 0 |
| Node/npm + Playwright | review UAT | ✓ by frontend manifest/scripts | manifest-managed | component tests if browser run unavailable |
| Embedding/LLM provider | optional signal only | not required | — | explicit `unavailable`, never score as fact |

No environment installation or implementation test was run during research. [VERIFIED: manifests/docs; ASSUMED runtime availability]

## Sources

- HIGH: Issue #29; `.planning/REQUIREMENTS.md`; `.planning/ROADMAP.md`; chunking, Reader Chat, Narrative Memory code.
- MEDIUM: `docs/architecture/02-module-map.md`, `03-data-model.md`, `10-testing-ci.md`.

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Existing hierarchy is the first boundary source for scene candidates. | Missing hierarchy coverage could require a fallback boundary pass. |
| A2 | Deterministic weighted scoring is adequate for Phase 31. | Weights may under-rank subtle scenes. |
| A3 | A frozen set manifest is the handoff to Phase 32. | Different handoff would change cross-phase contracts. |

## Open Questions (RESOLVED)

1. Existing Novel/Asset/version entities and their owner/novel/version lineage remain the authoritative boundary; Phase 27/28 signals are optional inputs and unavailable signals remain explicitly `unavailable`, never silently coerced to zero.
2. Use deterministic default precision/diversity thresholds recorded in the detector policy hash; thresholds are versionable policy data and may change only by creating a new candidate-set revision and review event.
