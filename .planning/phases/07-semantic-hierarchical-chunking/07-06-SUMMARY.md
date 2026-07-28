---
phase: 07-semantic-hierarchical-chunking
plan: 06
subsystem: rag-chunking
tags: [qualification, ab, release, auto-11]
requires:
  - phase: 07-05
    provides: immutable candidates + promotion consumer
  - phase: 06-08
    provides: five-tuple lineage comparability rules
provides:
  - run_ab_qualification same-snapshot A/B adapter
  - release_verifier → QualifiedChunkerEvidence
  - scripts/run_chunker_qualification.py CLI
requirements-completed: [REQ-CHUNK-08]
completed: 2026-07-13
---

# 07-06 Summary

Same-snapshot A/B qualification bound to AUTO-11 lineage semantics; deterministic release gates (F1, coverage, regression, cost); only qualified evidence can promote. LLM does not decide release. Live test is skip-marked for nightly only.

**Tests:** chunker_ab, release_verifier, chunker_release E2E green.
