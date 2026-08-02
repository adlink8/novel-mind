/**
 * policy-engine.test.ts（25.3-04 / D-10 / D-11 / REQ-AGENT-07）。
 *
 * 全决策矩阵：每个 D-10/D-15 动作 × {无层 / skill ask / skill allow（非法时 clamp）/
 * session approval} 断言精确结果；deny 终态（session approval 存在、skill allow 存在
 * 仍 deny）；未知动作 deny；非法 skill 规则 clamp 成 ask；last-match-wins 列表内；
 * most-restrictive-wins 层间；filterToolSet restrict-only（fuzz 子集性质）；
 * assertKnownTools 阻断未注册工具；镜像测试防止冻结表与测试漂移。
 *
 * 零上游导入：本测试与实现都不 import @gotgenes/pi-permission-system（clean-room）。
 */

import { describe, it, expect } from "vitest";
import {
  DOMAIN_ACTION_POLICY,
  DOMAIN_ACTIONS,
  PolicyDenied,
  evaluate,
  loadSkillRules,
} from "../src/policy/engine.js";
import { SessionApprovals } from "../src/policy/session-approvals.js";
import { assertKnownTools, filterToolSet } from "../src/policy/tool-visibility.js";

/** 空会话批准（绝大多数用例）。 */
const EMPTY = new Set<string>();

function rules(...entries: Array<{ action: string; policy: "allow" | "ask" | "deny" }>) {
  return entries;
}

// ────────────────────────── 镜像测试：表与词表不漂移 ──────────────────────────

describe("DOMAIN_ACTION_POLICY 冻结表", () => {
  it("覆盖且仅覆盖完整 D-15 动作词表（镜像测试）", () => {
    const tableActions = DOMAIN_ACTION_POLICY.map((r) => r.action).sort();
    const vocabulary = [...DOMAIN_ACTIONS].sort();
    expect(tableActions).toEqual(vocabulary);
  });

  it("每个动作有且仅有一条规则", () => {
    const counts = new Map<string, number>();
    for (const rule of DOMAIN_ACTION_POLICY) {
      counts.set(rule.action, (counts.get(rule.action) ?? 0) + 1);
    }
    for (const count of counts.values()) expect(count).toBe(1);
  });

  it("modify_original_canon 与 move_active_pointer 是永久 deny 且带原因", () => {
    const deny = DOMAIN_ACTION_POLICY.filter((r) => r.policy === "deny").map((r) => r.action);
    expect(deny.sort()).toEqual(["modify_original_canon", "move_active_pointer"]);
    for (const r of DOMAIN_ACTION_POLICY) {
      if (r.policy === "deny") expect(r.reason).toBeTruthy();
    }
  });
});

// ────────────────────────── 全决策矩阵（无层基线） ──────────────────────────

describe("决策矩阵：无层基线", () => {
  for (const rule of DOMAIN_ACTION_POLICY) {
    it(`${rule.action} 无任何层 -> ${rule.policy}`, () => {
      expect(evaluate(rule.action, { sessionApprovals: EMPTY })).toBe(rule.policy);
    });
  }
});

// ────────────────────────── skill 层组合 ──────────────────────────

describe("决策矩阵：skill 层", () => {
  for (const rule of DOMAIN_ACTION_POLICY) {
    const { action, policy } = rule;
    it(`${action} skill 声明 ask -> 至少 ask`, () => {
      const skillRules = rules({ action, policy: "ask" });
      expect(evaluate(action, { skillRules, sessionApprovals: EMPTY })).toBe(
        policy === "deny" ? "deny" : "ask",
      );
    });

    it(`${action} skill 声明 allow 不突破全局 ${policy}（most-restrictive-wins）`, () => {
      const skillRules = rules({ action, policy: "allow" });
      expect(evaluate(action, { skillRules, sessionApprovals: EMPTY })).toBe(policy);
    });

    it(`${action} skill 声明 deny -> deny`, () => {
      const skillRules = rules({ action, policy: "deny" });
      expect(evaluate(action, { skillRules, sessionApprovals: EMPTY })).toBe("deny");
    });
  }

  it("skill 规则列表内 last-match-wins：后声明者胜", () => {
    const skillRules = rules(
      { action: "search_original_text", policy: "ask" },
      { action: "search_original_text", policy: "allow" },
    );
    expect(evaluate("search_original_text", { skillRules, sessionApprovals: EMPTY })).toBe(
      "allow",
    );
    const reversed = rules(
      { action: "search_original_text", policy: "allow" },
      { action: "search_original_text", policy: "ask" },
    );
    expect(evaluate("search_original_text", { skillRules: reversed, sessionApprovals: EMPTY })).toBe(
      "ask",
    );
  });

  it("层间 most-restrictive-wins：skill deny + 全局 ask -> deny", () => {
    expect(
      evaluate("publish_illustration", {
        skillRules: rules({ action: "publish_illustration", policy: "deny" }),
        sessionApprovals: EMPTY,
      }),
    ).toBe("deny");
  });
});

// ────────────────────────── deny 终态（T-25.3-04-01 / ASVS V4） ──────────────────────────

describe("deny 终态：无审批路径", () => {
  for (const action of ["modify_original_canon", "move_active_pointer"]) {
    it(`${action} 即使会话批准存在仍 deny`, () => {
      const sessionApprovals = new Set([action]);
      expect(evaluate(action, { sessionApprovals })).toBe("deny");
    });

    it(`${action} 即使 skill 声明 allow 仍 deny`, () => {
      const skillRules = rules({ action, policy: "allow" });
      expect(evaluate(action, { skillRules, sessionApprovals: EMPTY })).toBe("deny");
    });

    it(`${action} 即使 skill deny + 会话批准仍 deny（确定性、终态）`, () => {
      const skillRules = rules({ action, policy: "deny" });
      const sessionApprovals = new Set([action]);
      expect(evaluate(action, { skillRules, sessionApprovals })).toBe("deny");
    });
  }
});

