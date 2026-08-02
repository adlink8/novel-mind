# Phase 39: Derivative Export Closeout — Research

**Researched:** 2026-08-01
**Domain:** reproducible Markdown/EPUB export, manifest/asset/citation parity, browser UAT and independent audit
**Confidence:** HIGH for audit/lineage requirements; MEDIUM for EPUB implementation choice

<user_constraints>
## User Constraints

负责 Phase 35–39，唯一写入五个 phase 目录。三空间隔离、branch-aware retrieval、Canon 污染负向测试为硬门；Agent 只产候选，确定性代码掌握发布。Issue #29 是范围权威；Phase 22 仍 0/3 nightly。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| REQ-FORK-05 | export preserves content/assets/citations/version parity and passes UAT/audit | immutable revision/manifest and derivative-only scope |
| REQ-CRE-07 | export Markdown/EPUB content matches version | deterministic export snapshot and round-trip fixtures |
| REQ-SHIP-02 | three-dimensional status report | existing status dimensions and blocked semantics |

## Summary

当前实现仓库没有可核实的 EPUB exporter；Phase 34 研究层已定义 frozen export manifest、Markdown/HTML/EPUB parity、missing-asset report 和 EPUB inspection fixture，但这些是上游规划契约，不是已通过的实现。[CITED: `.planning/phases/34-illustration-anchor-export/34-RESEARCH.md`; docs/需求文档.md; backend/app/api/fanfiction.py; IMPLEMENTATION-STATUS.md]

