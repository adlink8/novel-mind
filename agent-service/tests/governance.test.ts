/**
 * governance.test.ts（25.3-02 / D-04..D-06 对抗矩阵）。
 *
 * 覆盖：
 *  - lockfile.ts（D-04 启动校验）：缺条目 / reject-but-installed / integrity 不一致 /
 *    缺 qualification_report / @earendil-works 多版本 → LockfileVerificationError 指名包+字段。
 *  - permission-manifest.ts（D-05）：缺清单 / network allowlist 空 / filesystem=read /
 *    shell/env 非 deny / secrets=any / 未知字段 → PermissionManifestError 指名包+字段；
 *    installed:false 跳过。
 *  - tool-registry-manifest.ts（D-06）：7 个域工具条目 / schema_hash 稳定+漂移检出 /
 *    collision 门（域×技能、域×扩展、技能×扩展）指名双方 provider / false-positive guard /
 *    mcp 代理条目按锁使能。
 *  - 会话工厂：allowlist 源自 manifest；禁用条目不进会话；逃逸拒绝（drift-guard）。
 *  - runGovernanceChain：合法锁 → 7 域工具；已装扩展无清单 → 构建前拒绝。
 *  - 进程级 fail-closed：子进程以真实 TS 源码转译后运行投毒配置 → 非零退出 + 指名错误。
 *
 * 说明：模块顶层 vi.mock 了 @earendil-works/pi-coding-agent 的 createAgentSession
 *（供会话工厂用例）；governance 模块经 spread actual 仍使用真实 defineTool。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import {
  verifyLockfile,
  LockfileVerificationError,
  type GovernanceLock,
  type GovernanceLockEntry,
  type PermissionManifest,
} from "../src/governance/lockfile.js";
import {
  validatePermissionManifests,
  PermissionManifestError,
} from "../src/governance/permission-manifest.js";
import {
  buildToolRegistryManifest,
  ToolCollisionError,
  ToolSchemaDriftError,
  domainToolEntries,
  skillToolEntries,
  extensionToolEntries,
  mcpProxyEntry,
  schemaHash,
  canonicalizeSchema,
  verifySchemaHashes,
  type ToolRegistryEntry,
} from "../src/governance/tool-registry-manifest.js";
import { runGovernanceChain } from "../src/server.js";
import { createControlledAgentDir } from "../src/agent/resource-loader.js";
import { loadSkill, type LoadedSkill } from "../src/skills/loader.js";

// ────────────────────────── 测试辅助 ──────────────────────────

const VALID_PM: PermissionManifest = {
  network: "deny",
  network_allowlist: [],
  filesystem: "deny",
  shell: "deny",
  env: "deny",
  secrets: "named-only",
  tools: [],
  artifact_writes: [],
};

/** 一个合法的 manifest 条目（adopt + D-05 完整）。 */
function baselineEntry(overrides: Partial<GovernanceLockEntry> = {}): GovernanceLockEntry {
  return {
    name: "dep-a",
    version: "1.0.0",
    source: "npm",
    integrity: "sha512-good",
    verdict: "adopt",
    installed: true,
    qualification_report: "qualification/dep-a.md",
    permission_manifest: { ...VALID_PM },
    ...overrides,
  };
}

/** 写入临时 fixture 目录（含子目录），返回目录路径。 */
function writeFixtureDir(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "nm-gov-"));
  for (const [rel, content] of Object.entries(files)) {
    const abs = join(dir, rel);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, content, "utf8");
  }
  return dir;
}

/** 构造一套合法的 lockfile fixture 文件（可注入覆写）。 */
function buildFixtureFiles(opts: {
  pkgDeps?: Record<string, string>;
  lockEntries?: GovernanceLockEntry[];
  extraNpmPackages?: Record<string, Record<string, unknown>>;
  lockTop?: Record<string, unknown>;
} = {}): Record<string, string> {
  const pkgDeps = opts.pkgDeps ?? { "dep-a": "1.0.0" };
  const lockEntries = opts.lockEntries ?? [baselineEntry()];
  return {
    "package.json": JSON.stringify({ name: "root", dependencies: pkgDeps }),
    "package-lock.json": JSON.stringify({
      name: "root",
      lockfileVersion: 3,
      packages: {
        "": {},
        "node_modules/dep-a": { version: "1.0.0", integrity: "sha512-good" },
        ...(opts.extraNpmPackages ?? {}),
      },
    }),
    "packages.lock.json": JSON.stringify({
      version: 1,
      generated_by: "25.3-01",
      packages: lockEntries,
      ...(opts.lockTop ?? {}),
    }),
    "qualification/dep-a.md": "# dep-a 资格报告",
  };
}

