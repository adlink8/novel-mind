---
phase: 09-dynamic-character-relationship-graph
status: planned
nyquist_compliant: true
created: 2026-07-13
---

# Phase 09 Validation Strategy

## Validation Principle

Every implementation plan ends with **Test, Fix, and Confirm**. No release verdict may rely only on mocks, static report contents or frontend hiding. PostgreSQL observations, owner/version/spoiler queries and real browser behavior are the authority chain.

## Wave 0 Test Scaffolds

| Contract | Test file created before/with production code | Fast command |
|---|---|---|
| ORM/migration/append-only constraints | `backend/tests/integration/relationships/test_persistence.py` | `pytest tests/integration/relationships/test_persistence.py -q` |
| candidate/LLM/gates/fold | `backend/tests/unit/relationships/test_pipeline.py` | `pytest tests/unit/relationships/test_pipeline.py -q` |
| API/override/projection | `backend/tests/integration/relationships/test_api.py` | `pytest tests/integration/relationships/test_api.py -q` |
| frontend contracts/components | `frontend/src/lib/relationships.contract.test.ts`, `frontend/src/app/analysis/relationships.test.tsx` | `npm test -- --run relationships` |
| adversarial/performance/release | `backend/tests/adversarial/test_relationship_boundaries.py`, `backend/tests/integration/relationships/test_performance.py`, `frontend/e2e/relationships-real.spec.ts` | targeted commands below |

## Test Matrix

| Layer | Required proof |
|---|---|
| Migration | Alembic head upgrades real PostgreSQL; FKs, checks, indexes, uniqueness and append-only protection work; downgrade/upgrade path is explicit |
| Unit | strict schema, fiction whitelist, five relation types, candidate recall-only boundary, thresholds, transition chain, intervals, fold, override supersession/relink |
| Integration | accepted judgment → accepted observation; cross-owner/version/evidence rejection; no mutation; active/candidate isolation; replay checksum |
| Contract | OpenAPI response matches TypeScript; owner/version/spoiler/full-book query params and error shapes are stable |
| Adversarial | history, forged IDs/hash/offsets, prompt injection, vector-only, chat-only, legacy rows, future labels/counts, cross-version endpoint IDs all fail closed |
| Frontend | workspace/filter/zoom/evidence/keyboard list/timeline linkage; ECharts component remains intact |
| Browser | real Next.js + FastAPI + PostgreSQL, desktop and mobile 390px; only model transport may be controlled |
| Performance | 10k observation seed; normal/large/filters-required tiers; indexed API and Cytoscape interaction budgets |
| Release | signed/versioned evidence binds DB observations, fixture/schema/policy/version/package lock and internally captured command digests |

## Performance Budgets

- Spoiler-safe graph query over 10,000 accepted observations: p95 <= 300 ms after warmup on qualification environment.
- Normal graph first usable render: <= 1.5 s; large graph <= 2.5 s.
- Pan/zoom/selection interaction long task: p95 <= 100 ms for large tier.
- API never returns >500 nodes or >1500 edges; over-cap response contains zero elements and `degradation.mode=filters_required`.
- Repeated source/version switches destroy or recycle Cytoscape instances without unbounded listener/canvas growth.

## Commands

```powershell
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_persistence.py -q
.venv\Scripts\python.exe -m pytest tests/unit/relationships tests/integration/relationships -q
.venv\Scripts\python.exe -m pytest tests/adversarial/test_relationship_boundaries.py -q
.venv\Scripts\python.exe -m pytest tests/integration/relationships/test_performance.py -q
.venv\Scripts\python.exe scripts/run_relationship_qualification.py --verify-release --report artifacts/relationship-report.json

cd ..\frontend
npm test -- --run
npm run build
npm run test:e2e -- relationships-real.spec.ts
```

## Release Blockers

- any owner/version/spoiler leak, including labels/filters/counts
- any accepted fact not sourced from accepted judgment + valid evidence
- any history observation or legacy `character_relations` contamination
- any accepted row mutation or silent override loss/relink
- any graph payload above hard cap
- any Neo4j replay changing PostgreSQL or checksum mismatch
- missing/blocked command, PostgreSQL authority or browser evidence

## Multi-Source Coverage Audit

| Source | ID | Item | Plan | Status |
|---|---|---|---|---|
| GOAL | — | versioned, evolving, spoiler-safe fiction relationship graph | 01-05 | COVERED |
| REQ | REQ-REL-01 | accepted evidence/lineage facts only | 01,02,05 | COVERED |
| REQ | REQ-REL-02 | append-only interval/version observations | 01,02,03 | COVERED |
| REQ | REQ-REL-03 | owner/version/spoiler API | 03,05 | COVERED |
| REQ | REQ-REL-04 | Cytoscape analysis workspace | 04,05 | COVERED |
| REQ | REQ-REL-05 | protective overrides | 01,03,05 | COVERED |
| REQ | REQ-REL-06 | eval, projection, cache, browser, performance | 02-05 | COVERED |
| RESEARCH | — | new observation authority; legacy snapshots excluded | 01,03,05 | COVERED |
| RESEARCH | — | visible-set-first metadata | 03,05 | COVERED |
| RESEARCH | — | Cytoscape package/performance tiers | 04,05 | COVERED |
| RESEARCH | — | deterministic AI boundary and thresholds | 02,05 | COVERED |
| CONTEXT | D-01..D-04 | authority/history | 01,03 | COVERED |
| CONTEXT | D-05..D-08 | fiction/product/ontology/workspace | 01,02,04 | COVERED |
| CONTEXT | D-09..D-12 | spoiler/version | 03,05 | COVERED |
| CONTEXT | D-13..D-16 | AI/gates/state | 02,05 | COVERED |
| CONTEXT | D-17..D-18 | overrides | 01,03,05 | COVERED |
| CONTEXT | D-19..D-22 | frontend/performance | 04,05 | COVERED |
| CONTEXT | D-23..D-24 | Phase 10/11 dependencies only | 03,05 | COVERED |

All source items are covered. Deferred/forbidden items are intentionally absent from implementation tasks.

