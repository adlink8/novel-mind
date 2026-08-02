# Spike Experiments — Embedded Novel Agent Runtime

**Phase:** 25.2-01 | **Date:** 2026-08-02 | **Node:** v22.22.2

One recorded experiment per capability. Verdicts: PASS / FAIL / SKIP-with-reason.
No phrase "will be tested later" or "assumed working" is allowed in this file.

## Experiment 0 — npm re-verification (pre-install gate, Task 1)

**Capability:** Confirm exact pins and absence of lifecycle scripts before install.

**Command output (2026-08-02):**

```
@earendil-works/pi-coding-agent@0.83.0
  version      = '0.83.0'
  time.modified = '2026-07-29T22:30:38.873Z'
  engines      = { node: '>=22.19.0' }
  scripts      = { test, build, clean, shrinkwrap, copy-assets, build:binary }  # no postinstall/preinstall
@earendil-works/pi-agent-core@0.83.0
  version      = '0.83.0'
  engines      = { node: '>=22.19.0' }
  scripts      = { test, build, clean, test:harness, prepublishOnly }            # no postinstall/preinstall
@earendil-works/pi-ai@0.83.0
  version      = '0.83.0'
  engines      = { node: '>=22.19.0' }
  scripts      = { test, build, clean, build:offline, prepublishOnly }           # no postinstall/preinstall
```

**Verdict:** PASS — all three core packages are exactly 0.83.0, no postinstall/preinstall
lifecycle scripts, engines >=22.19.0 satisfied by local Node v22.22.2.

**Evidence:** `npm install` added 279 packages; `import('@earendil-works/pi-coding-agent')`
resolves `createAgentSession` as `function`; vitest scaffold smoke 3/3 passed.

## Experiment 1 — No default coding tools (D-05)

**Capability:** Pi default coding tools (bash/read/edit/write/grep/find/ls) are provably
absent from every created session.

**Script:** `agent-service/spikes/01-no-tools-enumeration.mjs`

**Assertions:** session created with `noTools: "all"` exposes zero of
bash/read/edit/write/grep/find/ls; a `read` tool call path is rejected.

**Verdict:** PASS — 10/10 assertions; noTools:"all" + empty tools allowlist yields 0 exposed tools; forbidden activation ignored.

## Experiment 2 — Custom tool roundtrip (D-06)

**Capability:** custom NovelMind domain tool registers and round-trips typed parameters.

**Script:** `agent-service/spikes/02-custom-tool-roundtrip.mjs`

**Assertions:** `defineTool` with TypeBox params registers; wrong-typed params rejected;
execute round trip returns canned JSON.

**Verdict:** PASS — 8/8; custom tool registered only when named in explicit tools allowlist (KEY FINDING: noTools:"all" sets allowedToolNames=[] which is truthy and filters out customTools too).

## Experiment 3 — ResourceLoader allowlist closure (D-18 / A2)

**Capability:** ResourceLoader loads only allowlisted resources; zero ambient
skills/prompts/themes/extensions/context discovered.

**Script:** `agent-service/spikes/03-resource-loader-allowlist.mjs`

**Assertions:** discovery surface enumerated; loaded set equals allowlist exactly; A2 ends
PROVEN or MITIGATED (empty controlled agentDir fallback).

**Verdict:** PASS — 9/9; A2 = PROVEN: systemPrompt + skillsOverride + noPromptTemplates/noThemes/noContextFiles/noExtensions + empty controlled agentDir closes every discovery surface; getSkills() returns {skills, diagnostics} object (not array).

## Experiment 4 — Session lifecycle (D-19)

**Capability:** create/resume/cancel/retry via abort()/continue() with stopReason surfaced.

**Script:** `agent-service/spikes/04-session-lifecycle.mjs`

**Assertions:** prompt → abort → stopReason `aborted`; continue resumes; cancel stops
in-flight tool execution via AbortSignal.

**Verdict:** PASS — 7/7 + 2 SKIP(live turn, no provider key); abort() safe when idle; resume via prompt() API shape proven.

## Experiment 5 — Streaming events (SSE subset)

**Capability:** Pi events (message_update, tool_execution_*, turn_end, agent_end) capturable
for SSE serialization.

**Script:** `agent-service/spikes/05-streaming-events.mjs`

**Assertions:** subscribe captures the curated subset in order; shapes map to RESEARCH
Pattern 5 SSE frames.

**Verdict:** PASS — 6/7 + 1 SKIP(live event capture, no provider key); subscribe()/unsubscribe() wiring + SSE frame mapping structural proof.

## Experiment 6 — Storage seam (A1) + fallback proof

**Capability:** custom SessionStorage injection seam settled; in-memory fallback viable.

**Script:** `agent-service/spikes/06-storage-injection-seam.mjs`

**Assertions:** (a) sqlite session-restore twice with trace diff (gated by Task 3 approval)
or SKIP-with-reason; (b) custom SessionStorage wiring seam found or NO SEAM recorded; (c)
run state reconstructible from event stream alone (D-11/D-12).

**Verdict:** PASS — 8/8. (a) sqlite persists + restores entries in a new storage instance
(Task 3 approved, exact pin 0.83.0); (b) A1 = **NO SEAM** — `createAgentSession` accepts
`SessionManager` only, no custom-storage injection point in 0.83.0 public surface → fallback
adopted; (c) run state (run id/status/input/answer/tool-calls) reconstructible from session
state + events (D-11/D-12). SQLite layer proven usable if later durability is desired.

## Experiment 7 — Skill instruction injection (Pitfall 3)

**Capability:** skill instructions injectable without the disabled read tool.

**Script:** `agent-service/spikes/07-skill-instruction-injection.mjs`

**Assertions:** with `noTools: "all"`, injected skill instructions via system prompt /
before_agent_start appear in session context.

**Verdict:** PASS — 5/5; systemPrompt injection carries skill instructions with noTools:"all" (read tool dead, Pitfall 3 settled); sendCustomMessage/steer paths proven.
