import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AgentWorkspacePanel,
} from "./agent-workspace-panel";
import type { AnalysisChapterRef } from "./analysis-chat-panel";
import type { StructureNodeSelection } from "@/components/structure/structure-types";

/**
 * AgentWorkspacePanel 单测（25.2-04 Task 3）。
 * - vi.hoisted：agent API + streamAgentRun + router 全 mock。
 * - vi.mock("@/lib/api")：importActual 展开，只覆盖 agentApi。
 * - vi.mock("@/lib/sse")：streamAgentRun 换为脚本化帧驱动（手动派发 delta/tool/artifact/run_end）。
 * - vi.mock("next/navigation")：routerPush spy。
 */

const mocks = vi.hoisted(() => ({
  getLatestRun: vi.fn(),
  getLatestArtifact: vi.fn(),
  getArtifact: vi.fn(),
  getArtifactContent: vi.fn(),
  cancelRun: vi.fn(),
  approveArtifact: vi.fn(),
  rejectArtifact: vi.fn(),
  streamAgentRun: vi.fn(),
  routerPush: vi.fn(),
  apiPost: vi.fn(),
  getAccessToken: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    agentApi: {
      getLatestRun: mocks.getLatestRun,
      getLatestArtifact: mocks.getLatestArtifact,
      getArtifact: mocks.getArtifact,
      getArtifactContent: mocks.getArtifactContent,
      cancelRun: mocks.cancelRun,
      approveArtifact: mocks.approveArtifact,
      rejectArtifact: mocks.rejectArtifact,
    },
    api: { post: mocks.apiPost },
    getAccessToken: mocks.getAccessToken,
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

/** 当前 streamAgentRun 收到的 opts（脚本化帧从这里手动派发）。 */
type StreamOpts = {
  signal?: AbortSignal;
  onEvent: (frame: Record<string, unknown>) => void;
  onError?: (err: unknown) => void;
};
let streamOpts: StreamOpts | null = null;
let resolveStream: (() => void) | null = null;

beforeEach(() => {
  vi.clearAllMocks();
  streamOpts = null;
  resolveStream = null;
  mocks.getLatestRun.mockResolvedValue(null);
  mocks.getLatestArtifact.mockResolvedValue(null);
  mocks.getArtifactContent.mockResolvedValue(null);
  mocks.streamAgentRun.mockImplementation(async (_url, _body, opts: StreamOpts) => {
    streamOpts = opts;
    await new Promise<void>((resolve) => {
      resolveStream = resolve;
    });
  });
});

afterEach(() => cleanup());

function renderPanel(
  overrides: Partial<React.ComponentProps<typeof AgentWorkspacePanel>> = {}
) {
  const props = {
    novelId: "11",
    chapters,
    fullBook: false,
    progressChapterId: 21,
    selection: selection as StructureNodeSelection | null,
    ...overrides,
  };
  return render(<AgentWorkspacePanel {...props} />);
}

async function submitQuestion(text: string) {
  fireEvent.change(screen.getByTestId("agent-input"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByTestId("agent-send"));
  await waitFor(() => expect(mocks.streamAgentRun).toHaveBeenCalledTimes(1));
  expect(streamOpts).toBeTruthy();
}

describe("AgentWorkspacePanel", () => {
  it("renders the empty workspace when no prior run exists", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("agent-empty")).toBeInTheDocument()
    );
    expect(mocks.getLatestRun).toHaveBeenCalledWith("11");
    expect(screen.getByTestId("agent-boundary")).toHaveTextContent(
      "基于你已读至第 1 章"
    );
  });

  it("rehydrates the latest run and candidate artifact on mount", async () => {
    mocks.getLatestRun.mockResolvedValue({
      id: 42,
      novel_id: 11,
      skill_version_id: 1,
      status: "completed",
      status_reason: "stop",
      stop_reason: "stop",
      branch: null,
      input_hash: "a".repeat(64),
      error_code: null,
      cancel_requested: false,
      retry_count: 0,
      created_at: "",
      updated_at: "",
    });
    mocks.getLatestArtifact.mockResolvedValue({
      id: 5,
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      status: "candidate",
    });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("agent-job-status")).toHaveAttribute(
        "data-status",
        "completed"
      )
    );
    await waitFor(() =>
      expect(screen.getByTestId("agent-artifact-status")).toHaveTextContent(
        "候选"
      )
    );
  });

  it("streams the answer incrementally and renders the tool summary", async () => {
    renderPanel();
    await submitQuestion("阿宁在竹林里看见了谁？");

    // streamAgentRun 调用约定：SSE 端点 + Bearer 由 sse.ts 注入，body 带固定技能。
    expect(mocks.streamAgentRun).toHaveBeenCalledWith(
      "/agent/novels/11/runs",
      {
        question: "阿宁在竹林里看见了谁？",
        skill: "answer-reading-question",
        input: {},
        branch: null,
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );

    // 第一段 delta：run_end 之前可见（增量渲染）。
    streamOpts!.onEvent({ type: "delta", text: "阿宁" });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-msg-0")).toHaveTextContent("阿宁")
    );
    expect(screen.getByTestId("agent-job-status")).toHaveAttribute(
      "data-status",
      "running"
    );

    // tool_start → 摘要条出现 running 条目。
    streamOpts!.onEvent({
      type: "tool_start",
      toolName: "search_novel_text",
      args: {},
    });
    await waitFor(() =>
      expect(screen.getByTestId("agent-tool-summary")).toHaveTextContent(
        "search_novel_text"
      )
    );
    expect(screen.getByTestId("agent-tool-call")).toHaveAttribute(
      "data-status",
      "running"
    );

    // 后续 delta 追加；tool_end → 条目转 done。
    streamOpts!.onEvent({ type: "delta", text: "在竹林里" });
    streamOpts!.onEvent({ type: "tool_end", toolName: "search_novel_text" });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-msg-0")).toHaveTextContent(
        "阿宁在竹林里"
      )
    );
    expect(screen.getByTestId("agent-tool-call")).toHaveAttribute(
      "data-status",
      "done"
    );

    // run_end completed → 终态 + 流 promise 结算。
    streamOpts!.onEvent({ type: "run_end", runId: 7, status: "completed" });
    resolveStream?.();
    await waitFor(() =>
      expect(screen.getByTestId("agent-job-status")).toHaveAttribute(
        "data-status",
        "completed"
      )
    );
  });

  it("cancels the stream and calls the cancel endpoint when a run id is known", async () => {
    // 第一次调用（mount restore）返回 null；第二次调用（submit 后 runId 发现）返回运行中 run。
    mocks.getLatestRun
      .mockResolvedValueOnce(null)
      .mockResolvedValue({
        id: 42,
        novel_id: 11,
        skill_version_id: 1,
        status: "running",
        status_reason: null,
        stop_reason: null,
        branch: null,
        input_hash: "b".repeat(64),
        error_code: null,
        cancel_requested: false,
        retry_count: 0,
        created_at: "",
        updated_at: "",
      });
    renderPanel();
    await submitQuestion("这一章的主线？");

    // 等 runId 发现（第二次 getLatestRun）完成并写回 ref。
    await waitFor(() => expect(mocks.getLatestRun).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 0));

    fireEvent.click(screen.getByTestId("agent-cancel"));
    await waitFor(() =>
      expect(mocks.cancelRun).toHaveBeenCalledWith("11", 42)
    );
    // 流被 abort（agent-service 断开即服务端取消，双保险）。
    expect(streamOpts!.signal?.aborted).toBe(true);
    expect(screen.getByTestId("agent-job-status")).toHaveAttribute(
      "data-status",
      "cancelled"
    );
  });

  it("approve round-trips through the approval endpoint and reflects the status", async () => {
    mocks.approveArtifact.mockResolvedValue({
      id: 5,
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      status: "approved",
    });
    renderPanel();
    await submitQuestion("背景设定？");

    streamOpts!.onEvent({
      type: "artifact",
      artifact: {
        id: 5,
        type: "cited_answer",
        schema_version: "cited-answer.v1",
        status: "candidate",
      },
    });
    await waitFor(() =>
      expect(screen.getByTestId("agent-artifact-status")).toHaveTextContent(
        "候选"
      )
    );

    fireEvent.click(screen.getByTestId("agent-approve"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-approve-confirm")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByTestId("agent-approve-confirm"));

    await waitFor(() =>
      expect(mocks.approveArtifact).toHaveBeenCalledWith(5)
    );
    await waitFor(() =>
      expect(screen.getByTestId("agent-artifact-status")).toHaveTextContent(
        "已批准"
      )
    );
  });

  it("reject round-trips through the reject endpoint and reflects the status", async () => {
    mocks.rejectArtifact.mockResolvedValue({
      id: 5,
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      status: "rejected",
    });
    renderPanel();
    await submitQuestion("关系线？");

    streamOpts!.onEvent({
      type: "artifact",
      artifact: {
        id: 5,
        type: "cited_answer",
        schema_version: "cited-answer.v1",
        status: "candidate",
      },
    });
    await waitFor(() =>
      expect(screen.getByTestId("agent-artifact-status")).toHaveTextContent(
        "候选"
      )
    );

    fireEvent.click(screen.getByTestId("agent-reject"));
    await waitFor(() =>
      expect(screen.getByTestId("agent-reject-confirm")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByTestId("agent-reject-confirm"));

    await waitFor(() => expect(mocks.rejectArtifact).toHaveBeenCalledWith(5));
    await waitFor(() =>
      expect(screen.getByTestId("agent-artifact-status")).toHaveTextContent(
        "已拒绝"
      )
    );
  });

  it("renders artifact citation chips and navigates to the reader URL", async () => {
    renderPanel();
    await submitQuestion("证据在哪？");

    streamOpts!.onEvent({ type: "delta", text: "证据出现在第三章。" });
    streamOpts!.onEvent({
      type: "artifact",
      artifact: {
        id: 5,
        type: "cited_answer",
        schema_version: "cited-answer.v1",
        status: "candidate",
        content: {
          answer: {
            answer_blocks: [
              {
                text: "证据出现在第三章。",
                citations: [
                  {
                    chapter_id: 23,
                    source_start: 10,
                    source_end: 14,
                    evidence_key: "evidence:1",
                    block_id: "b1",
                    context_evidence_ref_id: 7,
                  },
                ],
              },
            ],
          },
        },
      },
    });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-citation")).toBeInTheDocument()
    );

    fireEvent.click(screen.getByTestId("reader-chat-citation"));
    expect(mocks.routerPush).toHaveBeenCalledWith(
      "/novels/11?chapter=23&start=10&from=timeline"
    );
  });

  it("keeps panel state across prop identity churn without resetting the run", async () => {
    // 相同 novelId 换 chapters 引用：不触发换书重置，流内容保留。
    const { rerender } = renderPanel();
    await submitQuestion("测试保留");
    streamOpts!.onEvent({ type: "delta", text: "部分内容" });
    await waitFor(() =>
      expect(screen.getByTestId("reader-chat-msg-0")).toHaveTextContent(
        "部分内容"
      )
    );

    rerender(
      <AgentWorkspacePanel
        novelId="11"
        chapters={[...chapters]}
        fullBook={false}
        progressChapterId={21}
        selection={selection as StructureNodeSelection | null}
      />
    );
    expect(screen.getByTestId("reader-chat-msg-0")).toHaveTextContent(
      "部分内容"
    );
  });

  it("approval_request 帧 → 渲染对话框 → 确认 POST 回 FastAPI（round trip）", async () => {
    renderPanel();
    await submitQuestion("发布这张插画");

    // agent-service 发 approval_request SSE 帧（只通知，不携带决策权威）。
    streamOpts!.onEvent({
      type: "approval_request",
      request: {
        id: 42,
        run_id: 9,
        action: "publish_illustration",
        payload_summary: { action: "publish_illustration" },
        status: "pending",
      },
    });

    // 对话框渲染出 action。
    await waitFor(() =>
      expect(screen.getByTestId("approval-request-dialog")).toBeInTheDocument()
    );
    expect(screen.getByTestId("approval-action")).toHaveTextContent(
      "publish_illustration"
    );

    // 点 "批准一次" → POST confirm {mode:"once"}，带 token 认证。
    mocks.getAccessToken.mockReturnValue("token-roundtrip");
    mocks.apiPost.mockResolvedValue({
      data: { id: 42, action: "publish_illustration", status: "approved" },
    });
    fireEvent.click(screen.getByTestId("approval-approve-once"));
    await waitFor(() => expect(mocks.apiPost).toHaveBeenCalledTimes(1));
    const [url, body, config] = mocks.apiPost.mock.calls[0];
    expect(url).toBe("/agent/approval-requests/42/confirm");
    expect(body).toEqual({ mode: "once" });
    expect(config.headers.Authorization).toBe("Bearer token-roundtrip");

    // 决策成功后对话框关闭。
    await waitFor(() =>
      expect(screen.queryByTestId("approval-request-dialog")).not.toBeInTheDocument()
    );
  });
});
