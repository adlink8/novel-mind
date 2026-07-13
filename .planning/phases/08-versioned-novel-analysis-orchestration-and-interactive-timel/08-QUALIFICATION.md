# Phase 08 Qualification

**Release status: QUALIFIED**

- Dataset: `timeline-fiction.v1` (20 cases, 10 cross-chapter groups)
- Fixture SHA-256: `754bfc983bcb73c63c646aa860665b66225eb345259ae183d914d0ce3c1ee81e`
- Report SHA-256: `fa0412e472f6bff52f72601c065739aec7949985f0005f1a9a9a6f86ae011caa`
- Offline: `qualified`; controlled dual-model: `qualified`
- Live outage/block policy: `blocked_dependency`, `metrics=null`, never success

## Requirement Scorecard

| Requirement | Status | Evidence |
|---|---|---|
| REQ-TIME-01 | PASS | durable restart/checkpoint and zero duplicate completed calls |
| REQ-TIME-02 | PASS | immutable lineage, stale CAS rejection, byte-identical rollback |
| REQ-TIME-03 | PASS | first-entry trigger remains API-owned and idempotent |
| REQ-TIME-04 | PASS | four strict time shapes, participants, evidence, and causality |
| REQ-TIME-05 | PASS | quality reconciliation, override preservation, conflict retention |
| REQ-TIME-06 | PASS | progressive active/candidate envelopes remain separate |
| REQ-TIME-07 | PASS | global responsive timeline frontend suite and build |
| REQ-TIME-08 | PASS | visible-set-first spoiler policy and explicit full-book preference |
| REQ-TIME-09 | PASS | exact cache, balanced/quality tiers, priced fail-closed budgets |
| REQ-TIME-10 | PASS | accessible narrative/story ordering and causal controls |

## Decision Scorecard

| Decisions | Status | Evidence |
|---|---|---|
| D-01..D-03 | PASS | global /analysis fiction timeline; frontend unit/build gates |
| D-04..D-07 | PASS | person filter, dual order, four precisions, typed causal overlay |
| D-08..D-11 | PASS | automatic evidence gate, immutable candidates, overrides, rollback |
| D-12..D-15 | PASS | first-entry jobs, progressive chapters, dual tiers, exact cache/budget |
| D-16..D-17 | PASS | API spoiler filtering and persisted full-book preference |
| D-18..D-19 | PASS | fiction-only corpus; relationship graph, reader AI, clues, history absent |
| D-20..D-22 | PASS | first-chapter default, source isolation, unknown-price pause |

## Quality, Cost, and Latency

| Metric | Result | Gate |
|---|---:|---:|
| Event precision | 1.00 | >= 0.90 |
| Story pairwise accuracy | 1.00 | >= 0.90 |
| Duplicate F1 | 1.00 | >= 0.90 |
| Causal precision | 1.00 | >= 0.90 |
| Controlled calls | 2 | exactly 2 |
| Controlled cost | $0.002000 | recorded |
| Controlled p95 latency | 10.0 ms | recorded |

## Executed Gates

- Backend timeline unit/integration/adversarial: **56 passed**.
- Controlled live dual-model and fail-closed negatives: **7 passed**.
- Timeline release gate: **5 passed**.
- Frontend unit suite: **66 passed**; Next.js production build passed.
- No `08-VERIFICATION.md` was created or modified.
