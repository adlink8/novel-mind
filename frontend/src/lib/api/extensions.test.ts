import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockGet, mockPost, mockPatch } = vi.hoisted(() => ({
  mockGet: vi.fn().mockResolvedValue({ data: {} }),
  mockPost: vi.fn().mockResolvedValue({ data: {} }),
  mockPatch: vi.fn().mockResolvedValue({ data: {} }),
}));

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => ({
      get: mockGet,
      post: mockPost,
      put: vi.fn(),
      patch: mockPatch,
      delete: vi.fn(),
      interceptors: {
        request: { use: vi.fn(), eject: vi.fn() },
        response: { use: vi.fn(), eject: vi.fn() },
      },
    })),
  },
}));

import { extensionsApi } from "./extensions";

describe("extensionsApi", () => {
  beforeEach(() => vi.clearAllMocks());

  it("通过后端读取小说、Skill registry 和 Tool catalog", async () => {
    await extensionsApi.listNovels();
    await extensionsApi.listSkills();
    await extensionsApi.getToolCatalog();

    expect(mockGet).toHaveBeenNthCalledWith(1, "/novels");
    expect(mockGet).toHaveBeenNthCalledWith(2, "/agent/skills");
    expect(mockGet).toHaveBeenNthCalledWith(3, "/agent/tools/catalog");
  });

  it("只提交声明式 Skill，并能更新版本状态", async () => {
    const payload = {
      novel_id: 7,
      name: "chapter-notes",
      version: "1.0.0",
      description: "整理章节要点",
      prompt: "请整理输入章节",
      input_schema: { type: "object" },
      output_schema: { type: "object" },
      allowed_tools: ["get_chapter"],
      budget: { max_tool_calls: 3 },
    };

    await extensionsApi.createSkill(payload);
    await extensionsApi.updateSkillVersionStatus("chapter-notes", 11, "draft");

    expect(mockPost).toHaveBeenCalledWith("/agent/skills", payload);
    expect(mockPatch).toHaveBeenCalledWith("/agent/skills/chapter-notes/versions/11", {
      status: "draft",
    });
  });
});
