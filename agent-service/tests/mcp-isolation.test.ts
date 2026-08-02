/**
 * mcp-isolation.test.ts（25.3-03 / REQ-AGENT-06 负向测试矩阵，TDD）。
 *
 * 证明第三方 MCP 工具无法穿越证据边界：
 *  - Task 1：allowlist 拒绝矩阵（内部 URL / private IP / psql / shell / 浮动 npx /
 *    env 走私）→ McpAllowlistError 指名服务器；诚实 stub 配置构建；proxy-only 表面
 *    （directTools false）；hostConfigDiscovery "off"。
 *  - Task 2：external_evidence D-09 信封映射（prohibited_from_canon 恒定 true）；
 *    adapter 配置无 POSTGRES/DATABASE_URL env；stub stdio 服务器端到端（真实 MCP
 *    stdio 传输）→ 结果映射为合法信封。
 *
 * 隔离：`vi.mock("pi-mcp-adapter")` 用工厂桩替换真实包（TS-source + optional peer
 *  pi-tui 未提升，真实包加载留给 Task 3 live-run；见 qualification/pi-mcp-adapter.md）。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createMcpAdapter } from "pi-mcp-adapter";
import {
  buildMcpConfig,
  buildMcpExtension,
  validateMcpAllowlist,
  isPrivateHost,
  McpAllowlistError,
  type McpAllowlistConfig,
  type McpServerConfig,
} from "../src/mcp/adapter.js";
import {
  toExternalEvidence,
  type ExternalEvidenceEnvelope,
} from "../src/mcp/external-evidence.js";

vi.mock("pi-mcp-adapter", () => ({
  createMcpAdapter: vi.fn(() => () => {}),
}));

/** stub stdio 服务器条目：node（绝对路径）+ fixture 绝对路径。 */
const stubServer = (): McpServerConfig => ({
  name: "stub-external-research",
  transport: "stdio",
  command: process.execPath,
  args: [fileURLToPath(new URL("./fixtures/stub-mcp-server/server.mjs", import.meta.url))],
});

const stubAllowlist = (): McpAllowlistConfig => ({ servers: [stubServer()] });

describe("allowlist 拒绝矩阵（T-25.3-03-01，SSRF + 危险 stdio）", () => {
  const rejectionCases: Array<{ label: string; server: McpServerConfig }> = [
    {
      label: "loopback http（FastAPI 内部）",
      server: { name: "evil-http", transport: "http", url: "http://127.0.0.1:8000/api/novels" },
    },
    {
      label: "localhost + PostgreSQL 端口",
      server: { name: "evil-pg", transport: "http", url: "http://localhost:5432" },
    },
    {
      label: "private 192.168 网段",
      server: { name: "evil-lan", transport: "http", url: "https://192.168.1.10/status" },
    },
    {
      label: "10.0.0.0/8 网段",
      server: { name: "evil-10", transport: "http", url: "https://10.0.0.5/mcp" },
    },
    {
      label: "169.254 链路本地",
      server: { name: "evil-linklocal", transport: "http", url: "https://169.254.169.254/latest/meta-data" },
    },
    {
      label: "非 https http 传输",
      server: { name: "evil-plain", transport: "http", url: "http://mcp.example.com/search" },
    },
    {
      label: "psql stdio",
      server: { name: "evil-psql", transport: "stdio", command: "/usr/bin/psql", args: ["-c", "select 1"] },
    },
    {
      label: "bash stdio",
      server: { name: "evil-bash", transport: "stdio", command: "/bin/bash", args: ["-c", "rm -rf /"] },
    },
    {
      label: "npx 浮动包 stdio",
      server: { name: "evil-npx", transport: "stdio", command: "/usr/bin/npx", args: ["-y", "@some/pkg@latest"] },
    },
    {
      label: "非绝对路径 stdio 命令",
      server: { name: "evil-float-bin", transport: "stdio", command: "psql", args: [] },
    },
    {
      label: "python 解释器 stdio",
      server: { name: "evil-py", transport: "stdio", command: "/usr/bin/python3", args: ["-c", "print(1)"] },
    },
    {
      label: "env 走私（DATABASE_URL）",
      server: {
        name: "evil-env",
        transport: "stdio",
        command: process.execPath,
        args: ["server.mjs"],
        env: { DATABASE_URL: "postgres://internal/db" },
      } as McpServerConfig,
    },
  ];

  for (const { label, server } of rejectionCases) {
    it(`拒绝 ${label}（指名服务器 + /allowlist/i）`, () => {
      expect(() => buildMcpConfig({ servers: [server] })).toThrow(McpAllowlistError);
      try {
        buildMcpConfig({ servers: [server] });
        expect.unreachable();
      } catch (error) {
        const message = String((error as Error).message);
        expect(message).toMatch(/allowlist/i);
        expect(message).toContain(server.name);
      }
    });
  }

  it("isPrivateHost 覆盖 loopback/private/link-local 网段（单测宿主判定）", () => {
    for (const host of ["localhost", "127.0.0.1", "127.8.8.8", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.0.1", "169.254.1.1", "0.0.0.0", "::1", "fe80::1", "fc00::1"]) {
      expect(isPrivateHost(host), host).toBe(true);
    }
    for (const host of ["8.8.8.8", "1.1.1.1", "172.32.0.1", "192.169.1.1", "mcp.example.com"]) {
      expect(isPrivateHost(host), host).toBe(false);
    }
  });
});

describe("诚实 stub 配置构建（D-07 隔离快照）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("构建通过：settings 固定 hostConfigDiscovery off / directTools false / autoAuth false", async () => {
    const adapter = await buildMcpExtension(stubAllowlist());
    expect(typeof adapter).toBe("function");

    const call = vi.mocked(createMcpAdapter).mock.calls[0]?.[0];
    expect(call).toBeDefined();
    if (!call?.config) throw new Error("createMcpAdapter was not called with a config");
    expect(call.config.settings).toEqual({
      hostConfigDiscovery: "off",
      directTools: false,
      autoAuth: false,
    });
    expect(call.config.mcpServers["stub-external-research"]).toMatchObject({
      command: process.execPath,
      lifecycle: "lazy",
      requestTimeoutMs: 30_000,
      exposeResources: false,
    });
  });

  it("proxy-only 表面：directTools false，且无任何服务器级 directTools 注册（远端工具不入门）", () => {
    const config = buildMcpConfig(stubAllowlist());
    expect(config.settings?.directTools).toBe(false);
    for (const entry of Object.values(config.mcpServers)) {
      expect(entry).not.toHaveProperty("directTools");
      expect(entry).not.toHaveProperty("includeTools");
      expect(entry).not.toHaveProperty("excludeTools");
    }
  });

  it("hostConfigDiscovery 在构建出的配置中为 off（无 ambient 发现）", () => {
    const config = buildMcpConfig(stubAllowlist());
    expect(config.settings?.hostConfigDiscovery).toBe("off");
  });

  it("adapter 配置不携带 POSTGRES/DATABASE_URL env（无 DB 泄漏面）", () => {
    const config = buildMcpConfig(stubAllowlist());
    expect(JSON.stringify(config)).not.toMatch(/postgres|database_url/i);
    for (const entry of Object.values(config.mcpServers)) {
      expect(entry).not.toHaveProperty("env");
    }
  });

  it("validateMcpAllowlist 对诚实配置不抛错，返回 undefined", () => {
    expect(() => validateMcpAllowlist(stubAllowlist())).not.toThrow();
  });
});

