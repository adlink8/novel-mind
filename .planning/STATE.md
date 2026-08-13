---
gsd_state_version: 1.0
milestone: v1.6
milestone_name: Provider Protocol Unification
status: in_progress
last_updated: "2026-08-12T17:00:00+08:00"
last_activity: 2026-08-12 -- Vertex-specific runtime removed; five-provider TDD and custom live connection verified
progress:
  total_phases: 1
  completed_phases: 0
  total_plans: 4
  completed_plans: 0
  percent: 0
---

# NovelMind Execution State

## Current Position

Phase: 46 - Provider Protocol Unification and Live Qualification
Plan: 1 of 4 (in progress)
Status: In progress; user authorized provider integration and chain testing
Last activity: 2026-08-12 -- Vertex removal slice verified; Phase 46-01 pagination/capability work remains

## Truth Snapshot

| Dimension | State | Evidence |
|---|---|---|
| implementation_readiness | **HIGH for 25.2–25.3** | Phase 21, 23–25.1 implemented; 25.2/25.3 verified; Phase 22 gaps active |
| sample_data_coverage | PARTIAL | existing product fixtures; Phase 26–29 gold sets not built |
| quality_qualification | BLOCKED | latest scheduled run failed; Nightly benchmark 0/3 green |
| provider_integration | **FIVE-PROVIDER FOUNDATION VERIFIED / PARTIAL LIVE** | provider, settings and Pi contracts pass; configured custom OpenCode direct test returned `OK`; remaining provider/Pi live matrix is not complete |

No single aggregate completion percentage is authoritative.

## Phase 46 Planning Snapshot (2026-08-12)

- Current dirty worktree foundation: five provider profiles and discovery adapters, SSRF-safe
  `/api/models/discover`, owner/run-bound Pi gateway deployment resolution, settings discovery
  and first-model default behavior. This is local implementation evidence, not a Phase 46
  completion claim.
- Vertex-specific provider profiles, native adapter, GCP settings, probes and live runners were
  removed from current runtime. Two inactive legacy Vertex model rows were deleted; historical
  archive/eval evidence remains unchanged.
- Remaining authority gap: `backend/app/services/ai_router.py` and several consumers still
  carry static/global routing behavior; Phase 46-02 owns their removal from runtime selection.
- Remaining protocol gap: pagination/capability normalization and strict provider/config
  validation are incomplete; Phase 46-01 owns them.
- Remaining qualification gap: no real cloud-provider credential matrix has run. Phase 46-03
  must record unavailable providers as BLOCKED/PARTIAL and must never commit credentials.
- Historical truth is preserved: Phase 41 remains NO-GO; Phase 42–45 summaries and Phase 45
  evidence exist but are not rewritten here; Phase 22 remains 0/3.

## Baseline Decisions

- `master` is the sole GSD execution baseline.
- `feat/phase21-debtfix` is an evidence source only; delta verdict is selective
  reimplementation.

- Phase 23–25.1 master contracts remain authoritative.
- NM promotion, active-pointer cutover and production A/B are deferred to `999.x` and
  require explicit authorization.

## Active Work

1. `22-G1`: verified locally — stable failure classification and reproducible entry points.
2. `22-G2`: deferred — remote control-plane path is verified, but operator-owned
   Runner/token setup and a real scheduled observation remain blocked.

3. `22-G3`: lifecycle logic verified locally; three scheduled green observations remain.
4. `25.2`: **IMPLEMENTED & VERIFIED (2026-08-02)** — Pi SDK spike, 7 domain tools,
   Skill/Artifact runtime, Agent Workspace, production agent service, DECISION.md GO.

5. `25.3`: **IMPLEMENTED & VERIFIED (2026-08-02)** — package lock/governance, registry
   collision gate, MCP isolation (ADOPT), approval policy/UX, renderer feasibility.

6. `26`: **IMPLEMENTED & VERIFIED (2026-08-02/03)** — QueryPlan contract + durable trace,
   dimension adapters/fusion, leaf EvidenceRef + frozen manifest, shared consumers,
   answer-reading-question skill integration, structured-output integrity.

