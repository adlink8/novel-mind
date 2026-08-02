# Phase 34: Illustration Anchors, Reader and Export - Context

**Scope authority:** Issue #29 (`https://github.com/adlink8/novel-mind/issues/29`)

## Decisions

### D-34-01 — Stable source anchor
- An IllustrationAnchorProposal is tied to owner/novel/chapter/source snapshot,
  paragraph/range coordinates, anchor hash, and proposal-ready AssetRevision ID; only the
  Phase 34 approval/publisher transaction creates a published asset plus valid anchor.
- Offset/hash mismatch makes the anchor stale; it must not silently move to a nearby paragraph.

### D-34-02 — Reader-safe presentation
- Approved illustrations are inline content with accessible captions/alt text, lazy loading, missing-asset fallback, and no overlap with input/progress/navigation controls.
- Desktop, mobile 390px, scroll/long-page, and paged reading are supported by explicit tests.

### D-34-03 — Repair is explicit
- Text/version changes produce `valid`, `needs_repair`, or `invalid` anchor status; repair proposes a new candidate anchor and requires review.

### D-34-04 — Export parity
- Markdown/HTML/EPUB export uses a frozen manifest of text version, approved assets, anchors, captions, citations, and hashes.
- Missing assets fail gracefully with an explicit placeholder/report; export never invents a URL or silently drops provenance.

## Agent Consumer Contract

- Skill / mode: propose-illustration-anchor.
- Inputs: proposal-ready IllustrationRevision + hash-verified source span.
- Official output: IllustrationAnchorProposal, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: publish/attach require Web approval.
- Deterministic authority: anchor/source/version validator + deterministic publisher.
- Forbidden: Agent or browser publication; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Anchor granularity (paragraph, evidence range, or both), export library, and browser component split may follow existing chapter/selection coordinate patterns.
- Initial export may be deterministic HTML/Markdown plus a documented EPUB adapter seam if no EPUB library is already present.

## Deferred Ideas (OUT OF SCOPE)

- New generation/provider/consistency work: Phase 33.
- Derivative export/asset namespaces: Phase 39.
- Bookmarks/navigation/performance unrelated to illustration anchors; Phase 22 qualification.

## Canonical References

- Issue #29; `.planning/REQUIREMENTS.md` `REQ-VIS-05`; `.planning/ROADMAP.md` Phase 34.
- `frontend/src/components/reader/reader-content.tsx` and `frontend/src/lib/reader-selection.ts`: pagination, UTF-16/code-point conversion, progress, citation highlight.
- `backend/app/schemas/reader_chat.py` and `models/reader_chat.py`: immutable range/hash/evidence manifest analog.
- `backend/app/services/novel_service.py`: file containment/quarantine analog.
- `docs/architecture/09-frontend-architecture.md`, `10-testing-ci.md`: reader and browser validation baseline.
