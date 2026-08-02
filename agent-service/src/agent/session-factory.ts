/**
 * 会话工厂（25.2-05 / D-05 / D-18 / REQ-AGENT-01）。
 *
 * 每次创建会话都强制 spike-DECISION 的规范形状：
 * `noTools: "all"` + `customTools`（7 个域工具，携带 per-run 内部令牌）+
 * `tools` 显式 allowlist（skill 的 allowed_tools 在 ToolRegistryManifest「启用」条目内的
 * 子集，25.3-02 / D-06 单一 allowlist 源）+ 完全覆写的 ResourceLoader
 * （零 coding tools、零 ambient resources，A2）。
 *
 * 关键 spike 发现（FINDINGS #1）：`noTools:"all"` 使 allowedToolNames=[] 为 truthy，
 * customTools 必须显式列进 `tools` 才注册——因此 `tools` 永不缺席、永不为空数组。
 *
 * 存储策略（DECISION go-fallback）：会话按 run 存于内存（SessionManager.inMemory()），
 * 全部持久状态在 skill_runs / 产物（FastAPI 侧）；本工厂不接任何会话存储适配器。
 *
 * 技能指令经 ResourceLoader `systemPrompt` 确定性注入（spike 07 证明的 seam），
 * 绝不靠 Pi skill discovery（Pitfall 3）。
 */

import {
  createAgentSession,
  SessionManager,
  type AgentSession,
  type ModelRuntime,
  type Skill,
} from "@earendil-works/pi-coding-agent";
import { buildDomainTools } from "../tools/registry.js";
import { domainToolEntries, type ToolRegistryEntry } from "../governance/tool-registry-manifest.js";
import { skillInstructions } from "../skills/loader.js";
import type { LoadedSkill } from "../skills/loader.js";
import {
  NOVEL_AGENT_SYSTEM_PROMPT,
  buildResourceLoader,
  createControlledAgentDir,
} from "./resource-loader.js";
import { buildGatewayModel, buildGatewayModelRuntime } from "./provider.js";

/** 把 fail-closed 校验后的 LoadedSkill 转成 Pi Skill（供 ResourceLoader override）。 */
export function toPiSkill(skill: LoadedSkill): Skill {
  return {
    name: skill.name,
    description: skill.description,
    filePath: skill.filePath,
    baseDir: skill.baseDir,
    sourceInfo: {
      path: skill.filePath,
      source: "allowlist",
      scope: "temporary",
      origin: "top-level",
    },
    // 指令已注入 systemPrompt；禁止模型自行调用/发现技能（Pitfall 3）。
    disableModelInvocation: true,
  };
}

/** 构造会话选项。 */
export interface CreateSessionOptions {
  /** 运行级授权：FastAPI 铸造的 per-run 内部令牌（工具门面转发，绝不落日志）。 */
  auth: string;
  /** 已 fail-closed 校验的激活技能（loader 产出）。 */
  skill: LoadedSkill;
  /**
   * 会话存储策略（DECISION go-fallback）：不接任何适配器，会话按 run 存于内存。
   * 保留参数位以兼容 go-pg-adapter 分支，但 fallback 模式下恒为 undefined。
   */
  storage?: unknown;
  /** ModelRuntime 注入点（测试可传 mock；生产默认构建网关 runtime）。 */
  modelRuntime?: ModelRuntime;
  /** 受控 cwd/agentDir（默认新建临时空目录；测试可注入）。 */
  dirs?: { cwd: string; agentDir: string };
  /**
   * ToolRegistryManifest（25.3-02 / D-06 单一 allowlist 事实源）。会话 tools
   * allowlist 只取自「启用」条目；禁用条目不进入任何会话。缺省回退到域工具清单
   * （7 个域工具全启用）——与 25.2-05 行为一致。
   */
  manifest?: ToolRegistryEntry[];
}

/**
 * 创建生产会话。任何代码路径都不可能以默认 coding tools 创建会话：
 * `noTools: "all"` 恒定，`tools` 恒定非空（allowlist 子集，含全部 7 个域工具的
 * 子集），`customTools` 恒定携带运行级授权。
 */
export async function createSession(opts: CreateSessionOptions): Promise<AgentSession> {
  const { auth, skill } = opts;

  // D-06（25.3-02）：allowlist 唯一来源 = manifest 的启用条目；禁用条目不进会话。
  // manifest 本身已由启动治理链 fail-closed 构建（碰撞门保证无同名覆盖）。
  const enabledToolNames = (opts.manifest ?? domainToolEntries())
    .filter((entry) => entry.enabled)
    .map((entry) => entry.tool_name);

  // 防御纵深（T-25.2-05-05 三重门之一）：allowed_tools 必须 ⊆ 启用清单，
  // loader/manifest 之外的任何逃逸都在会话创建前拒绝。
  for (const tool of skill.allowedTools) {
    if (!enabledToolNames.includes(tool)) {
      throw new Error(`skill ${skill.name} 的 allowed_tools 逃出启用清单: ${tool}`);
    }
  }

  // tools allowlist：skill 的 allowed_tools 在启用清单内的子集，恒定非空。
  const tools = enabledToolNames.filter((name) => skill.allowedTools.includes(name));
  if (tools.length === 0) {
    throw new Error(`skill ${skill.name} 的 allowed_tools 为空，无法注册任何工具（fail-closed）`);
  }

  const dirs = opts.dirs ?? createControlledAgentDir();

  // 技能指令确定性注入 systemPrompt（spike 07 seam）。
  const resourceLoader = await buildResourceLoader(
    [toPiSkill(skill)],
    `${NOVEL_AGENT_SYSTEM_PROMPT}\n\n${skillInstructions(skill)}`,
    dirs,
  );

  const modelRuntime = opts.modelRuntime ?? (await buildGatewayModelRuntime());

  const { session } = await createAgentSession({
    cwd: dirs.cwd,
    agentDir: dirs.agentDir,
    resourceLoader,
    sessionManager: SessionManager.inMemory(),
    noTools: "all",
    tools,
    customTools: buildDomainTools(auth),
    modelRuntime,
    model: buildGatewayModel(),
  });

  return session;
}
