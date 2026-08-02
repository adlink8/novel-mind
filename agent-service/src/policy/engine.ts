/**
 * 域动作权限策略引擎（25.3-04 / D-10 / D-11 / REQ-AGENT-07）。
 *
 * clean-room ~250 行引擎：复用上游 pi-permission-system 的**文档化语义**
 * （last-match-wins、most-restrictive-wins、fail-closed clamps、session approvals、
 * restrict-only visibility），但**不**复制其 Pi-hook / TUI / tree-sitter 实现——
 * 本模块零外部导入（pattern-source only，D-03），且门控对象是**域动作**而非文件路径。
 *
 * 语义硬化（D-10 + ASVS V4 高于上游 session-override 语义）：
 *  - DOMAIN_ACTION_POLICY 是冻结表：两个 deny 动作（modify_original_canon /
 *    move_active_pointer）**没有审批路径**——evaluate 在任何会话批准/技能规则之前
 *    先检查全局 deny（终态）。
 *  - 未知动作 resolve deny（fail-closed）；非法的高优先级配置（skill 规则）clamp
 *    allow → ask。
 *  - 列表内 last-match-wins；global/skill 层间 most-restrictive-wins（deny > ask > allow）。
 *  - 会话级批准（SessionApprovals）只在单 run 内存中存活，永不落库。
 */

export type PolicyAction = "allow" | "ask" | "deny";

/** 单条域动作规则：action + 决策 + 可选原因。 */
export interface DomainActionRule {
  action: string;
  policy: PolicyAction;
  reason?: string;
}

/**
 * D-15 冻结动作词表（唯一事实源，镜像测试断言引擎表与其完全一致，防漂移）。
 * 覆盖 D-10 基础动作 + D-15 下游衍生/发布/导出动作。
 */
export const DOMAIN_ACTIONS = [
  "search_original_text",
  "create_scene_spec_candidate",
  "generate_image_candidate",
  "publish_illustration",
  "attach_illustration_to_text",
  "create_canon_fork",
  "apply_derivative_edit",
  "publish_derivative_revision",
  "allow_divergence",
  "publish_derivative_visual",
  "prepare_export",
  "approve_export",
  "materialize_export",
  "download_export",
  "modify_original_canon",
  "move_active_pointer",
] as const;

/**
 * D-10 冻结种子表：候选创建/读取动作 allow；发布、派生 apply/divergence/visual、
 * 导出审批动作 ask；Original Canon 修改与 active-pointer 移动永久 deny。
 * 原因 verbatim 自 25.3-CONTEXT（D-10 / D-14）。
 */
export const DOMAIN_ACTION_POLICY: readonly DomainActionRule[] = [
  { action: "search_original_text", policy: "allow", reason: "只读检索（D-10）" },
  {
    action: "create_scene_spec_candidate",
    policy: "allow",
    reason: "候选创建不触碰权威状态（D-10）",
  },
  { action: "generate_image_candidate", policy: "ask", reason: "图像生成需人工确认（D-10）" },
  { action: "publish_illustration", policy: "ask", reason: "发布动作需人工确认（D-10）" },
  { action: "attach_illustration_to_text", policy: "ask", reason: "插图绑定正文需人工确认（D-15）" },
  { action: "create_canon_fork", policy: "ask", reason: "Canon 分叉需人工确认（D-10）" },
  { action: "apply_derivative_edit", policy: "ask", reason: "派生改写需人工确认（D-15）" },
  { action: "publish_derivative_revision", policy: "ask", reason: "派生修订发布需人工确认（D-15）" },
  { action: "allow_divergence", policy: "ask", reason: "允许分歧需人工确认（D-15）" },
  { action: "publish_derivative_visual", policy: "ask", reason: "派生视觉发布需人工确认（D-15）" },
  { action: "prepare_export", policy: "ask", reason: "导出准备需人工确认（D-15）" },
  { action: "approve_export", policy: "ask", reason: "导出审批需人工确认（D-15）" },
  { action: "materialize_export", policy: "ask", reason: "导出物化需人工确认（D-15）" },
  { action: "download_export", policy: "ask", reason: "导出下载需人工确认（D-15）" },
  {
    action: "modify_original_canon",
    policy: "deny",
    reason: "Original Canon 对 agent 只读（D-14）",
  },
  {
    action: "move_active_pointer",
    policy: "deny",
    reason: "Active-pointer 切换需显式授权（D-10）",
  },
] as const;

