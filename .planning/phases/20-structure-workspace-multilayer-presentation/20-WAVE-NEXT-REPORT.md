# Ordered next-steps execution report

**Date:** 2026-07-17  
**Mode:** sequential roadmap → parallel subagents per wave  
**Promotion:** not attempted

## Wave 1 — Parallel discovery (done)

| Stream | Outcome |
|--------|---------|
| UAT smoke | Code/DB PASS; browser SKIP (services were down at audit time). See `20-UAT-SMOKE.md` |
| NM build | **BLOCKED** — hierarchy `content_hash_mismatch` / rebuild_required on novels 91 & 104. See `20-NM-BUILD-RUN.md` |
| Quality map | Ordered P0–P2 in `20-QUALITY-NEXT.md` |

## Wave 2 — Parallel execute (done)

| Stream | Outcome |
|--------|---------|
| Clue detail spoiler align | **Committed** `4b248e5` — 67 clue unit tests green |
| Clue re-run novel 91 | **Completed** version **22**, 32 clues, 0 payoffs, 32/32 cache_hit. See `20-CLUE-RERUN.md` |
| Services smoke | BE `:8000` health ok; FE on `:3005`. See `20-SERVICES-SMOKE.md` |

## Residual order (next session)

1. Phase 07 hierarchy rebuild for novel 91 (unblocks NM eligibility)  
2. Wire NM build CLI real transport; create candidate version; build L2–L4 (still no promote)  
3. Relationship seed honesty policy / transition UI  
4. Timeline server-side `chapter_start..chapter_end`  
5. Clue live re-judge (not cache_hit) + payoff quality  
6. Browser UAT on FE 3005 + BE 8000 with owner=2  

## Explicit non-goals honored

- No narrative-memory promotion  
- No force-push / no bulk unrelated WIP commit  
