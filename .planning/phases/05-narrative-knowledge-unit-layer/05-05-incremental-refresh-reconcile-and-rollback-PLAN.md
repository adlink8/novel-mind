---
phase: 05-narrative-knowledge-unit-layer
plan: 05-05-incremental-refresh-reconcile-and-rollback
type: implementation
wave: 5
depends_on: [05-04-frozen-evaluation-canary-and-promotion]
files_modified:
  - backend/app/services/knowledge_units/incremental.py
  - backend/app/services/knowledge_units/reconcile.py
  - backend/app/services/knowledge_units/rollback.py
  - backend/scripts/refresh_narrative_units.py
  - backend/scripts/reconcile_narrative_unit_index.py
  - backend/scripts/rollback_narrative_unit_index.py
  - backend/tests/test_knowledge_unit_incremental.py
  - backend/tests/test_knowledge_unit_reconcile.py
  - backend/tests/test_knowledge_unit_rollback.py
  - .planning/phases/05-narrative-knowledge-unit-layer/05-VERIFICATION.md
autonomous: false
requirements_addressed: [REQ-NU-03, REQ-NU-04, REQ-NU-07, REQ-NU-08]
truths:
  - "D-08: DB, collection, pointer, manifest, and watermark roll back together."
  - "D-09: content-hash deltas rebuild affected subjects only and no-change is zero-write."
---

# 05-05 - Incremental Refresh, Reconcile, and Rollback

## Objective

Close the narrative-unit lifecycle with affected-subject refresh, deletion/deprecation propagation, exact active-index reconcile, and a demonstrated joint rollback/restore drill.

## Steps

1. Compute deltas from the committed source watermark to current accepted judgment/evidence content hashes. Classify added, changed, rejected-after-acceptance, deleted evidence, domain/profile changes, and no-change.
2. Map deltas to affected canonical subjects and rebuild only those units into a fresh candidate. Reuse caches only after revalidating owner, evidence, lifecycle, prompt/schema/config hashes.
3. Propagate disputed/deprecated/deleted status through drafts, canonical units, candidate manifests, active retrieval filters, and graph-facing citations. Never physically delete audit lineage.
4. Guarantee no-change performs zero LLM calls, zero canonical writes, zero Chroma writes, zero pointer writes, and zero watermark writes.
5. Reconcile PostgreSQL manifest/pointer against actual Chroma IDs and report missing, orphan, duplicate, wrong-build, cross-owner, deleted, and deprecated residue. All must be zero before and after promotion.
6. Implement rollback and restore using the promotion journal. Exercise candidate failure, commit interruption, post-promote reconcile failure, rollback, reconcile, restore, and reconcile again.
7. Advance the committed watermark only after successful promotion and post-promote reconcile. Failed runs remain resumable and do not alter active state.
8. Run all Phase 05 targeted tests plus relevant Phase 03/04 and full non-e2e regression. Write `05-VERIFICATION.md` with actual commands, test counts, checksums, blocked live-service checks, and residual risks.
9. Test, Fix, and Confirm: only mark Phase 05 complete if lifecycle residue is zero and rollback/restore is proven.

## Must-Haves

- Incremental scope is derived from evidence/judgment hashes, not timestamps alone.
- Deleted/deprecated knowledge cannot remain retrievable.
- Watermark advancement is the final committed action.
- A reversible rollback/restore drill is part of acceptance, not documentation-only.
- Covers D-08, D-09 and REQ-NU-03/04/07/08.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_unit_incremental.py tests/test_knowledge_unit_reconcile.py tests/test_knowledge_unit_rollback.py -v
pytest tests/test_knowledge_*.py tests/test_eval_*.py -v
python scripts/refresh_narrative_units.py --owner-id 1 --novel-id 1 --snapshot-id 1 --domain fiction --dry-run
python scripts/reconcile_narrative_unit_index.py --active
python scripts/rollback_narrative_unit_index.py --journal-id TEST --dry-run
pytest tests -m "not e2e" -q
```
