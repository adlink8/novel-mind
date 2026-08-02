# Phase 30: Visual Bible - Context

**Scope authority:** Issue #29 (`https://github.com/adlink8/novel-mind/issues/29`)
**Status:** Research-only; no implementation or promotion decision.

## Decisions

### D-30-01 — Candidate-only visual authority
- Visual Bible revisions are immutable, versioned candidates until an explicit human approval action selects a revision for the owning novel.
- A generated image, extracted description, or user edit MUST NOT silently become Original Canon or an active visual pointer.

### D-30-02 — Evidence and interpretation are separate
- Every visual claim is labeled `canon_fact`, `probable_inference`, `literary_interpretation`, or `user_interpretation`.
- Canon claims require source snapshot, chapter/range, offsets/evidence refs, and cutoff; interpretation claims retain author and rationale.

### D-30-03 — Reusable IDs and scoped lineage
- Characters, places, factions, items, style profiles, constraints, and reference assets use stable IDs scoped by `owner_id`, `novel_id`, and visual-bible version.
- A revision records source snapshot, schema/prompt/model/config hashes where applicable, and parent revision; no in-place mutation of approved history.

### D-30-04 — Explicit human review
- Review actions are append-only and distinguish `approve`, `reject`, `edit`, `supersede`, and `needs_relink`.
- Approval is a candidate Artifact transition, not a canon mutation; unresolved evidence or rights/provenance issues remain visible.

## Agent Consumer Contract

- Skill / mode: build-visual-bible.
- Inputs: source evidence + world model + reference assets.
- Official output: VisualBibleArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: Web approval before accepted visual authority.
- Deterministic authority: evidence/rights/authority-label validator.
- Forbidden: Original Canon mutation or unsupported visual fact; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Internal table/model split, enum names, and pure compiler boundaries may follow existing SQLAlchemy/Pydantic conventions.
- The first slice may use deterministic fixtures and manually supplied reference metadata before any provider integration.
- UI placement may reuse the existing `/analysis` workspace patterns, provided review state and evidence are inspectable.

## Deferred Ideas (OUT OF SCOPE)

- Provider-specific image generation and consistency scoring belong to Phases 32–33.
- In-text placement, reader rendering, repair, Markdown/EPUB export belong to Phase 34.
- Original Canon/User Interpretation/Fanfiction Canon isolation beyond the visual-bible candidate boundary belongs to Phase 35–38.
- Active-pointer promotion, production A/B, or Phase 22 qualification are not unlocked by this research.

## Canonical References

- Issue #29: v1.3 Visual Bible, visual extraction, review/versioning, and explicit candidate workflow.
- `.planning/REQUIREMENTS.md`: `REQ-VIS-01`.
- `.planning/ROADMAP.md`: Phase 30 goal, plans, must-haves, and verification.
- `.planning/STATE.md`: Phase 22 remains blocked at 0/3 Nightly; later planning does not close that gate.
- `.planning/phases/12-read-only-asset-audit-and-eligibility/12-RESEARCH.md`: read-only asset audit, status/reason-code and evidence lineage analog.
- `backend/app/services/narrative_memory/`: immutable candidate, manifest, provenance, and source-link analogs.
- `backend/app/schemas/reader_chat.py` and `backend/app/models/reader_chat.py`: strict contracts, immutable manifest, citations, and reviewable durable-job analogs.
