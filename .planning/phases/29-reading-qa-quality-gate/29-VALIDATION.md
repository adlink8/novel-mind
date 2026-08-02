# Phase 29 Validation Strategy

| Gate | Fixture | Pass evidence |
|---|---|---|
| Gold set | eight question buckets | version/fingerprint, agreement, leakage audit |
| Retrieval | candidate vs leaf, same source/cutoff/budget | bucket recall/rank/fallback |
| Citation | valid/stale/wrong-owner/spoiler refs | correctness and fail closed |
| Answer | faithful/relevant/no-answer/unsupported | separate metrics |
| Operations | latency/cost/reuse/provider unavailable | p50/p95, cost, reuse, blocked |
| Browser | Reader/Analysis Chat desktop + 390px | citation jump, panel, focus, failure, no spoiler metadata |

Quick: cd backend; pytest tests/unit/narrative_memory tests/unit/reader_chat
tests/unit/qualification -q. Wave adds integration/adversarial qualification. Browser:
cd frontend; npx playwright test e2e/reader-chat*.spec.ts.

Human UAT samples worst bucket cases, verifies source citations manually, repeats with
reading cutoff and explicit whole-book switch, and records accessibility/spoiler defects.
Final verdict is qualified_candidate or blocked only; Phase 22 is reported separately.
