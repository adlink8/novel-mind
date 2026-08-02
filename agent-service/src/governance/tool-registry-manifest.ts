/**
 * ToolRegistryManifest（25.3-02 / D-06）—— 启动时构建的 JSON 文档。
 *
 * 记录每个工具 {tool_name, provider_package, schema_hash, permission, domain, enabled}。
 * 源：
 *  - domainToolEntries()：25.2-05 域工具注册表（7 个，provider "agent-service"）；
 *  - skillToolEntries(lock) / extensionToolEntries(lock)：技能/扩展声明的工具
 *    （provider "skill:<name>" / "extension:<name>"，来自锁的可选 skills/extensions 段）；
 *  - mcpProxyEntry()：MCP 代理单条目（provider "pi-mcp-adapter"），仅当锁的
 *    mcp.enabled 时加入（D-07；25.3-03 填写该段）。
 *
 * `buildToolRegistryManifest` 是 fail-closed 碰撞门：两个 provider 注册同名工具 →
 * 抛 ToolCollisionError 指名双方，进程先于 listen 以非零码退出，绝不静默按加载顺序
 * 覆盖（D-06 / Pitfall 4：门只覆盖 Pi 注册命名空间，MCP 远端名在其代理 allowlist 内）。
 *
 * schema_hash = sha256（node:crypto）对 canonicalized（稳定键序）的 TypeBox JSON
 * schema；升级改动 schema（如走私新参数）即在启动时经 verifySchemaHashes 检出
 * （schema drift，T-25.3-02-03）。
 */

import { createHash } from "node:crypto";
import { buildDomainTools } from "../tools/registry.js";
import type { GovernanceLock, LockToolProviderSection } from "./lockfile.js";

export type ManifestPermission = "allow" | "ask" | "deny";

/** 单个工具条目（D-06 字段）。 */
export interface ToolRegistryEntry {
  tool_name: string;
  /** "agent-service" | "pi-mcp-adapter" | "skill:<name>" | "extension:<name>"。 */
  provider_package: string;
  /** sha256 hex：canonicalized TypeBox JSON schema。 */
  schema_hash: string;
  permission: ManifestPermission;
  /** "novel-read" | "external-research" | ... */
  domain: string;
  enabled: boolean;
}

/** 同名工具被两个 provider 同时注册（D-06 fail-closed）。 */
export class ToolCollisionError extends Error {
  readonly toolName: string;
  readonly firstProvider: string;
  readonly secondProvider: string;

  constructor(toolName: string, firstProvider: string, secondProvider: string) {
    super(
      `tool_name 冲突: "${toolName}" 由 ${firstProvider} 与 ${secondProvider} 同时注册（D-06 fail-closed，拒绝启动）`,
    );
    this.name = "ToolCollisionError";
    this.toolName = toolName;
    this.firstProvider = firstProvider;
    this.secondProvider = secondProvider;
  }
}

/** schema drift 检出失败（启动时，T-25.3-02-03）。 */
export class ToolSchemaDriftError extends Error {
  readonly toolName: string;
  readonly recordedHash: string;
  readonly currentHash: string;

  constructor(toolName: string, recordedHash: string, currentHash: string) {
    super(
      `schema drift: 工具 "${toolName}" 的 schema_hash 与启动重哈希不一致（recorded=${recordedHash.slice(0, 12)}… current=${currentHash.slice(0, 12)}…），升级改动了 schema（fail-closed）`,
    );
    this.name = "ToolSchemaDriftError";
    this.toolName = toolName;
    this.recordedHash = recordedHash;
    this.currentHash = currentHash;
  }
}

// ────────────────────────── schema canonicalization + hashing ──────────────────────────