7. `27`: **IMPLEMENTED & VERIFIED (2026-08-03)** — event fact/causal edge, character
   epistemic history, entity/rule/faction/place/item, four-label authority + disclosure,
   propose-world-model-candidates skill (tool registry 7→12).

8. `28`: **IMPLEMENTED & VERIFIED (2026-08-03)** — failure/recovery checkpoints, chapter
   terminal convergence + frozen source manifest, semantic arc/volume/global candidates,
   cross-dimension closure + one-click analysis, analyze-chapter/build-story-arc skills.
   Migration head `20260801_2801`; backend 1352p, agent-service 433p.

9. `29`: **IMPLEMENTED & VERIFIED (2026-08-03)** — eight-bucket gold set, bucket-level
   evaluation with parity runner, browser UAT contract/smoke, three-dimension v1.2 audit,
   evaluate-reading-skill-runs skill. Backend 1197p, agent-service 491p.

10. `30`: **IMPLEMENTED & VERIFIED (2026-08-03)** — Visual Bible candidate contract,
    evidence materialization + owner-scoped API, workspace UI, review/versioning envelope,
    build-visual-bible skill. Migration head `20260801_visual_bible`; backend 1212p,
    agent-service 543p, frontend 315p.

11. `31`: **IMPLEMENTED & VERIFIED (2026-08-03)** — key scene contract/boundaries, salience
    + diversity ranking, human review + frozen set, detect-key-scenes skill (tool 12→13).
    Migration head `20260801_key_scene`; backend 1355p, agent-service 597p, frontend 330p.

12. `32`: **IMPLEMENTED & VERIFIED (2026-08-03)** — SceneSpec/PromptRevision contract,
    evidence-to-spec compiler, provider prompt adapters, validation/preview, compile-scene-spec
    skill (8 skills). Migration head `20260801_prompt_review_events`; backend 1443p,
    agent-service 655p, frontend 348p.

13. `33`: **IMPLEMENTED & VERIFIED (2026-08-04)** — illustration job/asset/budget contract,
    mock generation + durable worker + storage, identity/style consistency scoring,
    review/compare/approval workflow, illustrate-scene skill (tool 13→14, 9 skills).
    Migration head `20260801_illustration_jobs`; backend 1485p, agent-service 714p,
    frontend 367p.

14. `34`: **IMPLEMENTED & VERIFIED (2026-08-04)** — hash-verified anchor contract,
    responsive reader presentation, anchor repair, Markdown/HTML/EPUB export,
    propose-illustration-anchor skill + deterministic publish (tool 14→16, 10 skills).
    Migration head `20260801_illustration_anchors`; backend 1607p, agent-service 776p,
    frontend 386p. **Completes v1.3 milestone (30–34).**

15. `35`: **IMPLEMENTED & VERIFIED (2026-08-04)** — triple-space contract, Canon Fork
    snapshot/cutoff, isolated retrieval/citations, negative contamination guards,
    create-canon-fork skill + deterministic materializer (tool 16→17, 11 skills).
    Migration head `20260801_canon_contamination04`; backend 1720p, agent-service 825p,
    frontend 386p.

16. `36`: **IMPLEMENTED & VERIFIED (2026-08-04)** — derivative project CRUD, chapter
    planning + Markdown editor, autosave CAS/history/diff/rollback, browser UAT + gate,
    edit-derivative-story skill + deterministic Revision Service (tool 17→18, 12 skills).
    Migration head `20260801_derivative_agent_edit01`; backend 1855p, agent-service 875p,
    frontend 404p.

17. `37`: **IMPLEMENTED & VERIFIED (2026-08-04)** — context package compiler, constrained
    draft generation, consistency gates + BranchSuggestion, explicit divergence override,
    continue-derivative-story skill (tool 18→20, 13 skills). Migration head
    `20260802_derivative_override01`; backend 2059p, agent-service 927p, frontend 404p.

18. `38`: **IMPLEMENTED & VERIFIED (2026-08-05)** — forked Visual Bible schema/lineage/
    immutable-original boundary, derivative Scene Spec compiler + 8 gates, candidate asset
    storage + cross-chapter consistency + PublishedDerivativeVisualAsset DTO/query, review
    seam + review UI panel, illustrate-derivative-scene skill + publish_derivative_visual
    action (tool 20→21, 14 skills). Migration head `20260802_derivative_asset01`; backend
    2157p, agent-service 974p, frontend 416p.

