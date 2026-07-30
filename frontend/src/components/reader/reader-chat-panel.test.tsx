import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReaderChatPanel } from "./reader-chat-panel";
import type { SelectionCoordinate } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  patchConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listMessages: vi.fn(),
  createMessage: vi.fn(),
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
  generateImage: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    readerChatApi: {
      listConversations: mocks.listConversations,
      createConversation: mocks.createConversation,
      patchConversation: mocks.patchConversation,
      deleteConversation: mocks.deleteConversation,
      listMessages: mocks.listMessages,
      createMessage: mocks.createMessage,
      getJob: mocks.getJob,
      cancelJob: mocks.cancelJob,
      retryJob: mocks.retryJob,
    },
    pollReaderChatJob: vi.fn(async (_n, _c, _j, opts?: { onUpdate?: (j: unknown) => void }) => {
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
    }),
  };
});

vi.mock("@/lib/image-api", () => ({
  imageApi: { generate: mocks.generateImage },
}));

const selection: SelectionCoordinate = {
  chapter_id: 1,
  source_start: 0,
  source_end: 4,
  selection_text: "阿宁走进",
  selection_text_hash: "a".repeat(64),
  chapter_content_hash: "b".repeat(64),
};

function conv(id: number, title = `会话${id}`, status: "active" | "archived" = "active") {
  return {
    id,
    novel_id: 11,
    title,
    status,
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
  mocks.listConversations.mockResolvedValue({ data: { items: [conv(1)], total: 1, skip: 0, limit: 50 } });
  mocks.listMessages.mockResolvedValue({
    data: { items: [], total: 0, skip: 0, limit: 200, after_sequence: 0 },
  });
  mocks.createConversation.mockResolvedValue({ data: conv(2, "新会话") });
  mocks.patchConversation.mockImplementation(async (_n, id, body) => ({
    data: { ...conv(id), ...body },
  }));
  mocks.deleteConversation.mockResolvedValue({});
  mocks.createMessage.mockResolvedValue({
    data: {
      message: {
        id: 101,
        conversation_id: 1,
        sequence: 0,
        role: "user",
        body: "这段什么意思？",
        client_message_id: "cm-1",
        reply_to_message_id: null,
        selection: {
          chapter_id: 1,
          source_start: 0,
          source_end: 4,
          selection_text_hash: "a".repeat(64),
          chapter_content_hash: "b".repeat(64),
        },
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
  mocks.generateImage.mockResolvedValue({
    data: {
      id: 88,
      message_id: 108,
      image_url: "/storage/images/11/test.png",
      prompt: "阿宁走进\n\n画面补充：电影感",
      prompt_cn: "阿宁走进\n\n画面补充：电影感",
      created_at: "2026-07-15T00:00:00Z",
      width: 1024,
      height: 1024,
      file_size: 100,
    },
  });
});

afterEach(() => cleanup());

function renderPanel(
  overrides: Partial<React.ComponentProps<typeof ReaderChatPanel>> = {}
) {
  const props = {
    novelId: "11",
    currentChapterId: 1,
    layout: "desktop" as const,
    open: true,
    collapsed: false,
    onOpenChange: vi.fn(),
    onCollapsedChange: vi.fn(),
    pendingSelection: selection as SelectionCoordinate | null,
    onClearSelection: vi.fn(),
    onCitationNavigate: vi.fn(),
    ...overrides,
  };
  return { ...render(<ReaderChatPanel {...props} />), props };
}

describe("ReaderChatPanel", () => {
  it("renders empty state and desktop non-overlay panel attribute", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-panel")).toBeInTheDocument()
    );
    expect(screen.getByTestId("reader-chat-panel")).toHaveAttribute(
      "data-layout",
      "desktop"
    );
    expect(screen.getByTestId("reader-chat-empty")).toBeInTheDocument();
    expect(screen.getByTestId("reader-chat-selection-preview")).toHaveTextContent(
      "阿宁走进"
    );
  });

  it("mobile collapsed shows chip only", () => {
    renderPanel({ layout: "mobile", collapsed: true });
    expect(screen.getByTestId("reader-chat-chip")).toBeInTheDocument();
    expect(screen.queryByTestId("reader-chat-panel")).not.toBeInTheDocument();
  });

  it("desktop collapsed shows rail without full panel", () => {
    renderPanel({ layout: "desktop", collapsed: true, open: true });
    expect(screen.getByTestId("reader-chat-rail")).toBeInTheDocument();
    expect(screen.getByTestId("reader-chat-expand")).toBeInTheDocument();
    expect(screen.queryByTestId("reader-chat-panel")).not.toBeInTheDocument();
  });

  it("mobile expanded panel is height-bounded", async () => {
    renderPanel({ layout: "mobile", collapsed: false });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-panel")).toBeInTheDocument()
    );
    expect(screen.getByTestId("reader-chat-panel").className).toMatch(/max-h-\[45vh\]/);
    // 底部偏移需避开悬浮移动导航（含 safe-area）
    expect(screen.getByTestId("reader-chat-panel").parentElement?.className).toMatch(/bottom-\[calc/);
  });

  it("does not close when clicking outside — only collapse/close buttons", async () => {
    const { props } = renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-panel")).toBeInTheDocument()
    );
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => resolve())
    );
    fireEvent.pointerDown(document.body);
    expect(props.onOpenChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("折叠对话"));
    expect(props.onCollapsedChange).toHaveBeenCalledWith(true);
  });

  it("creates, switches, archives, restores and deletes conversations", async () => {
    const { props } = renderPanel();
    await waitFor(() => expect(screen.getByLabelText("归档会话")).toBeInTheDocument());

    mocks.listConversations.mockResolvedValue({
      data: {
        items: [conv(1), conv(2, "新会话")],
        total: 2,
        skip: 0,
        limit: 50,
      },
    });
    mocks.createConversation.mockResolvedValue({ data: conv(2, "新会话") });
    fireEvent.click(screen.getByLabelText("新建会话"));
    await waitFor(() => expect(mocks.createConversation).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-conv-2")).toBeInTheDocument()
    );

    // Switch back to conversation 1
    fireEvent.click(screen.getByTestId("reader-chat-conv-1"));
    await waitFor(() => expect(screen.getByLabelText("归档会话")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("归档会话"));
    await waitFor(() =>
      expect(mocks.patchConversation).toHaveBeenCalledWith("11", 1, {
        status: "archived",
      })
    );

    fireEvent.click(screen.getByLabelText("删除会话"));
    await waitFor(() =>
      expect(mocks.deleteConversation).toHaveBeenCalledWith("11", 1)
    );
    expect(props.onOpenChange).not.toHaveBeenCalled();
  });

  it("sends with immutable selection and never invents assistant body", async () => {
    const onClear = vi.fn();
    renderPanel({ onClearSelection: onClear });
    await waitFor(() => expect(mocks.listConversations).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId("reader-chat-input"), {
      target: { value: "这段什么意思？" },
    });
    fireEvent.click(screen.getByTestId("reader-chat-send"));

    await waitFor(() => expect(mocks.createMessage).toHaveBeenCalled());
    const body = mocks.createMessage.mock.calls[0][2];
    expect(body.selection).toEqual(selection);
    expect(body.chapter_id).toBe(1);
    expect(body.body).toBe("这段什么意思？");
    expect(body.client_message_id).toBeTruthy();

    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-msg-101")).toHaveTextContent(
        "这段什么意思？"
      )
    );
    // No optimistic assistant fabricated
    expect(screen.queryByText("阿宁走进竹林")).not.toBeInTheDocument();
    expect(onClear).toHaveBeenCalled();
  });

  it("sends a current-chapter question without fabricating a selection", async () => {
    renderPanel({ pendingSelection: null, currentChapterId: 7 });
    await waitFor(() => expect(mocks.listConversations).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId("reader-chat-input"), {
      target: { value: "这一章的主要冲突是什么？" },
    });
    fireEvent.click(screen.getByTestId("reader-chat-send"));

    await waitFor(() => expect(mocks.createMessage).toHaveBeenCalled());
    const body = mocks.createMessage.mock.calls[0][2];
    expect(body.chapter_id).toBe(7);
    expect(body.selection).toBeUndefined();
  });

  it("generates an image from the selected text in image mode", async () => {
    renderPanel({ mode: "image" });
    await waitFor(() => expect(screen.getByTestId("reader-chat-mode-image")).toHaveAttribute("aria-selected", "true"));

    expect(screen.getByTestId("reader-chat-input")).toHaveValue("阿宁走进");
    fireEvent.change(screen.getByTestId("reader-chat-input"), {
      target: { value: "阿宁走进，电影感" },
    });
    fireEvent.click(screen.getByTestId("reader-chat-send"));

    await waitFor(() => expect(mocks.generateImage).toHaveBeenCalled());
    expect(mocks.generateImage.mock.calls[0][0]).toBe("11");
    expect(mocks.generateImage.mock.calls[0][1]).toMatchObject({
      conversation_id: 1,
      chapter_id: 1,
      selected_text: "阿宁走进",
      user_refine: "阿宁走进，电影感",
      source_start: 0,
      source_end: 4,
    });
  });

  it("shows job loading, cancel and retry affordances", async () => {
    mocks.listMessages.mockResolvedValue({
      data: {
        items: [
          {
            id: 1,
            conversation_id: 1,
            sequence: 0,
            role: "user",
            body: "q",
            client_message_id: "c",
            reply_to_message_id: null,
            selection: null,
            citations: [],
            generation_job: {
              id: 9,
              user_message_id: 1,
              status: "running",
              status_reason: null,
              cancel_requested: false,
              retry_count: 0,
              error_code: null,
              created_at: "2026-07-15T00:00:00Z",
              updated_at: "2026-07-15T00:00:00Z",
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
    mocks.cancelJob.mockResolvedValue({
      data: {
        id: 9,
        user_message_id: 1,
        status: "cancelled",
        status_reason: "user_cancel",
        cancel_requested: true,
        retry_count: 0,
        error_code: "cancelled",
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:00:00Z",
      },
    });

    renderPanel({ pendingSelection: null });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-job-status")).toHaveAttribute(
        "data-status",
        "running"
      )
    );
    fireEvent.click(screen.getByLabelText("取消生成"));
    await waitFor(() => expect(mocks.cancelJob).toHaveBeenCalled());
  });

  it("renders citation chips and navigates to source offsets", async () => {
    const onNav = vi.fn();
    mocks.listMessages.mockResolvedValue({
      data: {
        items: [
          {
            id: 2,
            conversation_id: 1,
            sequence: 1,
            role: "assistant",
            body: "阿宁走进竹林。",
            client_message_id: null,
            reply_to_message_id: 1,
            selection: null,
            citations: [
              {
                block_id: "b1",
                evidence_key: "selection:1:0:4",
                context_evidence_ref_id: 7,
                chapter_id: 3,
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
    renderPanel({ onCitationNavigate: onNav, pendingSelection: null });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-citation")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByTestId("reader-chat-citation"));
    expect(onNav).toHaveBeenCalledWith({
      chapter_id: 3,
      source_start: 10,
      source_end: 14,
      evidence_key: "selection:1:0:4",
    });
  });

  it("displays budget/dependency paused states without domain apply UI", async () => {
    mocks.listMessages.mockResolvedValue({
      data: {
        items: [
          {
            id: 1,
            conversation_id: 1,
            sequence: 0,
            role: "user",
            body: "q",
            client_message_id: "c",
            reply_to_message_id: null,
            selection: null,
            citations: [],
            generation_job: {
              id: 9,
              user_message_id: 1,
              status: "paused_budget",
              status_reason: "budget_exceeded",
              cancel_requested: false,
              retry_count: 0,
              error_code: "budget_exceeded",
              created_at: "2026-07-15T00:00:00Z",
              updated_at: "2026-07-15T00:00:00Z",
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
    renderPanel({ pendingSelection: null });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-job-status")).toHaveTextContent(
        "预算已暂停"
      )
    );
    expect(screen.queryByText(/应用建议|确认写入|线索/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("重试生成")).toBeInTheDocument();
  });
});
