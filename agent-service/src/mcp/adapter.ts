/**
 * pi-mcp-adapter 隔离接线（25.3-03 / D-07 / T-25.3-03-01）。
 *
 * 唯一合法接线方式：createMcpAdapter({ config }) 提供的完整隔离配置快照
 * （RESEARCH Pattern 5）——绝不与文件 / imports / 全局配置 / 项目配置合并，
 * 也绝不突变。hostConfigDiscovery 恒为 "off"；directTools 恒为 false（只暴露
 * lazy 代理工具）；服务器 allowlist 来自 packages.lock.json（受治理、可审查），
 * 任何条目在构造 adapter 之前按 allowlist 规则校验（T-25.3-03-01，ASVS V10 SSRF，
 * 镜像 validate_ai_base_url 哲学），违规 → McpAllowlistError 指名服务器与规则。
 *
 * 加载机制（Pitfall 7 决策，见 qualification/pi-mcp-adapter.md）：
 * 类型层经 src/mcp/pi-mcp-adapter.shims.d.ts ambient 声明；运行时在此动态
 * import 真实包——仅在 buildMcpExtension 被调用（MCP 启用）时加载。
 */

import path from "node:path";
import type { McpConfig, McpSettings, ServerEntry } from "pi-mcp-adapter";

export type McpTransportKind = "stdio" | "http";

/** allowlist 服务器条目：stdio 为 pinned 绝对路径二进制；http 为 https-only URL。 */
export interface McpServerConfig {
  name: string;
  transport: McpTransportKind;
  url?: string;
  command?: string;
  args?: string[];
}

/** D-07 allowlist 配置快照（来自 packages.lock.json 治理条目，非 ambient 文件）。 */
export interface McpAllowlistConfig {
  servers: McpServerConfig[];
}

/** allowlist 校验失败（fail-closed，构造 adapter 之前）。 */
export class McpAllowlistError extends Error {
  readonly serverName: string;

  constructor(serverName: string, rule: string) {
    super(`MCP allowlist 拒绝服务器 "${serverName}": ${rule}`);
    this.name = "McpAllowlistError";
    this.serverName = serverName;
  }
}

// 内部服务端口（PostgreSQL / FastAPI）：http URL 带这些端口 → 拒绝。
const FORBIDDEN_HTTP_PORTS = new Set<number>([5432, 8000, 8010]);

// stdio 禁止二进制：数据库客户端、shell、包运行器（浮动包供应链风险）。
const FORBIDDEN_STDIO_BINARIES = new Set<string>([
  "psql",
  "pg_dump",
  "pg_restore",
  "sqlite3",
  "mysql",
  "mariadb",
  "bash",
  "sh",
  "zsh",
  "fish",
  "cmd",
  "powershell",
  "pwsh",
  "npx",
  "npm",
  "yarn",
  "pnpm",
  "bun",
  "python",
  "python3",
  "py",
]);

// 浮动包引用：@latest / @* / 裸 latest 尾缀。
const FLOATING_PACKAGE_RE = /@latest$|@\*$|(^|[/\\@])latest$/i;

function allowlistError(server: McpServerConfig, rule: string): never {
  throw new McpAllowlistError(server.name, rule);
}

/** 宿主是否为 loopback / private / link-local（含 IPv6 ULA 与链路本地）。 */
export function isPrivateHost(hostname: string): boolean {
  const lower = hostname.toLowerCase();
  if (lower === "localhost" || lower === "::1" || lower === "::") return true;
  const v4 = lower.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    if (a === 127 || a === 10) return true; // loopback / 10.0.0.0/8
    if (a === 172 && b >= 16 && b <= 31) return true; // 172.16.0.0/12
    if (a === 192 && b === 168) return true; // 192.168.0.0/16
    if (a === 169 && b === 254) return true; // 169.254.0.0/16 link-local
    if (a === 0) return true; // 0.0.0.0
    return false;
  }
  if (lower.startsWith("fc") || lower.startsWith("fd")) return true; // fc00::/7 ULA
  if (lower.startsWith("fe80:")) return true; // fe80::/10 link-local
  return false;
}

