# Phase 35: Canon Fork — Research

**Researched:** 2026-08-01
**Domain:** authority-separated retrieval, immutable candidate lineage, negative contamination gates
**Confidence:** HIGH for repository contracts; MEDIUM for Issue #29 scope (public URL returned 404)

<user_constraints>
## User Constraints

负责 Phase 35–39，唯一写入 35-canon-fork、36-derivative-editor、37-constrained-derivative-generation、38-derivative-visual-consistency、39-derivative-export-closeout 五个目录。三空间隔离、branch-aware retrieval、Canon 污染负向测试为硬门；Agent 只产候选，确定性代码掌握发布。

Issue #29 是范围权威；Phase 22 仍 0/3 nightly，执行门不因规划完成而解除。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| REQ-FORK-01 | 三空间具有独立 authority/namespace/version/citation | `NarrativeMemory` scope/hash/provenance 可复用；新空间必须显式隔离 |
| REQ-CRE-01 | 三空间独立 authority、namespace、version、citation | `retrieval_contracts.py` 的 immutable scope/cutoff/manifest 是 analog |
| REQ-CRE-02 | 创作不得进入原作检索、评测或 facet 链 | 负向写入、索引查询、eval/facet fixture 三类测试 |
</phase_requirements>

## Summary

不要把既有 `FanFiction` 表直接扩成“原作 + 创作共用”的检索实体；它当前没有 owner、space、fork、revision 或 citation lineage，且 `/api/fanfiction` 明确返回 501 deferred（`backend/app/api/fanfiction.py`）。使用独立的 authority/namespace contract，将 Original Canon 作为只读输入，将 User Interpretation 作为受保护 override，将 Fanfiction Canon 作为 derivative candidate 输出。[CITED: backend/app/api/fanfiction.py; backend/app/models/fanfiction.py]

核心边界应在排序/LLM 前应用：`owner_id + novel_id + space + fork_id + version_id + cutoff + source_snapshot_hash` 组成 retrieval scope；任何跨空间结果、未来章节、错误 fork 或不匹配 hash 都返回 blocked/empty-with-reason，而不是过滤后继续发布。[CITED: backend/app/services/narrative_memory/retrieval_contracts.py]

