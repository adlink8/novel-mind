---
phase: 12-read-only-asset-audit-and-eligibility
verified: 2026-07-15
status: gaps_found
score: "1/4 requirements fully verified; 5/10 plan truths verified"
---

# Phase 12 Verification — Read-only Asset Audit and Eligibility

**Goal:** 在任何模型调用或上层写入前，以只读方式证明单本小说现有 hierarchy 与可选分析资产是否可被 v0.8 精确复用，并给出可机器处理的阻断原因和最小重建范围。

**Verdict:** `gaps_found`

Phase 12 已交付严格报告 contract、纯资格 evaluator、PostgreSQL reader、provider guard、管理员 API 和 CLI；定向测试与 Ruff 均通过，实际代码中也未发现 provider、repair、promotion 或 ORM 写入路径。但是当前 PostgreSQL 资格判断尚不能证明所有 `reusable_exact` 都是 exact：required hierarchy 未校验 build lifecycle/immutability 且会过滤掉同 build 的 foreign-scope node；optional readers 未完整校验 owner/novel/checksum/事实计数。因而 Phase 12 goal 和 V08-AUDIT-01..03 尚不能判定通过。

## Evidence Reviewed

- `12-01-PLAN.md`, `12-02-PLAN.md`, `12-03-PLAN.md`
- `12-01-SUMMARY.md`, `12-02-SUMMARY.md`, `12-03-SUMMARY.md`
- `12-CONTEXT.md`, `12-RESEARCH.md`, `.planning/REQUIREMENTS.md`
- `backend/app/services/narrative_memory/{audit_contracts,audit_sources,audit,audit_pg}.py`
- `backend/app/api/asset_audit.py`, `backend/scripts/run_asset_audit.py`, `backend/app/main.py`
- Phase 12 unit/integration tests and existing Phase 11 source-protocol tests

## Commands Executed

```text
cd backend
.\.venv\Scripts\python.exe -m pytest \
  tests/unit/narrative_memory \
  tests/integration/narrative_memory \
  tests/integration/clues/test_source_protocols.py -q
# 29 passed, 3 existing pytest-timeout configuration warnings

.\.venv\Scripts\ruff.exe check \
  app/services/narrative_memory app/api/asset_audit.py \
  scripts/run_asset_audit.py tests/unit/narrative_memory \
  tests/integration/narrative_memory
# All checks passed
```

Prior execution evidence retained from `12-03-SUMMARY.md`:

- Real CLI audit for novel 91 completed read-only and returned hierarchy `rebuild_required`, exit 2.
- Real CLI audit for novel 104 inspected 9,413 hierarchy rows, returned `content_hash_mismatch` with rebuild range 0–419, exit 2.
- Neither real book was repaired or reanalyzed.

## Requirement Verification

| Requirement | Status | Evidence / Gap |
|---|---|---|
| V08-AUDIT-01 — 按资产与版本生成只读资格报告 | ⚠ PARTIAL | Strict report、API、CLI 和四类资产均存在；但 optional inventory 可将跨 scope/checksum 不一致版本或非空 timeline/clue 错报为 exact/healthy-empty，报告事实不充分。 |
| V08-AUDIT-02 — 四种唯一分类 | ⚠ PARTIAL | Enum 和每资产唯一状态已验证；分类输入不完整，故 `reusable_exact` 结论并非始终可靠。 |
| V08-AUDIT-03 — 无效 Phase 07 hierarchy 在 provider 前阻断 | ✗ GAP | guard 本身 fail-closed，但 hierarchy adapter 未检查 `ChunkBuild.immutable`/status，并过滤同 build 的 foreign-novel nodes；这些无效状态可无 reason code 地到达 `reusable_exact`，使 guard 为 true。 |
| V08-AUDIT-04 — 零模型、零修复、零 active-pointer 写入 | ✓ VERIFIED | package/API/CLI 仅包含 SELECT/read 路径；capability scan、before/after tests 通过；未发现 provider/repair/promotion/session add/delete/commit/flush。测试证明范围仍有下述独立 observer 缺口，但实际代码未发现写能力。 |

**Requirement score:** 1/4 fully verified.

## Plan Must-have Verification