19. `39`: **IMPLEMENTED & VERIFIED (2026-08-05)** — derivative-only reproducible Markdown/
    EPUB export, bounded provenance package + three-dimension audit, export browser UAT
    panel, prepare-export skill + approve_export/materialize_export actions (tool 21→23,
    15 skills), independent Phase 39 audit gate (lineage + REQ-SHIP-01 baseline, no
    promotion path). Migration head `20260802_derivative_asset01`; backend 2229p,
    agent-service 1020p, frontend 429p. **Completes the v1.4 milestone (35–39).**

## Planning Portfolio

| Range | Planning status | Plans | Execution status |
|---|---|---|---:|
| Phase 25.2 | VERIFIED | 8 | ✅ 2026-08-02 (user execution override) |
| Phase 25.3 | VERIFIED | 7 | ✅ 2026-08-02 |
| Phase 26 | VERIFIED | 7 | ✅ 2026-08-02/03 (user execution override) |
| Phase 27 | VERIFIED | 5 | ✅ 2026-08-03 (user execution override) |
| Phase 28 | VERIFIED | 5 | ✅ 2026-08-03 (user execution override) |
| Phase 29 | VERIFIED | 5 | ✅ 2026-08-03 (user execution override) |
| Phase 30 | VERIFIED | 5 | ✅ 2026-08-03 (user execution override) |
| Phase 31 | VERIFIED | 4 | ✅ 2026-08-03 (user execution override) |
| Phase 32 | VERIFIED | 5 | ✅ 2026-08-03 (user execution override) |
| Phase 33 | VERIFIED | 5 | ✅ 2026-08-03/04 (user execution override) |
| Phase 34 | VERIFIED | 5 | ✅ 2026-08-04 (user execution override) |
| Phase 35 | VERIFIED | 5 | ✅ 2026-08-04 (user execution override) |
| Phase 36 | VERIFIED | 5 | ✅ 2026-08-04 (user execution override) |
| Phase 37 | VERIFIED | 5 | ✅ 2026-08-04 (user execution override) |
| Phase 38 | VERIFIED | 5 | ✅ 2026-08-05 (user execution override) |
| Phase 39 | VERIFIED | 5 | ✅ 2026-08-05 (user execution override) |
| Phase 46 | PLANNED | 4 | ⏸ execution not authorized; live credentials not supplied |

The earlier 55-plan structural verdict did not prove Issue #29 Agent-consumption coverage.
The corrected 86-plan portfolio must cover REQ-AGENT-01..08, AgentVN-derived candidate-only
heuristics/context/branch-suggestion contracts, and the per-phase Skill/Artifact map in
`.planning/AGENT-RUNTIME-CONTRACT.md`. No future SUMMARY or VERIFICATION placeholder is
created.

## Latest Evidence

- Branch divergence: master-only 38, old-branch-only 41, no patch-equivalent branch commit.
- `2026-07-31` schedule run `30607067442`: frontend unit failure; Nightly skipped.
- Runs `30330904855`, `30424693088`, `30515165945`: self-hosted Nightly runner unavailable,
  then cancelled.

- Open automated alert issues: #24, #25, #27, #28.
- PR #31 merged as `912ca6b`; this is the current master execution baseline.
- G2 local contracts: 126 CI tests passed; actionlint and Ruff passed.
- GitHub Runner inventory remains empty; operator setup is recorded in
  `22-G2-USER-SETUP.md`.

- Workflow-dispatch run `30623438107` emitted `nightly-control-report` and
  `nightly-rag-report`; provider job and promotion were correctly skipped.

- Canonical report: `blocked_dependency`, `quality_comparable=false`, `metrics=null`,
  `promotable=false`; alert issue #32 used root class `runner-or-environment-unavailable`.

