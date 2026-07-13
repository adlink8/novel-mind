---
phase: 07-semantic-hierarchical-chunking
plan: 04
subsystem: rag-chunking
tags: [hierarchy, scene, evidence, retrieval]
requires:
  - phase: 07-03
    provides: validated/fallback decisions feeding segments
provides:
  - HierarchyTree chapter→scene→evidence assembler + invariants
  - evidence-hit scene expansion with raw fallback
  - in-memory hierarchy attached to candidate builds
requirements-completed: [REQ-CHUNK-04]
completed: 2026-07-13
---

# 07-04 Summary

Deterministic hierarchy assembly with unique evidence parents, scene rebuild-from-children, no cross-chapter, and retrieval expansion that falls back to raw when hierarchy is missing.

**Tests:** unit hierarchy + integration storage/retrieval green.
