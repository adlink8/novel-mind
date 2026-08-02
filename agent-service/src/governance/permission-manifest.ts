/**
 * D-05 permission-manifest 校验器（25.3-02，fail-closed）。
 *
 * 每个已装（installed:true）包必须声明完整 D-05 权限清单：network 为
 * "deny"|"allowlist"（allowlist 必须有非空 network_allowlist），filesystem / shell /
 * env 恒为字面量 "deny"，secrets 恒为 "named-only"，tools / artifact_writes 为字符串
 * 数组。缺失或非法 → 抛 PermissionManifestError 指名包与字段；installed:false 条目
 * 跳过（pattern-only/reject 从不加载）。无自动修复、无默认放行——一个包无法声明
 * 其权限，就不得进入服务（D-05）。
 */

import { Ajv } from "ajv";
import type { GovernanceLock } from "./lockfile.js";

/** D-05 清单校验失败（fail-closed），消息指名包与违规字段。 */
export class PermissionManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PermissionManifestError";
  }
}

/** D-05 permission_manifest 的 ajv schema（精确编码 D-05 形状）。 */
export const PERMISSION_MANIFEST_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: [
    "network",
    "network_allowlist",
    "filesystem",
    "shell",
    "env",
    "secrets",
    "tools",
    "artifact_writes",
  ],
  properties: {
    network: { type: "string", enum: ["deny", "allowlist"] },
    network_allowlist: { type: "array", items: { type: "string", minLength: 1 } },
    filesystem: { type: "string", const: "deny" },
    shell: { type: "string", const: "deny" },
    env: { type: "string", const: "deny" },
    secrets: { type: "string", const: "named-only" },
    tools: { type: "array", items: { type: "string", minLength: 1 } },
    artifact_writes: { type: "array", items: { type: "string", minLength: 1 } },
  },
} as const;

// 复用 25.2-05 已 pin 的 ajv（不新增校验库）。strict:false 避免对 const 值表报 strict 模式错误。
const validatePermissionManifest = new Ajv({ allErrors: true, strict: false }).compile(
  PERMISSION_MANIFEST_SCHEMA,
);

/**
 * 校验锁清单中每个已装条目的 D-05 permission_manifest。
 *
 * @throws PermissionManifestError —— 指名包与 ajv 失败字段；installed:false 跳过。
 */
export function validatePermissionManifests(lock: GovernanceLock): void {
  for (const entry of lock.packages) {
    if (entry.installed !== true) continue; // pattern-only / reject 从不加载

    const pm = entry.permission_manifest;
    if (!pm || typeof pm !== "object") {
      throw new PermissionManifestError(
        `${entry.name}: permission_manifest 缺失（installed 包必须声明 D-05 清单）`,
      );
    }
    if (!validatePermissionManifest(pm)) {
      throw new PermissionManifestError(
        `${entry.name}: permission_manifest 非法（字段: ${firstErrorField(validatePermissionManifest.errors ?? [])}）`,
      );
    }
    // 条件约束：network=allowlist 必须携带非空 network_allowlist（主机 allowlist）。
    if (pm.network === "allowlist" && (pm.network_allowlist?.length ?? 0) === 0) {
      throw new PermissionManifestError(
        `${entry.name}: permission_manifest.network_allowlist 在 network=allowlist 时必须非空`,
      );
    }
  }
}

/** 取首个非根 instancePath（如 /filesystem、/secrets），根级错误回退 "<unknown>"。 */
function firstErrorField(errors: Array<{ instancePath?: string }>): string {
  for (const error of errors) {
    const path = (error.instancePath ?? "").replace(/^\//, "");
    if (path) return path;
  }
  return "<unknown>";
}
