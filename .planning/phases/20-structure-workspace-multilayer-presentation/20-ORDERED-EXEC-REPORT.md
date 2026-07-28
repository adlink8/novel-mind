# Ordered execution report (2026-07-17)

Sequence from residual backlog after Phase 20 P0. **No NM promote.**

| Step | Item | Result | Evidence |
|------|------|--------|----------|
| **1** | Hierarchy rebuild novel 91 | **DONE** | `cb_9f9aee6bf1cb427b`, audit EXIT=0, `reusable_exact`. Commit `e322c45` (segment content_hash fix). `20-HIERARCHY-REBUILD.md` |
| **2** | NM candidate build + transport | **PARTIAL** | version_id=1, ~12 `chapter_state` nodes, run partial (~500 chapters pending). Transport/create-version CLI WIP in working tree. Resume later. `20-NM-BUILD-PARTIAL.md` |
| **3** | Relationship transition honesty UI | **DONE** | Commit `b986dea`. `20-REL-HONESTY.md` |
| **4** | Timeline server chapter range | **DONE** | Commit `31be1f6`. Params `chapter_start`/`chapter_end`. Unit 8 passed. `20-TIMELINE-RANGE.md`. **Restart BE** to load new code in smoke server. |
| **5** | Clue live re-judge | **DONE (ops)** | Live Vertex OK → active **v24**, 32 clues, **payoff still 0**, titles still meta. Gate/worker path issue. `20-CLUE-LIVE-REJUDGE.md` |
| **6** | API UAT smoke | **DONE** | health/timeline/rel/clues/nm versions PASS (auth admin). `20-UAT-API.md`. Timeline range counts residual if BE not reloaded. |

## Remaining next sequence

1. Finish/resume NM build to completion (hours) or scoped chapter subset; commit transport CLI when stable  
2. Clue worker gate: allow provisional→payoff progression + title builder (product code)  
3. Restart BE on :8000 to verify server-side chapter range counts  
4. Optional: hierarchy rebuild novel 104  

## Services

- BE smoke: `http://127.0.0.1:8000`  
- FE: `http://127.0.0.1:3005`  
