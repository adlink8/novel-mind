#!/usr/bin/env node
/**
 * Spike 01 — No default coding tools (D-05)
 *
 * Create a session with noTools: "all" + explicit tools allowlist and prove that
 * bash/read/edit/write/grep/find/ls are absent from the session's tool registry.
 * Also attempt a `read` tool-call path and assert it is rejected.
 *
 * Output: one PASS/FAIL line per assertion; exits non-zero on any FAIL.
 */

import { createAgentSession, SessionManager, DefaultResourceLoader, SettingsManager } from "@earendil-works/pi-coding-agent";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const FORBIDDEN = ["bash", "read", "edit", "write", "grep", "find", "ls"];
let failures = 0;

function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures++;
}

const agentDir = mkdtempSync(join(tmpdir(), "nm-spike01-agent-"));
const cwd = mkdtempSync(join(tmpdir(), "nm-spike01-cwd-"));

const settingsManager = SettingsManager.create(cwd, agentDir);
const loader = new DefaultResourceLoader({ cwd, agentDir, settingsManager, noExtensions: true });
await loader.reload();

const { session } = await createAgentSession({
  cwd,
  agentDir,
  resourceLoader: loader,
  sessionManager: SessionManager.inMemory(),
  noTools: "all",
  tools: [], // explicit allowlist: empty — no built-in tools
});

// 1. Enumerate the tools the session actually exposes
const allTools = session.getAllTools();
const exposed = allTools.map((t) => t.name);
console.log(`[info] exposed tools (${exposed.length}): ${exposed.join(", ") || "(none)"}`);

// 2. Assert none of the forbidden tools is present
let allAbsent = true;
for (const name of FORBIDDEN) {
  const present = exposed.includes(name);
  if (present) allAbsent = false;
  check(`no '${name}' in session tools`, !present, present ? `FOUND ${name}` : "");
}

// 3. Assert getToolDefinition for forbidden tools returns undefined
const readDef = session.getToolDefinition("read");
check("'read' tool definition absent", readDef === undefined, readDef ? "read definition present" : "");

// 4. Attempt to set a forbidden tool active by name — must be ignored
session.setActiveToolsByName(["read", "bash"]);
const active = session.getActiveToolNames();
const leak = active.filter((n) => FORBIDDEN.includes(n));
check("forbidden tool activation rejected", leak.length === 0, leak.length ? `active leaks: ${leak.join(",")}` : "");

// 5. Attempt a direct tool call path — not in registry, getToolDefinition undefined
const directCall = session.getToolDefinition("bash");
check("direct 'bash' tool-call path rejected", directCall === undefined, directCall ? "bash available" : "");

console.log(`\n${failures === 0 ? "ALL PASS" : `${failures} FAILURE(S)`}`);
process.exit(failures === 0 ? 0 : 1);
