import { describe, expect, it, vi } from "vitest";
import { buildConnectorTools, type ConnectorRuntimeManifest } from "../src/tools/connector-tools.js";
import { createSession } from "../src/agent/session-factory.js";
import { loadSkillFromManifest, runtimeManifestChecksum } from "../src/skills/loader.js";

const connector: ConnectorRuntimeManifest = {
  tool_name: "connector:weather_lookup",
  connector_id: 7,
  version_id: 11,
  version: 2,
  checksum: "a".repeat(64),
  method: "GET",
  request_schema: { type: "object", properties: { city: { type: "string" } } },
  response_schema: { type: "object" },
};

describe("restricted HTTPS connector tools", () => {
  it("builds a stable namespaced tool and never accepts a URL parameter", () => {
    const tools = buildConnectorTools([connector], "Bearer run-token", 3);
    expect(tools).toHaveLength(1);
    expect(tools[0].name).toBe("connector:weather_lookup");
    expect(JSON.stringify(tools[0].parameters)).not.toContain("url");
  });

  it("loads a declarative skill manifest that references a connector", () => {
    const payload = {
      owner_id: 1,
      novel_id: 3,
      skill_version_id: 9,
      name: "weather-skill",
      version: "1.0.0",
      description: "",
      prompt: "Use the connector.",
      execution_mode: "declarative_only" as const,
      allowed_tools: ["connector:weather_lookup"],
      read_permissions: [],
      write_permissions: [],
      forbidden_spaces: [],
      budget: {},
      approval_required_for: [],
      input_schema: { type: "object" },
      output_schema: { type: "object" },
      connector_versions: [connector],
    };
    const {
      owner_id: _ownerId,
      novel_id: _novelId,
      skill_version_id: _skillVersionId,
      connector_versions: _connectorVersions,
      ...checksumPayload
    } = payload;
    const manifest = { ...payload, checksum: runtimeManifestChecksum(checksumPayload) };
    expect(() => loadSkillFromManifest(manifest)).not.toThrow();
  });

  it("session rejects a connector that is not in the frozen manifest", async () => {
    const skill = {
      name: "weather-skill",
      version: "1.0.0",
      description: "",
      allowedTools: ["connector:missing"],
      readPermissions: [],
      writePermissions: [],
      forbiddenSpaces: [],
      budget: {},
      approvalRequiredFor: [],
      filePath: "db://skill",
      baseDir: "db://skill",
      instructions: "Use it.",
      validateInput: vi.fn(() => true),
      validateOutput: vi.fn(() => true),
    } as never;
    await expect(
      createSession({ auth: "Bearer run-token", novelId: 3, skill }),
    ).rejects.toThrow(/connector:missing/);
  });
});
