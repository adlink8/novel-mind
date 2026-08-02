import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { DOMAIN_TOOL_NAMES } from "../src/tools/registry.js";
import { loadSkill } from "../src/skills/loader.js";
import { createControlledAgentDir } from "../src/agent/resource-loader.js";
import { buildGatewayModel } from "../src/agent/provider.js";
import type { LoadedSkill } from "../src/skills/loader.js";

/**
 * session-factory.test.ts（25.2-05 Task 2）：
 * - 工厂恒定传 noTools:"all"，tools allowlist 非空且 ⊆ DOMAIN_TOOL_NAMES
 * - ResourceLoader override 返回 exactly allowlist（A2）
 * - provider baseUrl 以 /api/gateway/v1 结尾，cost 全零（D-15）
 * - allowed_tools 逃逸域工具白名单 → 任何会话创建前拒绝
 * 使用 mock 的 createAgentSession 检查 options；不真连 backend / 不建真实 provider。
 */

// 捕获 createAgentSession 收到的 options（mock Pi 会话创建，真实代码路径）。
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

/** 构造一个逃逸/空白名单的 fake skill（无需落盘）。 */
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

describe("session factory", () => {
  beforeEach(() => {
    mocks.createAgentSession.mockReset();
    mocks.createAgentSession.mockResolvedValue({ session: fakeSession() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("恒定传 noTools:\"all\" + 非空 tools allowlist + 7 个 customTools（真实 skill）", async () => {
    const skill = loadSkill("answer-reading-question");
    const dirs = createControlledAgentDir();
    await createSession({ auth: "Bearer per-run-token", skill, modelRuntime: {} as never, dirs });

    expect(mocks.createAgentSession).toHaveBeenCalledTimes(1);
    const options = mocks.createAgentSession.mock.calls[0][0] as Record<string, unknown>;

    expect(options.noTools).toBe("all");
    // tools 恒定非空、为 allowed_tools ∩ DOMAIN_TOOL_NAMES
    const tools = options.tools as string[];
    expect(Array.isArray(tools)).toBe(true);
    expect(tools.length).toBeGreaterThan(0);
    expect(tools.length).toBeLessThanOrEqual(DOMAIN_TOOL_NAMES.length);
    for (const name of tools) {
      expect(DOMAIN_TOOL_NAMES).toContain(name);
    }
    // 首技能白名单排除 get_narrative_memory（Open Question 4）
    expect(tools).not.toContain("get_narrative_memory");
    // customTools 恰为 7 个域工具
    const customTools = options.customTools as Array<{ name: string }>;
    expect(customTools).toHaveLength(DOMAIN_TOOL_NAMES.length);
    expect(customTools.map((t) => t.name)).toEqual([...DOMAIN_TOOL_NAMES]);
    // sessionManager + model + modelRuntime 均就位
    expect(options.sessionManager).toBeDefined();
    expect((options.model as { id: string }).id).toBe("reader-chat-default");
    expect(options.modelRuntime).toBeDefined();
  });

  it("ResourceLoader override 返回 exactly allowlist（A2：零 ambient）", async () => {
    const skill = loadSkill("answer-reading-question");
    const dirs = createControlledAgentDir();
    await createSession({ auth: "Bearer t", skill, modelRuntime: {} as never, dirs });

    const options = mocks.createAgentSession.mock.calls[0][0] as Record<string, unknown>;
    const loader = options.resourceLoader as {
      getSkills(): { skills: Array<{ name: string }> };
      getPrompts(): { prompts: unknown[] };
      getThemes(): { themes: unknown[] };
      getAgentsFiles(): { agentsFiles: unknown[] };
      getExtensions(): { extensions: unknown[] };
    };
    const skills = loader.getSkills();
    expect(skills.skills).toHaveLength(1);
    expect(skills.skills[0].name).toBe("answer-reading-question");
    expect(loader.getPrompts().prompts).toHaveLength(0);
    expect(loader.getThemes().themes).toHaveLength(0);
    expect(loader.getAgentsFiles().agentsFiles).toHaveLength(0);
    expect(loader.getExtensions().extensions).toHaveLength(0);
  });

  it("技能指令确定性注入 systemPrompt（含 NOVEL_AGENT 基础提示 + SKILL.md）", async () => {
    const skill = loadSkill("answer-reading-question");
    const dirs = createControlledAgentDir();
    await createSession({ auth: "Bearer t", skill, modelRuntime: {} as never, dirs });

    const options = mocks.createAgentSession.mock.calls[0][0] as Record<string, unknown>;
    const loader = options.resourceLoader as {
      getSystemPrompt(): string | undefined;
    };
    const prompt = loader.getSystemPrompt();
    expect(prompt).toBeDefined();
    expect(prompt).toContain("Embedded Novel Agent");
    expect(prompt).toContain("answer-reading-question");
  });

  it("allowed_tools 逃逸域工具白名单 → 会话创建前拒绝，createAgentSession 未被调用", async () => {
    const dirs = createControlledAgentDir();
    await expect(
      createSession({
        auth: "Bearer t",
        skill: fakeSkill(["get_novel", "bash"]),
        modelRuntime: {} as never,
        dirs,
      }),
    ).rejects.toThrow(/bash/);
    expect(mocks.createAgentSession).not.toHaveBeenCalled();
  });

  it("空 allowed_tools → fail-closed 拒绝（tools allowlist 永不空）", async () => {
    const dirs = createControlledAgentDir();
    await expect(
      createSession({ auth: "Bearer t", skill: fakeSkill([]), modelRuntime: {} as never, dirs }),
    ).rejects.toThrow(/为空/);
    expect(mocks.createAgentSession).not.toHaveBeenCalled();
  });
});

describe("gateway provider (D-15)", () => {
  it("模型 baseUrl 指向 /api/gateway/v1，cost 全零，reasoning false", () => {
    const model = buildGatewayModel();
    expect(model.api).toBe("openai-completions");
    expect(model.baseUrl).toMatch(/\/api\/gateway\/v1$/);
    expect(model.reasoning).toBe(false);
    expect(model.cost).toEqual({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 });
    expect(model.provider).toBe("novelmind");
    expect(model.id).toBe("reader-chat-default");
  });
});