/** 构建 + 落盘 fixture，返回目录与三个路径。 */
function fixturePaths(opts: Parameters<typeof buildFixtureFiles>[0] = {}): {
  dir: string;
  paths: {
    packagesLockPath: string;
    packageLockPath: string;
    packageJsonPath: string;
  };
} {
  const dir = writeFixtureDir(buildFixtureFiles(opts));
  return {
    dir,
    paths: {
      packagesLockPath: join(dir, "packages.lock.json"),
      packageLockPath: join(dir, "package-lock.json"),
      packageJsonPath: join(dir, "package.json"),
    },
  };
}

function lockWithPackages(packages: GovernanceLockEntry[]): GovernanceLock {
  return { version: 1, generated_by: "25.3-01", packages };
}

/** 断言抛错类型 + 消息命中（fail-closed 必须指名包/字段）。 */
function expectGovError(
  fn: () => unknown,
  ErrorClass: { new (...args: never[]): Error },
  pattern: RegExp,
): void {
  let thrown: unknown;
  try {
    fn();
  } catch (err) {
    thrown = err;
  }
  expect(thrown).toBeInstanceOf(ErrorClass);
  expect(String(thrown)).toMatch(pattern);
}

const fixtureDirs: string[] = [];
function track(dir: string): void {
  fixtureDirs.push(dir);
}

// ────────────────────────── Task 1: lockfile 校验器 ──────────────────────────

describe("governance/lockfile.ts（D-04 启动校验，fail-closed）", () => {
  afterEach(() => {
    // fixture 目录位于系统 temp，交由 OS 清理（沙箱删除守卫限制 bulk delete）。
  });

  it("lockfile: 合法 fixture 通过并返回 GovernanceLock", () => {
    const { dir, paths } = fixturePaths();
    track(dir);
    const lock = verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath);
    expect(lock.version).toBe(1);
    expect(lock.packages).toHaveLength(1);
    expect(lock.packages[0].name).toBe("dep-a");
  });

  it("lockfile: 已装 runtime 依赖缺清单条目 → 拒绝并指名包", () => {
    const { dir, paths } = fixturePaths({ pkgDeps: { "dep-a": "1.0.0", "dep-b": "2.0.0" } });
    track(dir);
    expectGovError(
      () => verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath),
      LockfileVerificationError,
      /dep-b/,
    );
  });

  it("lockfile: installed=true 且 verdict=reject → 拒绝", () => {
    const { dir, paths } = fixturePaths({
      lockEntries: [baselineEntry({ verdict: "reject" })],
    });
    track(dir);
    expectGovError(
      () => verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath),
      LockfileVerificationError,
      /dep-a.*reject/s,
    );
  });

  it("lockfile: integrity 与 package-lock.json 不一致 → 拒绝", () => {
    const { dir, paths } = fixturePaths({
      lockEntries: [baselineEntry({ integrity: "sha512-bad" })],
    });
    track(dir);
    expectGovError(
      () => verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath),
      LockfileVerificationError,
      /dep-a.*integrity/s,
    );
  });

  it("lockfile: adopt 条目缺 qualification_report 文件 → 拒绝", () => {
    const { dir, paths } = fixturePaths({
      lockEntries: [baselineEntry({ qualification_report: "qualification/missing.md" })],
    });
    track(dir);
    expectGovError(
      () => verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath),
      LockfileVerificationError,
      /dep-a.*qualification_report/s,
    );
  });

  it("lockfile: @earendil-works/* 闭包内多版本 → 拒绝（Pitfall 3）", () => {
    const { dir, paths } = fixturePaths({
      extraNpmPackages: {
        "node_modules/@earendil-works/pi-ai": { version: "0.83.0", integrity: "sha512-a" },
        "node_modules/foo/node_modules/@earendil-works/pi-ai": { version: "0.82.0", integrity: "sha512-b" },
      },
    });
    track(dir);
    expectGovError(
      () => verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath),
      LockfileVerificationError,
      /@earendil-works\/pi-ai.*多个版本/s,
    );
  });
});