- **2026-08-02 recovery**: master restored (PR #35 → `a904d64`, restore commit `2c4b4bb`).
- **2026-08-02 25.2 verification**: main.py mounts/gate script/models exports restored
  (`6988ceb`); `25.2-VERIFICATION.md` passed; agent_runtime integration 24p, CI gate 9p,
  contract 83p, adversarial 47p, unit 452p, agent-service 201p.

- **2026-08-02 25.3 verification**: 25.3-03 MCP isolation + 25.3-05 renderer delivered;
  `25.3-VERIFICATION.md` passed; backend 195p, agent-service 223p, frontend 281p, CI 37p.

- Known environment limitations: `test_openapi_contract.py` hangs under pytest (subprocess →
  litellm/tiktoken download); Next canary dev server fails to compile pages (e2e env-limited).

- **2026-08-02/03 Phase 26 verification**: 26-00 gate + 26-01..26-06 delivered;
  `26-VERIFICATION.md` passed (source_commit `cb071bc`); independent test sub-agent:
  backend 920p (unit 548 + integration/queryplan 68 + adversarial 129 + agent_runtime 60

  + ci 37 + contract 78), agent-service vitest 282p, tsc 0, alembic single head
  `20260801_2601`.

- **2026-08-03 Phase 27 verification**: 27-01..27-05 delivered; `27-VERIFICATION.md`
  passed (source_commit `0616920`); backend unit 622 + world_model 128 + adversarial 212

  + agent_runtime 76 + queryplan 164 + ci 37 + contract 118; agent-service vitest 331p,
  tsc 0, alembic single head `20260801_2703`.

- **2026-08-03 Phase 28 verification**: 28-01..28-05 delivered; `28-VERIFICATION.md`
  passed (source_commit `a7414c5`); backend 1352p (unit 650 + narrative_memory 157 +
  adversarial 223 + agent_runtime 96 + ci 37); agent-service vitest 433p, tsc 0, alembic
  single head `20260801_2801`.

- **2026-08-03 Phase 29 verification**: 29-01..29-05 delivered; `29-VERIFICATION.md`
  passed (source_commit `efa4f77`); backend 1197p (unit 683 + integration/qualification 90

  + adversarial 239 + agent_runtime 115 + ci 37); agent-service vitest 491p, tsc 0, alembic
  single head `20260801_2801` (no new migration). **Completes v1.2 milestone (26–29).**

- **2026-08-03 Phase 30 verification**: 30-01..30-05 delivered; `30-VERIFICATION.md`
  passed (source_commit `67908b1`); backend 1212p (unit 732 + visual_bible unit 49 +
  integration 22 + adversarial 239 + agent_runtime 133 + ci 37); agent-service vitest 543p,
  frontend vitest 315p, tsc 0, alembic single head `20260801_visual_bible`.

- **2026-08-03 Phase 31 verification**: 31-01..31-04 delivered; `31-VERIFICATION.md`
  passed (source_commit `fae6b68`); backend 1355p (unit 786 + key_scenes unit 54 +
  integration 26 + adversarial 245 + agent_runtime 151 + visual_bible 22 + ci 37);
  agent-service vitest 597p, frontend vitest 330p, tsc 0, alembic single head
  `20260801_key_scene`.

- **2026-08-03 Phase 32 verification**: 32-01..32-05 delivered; `32-VERIFICATION.md`
  passed (source_commit `ca06706`); backend 1443p (unit 875 + scene_spec unit 58 +
  integration 8 + prompt_compiler unit 31 + integration 17 + adversarial 245 +
  agent_runtime 172 + ci 37); agent-service vitest 655p, frontend vitest 348p, tsc 0,
  alembic single head `20260801_prompt_review_events`.

- **2026-08-04 Phase 33 verification**: 33-01..33-05 delivered; `33-VERIFICATION.md`
  passed (source_commit `1b8a658`); backend 1485p (unit 927 + illustrations unit 52 +
  integration 30 + adversarial 251 + agent_runtime 188 + ci 37); agent-service vitest 714p,
  frontend vitest 367p, tsc 0, alembic single head `20260801_illustration_jobs`.

- **2026-08-04 Phase 34 verification**: 34-01..34-05 delivered; `34-VERIFICATION.md`
  passed (source_commit `68819ac`); backend 1607p (unit 1002 + anchors unit 60 +
  integration 31 + export 24 + adversarial 251 + agent_runtime 202 + ci 37);
  agent-service vitest 776p, frontend vitest 386p, tsc 0, alembic single head
  `20260801_illustration_anchors`. **Completes v1.3 milestone (30–34).**

- **2026-08-04 Phase 35 verification**: 35-01..35-05 delivered; `35-VERIFICATION.md`
  passed (source_commit `5992c25`); backend 1720p (unit 1052 + canon_fork unit 50 +
  integration 43 + adversarial 329 + agent_runtime 219 + ci 37); agent-service vitest 825p,
  frontend vitest 386p, tsc 0, alembic single head `20260801_canon_contamination04`.

- **2026-08-04 Phase 36 verification**: 36-01..36-05 delivered; `36-VERIFICATION.md`
  passed (source_commit `a354a1e`); backend 1855p (unit 1063 + derivative suite 125 +
  adversarial 391 + agent_runtime 239 + ci 37); agent-service vitest 875p, frontend vitest
  404p, tsc 0, alembic single head `20260801_derivative_agent_edit01`.

- **2026-08-04 Phase 37 verification**: 37-01..37-05 delivered; `37-VERIFICATION.md`
  passed (source_commit `b8594e3`); backend 2059p (unit 1171 + derivative_generation 108 +
  integration 30 + adversarial 451 + agent_runtime 263 + ci 37); agent-service vitest 927p,
  frontend vitest 404p, tsc 0, alembic single head `20260802_derivative_override01`.

- **2026-08-05 Phase 38 verification**: 38-01..38-05 delivered; `38-VERIFICATION.md`
  passed (source_commit `fad8978`); backend 2157p (unit 1214 + derivative 10-file integration
  121 + adversarial 500 + agent_runtime 285 + ci 37); agent-service vitest 974p, frontend
  vitest 416p, tsc 0, alembic single head `20260802_derivative_asset01`. Two stale assertions
  (migration round-trip downgrade target; editor-gate no-publish scope vs approval-gated
  agent-tools action domain) fixed by lead at `fad8978` and re-verified.

- **2026-08-05 Phase 39 verification**: 39-01..39-05 delivered; `39-VERIFICATION.md`
  passed (source_commit `c21c9e0`); backend 2229p (unit 1258 + derivative export
  integration/security 76 + adversarial 550 + agent_runtime 308 + ci 37); agent-service
  vitest 1020p, frontend vitest 429p, tsc 0, alembic single head `20260802_derivative_asset01`.
  **Completes the v1.4 milestone (35–39).**

- **2026-08-03 stub-SUT finding (recorded, deferred)**: current nightly benchmark scores
  with deterministic stubs (`default_stub_*`, no live model calls); Ollama is only a health
  gate. Full G2 deployment (runner+Ollama+Docker) is premature for the stub pipeline.
  Decision: defer the lightweight unblock path (drop `ollama` label + `--live-health`, add
  `sut: "deterministic-stub"` marker, register plain Linux runner) until real model scoring
  is needed (Phase 29 / Pi-gateway live adapters). Full note in
  `22-RESEARCH.md` (Stub-SUT Nightly Finding).

## Execution Override (2026-08-02, user authorized)

- User explicitly authorized skipping the Phase 22 3/3 gate for Phase 25.2 implementation
  (2026-08-02, interactive confirmation "授权跳过门禁"). This is an EXECUTION override —
  distinct from and superseding the 2026-07-31 planning-only override.

- Scope: Phase 25.2-01 Pi SDK spike proceeds without Phase 22 real scheduled green evidence.
- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 25.2-03/04/05 and Phase 26 still require passed upstream verification artifacts per
  ROADMAP contracts; this override does not waive those.

- The fail-closed gate scripts (25.2-00/25.3-00) are still created and tested; their
  "blocked repo exits non-zero" behavior is preserved, with this override recorded as the
  authorized execution bypass.

## Execution Override — Phase 26 (2026-08-02, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 26 implementation (2026-08-02,
  interactive confirmation "授权跳过 gate"). Scope: Phase 26-01..06 (QueryPlan contract,
  retrieval adapters, EvidenceRef materialization, consumers, Agent integration,
  structured-output integrity) proceed without Phase 22 real scheduled green evidence.

