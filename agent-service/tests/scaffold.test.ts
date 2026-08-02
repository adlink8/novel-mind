import { describe, it, expect, vi } from "vitest";
import type { AppConfig } from "../src/config.js";

async function loadConfig(): Promise<AppConfig> {
  vi.resetModules();
  const mod = await import("../src/config.js");
  return mod.config;
}

describe("agent-service scaffold", () => {
  it("resolves the pinned Pi SDK entry point", async () => {
    const m = await import("@earendil-works/pi-coding-agent");
    expect(typeof m.createAgentSession).toBe("function");
  });

  it("exports a frozen config object with expected shape", async () => {
    process.env.NOVELMIND_GATEWAY_TOKEN = "test-token";
    const config = await loadConfig();
    expect(config.port).toBe(3100);
    expect(config.fastApiBaseUrl).toBe("http://127.0.0.1:8000");
    expect(Object.isFrozen(config)).toBe(true);
  });

  it("fails fast when the gateway token is absent", async () => {
    delete process.env.NOVELMIND_GATEWAY_TOKEN;
    await expect(loadConfig()).rejects.toThrow("missing required env var");
  });
});
