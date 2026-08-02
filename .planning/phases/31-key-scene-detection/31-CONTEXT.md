# Phase 31: Key Scene Detection - Context

**Scope authority:** Issue #29 (`https://github.com/adlink8/novel-mind/issues/29`)

## Decisions

### D-31-01 — Candidate, not canon
- Scene candidates are derived artifacts only; selection never changes source text, canon, or active reader state.

### D-31-02 — Evidence-first scene identity
- Every candidate stores chapter/range, source hash, cast, place, time, POV, spoiler cutoff, salience reasons, diversity key, and detector lineage.

### D-31-03 — Multi-signal ranking
- Ranking MUST combine narrative salience and diversity/coverage; embedding similarity alone is insufficient.
- Reasons remain inspectable and deterministic scoring inputs are versioned.

### D-31-04 — Explicit human set
- Human review creates an append-only decision and a frozen key-scene set; rejected candidates remain auditable.

## Agent Consumer Contract

- Skill / mode: detect-key-scenes.
- Inputs: validated events/world model + VisualBibleArtifact.
- Official output: SceneCandidateArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: user selection/review.
- Deterministic authority: score/diversity/density/spoiler validator.
- Forbidden: source mutation or unreviewed publication; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Scoring weights, reason-code vocabulary, and deduplication algorithm may be chosen after fixture comparison.
- Candidate generation may use existing chapter/scene/evidence hierarchy before adding semantic extraction.
- Review UI may reuse Analysis workspace patterns.

## Deferred Ideas (OUT OF SCOPE)

- Prompt compilation and provider adapters: Phase 32.
- Image generation, cost, consistency: Phase 33.
- Reader anchors and export: Phase 34.
- Phase 22 Nightly qualification or promotion: not affected.

## Canonical References

- Issue #29; `.planning/REQUIREMENTS.md` `REQ-VIS-02`; `.planning/ROADMAP.md` Phase 31.
- `backend/app/services/chunking/` for chapter→scene→evidence source ranges.
- `backend/app/services/narrative_memory/` for candidate/manifests/lineage.
- `backend/app/services/reader_chat/retrieval.py` and `schemas/reader_chat.py` for cutoff/evidence packaging.
- `frontend/src/components/structure/` and `frontend/src/components/reader/` for workspace/reader review analogs.