// ────────────────────────── Task 1: permission-manifest 校验器 ──────────────────────────

describe("governance/permission-manifest.ts（D-05 ajv，fail-closed）", () => {
  it("permission: 合法 installed 清单通过；installed:false 跳过", () => {
    expect(() => validatePermissionManifests(lockWithPackages([baselineEntry()]))).not.toThrow();
    const patternOnly = baselineEntry({
      installed: false,
      verdict: "pattern-only",
      permission_manifest: null,
    });
    expect(() => validatePermissionManifests(lockWithPackages([patternOnly]))).not.toThrow();
  });

  it("permission: installed 包缺 permission_manifest → 拒绝指名包+字段", () => {
    const entry = baselineEntry({ permission_manifest: null });
    expectGovError(
      () => validatePermissionManifests(lockWithPackages([entry])),
      PermissionManifestError,
      /dep-a.*permission_manifest/s,
    );
  });

  it("permission: network=allowlist 但 network_allowlist 空 → 拒绝指名字段", () => {
    const entry = baselineEntry({
      permission_manifest: { ...VALID_PM, network: "allowlist", network_allowlist: [] } as unknown as PermissionManifest,
    });
    expectGovError(
      () => validatePermissionManifests(lockWithPackages([entry])),
      PermissionManifestError,
      /network_allowlist/,
    );
  });

  it("permission: filesystem=read / shell=allow / env=read / secrets=any → 各自拒绝指名字段", () => {
    const badFs = baselineEntry({ permission_manifest: { ...VALID_PM, filesystem: "read" } as unknown as PermissionManifest });
    const badShell = baselineEntry({ permission_manifest: { ...VALID_PM, shell: "allow" } as unknown as PermissionManifest });
    const badEnv = baselineEntry({ permission_manifest: { ...VALID_PM, env: "read" } as unknown as PermissionManifest });
    const badSecrets = baselineEntry({ permission_manifest: { ...VALID_PM, secrets: "any" } as unknown as PermissionManifest });
    expectGovError(() => validatePermissionManifests(lockWithPackages([badFs])), PermissionManifestError, /filesystem/);
    expectGovError(() => validatePermissionManifests(lockWithPackages([badShell])), PermissionManifestError, /shell/);
    expectGovError(() => validatePermissionManifests(lockWithPackages([badEnv])), PermissionManifestError, /env/);
    expectGovError(() => validatePermissionManifests(lockWithPackages([badSecrets])), PermissionManifestError, /secrets/);
  });

  it("permission: 未知多余字段（additionalProperties:false）→ 拒绝", () => {
    const entry = baselineEntry({ permission_manifest: { ...VALID_PM, extra: true } as unknown as PermissionManifest });
    expectGovError(
      () => validatePermissionManifests(lockWithPackages([entry])),
      PermissionManifestError,
      /dep-a.*permission_manifest/s,
    );
  });
});

// ────────────────────────── Task 2: ToolRegistryManifest 构建 + 碰撞门 ──────────────────────────

