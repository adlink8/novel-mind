# 06-03 Summary — Frozen Fixtures, Adversarial Gates & Judge Calibration

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-03-PLAN.md`  
**Decisions:** D-01, D-02, D-03, D-11, D-15

## What Was Done

### Slice 1 — Frozen fixture and adversarial contracts

- Extended Pydantic contracts in `backend/app/schemas/eval.py`:
  - `SourceSnapshot`, `SnapshotChunk`, `EvidenceRef`, `Claim`, `EquivalentEvidenceSet`
  - `ModelLineage` (`weights/revision` alias), `JudgeFixtureVerdict`, `DeterministicChecks`
  - `EvalCase`, `FixtureJobState`, `FailClosedResult`
- Extended ORM in `backend/app/models/eval.py` (legacy EvalDataset/Run/Result preserved):
  - `RagSourceSnapshot`, `RagFixtureJob`, `RagEvalCase`
- Alembic migration `06_rag_fixture_jobs.py` revision `f6a0303ragfix` ← `e5b8c20d4a73` (single head)
- Core service `backend/app/services/rag_fixture.py`:
  - Content-hash snapshots + HMAC signatures (truth = hash + offsets, never DB gold IDs alone)
  - Pipeline: `snapshot_ready → generating → deterministic_validation → judge_review → frozen`
  - Deterministic checks: schema, snapshot/hash, offset/quote, claims, critical support, equivalence, leak, no-answer, hard-negative
  - Judge rubric 0..4 (≥3 each) + critical_ambiguity=0; max regenerate 2 → `quarantined`
  - G/J isolation: different `model_family` AND `weights_revision` (fail closed → `invalid_lineage`)
  - Offline stub generator/judge for unit/contract tests (no live Ollama)
- Prompts: `backend/prompts/rag_fixture_generator.v1.txt`, `rag_fixture_judge.v1.txt`
- Signed benchmark fixture + adversarial suite under `backend/evals/fixtures/`
- Adversarial coverage: instruction injection, oversize, schema smuggling, malicious quote/offset, cross-owner → `invalid_fixture` / `failed_policy`, `metrics=null`

### Slice 2 — Independent Judge calibration

- Signed calibration suite `backend/evals/calibration/rag-judge-calibration.v1.json`
  - Categories: supported / partial / unsupported / contradictory / no-answer / hard-negative / equivalent_evidence
  - Domain `calibration-synthetic` (isolated from benchmark domain `fiction`)
- 3-repeat calibration runner with confusion matrix
- Gates: critical false accept = 0, consistency ≥ 0.80 else `invalid_lineage` + `metrics=null`
- Hash/domain isolation asserted against benchmark suite

## Files Changed

| Path | Role |
|------|------|
| `backend/app/schemas/eval.py` | RAG quality Pydantic contracts |
| `backend/app/models/eval.py` | Snapshot/job/case ORM (+ legacy eval tables) |
| `backend/app/models/__init__.py` | Export new ORM models |
| `backend/migrations/versions/06_rag_fixture_jobs.py` | Alembic tables |
| `backend/app/services/rag_fixture.py` | Fixture pipeline, adversarial, calibration |
| `backend/prompts/rag_fixture_generator.v1.txt` | Generator prompt v1 |
| `backend/prompts/rag_fixture_judge.v1.txt` | Judge prompt v1 |
| `backend/evals/fixtures/rag-quality-benchmark.v1.json` | Signed frozen benchmark |
| `backend/evals/fixtures/rag-quality-adversarial.v1.json` | Adversarial attack suite |
| `backend/evals/calibration/rag-judge-calibration.v1.json` | Signed calibration suite |
| `backend/tests/test_rag_quality_models.py` | unit ORM/schema |
| `backend/tests/test_rag_quality_fixture.py` | unit+contract pipeline |
| `backend/tests/test_rag_quality_adversarial.py` | unit+contract adversarial |
| `backend/tests/test_rag_quality_calibration.py` | contract calibration |

## Verification

```text
cd backend
pytest tests/test_rag_quality_models.py tests/test_rag_quality_fixture.py \
  tests/test_rag_quality_adversarial.py -m "unit or contract" \
  --junitxml=artifacts/fixture.xml
# → 26 passed

pytest tests/test_rag_quality_fixture.py tests/test_rag_quality_adversarial.py --timeout=15
# → 19 passed

pytest tests/test_rag_quality_calibration.py -m contract --junitxml=artifacts/calibration.xml
# → 8 passed

pytest tests/test_rag_quality_calibration.py --timeout=180
# → 8 passed

alembic heads
# → f6a0303ragfix (head)  [single head]
```

**Test totals:** 34 unique tests (26 fixture slice + 8 calibration).

## Deviations

1. **Offline stubs only for 06-03:** Live dual-model Ollama paths deferred to 06-04 per plan/AI-SPEC Offline/Online split.
2. **Fixture job metrics always null:** Freeze job does not produce SUT quality scores; `quality_comparable=false` until 06-04 scoring.
3. **Migration revision id** uses `f6a0303ragfix` (valid Alembic id) with filename `06_rag_fixture_jobs.py` as specified.
4. **Legacy `eval.py` models** bundled with RAG quality tables in the same module file so existing import paths keep working; migration only creates the three new RAG tables.

## Out of Scope (confirmed)

- SUT retrieval/answer scoring, policy arbiter, durable worker run paths (06-04)
- Eval API run adapters (06-04)
- Nightly/report promotion (06-06/06-07)

## Commit Hashes

- `feat(06-03): frozen fixtures, adversarial gates, G/J isolation, judge calibration`
- `docs(06-03): SUMMARY + STATE updates`

## Next

- Do **not** start 06-04 from this plan execution unless scheduled.
- 06-04 consumes signed fixtures + calibration-passed lineage for SUT scoring.
