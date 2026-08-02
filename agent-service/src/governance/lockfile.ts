/**
 * packages.lock.json 启动加载 + 校验（25.3-02 / D-04 / D-05，fail-closed）。
 *
 * 在 listen 之前把治理锁清单（packages.lock.json）与 npm 的 package-lock.json、
 * package.json 逐项对照：每个已装 runtime 依赖必须有非 reject 的清单条目，
 * 已装条目 version+integrity 必须与 package-lock.json 精确一致，adopt 条目必须
 * 存在 qualification_report，@earendil-works/* 闭包内只能有一个版本（Pitfall 3）。
 * 任一违规抛 LockfileVerificationError 指名包与字段，绝不带病启动（无自动修复、
 * 无交互、无默认放行）。
 *
 * permission_manifest 的 D-05 深度校验由 permission-manifest.ts 负责；本模块只
 * 保证锁清单与 npm lockfile 的 pin/verdict/报告一致性。
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

/** D-02 裁决值。 */
export type Verdict = "adopt" | "fork" | "pattern-only" | "reject";

/** D-05 权限清单形状（与 permission-manifest.ts 的 ajv schema 镜像）。 */
export interface PermissionManifest {
  network: "deny" | "allowlist";
  network_allowlist: string[];
  filesystem: "deny";
  shell: "deny";
  env: "deny";
  secrets: "named-only";
  tools: string[];
  artifact_writes: string[];
}

/** packages.lock.json 单条目（RESEARCH Pattern 1 形状）。 */
export interface GovernanceLockEntry {
  name: string;
  version: string;
  source: "npm" | "git";
  integrity: string | null;
  verdict: Verdict;
  scope?: string;
  installed: boolean;
  qualification_report: string | null;
  permission_manifest: PermissionManifest | null;
}

/** 技能/扩展声明的工具（ToolRegistryManifest 的 skill/extension 源；schema 可选）。 */
export interface LockToolDeclaration {
  tool_name: string;
  /** 工具参数 schema（无可用 schema 时用哨兵 {type:"object"} 哈希）。 */
  schema?: unknown;
}

/** 锁中的技能/扩展段条目：provider 为 "skill:<name>" / "extension:<name>"。 */
export interface LockToolProviderSection {
  name: string;
  tools: LockToolDeclaration[];
  enabled?: boolean;
  permission?: "allow" | "ask" | "deny";
  domain?: string;
}

/** 解析后的治理锁对象（manifest 构建器的输入）。 */
export interface GovernanceLock {
  version: number;
  generated_by: string;
  packages: GovernanceLockEntry[];
  /** MCP 使能段（25.3-03 填写）；当前锁无此段 → mcp 代理条目不加入。 */
  mcp?: { enabled?: boolean };
  /** 技能段：技能注册的自有工具（当前锁为空）。 */
  skills?: LockToolProviderSection[];
  /** 扩展段：扩展注册的工具（当前锁为空）。 */
  extensions?: LockToolProviderSection[];
}

/** 锁清单校验失败（fail-closed），消息指名违规包与字段。 */
export class LockfileVerificationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LockfileVerificationError";
  }
}

