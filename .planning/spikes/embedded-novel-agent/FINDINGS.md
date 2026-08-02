# Spike Findings — Embedded Novel Agent Runtime

**Phase:** 25.2-01 | **Date:** 2026-08-02 | **Node:** v22.22.2 | **Pi SDK:** 0.83.0

## Verdict Summary

| # | Capability | Verdict |
|---|---|---|
| 1 | No default coding tools (D-05) | **PASS** — 10/10 |
| 2 | Custom tool roundtrip + typed params (D-06) | **PASS** — 8/8 |
| 3 | ResourceLoader allowlist closure (D-18 / A2) | **PASS** — 9/9, A2 = PROVEN |
| 4 | Session lifecycle (D-19) | **PASS** — 7/7 (+2 SKIP live-turn, no provider key) |
| 5 | Streaming events (SSE subset) | **PASS** — 6/7 (+1 SKIP live capture) |
| 6 | Storage seam (A1) + fallback | **PASS** — 8/8, A1 = NO SEAM → fallback adopted |
| 7 | Skill instruction injection (Pitfall 3) | **PASS** — 5/5 |

## A1 Verdict: NO SEAM — Fallback Adopted

```
A1: NO SEAM
```

**Evidence:** `createAgentSession` (0.83.0) accepts `sessionManager?: SessionManager`
only. `SessionManager`'s public surface (constructor, `create`, `inMemory`, static
factories) exposes no storage-injection parameter — the file/JSONL or in-memory backend
is selected internally. `pi-agent-core` exports `InMemorySessionStorage`/`JsonlSessionStorage`
interfaces, but no `createAgentSession` option or `SessionManager` factory accepts a custom
`SessionStorage` implementation in the 0.83.0 public surface.

**Consequence:** NovelMind must adopt the documented fallback — in-memory Pi session +
NovelMind-owned run state. This validates D-11/D-12: the event stream + session state
carry everything needed (run id, status, input, final answer text, tool-call list) without
depending on Pi's own session persistence.

**Implication for 25.2-03:** The `agent_session_entries` PG table and
`pg-session-storage.ts` are NOT required as a Pi storage adapter. Instead NovelMind owns
run state persistence (`skill_runs` style) and the Pi session is ephemeral per run.
Sub-experiment (a) still proves sqlite-backed persistence works at the storage layer —
useful if a later phase wants Pi-native session durability; but it is not required for
the runtime foundation.

## A2 Verdict: PROVEN

```
A2: PROVEN
```

**Evidence:** `DefaultResourceLoader` with `systemPrompt` + `skillsOverride` +
`noPromptTemplates`/`noThemes`/`noContextFiles`/`noExtensions` + inline `extensionFactories`
+ empty controlled `agentDir` closes every discovery surface:
- skills = exactly `["answer-reading-question"]` (allowlist)
- prompts = 0, themes = 0, agentsFiles = 0, extensions = 0

**Key API facts (0.83.0):** all overrides are **functions** (`(base) => next`, not arrays);
`getSkills()`/`getPrompts()`/`getThemes()`/`getAgentsFiles()`/`getExtensions()` return
`{ ...items, diagnostics }` **objects, not arrays**; `getSystemPrompt()` is undefined when
constructed with a `systemPrompt` override.

## Key Findings by Capability

### 1. Tool suppression (D-05) — CRITICAL CONFIG DETAIL
- `noTools: "all"` + `tools: []` yields **zero** exposed tools — bash/read/edit/write/
  grep/find/ls provably absent; `setActiveToolsByName(["read","bash"])` silently ignored;
  `getToolDefinition("read"/"bash")` returns undefined.
- **CRITICAL:** with `noTools: "all"`, `allowedToolNames` resolves to `[]`, and because an
  empty array is **truthy**, `isAllowedTool(name) = allowedToolNames.has(name)` filters out
  **everything, including customTools**. Custom tools MUST be named in the explicit
  `tools: [...]` allowlist to be registered. This is the canonical NovelMind session
  shape: `noTools: "all"` + `tools: [domainToolNames]` + `customTools: [defs]`.

### 2. Custom tools (D-06)
- `defineTool` validates shape only; typed param validation is delegated to
  `typebox/value` (`Check`) at call time. Pi 0.83.0 `ToolDefinition.execute` signature is
  `(toolCallId, params, signal, onUpdate, ctx)` — NOT `(params)`.
- Round trip returns `{ type: "text", content: [{ type: "text", text }] }` shape.

### 3. ResourceLoader (D-18 / A2) — see above

### 4. Session lifecycle (D-19)
- `abort()` is safe when idle; `prompt()` resumes after abort (API shape proven). Live
  `stopReason=aborted` and cancel-mid-tool-call require a live provider key — SKIP with
  reason (D-15 spike exception not satisfied in this environment).

### 5. Streaming events
- `session.subscribe(listener)` returns unsubscribe; `session.messages`/`isStreaming`/
  `isIdle` expose the SSE frame payloads. Curated subset (message_update,
  tool_execution_start/end, turn_end, agent_end) maps structurally; live capture SKIP.

### 6. Storage seam — see A1 above

### 7. Skill injection (Pitfall 3)
- With `noTools: "all"`, skill instructions injected via loader `systemPrompt` appear in
  `session.systemPrompt`; `sendCustomMessage({customType:"skill_context"})` and `steer()`
  provide additional injection paths. **Conclusion: skill instructions MUST be injected,
  never discovered** (read-tool-based discovery is dead under D-05).

## Abort Propagation (A3) Observation

- Structural: `abort()` exists and is idle-safe; the runtime wires an AbortController
  per session (field `_abortController` observed in source). Live mid-tool-call cancel
  propagation not exercised (no provider key) — carried to 25.2-03 verification with a
  mocked provider.

## Constraints Handed to 25.2-03 / 25.2-04

1. Canonical session shape: `noTools: "all"` + `tools: [domainTools]` + `customTools`.
2. Tool execute signature: `(toolCallId, params, signal, onUpdate, ctx)`.
3. Storage: NO Pi-native session persistence — NovelMind-owned run state
   (`skill_runs`-style) + in-memory Pi session per run; `agent_session_entries` PG table
   NOT needed (revisit only if sqlite-native durability is later desired).
4. Skill instructions: injected via systemPrompt / custom message / steer — never
   file-discovered.
5. ResourceLoader: overrides are functions; no* switches + empty controlled agentDir is
   the closure recipe.
6. No write path and no default coding tool was exercised anywhere in this spike (D-22).

## Package Pins (frozen)

- `@earendil-works/pi-coding-agent`: 0.83.0
- `@earendil-works/pi-agent-core`: 0.83.0
- `@earendil-works/pi-ai`: 0.83.0
- `@earendil-works/pi-storage-sqlite-node`: 0.83.0 (SUS, human-approved 2026-08-02, spike-only D-17)
- `typebox`: 1.3.7
- dev: typescript 5.9.3, vitest 4.1.10

*Decision on go/no-go and production storage is owned by 25.2-06 (DECISION.md). This spike
does not create it.*
