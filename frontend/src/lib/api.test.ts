import { describe, it, expect, vi, beforeEach } from "vitest";

// 使用 vi.hoisted 确保 mock 函数在模块顶部初始化，早于 vi.mock 执行
const { mockGet, mockPost, mockPut, mockPatch, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn().mockResolvedValue({ data: {} }),
  mockPost: vi.fn().mockResolvedValue({ data: {} }),
  mockPut: vi.fn().mockResolvedValue({ data: {} }),
  mockPatch: vi.fn().mockResolvedValue({ data: {} }),
  mockDelete: vi.fn().mockResolvedValue({ data: {} }),
}));

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => ({
      get: mockGet,
      post: mockPost,
      put: mockPut,
      patch: mockPatch,
      delete: mockDelete,
      defaults: { baseURL: "/api", timeout: 30000 },
    })),
  },
}));

import {
  novelsApi,
  aiModelsApi,
  analysisApi,
  timelineApi,
  charactersApi,
  fanfictionApi,
} from "./api";

describe("API 客户端", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("novelsApi", () => {
    it("list 调用 GET /novels", async () => {
      await novelsApi.list();
      expect(mockGet).toHaveBeenCalledWith("/novels");
    });

    it("get 调用 GET /novels/:id", async () => {
      await novelsApi.get("1");
      expect(mockGet).toHaveBeenCalledWith("/novels/1");
    });

    it("upload 使用 multipart/form-data", async () => {
      const file = new File(["test"], "test.txt", { type: "text/plain" });
      await novelsApi.upload(file);
      expect(mockPost).toHaveBeenCalledWith(
        "/novels/upload",
        expect.any(FormData),
        { headers: { "Content-Type": "multipart/form-data" } }
      );
    });

    it("delete 调用 DELETE /novels/:id", async () => {
      await novelsApi.delete("1");
      expect(mockDelete).toHaveBeenCalledWith("/novels/1");
    });

    it("updateProgress 调用 PATCH /novels/:id/progress", async () => {
      await novelsApi.updateProgress("1", 5, 45.5);
      expect(mockPatch).toHaveBeenCalledWith("/novels/1/progress", {
        chapter_id: 5,
        progress_percent: 45.5,
      });
    });

    it("getImportStatus 调用 GET /novels/:id/import-status", async () => {
      await novelsApi.getImportStatus("1");
      expect(mockGet).toHaveBeenCalledWith("/novels/1/import-status");
    });
  });

  describe("aiModelsApi", () => {
    it("list 调用 GET /models", async () => {
      await aiModelsApi.list();
      expect(mockGet).toHaveBeenCalledWith("/models");
    });

    it("create 调用 POST /models", async () => {
      const data = { name: "test", provider: "openai", model_id: "gpt-4o" };
      await aiModelsApi.create(data as any);
      expect(mockPost).toHaveBeenCalledWith("/models", data);
    });

    it("test 调用 POST /models/:id/test", async () => {
      await aiModelsApi.test(1);
      expect(mockPost).toHaveBeenCalledWith("/models/1/test");
    });

    it("setDefault 调用 POST /models/:id/default", async () => {
      await aiModelsApi.setDefault(1);
      expect(mockPost).toHaveBeenCalledWith("/models/1/default");
    });

    it("delete 调用 DELETE /models/:id", async () => {
      await aiModelsApi.delete(1);
      expect(mockDelete).toHaveBeenCalledWith("/models/1");
    });
  });

  describe("占位端点 API", () => {
    it("analysisApi.analyze 调用 POST /analysis/:id/analyze", async () => {
      await analysisApi.analyze("1");
      expect(mockPost).toHaveBeenCalledWith("/analysis/1/analyze");
    });

    it("timelineApi.extractTimeline 调用 POST /timeline/:id/extract", async () => {
      await timelineApi.extractTimeline("1");
      expect(mockPost).toHaveBeenCalledWith("/timeline/1/extract");
    });

    it("charactersApi.extractCharacters 调用 POST /characters/:id/extract", async () => {
      await charactersApi.extractCharacters("1");
      expect(mockPost).toHaveBeenCalledWith("/characters/1/extract");
    });

    it("fanfictionApi.create 调用 POST /fanfiction", async () => {
      await fanfictionApi.create({ title: "test" });
      expect(mockPost).toHaveBeenCalledWith("/fanfiction", { title: "test" });
    });
  });
});
