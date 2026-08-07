import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// 使用 vi.hoisted 确保 mock 函数在模块顶部初始化，早于 vi.mock 执行
const { mockGet, mockPost, mockPut, mockPatch, mockDelete, handlers } = vi.hoisted(() => ({
  mockGet: vi.fn().mockResolvedValue({ data: { items: [] } }),
  mockPost: vi.fn().mockResolvedValue({ data: {} }),
  mockPut: vi.fn().mockResolvedValue({ data: {} }),
  mockPatch: vi.fn().mockResolvedValue({ data: {} }),
  mockDelete: vi.fn().mockResolvedValue({ data: {} }),
  // 捕获 api.interceptors.request.use 注册的回调，便于在测试中直接驱动拦截器
  handlers: { request: null as null | ((config: any) => any) },
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
      interceptors: {
        request: {
          use: vi.fn((fn: any) => {
            handlers.request = fn;
          }),
          eject: vi.fn(),
        },
        response: { use: vi.fn(), eject: vi.fn() },
      },
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
  setAccessToken,
  getAccessToken,
  authApi,
  isTerminalJobStatus,
  pollReaderChatJob,
  isQualityComparable,
  qualityStatusTone,
  agentApi,
  evalApi,
  readerChatApi,
  relationshipsApi,
  settingsApi,
  usageApi,
  searchApi,
} from "./api";

describe("API 客户端", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
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

    it("upload 使用 multipart FormData 与长超时", async () => {
      const file = new File(["test"], "test.txt", { type: "text/plain" });
      await novelsApi.upload(file);
      expect(mockPost).toHaveBeenCalledWith(
        "/novels/upload",
        expect.any(FormData),
        { timeout: 600000 }
      );
    });

    it("delete 调用 DELETE /novels/:id", async () => {
      await novelsApi.delete("1");
      expect(mockDelete).toHaveBeenCalledWith("/novels/1");
    });

    it("update 调用 PATCH /novels/:id", async () => {
      await novelsApi.update("1", { title: "新书名" });
      expect(mockPatch).toHaveBeenCalledWith("/novels/1", { title: "新书名" });
    });

    it("bulkDelete 调用 DELETE /novels/bulk", async () => {
      await novelsApi.bulkDelete([1, 2]);
      expect(mockDelete).toHaveBeenCalledWith("/novels/bulk", {
        data: { novel_ids: [1, 2] },
      });
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
      await analysisApi.analyze("1", { analysis_type: "plot_summary" });
      expect(mockPost).toHaveBeenCalledWith("/analysis/1/analyze", {
        analysis_type: "plot_summary",
      });
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

  describe("access token", () => {
    it("setAccessToken 写入 sessionStorage", () => {
      setAccessToken("tok-123");
      expect(window.sessionStorage.getItem("novelmind_access_token")).toBe(
        "tok-123"
      );
      expect(getAccessToken()).toBe("tok-123");
    });

    it("setAccessToken(null) 移除 token", () => {
      setAccessToken("tok-123");
      setAccessToken(null);
      expect(getAccessToken()).toBeNull();
      expect(window.sessionStorage.getItem("novelmind_access_token")).toBeNull();
    });

    it("未设置 token 时 getAccessToken 返回 null", () => {
      expect(getAccessToken()).toBeNull();
    });
  });

  describe("request 拦截器", () => {
    it("有 token 时注入 Bearer Authorization", () => {
      setAccessToken("abc");
      const out = handlers.request!({ headers: {} });
      expect(out.headers.Authorization).toBe("Bearer abc");
    });

    it("无 token 时不注入 Authorization", () => {
      const out = handlers.request!({});
      expect(out.headers).toBeUndefined();
    });

    it("FormData 请求移除 Content-Type（交由浏览器设置 boundary）", () => {
      const out = handlers.request!({
        headers: { "Content-Type": "application/json" },
        data: new FormData(),
      });
      expect(out.headers).not.toHaveProperty("Content-Type");
    });
  });

  describe("authApi", () => {
    it("login 成功后写入 access token", async () => {
      mockPost.mockResolvedValue({
        data: { access_token: "tok-1", token_type: "bearer", user_id: 1 },
      });
      const res = await authApi.login("u", "p");
      expect(mockPost).toHaveBeenCalledWith("/auth/login", {
        username: "u",
        password: "p",
      });
      expect(getAccessToken()).toBe("tok-1");
      expect(res.data.access_token).toBe("tok-1");
    });

    it("logout 成功后清空 token", async () => {
      setAccessToken("tok-keep");
      mockPost.mockResolvedValue({ data: {} });
      await authApi.logout();
      expect(mockPost).toHaveBeenCalledWith("/auth/logout");
      expect(getAccessToken()).toBeNull();
    });

    it("logout 即使请求失败也清空 token", async () => {
      setAccessToken("tok-keep");
      mockPost.mockRejectedValue(new Error("net"));
      await expect(authApi.logout()).rejects.toThrow("net");
      expect(getAccessToken()).toBeNull();
    });
  });

  describe("isTerminalJobStatus", () => {
    it("terminal 状态返回 true", () => {
      expect(isTerminalJobStatus("completed")).toBe(true);
      expect(isTerminalJobStatus("cancelled")).toBe(true);
      expect(isTerminalJobStatus("failed")).toBe(true);
      expect(isTerminalJobStatus("failed_validation")).toBe(true);
      expect(isTerminalJobStatus("paused_budget")).toBe(true);
      expect(isTerminalJobStatus("paused_dependency")).toBe(true);
    });

    it("非 terminal 状态返回 false", () => {
      expect(isTerminalJobStatus("queued")).toBe(false);
      expect(isTerminalJobStatus("running")).toBe(false);
      expect(isTerminalJobStatus("unknown")).toBe(false);
    });
  });

  describe("pollReaderChatJob", () => {
    it("轮询到 terminal 状态立即返回", async () => {
      mockGet.mockResolvedValue({ data: { status: "completed" } });
      const job = await pollReaderChatJob(1, 2, 3, {
        intervalMs: 50,
        timeoutMs: 500,
      });
      expect(job.status).toBe("completed");
    });

    it("abort 信号触发时抛 AbortError", async () => {
      const controller = new AbortController();
      controller.abort();
      await expect(
        pollReaderChatJob(1, 2, 3, { signal: controller.signal })
      ).rejects.toThrow("Aborted");
    });

    it("超时后返回最后一次非 terminal 的 job", async () => {
      vi.useFakeTimers();
      mockGet.mockResolvedValue({ data: { status: "running" } });
      const promise = pollReaderChatJob(1, 2, 3, {
        intervalMs: 100,
        timeoutMs: 250,
      });
      await vi.advanceTimersByTimeAsync(600);
      await expect(promise).resolves.toEqual(
        expect.objectContaining({ status: "running" })
      );
    });

    it("getJob 请求失败时向上抛出错误", async () => {
      mockGet.mockRejectedValue(new Error("network"));
      await expect(
        pollReaderChatJob(1, 2, 3, { intervalMs: 50, timeoutMs: 500 })
      ).rejects.toThrow("network");
    });

    it("onUpdate 回调收到每次轮询的 job", async () => {
      const onUpdate = vi.fn();
      mockGet.mockResolvedValue({ data: { status: "completed" } });
      await pollReaderChatJob(1, 2, 3, { onUpdate });
      expect(onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ status: "completed" })
      );
    });

    it("readerChatApi.getJob 生成正确 URL", async () => {
      await readerChatApi.getJob(7, 42, 9);
      expect(mockGet).toHaveBeenCalledWith(
        "/novels/7/conversations/42/jobs/9"
      );
    });
  });

  describe("isQualityComparable", () => {
    it("flag=false 恒为 false", () => {
      expect(isQualityComparable("passed", false)).toBe(false);
      expect(isQualityComparable("qualified", false)).toBe(false);
    });

    it("flag=true 时仅 comparable 状态为 true", () => {
      expect(isQualityComparable("passed", true)).toBe(true);
      expect(isQualityComparable("qualified", true)).toBe(true);
      expect(isQualityComparable("queued", true)).toBe(false);
      expect(isQualityComparable("blocked_dependency", true)).toBe(false);
    });

    it("未传 flag 时默认按 comparable 状态判断", () => {
      expect(isQualityComparable("passed")).toBe(true);
      expect(isQualityComparable("cancelled")).toBe(false);
    });
  });

  describe("qualityStatusTone", () => {
    it("成功状态返回 success", () => {
      expect(qualityStatusTone("passed")).toBe("success");
      expect(qualityStatusTone("qualified")).toBe("success");
    });

    it("失败状态返回 danger", () => {
      expect(qualityStatusTone("quality_regression")).toBe("danger");
      expect(qualityStatusTone("failed_policy")).toBe("danger");
    });

    it("阻塞/无效状态返回 warning", () => {
      expect(qualityStatusTone("blocked_dependency")).toBe("warning");
      expect(qualityStatusTone("invalid_fixture")).toBe("warning");
      expect(qualityStatusTone("invalid_lineage")).toBe("warning");
      expect(qualityStatusTone("quarantined")).toBe("warning");
    });

    it("cancelled 返回 muted", () => {
      expect(qualityStatusTone("cancelled")).toBe("muted");
    });

    it("其他状态返回 info", () => {
      expect(qualityStatusTone("queued")).toBe("info");
      expect(qualityStatusTone("unknown")).toBe("info");
    });
  });

  describe("agentApi", () => {
    it("getLatestRun 空列表返回 null", async () => {
      mockGet.mockResolvedValue({ data: { items: [] } });
      expect(await agentApi.getLatestRun(1)).toBeNull();
    });

    it("getLatestRun 返回最新一条 run", async () => {
      mockGet.mockResolvedValue({ data: { items: [{ id: 5 }] } });
      const run = await agentApi.getLatestRun(1);
      expect(run?.id).toBe(5);
    });

    it("getLatestArtifact 空列表返回 null", async () => {
      mockGet.mockResolvedValue({ data: { items: [] } });
      expect(await agentApi.getLatestArtifact(1)).toBeNull();
    });

    it("getLatestArtifact 返回最新一条 artifact", async () => {
      mockGet.mockResolvedValue({ data: { items: [{ id: 9 }] } });
      const artifact = await agentApi.getLatestArtifact(1);
      expect(artifact?.id).toBe(9);
    });

    it("getArtifact 返回 artifact 详情", async () => {
      mockGet.mockResolvedValue({ data: { id: 9, type: "analysis" } });
      const artifact = await agentApi.getArtifact(1, 9);
      expect(artifact.type).toBe("analysis");
    });

    it("getArtifactContent 无修订返回 null", async () => {
      mockGet.mockResolvedValue({ data: { items: [] } });
      expect(await agentApi.getArtifactContent(1, 9)).toBeNull();
    });

    it("getArtifactContent 返回最新修订的 content", async () => {
      mockGet.mockResolvedValue({
        data: {
          items: [
            { content: { type: "v1" } },
            { content: { type: "v2" } },
          ],
        },
      });
      const content = await agentApi.getArtifactContent(1, 9);
      expect(content?.type).toBe("v2");
    });

    it("approveArtifact 返回批准后的 artifact", async () => {
      mockPost.mockResolvedValue({ data: { id: 9, status: "approved" } });
      const artifact = await agentApi.approveArtifact(9);
      expect(artifact.status).toBe("approved");
      expect(mockPost).toHaveBeenCalledWith("/agent/artifacts/9/approve");
    });

    it("rejectArtifact 返回拒绝后的 artifact", async () => {
      mockPost.mockResolvedValue({ data: { id: 9, status: "rejected" } });
      const artifact = await agentApi.rejectArtifact(9);
      expect(artifact.status).toBe("rejected");
      expect(mockPost).toHaveBeenCalledWith("/agent/artifacts/9/reject");
    });
  });

  describe("evalApi", () => {
    it("listDatasets 无参数时 URL 不带 query", async () => {
      await evalApi.listDatasets();
      expect(mockGet).toHaveBeenCalledWith("/eval/datasets");
    });

    it("listDatasets 组合参数生成 query", async () => {
      await evalApi.listDatasets({
        novel_id: 7,
        status: "confirmed",
        question_type: "plot",
      });
      expect(mockGet).toHaveBeenCalledWith(
        "/eval/datasets?novel_id=7&status=confirmed&question_type=plot"
      );
    });

    it("listDatasets novel_id=0 仍被保留", async () => {
      await evalApi.listDatasets({ novel_id: 0 });
      expect(mockGet).toHaveBeenCalledWith("/eval/datasets?novel_id=0");
    });

    it("listRuns 带 novelId 生成 query", async () => {
      await evalApi.listRuns(3);
      expect(mockGet).toHaveBeenCalledWith("/eval/runs?novel_id=3");
    });

    it("listRuns 不带 novelId 无 query", async () => {
      await evalApi.listRuns();
      expect(mockGet).toHaveBeenCalledWith("/eval/runs");
    });
  });

  describe("authApi 附加", () => {
    it("me 调用 GET /auth/me", async () => {
      await authApi.me();
      expect(mockGet).toHaveBeenCalledWith("/auth/me");
    });

    it("register 调用 POST /auth/register", async () => {
      await authApi.register("u", "e@x.com", "p");
      expect(mockPost).toHaveBeenCalledWith("/auth/register", {
        username: "u",
        email: "e@x.com",
        password: "p",
      });
    });
  });

  describe("novelsApi 附加", () => {
    it("getChapters 调用 GET /novels/:id/chapters", async () => {
      await novelsApi.getChapters("1");
      expect(mockGet).toHaveBeenCalledWith("/novels/1/chapters");
    });

    it("getChapter 调用 GET /novels/:novelId/chapters/:chapterId", async () => {
      await novelsApi.getChapter("1", "5");
      expect(mockGet).toHaveBeenCalledWith("/novels/1/chapters/5");
    });

    it("listBookmarks 调用 GET /novels/:id/bookmarks", async () => {
      await novelsApi.listBookmarks(1);
      expect(mockGet).toHaveBeenCalledWith("/novels/1/bookmarks");
    });

    it("createBookmark 调用 POST /novels/:id/bookmarks", async () => {
      await novelsApi.createBookmark(1, { chapter_id: 2, position_percent: 50 });
      expect(mockPost).toHaveBeenCalledWith("/novels/1/bookmarks", {
        chapter_id: 2,
        position_percent: 50,
      });
    });

    it("deleteBookmark 调用 DELETE /novels/:id/bookmarks/:bid", async () => {
      await novelsApi.deleteBookmark(1, 9);
      expect(mockDelete).toHaveBeenCalledWith("/novels/1/bookmarks/9");
    });

    it("getImportJobStatus 调用 GET /novels/import-jobs/:jobId", async () => {
      await novelsApi.getImportJobStatus("j1");
      expect(mockGet).toHaveBeenCalledWith("/novels/import-jobs/j1");
    });
  });

  describe("analysisApi 附加", () => {
    it("getAnalysis 无 analysisType 时不带 params", async () => {
      await analysisApi.getAnalysis("1");
      expect(mockGet).toHaveBeenCalledWith("/analysis/1", { params: undefined });
    });

    it("getAnalysis 带 analysisType 传入 params", async () => {
      await analysisApi.getAnalysis("1", "plot_summary");
      expect(mockGet).toHaveBeenCalledWith("/analysis/1", {
        params: { analysis_type: "plot_summary" },
      });
    });

    it("analyzeChapter 调用 POST 章节分析", async () => {
      await analysisApi.analyzeChapter("1", "5", {
        analysis_type: "chapter_summary",
      });
      expect(mockPost).toHaveBeenCalledWith("/analysis/1/chapters/5/analyze", {
        analysis_type: "chapter_summary",
      });
    });

    it("hierarchy 调用 GET /analysis/:id/hierarchy", async () => {
      await analysisApi.hierarchy("1");
      expect(mockGet).toHaveBeenCalledWith("/analysis/1/hierarchy");
    });

    it("rebuildHierarchy 调用 POST /analysis/:id/hierarchy/rebuild", async () => {
      await analysisApi.rebuildHierarchy("1");
      expect(mockPost).toHaveBeenCalledWith("/analysis/1/hierarchy/rebuild");
    });
  });

  describe("timelineApi 附加", () => {
    it("startOrResume 调用 POST start-or-resume（长超时）", async () => {
      await timelineApi.startOrResume("1");
      expect(mockPost).toHaveBeenCalledWith("/timeline/1/start-or-resume", null, {
        timeout: 300000,
      });
    });

    it("status/cancel/resume 各端点", async () => {
      await timelineApi.status("1");
      expect(mockGet).toHaveBeenCalledWith("/timeline/1/status");
      await timelineApi.cancel("1");
      expect(mockPost).toHaveBeenCalledWith("/timeline/1/cancel");
      await timelineApi.resume("1");
      expect(mockPost).toHaveBeenCalledWith("/timeline/1/resume", null, {
        timeout: 300000,
      });
    });

    it("getTimeline 带 params 调用 GET", async () => {
      await timelineApi.getTimeline("1", { ordering: "narrative", causal: true });
      expect(mockGet).toHaveBeenCalledWith("/timeline/1", {
        params: { ordering: "narrative", causal: true },
      });
    });

    it("getVersion 调用 GET 版本详情", async () => {
      await timelineApi.getVersion("1", 3, { full_book: true });
      expect(mockGet).toHaveBeenCalledWith("/timeline/1/versions/3", {
        params: { full_book: true },
      });
    });

    it("rollback 调用 POST rollback", async () => {
      await timelineApi.rollback("1", 3, 2);
      expect(mockPost).toHaveBeenCalledWith("/timeline/1/rollback", {
        target_version_id: 3,
        expected_revision: 2,
      });
    });

    it("updateEvent / setFullBookPreference 调用 PUT", async () => {
      await timelineApi.updateEvent("1", "e1", "title", "新标题");
      expect(mockPut).toHaveBeenCalledWith("/timeline/1/events/e1", {
        field_name: "title",
        value: "新标题",
      });
      await timelineApi.setFullBookPreference("1", true);
      expect(mockPut).toHaveBeenCalledWith("/timeline/1/preference", {
        full_book: true,
      });
    });

    it("deleteEvent 调用 DELETE 旧端点", async () => {
      await timelineApi.deleteEvent("e1");
      expect(mockDelete).toHaveBeenCalledWith("/timeline/events/e1");
    });
  });

  describe("charactersApi 附加", () => {
    it("getCharacters 调用 GET /characters/:id", async () => {
      await charactersApi.getCharacters("1");
      expect(mockGet).toHaveBeenCalledWith("/characters/1");
    });

    it("getRelations 调用 GET /characters/:id/relations", async () => {
      await charactersApi.getRelations("1");
      expect(mockGet).toHaveBeenCalledWith("/characters/1/relations");
    });
  });

  describe("relationshipsApi", () => {
    it("getGraph 带过滤 params 调用 GET graph", async () => {
      await relationshipsApi.getGraph(7, {
        source: "active",
        through_chapter: 5,
        character_id: 3,
        relation_type: "ally",
        include_provisional: true,
      });
      expect(mockGet).toHaveBeenCalledWith("/relationships/7/graph", {
        params: {
          source: "active",
          version_id: undefined,
          through_chapter: 5,
          full_book: undefined,
          character_id: 3,
          relation_type: "ally",
          include_provisional: true,
        },
      });
    });

    it("getGraph 无 params 时全部 undefined", async () => {
      await relationshipsApi.getGraph(7);
      expect(mockGet).toHaveBeenCalledWith("/relationships/7/graph", {
        params: {
          source: undefined,
          version_id: undefined,
          through_chapter: undefined,
          full_book: undefined,
          character_id: undefined,
          relation_type: undefined,
          include_provisional: undefined,
        },
      });
    });

    it("getEvidence 调用 GET observations/:id/evidence", async () => {
      await relationshipsApi.getEvidence(7, 11, { source: "history" });
      expect(mockGet).toHaveBeenCalledWith(
        "/relationships/7/observations/11/evidence",
        {
          params: {
            source: "history",
            version_id: undefined,
            through_chapter: undefined,
            full_book: undefined,
          },
        }
      );
    });
  });

  describe("readerChatApi 附加", () => {
    it("listConversations 调用 GET conversations", async () => {
      await readerChatApi.listConversations(7, {
        status: "active",
        skip: 1,
        limit: 10,
      });
      expect(mockGet).toHaveBeenCalledWith("/novels/7/conversations", {
        params: { status: "active", skip: 1, limit: 10 },
      });
    });

    it("createConversation 默认标题与自定义标题", async () => {
      await readerChatApi.createConversation(7);
      expect(mockPost).toHaveBeenCalledWith("/novels/7/conversations", {
        title: "New chat",
      });
      await readerChatApi.createConversation(7, "自定义");
      expect(mockPost).toHaveBeenCalledWith("/novels/7/conversations", {
        title: "自定义",
      });
    });

    it("getConversation 调用 GET conversations/:id", async () => {
      await readerChatApi.getConversation(7, 42);
      expect(mockGet).toHaveBeenCalledWith("/novels/7/conversations/42");
    });

    it("patchConversation 调用 PATCH", async () => {
      await readerChatApi.patchConversation(7, 42, { title: "t" });
      expect(mockPatch).toHaveBeenCalledWith("/novels/7/conversations/42", {
        title: "t",
      });
    });

    it("deleteConversation 调用 DELETE", async () => {
      await readerChatApi.deleteConversation(7, 42);
      expect(mockDelete).toHaveBeenCalledWith("/novels/7/conversations/42");
    });

    it("listMessages 调用 GET messages", async () => {
      await readerChatApi.listMessages(7, 42, { after_sequence: 5 });
      expect(mockGet).toHaveBeenCalledWith("/novels/7/conversations/42/messages", {
        params: { after_sequence: 5, skip: undefined, limit: undefined },
      });
    });

    it("createMessage 调用 POST messages", async () => {
      const body = { client_message_id: "c1", body: "你好" };
      await readerChatApi.createMessage(7, 42, body);
      expect(mockPost).toHaveBeenCalledWith(
        "/novels/7/conversations/42/messages",
        body
      );
    });

    it("cancelJob / retryJob 调用 POST", async () => {
      await readerChatApi.cancelJob(7, 42, 9);
      expect(mockPost).toHaveBeenCalledWith(
        "/novels/7/conversations/42/jobs/9/cancel"
      );
      await readerChatApi.retryJob(7, 42, 9);
      expect(mockPost).toHaveBeenCalledWith(
        "/novels/7/conversations/42/jobs/9/retry"
      );
    });
  });

  describe("agentApi 附加", () => {
    it("cancelRun 调用 POST cancel", async () => {
      await agentApi.cancelRun(7, 9);
      expect(mockPost).toHaveBeenCalledWith("/agent/novels/7/skill-runs/9/cancel");
    });
  });

  describe("fanfictionApi 附加", () => {
    it("list 调用 GET /fanfiction/:novelId", async () => {
      await fanfictionApi.list("1");
      expect(mockGet).toHaveBeenCalledWith("/fanfiction/1");
    });

    it("continueWriting 调用 POST continue", async () => {
      await fanfictionApi.continueWriting("f1", "继续写");
      expect(mockPost).toHaveBeenCalledWith("/fanfiction/f1/continue", {
        prompt: "继续写",
      });
    });
  });

  describe("settingsApi / usageApi / searchApi", () => {
    it("settingsApi.getRouting / putRouting", async () => {
      await settingsApi.getRouting();
      expect(mockGet).toHaveBeenCalledWith("/settings/routing");
      await settingsApi.putRouting("quality");
      expect(mockPut).toHaveBeenCalledWith("/settings/routing", {
        preference: "quality",
      });
    });

    it("usageApi.summary 调用 GET /usage/summary", async () => {
      await usageApi.summary();
      expect(mockGet).toHaveBeenCalledWith("/usage/summary");
    });

    it("searchApi.global 默认与自定义 top_k", async () => {
      await searchApi.global("query");
      expect(mockPost).toHaveBeenCalledWith("/search", { query: "query", top_k: 10 });
      await searchApi.global("query", 5);
      expect(mockPost).toHaveBeenCalledWith("/search", { query: "query", top_k: 5 });
    });

    it("searchApi.inNovel 调用 POST /search/novels/:id", async () => {
      await searchApi.inNovel(3, "q", 20);
      expect(mockPost).toHaveBeenCalledWith("/search/novels/3", {
        query: "q",
        top_k: 20,
      });
    });
  });

  describe("evalApi 附加", () => {
    it("updateDataset 调用 PATCH", async () => {
      await evalApi.updateDataset(1, { status: "confirmed" });
      expect(mockPatch).toHaveBeenCalledWith("/eval/datasets/1", {
        status: "confirmed",
      });
    });

    it("getRun 调用 GET /eval/runs/:id", async () => {
      await evalApi.getRun(3);
      expect(mockGet).toHaveBeenCalledWith("/eval/runs/3");
    });

    it("createRun 调用 POST /eval/runs", async () => {
      const body = { run_name: "r", strategy: "s", novel_id: 1, dataset_ids: [1] };
      await evalApi.createRun(body);
      expect(mockPost).toHaveBeenCalledWith("/eval/runs", body);
    });

    it("listQualityRuns 调用 GET /eval/quality/runs", async () => {
      await evalApi.listQualityRuns();
      expect(mockGet).toHaveBeenCalledWith("/eval/quality/runs");
    });

    it("getQualityRun 调用 GET /eval/quality/runs/:jobId", async () => {
      await evalApi.getQualityRun("j1");
      expect(mockGet).toHaveBeenCalledWith("/eval/quality/runs/j1");
    });

    it("createQualityRun 调用 POST /eval/quality/runs", async () => {
      const body = { snapshot: {}, cases: [] };
      await evalApi.createQualityRun(body as any);
      expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs", body);
    });

    it("createQualityRunFromNovel 调用 POST from-novel", async () => {
      await evalApi.createQualityRunFromNovel({ novel_id: 1, dataset_ids: [1] });
      expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs/from-novel", {
        novel_id: 1,
        dataset_ids: [1],
      });
    });

    it("resumeQualityRun / cancelQualityRun 调用 POST", async () => {
      await evalApi.resumeQualityRun("j1");
      expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs/j1/resume");
      await evalApi.cancelQualityRun("j1");
      expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs/j1/cancel");
    });
  });
});
