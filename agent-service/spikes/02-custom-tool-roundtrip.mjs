#!/usr/bin/env node
/**
 * Spike 02 — Custom tool roundtrip (D-06)
 *
 * Register a stub NovelMind domain tool `get_chapter` via defineTool with TypeBox
 * typed parameters. Assert: registration succeeds, wrong-typed params are rejected
 * (typebox/value Check), and an execute round trip returns the canned JSON
 * (no FastAPI call). Uses Pi 0.83.0's real ToolDefinition.execute signature:
 * (toolCallId, params, signal, onUpdate, ctx).
 *
 * Output: one PASS/FAIL line per assertion; exits non-zero on any FAIL.
 */

import { createAgentSession, defineTool, SessionManager, DefaultResourceLoader, SettingsManager } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Check } from "typebox/value";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures++;
}

const paramsSchema = Type.Object({
  novel: Type.String({ description: "Novel id" }),
  chapter: Type.Integer({ minimum: 1, description: "1-based chapter number" }),
});

// Stub domain tool: get_chapter(novel, chapter) -> canned JSON. Pi 0.83.0 signature:
// execute(toolCallId, params, signal, onUpdate, ctx)
const getChapterTool = defineTool({
  name: "get_chapter",
  label: "Get Chapter",
  description: "Return the raw text of a novel chapter (stub, canned JSON)",
  parameters: paramsSchema,
  execute: async (_toolCallId, params, _signal, _onUpdate) => {
    return {
      type: "text",
      content: [{ type: "text", text: `(stub) chapter ${params.chapter} of ${params.novel}` }],
    };
  },
});

const agentDir = mkdtempSync(join(tmpdir(), "nm-spike02-agent-"));
const cwd = mkdtempSync(join(tmpdir(), "nm-spike02-cwd-"));

const settingsManager = SettingsManager.create(cwd, agentDir);
const loader = new DefaultResourceLoader({ cwd, agentDir, settingsManager, noExtensions: true });
await loader.reload();

const { session } = await createAgentSession({
  cwd,
  agentDir,
  resourceLoader: loader,
  sessionManager: SessionManager.inMemory(),
  noTools: "all",
  // KEY FINDING (see FINDINGS.md): with noTools:"all", allowedToolNames resolves to [].
  // An empty array is truthy, so isAllowedTool(name) = allowedToolNames.has(name) filters
  // OUT every tool INCLUDING customTools. Custom tools must be named in the explicit
  // tools allowlist to be registered.
  tools: ["get_chapter"],
  customTools: [getChapterTool],
});

// 1. Tool registered and present in the registry
const def = session.getToolDefinition("get_chapter");
check("get_chapter registered", def !== undefined, def ? "" : "definition missing");
check("get_chapter exposed in getAllTools", session.getAllTools().some((t) => t.name === "get_chapter"), "");

// 2. Parameter schema present
check("parameter schema attached", def?.parameters !== undefined, "");

// 3. TypeBox schema rejects wrong-typed params (integer field, string given)
check(
  "wrong-typed params rejected by typebox/value",
  !Check(paramsSchema, { novel: "nm", chapter: "three" }),
  "",
);
check("valid typed params accepted by typebox/value", Check(paramsSchema, { novel: "nm", chapter: 3 }), "");
check(
  "missing param rejected by typebox/value",
  !Check(paramsSchema, { novel: "nm" }),
  "",
);

// 4. Execute round trip through the tool's execute directly (SDK wiring proof)
const result = await getChapterTool.execute("call-1", { novel: "nm", chapter: 3 }, undefined, undefined, {});
const text = result?.content?.[0]?.text ?? JSON.stringify(result);
check("execute round trip returns canned JSON", text === "(stub) chapter 3 of nm", text);

// 5. Invoke through the session's tool context if exposed (best-effort)
const toolInfo = session.getAllTools().find((t) => t.name === "get_chapter");
check("tool info exposes name + description", toolInfo?.name === "get_chapter" && toolInfo.description.length > 0, "");

console.log(`\n${failures === 0 ? "ALL PASS" : `${failures} FAILURE(S)`}`);
process.exit(failures === 0 ? 0 : 1);
