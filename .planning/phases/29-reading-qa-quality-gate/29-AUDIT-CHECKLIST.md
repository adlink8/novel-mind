# Phase 29-04 Audit Checklist — v1.2 Three-Dimension Evidence Reconciliation

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 29 override)

Implemented in `backend/app/services/qualification/audit.py` and verified by
`backend/tests/integration/qualification/test_audit.py`. This checklist is the
human-readable mirror of the audit gates; the code is the deterministic authority.

## Audit Header (D-02)

Every audit run binds, and cross-checks against the 29-02 report:

- [ ] `db_fingerprint` — bound, and equal to the report header fingerprint.
- [ ] `dataset_version` / `source_snapshot` — bound, equal to the gold set and report header.
- [ ] `commit` — bound, equal to the report header commit.
- [ ] `model` / `prompt` / `schema_version` / `config` — bound.
- [ ] `budget` — bound, carries `max_calls`/`max_input_tokens`/`max_output_tokens`/`max_cost_usd`, equal to report budget.

## Dimension 1 — implementation_readiness

Each item fails closed to `blocked` when its evidence is missing or mismatched.

- [ ] Required source files present (qualification package, narrative_memory contracts/manifest, gold set, e2e specs).
- [ ] Required modules importable.
- [ ] Schema migration: single alembic head `20260801_2801` (no multi-head).
- [ ] Candidate-only gate: no promotion/provider capability in the qualified services
      (`*_has_promotion_capability() == False`, `*_has_provider_capability() == False`).
- [ ] No pointer/promotion/cutover *writes* in qualified sources (static scan).
- [ ] No provider transport imports in the qualified services.
- [ ] Owner / spoiler / budget / evidence / approval gates present (live-code capability
      scan + report/manifest validation).

## Dimension 2 — sample_data_coverage

- [ ] Frozen gold set fingerprint recomputes to the stored fingerprint.
- [ ] Curator agreement is unanimous (1.0).
- [ ] All eight buckets present (`local/cross_chapter/global/causal/character_knowledge/
      world_rule/no_answer/spoiler`) with ≥ 1 sample each.
- [ ] Phase 28-04 `CandidateManifest` bound: source_snapshot == gold set snapshot,
      checksum valid, all five `DimensionKind`s present.
- [ ] Manifest parity: every `DimensionResult` matches the manifest header on
      snapshot/cutoff/owner/version/budget/lineage; a blocked dimension carries a
      stable `blocked_reason`.
- [ ] Candidate vs leaf-baseline manifest parity (D-04): identical source/cutoff/budget/
      owner/version/lineage/blocked_reason.
- [ ] No pointer vocabulary in the manifest payload.

## Dimension 3 — quality_qualification

- [ ] 29-02 `QualificationReport` bound: checksum valid, verdict legal, header lineage
      matches the audit header.
- [ ] Report manifest snapshot matches the bound `CandidateManifest`.
- [ ] All eight bucket reports present with candidate + baseline metric blocks and the
      18 required bucket metrics.
- [ ] Operations ledger complete (`cost_usd_total` never silently missing).
- [ ] Browser evidence bound (D-06): e2e specs exist and assert spoiler-safe paths,
      citation jump, partial/failure states, accessibility and mobile width.
- [ ] Phase 22 0/3 blocked risk preserved **independently**; never folded into the
      verdict, never reported as a percentage.

## Overall

- [ ] Verdict is only `qualified_candidate` or `blocked` (D-05).
- [ ] No single completion percentage is produced anywhere.
- [ ] Audit consumes evidence only: never writes STATE/ROADMAP, never patches or
      rewrites a manifest, never writes an active pointer.

## 独立测试验证（本任务本地自测；最终以独立测试子代理为准）

| 命令 | 结果 |
|---|---|
| `cd backend; pytest tests/integration/qualification/test_audit.py -q` | ✅ 本地自测（见测试输出摘要） |

## 备注 / 偏差

- 无 DB schema 变更；审计为纯内存证据调和契约（同 runner/report）。
- 审计不产出 SUMMARY/VERIFICATION（由主代理在独立验证后统一处理），本文件为 PLAN
  Task 1 授权的唯一审计 checklist 工件。