- `scripts/check_phase_execution_gate.py` (26-00) is built and tested; its current-repo
  blocked (Phase 22 0/3) non-zero behavior is preserved as the fail-closed default.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 27+ still require passed Phase 26 verification artifacts per ROADMAP contracts;
  this override does not waive those.

- Upstream verification artifacts still required: passed 25.1/25.2/25.3 (all exist) and
  each Phase 26 plan's own SUMMARY/VERIFICATION when complete.

## Execution Override — Phase 27 (2026-08-03, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 27 implementation (2026-08-03,
  interactive confirmation "授权跳过门禁"). Scope: Phase 27-01..05 (shared event fact and
  causal edge, character state/goal/motivation/knowledge, world entity/rule/faction/place/
  item, POV/disclosure/epistemic authority, Agent integration) proceed without Phase 22
  real scheduled green evidence.

- `scripts/check_phase_execution_gate.py` (26-00) remains the fail-closed default; its
  current-repo blocked (Phase 22 0/3) non-zero behavior is preserved.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 28+ still require a passed Phase 27 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 28 (2026-08-03, user authorized)

- User authorized continuing phase execution and skipping the Phase 22 3/3 gate for Phase 28
  (2026-08-03, "可以多子代理进行 不设限"). Scope: Phase 28-01..05 (failure classification and
  recovery, chapter state terminal convergence, semantic arc/volume/global, cross-dimension
  closure, Agent integration) proceed without Phase 22 real scheduled green evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 29+ still require a passed Phase 28 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 29 (2026-08-03, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 29 implementation (2026-08-03,
  interactive confirmation "授权跳过门禁"). Scope: Phase 29-01..05 (reading QA gold set,
  retrieval/citation/answer evaluation, browser UAT, v1.2 audit, Agent integration) proceed
  without Phase 22 real scheduled green evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 30+ still require a passed Phase 29 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 30 (2026-08-03, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 30 implementation (2026-08-03,
  interactive confirmation "授权跳过门禁"). Scope: Phase 30-01..05 (visual entity schema and
  evidence, character/place/item sheets, style/negative constraints, Visual Bible review/
  versioning, Agent integration) proceed without Phase 22 real scheduled green evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 31+ still require a passed Phase 30 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 31 (2026-08-03, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 31 implementation (2026-08-03,
  interactive confirmation "授权跳过门禁"). Scope: Phase 31-01..04 (scene boundary/candidate
  contract, narrative salience and diversity ranking, human review and frozen key-scene set,
  Agent integration) proceed without Phase 22 real scheduled green evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 32+ still require a passed Phase 31 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 32 (2026-08-03, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 32 implementation (2026-08-03,
  interactive confirmation "授权跳过门禁"). Scope: Phase 32-01..05 (Scene Spec schema,
  evidence-to-spec compiler, provider prompt adapters, validation/safety/prompt preview,
  Agent integration) proceed without Phase 22 real scheduled green evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 33+ still require a passed Phase 32 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 33 (2026-08-03, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 33 implementation (2026-08-03,
  interactive confirmation "授权跳过门禁"). Scope: Phase 33-01..05 (provider/job/budget
  contract, generation and asset storage, identity/style consistency scoring, retry/compare/
  approval workflow, Agent integration) proceed without Phase 22 real scheduled green
  evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 34+ still require a passed Phase 33 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 34 (2026-08-04, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 34 implementation (2026-08-04,
  interactive confirmation "授权跳过门禁"). Scope: Phase 34-01..05 (source anchor/version
  contract, responsive reader presentation, anchor repair after text/version changes,
  Markdown/EPUB export and UAT, Agent integration) proceed without Phase 22 real scheduled
  green evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- Phase 35+ still require a passed Phase 34 verification artifact per ROADMAP contracts;
  this override does not waive those.

## Execution Override — Phase 35–39 (2026-08-04, user authorized)

- User authorized skipping the Phase 22 3/3 gate for Phase 35..39 implementation
  (2026-08-04, interactive confirmation "授权跳过门禁"). Scope: Phase 35-01..05 (triple
  knowledge spaces and Canon Fork), 36-01..05 (derivative project and editor), 37-01..05
  (constrained generation), 38-01..05 (derivative visual consistency), 39-01..05
  (derivative export, UAT and audit) all proceed without Phase 22 real scheduled green
  evidence.

- Phase 22 verdict is NOT changed: remains blocked at 0/3 and must not be marked complete.
- v1.4 milestone (35–39) completes the roadmap; no further phase execution gates apply.

## Execution Cursor

Phase 46 is the active **planning** cursor (0/4). No Phase 46 execution, real cloud call,
credential use, commit or push is authorized by this state update.

Phase 22 execution is paused. Its recovery entry remains:

`.planning/phases/22-ci-nightly-gap-closure/22-G2-PLAN.md`

Phase 26–39 planning is complete, including the AgentVN planning integration recorded on
2026-08-02. Phase 25.2, Phase 25.3, Phase 26, Phase 27 and Phase 28 are implemented and
verified under the 2026-08-02/03 execution overrides (28-VERIFICATION passed at `a7414c5`).
Phase 29 execution is authorized by the 2026-08-03 Phase 29 override, and Phase 29 is
now verified (29-VERIFICATION passed at `efa4f77`), completing the v1.2 milestone (26–29).
Phase 30 execution is authorized by the 2026-08-03 Phase 30 override, and Phase 30 is now
verified (30-VERIFICATION passed at `67908b1`).
Phase 31 execution is authorized by the 2026-08-03 Phase 31 override, and Phase 31 is now
verified (31-VERIFICATION passed at `fae6b68`).
Phase 32 execution is authorized by the 2026-08-03 Phase 32 override, and Phase 32 is now
verified (32-VERIFICATION passed at `ca06706`).
Phase 33 execution is authorized by the 2026-08-03 Phase 33 override, and Phase 33 is now
verified (33-VERIFICATION passed at `1b8a658`).
Phase 34 execution is authorized by the 2026-08-04 Phase 34 override, and Phase 34 is now
verified (34-VERIFICATION passed at `68819ac`), completing the v1.3 milestone (30–34).
Phase 35 execution is authorized by the 2026-08-04 Phase 35–39 override, and Phase 35 is
now verified (35-VERIFICATION passed at `5992c25`). Phase 36 is now verified
(36-VERIFICATION passed at `a354a1e`). Phase 37 is now verified (37-VERIFICATION passed at
`b8594e3`). Phase 38 is now verified (38-VERIFICATION passed at `fad8978`). Phase 39 is now
verified (39-VERIFICATION passed at `c21c9e0`), **completing the v1.4 milestone (35–39)
and the full roadmap through Phase 39**.
Phase 22 remains 0/3 (not satisfied, verdict unchanged).
If Phase 22 is resumed, use `$gsd-resume-work`; do not mark it complete early.

## Roadmap

- Agent runtime foundation: Phase 25.2–25.3.
- v1.2 trusted understanding: Phase 26–29.
- v1.3 Visual Bible, key scenes and illustrations: Phase 30–34.
- v1.4 Canon Fork and constrained derivatives: Phase 35–39 (**COMPLETE 2026-08-05**).
- v1.5 Windows desktop runtime: Phase 41 NO-GO preserved; Phase 42–45 execution/evidence
  artifacts exist under override and retain their documented release boundaries.
- v1.6 provider protocol unification: Phase 46 (**PLANNED 0/4**).

See `.planning/ROADMAP.md`.

## 2026-08-05 追加：Phase 40 chat_backfill（用户扩展，非 roadmap 规划阶段）

用户确认扩展（2026-08-05）：问答证据不足时后台按需触发分析 skill，结果物化到
域表 candidate，下一轮检索可见。原 D-13（v1 无子代理）与 D-15（证据不足
fail-closed）被显式放宽为「单 Agent 按需执行 skill（不引入子代理）+ 物化只写
candidate（不自动 promotion）」。

已实现（snapshot: master @ 8ba59d3 + 8b23786）：

- 触发：worker abstain 后按 QueryDimension 映射触发（每次最多 2 skill），
  SkillRun 增 origin/backfill_dimension/user_message_id + 部分唯一索引

- poller 端点：GET /queued-runs + POST /claim（gateway token 认证）
- agent-service poller.ts：轮询/claim/执行/finalize（并发上限 + lease reclaim）
- materialize.py：finalize 后 background task 记录物化（digest 类型诚实 skipped）
- 前端：MessageView.backfill_runs + analysis-chat-panel「后台分析中」chip

边界：candidate → available/published 仍需现有用户审批（无自动 promotion）；
timeline/clues/relationships 自动可见属另一条确定性 worker 链路，未纳入本轮。
Phase 22 仍 0/3（verdict 不变）。

## 2026-08-05 追加：Phase 40 第二层物化闭环完成（master @ 4b1adb5）

用户确认扩展「物化+检索接线（完整闭环）+ 尽力物化诚实 skip」。已实现：

- 物化升级：materializers.py dispatcher → 三域函数（key_scenes via
  CandidateService.import_set / world_model via EpistemicGate+append_projection /
  visual_bible via EvidenceService+create_revision），全部 candidate-only

- version_id/snapshot_hash 从 chat 同源权威解析（materialize_helpers +
  build_source_snapshot undefer content）

- 检索接线：reader_chat.fetch_knowledge_evidence（三域 candidate 行 →
  RetrievedEvidence，candidate:True 标记）+ queryplan world_projection
  resolver（opt-in dimensions 参数，run_reader_queryplan /
  AnalysisQueryPlanAdapter 补跑 adapter）

边界：物化只写域表 candidate；candidate → available/published 仍需现有
用户审批（无自动 promotion）；digest 类型诚实 skipped。测试：backend 101p +
agent_runtime 61p + agent-service 1028p 全绿；alembic check clean。

## 2026-08-06 追加：Agent 自动路由闭环 + 统一 AI 助手（master @ 8c21c5c）

用户确认产品形态（2026-08-06）：**小说主导 + 阅读侧边栏 / 分析页双入口 AI 助手**；
交互路径不暴露 skill 选择——用户提问，Agent 自动按 intent 路由到对应 skill
（第二步「记忆 / 自定义 skill」暂缓）。

已实现（snapshot: master @ 8c21c5c）：

- **契约恢复**：AGENT-RUNTIME-CONTRACT「The Agent selects versioned Skills」——
  用户不再选 skill；SSE run body.skill 缺省 → 服务端 `skill_router.py` 按问题关键词
  启发式路由（画图/关系/性格/关键场景/续写 5 类意图，无命中回退
  answer-reading-question），显式 body.skill 仅高级覆盖

- `POST /api/agent/novels/{id}/route-skill`：按 owner+novel 过滤 active skill，
  返回 `{skills, primary, question_hash, input_anchor}`

- 锚点自动补全：生图 skill 从最新**已批准** PromptRevision 血缘自动解析
  prompt_revision_id / visual_bible_version_id / scene_spec_revision_id /
  source_snapshot_id / job_key（血缘不完整诚实失败，不伪造锚）

- 前端统一 AI 助手：分析页 AnalysisUnifiedChat（取代 chat/agent 双 tab）
  + 阅读页侧边栏 AI 助手（对话 / 选区画图 / 续写入口）；前端不暴露 skill
- 真实生图：腾讯混元 hunyuan-image via ZCodeProxy（illustration_provider=mock|hunyuan）
- 书签（reader_bookmarks 移植）+ 插图 asset URL 双重 /api 前缀修复 + 端口固化
  （后端 8010 / 前端 3005 / agent 3100 / ZCodeProxy 3001）

- 端到端验证通过：自动路由 5 意图 + SSE 自动执行 + 真实生图（asset 281KB JPEG）
  + 锚点 + 前端 200

部署注意：agent-service 启动必须注入
`NOVELMIND_GATEWAY_TOKEN=dev-agent-gateway-token-local`（backend/.env:39 定义；
Makefile `dev-agent` / `scripts/keep-alive.ps1` 已注入）。
测试：backend 1305p + agent-service 1039p + frontend 460p 全绿。
Phase 22 仍 0/3（verdict 不变）。
