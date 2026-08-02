# Phase 30-02 Summary: A/B Qualification

## Status

VERIFIED AS BLOCKED — the fail-closed A/B preflight is complete and deliberately issued no
release comparison or consumer decision because the common NM/NU/raw fixture and owner-scoped
consumer evidence are absent.

## Evidence

- Novel 91 formal runs compare BM25, baseline vector, and hybrid retrieval on the same 100-case candidate set.
- The novel 91 signed content-hash candidate fixture passes deterministic validation, independent live semantic review, calibrated live SUT quality, and offline runner compatibility; it remains explicitly `candidate_frozen_requires_semantic_review` until the owner-scoped residual closes.
- A dated Gemini 3.5 Flash-Lite price snapshot estimates the live judge batch at `$0.01970969`; the formal usage log still records `cost_usd=0.0`, so the estimate is kept separate from invoice truth.
- The calibrated live SUT report passes 100/100 cases with BM25 Recall@5 `1.0`, accepted rate `0.96`, consistency `0.9933`, and zero errors/critical ambiguity; its estimated cost is `$0.17057084` and it is marked comparable only after calibration/lineage matching.
- The BM25 fallback was corrected to preserve source punctuation/nested quotes; a read-only 100-case smoke now reaches Recall@5 `1.0`.
- The formal database has one `narrative_memory_versions` candidate row for novel 91, zero
  `narrative_memory_qualification_runs`, zero `narrative_memory_qualification_reports`, zero
  `narrative_units`, zero `narrative_index_builds`, and zero `narrative_active_pointers`.
- The blocked preflight is recorded in `30-02-VERIFICATION.md`; it is a candidate-only outcome,
  not a claim that NM/NU/raw A/B passed.
- No consumer or active pointer was changed.

## Unblock

The judge calibration/lineage and live quality prerequisites are supplied, but the remaining
signed comparable A/B fixture and owner/spoiler/citation checks require additional source data
and an owner-scoped browser session. No active pointer or consumer mutation is permitted.
