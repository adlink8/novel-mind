# Phase 26 Context — Question-Driven Retrieval and Evidence

## Scope authority

Issue #29 is authoritative. Phase 22 remains BLOCKED / NOT_VERIFIED at 0/3 scheduled
Nightly greens; the explicit override unlocks planning only. Phase 25.2 Runtime and Phase
25.3 governance are required Agent foundations, not verified implementation. [VERIFIED: repository grep]

## Decisions

- D-01: Typed QueryPlan captures intent, entities, owner/novel/version scope, spoiler cutoff, dimensions, fallback, answer constraints, and a persisted trace.
- D-02: Deterministic schema validation; unknown/ambiguous intent clarifies, never guesses. Owner/novel/version/cutoff are mandatory.
- D-03: Representative reading/analysis questions become fixtures; scope escape, future probing, and contradictory constraints fail closed.
- D-04: Dimensions: raw text; events/causality; character state/goal/motivation/knowledge; relations; timeline; clues/foreshadowing; world rules/places/factions/items; NM chapter/arc/global, candidate-only and ADR-0002 labeled.
- D-05: Every dimension has availability semantics; missing/partial is never empty-success; provenance and deterministic fallback reason are recorded.
- D-06: Fusion/ranking is deterministic, single-source comparable, and changes with the dimension set.
- D-07: Candidates resolve to leaf/raw chapter, Unicode offsets, and content hash from a frozen snapshot; stale hashes reject.
- D-08: Manifest freezes before generation; citations only use leaf/raw evidence, never summaries, scores, routing metadata, or chat text.
- D-09: No uncited factual assertion; no-answer abstains with omitted/fallback records.
- D-10: Reader and Analysis Chat share the core, with selection and structure-range anchors.
- D-11: Phase 25.2 answer-reading-question Skill is agent orchestration boundary; deterministic services own legality.
- D-12: Both consumers use reading-progress cutoff; whole-book requires the per-novel switch.
- D-13: Execution starts only after Phase 22 three-green and passed Phase 25.2/25.3 verification; planning proceeds now.
- D-14: No NM promotion/active-pointer/consumer cutover, no summary replacing raw authority, no second agent retrieval stack.
- D-15: Each missing dimension follows one explicit fallback chain: exact/domain reader first; deterministic heuristic extraction second for candidate recall only; unresolved or insufficient coverage ends as a stable `partial`/`unavailable` reason. Heuristic output never becomes a fact, EvidenceRef or citation.
- D-16: Model-produced structured output may receive only declared alias repair, enum canonicalization or unambiguous container-shape normalization. The repaired payload is strictly validated; no repair may synthesize evidence, owner, cutoff, authority, branch, fork or approval fields. Unsafe repair blocks with stable warnings and preserves raw/repaired hashes and normalization actions.

## Phase 26 execution order

- 26-00 → 26-01 → 26-02 → 26-03 → 26-04 → 26-06 → 26-05.
- 26-06 is the shared structured-output integrity boundary consumed by the Phase 26 Skill; 26-05 may not introduce a local normalizer or bypass its strict post-repair validator.

## Agent Consumer Contract

- Skill / mode: answer-reading-question.
- Inputs: Question + Reader/Analysis anchor + source snapshot.
- Official output: CitedAnswerArtifact + Frozen Manifest, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: read-only; no approval.
- Deterministic authority: FastAPI evidence/citation/cutoff validator.
- Structured-output authority: shared conservative normalizer followed by strict schema/lineage validation; raw model output remains immutable audit evidence.
- Forbidden: uncited factual output or direct domain writes; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- QueryPlan field names/types within D-01.
- Deterministic fusion formula within D-06.
- Adapter internals.
- Trace/citation UI exposure.

## Deferred Ideas (OUT OF SCOPE)

- Active NM hierarchical retrieval: 999.x backlog with promotion authorization.
- Phase 27 world-model deep integration.
- Cross-novel retrieval.

## Canonical refs

- docs/architecture/03-data-model.md, 06-rag-pipeline.md, 09-frontend-architecture.md. [VERIFIED: repository grep]
- backend/app/services/knowledge_units/search.py; reader_chat/context.py, retrieval.py, conversations.py, worker.py; schemas/reader_chat.py. [VERIFIED: repository grep]
- backend/app/services/narrative_memory/candidate_reader.py, citations.py, manifests.py. [VERIFIED: repository grep]
- backend/tests/fixtures/narrative_memory/qualification/single_book_v1.json and backend/tests/adversarial/test_reader_chat_boundaries.py. [VERIFIED: repository grep]
- D:\ADLINK\Myproject\novel-mind-new\.claude\worktrees\issue29-agent-runtime\.planning\phases\26-question-driven-retrieval\26-{CONTEXT,RESEARCH,PATTERNS}.md, adapted to current facts.

Only research artifacts are authorized; no PLAN/SUMMARY/VERIFICATION.