describe("governance/tool-registry-manifest.ts（D-06 构建 + collision 门）", () => {
  it("manifest: domainToolEntries 恰 23 个域工具，字段完整", () => {
    const entries = domainToolEntries();
    expect(entries).toHaveLength(23);
    expect(entries.map((e) => e.tool_name)).toEqual([
      "get_novel",
      "get_chapter",
      "search_novel_text",
      "get_timeline",
      "get_relationships",
      "get_clues",
      "get_narrative_memory",
      "get_events",
      "get_character_state",
      "get_character_knowledge",
      "get_world_rules",
      "get_evidence_span",
      "get_visual_bible",
      "generate_image_candidate",
      "publish_illustration",
      "attach_illustration_to_text",
      "create_canon_fork",
      "apply_derivative_edit",
      "allow_divergence",
      "publish_derivative_revision",
      "publish_derivative_visual",
      "approve_export",
      "materialize_export",
    ]);
    for (const entry of entries) {
      expect(entry.provider_package).toBe("agent-service");
      expect(entry.domain).toBe("novel-read");
      expect(entry.permission).toBe("allow");
      expect(entry.enabled).toBe(true);
      expect(entry.schema_hash).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it("manifest: canonicalizeSchema 键序无关，schemaHash 稳定且随内容漂移", () => {
    const schemaA = { type: "object", properties: { novel_id: { type: "integer" } } };
    const schemaB = { properties: { novel_id: { type: "integer" } }, type: "object" }; // 键序打乱
    expect(canonicalizeSchema(schemaA)).toBe(canonicalizeSchema(schemaB));
    expect(schemaHash(schemaA)).toBe(schemaHash(schemaB));
    expect(schemaHash(schemaA)).toBe(schemaHash(schemaA));
    // 升级走私新参数 → 哈希不同（启动时 drift 检出）
    const mutated = { type: "object", properties: { novel_id: { type: "integer" }, stealth: { type: "string" } } };
    expect(schemaHash(mutated)).not.toBe(schemaHash(schemaA));
  });

  it("drift: 记录哈希 ≠ 启动重哈希 → ToolSchemaDriftError 指名工具（升级改 schema 检出）", () => {
    const entries: ToolRegistryEntry[] = [
      {
        tool_name: "get_novel",
        provider_package: "agent-service",
        schema_hash: schemaHash({ type: "object", properties: { novel_id: { type: "integer" } } }),
        permission: "allow",
        domain: "novel-read",
        enabled: true,
      },
    ];
    // 升级改动后的 schema 哈希 ≠ 记录哈希
    const upgraded = schemaHash({ type: "object", properties: { novel_id: { type: "integer" }, stealth: { type: "string" } } });
    const mutatedEntries = [{ ...entries[0], schema_hash: upgraded }];
    expectGovError(
      () => verifySchemaHashes(mutatedEntries, { get_novel: entries[0].schema_hash }),
      ToolSchemaDriftError,
      /schema drift.*get_novel/s,
    );
    // 哈希一致 → 通过
    expect(() => verifySchemaHashes(entries, { get_novel: entries[0].schema_hash })).not.toThrow();
  });

  it("collision: 域源 × 技能源同名工具 → ToolCollisionError 指名双方 provider", () => {
    const lock: GovernanceLock = {
      version: 1,
      generated_by: "25.3-01",
      packages: [],
      skills: [{ name: "evil-skill", tools: [{ tool_name: "get_novel" }] }],
    };
    const skill = skillToolEntries(lock);
    expect(skill).toHaveLength(1);
    expect(skill[0].provider_package).toBe("skill:evil-skill");
    let thrown: unknown;
    try {
      buildToolRegistryManifest([domainToolEntries(), skill]);
    } catch (err) {
      thrown = err;
    }
    expect(thrown).toBeInstanceOf(ToolCollisionError);
    expect(String(thrown)).toMatch(/get_novel/);
    expect(String(thrown)).toMatch(/agent-service/);
    expect(String(thrown)).toMatch(/skill:evil-skill/);
  });

  it("collision: 域源 × 扩展源同名工具 → 拒绝，绝不静默按加载顺序覆盖", () => {
    const lock: GovernanceLock = {
      version: 1,
      generated_by: "25.3-01",
      packages: [],
      extensions: [{ name: "evil-ext", tools: [{ tool_name: "get_chapter" }] }],
    };
    expect(() => buildToolRegistryManifest([domainToolEntries(), extensionToolEntries(lock)]))
      .toThrowError(/get_chapter.*agent-service.*extension:evil-ext/s);
  });

  it("collision: 技能源 × 扩展源同名工具 → 拒绝", () => {
    const lock: GovernanceLock = {
      version: 1,
      generated_by: "25.3-01",
      packages: [],
      skills: [{ name: "s1", tools: [{ tool_name: "web_search" }] }],
      extensions: [{ name: "e1", tools: [{ tool_name: "web_search" }] }],
    };
    expect(() => buildToolRegistryManifest([skillToolEntries(lock), extensionToolEntries(lock)]))
      .toThrowError(/web_search.*skill:s1.*extension:e1/s);
  });

  it("false-positive guard: 相似但不同名的工具正常启动（不误杀）", () => {
    const lock: GovernanceLock = {
      version: 1,
      generated_by: "25.3-01",
      packages: [],
      extensions: [
        {
          name: "novel-tools",
          tools: [
            { tool_name: "get_novel_timeline", schema: { type: "object", properties: { novel_id: { type: "integer" } } } },
            { tool_name: "get_novel_summary", schema: { type: "object" } },
          ],
        },
      ],
    };
    const manifest = buildToolRegistryManifest([domainToolEntries(), extensionToolEntries(lock)]);
    expect(manifest).toHaveLength(25);
    expect(manifest.map((e) => e.tool_name)).toContain("get_novel_timeline");
    expect(manifest.map((e) => e.tool_name)).toContain("get_novel_summary");
  });

  it("mcp 代理条目: 字段完整；锁未启用 MCP 时不加入（Pitfall 4 命名空间）", () => {
    const proxy = mcpProxyEntry();
    expect(proxy).toMatchObject({
      tool_name: "mcp",
      provider_package: "pi-mcp-adapter",
      domain: "external-research",
      permission: "ask",
      enabled: true,
    });
    expect(proxy.schema_hash).toMatch(/^[0-9a-f]{64}$/);
    const lock: GovernanceLock = { version: 1, generated_by: "25.3-01", packages: [] };
    const manifest = buildToolRegistryManifest([
      domainToolEntries(),
      skillToolEntries(lock),
      extensionToolEntries(lock),
      lock.mcp?.enabled ? [proxy] : [],
    ]);
    expect(manifest.map((e) => e.tool_name)).not.toContain("mcp");
    // 锁启用 MCP → 加入代理条目
    const enabledLock: GovernanceLock = {
      version: 1,
      generated_by: "25.3-01",
      packages: [],
      mcp: { enabled: true },
    };
    const manifest2 = buildToolRegistryManifest([
      domainToolEntries(),
      skillToolEntries(enabledLock),
      extensionToolEntries(enabledLock),
      enabledLock.mcp?.enabled ? [proxy] : [],
    ]);
    expect(manifest2.map((e) => e.tool_name)).toContain("mcp");
    expect(manifest2).toHaveLength(24);
  });
});

// ────────────────────────── Task 2: 启动治理链 ──────────────────────────

describe("启动治理链 runGovernanceChain（server.ts，先于 listen）", () => {
  afterEach(() => {
    // fixture 目录位于系统 temp，交由 OS 清理（沙箱删除守卫限制 bulk delete）。
  });

  it("chain: 合法锁 → 返回含 23 个域工具的 manifest", () => {
    const { dir, paths } = fixturePaths();
    track(dir);
    const manifest = runGovernanceChain(paths);
    expect(manifest).toHaveLength(23);
    expect(manifest.map((e) => e.tool_name)).toEqual([
      "get_novel",
      "get_chapter",
      "search_novel_text",
      "get_timeline",
      "get_relationships",
      "get_clues",
      "get_narrative_memory",
      "get_events",
      "get_character_state",
      "get_character_knowledge",
      "get_world_rules",
      "get_evidence_span",
      "get_visual_bible",
      "generate_image_candidate",
      "publish_illustration",
      "attach_illustration_to_text",
      "create_canon_fork",
      "apply_derivative_edit",
      "allow_divergence",
      "publish_derivative_revision",
      "publish_derivative_visual",
      "approve_export",
      "materialize_export",
    ]);
  });

  it("chain: 已装扩展无 permission_manifest → manifest 构建前拒绝，指名包+字段", () => {
    const { dir, paths } = fixturePaths({
      extraNpmPackages: {
        "node_modules/evil-ext": { version: "0.1.0", integrity: "sha512-ext" },
      },
      lockEntries: [
        baselineEntry(),
        {
          name: "evil-ext",
          version: "0.1.0",
          source: "npm",
          integrity: "sha512-ext",
          verdict: "adopt",
          installed: true,
          qualification_report: "qualification/evil-ext.md",
          permission_manifest: null,
        },
      ],
    });
    // 补 evil-ext 资格报告（lockfile 层通过），让失败点精确落在 D-05 permission 校验
    writeFileSync(join(dir, "qualification/evil-ext.md"), "# evil-ext", "utf8");
    track(dir);
    expectGovError(() => runGovernanceChain(paths), PermissionManifestError, /evil-ext.*permission_manifest/s);
  });

  it("chain: 技能重声明域工具名 → collision 拒绝（进程启动场景的代码路径）", () => {
    const { dir, paths } = fixturePaths({
      lockTop: { skills: [{ name: "evil-skill", tools: [{ tool_name: "get_novel" }] }] },
    });
    track(dir);
    expectGovError(
      () => runGovernanceChain(paths),
      ToolCollisionError,
      /get_novel.*agent-service.*skill:evil-skill/s,
    );
  });

  it("chain: 记录哈希与重哈希不符 → schema drift 拒绝", () => {
    const { dir, paths } = fixturePaths();
    track(dir);
    expectGovError(
      () => runGovernanceChain({ ...paths, expectedSchemaHashes: { get_novel: "0".repeat(64) } }),
      ToolSchemaDriftError,
      /get_novel/,
    );
  });
});

// ────────────────────────── Task 2: 会话工厂消费 manifest（D-06 单一源） ──────────────────────────

const mocks = vi.hoisted(() => ({
  createAgentSession: vi.fn(),
}));

vi.mock("@earendil-works/pi-coding-agent", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@earendil-works/pi-coding-agent")>();
  return { ...actual, createAgentSession: mocks.createAgentSession };
});

const { createSession } = await import("../src/agent/session-factory.js");

function fakeSession(): AgentSession {
  return {
    sessionId: "session-1",
    messages: [],
    isStreaming: false,
    isIdle: true,
    systemPrompt: "",
    getAllTools: vi.fn(() => []),
    getToolDefinition: vi.fn(() => undefined),
    getActiveToolNames: vi.fn(() => []),
    setActiveToolsByName: vi.fn(),
    subscribe: vi.fn(() => () => {}),
    prompt: vi.fn(async () => {}),
    abort: vi.fn(),
    steer: vi.fn(async () => {}),
    sendCustomMessage: vi.fn(async () => {}),
  } as unknown as AgentSession;
}

function fakeSkill(allowedTools: string[]): LoadedSkill {
  return {
    name: "fixture-skill",
    version: "1.0.0",
    description: "fixture",
    allowedTools,
    readPermissions: [],
    writePermissions: [],
    forbiddenSpaces: [],
    budget: {},
    approvalRequiredFor: [],
    filePath: "<fixture>/skill.yaml",
    baseDir: "<fixture>",
    instructions: "fixture instructions",
    validateInput: vi.fn(() => true) as unknown as LoadedSkill["validateInput"],
    validateOutput: vi.fn(() => true) as unknown as LoadedSkill["validateOutput"],
  };
}

/** 成功路径用：复用真实技能资产的磁盘路径（buildResourceLoader 会 stat filePath）。 */
function fakeSkillOnDisk(allowedTools: string[]): LoadedSkill {
  const real = loadSkill("answer-reading-question");
  return { ...real, name: "fixture-skill", allowedTools };
}

describe("会话工厂从 manifest 派生 allowlist（drift-guard / T-25.3-02-04）", () => {
  beforeEach(() => {
    mocks.createAgentSession.mockReset();
    mocks.createAgentSession.mockResolvedValue({ session: fakeSession() });
  });

  it("factory: allowlist 源自 manifest（缺省 = 域工具清单全启用）", async () => {
    const skill = fakeSkillOnDisk(["get_novel", "get_chapter"]);
    await createSession({
      novelId: 1,
      auth: "Bearer t",
      skill,
      modelRuntime: {} as never,
      dirs: createControlledAgentDir(),
    });
    const options = mocks.createAgentSession.mock.calls[0][0] as Record<string, unknown>;
    expect(options.tools).toEqual(["get_novel", "get_chapter"]);
  });

  it("factory: 禁用条目不进入会话 allowlist（registry↔manifest↔session 不漂移）", async () => {
    const manifest: ToolRegistryEntry[] = domainToolEntries().map((entry) =>
      entry.tool_name === "get_novel" ? { ...entry, enabled: false } : entry,
    );
    // 技能只声明启用条目；禁用条目（get_novel）绝不进入会话 tools
    const skill = fakeSkillOnDisk(["get_chapter"]);
    await createSession({
      novelId: 1,
      auth: "Bearer t",
      skill,
      modelRuntime: {} as never,
      dirs: createControlledAgentDir(),
      manifest,
    });
    const options = mocks.createAgentSession.mock.calls[0][0] as Record<string, unknown>;
    expect(options.tools).toEqual(["get_chapter"]);
    expect(options.tools).not.toContain("get_novel");
  });

  it("factory: skill 引用禁用/未知条目 → 会话创建前拒绝（fail-closed）", async () => {
    const manifest: ToolRegistryEntry[] = domainToolEntries().map((entry) =>
      entry.tool_name === "get_novel" ? { ...entry, enabled: false } : entry,
    );
    const skill = fakeSkillOnDisk(["get_novel", "get_chapter"]);
    await expect(
      createSession({
      novelId: 1,
      auth: "Bearer t",
        skill,
        modelRuntime: {} as never,
        dirs: createControlledAgentDir(),
        manifest,
      }),
    ).rejects.toThrow(/get_novel/);
    expect(mocks.createAgentSession).not.toHaveBeenCalled();
  });

  it("factory: skill allowed_tools 逃出启用清单（含禁用条目）→ 会话创建前拒绝", async () => {
    const manifest: ToolRegistryEntry[] = domainToolEntries().filter(
      (entry) => entry.tool_name === "get_novel",
    );
    const skill = fakeSkill(["get_novel", "get_secret_ext"]);
    await expect(
      createSession({
      novelId: 1,
      auth: "Bearer t",
        skill,
        modelRuntime: {} as never,
        dirs: createControlledAgentDir(),
        manifest,
      }),
    ).rejects.toThrow(/get_secret_ext/);
    expect(mocks.createAgentSession).not.toHaveBeenCalled();
  });
});

// ────────────────────────── Task 3: 进程级 fail-closed 证明 ──────────────────────────

/** 把真实 TS 源码转译成 ESM .mjs（Node 不做 .js→.ts 说明符重写，这里补上）。 */
function transpileSource(source: string, fileName: string): string {
  const out = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName,
  });
  return out.outputText.replace(
    /(\bfrom\s+["'])(\.{1,2}\/[^"']*?\.js)(["'])/g,
    (_m, p1: string, p2: string, p3: string) => p1 + p2.replace(/\.js$/, ".mjs") + p3,
  );
}