/** 策略拒绝：携带 action 与原因，进入 run 的稳定错误路径。 */
export class PolicyDenied extends Error {
  readonly action: string;

  constructor(action: string, reason?: string) {
    super(`策略拒绝: 域动作 ${action} 被禁止（${reason ?? "无审批路径"}）`);
    this.name = "PolicyDenied";
    this.action = action;
  }
}

/** evaluate 的上下文：skill 层规则 + 会话级批准集合。 */
export interface EvaluateContext {
  /** skill.yaml approval_required_for 派生出的规则（可缺省）。 */
  skillRules?: DomainActionRule[];
  /** 单 run 会话级批准（D-11）：命中即 allow（但全局 deny 仍优先）。 */
  sessionApprovals: ReadonlySet<string>;
}

/** 列表内 last-match-wins：返回列表中匹配 action 的最后一条规则（无则 undefined）。 */
function lastMatch(rules: readonly DomainActionRule[], action: string): DomainActionRule | undefined {
  let hit: DomainActionRule | undefined;
  for (const rule of rules) {
    if (rule.action === action) hit = rule;
  }
  return hit;
}

/** 决策优先级：deny > ask > allow（most-restrictive-wins）。 */
function mostRestrictive(decisions: PolicyAction[]): PolicyAction {
  if (decisions.includes("deny")) return "deny";
  if (decisions.includes("ask")) return "ask";
  return "allow";
}

/**
 * 求值策略：
 *  1) 未知动作 → deny（fail-closed，会话批准不可拯救）。
 *  2) 全局 deny 是**终态**：在任何会话批准/技能规则之前返回——两个 deny 动作
 *     绝无审批路径（D-10 + ASVS V4）。
 *  3) 会话级批准命中 → allow（D-11，单 run 内有效）。
 *  4) skill + global 层间 most-restrictive-wins。
 */
export function evaluate(action: string, ctx: EvaluateContext): PolicyAction {
  const global = lastMatch(DOMAIN_ACTION_POLICY, action);
  if (!global) return "deny"; // 未知动作：fail-closed
  if (global.policy === "deny") return "deny"; // deny 终态，无审批路径
  if (ctx.sessionApprovals.has(action)) return "allow";
  const skill = ctx.skillRules ? lastMatch(ctx.skillRules, action) : undefined;
  const decisions = [skill?.policy, global.policy].filter(Boolean) as PolicyAction[];
  return mostRestrictive(decisions);
}

/**
 * 解析 skill.yaml 的 approval_required_for 条目为规则列表（fail-closed clamp）。
 *  - 字符串条目 → 该动作需审批（ask）。
 *  - 对象条目 {action, policy} → 合法 policy 照录；非法 policy → clamp 成 ask
 *    （非法高优先级配置 fail-closed，D-11 / RESEARCH Pattern 4）。
 *  - 无法解析（空串/缺 action 的对象/其它类型）→ 丢弃：评估仍由全局表 fail-closed 兜底。
 */
export function loadSkillRules(raw: unknown): DomainActionRule[] {
  if (!Array.isArray(raw)) return [];
  const rules: DomainActionRule[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      if (entry.trim()) rules.push({ action: entry, policy: "ask" });
      continue;
    }
    if (entry !== null && typeof entry === "object") {
      const { action, policy } = entry as { action?: unknown; policy?: unknown };
      if (typeof action !== "string" || !action.trim()) continue;
      if (policy === "allow" || policy === "ask" || policy === "deny") {
        rules.push({ action, policy });
      } else {
        // 非法高优先级配置：clamp allow -> ask（fail-closed）。
        rules.push({ action, policy: "ask" });
      }
    }
  }
  return rules;
}
