/**
 * guided 执行器（「确定性检索 + 单轮生成」改造 Slice 3）。
 *
 * guided 模式下模型**不接触任何工具与原始 ID/key**：
 *   1. guidedRetrieve 程序化检索 + 物化（工具门面的 owner/cutoff/预算门
 *      照常生效），产出编号证据菜单；
 *   2. 单次网关补全：模型读问题 + 编号摘录菜单，输出语义 JSON
 *      （claims 用 evidence_indices 引用菜单编号）；
 *   3. translateEvidenceIndices 把编号映射回真实 evidence_key（越界 fail
 *      closed），后续投影/信封/finalize 与 agentic 路径完全同构；
 *   4. 有界修复环：校验失败把错误 + 菜单作为后续消息追加，重试补全
 *      （最多 MAX_REPAIR_ROUNDS 轮），超出 fail closed 零写入。
 *
 * 模型能力安全区论证（四模型 E2E 矩阵）：多轮工具编排 + 原始 ID 簿记是
 * flash 级模型的天花板；单轮小上下文、照菜单选编号是所有受测模型的
 * 安全区。成本同时降一个数量级（一次补全 vs 几十次工具调用的全量
 * transcript 重发）。
 */

import { config } from "../config.js";
import { guidedRetrieve, type GuidedRetrieval, type ToolCaller } from "./retrieval.js";
import { translateEvidenceIndices } from "./translate.js";
import { buildAnalysisEnvelope } from "../structured-output/analysis-envelope-builder.js";
import type { RunLineageContext } from "../structured-output/cited-answer-builder.js";
import type { LoadedSkill } from "../skills/loader.js";

type JsonObject = Record<string, unknown>;

/** guided 模式的 per-skill 输出契约（system prompt 规则块 + 输出骨架）。 */
interface GuidedSkillSpec {
  analyzer: string;
  rules: string[];
  skeleton: string;
}

/**
 * guided 模式 skill 注册表。wmc 刻意不在其中：其 claim 需要
 * disclosure_cutoff 等血缘字段，而 wmc run input 当前不做快照锚定——
 * 先锚定再接入，否则等于让模型猜 cutoff。
 */
const GUIDED_SKILLS: Record<string, GuidedSkillSpec> = {
  "build-visual-bible": {
    analyzer: "Visual Bible 分析器",
    rules: [
      "- entities：1-5 个，每项含 entity_key（英文 slug）、entity_type（character|place|item|faction|style）、description、authority（canon_fact|probable_inference|literary_interpretation|user_interpretation）；",
      "- claims：1-8 条，每项含 entity_key（必须引用 entities 里的）、authority、description；",
      "- canon_fact claim 必须带 evidence_indices（数字数组，只能取证据菜单里的编号）；interpretation 类 claim 必须带 author 和 rationale、不带 evidence_indices；",
      "- 至少 1 条 canon_fact claim；",
    ],
    skeleton: '{"visual_bible": {"entities": [...], "claims": [...]}}',
  },
  "detect-key-scenes": {
    analyzer: "关键场景检测器",
    rules: [
      "- candidates：1-5 个，每项含 evidence_indices（恰好 1 个菜单编号，选最能支撑该场景的摘录）、coordinates（cast/place/time/pov）、salience_reasons；",
      "- salience_reasons[].reason_code 只能取：plot_turn|emotional_peak|character_salience|visual_expressiveness|arc_impact|quiet_emotional|dialogue_turn；score ∈ [0,1]；",
      "- 可选 score_total、score_breakdown、diversity_key；",
      "- 每个候选必须挂且仅挂 1 条证据（evidence_indices 长度恒为 1）；",
    ],
    skeleton:
      '{"scene_candidate_set": {"candidates": [{"evidence_indices": [1], "coordinates": {...}, "salience_reasons": [...]}]}}',
  },
};

export function isGuidedSkill(skillName: string): boolean {
  return skillName in GUIDED_SKILLS;
}

/** 信封构建失败后的最大修复轮数（与 agentic 路径一致）。 */
const MAX_REPAIR_ROUNDS = 2;

interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface GuidedRunOptions {
  fetchImpl: typeof fetch;
  callTool?: ToolCaller;
  runId: number;
  novelId: number;
  internalToken: string;
  question: string;
  branch: string | null;
  skill: LoadedSkill;
  runLineage: RunLineageContext;
}

function guidedSystemPrompt(spec: GuidedSkillSpec): string {
  return [
    `你是 NovelMind 的${spec.analyzer}。根据用户问题和编号证据摘录，输出且仅输出一个 JSON 对象（不要 markdown 围栏、不要任何解释文字）。`,
    "规则：",
    ...spec.rules,
    "- 绝不输出任何哈希、ID、evidence_key、cutoff 或血缘字段（由程序注入）。",
  ].join("\n");
}

function buildGuidedUserPrompt(
  question: string,
  retrieval: GuidedRetrieval,
  spec: GuidedSkillSpec,
): string {
  const menuLines = retrieval.menu.map(
    (item) =>
      `[${item.index}] 第${item.chapter_number}章：「${item.excerpt}」`,
  );
  return [
    `问题：${question}`,
    "",
    "证据菜单（evidence_indices 只能从这里选编号）：",
    ...menuLines,
    "",
        `输出骨架：${spec.skeleton}`,
  ].join("\n");
}

