# Phase 21 Branch Delta Findings

## Investigation Trail

### 1. Histories are genuinely divergent

Patch-equivalence inspection reports 38 `master`-only commits and 41 branch-only commits.
Every branch-only commit is still reported by `git cherry -v`; therefore none may be
described as an equivalent merge.

### 2. Master contains newer authority contracts

`master` adds Phase 23–25.1 work that the old branch deletes or predates:

- Layer Registry and NU/NM boundary ADRs.
- Facet read-only projection contract.
- Relationship `intake_kind` provenance.
- Clue `short_title` and real `cost_usd` settlement.
- Chunk-index journal, reconcile gate and manifest binding.
- Server-side retrieval router with honest fallback.
- Analysis Chat chapter-range anchor and default chat workspace.

A wholesale merge would delete tests and implementation for several of these contracts,
including indexing journal/reconcile, retrieval-router fallback, facet read-only,
relationship provenance, clue cost and analysis-chat tests.

### 3. The old branch still contains product value

The following capabilities are absent or materially incomplete on `master`:

- selection bookmarks and startup embedding recovery;
- paged/scroll reader navigation, prefetching and progress throttling;
- reader illustration generation and prompt enrichment;
- one-click full-analysis orchestration and aggregate progress;
- NM resume/recovery hardening and richer reader evidence/context.

They are not safe to transplant because they are coupled to older migrations, API schemas,
settings, reader-chat and analysis-page shapes. They become inputs to the new roadmap:

| Branch capability | New owner |
|---|---|
| question/context enrichment and evidence stability | Phase 26 |
| NM recovery and whole-book orchestration | Phase 28 |
| one-click analysis progress/UAT | Phase 28–29 |
| image prompt enrichment and reader illustration | Phase 30–34 |
| bookmarks/reader navigation/performance | backlog item after Phase 29, unless required by illustration anchoring |

### 4. Planning files on the old branch are actively misleading

The branch removes the latest audit refresh and Phase 21 recognition artifacts while
rewriting `STATE`, `ROADMAP`, `PROJECT` and `REQUIREMENTS` around an older implementation
shape. Those files must never be copied to `master`.

## Risk Notes

- Cherry-picking migration commits can reintroduce divergent Alembic heads or delete the
  Phase 24 journal migrations.
- Cherry-picking frontend commits can remove the analysis-chat default view and its tests.
- Cherry-picking settings commits can restore a routing UI intentionally retired by the
  master governance work.
- Branch tests prove behavior only against the old branch contract; they are useful as
  acceptance-test references, not as merge proof.

## Result

The branch is archived as an evidence source. Selective reimplementation is required.