function validateHttpServer(server: McpServerConfig): void {
  if (!server.url) allowlistError(server, "http 服务器必须提供 url");
  let parsed: URL;
  try {
    parsed = new URL(server.url as string);
  } catch {
    allowlistError(server, `http url 不可解析: ${server.url}`);
  }
  if (parsed.protocol !== "https:") {
    allowlistError(server, `http 传输必须 https（实际 ${parsed.protocol}//）`);
  }
  if (isPrivateHost(parsed.hostname)) {
    allowlistError(server, `宿主为 loopback/private/link-local（${parsed.hostname}），禁止内部网络`);
  }
  if (parsed.port && FORBIDDEN_HTTP_PORTS.has(Number(parsed.port))) {
    allowlistError(server, `端口 ${parsed.port} 指向内部服务（PostgreSQL/FastAPI），拒绝`);
  }
}

function validateStdioServer(server: McpServerConfig): void {
  if (!server.command) allowlistError(server, "stdio 服务器必须提供 pinned 绝对路径命令");
  if (!path.isAbsolute(server.command as string)) {
    allowlistError(server, `stdio 命令必须是绝对路径（实际 ${server.command}）`);
  }
  const base = path
    .basename(server.command as string)
    .toLowerCase()
    .replace(/\.exe$/, "");
  if (FORBIDDEN_STDIO_BINARIES.has(base)) {
    allowlistError(server, `stdio 二进制 ${base} 在禁止清单（数据库客户端/shell/包运行器）`);
  }
  for (const arg of server.args ?? []) {
    if (FLOATING_PACKAGE_RE.test(arg)) {
      allowlistError(server, `stdio 参数含浮动包引用 ${arg}（必须精确 pin 或绝对路径）`);
    }
  }
}

/**
 * 校验 allowlist 条目（构造 adapter 之前）。任何违规条目 → McpAllowlistError
 * 指名服务器与规则；无默认放行（fail-closed）。
 */
export function validateMcpAllowlist(cfg: McpAllowlistConfig): void {
  for (const server of cfg.servers) {
    if (server.transport === "http") validateHttpServer(server);
    else if (server.transport === "stdio") validateStdioServer(server);
    else allowlistError(server, `未知传输类型 ${String(server.transport)}`);
    // D-07/Pitfall 5：adapter 配置不携带任何环境变量（无 DB env 泄漏面）。
    if (Object.prototype.hasOwnProperty.call(server, "env")) {
      allowlistError(server, "adapter 配置不携带环境变量（env 被拒绝）");
    }
  }
}

/**
 * 构建隔离配置快照（纯函数，可测试）：允许的服务器映射为 ServerEntry，settings
 * 固定 hostConfigDiscovery "off" / directTools false / autoAuth false。
 * 配置绝不携带任何环境变量——ServerEntry 不注入 env，不存在 POSTGRES/DATABASE_URL
 * 泄漏面（T-25.3-03-01「adapter 配置无 DB env」）。
 */
export function buildMcpConfig(cfg: McpAllowlistConfig): McpConfig {
  validateMcpAllowlist(cfg);
  const mcpServers: Record<string, ServerEntry> = {};
  for (const server of cfg.servers) {
    const common: Pick<ServerEntry, "lifecycle" | "requestTimeoutMs" | "exposeResources"> = {
      lifecycle: "lazy",
      requestTimeoutMs: 30_000,
      exposeResources: false,
    };
    mcpServers[server.name] =
      server.transport === "http"
        ? { url: server.url, auth: false, ...common }
        : { command: server.command, args: server.args ?? [], ...common };
  }
  const settings: McpSettings = {
    hostConfigDiscovery: "off",
    directTools: false,
    autoAuth: false,
  };
  return { settings, mcpServers };
}

/**
 * 隔离接线入口：allowlist 校验 + 快照构建之后才调用 createMcpAdapter({ config })。
 * 真实包为 TS-source（Pitfall 7），此处延迟动态 import，仅在 MCP 启用时加载。
 */
export async function buildMcpExtension(cfg: McpAllowlistConfig) {
  const config = buildMcpConfig(cfg);
  const { createMcpAdapter } = await import("pi-mcp-adapter");
  return createMcpAdapter({ config });
}
