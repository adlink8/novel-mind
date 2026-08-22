import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockDelete, mockGet, mockPut } = vi.hoisted(() => ({
  mockDelete: vi.fn().mockResolvedValue({ data: undefined }),
  mockGet: vi.fn().mockResolvedValue({ data: {} }),
  mockPut: vi.fn().mockResolvedValue({ data: {} }),
}));

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => ({
      get: mockGet,
      post: vi.fn(),
      put: mockPut,
      patch: vi.fn(),
      delete: mockDelete,
      interceptors: {
        request: { use: vi.fn(), eject: vi.fn() },
        response: { use: vi.fn(), eject: vi.fn() },
      },
    })),
  },
}));

import {
  settingsApi,
  type AgentSettingsPayload,
} from "./settings";

const payload: AgentSettingsPayload = {
  auto_deep_analysis: true,
  memory_enabled: false,
  memory_retention_days: 30,
  show_analysis_progress: false,
  notify_analysis_complete: true,
  auto_create_candidate_artifacts: true,
  task_model_bindings: {
    qa: 1,
    deep_analysis: 2,
    continuation: null,
    illustration: 3,
    rag_eval: null,
    embedding: 4,
  },
};

describe("settingsApi agent settings", () => {
  beforeEach(() => vi.clearAllMocks());

  it("读取 Agent 设置与六类任务绑定", async () => {
    await settingsApi.getAgent();

    expect(mockGet).toHaveBeenCalledWith("/settings/agent");
  });

  it("保存 Agent 设置与六类任务绑定", async () => {
    await settingsApi.putAgent(payload);

    expect(mockPut).toHaveBeenCalledWith("/settings/agent", payload);
  });

  it("读取、删除和清空个性化记忆", async () => {
    await settingsApi.listMemoryPreferences();
    await settingsApi.deleteMemoryPreference(7);
    await settingsApi.clearMemoryPreferences();

    expect(mockGet).toHaveBeenCalledWith("/memory/preferences");
    expect(mockDelete).toHaveBeenNthCalledWith(1, "/memory/preferences/7");
    expect(mockDelete).toHaveBeenNthCalledWith(2, "/memory/preferences");
  });
});
