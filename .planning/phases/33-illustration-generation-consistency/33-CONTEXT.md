# Phase 33: Illustration Generation and Consistency - Context

**Scope authority:** Issue #29 (`https://github.com/adlink8/novel-mind/issues/29`)

## Decisions

### D-33-01 — Durable candidate Artifact jobs
- Each generation request is an idempotent durable job keyed by owner/novel/SceneSpec/prompt/model/config lineage.
- Jobs have explicit terminal and paused/failure states; provider failures never become successful empty assets.

### D-33-02 — Budget and cost are first-class
- Reserve and settle call/token/cost budgets using a price snapshot; unknown usage/cost remains explicit.
- Retry policy is bounded, reason-coded, and cannot bypass budget or duplicate a successful attempt.

### D-33-03 — Immutable asset revisions
- Provider outputs are immutable asset revisions with content hash, MIME/dimensions, prompt/spec/model lineage, provider request ID, and provenance/rights status.
- Generated output is `candidate` until explicit human approval; it cannot enter reader/export automatically.

### D-33-04 — Consistency is evidence, not canon
- Identity/style consistency scores are review signals with model/version/fixture lineage, not automatic canon claims.

## Agent Consumer Contract

- Skill / mode: illustrate-scene.
- Inputs: SceneSpecArtifact + PromptArtifact + VisualBibleArtifact.
- Official output: IllustrationRevision, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: reviewed/proposal_ready only; publish moves to Phase 34.
- Deterministic authority: budget/rights/fidelity/consistency validator.
- Forbidden: published asset or anchor creation; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Job table split, lease fields, retry reason codes, and mock provider interface may follow Reader Chat durable-job patterns.
- Initial consistency checker may be deterministic/fixture-driven and may report `unavailable` when no evaluator is configured.

## Deferred Ideas (OUT OF SCOPE)

- Reader anchors, responsive display, repair, Markdown/EPUB export: Phase 34.
- Derivative visual namespace: Phase 38.
- Production A/B, active-pointer cutover, and Phase 22 Nightly qualification.

## Canonical References

- Issue #29; `.planning/REQUIREMENTS.md` `REQ-VIS-04`; `.planning/ROADMAP.md` Phase 33.
- `backend/app/models/reader_chat.py`, `services/reader_chat/worker.py`, `budget.py`, `gateway.py`: durable job, lease, attempt, budget, provider and failure analog.
- `backend/app/services/ai_router.py`, `ai_service.py`, `vertex_gemini.py`: provider routing/cost analog.
- `backend/app/services/narrative_memory/qualification_*`: fixture-based quality/consistency qualification analog.
