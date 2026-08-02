/**
 * ResourceLoader 完全受控（25.2-05 / D-18 / A2 PROVEN）。
 *
 * 按 spike 03 配方闭掉每一个发现面：
 * - `systemPrompt`：只含 agent-service 注入的 Novel Agent 系统提示 + 当前技能指令
 *   （Pitfall 3 —— 技能指令确定性注入，绝不靠 Pi skill discovery）。
 * - `skillsOverride`：只返回 allowlist 技能（来自 skill loader），零 ambient。
 * - `noPromptTemplates` / `noThemes` / `noContextFiles` / `noExtensions`：清空其余发现面。
 * - 受控空 `agentDir` + 空 `cwd`（临时目录）：任何无法被 override 的表面兜底为空。
 *   A2 = PROVEN：skills/prompts/themes/agentsFiles/extensions 全部 = allowlist 精确。
 */

import {
  DefaultResourceLoader,
  SettingsManager,
  type Skill,
} from "@earendil-works/pi-coding-agent";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/** Novel Agent 基础系统提示（无 coding tools、只读域工具、服务端强制边界）。 */
export const NOVEL_AGENT_SYSTEM_PROMPT = `你是一个嵌入在 NovelMind 中的小说阅读助手（Embedded Novel Agent）。
你只拥有只读域工具（get_novel / get_chapter / search_novel_text / get_timeline /
get_relationships / get_clues / get_narrative_memory），没有任何编码或文件系统工具。
所有 owner 校验、剧透截止点、预算与字节上限都由服务端强制，你无需也无法绕过。
你产出的最终答案必须是基于工具证据的 Cited Answer Artifact（证据引用来自工具返回的
evidence span），并遵守当前激活技能的指令。`;

/**
 * 创建受控的临时 cwd/agentDir（空目录，含空 skills/ 子目录），作为
 * ResourceLoader 的发现根——零 ambient 内容的构造性保证（spike 03 A2 兜底）。
 */
export function createControlledAgentDir(): { cwd: string; agentDir: string } {
  const agentDir = mkdtempSync(join(tmpdir(), "nm-agent-runtime-agent-"));
  const cwd = mkdtempSync(join(tmpdir(), "nm-agent-runtime-cwd-"));
  mkdirSync(join(agentDir, "skills"), { recursive: true });
  mkdirSync(join(cwd, ".pi", "skills"), { recursive: true });
  return { cwd, agentDir };
}

/**
 * 构建完全受控的 ResourceLoader。
 *
 * @param allowlistedSkills 允许加载的技能（skill loader 产出，已是 fail-closed 校验后的
 *   Pi Skill 形状）；`skillsOverride` 只返回它们。
 * @param systemPrompt 注入的系统提示（含当前激活技能的指令）。
 * @param dirs 受控 cwd/agentDir（默认 `createControlledAgentDir()`）。会话工厂会
 *   复用同一对目录传给 createAgentSession，保证 loader 与 session 同根。
 * @returns 已 `reload()` 的 DefaultResourceLoader。
 */
export async function buildResourceLoader(
  allowlistedSkills: Skill[],
  systemPrompt: string,
  dirs: { cwd: string; agentDir: string } = createControlledAgentDir(),
): Promise<DefaultResourceLoader> {
  const { cwd, agentDir } = dirs;
  const loader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager: SettingsManager.create(cwd, agentDir),
    systemPrompt,
    noSkills: false,
    skillsOverride: (base) => ({ skills: allowlistedSkills, diagnostics: base.diagnostics }),
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    noExtensions: true,
    agentsFilesOverride: (base) => base,
    extensionFactories: [],
  });
  // reload 使 override 生效（spike 03 同款流程）。
  await loader.reload();
  return loader;
}