describe("external_evidence 信封映射（D-09 / T-25.3-03-02）", () => {
  it("结果数组 → 完整 D-09 信封，prohibited_from_canon 恒为 true 常量", () => {
    const envelope = toExternalEvidence(
      "stub-external-research",
      "external_search",
      [
        { text: "finding one", uri: "https://stub.example/research/1", title: "Stub Finding 1" },
        { text: "finding two" },
      ],
      { confidence: "high" },
    );

    expect(envelope.type).toBe("external_evidence");
    expect(envelope.schema_version).toBe(1);
    expect(envelope.release_status).toBe("external");
    expect(envelope.prohibited_from_canon).toBe(true);
    expect(envelope.retrieval_time).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
    expect(envelope.confidence).toBe("high");

    expect(envelope.sources).toHaveLength(1);
    expect(envelope.sources[0]).toMatchObject({
      server: "stub-external-research",
      tool: "external_search",
      retrieved_from: "mcp",
    });
    expect(envelope.claims).toEqual([
      { text: "finding one", source_index: 0 },
      { text: "finding two", source_index: 1 },
    ]);
  });

  it("{content:[…]} MCP 标准负载 → claims 提取", () => {
    const envelope = toExternalEvidence("srv", "tool", {
      content: [{ type: "text", text: "payload text" }],
    });
    expect(envelope.claims).toEqual([{ text: "payload text", source_index: 0 }]);
  });

  it("confidence 缺省为 low；prohibited_from_canon 无参数可翻转", () => {
    const low = toExternalEvidence("srv", "tool", [{ text: "x" }]);
    expect(low.confidence).toBe("low");
    const empty = toExternalEvidence("srv", "tool", []);
    expect(empty.prohibited_from_canon).toBe(true);
  });
});

describe("stub 服务器端到端（真实 stdio 传输，Task 2）", () => {
  it("allowlist 的 stub 服务器应答 external_search 并映射为合法信封", async () => {
    const serverPath = fileURLToPath(new URL("./fixtures/stub-mcp-server/server.mjs", import.meta.url));
    const transport = new StdioClientTransport({
      command: process.execPath,
      args: [serverPath],
    });
    const client = new Client({ name: "stub-e2e-test", version: "1.0.0" });
    await client.connect(transport);
    try {
      const result = await client.callTool({
        name: "external_search",
        arguments: { query: "novel plot", count: 1 },
      });
      const text = (result.content as Array<{ type: string; text: string }>)[0].text;
      const parsed = JSON.parse(text);
      const envelope: ExternalEvidenceEnvelope = toExternalEvidence(
        "stub-external-research",
        "external_search",
        parsed,
        { confidence: "medium" },
      );
      expect(envelope.type).toBe("external_evidence");
      expect(envelope.sources[0].retrieved_from).toBe("mcp");
      expect(envelope.sources[0].uri).toBe("https://stub.example/research/1");
      expect(envelope.prohibited_from_canon).toBe(true);
      expect(envelope.claims.length).toBeGreaterThan(0);
    } finally {
      await client.close();
    }
  });
});