/** 把治理链所需 TS 源转译到临时目录（置于 agent-service 内以命中 node_modules 解析）。 */
function transpileGovernanceTo(dir: string): void {
  const AGENT_SERVICE = fileURLToPath(new URL("../", import.meta.url));
  const sources = [
    "src/config.ts",
    "src/tools/fastapi-client.ts",
    "src/tools/registry.ts",
    "src/governance/lockfile.ts",
    "src/governance/permission-manifest.ts",
    "src/governance/tool-registry-manifest.ts",
  ];
  // 域工具拆分（tools/domain/*）是 registry 的构建依赖，必须一并转译。
  for (const entry of readdirSync(join(AGENT_SERVICE, "src/tools/domain"))) {
    if (entry.endsWith(".ts")) {
      sources.push(`src/tools/domain/${entry}`);
    }
  }
  for (const rel of sources) {
    const source = readFileSync(join(AGENT_SERVICE, rel), "utf8");
    const outPath = join(dir, rel.replace(/\.ts$/, ".mjs"));
    mkdirSync(dirname(outPath), { recursive: true });
    writeFileSync(outPath, transpileSource(source, rel), "utf8");
  }
}

const PROCESS_HARNESS = String.raw`
// 进程级 fail-closed 启动治理链 harness（governance.test.ts 动态生成）。
// 全程不 catch：治理失败 = uncaught → node 非零退出（先于 listen 的等价路径）。
import { verifyLockfile } from "./src/governance/lockfile.mjs";
import { validatePermissionManifests } from "./src/governance/permission-manifest.mjs";
import {
  buildToolRegistryManifest,
  domainToolEntries,
  skillToolEntries,
  extensionToolEntries,
  mcpProxyEntry,
  verifySchemaHashes,
} from "./src/governance/tool-registry-manifest.mjs";

const [, , pkgLock, npmLock, pkgJson, expectedHashes] = process.argv;

const lock = verifyLockfile(pkgLock, npmLock, pkgJson);
validatePermissionManifests(lock);
const manifest = buildToolRegistryManifest([
  domainToolEntries(),
  skillToolEntries(lock),
  extensionToolEntries(lock),
  lock.mcp && lock.mcp.enabled ? [mcpProxyEntry()] : [],
]);
if (expectedHashes && expectedHashes !== "none") {
  verifySchemaHashes(manifest, JSON.parse(expectedHashes));
}
console.log("GOVERNANCE_OK", manifest.length);
`;