// ────────────────────────── 会话级批准（D-11 / A5） ──────────────────────────

describe("会话级批准", () => {
  it("ask 动作经会话批准后 -> allow", () => {
    const sessionApprovals = new Set(["publish_illustration"]);
    expect(evaluate("publish_illustration", { sessionApprovals })).toBe("allow");
  });

  it("会话批准仅作用于对应动作（不扩散到同类 ask）", () => {
    const sessionApprovals = new Set(["publish_illustration"]);
    expect(evaluate("generate_image_candidate", { sessionApprovals })).toBe("ask");
  });

  it("SessionApprovals：add/has/size，无持久化路径", () => {
    const sa = new SessionApprovals();
    expect(sa.size).toBe(0);
    expect(sa.has("publish_illustration")).toBe(false);
    sa.add("publish_illustration");
    expect(sa.has("publish_illustration")).toBe(true);
    expect(sa.size).toBe(1);
    // 新 run 重新构造：空集合（per-run 语义）。
    expect(new SessionApprovals().size).toBe(0);
  });
});

// ────────────────────────── fail-closed：未知动作 / 非法配置 ──────────────────────────

describe("fail-closed", () => {
  it("未知动作 resolve deny（即使会话批准存在）", () => {
    const sessionApprovals = new Set(["some_unknown_action"]);
    expect(evaluate("some_unknown_action", { sessionApprovals })).toBe("deny");
  });

  it("未知动作 resolve deny（即使 skill 规则存在）", () => {
    const skillRules = rules({ action: "some_unknown_action", policy: "allow" });
    expect(evaluate("some_unknown_action", { skillRules, sessionApprovals: EMPTY })).toBe(
      "deny",
    );
  });

  it("loadSkillRules：合法字符串条目 -> ask", () => {
    const result = loadSkillRules(["publish_illustration", "generate_image_candidate"]);
    expect(result).toEqual([
      { action: "publish_illustration", policy: "ask" },
      { action: "generate_image_candidate", policy: "ask" },
    ]);
  });

  it("loadSkillRules：非法 policy clamp 成 ask（fail-closed）", () => {
    const result = loadSkillRules([{ action: "publish_illustration", policy: "maybe" }]);
    expect(result).toEqual([{ action: "publish_illustration", policy: "ask" }]);
  });

  it("loadSkillRules：合法对象条目原样保留", () => {
    const result = loadSkillRules([
      { action: "search_original_text", policy: "allow" },
      { action: "apply_derivative_edit", policy: "deny" },
    ]);
    expect(result).toEqual([
      { action: "search_original_text", policy: "allow" },
      { action: "apply_derivative_edit", policy: "deny" },
    ]);
  });

  it("loadSkillRules：无法解析的条目被丢弃，不抛错", () => {
    const result = loadSkillRules(["", 42, null, { policy: "allow" }, {}]);
    expect(result).toEqual([]);
  });

  it("loadSkillRules：非数组输入 -> 空规则（全局表兜底）", () => {
    expect(loadSkillRules("publish_illustration")).toEqual([]);
    expect(loadSkillRules(undefined)).toEqual([]);
  });

  it("非法 skill 规则 clamp 成 ask 后，evaluate 表现为 ask", () => {
    const skillRules = loadSkillRules([{ action: "search_original_text", policy: "invalid" }]);
    expect(evaluate("search_original_text", { skillRules, sessionApprovals: EMPTY })).toBe("ask");
  });
});

// ────────────────────────── PolicyDenied ──────────────────────────

describe("PolicyDenied", () => {
  it("携带 action 与可读消息（稳定错误路径）", () => {
    const err = new PolicyDenied("modify_original_canon", "Original Canon 只读");
    expect(err).toBeInstanceOf(Error);
    expect(err.action).toBe("modify_original_canon");
    expect(err.message).toContain("modify_original_canon");
  });
});

// ────────────────────────── restrict-only 可见性 ──────────────────────────

describe("filterToolSet / assertKnownTools", () => {
  it("只移除 denied/disabled 条目（restrict-only）", () => {
    const active = ["get_novel", "get_chapter", "search_novel_text"];
    const denied = new Set(["get_chapter"]);
    expect(filterToolSet(active, denied)).toEqual(["get_novel", "search_novel_text"]);
  });

  it("无 denied 条目时保持原集", () => {
    const active = ["get_novel"];
    expect(filterToolSet(active, new Set())).toEqual(["get_novel"]);
  });

  it("fuzz：输出永远是输入的子集", () => {
    const pool = ["a", "b", "c", "d", "e"];
    for (let i = 0; i < 50; i++) {
      const active = pool.filter(() => Math.random() < 0.7);
      const denied = new Set(pool.filter(() => Math.random() < 0.5));
      const out = filterToolSet(active, denied);
      expect(new Set(out)).toEqual(new Set(active.filter((t) => !denied.has(t))));
      expect(out.every((t) => active.includes(t))).toBe(true);
    }
  });

  it("assertKnownTools 阻断未注册工具（fail-closed）", () => {
    const registry = new Set(["get_novel", "get_chapter"]);
    expect(() => assertKnownTools(["get_novel"], registry)).not.toThrow();
    expect(() => assertKnownTools(["evil_tool"], registry)).toThrow(/ToolRegistryManifest/);
  });

  it("assertKnownTools 支持只读数组 registry 名", () => {
    expect(() => assertKnownTools(["get_novel"], ["get_novel"])).not.toThrow();
  });
});