/** 递归按键排序，产出稳定键序的值（TypeBox schema 是纯 JSON，无函数值）。 */
function canonicalizeValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalizeValue);
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as object).sort()) {
      out[key] = canonicalizeValue((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  return value;
}

/** 稳定键序 JSON 字符串：键序打乱不影响哈希，schema 内容变化必然改变哈希。 */
export function canonicalizeSchema(schema: unknown): string {
  return JSON.stringify(canonicalizeValue(schema));
}

/** sha256 hex（node:crypto），对 canonicalized schema 计算。 */
export function schemaHash(schema: unknown): string {
  return createHash("sha256").update(canonicalizeSchema(schema)).digest("hex");
}

// ────────────────────────── manifest 源 ──────────────────────────

/** 25.2-05 域工具条目：7 个，provider "agent-service"、domain "novel-read"、permission "allow"。 */
export function domainToolEntries(): ToolRegistryEntry[] {
  // auth 仅为占位（execute 不在此处调用，永不触网）；schema 来自真实注册工具。
  return buildDomainTools("manifest-auth-placeholder").map((tool) => ({
    tool_name: tool.name,
    provider_package: "agent-service",
    schema_hash: schemaHash(tool.parameters),
    permission: "allow",
    domain: "novel-read",
    enabled: true,
  }));
}

/** MCP 代理工具 schema（D-07 单一 proxy 面：lazy 搜索 → 按名调用）。 */
const MCP_PROXY_SCHEMA = {
  type: "object",
  properties: {
    search: { type: "string", minLength: 1, description: "按名称搜索外部 MCP 工具" },
    tool: { type: "string", description: "已发现的外部工具名" },
    args: { type: "object", description: "外部工具参数" },
  },
  required: ["search"],
} as const;

/** MCP 代理单条目（D-07 / Pitfall 4：门内只此一条，远端名走服务器 allowlist）。 */
export function mcpProxyEntry(): ToolRegistryEntry {
  return {
    tool_name: "mcp",
    provider_package: "pi-mcp-adapter",
    schema_hash: schemaHash(MCP_PROXY_SCHEMA),
    permission: "ask",
    domain: "external-research",
    enabled: true,
  };
}

/** 技能声明的工具条目（provider "skill:<name>"；当前锁无 skills 段 → 空）。 */
export function skillToolEntries(lock: GovernanceLock): ToolRegistryEntry[] {
  return (lock.skills ?? []).flatMap((skill) =>
    providerToolEntries(skill, `skill:${skill.name}`, "skill-tool"),
  );
}

/** 扩展声明的工具条目（provider "extension:<name>"；当前锁无 extensions 段 → 空）。 */
export function extensionToolEntries(lock: GovernanceLock): ToolRegistryEntry[] {
  return (lock.extensions ?? []).flatMap((extension) =>
    providerToolEntries(extension, `extension:${extension.name}`, "extension-tool"),
  );
}

function providerToolEntries(
  provider: LockToolProviderSection,
  providerPackage: string,
  defaultDomain: string,
): ToolRegistryEntry[] {
  return (provider.tools ?? []).map((tool) => ({
    tool_name: tool.tool_name,
    provider_package: providerPackage,
    schema_hash: schemaHash(tool.schema ?? { type: "object" }),
    permission: provider.permission ?? "ask",
    domain: provider.domain ?? defaultDomain,
    enabled: provider.enabled ?? true,
  }));
}

// ────────────────────────── 碰撞门 + drift 门 ──────────────────────────

/**
 * fail-closed 碰撞门（D-06）：同名工具出现在任何两个源 → 抛 ToolCollisionError
 * 指名双方 provider_package，绝不静默按加载顺序覆盖。
 */
export function buildToolRegistryManifest(sources: readonly ToolRegistryEntry[][]): ToolRegistryEntry[] {
  const seen = new Map<string, ToolRegistryEntry>();
  for (const entry of sources.flat()) {
    const prior = seen.get(entry.tool_name);
    if (prior) {
      throw new ToolCollisionError(entry.tool_name, prior.provider_package, entry.provider_package);
    }
    seen.set(entry.tool_name, entry);
  }
  return [...seen.values()];
}

/**
 * schema drift 启动门（T-25.3-02-03）：把启动重哈希的 manifest 与记录哈希
 * （如上次启动持久化的 manifest）对照；同一工具名哈希变化 → 抛 ToolSchemaDriftError。
 * runGovernanceChain 在传入 expectedHashes 时于碰撞门之后调用。
 */
export function verifySchemaHashes(
  entries: readonly ToolRegistryEntry[],
  expectedHashes: ReadonlyMap<string, string> | Record<string, string>,
): void {
  const map = expectedHashes instanceof Map ? expectedHashes : new Map(Object.entries(expectedHashes));
  for (const entry of entries) {
    const recorded = map.get(entry.tool_name);
    if (recorded && recorded !== entry.schema_hash) {
      throw new ToolSchemaDriftError(entry.tool_name, recorded, entry.schema_hash);
    }
  }
}
