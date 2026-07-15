# Phase 11 Validation Strategy

**Nyquist status:** planned  
**Mode:** automation-first; no manual-only acceptance  
**Domain:** fiction only

## Validation Principles

1. Every requirement has at least one fast automated test and one end-to-end or release-level proof.
2. Production code tasks write failing tests before implementation where behavior has deterministic input/output.
3. Tests distinguish candidate recall from published lifecycle quality.
4. All derived spoiler fields are tested, not only clue rows.
5. Phase 09/10 absence is a first-class test environment.
6. A blocked live dependency cannot be represented as a passing zero score.

## Requirement Validation Matrix

| Requirement | Unit/contract | Integration/API | Browser/release |
|---|---|---|---|
| REQ-CLUE-01 | deterministic recall/package IDs; strict LLM schema; evidence gates | frozen transcript worker with exact cache and no direct state write | qualification report binds package/model lineage |
| REQ-CLUE-02 | transition table, replay and evidence-role rules | PostgreSQL append-only constraints, concurrent transitions | release authority replays lifecycle from DB |
| REQ-CLUE-03 | typed links and null source protocols | owner/novel/version target validation | evidence panel shows only validated visible links |
| REQ-CLUE-04 | cutoff/state recomputation properties | default/full-book API diff, counts/filters/links/chains | desktop/mobile spoiler journey |
| REQ-CLUE-05 | override supersession and relink | reanalysis version diff preserves decisions | confirm/reject/note/link UI journey |
| REQ-CLUE-06 | component parity and keyboard interactions | typed API contracts | 1440×900 and 390×844 Playwright |
| REQ-CLUE-07 | metric calculations and fail-closed verdict | PostgreSQL qualification and fixed commands | executable release gate |

## Frozen Fixture

Path planned: backend/evals/clue_fiction.v1.json

Minimum composition:

- 8 genuine cue→reinforcement→payoff chains;
- 4 cue-only active clues;
- 4 reinforced but unresolved clues;
- 8 hard negatives: recurring motif, repeated object, similar wording, same people/location, summary paraphrase, payoff-before-cue, unsupported author-intent inference, chat-only assertion;
- exact evidence IDs, chapters, offsets/hashes, expected lifecycle and acceptable ambiguity;
- dev and frozen partitions with immutable fixture hash.

## Adversarial Gates

The following are release blockers:

- any active/reinforced/paid_off event with invalid evidence;
- any paid_off without distinct early cue and later payoff;
- any state inferred directly from similarity/chat;
- any cross-owner/novel/version link;
- any future title/status/count/filter/link/evidence/payoff-chain leak;
- any illegal lifecycle transition or historical row mutation;
- any human decision overwrite or ambiguous auto-relink;
- any model call after budget/dependency pause;
- any non-qualified candidate pointer move.

## Planned Test Inventory

### Backend unit

- tests/unit/clues/test_schemas.py
- tests/unit/clues/test_lifecycle.py
- tests/unit/clues/test_candidates.py
- tests/unit/clues/test_evidence_gates.py
- tests/unit/clues/test_llm_judgment.py
- tests/unit/clues/test_overrides.py
- tests/unit/clues/test_query_projection.py

### Backend integration/API

- tests/integration/clues/test_persistence.py
- tests/integration/clues/test_worker_versions.py
- tests/integration/clues/test_source_protocols.py
- tests/integration/clues/test_spoiler_api.py
- tests/integration/clues/test_override_reanalysis.py
- tests/integration/clues/test_real_qualification.py
- tests/adversarial/test_clue_false_positives.py
- tests/adversarial/test_clue_spoilers_and_versions.py

### Frontend/browser

