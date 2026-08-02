#!/usr/bin/env node
/**
 * Spike 07 — Skill instruction injection (Pitfall 3)
 *
 * With noTools:"all" (Pi's read-based skill loading is dead), inject skill
 * instructions via the session's system prompt / custom message path and
 * assert the session behavior reflects them — i.e. instructions land in the
 * session context without any read tool being available.
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

const SKILL_INSTRUCTION =
  "NovelMind skill: answer-reading-question. Always call get_chapter before answering; cite raw offsets.";

const agentDir = mkdtempSync(join(tmpdir(), "nm-spike07-agent-"));
const cwd = mkdtempSync(join(tmpdir(), "nm-spike07-cwd-"));
const settingsManager = SettingsManager.create(cwd, agentDir);
const loader = new DefaultResourceLoader({
  cwd,
  agentDir,
  settingsManager,
  systemPrompt: SKILL_INSTRUCTION, // injected directly — no read tool needed
  noExtensions: true,
});
await loader.reload();

const { session } = await createAgentSession({
  cwd,
  agentDir,
  resourceLoader: loader,
  sessionManager: SessionManager.inMemory(),
  noTools: "all",
});

// 1. Skill instructions present in the effective system prompt
check("system prompt carries injected skill instruction", session.systemPrompt.includes("answer-reading-question"), session.systemPrompt ? session.systemPrompt.slice(0, 60) : "(empty)");
check("skill instruction includes domain guidance", session.systemPrompt.includes("cite raw offsets"), "");

// 2. Injection did NOT require the read tool — session has no coding tools
const exposed = session.getAllTools().map((t) => t.name);
check("no read tool used for injection (Pitfall 3 settled)", !exposed.includes("read"), `exposed=${exposed.join(",") || "(none)"}`);

// 3. Custom message path also carries skill context (sendCustomMessage, no turn)
const before = session.messages.length;
await session.sendCustomMessage({
  customType: "skill_context",
  content: [{ type: "text", text: SKILL_INSTRUCTION }],
  display: false,
  details: { skill: "answer-reading-question" },
});
check("skill context injectable via custom message", session.messages.length > before, `messages ${before} -> ${session.messages.length}`);

// 4. Steering path available for mid-turn skill reminders (API shape)
check("steer API present for skill reminders", typeof session.steer === "function", "");

console.log(`\n${failures === 0 ? "ALL PASS" : `${failures} FAILURE(S)`}`);
process.exit(failures === 0 ? 0 : 1);
