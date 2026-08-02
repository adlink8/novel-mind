import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AnalysisChatPanel,
  type AnalysisChapterRef,
} from "./analysis-chat-panel";
import type { StructureNodeSelection } from "@/components/structure/structure-types";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  listMessages: vi.fn(),
  createMessage: vi.fn(),
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
  routerPush: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    readerChatApi: {
      listConversations: mocks.listConversations,
      createConversation: mocks.createConversation,
      listMessages: mocks.listMessages,
      createMessage: mocks.createMessage,
      getJob: mocks.getJob,
      cancelJob: mocks.cancelJob,
      retryJob: mocks.retryJob,
    },
    pollReaderChatJob: vi.fn(
      async (_n, _c, _j, opts?: { onUpdate?: (j: unknown) => void }) => {
        const job = {
          id: 9,
          user_message_id: 1,
          status: "completed" as const,
          status_reason: "published",
          cancel_requested: false,
          retry_count: 0,
          error_code: null,
          created_at: "2026-07-15T00:00:00Z",
          updated_at: "2026-07-15T00:00:00Z",
        };
        opts?.onUpdate?.(job);
        return job;
      }
    ),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

const chapters: AnalysisChapterRef[] = [
  { id: 21, chapter_number: 1, title: "第一章" },
  { id: 22, chapter_number: 2, title: "第二章" },
  { id: 23, chapter_number: 3, title: "第三章" },
];

const selection: StructureNodeSelection = {
  id: "book",
  kind: "book",
  chapterStart: 1,
  chapterEnd: 3,
  label: "全书",
};

function conv(id: number, title = `会话${id}`) {
  return {
    id,
    novel_id: 11,
    title,
    status: "active" as const,
    next_sequence: 1,
    last_opened_at: null,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    last_message_sequence: null,
    last_message_role: null,
    last_message_at: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listConversations.mockResolvedValue({
    data: { items: [conv(1)], total: 1, skip: 0, limit: 50 },
  });
  mocks.listMessages.mockResolvedValue({
    data: { items: [], total: 0, skip: 0, limit: 200, after_sequence: 0 },
  });
  mocks.createConversation.mockResolvedValue({ data: conv(2, "新会话") });
  mocks.createMessage.mockResolvedValue({
    data: {
      message: {
        id: 101,
        conversation_id: 1,
        sequence: 0,
        role: "user",
        body: "这一卷的主线是什么？",
        client_message_id: "cm-1",
        reply_to_message_id: null,
        selection: null,
        citations: [],
        generation_job: {
          id: 9,
          user_message_id: 101,
          status: "queued",
          status_reason: null,
          cancel_requested: false,
          retry_count: 0,
          error_code: null,
          created_at: "2026-07-15T00:00:00Z",
          updated_at: "2026-07-15T00:00:00Z",
        },
        created_at: "2026-07-15T00:00:00Z",
      },
      job: {
        id: 9,
        user_message_id: 101,
        status: "queued",
        status_reason: null,
        cancel_requested: false,
        retry_count: 0,
        error_code: null,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:00:00Z",
      },
    },
  });
});

afterEach(() => cleanup());

function renderPanel(
  overrides: Partial<React.ComponentProps<typeof AnalysisChatPanel>> = {}
) {
  const props = {
    novelId: "11",
    chapters,
    fullBook: false,
    progressChapterId: 22,
    selection: selection as StructureNodeSelection | null,
    ...overrides,
  };
  return { ...render(<AnalysisChatPanel {...props} />), props };
}

