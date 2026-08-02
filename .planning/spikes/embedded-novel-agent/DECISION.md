# Pi SDK Spike Decision — Embedded Novel Agent Runtime

**Phase:** 25.2-06 | **Date:** 2026-08-02 | **Source evidence:** EXPERIMENTS.md + FINDINGS.md (25.2-01)

## Verdict: GO

All required capabilities PASS. Two live-turn assertions are SKIP-with-reason (no dev
provider key in this environment, D-15 spike exception not satisfied) — neither is a
required capability failure, and both are re-verified with a mocked provider in 25.2-03.

| # | Required capability | Verdict | Evidence |
|---|---|---|---|
| 1 | No default coding tools (D-05) | **PASS** | EXPERIMENTS #1: 0 exposed tools with `noTools:"all"` + empty allowlist; activation rejected |
| 2 | Custom domain tool roundtrip (D-06) | **PASS** | EXPERIMENTS #2: register + TypeBox typed params + round trip |
| 3 | ResourceLoader closure (D-18 / A2) | **PASS** | EXPERIMENTS #3: every surface = allowlist exactly; A2 = PROVEN |
| 4 | Session lifecycle (D-19) | **PASS** | EXPERIMENTS #4: create/abort-safe/resume API shape (live stopReason SKIP) |
| 5 | Streaming events (SSE subset) | **PASS** | EXPERIMENTS #5: subscribe/unsubscribe + SSE frame mapping (live capture SKIP) |
| 6 | Storage seam (A1) + fallback | **PASS** | EXPERIMENTS #6: sqlite round trip + **A1 = NO SEAM** + fallback proof |
| 7 | Skill instruction injection (Pitfall 3) | **PASS** | EXPERIMENTS #7: systemPrompt/customMessage/steer carry instructions, no read tool |

## Decision Record

- **Go/no-go: GO.** No skipped or failed required capability; no open storage or session
  assumption blocks production plans.
- **SessionStorage verdict: NO SEAM.** `createAgentSession` (Pi 0.83.0) accepts
  `SessionManager` only; no custom `SessionStorage` injection point in the public surface.
- **Selected fallback (D-11/D-12):** in-memory Pi session per run + NovelMind-owned run
  state (`skill_runs`-style). `agent_session_entries` PG table NOT required for 25.2-03.
- **SQLite layer:** proven usable at the storage layer (EXPERIMENTS #6a) but NOT required;
  defer any Pi-native durability decision to a later phase.
- **Unresolved blockers:** none. Live provider-turn verification (stopReason/cancel/
  streaming capture) is deferred to 25.2-03 with a mocked provider — no-go is not implied.

## Pins (frozen)

`@earendil-works/pi-coding-agent` / `pi-agent-core` / `pi-ai` / `pi-storage-sqlite-node`
all `0.83.0`; `typebox 1.3.7`; dev `typescript 5.9.3`, `vitest 4.1.10`.

## Handoff to 25.2-02 / 25.2-03

- 25.2-02 consumes: canonical session shape (`noTools:"all"` + `tools:[domain]` +
  `customTools`), 5-arg `execute(toolCallId, params, signal, onUpdate, ctx)`.
- 25.2-03 consumes: NO SEAM fallback (memory session + NovelMind run state), skill
  instructions MUST be injected (never discovered), ResourceLoader closure recipe.
