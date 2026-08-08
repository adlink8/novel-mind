/**
 * 域工具注册表（25.2-05 / D-06 / REQ-AGENT-01；27-05 扩展 Phase 27 世界模型工具）。
 *
 * 本文件现在是拆分后的 re-export 兼容层：工具定义按域拆分到 ``tools/domain/*``
 * （reading / world-model / visual / illustration / canon-derivative /
 * derivative-export），本文件只保留：
 *
 * - ``DOMAIN_TOOL_NAMES`` —— 23 个工具 allowlist 的唯一事实源（会话工厂的
 *   ``tools`` 数组、skill loader 的 allowed_tools 校验都消费它，三者无法漂移）；
 * - ``ToolAuth`` —— 工具注册时的运行级授权类型；
 * - ``buildDomainTools`` —— 按固定顺序拼接各域 builder 并返回完整工具数组。
 *
 * 消费方（session-factory / skills-loader / governance-tool-registry-manifest /
 * tests/registry.test.ts）的 import surface 保持不变。
 */

import { buildCanonDerivativeTools } from "./domain/canon-derivative.js";
import { buildDerivativeExportTools } from "./domain/derivative-export.js";
import { buildIllustrationTools } from "./domain/illustration.js";
import { buildReadingTools } from "./domain/reading.js";
import { buildVisualTools } from "./domain/visual.js";
import { buildWorldModelTools } from "./domain/world-model.js";

/** 23 个域工具名（固定顺序；唯一 allowlist 事实源）。 */
export const DOMAIN_TOOL_NAMES = [
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
] as const;

/** 工具注册时的运行级授权（端用户 JWT 或 per-run 内部令牌）。 */
export type ToolAuth = string;

/**
 * 构建 23 个域工具数组，携带给定的运行级授权（每次 run 分发时以 per-run 内部令牌调用）。
 *
 * 固定拼接顺序必须与 ``DOMAIN_TOOL_NAMES`` 完全一致：reading → world-model →
 * visual → illustration → canon-derivative → derivative-export。
 */
export function buildDomainTools(auth: ToolAuth, runNovelId: number) {
  return [
    ...buildReadingTools(auth, runNovelId),
    ...buildWorldModelTools(auth, runNovelId),
    ...buildVisualTools(auth, runNovelId),
    ...buildIllustrationTools(auth, runNovelId),
    ...buildCanonDerivativeTools(auth, runNovelId),
    ...buildDerivativeExportTools(auth, runNovelId),
  ];
}