function readJsonOrThrow(file: string, what: string): unknown {
  let raw: string;
  try {
    raw = readFileSync(file, "utf8");
  } catch (err) {
    throw new LockfileVerificationError(`无法读取 ${what}: ${file} (${(err as Error).message})`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new LockfileVerificationError(`${what} 不是合法 JSON: ${file} (${(err as Error).message})`);
  }
}

/**
 * 启动加载 + 校验治理锁清单。
 *
 * @param packagesLockPath packages.lock.json 路径（默认相对 CWD）。
 * @param packageLockPath  npm package-lock.json 路径。
 * @param packageJsonPath  package.json 路径（已装 runtime 依赖集合）。
 * @returns 校验通过的 GovernanceLock（供 permission-manifest / manifest 构建器消费）。
 * @throws LockfileVerificationError —— 任何不一致先于 listen 拒绝启动。
 */
export function verifyLockfile(
  packagesLockPath = "packages.lock.json",
  packageLockPath = "package-lock.json",
  packageJsonPath = "package.json",
): GovernanceLock {
  const rawLock = readJsonOrThrow(packagesLockPath, "packages.lock.json");
  const rawNpm = readJsonOrThrow(packageLockPath, "package-lock.json");
  const rawPkg = readJsonOrThrow(packageJsonPath, "package.json");
  const lockBaseDir = dirname(resolve(packagesLockPath));

  const lock = rawLock as Partial<GovernanceLock>;
  if (lock.version !== 1) {
    throw new LockfileVerificationError("packages.lock.json: 顶层 version != 1");
  }
  if (!Array.isArray(lock.packages)) {
    throw new LockfileVerificationError("packages.lock.json: 顶层 packages 不是数组");
  }

  const entries = lock.packages as unknown as GovernanceLockEntry[];
  const npmPackages =
    (rawNpm as { packages?: Record<string, { version?: string; integrity?: string }> }).packages ?? {};
  const pkgDeps = (rawPkg as { dependencies?: Record<string, string> }).dependencies ?? {};
  const entryByName = new Map<string, GovernanceLockEntry>();

  for (const entry of entries) {
    entryByName.set(entry.name, entry);

    // 条目基本形状：name/version/source/verdict/installed 必须齐全。
    for (const field of ["name", "version", "source", "verdict", "installed"] as const) {
      if (entry[field] === undefined || entry[field] === null) {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目缺少字段 ${field} (${entry.name ?? "<unnamed>"})`,
        );
      }
    }

    if (entry.installed === true) {
      // D-04：已装条目 verdict 不得为 reject（pattern-only/reject 从不加载）。
      if (entry.verdict === "reject") {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目 ${entry.name} verdict=reject 但 installed=true`,
        );
      }
      // 与 package-lock.json 精确对照（version + integrity，Pitfall 1/3）。
      const lockEntry = npmPackages[`node_modules/${entry.name}`];
      if (!lockEntry) {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目 ${entry.name} installed=true 但 package-lock.json 无对应条目`,
        );
      }
      if (lockEntry.version !== entry.version) {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目 ${entry.name} 版本不一致: manifest=${entry.version}, package-lock=${lockEntry.version}`,
        );
      }
      if (!entry.integrity || entry.integrity !== lockEntry.integrity) {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目 ${entry.name} integrity 不一致或缺失 (manifest=${entry.integrity ?? "<缺失>"})`,
        );
      }
    }

    // D-04：adopt 条目必须引用存在的 qualification_report。
    if (entry.verdict === "adopt") {
      if (!entry.qualification_report) {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目 ${entry.name} adopt 但缺少 qualification_report`,
        );
      }
      if (!existsSync(resolve(lockBaseDir, entry.qualification_report))) {
        throw new LockfileVerificationError(
          `packages.lock.json: 条目 ${entry.name} qualification_report 文件不存在: ${entry.qualification_report}`,
        );
      }
    }
  }

  // D-04：每个已装 runtime 依赖（package.json dependencies）都必须有非 reject 的 installed 条目。
  for (const name of Object.keys(pkgDeps)) {
    const entry = entryByName.get(name);
    if (!entry) {
      throw new LockfileVerificationError(
        `package.json: 已装 runtime 依赖 ${name} 在 packages.lock.json 无条目`,
      );
    }
    if (entry.installed !== true) {
      throw new LockfileVerificationError(
        `package.json: runtime 依赖 ${name} 存在但 installed != true`,
      );
    }
    if (entry.verdict === "reject") {
      throw new LockfileVerificationError(`package.json: runtime 依赖 ${name} verdict=reject`);
    }
  }

  // Pitfall 3：@earendil-works/* 在闭包内只能有一个版本。
  const versions = new Map<string, string>();
  for (const [key, npmEntry] of Object.entries(npmPackages)) {
    const leaf = key.replace(/^node_modules\//, "").split("/node_modules/").pop();
    if (!leaf || !leaf.startsWith("@earendil-works/")) continue;
    const version = npmEntry.version;
    if (!version) continue; // 无版本条目（如根）不参与单版本对照
    if (versions.has(leaf)) {
      if (versions.get(leaf) !== version) {
        throw new LockfileVerificationError(
          `package-lock.json: ${leaf} 闭包内存在多个版本: ${versions.get(leaf)} 与 ${version}`,
        );
      }
    } else {
      versions.set(leaf, version);
    }
  }

  return lock as GovernanceLock;
}