function buildGuidedRepairMessage(
  reason: string,
  retrieval: GuidedRetrieval,
): string {
  const menuLines = retrieval.menu.map(
    (item) => `[${item.index}] 第${item.chapter_number}章`,
  );
  return [
    "上一次输出未通过校验。错误：",
    reason.replace(/[\r\n]+/g, " ").slice(0, 500),
    `可用证据编号只有：${menuLines.join("、")}。`,
    "重新输出完整的修正 JSON（只要 JSON，不要其他文字）。",
  ].join("\n");
}

/** 单次网关补全（无工具、无 agent 循环；run 上下文经 header 鉴权）。 */
async function gatewayChat(
  fetchImpl: typeof fetch,
  opts: { runToken: string; novelId: number },
  messages: ChatMessage[],
): Promise<{ text: string; usage: JsonObject }> {
  const res = await fetchImpl(
    `${config.fastApiBaseUrl}/api/gateway/v1/chat/completions`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${config.novelmindGatewayToken}`,
        "content-type": "application/json",
        "X-NovelMind-Run-Token": opts.runToken,
        "X-NovelMind-Novel-ID": String(opts.novelId),
      },
      body: JSON.stringify({
        model: "reader-chat-default",
        messages,
        stream: false,
      }),
    },
  );
  if (!res.ok) {
    throw new Error(`gateway chat HTTP ${res.status}`);
  }
  const payload = (await res.json()) as JsonObject;
  const choices = Array.isArray(payload.choices) ? payload.choices : [];
  const first = choices[0];
  const message = isObject(first) && isObject(first.message) ? first.message : {};
  const text = typeof message.content === "string" ? message.content : "";
  if (!text) {
    throw new Error("gateway chat returned empty content");
  }
  return {
    text,
    usage: isObject(payload.usage) ? payload.usage : {},
  };
}

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 执行一次 guided backfill run（检索 → 单轮生成 → 翻译 → 投影 → finalize）。 */
export async function executeGuidedRun(opts: GuidedRunOptions): Promise<void> {
  const auth = `Bearer ${opts.internalToken}`;
  const retrieval = await guidedRetrieve({
    question: opts.question,
    auth,
    novelId: opts.novelId,
    ...(opts.callTool ? { callTool: opts.callTool } : {}),
  });
  const toolRuns = [
    { tool_name: "search_novel_text", calls: retrieval.searchCalls, errors: 0 },
    {
      tool_name: "get_evidence_span",
      calls: retrieval.evidences.length,
      errors: 0,
    },
  ];

  const spec = GUIDED_SKILLS[opts.skill.name];
  if (!spec) {
    throw new Error(`guided run: skill ${opts.skill.name} has no guided spec`);
  }
  const messages: ChatMessage[] = [
    { role: "system", content: guidedSystemPrompt(spec) },
    { role: "user", content: buildGuidedUserPrompt(opts.question, retrieval, spec) },
  ];

  let envelopePayload: JsonObject | null = null;
  let frozenManifest: JsonObject = {};
  let usage: JsonObject = {};
  for (let round = 0; ; round += 1) {
    const completion = await gatewayChat(
      opts.fetchImpl,
      { runToken: opts.internalToken, novelId: opts.novelId },
      messages,
    );
    usage = completion.usage;
    try {
      const translated = translateEvidenceIndices(
        completion.text,
        retrieval.keys,
        opts.skill.name,
      );
      const built = buildAnalysisEnvelope(
        translated,
        opts.runLineage,
        opts.skill,
        opts.branch,
        toolRuns,
        retrieval.evidences,
      );
      envelopePayload = built.envelope as JsonObject;
      frozenManifest = built.frozenManifest as JsonObject;
      break;
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      console.warn(
        `[agent-poller] run=${opts.runId} guided build round=${round} failed: ${reason.slice(0, 240)}`,
      );
      if (round >= MAX_REPAIR_ROUNDS) throw err;
      messages.push(
        { role: "assistant", content: completion.text },
        { role: "user", content: buildGuidedRepairMessage(reason, retrieval) },
      );
    }
  }
  if (envelopePayload === null) {
    throw new Error("guided run: envelope build did not produce a payload");
  }

  const finalizeRes = await opts.fetchImpl(
    `${config.fastApiBaseUrl}/api/agent/novels/${opts.novelId}/skill-runs/${opts.runId}/finalize`,
    {
      method: "POST",
      headers: {
        authorization: auth,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        stop_reason: "stop",
        envelope: envelopePayload,
        model_lineage: { provider: "novelmind-gateway", model: "reader-chat-default" },
        source_versions:
          (envelopePayload.source_versions as JsonObject | undefined) ?? {},
        usage,
        frozen_manifest: frozenManifest,
      }),
    },
  );
  if (!finalizeRes.ok) {
    throw new Error(`finalize HTTP ${finalizeRes.status}`);
  }
}
