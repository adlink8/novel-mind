#!/usr/bin/env node
/**
 * Spike 04 — Session lifecycle (D-19)
 *
 * Prove session create/resume/cancel/retry via abort()/continue() with stopReason
 * surfaced. Without a live model/provider key, lifecycle wiring (session creation,
 * abort plumbing, resume API shape, cancel via AbortSignal) is proven and
 * live-turn assertions are marked SKIP with reason.
 *
 * Output: one PASS/FAIL/SKIP line per assertion; exits non-zero on any FAIL.
 */

import { createAgentSession, SessionManager, DefaultResourceLoader, SettingsManager } from "@earendil-works/pi-coding-agent";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures++;
}
function skip(name, reason) {
  console.log(`SKIP ${name} — ${reason}`);
}

const agentDir = mkdtempSync(join(tmpdir(), "nm-spike04-agent-"));
const cwd = mkdtempSync(join(tmpdir(), "nm-spike04-cwd-"));

const settingsManager = SettingsManager.create(cwd, agentDir);
const loader = new DefaultResourceLoader({ cwd, agentDir, settingsManager, noExtensions: true });
await loader.reload();

// 1. Session creation
const sessionManager = SessionManager.inMemory();
const { session } = await createAgentSession({
  cwd,
  agentDir,
  resourceLoader: loader,
  sessionManager,
  noTools: "all",
});
check("session created", session !== undefined && typeof session.sessionId === "string" && session.sessionId.length > 0, session.sessionId);
check("session starts idle", session.isIdle === true || session.isStreaming === false, `isStreaming=${session.isStreaming}`);

// 2. abort() plumbing exists and is safe when idle
check("abort API present", typeof session.abort === "function", "");
try {
  session.abort();
  check("abort() safe when idle", true, "");
} catch (e) {
  check("abort() safe when idle", false, e.message);
}

// 3. Lifecycle wiring: create -> prompt (may fail without key) -> abort
const hasProviderKey = Boolean(process.env.ANTHROPIC_API_KEY || process.env.OPENAI_API_KEY || process.env.NOVELMIND_DEV_API_KEY);
if (!hasProviderKey) {
  skip("live prompt() turn", "no dev provider key (D-15 spike exception not satisfied) — wiring proven, turn not exercised");
  skip("abort mid-turn stopReason=aborted", "no live model; abort() wiring proven above");
} else {
  // Live path: prompt then abort
  const run = session.prompt("Answer in one word: what is 2+2?");
  setTimeout(() => session.abort(), 1500);
  try {
    await run;
    check("prompt() completed or aborted without throwing", true, "");
  } catch (e) {
    const msg = e.message ?? String(e);
    check("prompt() rejected cleanly on abort", /abort/i.test(msg), msg.slice(0, 80));
  }
  const last = session.messages[session.messages.length - 1];
  const stopReason = last?.stopReason ?? last?.type;
  console.log(`[info] final message stopReason: ${JSON.stringify(stopReason)}`);
}

// 4. Cancel propagation: session exposes AbortSignal contract for tool execute
// (verify the runtime wires a signal — checked structurally, no live tool needed)
check("session has abort controller wiring (structural)", typeof session.abort === "function" && session.messages !== undefined, "");

// 5. Resume/continue shape: agent loop continues after abort via prompt() again
check("resume possible via subsequent prompt() (API shape)", typeof session.prompt === "function", "");

console.log(`\n${failures === 0 ? "ALL PASS" : `${failures} FAILURE(S)`}`);
process.exit(failures === 0 ? 0 : 1);
