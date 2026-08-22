#!/usr/bin/env node
/**
 * Stub stdio MCP server fixture（25.3-03 spike，零外部依赖）。
 *
 * 通过 @modelcontextprotocol/sdk（pi-mcp-adapter 的传递依赖，闭包内已声明）
 * 暴露单一工具 `external_search`，返回固定 JSON payload。用于证明：
 *   - allowlist 的 stdio 条目（node + 本文件绝对路径）能经 MCP 协议被调用；
 *   - 外部结果经 toExternalEvidence 物化为 external_evidence 信封。
 *
 * 运行方式（allowlist 条目形式）：`node <本文件绝对路径>`。
 * 协议走 stdin/stdout JSON-RPC（StdioServerTransport），无任何网络/文件系统副作用。
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const FIXED_RESULTS = [
  {
    uri: "https://stub.example/research/1",
    title: "Stub Research Finding 1",
    text: "Fictional external research finding: the query topic appears in a publicly indexed source.",
  },
  {
    uri: "https://stub.example/research/2",
    title: "Stub Research Finding 2",
    text: "Fictional external research finding: cross-reference confirms the topic with a secondary source.",
  },
];

const server = new McpServer({
  name: "stub-external-research",
  version: "1.0.0",
});

server.registerTool(
  "external_search",
  {
    query: { type: "string", minLength: 1 },
    count: { type: "integer", minimum: 1, maximum: 5, default: 1 },
  },
  async ({ count }) => {
    const items = FIXED_RESULTS.slice(0, count ?? 1);
    return {
      content: [{ type: "text", text: JSON.stringify(items) }],
    };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
