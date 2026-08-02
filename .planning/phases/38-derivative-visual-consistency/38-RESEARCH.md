# Phase 38: Derivative Visual Consistency — Research

**Researched:** 2026-08-01
**Domain:** forked Visual Bible, derivative Scene Specs, asset provenance and cross-chapter identity checks
**Confidence:** MEDIUM (Phase 30–34 artifacts are absent in this checkout); HIGH for shared lineage/security patterns

<user_constraints>
## User Constraints

负责 Phase 35–39，唯一写入五个 phase 目录。三空间隔离、branch-aware retrieval、Canon 污染负向测试为硬门；Agent 只产候选，确定性代码掌握发布。Issue #29 是范围权威；Phase 22 仍 0/3 nightly。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| REQ-FORK-04 | derivative Visual Bible/assets cannot mutate original visual authority | immutable candidate/version + asset provenance pattern |
| REQ-CRE-06 | explicit divergence and no Original write-back | deterministic review/publish gate |

## Summary

Phase 30–34 的研究层工件现已由并发 agent 出现在工作区：Phase 30 定义 candidate Visual Bible，Phase 32 定义 evidence-bounded Scene Spec/prompt compiler，Phase 33 定义 immutable AssetRevision/candidate-only generation，Phase 34 定义 anchor hash/approved asset/export manifest。实现代码仍未由本次研究核实，因此 Phase 38 应把这些工件当作上游规划契约，并在执行前检查实际代码。[CITED: `.planning/phases/30-visual-bible/30-RESEARCH.md`; `.planning/phases/32-scene-spec-prompt-compiler/32-RESEARCH.md`; `.planning/phases/33-illustration-generation-consistency/33-CONTEXT.md`; `.planning/phases/34-illustration-anchor-export/34-CONTEXT.md`]

资产不能只以文件路径识别：需要 `owner_id`, `project_id`, `fork_id`, `visual_version`, `scene_spec_id`, `source_asset_refs`, `identity_key`, content hash, generator lineage, divergence and review status。这个结构直接对应 NM 的 immutable version/manifest/source link 语义。[CITED: backend/app/models/narrative_memory.py]

