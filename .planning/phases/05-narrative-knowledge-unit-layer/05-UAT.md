---
status: passed
phase: 05-narrative-knowledge-unit-layer
updated: 2026-07-11
---

# Phase 05 UAT

## Automated Release Contract

- Fiction and history frozen fixtures must pass chunks/units/hybrid A/B.
- Hybrid Recall@5 and MRR@5 must not regress from raw chunks.
- Canary critical wrong/stale/cross-owner count must be zero.
- Candidate actual IDs must exactly reconcile with its manifest.
- Prepare binds an exact checksum, dataset hash, canary report, reconcile report, and approver.
- Commit accepts only the prepared journal and unchanged active pointer.

## First Real Cutover

No live candidate was promoted during implementation. The first real active-pointer cutover remains an explicit operator action using the exact prepared journal after PostgreSQL, Chroma, frozen eval, and canary are healthy. Automated tests prove the transaction contract but do not impersonate operator approval.

This operational approval is a production rollout checkpoint, not an implementation or verification gap. The automated release contract passed in `05-VERIFICATION.md`.
