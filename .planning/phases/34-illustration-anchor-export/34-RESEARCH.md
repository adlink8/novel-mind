# Phase 34: Illustration Anchors, Reader and Export - Research

**Researched:** 2026-08-01
**Domain:** Hash-verified inline assets and export parity
**Confidence:** MEDIUM

## Summary

Issue #29 requires illustration anchors, paragraph insertion/lazy loading, review/replacement/relocation/stale detection, and HTML/EPUB export. [CITED: https://github.com/adlink8/novel-mind/issues/29] The roadmap adds no overlap with progress/input, accessible captions, anchor hash verification, graceful missing assets, and export parity. [CITED: `.planning/ROADMAP.md` Phase 34]

The current reader already maintains chapter content, paged/scroll modes, UTF-16 to code-point conversion, progress restoration, citation highlight, and selection coordinates. [VERIFIED: `frontend/src/components/reader/reader-content.tsx`; `frontend/src/lib/reader-selection.ts`] Reader Chat stores immutable source offsets and chapter content hashes. [VERIFIED: `backend/app/models/reader_chat.py`] There is no existing illustration anchor or EPUB export implementation. [VERIFIED: codebase grep]

**Primary recommendation:** bind proposal-ready AssetRevisions to immutable source ranges,
then use one server-authoritative approval and deterministic transaction to publish the asset
and valid anchor; reader/export consume only that published result. [ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Anchor/version/repair state | Database / Storage | API / Backend | stale detection and audit require durable source hashes. [ASSUMED] |
| Reader insertion/lazy loading | Browser / Client | CDN / Static | layout, focus, responsive behavior are client concerns. [VERIFIED: frontend architecture] |
| Export manifest and asset packaging | API / Backend | CDN / Static | server creates reproducible package; bytes are delivered as package members. [ASSUMED] |

## User Constraints

- Honor `D-34-01` through `D-34-04` in `34-CONTEXT.md`.
- Use only Phase 33 `proposal_ready` AssetRevisions as proposals; no direct provider call,
  hidden generation or pre-approval reader/export consumption.
- Phase 22 remains 0/3 and blocked. [CITED: `.planning/STATE.md`]

## Standard Stack

| Component | Version | Use | Provenance |
|---|---|---|---|
| React/Next.js/TypeScript | React `19.2.7`, Next `16.3.0-canary.6` | reader inline blocks and responsive behavior | [VERIFIED: `frontend/package.json`] |
| Existing reader selection utilities | current | exact coordinate conversion/hash semantics | [VERIFIED: codebase grep] |
| FastAPI/Pydantic/SQLAlchemy | requirements ranges | anchor API/export manifest/persistence | [VERIFIED: `backend/requirements.txt`] |
| Playwright/Vitest/pytest | current scripts | browser/unit/integration validation | [VERIFIED: project docs/manifests] |

No EPUB package is currently present in the frontend/backend manifests. [VERIFIED: package/requirements grep] Select and verify one only during implementation planning; do not recommend an unverified package here. [ASSUMED]

## Architecture Patterns

- Store anchor `chapter_id`, source start/end, excerpt/content hash, source snapshot/version, asset revision, caption/alt, status, and review lineage. [ASSUMED]
- Validate anchor against current chapter content before render/export; mismatch yields stale/needs_repair, not nearest-match auto-relocation. [ASSUMED]
- Build one immutable export manifest consumed by Markdown/HTML/EPUB adapters; include text, anchor, asset, caption, citation, and hash entries. [ASSUMED]
- In ReaderContent, treat illustration as a sibling block around the text node, preserving selection and progress calculations; do not inject HTML into the text string. [VERIFIED: reader component structure; ASSUMED implementation]
- Reuse `data-source-start-utf16`/code-point mapping and existing page reset/highlight patterns for anchor navigation. [VERIFIED: codebase grep]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---|---|---|
| Unicode/offset conversion | new index math | `reader-selection.ts` helpers |
| asset path safety | string concatenation | `novel_service.py` realpath/commonpath/quarantine pattern |
| export consistency | per-format independent queries | frozen export manifest |
| accessibility | image-only decoration | semantic figure/figcaption, alt/caption contract and browser checks |

## Common Pitfalls

- **Offset drift:** text edits invalidate stored offsets; hash check must precede display and export. [CITED: `REQ-VIS-05`; verified Reader Chat hash analog]
- **Pagination mismatch:** one illustration may split or overlap a page; page model needs explicit block handling and progress semantics. [VERIFIED: `reader-content.tsx` pagination]
- **Progress/input overlap:** floating reader controls can cover captions or images on 390px; use layout flow and browser assertions. [CITED: `.planning/ROADMAP.md` Phase 34]
- **Missing binary treated as success:** retain placeholder and manifest error instead of broken URL or silent omission. [ASSUMED]
- **Export drift:** HTML preview and EPUB may select different asset revision/caption; both must consume one frozen manifest. [ASSUMED]

## Code Examples

```typescript
const anchorIsCurrent =
  sha256Hex(codePointSlice(chapter.content, anchor.sourceStart, anchor.sourceEnd)) ===
  anchor.excerptHash;
if (!anchorIsCurrent) return <IllustrationPlaceholder status="needs_repair" />;
```

The coordinate conversion/hash concept follows existing `reader-selection.ts` and Reader Chat selection persistence. [VERIFIED: codebase grep; exact component is proposed]

## Validation Architecture

| Req | Behavior | Type | Command | File |
|---|---|---|---|---|
| REQ-VIS-05 | valid anchor renders only approved asset at matching hash | unit/integration | `pytest tests/integration/illustration_anchors -q` | Wave 0 |
| REQ-VIS-05 | changed text marks stale and requires repair | adversarial | `pytest tests/unit/illustration_anchors/test_repair.py -q` | Wave 0 |
| REQ-VIS-05 | reader paged/scroll desktop/390px has no overlap and accessible caption | browser | `npm run test:e2e -- illustration-anchors` | Wave 0 |
| REQ-VIS-05 | export manifest and HTML/EPUB asset/citation/version parity | integration/manual | `pytest tests/integration/export/test_illustrations.py -q` | Wave 0 |

### Wave 0 Gaps

- Unicode/paragraph anchor fixture with changed text and changed normalization;
- approved/missing/stale asset fixture;
- export manifest golden fixture and EPUB inspection fixture;
- mobile/long-page/page-mode Playwright cases.

## Security Domain

V4 owner/novel/asset access, V5 anchor/range/MIME validation, V6 content hashes, and safe file containment apply. [VERIFIED: codebase security analogs]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python backend environment | anchor/export manifest | ✓ by repository setup | requirements-managed | deterministic fixtures |
| PostgreSQL 16 | anchor/revision metadata | project-supported | 16 baseline | test DB/contract fixtures |
| Node/npm + Playwright | reader responsive UAT | ✓ by manifest/scripts | manifest-managed | Vitest + manual inspection |
| EPUB library | EPUB adapter | not present in current manifests | — | plan a verified dependency or emit deterministic HTML/Markdown first |

No export dependency was installed and no implementation test was run during research. [VERIFIED: manifests/docs; ASSUMED runtime availability]

## Sources

- HIGH: Issue #29; requirements/roadmap/state; reader-content/reader-selection; Reader Chat models; novel storage security.
- MEDIUM: architecture docs 03/09/10.

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Anchor should use exact source range plus hash rather than paragraph identity only. | Schema could be more granular than planned. |
| A2 | One frozen manifest can serve Markdown/HTML/EPUB. | Format-specific packaging may need additional derived metadata. |
| A3 | No existing EPUB library is available. | A dependency may already be introduced outside current manifests. |

## Open Questions (RESOLVED)

1. Export is server-side in the FastAPI exporter. `html.py` is the explicit HTML adapter consumed from the frozen manifest; `epub.py` uses fixed EPUB3 packaging with standard-library/现有能力 zip/XML checks, adds no production dependency, and validates manifest/package/resource parity.
2. An anchor whose surrounding text changed is `needs_repair` even when the excerpt exists elsewhere; repair remains an explicit candidate/approval event and never silently relocates.