**Primary recommendation:** 原作 Visual Bible 作为 immutable source snapshot；Scene Spec compiler 只生成 derivative candidate metadata；图像 provider 只产生候选文件；确定性 asset gate 校验 namespace、hash、identity/provenance、review and version parity 后才发布至 Fanfiction Canon visual namespace。[CITED: ROADMAP.md#Phase 38; REQUIREMENTS.md#REQ-FORK-04]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Visual Bible/version/provenance | Database / Storage | API / Backend | lineage and immutability are durable facts |
| Scene Spec compilation | API / Backend | Database / Storage | deterministic references and constraints |
| image generation candidate | external provider | API / Backend | provider output has no authority |
| review and asset display | Browser / Client | API / Backend | UI presents candidate/review state; server controls transitions |

## Standard Stack

| Library | Version | Purpose | Evidence |
|---|---|---|---|
| FastAPI/SQLAlchemy/Pydantic | existing requirements | asset API, DB lineage, strict contracts | [CITED: backend/requirements.txt] |
| existing file storage abstraction | repository `storage/`/upload paths | candidate bytes and checksums | [CITED: backend/app/models/novel.py; docs/architecture/03-data-model.md] |
| Playwright/Vitest | existing frontend manifest | identity/review/browser checks | [CITED: frontend/package.json; frontend/e2e] |

No new image SDK or visual package is approved. Phase 38 planning must include a provider/registry checkpoint if implementation needs one.[ASSUMED]

## Package Legitimacy Audit

No new package installation proposed; no audit applicable. Any image provider SDK must pass registry/source/postinstall review before install.

## Architecture Patterns

```text
Original Visual Bible snapshot (read-only)
  -> ForkVisualBible + divergence declaration
  -> derivative SceneSpec compiler (allowed refs only)
  -> provider -> AssetCandidate bytes + provider lineage
  -> deterministic asset gate (namespace/hash/identity/provenance/review)
  -> derivative published asset/version
```

### Recommended Project Structure

```text
backend/app/models/derivative_visual.py
backend/app/schemas/derivative_visual.py
backend/app/services/derivative_visual/{fork,scene_spec,assets,gates}.py
backend/app/api/derivative_visual.py
backend/tests/adversarial/test_visual_namespace_isolation.py
frontend/src/components/writing/visual-review-panel.tsx
frontend/src/lib/derivative-visual-api.ts
```

### Pattern: identity separate from style

Store stable character/place/object identity references separately from style/render parameters; a style change should create a new candidate version without changing identity. This is a design recommendation inferred from the requirement for cross-chapter consistency.[ASSUMED]

### Anti-Patterns

- Copying original asset rows into derivative namespace without preserving source snapshot and divergence.
- Treating a generated file path as provenance.
- Updating an original Visual Bible row when derivative review accepts an asset.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| asset identity | filename conventions | identity key + content hash + provenance rows | filenames collide and do not express versions [ASSUMED] |
| review state | boolean `approved` only | explicit candidate/review/published/rejected transitions | audit and rollback need reason/actor/time [CITED: clue/timeline override patterns] |
| access control | path-only checks | owner/project/fork scope dependencies | prevents asset IDOR [CITED: backend/app/api/dependencies.py] |

## Common Pitfalls

1. Original reference URL accidentally becomes derivative publish target.
2. A rejected candidate remains reachable through a CDN/storage path.[ASSUMED]
3. Cross-chapter consistency compares pixels only and ignores identity/version metadata.[ASSUMED]
4. Review UI displays a derivative asset without showing divergence and source refs.
5. Asset manifests omit the exact derivative content revision, causing export mismatch later.[CITED: REQ-FORK-05]

## Code Examples

```python
candidate = AssetCandidate(
    namespace="fanfiction_visual",
    fork_id=fork.id,
    source_snapshot_hash=fork.source_snapshot_hash,
    identity_key=scene_spec.identity_key,
    content_hash=sha256_bytes(blob),
    status="candidate",
)
# deterministic publisher rejects namespace != derivative or source hash mismatch
```

Planning sketch; field names are proposed, not current API.[ASSUMED]

## State of the Art

The reliable repository pattern is immutable, checksum-sealed candidates with explicit qualification and no active pointer side effects; visual assets should use the same lifecycle.[CITED: backend/app/models/narrative_memory.py; `.planning/phases/17-*/17-RESEARCH.md`]

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Phase 30–34 research contracts are present, but their implementation status is not verified in this turn. | Names/fields may still drift from implementation. |
| A2 | Asset bytes can use existing local storage rather than object storage. | Deployment may require a storage adapter. |
| A3 | Identity keys are manually/algorithmically supplied before generation. | Provider-specific identity control may be needed. |

## Open Questions (RESOLVED)

- **Phase 30–34 authority and citation model — RESOLVED:** the authoritative visual contract is the Phase 30–34 CONTEXT/PLAN and produced artifact set; Phase 38 must read and reference those contracts for entity identity, key-scene evidence, Scene Spec, provider lineage and anchor/export provenance.
- **Image provider and consistency signal — RESOLVED:** reuse the provider-neutral Phase 33 contract and its identity/style evidence fields; no new provider or unversioned signal is selected here.
- **Review granularity — RESOLVED:** review is attached to each candidate asset and its Scene Spec lineage; chapter/package views aggregate those immutable asset decisions without changing their scope.

## Environment Availability

| Dependency | Available | Version | Fallback |
|---|---|---|---|
| local file storage | ✓ directory exists | — | use isolated derivative path and hash |
| image provider credentials | not inspected | — | deterministic fixture assets for unit/UAT; live generation blocked |
| browser tooling | manifest present | Playwright `^1.61.1` | manual UAT if browsers unavailable |

## Validation Architecture

| Property | Value |
|---|---|
| Backend | pytest contract/integration/adversarial |
| Frontend | Vitest/RTL + Playwright desktop/mobile |
| Quick command | `pytest backend/tests/adversarial/test_visual_namespace_isolation.py -q` (planned) |
| Full command | relevant backend suite + frontend tests + targeted browser UAT |

| Req | Behavior | Test | File |
|---|---|---|---|
| REQ-FORK-04 | original Visual Bible and asset rows remain unchanged | integration negative | ❌ Wave 0 |
| REQ-CRE-06 | divergence is explicit and candidate gate is deterministic | unit/adversarial | ❌ Wave 0 |
| REQ-FORK-04 | cross-chapter identity/version mismatch blocks publish | fixture/integration | ❌ Wave 0 |

Fixtures: same identity across three chapters, intentional style divergence, wrong source namespace, reused original path, changed source hash, rejected candidate, two owners. Manual UAT: fork, create Scene Spec, inspect source refs, review candidate, reject/accept, verify original view/index unchanged and derivative asset survives refresh.

## Security Domain

V2/V3 existing auth; V4 owner/project/fork/asset scope; V5 file type/size/content validation; V6 existing key encryption and hashes. Threats: asset IDOR, path traversal, untrusted image metadata/SSRF provider URL, original asset overwrite, leakage via thumbnails. Mitigate with allowlisted storage roots, generated IDs, scope checks, existing SSRF validator and no direct provider publish.[CITED: docs/architecture/07-auth-security.md]

## Sources

- HIGH: `ROADMAP.md`, `REQUIREMENTS.md`, `STATE.md`.
- HIGH: `backend/app/models/narrative_memory.py`, `backend/app/api/dependencies.py`, `backend/requirements.txt`.
- MEDIUM: `docs/architecture/03-data-model.md`, `07-auth-security.md`, `.planning/phases/17-*/17-RESEARCH.md`.
- LOW: local visual contract inference; Phase 30–34 artifacts were not found.

## Metadata

Standard stack HIGH; architecture MEDIUM because upstream implementation status is unverified; pitfalls MEDIUM/LOW. Valid until 2026-08-15 pending Phase 34 execution evidence.