/** 转译 + 落盘 harness，返回工作目录（必须置于 agent-service 内部以命中 node_modules）。 */
/** 转译 + 落盘 harness，返回固定工作目录（幂等覆写，无需删除；gitignore 管理）。 */
function createProcessHarness(): string {
  const AGENT_SERVICE = fileURLToPath(new URL("../", import.meta.url));
  const work = join(AGENT_SERVICE, "tests", "fixtures", ".gov-tmp");
  mkdirSync(work, { recursive: true });
  transpileGovernanceTo(work);
  writeFileSync(join(work, "poison-chain.mjs"), PROCESS_HARNESS, "utf8");
  return work;
}

describe("进程级 fail-closed（投毒配置 → 非零退出 + 指名错误）", () => {
  afterEach(() => {
    // fixture 目录位于系统 temp，交由 OS 清理（沙箱删除守卫限制 bulk delete）。
  });

  it("(a) 技能重声明 get_novel → 进程非零退出，错误指名双方 provider", () => {
    const work = createProcessHarness();
    const { dir, paths } = fixturePaths({
      lockTop: { skills: [{ name: "evil-skill", tools: [{ tool_name: "get_novel" }] }] },
    });
    track(dir);

    const result = spawnSync(
      process.execPath,
      [join(work, "poison-chain.mjs"), paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath, "none"],
      { encoding: "utf8" },
    );
    expect(result.status).not.toBe(0);
    const output = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
    expect(output).toMatch(/get_novel/);
    expect(output).toMatch(/agent-service/);
    expect(output).toMatch(/skill:evil-skill/);
  });

  it("(b) 已装扩展无 permission_manifest → 进程非零退出，错误指名包+字段", () => {
    const work = createProcessHarness();
    const { dir, paths } = fixturePaths({
      extraNpmPackages: {
        "node_modules/evil-ext": { version: "0.1.0", integrity: "sha512-ext" },
      },
      lockEntries: [
        baselineEntry(),
        {
          name: "evil-ext",
          version: "0.1.0",
          source: "npm",
          integrity: "sha512-ext",
          verdict: "adopt",
          installed: true,
          qualification_report: "qualification/evil-ext.md",
          permission_manifest: null,
        },
      ],
    });
    writeFileSync(join(dir, "qualification/evil-ext.md"), "# evil-ext", "utf8");
    track(dir);

    const result = spawnSync(
      process.execPath,
      [join(work, "poison-chain.mjs"), paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath, "none"],
      { encoding: "utf8" },
    );
    expect(result.status).not.toBe(0);
    const output = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
    expect(output).toMatch(/evil-ext/);
    expect(output).toMatch(/permission_manifest/);
  });

  it("(c) schema drift（记录哈希 ≠ 重哈希）→ 进程非零退出，错误指名工具", () => {
    const work = createProcessHarness();
    const { dir, paths } = fixturePaths();
    track(dir);

    const result = spawnSync(
      process.execPath,
      [join(work, "poison-chain.mjs"), paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath, JSON.stringify({ get_novel: "0".repeat(64) })],
      { encoding: "utf8" },
    );
    expect(result.status).not.toBe(0);
    const output = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
    expect(output).toMatch(/schema drift/);
    expect(output).toMatch(/get_novel/);
  });

  it("(d) 诚实配置 → 治理链通过，进程存活（GOVERNANCE_OK 23，exit 0）", () => {
    const work = createProcessHarness();
    const { dir, paths } = fixturePaths();
    track(dir);

    const result = spawnSync(
      process.execPath,
      [join(work, "poison-chain.mjs"), paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath, "none"],
      { encoding: "utf8" },
    );
    expect(result.status).toBe(0);
    expect(`${result.stdout ?? ""}`).toMatch(/GOVERNANCE_OK 23/);
  });
});