**Primary recommendation:** 新增独立三空间 contract 和 immutable fork manifest，复用现有 retrieval/citation hash 语义，并用“写入原作索引后查询/评测/facet 必须看不到 derivative 内容”的负向测试作为 Phase 35 release gate。[CITED: ROADMAP.md#Phase 35; REQUIREMENTS.md#REQ-CRE-02]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| 三空间 authority/namespace/version | Database / Storage | API / Backend | 数据库约束和 manifest 必须阻止跨空间引用；API 只暴露已校验 scope。 |
| branch-aware retrieval/cutoff | API / Backend | Database / Storage | 路由、可见集和 citation revalidation 是后端确定性职责。 |
| contamination prevention | Database / Storage | API / Backend | index/eval/facet 写入边界和查询过滤需 fail closed。 |
| fork selection and citation display | Browser / Client | API / Backend | 客户端选择 fork；服务端重新验证 owner、version、cutoff 与 citation。 |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---|---|---|---|
| FastAPI | `>=0.115` | authenticated API/dependencies | 当前后端框架；沿用 `require_user`/owner dependency。[CITED: backend/requirements.txt; backend/app/api/dependencies.py] |
| SQLAlchemy | `>=2.0` | scoped ORM/constraints/transactions | 现有 immutable candidate and owner models 均采用。[CITED: backend/requirements.txt; backend/app/models/narrative_memory.py] |
| Pydantic | `>=2.13` | strict frozen DTOs | retrieval contract 使用 `extra=forbid`, strict/frozen validation。[CITED: backend/requirements.txt; backend/app/services/narrative_memory/retrieval_contracts.py] |
| PostgreSQL + Alembic | `asyncpg>=0.30`, `alembic>=1.14` | durable scope and migration authority | 当前生产数据/迁移路径。[CITED: backend/requirements.txt; backend/migrations/versions/13_narrative_memory_authority.py] |

### Supporting

| Library | Version | Purpose | When to Use |
|---|---|---|---|
| ChromaDB | `1.5.9` | existing raw/vector index boundary | 只允许 Original Canon index adapter 接收原作 rows；不新建混合 collection。[CITED: backend/requirements.txt; docs/architecture/04-request-flow.md] |
| pytest/httpx | `httpx>=0.28`; pytest from dev environment | API/adversarial tests | 复用 backend unit/integration/adversarial layout。[CITED: backend/requirements-dev.txt; backend/tests/adversarial] |

**Installation:** 本阶段不锁定新依赖；使用现有依赖。任何新包必须另加 human-verify 与 legitimacy audit。

## Package Legitimacy Audit

本阶段无新增外部包安装，故无待审计包；以上为仓库已有 manifest 依赖，不代表批准新增依赖。

## Architecture Patterns

### System Architecture Diagram

```text
Authenticated request
  -> deterministic ScopeResolver(owner, novel, space, fork, version, cutoff)
  -> branch-aware candidate loader (Original/User/Fanfiction namespace only)
  -> visible-set + hash validation
  -> leaf citation revalidation
  -> read-only response / candidate derivative result
  -> negative gate: derivative IDs absent from Original index/eval/facet
```

### Recommended Project Structure

```text
backend/app/services/canon_fork/
├── contracts.py          # strict space/fork/cutoff DTOs
├── authority.py          # deterministic scope and mutation guards
├── retrieval.py          # branch-aware loading and leaf citations
└── contamination.py      # index/eval/facet negative gate helpers
backend/app/models/canon_fork.py
backend/app/api/canon_fork.py
backend/tests/adversarial/test_canon_space_isolation.py
```

### Pattern 1: Scope-before-ranking

先构造 immutable retrieval scope，再加载 candidate；不得先全局向量搜索再靠 UI 过滤。[CITED: backend/app/services/narrative_memory/retrieval_contracts.py]

### Pattern 2: Immutable manifest and leaf citation

每个 fork 指向 source snapshot、parent version、cutoff hash 和 manifest checksum；最终 citation 必须重新切片并校验 content hash。[CITED: backend/app/services/narrative_memory/retrieval_manifests.py]

### Anti-Patterns to Avoid

- **共享 collection + metadata 后过滤：** 任何遗漏都会造成 Canon 污染；使用 namespace-specific adapter/collection boundary。[ASSUMED]
- **把 User Interpretation 直接写成 Original claim：** override 应追加、可追踪、不可静默升级。[CITED: `.planning/ROADMAP.md` execution rules]
- **让模型决定空间或发布状态：** 模型只能候选，确定性代码 gate 决定。[CITED: docs/architecture/08-ai-model-layer.md]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| owner/novel authorization | 每个 route 手写查询 | `require_user` + `require_owned_novel` analog | 统一 404/owner 语义，降低跨用户泄露。[CITED: backend/app/api/dependencies.py] |
| citation integrity | 只保存 excerpt | existing leaf offset/hash + manifest pattern | excerpt 可被文本变化或伪造，hash/offset 才能重验。[CITED: retrieval_contracts.py] |
| immutable candidate identity | mutable active row | `NarrativeMemoryVersion`/manifest pattern | 复现、回滚、审计和 no-cutover 已被现有模式覆盖。[CITED: backend/app/models/narrative_memory.py] |

## Common Pitfalls

1. **空间只存在于 API 参数：** 数据库唯一键/外键未含 space/fork，错误引用可持久化；所有 identity 和 FK scope 都要带 namespace。[CITED: backend/app/models/narrative_memory.py]
2. **branch-aware retrieval 只检查 cutoff：** 错 fork 同样会泄露；scope hash 必须包含 fork/version/source snapshot。[CITED: retrieval_contracts.py]
3. **负向测试只查数据库：** Chroma、eval dataset、facet production 可能另有入口；测试必须覆盖三条写入链。[ASSUMED]
4. **健康空结果与 blocked 混同：** response 要区分 `absent`, `blocked`, `mismatch`，不得返回看似成功的空数组。[CITED: retrieval_contracts.py; backend/app/api/fanfiction.py]

## Code Examples

```python
# Pattern: scope is frozen before loading candidates (adapt existing contract)
scope = RetrievalScope(
    owner_id=owner_id, novel_id=novel_id, version_id=canon_version_id,
    source_snapshot_hash=snapshot_hash, hierarchy_build_id=build_id,
    hierarchy_checksum=hierarchy_checksum, candidate_manifest_checksum=manifest,
    cutoff=cutoff, policy_version=policy_version, policy_hash=policy_hash,
)
```

Source: `backend/app/services/narrative_memory/retrieval_contracts.py`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|---|---|---|---|
| fanfiction placeholder/501 | explicit derivative domain with candidate-only lineage | v1.4 roadmap | removes fake success and protects Original Canon.[CITED: backend/app/api/fanfiction.py; ROADMAP.md] |
| unscoped hierarchical retrieval | owner/version/cutoff/hash scoped retrieval | Phase 15 contract | branch-aware fork can reuse proven safety boundary.[CITED: 15-RESEARCH.md; retrieval_contracts.py] |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | Issue #29 content is unavailable because public URL returned 404; user-provided wording is sufficient scope. | Summary/Context | Hidden issue details could add requirements. |
| A2 | Phase 30–34 visual contracts are not present in this checkout. | Context | Phase 38 may need alternate refs. |
| A3 | Separate physical/vector boundaries are preferable to shared collection metadata filtering. | Anti-patterns | Storage design may need migration/ops work. |

## Open Questions (RESOLVED)

1. **Issue #29 exact body and acceptance checklist — RESOLVED:** this phase follows the locked CONTEXT/ROADMAP requirements; no extra Issue #29 scope is introduced.
2. **Namespace physical layout — RESOLVED:** Original, User Interpretation and Fanfiction use the same index/storage implementation with a mandatory composite scope (`owner_id`, `novel_id`, `space`, `namespace`, `fork_id`, `version_id`, `cutoff`, `source_snapshot_hash`); no separate unscoped collection is permitted.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python | backend contracts/tests | ✓ | 3.14.2 | project venv/toolchain must confirm supported version |
| PostgreSQL/Docker | integration isolation tests | ✓ Docker 29.6.1; DB not probed | — | unit tests with SQLite are insufficient for FK/index semantics |
| ChromaDB | original index boundary | manifest present; service not probed | 1.5.9 required | use fake adapter for unit tests, integration gate needs service |

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Framework | pytest + pytest-asyncio/httpx; Vitest/Playwright for UI consumers |
| Config file | `backend/pytest.ini`, `frontend/vitest.config.ts`, `frontend/playwright.config.ts` |
| Quick run command | `cd backend; pytest tests/adversarial/test_canon_space_isolation.py -q` (planned) |
| Full suite command | `cd backend; pytest tests/ -q` plus frontend `npm test` and targeted Playwright |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| REQ-FORK-01 | same owner/novel cannot cross space/fork/version | integration/adversarial | `pytest backend/tests/adversarial/test_canon_space_isolation.py -q` | ❌ Wave 0 |
| REQ-CRE-02 | derivative absent from Original index/eval/facet | integration negative | `pytest backend/tests/adversarial/test_canon_contamination.py -q` | ❌ Wave 0 |
| REQ-CRE-01 | citation only resolves to authorized leaf | unit | `pytest backend/tests/unit/canon_fork/test_contracts.py -q` | ❌ Wave 0 |

### Sampling Rate

- Per task commit: planned focused pytest command.
- Per wave merge: backend contract + adversarial subset and frontend contract tests.
- Phase gate: full relevant suite green; do not claim Phase 22 qualification.

### Wave 0 Gaps

- [ ] `backend/app/models/canon_fork.py` and migration
- [ ] `backend/tests/unit/canon_fork/test_contracts.py`
- [ ] `backend/tests/integration/test_canon_space_isolation.py`
- [ ] `backend/tests/adversarial/test_canon_contamination.py`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | yes | existing `require_user` |
| V3 Session Management | yes | existing JWT/cookie middleware |
| V4 Access Control | yes | owner + novel + space/fork scope, 404 on mismatch |
| V5 Input Validation | yes | strict Pydantic/frozen contracts |
| V6 Cryptography | yes | existing SHA-256 lineage hashes; never hand-roll encryption |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| derivative row returned by Original query | Information disclosure | namespace predicates + negative tests |
| forged fork/version/citation IDs | Tampering | composite scope checks + leaf hash revalidation |
| future cutoff in branch retrieval | Information disclosure | server-derived cutoff, full-book authorization gate |

## Sources

### Primary (HIGH confidence)

- `ROADMAP.md`, Phase 35; `REQUIREMENTS.md`, `REQ-FORK-01/REQ-CRE-01/02`.
- `backend/app/services/narrative_memory/retrieval_contracts.py`, `retrieval_manifests.py`, `backend/app/models/narrative_memory.py`.
- `backend/app/api/dependencies.py`, `backend/app/api/fanfiction.py`.

### Secondary (MEDIUM confidence)

- `.planning/phases/15-*/15-RESEARCH.md`, `.planning/phases/17-*/17-RESEARCH.md`.
- `docs/architecture/03-data-model.md`, `04-request-flow.md`, `08-ai-model-layer.md`.

### Tertiary (LOW confidence)

- GitHub issue URL `https://github.com/adlink8/novel-mind/issues/29` — returned 404; not used to add requirements.

## Metadata

**Confidence breakdown:** Standard stack HIGH (existing manifests); architecture HIGH (existing contracts); pitfalls MEDIUM (some storage boundary recommendations are inferred).
**Research date:** 2026-08-01
**Valid until:** 2026-09-01 for stable repository contracts; shorter if dependency manifests change.
