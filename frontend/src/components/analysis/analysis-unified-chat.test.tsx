import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalysisUnifiedChat } from "./analysis-unified-chat";
import type { AnalysisChapterRef } from "./analysis-chat-panel";
import type { StructureNodeSelection } from "@/components/structure/structure-types";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  listMessages: vi.fn(),
  createMessage: vi.fn(),
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
  streamAgentRun: vi.fn(),
  getLatestRun: vi.fn(),
  getLatestArtifact: vi.fn(),
  getArtifact: vi.fn(),
  getArtifactContent: vi.fn(),
  cancelRun: vi.fn(),
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
    pollReaderChatJob: vi.fn(async () => {
      // 悬停：保持非终态 job 可见，供取消/重试断言（终态由测试显式驱动）。
      await new Promise<void>(() => {
        /* hang until abort */
      });
    }),
    agentApi: {
      getLatestRun: mocks.getLatestRun,
      getLatestArtifact: mocks.getLatestArtifact,
      getArtifact: mocks.getArtifact,
      getArtifactContent: mocks.getArtifactContent,
      cancelRun: mocks.cancelRun,
      approveArtifact: vi.fn(),
      rejectArtifact: vi.fn(),
    },
  };
});

vi.mock("@/lib/sse", () => ({
  streamAgentRun: mocks.streamAgentRun,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

const chapters: AnalysisChapterRef[] = [
  { id: 21, chapter_number: 1, title: "第一章" },
  { id: 22, chapter_number: 2, title: "第二章" },
];

const selection: StructureNodeSelection = {
  id: "book",
  kind: "book",
  chapterStart: 1,
  chapterEnd: 2,
  label: "全书",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listConversations.mockResolvedValue({
    data: {
      items: [
        {
          id: 1,
          novel_id: 11,
          title: "会话 1",
          status: "active",
          next_sequence: 1,
          last_opened_at: null,
          created_at: "2026-07-15T00:00:00Z",
          updated_at: "2026-07-15T00:00:00Z",
          last_message_sequence: null,
          last_message_role: null,
          last_message_at: null,
        },
      ],
      total: 1,
      skip: 0,
      limit: 50,
    },
  });
  mocks.listMessages.mockResolvedValue({
    data: { items: [], total: 0, skip: 0, limit: 200, after_sequence: 0 },
  });
  mocks.createConversation.mockResolvedValue({
    data: {
      id: 1,
      novel_id: 11,
      title: "新会话",
      status: "active",
      next_sequence: 1,
      last_opened_at: null,
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:00Z",
      last_message_sequence: null,
      last_message_role: null,
      last_message_at: null,
    },
  });
  mocks.createMessage.mockResolvedValue({
    data: {
      message: {
        id: 101,
        conversation_id: 1,
        sequence: 0,
        role: "user",
        body: "主角是谁？",
        client_message_id: "cm-1",
        reply_to_message_id: null,
        selection: null,
        citations: [],
        generation_job: null,
        created_at: "2026-07-15T00:00:00Z",
      },
      job: null,
    },
  });
  mocks.streamAgentRun.mockImplementation(async () => {
    await new Promise<void>(() => {
      /* hang until test-driven frame dispatch or abort */
    });
  });
  mocks.getLatestRun.mockResolvedValue(null);
  mocks.getLatestArtifact.mockResolvedValue(null);
  mocks.getArtifactContent.mockResolvedValue(null);
});

afterEach(() => cleanup());

function renderChat(overrides: Partial<React.ComponentProps<typeof AnalysisUnifiedChat>> = {}) {
  const props: React.ComponentProps<typeof AnalysisUnifiedChat> = {
    novelId: "11",
    chapters,
    fullBook: false,
    progressChapterId: 21,
    selection: selection as StructureNodeSelection | null,
    ...overrides,
  };
  return render(<AnalysisUnifiedChat {...props} />);
}

async function waitReady() {
  await waitFor(() =>
    expect(screen.getByTestId("analysis-chat-panel")).toBeInTheDocument()
  );
  await waitFor(() =>
    expect(screen.getByTestId("analysis-chat-boundary")).toHaveTextContent(
      "基于你已读至第 1 章"
    )
  );
}

describe("AnalysisUnifiedChat", () => {
  it("renders the unified panel with boundary and empty state", async () => {
    renderChat();
    await waitReady();
    expect(screen.getByTestId("analysis-chat-empty")).toBeInTheDocument();
    expect(screen.getByTestId("analysis-chat-context")).toHaveTextContent(
      "范围："
    );
  });

  it("sends ordinary questions through reader_chat with a chapter_range anchor", async () => {
    renderChat();
    await waitReady();
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "主角是谁？" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));
    await waitFor(() => expect(mocks.createMessage).toHaveBeenCalled());
    const [, convId, body] = mocks.createMessage.mock.calls[0];
    expect(convId).toBe(1);
    expect(body.body).toBe("主角是谁？");
    expect(body.chapter_range).toEqual({ chapter_start: 1, chapter_end: 2 });
    expect(body.client_message_id).toBeTruthy();
    // 普通问句不触发智能体回合
    expect(mocks.streamAgentRun).not.toHaveBeenCalled();
    expect(screen.queryByTestId("agent-turn-inline")).not.toBeInTheDocument();
  });

  it("routes illustration intent to an inline agent turn without a skill", async () => {
    renderChat();
    await waitReady();
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "请为第一章配图" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-inline")).toBeInTheDocument()
    );
    expect(mocks.createMessage).not.toHaveBeenCalled();
    expect(mocks.streamAgentRun).toHaveBeenCalledTimes(1);
    const [url, body] = mocks.streamAgentRun.mock.calls[0];
    expect(url).toBe("/agent/novels/11/runs");
    expect(body.question).toBe("请为第一章配图");
    // 不注入 input 锚（路由 skill 的 schema 多为 additionalProperties:false，
    // 多余字段会 422；范围由问题文本承载 + Agent 只读工具解析）。
    expect(body.input).toEqual({});
    expect("skill" in body).toBe(false);
  });

  it("keeps agent turns and reader_chat messages in one unified message area", async () => {
    renderChat();
    await waitReady();
    // reader_chat 先发一条普通消息
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "主角是谁？" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));
    await waitFor(() => expect(mocks.createMessage).toHaveBeenCalled());

    // 再发一条续写意图 → 智能体回合追加进同一消息区
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "请续写这个故事" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-inline")).toBeInTheDocument()
    );
    const messagesContainer = screen.getByTestId("analysis-chat-messages");
    expect(messagesContainer).toContainElement(
      screen.getByTestId("agent-turn-inline")
    );
  });

  it("resets agent turns and conversations when switching novel", async () => {
    const { rerender } = renderChat();
    await waitReady();
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "请为第一章配图" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-inline")).toBeInTheDocument()
    );

    rerender(
      <AnalysisUnifiedChat
        novelId="22"
        chapters={chapters}
        fullBook={false}
        progressChapterId={21}
        selection={selection as StructureNodeSelection | null}
      />
    );
    await waitFor(() =>
      expect(screen.queryByTestId("agent-turn-inline")).not.toBeInTheDocument()
    );
  });

  it("surfaces reader_chat job status with cancel affordance", async () => {
    mocks.createMessage.mockResolvedValue({
      data: {
        message: {
          id: 101,
          conversation_id: 1,
          sequence: 0,
          role: "user",
          body: "主角是谁？",
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
    mocks.cancelJob.mockResolvedValue({ data: {} });
    renderChat();
    await waitReady();
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "主角是谁？" },
    });
    fireEvent.click(screen.getByTestId("analysis-chat-send"));
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-job-status")).toHaveAttribute(
        "data-status",
        "queued"
      )
    );
    fireEvent.click(screen.getByLabelText("取消生成"));
    await waitFor(() =>
      expect(mocks.cancelJob).toHaveBeenCalledWith("11", 1, 9)
    );
  });
});
