# 15-01 Summary: Deterministic Router and Visible Candidate Sets

**Status:** complete  
**Date:** 2026-07-16  
**Requirements:** V08-RETR-01, V08-RETR-04, V08-RETR-05

## Deliverables

| Path | Role |
| --- | --- |
| `backend/app/services/narrative_memory/retrieval_contracts.py` | Frozen scope/question/route/visible/trace/cache/citation DTOs + query normalization/hashing |
| `backend/app/services/narrative_memory/routing.py` | Versioned deterministic `local\|arc\|global\|mixed` policy (`narrative-memory-routing.v1`) |
| `backend/app/services/narrative_memory/candidate_reader.py` | Explicit-version eligibility + cutoff-first SQL loaders + isolated cache envelopes |
| `backend/tests/unit/narrative_memory/test_retrieval_routing.py` | Contract + route matrix (19 tests) |
| `backend/tests/integration/narrative_memory/test_retrieval_candidates_pg.py` | PG visible-set/cutoff/cache isolation (6 tests) |

## Route policy matrix

| Fixture | Mode | Start levels | Key reason codes |
| --- | --- | --- | --- |
| selection + local text | `local` | `chapter_state` | `selection_anchor` |
| local fact/entity | `local` | `chapter_state` | `local_fact_intent` |
| causal/cross-chapter | `arc` | `story_arc`, `volume` | `cross_chapter_intent` |
| whole-book + full-book auth | `global` | `global_story` | `whole_book_intent` |
| whole-book without auth | non-global safe | highest visible | `unauthorized_global` |
| multi-signal | `mixed` | local+arc/volume | `multiple_scope_signals` |
| no-answer / ambiguous | `mixed` | mixed | `no_answer_shape` / `safe_default` |

Router never inspects candidates, summaries, counts, embeddings, or providers.

## Visible-set SQL predicates

- `owner_id` + `novel_id` + explicit `version_id`
- Sealed manifest + structural report + Phase 14 `completed` run/stages + `completed_candidate` report
- Nodes: `chapter_end <= cutoff` (no partial arc/global exposure)
- Claims: `visible_from_chapter <= cutoff`
- Links: matching snapshot/build + `chapter_number <= cutoff`
- Ranking/counts only after admission; `VisibleCandidate` omits titles/summaries/confidence

## Cache identity fields

`owner_id`, `novel_id`, `version_id`, candidate manifest checksum, source snapshot, hierarchy checksum, cutoff snapshot hash, route hash, query hash, budget hash, policy hash → `identity_hash` (raw key never public).

## Adversarial non-interference

Future nodes (`arc-fut`, `ch-3`, `claim-future`, global spanning beyond cutoff) and foreign-scope rows produce zero difference in re-queried serialized visible sets; no `FUTURE`/`FOREIGN` tokens in public JSON.

## Verification

```text
pytest tests/unit/narrative_memory/test_retrieval_routing.py \
       tests/integration/narrative_memory/test_retrieval_candidates_pg.py -q
# 25 passed
ruff check <15-01 files>  # clean
```
