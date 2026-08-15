/**
 * 统一对话的发送路由 seam（AI 自动路由）。
 *
 * 产品原则：用户不选 skill，Agent 自动路由。前端只决定「走哪条通道」：
 *   - `reader_chat`：读者对话（生成作业轮询、引用、QueryPlan/backfill 展示）；
 *   - `agent`：智能体 SSE 流式回合（工具调用、候选产物、审批）。
 *
 * skill 选择永远不在 UI 暴露。Agent run 请求体**不带 skill** ——
 * agent-service 在后端自动路由就绪后按意图自行决定；路由未就绪时
 * 服务端回落到默认问答技能（answer-reading-question），前端不感知差异。
 *
 * 意图判定：`resolveSendRouting` 是**临时前端占位**，用轻量关键词启发式
 * 区分「画图/续写」类显式意图。后端自动路由返回后，reader_chat 响应中
 * 的建议 skill 应通过 `backendHint` 喂回本函数，届时以后端裁决为准。
 */

/** 后端自动路由提示（reader_chat 响应可能携带；字段名待后端契约定稿）。 */
export type BackendRoutingHint = {
  /** 后端建议的技能名（缺省则前端不回退启发式判定）。 */
  suggestedSkill?: string | null;
  /** 后端是否建议走智能体通道（缺省 null = 由前端启发式判定）。 */
  suggestAgent?: boolean | null;
};

export type SendRouting =
  | { mode: "reader_chat" }
  | { mode: "agent"; skill?: string };

/** 「画图」类意图关键词（插图/配图/生成图等）。 */
const ILLUSTRATION_PATTERN =
  /(画图|画一|画个|画张|画出|画画|绘制|画像|插图|插画|配图|生成图|出图|illustrat|draw)/i;

/** 「续写」类意图关键词（接续剧情等）。 */
const CONTINUATION_PATTERN =
  /(续写|接着写|继续写|接下来写|往后写|写下去|往下写|接着编|接下来会发生什么|continue)/i;

/** 前端启发式：消息是否明显指向智能体通道（画图/续写）。 */
export function hasAgentIntent(draft: string): boolean {
  const text = draft.trim();
  if (!text) return false;
  return ILLUSTRATION_PATTERN.test(text) || CONTINUATION_PATTERN.test(text);
}

/**
 * 解析一次发送的路由。优先级：
 *   1. 后端建议（suggestAgent / suggestedSkill）—— 后端裁决优先；
 *   2. 前端启发式（画图/续写意图）→ agent（skill 缺省，由后端自动路由）；
 *   3. 其余 → reader_chat。
 *
 * 绝不返回用户可见的 skill 选择：skill 只可能来自后端建议或内部覆盖。
 */
export function resolveSendRouting(
  draft: string,
  hint?: BackendRoutingHint | null
): SendRouting {
  if (hint?.suggestAgent) {
    return hint.suggestedSkill
      ? { mode: "agent", skill: hint.suggestedSkill }
      : { mode: "agent" };
  }
  if (hint?.suggestedSkill) {
    return { mode: "agent", skill: hint.suggestedSkill };
  }
  if (hasAgentIntent(draft)) {
    return { mode: "agent" };
  }
  return { mode: "reader_chat" };
}
