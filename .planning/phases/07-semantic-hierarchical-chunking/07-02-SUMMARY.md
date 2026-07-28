---
phase: 07-semantic-hierarchical-chunking
plan: 02
subsystem: rag-chunking
tags: [boundary, confidence, atomic-span, segmentation, rules]

requires:
  - phase: 07-01
    provides: source offsets, stable hashes, baseline contracts
provides:
  - AtomicSpan scanner with unicode offsets
  - BoundaryProposal engine with versioned confidence and reason codes
  - Deterministic CandidateSegmentation with pending adjudication queue
affects: [07-03, 07-04]

tech-stack:
  added: []
  patterns:
    - "confidence is rule-confidence.v1 heuristic score, not probability"
    - "hard CHAPTER_EDGE / HARD_MAX_SIZE never llm_eligible"
    - "low confidence uses fallback_decision while queuing proposal_id"

key-files:
  created:
    - backend/app/services/chunking/rules.py
    - backend/app/services/chunking/segmentation.py
    - backend/tests/unit/chunking/test_rule_proposals.py
    - backend/tests/unit/chunking/test_candidate_segmentation.py
  modified:
    - backend/app/services/chunking/schemas.py

key-decisions:
  - "auto_accept=0.75, adjudicate below that if not hard"
  - "abstain → conservative fallback (merge if under hard max else split)"
  - "pure functions only — no DB, index, or active pointer side effects"

patterns-established:
  - "analyze_chapter → propose_boundaries → segment_from_proposals pipeline"
  - "reason codes: CHAPTER_EDGE, STRUCTURAL_BREAK, TIME/LOCATION/SPEAKER/POV_SHIFT, OPEN_QUOTE, COREFERENCE_RISK, TARGET/HARD_MAX/UNDER_MIN_SIZE"

requirements-completed: [REQ-CHUNK-02]

duration: ~25min
completed: 2026-07-13
---

# Phase 07 Plan 02: Rule Boundary Proposals and Candidate Segmentation

**Every adjacent atomic boundary gets a stable proposal with heuristic confidence and reason codes; deterministic segmentation covers the chapter with an explicit low-confidence adjudication queue.**

## Verification

```text
pytest tests/unit/chunking/test_rule_proposals.py \
  tests/unit/chunking/test_candidate_segmentation.py -q
# 16 passed
# full chunking suite also green
```

## Must-Haves

| Truth | Evidence |
|---|---|
| Full adjacent proposals + chapter edges | test_every_adjacent_pair_has_one_proposal |
| Hard boundaries not LLM-eligible | test_hard_max_not_llm_eligible |
| Coverage without cross-chapter | test_full_coverage_no_overlap_same_chapter |
| Pending = llm_eligible only | test_pending_adjudication_is_llm_eligible_only |

## Next

Execute **07-03** LLM low-confidence boundary adjudication and fallback.
