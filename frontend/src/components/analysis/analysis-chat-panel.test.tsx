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
    // 区间末章超出边界 → 锚点收窄到第 2 章并明示降级
    expect(screen.getByTestId("analysis-chat-anchor-note")).toHaveTextContent(
      "上下文锚定第 2 章（区间超出剧透边界，已收窄）"
    );
    expect(screen.getByTestId("analysis-chat-anchor-note")).toHaveTextContent(
      "25.1-01"
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
      "上下文锚定第 3 章"
    );
    expect(
      screen.getByTestId("analysis-chat-anchor-note")
    ).not.toHaveTextContent("已收窄");
  });

  it("falls back to first chapter boundary without reading progress", async () => {
    renderPanel({ progressChapterId: null });
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-boundary")).toHaveTextContent(
        "基于你已读至第 1 章"
      )
    );
  });

  it("sends anchored to the clamped structure-range chapter id without fabricating a selection", async () => {
    renderPanel();
    await waitFor(() => expect(mocks.listConversations).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "这一卷的主线是什么？" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));

    await waitFor(() => expect(mocks.createMessage).toHaveBeenCalled());
    const body = mocks.createMessage.mock.calls[0][2];
    // 结构范围末章第 3 章超出进度边界（第 2 章）→ 锚定第 2 章（id=22）
    expect(body.chapter_id).toBe(22);
    expect(body.selection).toBeUndefined();
    expect(body.body).toBe("这一卷的主线是什么？");
    expect(body.client_message_id).toBeTruthy();

    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-msg-101")).toHaveTextContent(
        "这一卷的主线是什么？"
      )
    );
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

  it("navigates citations to the reader page chapter without touching progress", async () => {
    mocks.listMessages.mockResolvedValue({
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
});