- frontend/src/components/clues/*.test.tsx
- frontend/src/app/analysis/page.test.tsx additions
- frontend/src/lib/clue-api.contract.test.ts
- frontend/e2e/clue-real.spec.ts

## Commands

Fast contract loop:

~~~powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/clues -q -x
~~~

Backend integration:

~~~powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/integration/clues tests/adversarial/test_clue_false_positives.py tests/adversarial/test_clue_spoilers_and_versions.py -q -x
~~~

Frontend:

~~~powershell
cd frontend
npm test -- --run
npm run build
npm run test:e2e -- clue-real.spec.ts
~~~

Qualification:

~~~powershell
cd backend
.\.venv\Scripts\python.exe scripts/run_clue_qualification.py --offline --fixture evals/clue_fiction.v1.json
.\.venv\Scripts\python.exe scripts/run_clue_qualification.py --verify-release --report artifacts/clue-qualification.json
~~~

## Release Evidence Contract

The report must include:

- source snapshot, hierarchy build/checksum and timeline version/checksum;
- clue prompt/schema/model/decoding/config/price hashes;
- fixture and policy hashes;
- lifecycle manifest and active pointer revision/checksum;
- schema/evidence/false-positive/spoiler/override/version metrics;
- call/token/cost and p50/p95 latency;
- fixed command argv/cwd/exit/digest;
- fresh PostgreSQL observations for run/version/pointer/lifecycle/evidence/override/attempt rows;
- signed or canonical report checksum.

The CLI rejects caller-supplied command lists, digests, database observations or success flags.

## Validation Waves

| Wave | Validation produced |
|---|---|
| 1 | schema, transition, persistence and migration tests |
| 2 | candidate/evidence/LLM/gate/adversarial unit tests |
| 3 | worker/version/override/source/API/spoiler integration tests |
| 4 | frontend contract/component/accessibility tests |
| 5 | frozen metrics, real PostgreSQL, dual viewport browser and release gate |

## Source Coverage Audit

| SOURCE | ID | Item | Plan | Status |
|---|---|---|---|---|
| GOAL | — | evidence/version/human-controlled five-state clue tracking | 01-05 | COVERED |
| REQ | REQ-CLUE-01 | script candidates + bounded LLM + evidence gate | 02,05 | COVERED |
| REQ | REQ-CLUE-02 | append-only five-state lifecycle | 01,03,05 | COVERED |
| REQ | REQ-CLUE-03 | evidence-only person/relation/timeline links | 01,02,03,05 | COVERED |
| REQ | REQ-CLUE-04 | reading-progress spoiler protection | 03,04,05 | COVERED |
| REQ | REQ-CLUE-05 | protected human override and reanalysis versions | 01,03,04,05 | COVERED |
| REQ | REQ-CLUE-06 | analysis workspace clue band/filter/evidence/payoff chain | 04,05 | COVERED |
| REQ | REQ-CLUE-07 | frozen/adversarial/cost/API/browser/release gates | 05 | COVERED |
| RESEARCH | — | clue-owned authority tables | 01 | COVERED |
| RESEARCH | — | null Phase 09/10 protocols | 02,03 | COVERED |
| RESEARCH | — | paid_off as hardest constraint | 01,02,05 | COVERED |
| RESEARCH | — | visible-set-first projection | 03,04,05 | COVERED |
| RESEARCH | — | fixed-command independent release authority | 05 | COVERED |
| CONTEXT | D-01 | fiction only | 01-05 | COVERED |
| CONTEXT | D-02 | LLM minimum authority | 02,03,05 | COVERED |
| CONTEXT | D-03 | append-only lifecycle | 01,03,05 | COVERED |
| CONTEXT | D-04 | active/reinforced/paid_off evidence | 01,02,03,05 | COVERED |
| CONTEXT | D-05 | protected human decisions | 01,03,04,05 | COVERED |
| CONTEXT | D-06 | evidence-only links; absent Phase 09/10 | 01,02,03,05 | COVERED |
| CONTEXT | D-07 | Phase 08 spoiler/full-book reuse | 03,04,05 | COVERED |
| CONTEXT | D-08 | /analysis UI; no intermediate menu | 04,05 | COVERED |
| CONTEXT | D-09 | durable version/budget/cache/pointer | 01,03,05 | COVERED |
| CONTEXT | D-10 | complete qualification/release | 05 | COVERED |
| CONTEXT | D-11 | no writing/relation/chat implementation | 01-05 | COVERED |
| CONTEXT | D-12 | existing stack only | 01-05 | COVERED |

No source item is missing. Deferred ideas and later-phase implementations are excluded, not gaps.

## Final Release Criteria

Phase 11 is releasable only when all five plans are complete, every critical gate is zero-failure, the full backend/frontend/browser suite passes, PostgreSQL authority matches the report, and quality_comparable=true. No human-only checkpoint can substitute for these proofs.