| Plan truth | Status | Evidence / Gap |
|---|---|---|
| 每项资产只有一种 closed status 与稳定 reason codes | ✓ | Strict enums、unique kind validation、canonical serialization unit tests通过。 |
| required hierarchy 仅 exact 时允许 provider；未知/缺失 fail closed | ⚠ | `EligibilityReport` guard 正确，但 PG adapter 可把未检查的 invalid build 标成 exact。 |
| optional unavailable 与 healthy empty 区分且不制造事实 | ✗ | unavailable 状态存在；但 timeline/clue reader不统计真实事实，兼容 active version 默认 `item_count=0`、`healthy_empty=true`。 |
| 首切片无 provider/ORM writer/repair/promotion/dispatch/pointer capability | ✓ | 静态扫描和源码检查通过。 |
| active hierarchy 被 lossless inventory，不由修复树推断 exact | ✗ | 查询在 `audit_pg.py:115-118` 预先限定 `novel_id`，因此同 `build_id` 的 foreign-scope rows 被隐藏而不是形成 anomaly。 |
| owner/novel/build/snapshot/manifest/tree/offset/hash/coverage 均在 exact 前证明 | ✗ | owner、novel、snapshot、manifest、tree、offset/hash、coverage 有检查；build `immutable`、lifecycle/status 未检查。 |
| timeline/relationship/clue 遵循真实 authority 且保持 optional | ✗ | authority 入口正确，但引用 version 后未重新校验 version owner/novel，也未校验 `hierarchy_checksum`；timeline/clue pointer manifest 与实际事实数未验证。 |
| 授权 operator 可通过 API/CLI 重现 scoped report | ✓ | superuser API、CLI 共用 service；entrypoint tests通过；canonical payload一致。 |
| audit 不改变 provider calls/domain rows/pointers/revisions/journals | ✓（代码）/⚠（证明） | 源码为 SELECT-only 且现有 before/after test通过；但测试使用同一 session，不是计划要求的 fresh observer，也未对所有 domain rows 做内容 checksum。 |
| blocked hierarchy 无法越过 pre-provider guard | ✓ | `provider_calls_allowed` 只由 required exact 派生；blocked CLI exit 2。invalid-but-misclassified 情况归入上面的 adapter gap。 |

**Plan truth score:** 5/10 verified；4 gaps；1 implementation verified but independent-proof incomplete.

## Blocking Gaps

### 1. Required hierarchy can be falsely classified `reusable_exact`

`PostgresAuditSource._hierarchy_inventory` verifies hashes/tree/coverage but never checks `ChunkBuild.immutable`, candidate/lifecycle status, or whether the active build is in an allowed committed state. An active pointer to a mutable or otherwise invalid build can therefore produce no reason code and allow `provider_calls_allowed=true`.

The node query also filters by both build and target novel (`audit_pg.py:115-118`). Because `ChunkHierarchyNode.build_id` is not a composite FK to the build's novel, a foreign-novel row sharing the build ID is possible. Filtering it out violates the plan's lossless inventory requirement and hides a foreign-scope anomaly.

**Required closure:** load all rows for the selected build ID, reject/flag any row whose novel differs, validate allowed build lifecycle plus `immutable=true`, and add PostgreSQL adversarial tests proving both cases keep the guard false.

### 2. Optional assets are not proved exact and can be mislabeled healthy-empty

Timeline and relationship use `AnalysisVersion`, and clue uses `ClueAnalysisVersion`, but the readers do not re-check referenced version owner/novel. `_lineage_reasons` only compares source snapshot and hierarchy build ID (`audit_pg.py:292-300`); it omits `hierarchy_checksum`. Clue has the same omission (`audit_pg.py:285`). Pointer manifest checks are also absent.

Additionally, timeline and clue always use default `item_count=0`; `_optional_result` then marks them `healthy_empty=true` (`audit_pg.py:316-331`) even when their active versions contain events/clues. This can turn “facts not inventoried” into “healthy empty,” contrary to the required unavailable-vs-empty semantics.

**Required closure:** validate owner/novel/version status, hierarchy checksum and pointer/version manifest lineage; count or otherwise prove the relevant domain facts/evidence; add active-nonempty, true-empty, cross-owner version and checksum-mismatch integration cases.

### 3. The promised fresh-observer no-side-effect proof is incomplete

`test_service_and_cli_helpers_leave_authority_unchanged` uses the same `audit_pg_session` for audit and before/after observation (`test_audit_no_side_effects.py:63-74`). The observer records selected table counts and pointers, not content checksums for all relevant domain authority, and it does not wrap the real HTTP API plus separate CLI process in the same independent observation.

Static inspection strongly supports V08-AUDIT-04 and no mutation was observed, so this is an evidence/plan-must-have gap rather than a discovered write. It still fails the explicit 12-03 “fresh PostgreSQL observer” acceptance claim.

**Required closure:** use a separate fresh session/connection to snapshot content digests and pointers/journals around the actual API and CLI entry paths, including malformed/blocked cases.

## Verified Artifacts and Links

- `audit_contracts.py`: strict four-status schema and policy-owned required/optional mapping.
- `audit.py`: deterministic evaluator and derived provider guard.
- `audit_pg.py`: real PostgreSQL reader, source re-slice, tree/manifest/coverage checks and optional adapters.
- `asset_audit.py`: authenticated superuser GET endpoint; no mutation route.
- `run_asset_audit.py`: canonical JSON; exit 0 only for allowed report, otherwise 2.
- `main.py`: router registered.
- 29 targeted tests pass, including API/CLI equivalence, owner isolation, malformed hierarchy, provider guard, capability scan and existing clue unavailable semantics.

## Overall Verdict

**GAPS FOUND.** The read-only/control-capability boundary is implemented correctly, and real CLI audits safely block both current books. However, Phase 12's core promise is not merely to block current fixtures; it must prove that any `reusable_exact` classification is exact. Missing hierarchy lifecycle/foreign-row validation and incomplete optional version/empty-state validation leave false-exact paths. Close these gaps and rerun independent verification before Phase 14 is allowed to treat the report as a provider-call gate.
