# Phase 28-01 Summary: Gold Set and Non-Zero Retrieval Metrics

## Status

PARTIAL — retrieval evidence is reproducible and non-zero, and the signed content-hash candidate fixture now passes independent live semantic review plus calibrated live SUT quality; the remaining Phase 28 residual is the real Browser/v0.3 closeout.

## Delivered

- Generated 100 deterministic rule-based questions for novel 91 from the formal PostgreSQL `text_chunks` source, 20 per type across `original_text`, `character_relation`, `event_causality`, `timeline`, and `foreshadowing`.
- Fixed the generator seed to `novel_id`, removed answer-leaking/truncated questions, filtered context-dependent fragments, added an eight-character lexical anchor, and restored the 20-per-type contract; the current candidate file hash is `d011c969b77ffffbcf571f5faa63af333ee239f6af56293ee659289cc2817a04`.
- Verified all 100 gold chunk IDs exist in novel 91's 8,851 embedded chunks, then imported them as candidate rows (IDs 201–300).
- Fixed the legacy BM25 evaluation path for contiguous Chinese text by adding a bounded source-phrase fallback after an empty tsvector result; added a CLI `bm25` strategy and unit coverage.
- Built `evals/results/phase28/novel91-quality-candidate.json` from formal source text: 100 frozen cases, 83 unique evidence chunks, valid snapshot signature, 100 valid case signatures, 100/100 deterministic checks, and zero DB-ID truth fields. Current fixture hash: `4103968b7edd933c439c6b576771638c4c233157912d6eb045e707a15e3fa65e`.
- Added dated official price snapshot `evals/pricing/vertex-gemini-3.5-flash-lite-2026-07-28.json` (hash `5acadeb910cf4911486cf9af4a65afec24a7e509713737f8046f3f22703ad949`) and reran `scripts/run_novel91_live_quality_review.py`: 100/100 accepted, 0 rejected, 0 errors, 35,418 input tokens, 3,834 output tokens, estimated cost `$0.02223144`. Current review report hash: `61fcd272420a6cc3eae6ad772cce27ed4d12e22ff60ef59e8a2ab8b51eeb5ebc`.
- Added a signed independent live calibration suite (`suite_hash=df234514ec1c4348e6a6d7b6eee32ac20c457c86accaa99de26ec63a9b794a86`): 7 cases × 3 repeats, consistency `1.0`, critical false accept `0`, status `passed`.
- Fixed `_extract_chinese_needle` to preserve punctuation and nested quotes in the bounded BM25 fallback; the read-only 100-case retrieval smoke now reaches Recall@5 `1.0` (the historical persisted run 13 remains `0.80`).
- Added the real SUT runner `scripts/run_novel91_live_rag_quality.py`: formal PostgreSQL BM25 → Vertex answer generation → calibrated Vertex Judge ×3. The final 100-case report is `live_quality_passed`, 100/100 reviewed, 0 errors, Recall@5 `1.0`, accepted rate `0.96`, consistency `0.9933`, critical ambiguity `0`, estimated cost `$0.17057084`, and `quality_comparable=true` with matching calibration lineage.

## Formal PostgreSQL results

| Run | Strategy | Questions | Recall@5 | MRR | NDCG@5 | Quality comparable |
|---:|---|---:|---:|---:|---:|---|
| 13 | bm25 | 100 | 0.8000 | 0.7933 | 0.7950 | false |
| 10 | baseline_vector | 100 | 0.2300 | 0.1795 | 0.1921 | false |
| 9 | hybrid_search | 100 | 0.2300 | 0.1795 | 0.1921 | false |

The current semantic candidate dataset was appended as formal PostgreSQL candidate rows 401–500 and evaluated as run 13; that historical run reached BM25 Recall@5 0.80 before the punctuation-preserving fallback fix. The legacy evaluator remains `quality_comparable=false` because its truth rows are DB-ID `gold_chunks`; the signed fixture and live SUT report are the comparable path. The fixture remains explicitly `candidate_frozen_requires_semantic_review` until the Browser/v0.3 residual closes. The generic usage logger records `cost_usd=0.0`; reports separately record dated price-snapshot estimates. No production pointer was changed.

## Verification

- `pytest tests/test_eval_service.py tests/test_eval_candidates.py -q` — 29 passed.
- `py_compile` passed for the changed evaluator and scripts.
- Repeated BM25 runs produced identical retrieval metrics (runs 8 and 11); only latency varied.

## Remaining gate

Calibration/lineage authority and comparable live SUT quality now pass; remaining work is the owner-scoped Browser/v0.3 residual and milestone closeout.