describe("AnalysisChatPanel", () => {
  it("renders empty conversation state with structure scope and progress boundary", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-empty")).toBeInTheDocument()
    );
    // 锚点=结构：范围提示显示当前结构选中区间
    expect(screen.getByTestId("analysis-chat-context")).toHaveTextContent(
      "第 1–3 章"
    );
    // 剧透边界：默认按阅读进度（进度 chapter_id=22 → 第 2 章）
    expect(screen.getByTestId("analysis-chat-boundary")).toHaveTextContent(
      "基于你已读至第 2 章"
    );
    // 区间末章超出边界 → 发送原始区间，服务端收窄（UI 预告）
    expect(screen.getByTestId("analysis-chat-anchor-note")).toHaveTextContent(
      "上下文范围：第 1–3 章（末章超出阅读进度，将按已读边界收窄）"
    );
  });

  it("shows full-book boundary and anchors to the range end", async () => {
    renderPanel({ fullBook: true });
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-boundary")).toHaveTextContent(
        "全书模式"
      )
    );
    expect(screen.getByTestId("analysis-chat-anchor-note")).toHaveTextContent(
      "上下文范围：第 1–3 章"
    );
    expect(
      screen.getByTestId("analysis-chat-anchor-note")
    ).not.toHaveTextContent("收窄");
  });

  it("falls back to first chapter boundary without reading progress", async () => {
    renderPanel({ progressChapterId: null });
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-boundary")).toHaveTextContent(
        "基于你已读至第 1 章"
      )
    );
  });

  it("sends the requested chapter_range without fabricating a selection", async () => {
    renderPanel();
    await waitFor(() => expect(mocks.listConversations).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "这一卷的主线是什么？" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));

    await waitFor(() => expect(mocks.createMessage).toHaveBeenCalled());
    const body = mocks.createMessage.mock.calls[0][2];
    // 发送原始请求区间（章号语义）；收窄由服务端完成并经 anchor 回显
    expect(body.chapter_range).toEqual({ chapter_start: 1, chapter_end: 3 });
    expect(body.chapter_id).toBeUndefined();
    expect(body.selection).toBeUndefined();
    expect(body.body).toBe("这一卷的主线是什么？");
    expect(body.client_message_id).toBeTruthy();

    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-msg-101")).toHaveTextContent(
        "这一卷的主线是什么？"
      )
    );
  });

  it("renders the server-narrowed anchor above replayed messages", async () => {
    mocks.listMessages.mockResolvedValue({
      data: {
        items: [
          {
            id: 55,
            conversation_id: 1,
            sequence: 0,
            role: "user",
            body: "第一二章发生了什么？",
            client_message_id: null,
            reply_to_message_id: null,
            selection: null,
            anchor: { kind: "chapter_range", chapter_start: 1, chapter_end: 2 },
            citations: [],
            generation_job: null,
            created_at: "2026-07-15T00:00:00Z",
          },
        ],
        total: 1,
        skip: 0,
        limit: 200,
        after_sequence: 0,
      },
    });
    renderPanel();
    await waitFor(() =>
      expect(
        screen.getByTestId("analysis-chat-msg-anchor-55")
      ).toHaveTextContent("范围：第 1–2 章")
    );
  });

  it("blocks sending when the range starts beyond reading progress", async () => {
    renderPanel({
      selection: {
        id: "late",
        kind: "book",
        chapterStart: 3,
        chapterEnd: 3,
        label: "后段",
      } as StructureNodeSelection,
    });
    await waitFor(() => expect(mocks.listConversations).toHaveBeenCalled());
    expect(screen.getByTestId("analysis-chat-anchor-note")).toHaveTextContent(
      "起始章超出阅读进度，无法发送"
    );
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "问点什么" },
    });
    expect(screen.getByTestId("analysis-chat-send")).toBeDisabled();
    expect(mocks.createMessage).not.toHaveBeenCalled();
  });

  it("disables send and explains when chapter data is unavailable", async () => {
    renderPanel({ chapters: [] });
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-anchor-note")).toHaveTextContent(
        "章节数据尚未加载"
      )
    );
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "问点什么" },
    });
    expect(screen.getByTestId("analysis-chat-send")).toBeDisabled();
    expect(mocks.createMessage).not.toHaveBeenCalled();
  });

  it("navigates citations to the reader page chapter without touching progress", async () => {    mocks.listMessages.mockResolvedValue({
      data: {
        items: [
          {
            id: 2,
            conversation_id: 1,
            sequence: 1,
            role: "assistant",
            body: "主线围绕雾中铃铛展开。",
            client_message_id: null,
            reply_to_message_id: 1,
            selection: null,
            citations: [
              {
                block_id: "b1",
                evidence_key: "chapter:23",
                context_evidence_ref_id: 7,
                chapter_id: 23,
                source_start: 10,
                source_end: 14,
              },
            ],
            generation_job: null,
            created_at: "2026-07-15T00:00:00Z",
          },
        ],
        total: 1,
        skip: 0,
        limit: 200,
        after_sequence: 0,
      },
    });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-citation")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByTestId("reader-chat-citation"));
    expect(mocks.routerPush).toHaveBeenCalledWith(
      "/novels/11?chapter=23&start=10&from=timeline"
    );
  });

  it("exposes the shared QueryPlan trace on assistant messages", async () => {
    mocks.listMessages.mockResolvedValue({
      data: {
        items: [
          {
            id: 77,
            conversation_id: 1,
            sequence: 1,
            role: "assistant",
            body: "主线围绕雾中铃铛展开。",
            client_message_id: null,
            reply_to_message_id: 76,
            selection: null,
            citations: [
              {
                block_id: "b1",
                evidence_key: "qp:23:10:14:abc",
                context_evidence_ref_id: 7,
                chapter_id: 23,
                source_start: 10,
                source_end: 14,
              },
            ],
            generation_job: null,
            queryplan: {
              trace_id: "a".repeat(32),
              plan_hash: "b".repeat(64),
              intent: "analysis",
              anchor_kind: "chapter_range",
              cutoff_mode: "reading_progress",
              through_chapter: 2,
              full_book_authorized: false,
              availability: [
                { dimension: "relations", status: "available", reason: "reader_ok", provenance: "exact_reader_v1" },
                { dimension: "character_state", status: "unavailable", reason: "dimension_unavailable", provenance: "deterministic_contract_v1" },
              ],
              fallback: { chain: ["exact_reader"] },
              manifest_checksum: "c".repeat(64),
              allowed_evidence_ids: ["qp:23:10:14:abc"],
              citation_jump: [
                {
                  evidence_key: "qp:23:10:14:abc",
                  chapter_id: 23,
                  chapter_number: 3,
                  source_start: 10,
                  source_end: 14,
                  excerpt: "主线围绕",
                },
              ],
              abstained: false,
            },
            created_at: "2026-07-15T00:00:00Z",
          },
        ],
        total: 1,
        skip: 0,
        limit: 200,
        after_sequence: 0,
      },
    });
    renderPanel();
    await waitFor(() =>
      expect(
        screen.getByTestId("analysis-chat-queryplan-77")
      ).toHaveTextContent("QueryPlan")
    );
    expect(screen.getByTestId("analysis-chat-queryplan-77")).toHaveTextContent(
      "分析"
    );
    expect(
      screen.getByTestId("analysis-chat-queryplan-77")
    ).toHaveTextContent("结构区间锚点");
    expect(
      screen.getByTestId("analysis-chat-queryplan-77")
    ).toHaveTextContent("已读至第 2 章");
    expect(
      screen.getByTestId("analysis-chat-queryplan-77")
    ).toHaveTextContent("引用 1");
    expect(
      screen.getByTestId("analysis-chat-queryplan-77")
    ).toHaveTextContent("部分维度不可用");
  });
});
