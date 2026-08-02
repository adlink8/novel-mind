---
phase: 23-layer-registry-and-narrative-boundaries
plan: 02
status: complete
completed: 2026-07-27
---

# Plan 23-02 Summary

## Result

NU/NM 边界、消费顺序和 Facet/Neo4j 只读投影契约已落地，accepted PostgreSQL facts 不会被投影失败反写。

## Evidence

- `docs/adr/0002-narrative-unit-vs-narrative-memory.md`。
- `tests/contract/test_facet_readonly_contract.py` 及 Phase 23 combined suite：42 passed。

## Boundary

NM 保持 candidate-only；未实现 promotion、active pointer 或 Reader Chat cutover。