导出应先冻结一个 derivative export manifest：project/fork/revision IDs and checksums、ordered chapters、asset IDs/hashes、citation refs/source snapshot、exporter/schema/config versions。Markdown 和 EPUB 都从同一个 snapshot materialize；禁止两个格式各自查询 live database，否则会产生版本漂移。[CITED: ROADMAP.md#Phase 39; backend/app/services/narrative_memory/retrieval_manifests.py]

**Primary recommendation:** 先生成 immutable export snapshot，再用无新增依赖的确定性 Markdown serializer 和最小 EPUB package writer；若需要第三方 EPUB library，必须由 planner 加 registry/source/slopcheck/human-verify gate，不在本研究锁定。[ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| export snapshot/manifest | Database / Storage | API / Backend | parity and provenance must be durable and hashable |
| Markdown/EPUB serialization | API / Backend | CDN / Static | server owns deterministic bytes; files are output artifacts |
| asset/citation package | API / Backend | Database / Storage | server revalidates derivative scope and leaf refs |
| browser UAT/audit report | Browser / Client | API / Backend | user flow is browser-observable, verdict is evidence-backed |

## Standard Stack

| Library/Tool | Version | Purpose | Evidence |
|---|---|---|---|
| Python standard library (`zipfile`, `xml.etree`, `hashlib`) | runtime | minimal deterministic EPUB container and checksums | [CITED: Python runtime available; no EPUB dependency in `backend/requirements.txt`] |
| FastAPI/SQLAlchemy/Pydantic | existing manifest | export endpoint, snapshot transaction, response contract | [CITED: backend/requirements.txt] |
| Playwright | `^1.61.1` | desktop/mobile end-to-end UAT | [CITED: frontend/package.json; frontend/e2e] |
| pytest/httpx | existing test toolchain | round-trip/security/audit tests | [CITED: backend/requirements-dev.txt; docs/architecture/10-testing-ci.md] |

No new package is locked. EPUB third-party alternatives are not recommended until registry/legitimacy verification.[ASSUMED]

## Package Legitimacy Audit

No external package installation proposed; audit not applicable. If a planner introduces `ebooklib` or another package, it must run the mandated slopcheck + correct registry + postinstall review before adding it to Standard Stack.

## Architecture Patterns

```text
published derivative revision + derivative assets
  -> scope/owner/fork/version/citation validation
  -> immutable ExportManifest + manifest checksum
  -> deterministic serializers (Markdown, EPUB)
  -> artifact hashes + package manifest
  -> download/UAT/audit
  -> three-dimensional report; no pointer/canon mutation
```

### Recommended Project Structure

```text
backend/app/services/derivative_export/
├── snapshot.py
├── manifest.py
├── markdown.py
├── epub.py
└── audit.py
backend/app/api/derivative_export.py
backend/tests/integration/test_derivative_export.py
backend/tests/adversarial/test_derivative_export_isolation.py
frontend/e2e/derivative-export.spec.ts
```

### Pattern: one snapshot, two serializers

Load and validate all content/assets/citations once; serializers receive only the frozen DTO. Artifact metadata records source manifest checksum, exporter version and byte hash.[CITED: backend/app/services/narrative_memory/retrieval_manifests.py]

### Anti-Patterns

- Exporting from `FanFiction.content` and chapter rows in separate live queries.
- Embedding original asset paths or unrestricted future citations.
- Calling “download succeeded” a quality/audit pass without round-trip and security evidence.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| version parity | two independent data reads | frozen `ExportManifest` | single source for both formats [CITED: roadmap] |
| provenance | filename/URL only | IDs + hashes + source snapshot/citation refs | export must be reproducible [CITED: narrative memory manifest] |
| auth | public artifact path | authenticated scope check + non-guessable artifact ID | prevents owner leakage [CITED: docs/architecture/07-auth-security.md] |

## Common Pitfalls

1. EPUB OPF/NCX/container metadata order is nondeterministic; canonicalize ordering and timestamps.[ASSUMED]
2. Markdown and EPUB normalize whitespace differently; compare normalized chapter content and source revision hash, not raw HTML.[ASSUMED]
3. Missing derivative asset causes silent placeholder; fail export or report explicit missing asset.
4. Citation references point to original future chapters or stale hashes; revalidate before snapshot.[CITED: retrieval_contracts.py]
5. Audit report claims green while Phase 22 remains blocked; keep dimensions independent.[CITED: STATE.md; ROADMAP.md]

## Code Examples

```python
snapshot = build_export_snapshot(scope=project_scope, revision_id=revision_id)
manifest = seal_manifest(snapshot)
markdown_bytes = render_markdown(snapshot)
epub_bytes = render_epub(snapshot)
assert sha256(markdown_bytes).hexdigest() == manifest.markdown_sha256
```

Planning sketch; serializer symbols are proposed.[ASSUMED]

## State of the Art

Current repository qualification reports distinguish implementation readiness, sample coverage and quality qualification; Phase 39 should use the same independent dimensions and emit `qualified_candidate` or `blocked`, never production promotion.[CITED: STATE.md; ROADMAP.md]

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Python stdlib EPUB writer is acceptable and interoperable enough. | Need package or external EPUB validator; planner must add checkpoint. |
| A2 | Local artifact delivery is sufficient; no object storage/CDN required. | Deployment may require storage adapter. |
| A3 | “Published derivative revision” is the export input state. | Phase 36/37 status vocabulary may change. |

## Open Questions (RESOLVED)

- **EPUB/accessibility/validation — RESOLVED:** emit EPUB3 with required package metadata, navigation and accessible semantic structure; validate with the repository's available EPUB inspection fixture/command when present, otherwise record interoperability as unverified rather than green.
- **Citation package placement — RESOLVED:** include citations as a companion manifest package with leaf refs/source snapshot/hash and expose stable links/IDs from Markdown and EPUB content; serializers consume the same snapshot DTO.
- **Audit owner — RESOLVED:** the independent GSD Phase 39 audit gate owns the final evidence review and may issue only `qualified_candidate` or `blocked`; it cannot promote, cut over an active pointer or run production A/B.

## Environment Availability

| Dependency | Available | Version | Fallback |
|---|---|---|---|
| Python | ✓ | 3.14.2 | repository backend venv for execution |
| Pandoc | ✗ | — | deterministic Markdown; EPUB requires stdlib writer or approved package |
| Calibre/ebook-convert | ✗ | — | no external EPUB conversion; flag as blocking for validation if required |
| Docker | ✓ | 29.6.1 | PG integration still needs service readiness |
| Playwright package | manifest present | `^1.61.1` | manual browser UAT if browser binary unavailable |

**Missing dependencies with no fallback:** none for planning; EPUB interoperability certification is blocked if an external validator becomes a hard requirement.

## Validation Architecture

| Property | Value |
|---|---|
| Backend | pytest + httpx integration/adversarial; `backend/pytest.ini` |
| Frontend | Playwright desktop + 390px mobile; existing e2e helpers |
| Quick command | `pytest backend/tests/integration/test_derivative_export.py -q` (planned) |
| Full command | backend export/security suite + frontend targeted UAT + audit script |

| Req | Behavior | Test | File |
|---|---|---|---|
| REQ-CRE-07 | Markdown/EPUB round-trip equals frozen revision | integration/fixture | ❌ Wave 0 |
| REQ-FORK-05 | asset/citation/version manifest parity | integration/adversarial | ❌ Wave 0 |
| REQ-FORK-05 | other owner and Original space cannot export | security | ❌ Wave 0 |
| REQ-SHIP-02 | report preserves readiness/data/quality independently | audit contract | ❌ Wave 0 |

Fixture strategy: two owners, two forks, two revisions, one approved and one rejected asset, citation hash mutation, missing asset, stale revision, and original-space ID attempt. Manual UAT: create/edit/review, export both formats, download/reopen, compare chapter order/content/asset list/citations, attempt cross-owner/original access, inspect manifest and report. Do not run implementation tests during this research turn.

## Security Domain

V2/V3 existing auth/session; V4 owner/project/fork/export scope; V5 content/asset validation and output size; V6 hashes and encrypted provider configuration. Threats: artifact IDOR, path traversal, zip-slip, stale citation/future leakage, archive bombs and original-space mutation. Mitigate with generated archive paths, allowlisted entries, bounded sizes, scope checks, fresh hash validation and no writes to Original tables.[CITED: docs/architecture/07-auth-security.md; backend/app/services/narrative_memory/retrieval_contracts.py]

## Sources

- HIGH: `ROADMAP.md`, `REQUIREMENTS.md`, `STATE.md`, `IMPLEMENTATION-STATUS.md`.
- HIGH: `backend/app/services/narrative_memory/retrieval_manifests.py`, `backend/app/models/narrative_memory.py`, `docs/architecture/10-testing-ci.md`.
- MEDIUM: `.planning/phases/34-illustration-anchor-export/34-RESEARCH.md`, `docs/需求文档.md`, `docs/architecture/07-auth-security.md`, `frontend/e2e/helpers.ts`.
- LOW: EPUB stdlib recommendation; no official project requirement or validator is present.

## Metadata

Requirements/audit HIGH; export architecture MEDIUM; EPUB interoperability LOW until validator and format requirements are decided. Valid until 2026-08-15.
