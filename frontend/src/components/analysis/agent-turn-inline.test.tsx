import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentTurnInline } from "./agent-turn-inline";

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
  mocks.cancelRun.mockResolvedValue({ data: {} });
  mocks.streamAgentRun.mockImplementation(async (_url, _body, opts: StreamOpts) => {
    streamOpts = opts;
    await new Promise<void>((resolve) => {
      resolveStream = resolve;
    });
  });
});

afterEach(() => cleanup());

function renderTurn(overrides: Partial<React.ComponentProps<typeof AgentTurnInline>> = {}) {
  const props: React.ComponentProps<typeof AgentTurnInline> = {
    novelId: "11",
    initialQuestion: "主角是谁？",
    onCitationNavigate: vi.fn(),
    ...overrides,
  };
  return render(<AgentTurnInline {...props} />);
}

async function waitStream() {
  await waitFor(() => expect(mocks.streamAgentRun).toHaveBeenCalledTimes(1));
  expect(streamOpts).toBeTruthy();
}

describe("AgentTurnInline", () => {
  it("starts a run on mount without a skill (backend auto-routes)", async () => {
    renderTurn();
    await waitStream();
    const [url, body] = mocks.streamAgentRun.mock.calls[0];
    expect(url).toBe("/agent/novels/11/runs");
    expect(body).toMatchObject({ question: "主角是谁？", branch: null });
    expect(body.input).toEqual({});
    expect("skill" in (body as Record<string, unknown>)).toBe(false);
    expect(screen.getByTestId("agent-turn-question")).toHaveTextContent(
      "主角是谁？"
    );
  });

  it("passes a hidden skill override when provided (still no UI selector)", async () => {
    renderTurn({ skill: "illustrate-scene", initialQuestion: "生成插图" });
    await waitStream();
    const body = mocks.streamAgentRun.mock.calls[0][1] as Record<string, unknown>;
    expect(body.skill).toBe("illustrate-scene");
  });

  it("streams delta answer into a message bubble", async () => {
    renderTurn();
    await waitStream();
    streamOpts?.onEvent({ type: "delta", text: "主角是林墨。" });
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-inline")).toHaveTextContent(
        "主角是林墨。"
      )
    );
  });

  it("renders tool call chips from tool_start/tool_end", async () => {
    renderTurn();
    await waitStream();
    streamOpts?.onEvent({ type: "tool_start", toolName: "search_novel_text" });
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-tool-call")).toHaveAttribute(
        "data-status",
        "running"
      )
    );
    streamOpts?.onEvent({
      type: "tool_end",
      toolName: "search_novel_text",
      isError: false,
    });
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-tool-call")).toHaveAttribute(
        "data-status",
        "done"
      )
    );
  });

  it("finalizes on run_end and calls onDone + loads latest artifact", async () => {
    const onDone = vi.fn();
    mocks.getLatestArtifact.mockResolvedValue({
      id: 7,
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      status: "candidate",
    });
    renderTurn({ onDone });
    await waitStream();
    streamOpts?.onEvent({ type: "run_end", runId: 42, status: "completed" });
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(screen.getByTestId("agent-turn-job-status")).toHaveAttribute(
      "data-status",
      "completed"
    );
    await waitFor(() =>
      expect(mocks.getLatestArtifact).toHaveBeenCalledWith("11")
    );
  });

  it("aborts and cancels the server run on cancel", async () => {
    mocks.getLatestRun.mockResolvedValue({
      id: 9,
      novel_id: 11,
      skill_version_id: 1,
      status: "running",
      status_reason: null,
      stop_reason: null,
      branch: null,
      input_hash: "a".repeat(64),
      error_code: null,
      cancel_requested: false,
      retry_count: 0,
      created_at: "",
      updated_at: "",
    });
    const onDone = vi.fn();
    renderTurn({ onDone });
    await waitStream();
    fireEvent.click(screen.getByTestId("agent-turn-cancel"));
    await waitFor(() => expect(mocks.cancelRun).toHaveBeenCalledWith("11", 9));
    expect(onDone).toHaveBeenCalled();
  });

  it("opens the approval request dialog on approval_request frame", async () => {
    renderTurn();
    await waitStream();
    streamOpts?.onEvent({
      type: "approval_request",
      request: {
        id: 5,
        action: "publish_illustration",
        status: "pending",
        payload_summary: { summary: "发布插图到第 3 段" },
      },
    });
    await waitFor(() =>
      expect(screen.getByTestId("approval-request-dialog")).toBeInTheDocument()
    );
  });

  it("renders an artifact preview with approve/reject affordances", async () => {
    mocks.getLatestArtifact.mockResolvedValue({
      id: 7,
      type: "illustration_revision",
      schema_version: "illustration-revision.v1",
      status: "candidate",
    });
    renderTurn();
    await waitStream();
    streamOpts?.onEvent({ type: "artifact", artifact_id: 7 });
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-artifact-preview")).toBeInTheDocument()
    );
    expect(screen.getByTestId("agent-turn-approve")).toBeInTheDocument();
    expect(screen.getByTestId("agent-turn-reject")).toBeInTheDocument();
  });

  it("shows an error banner on stream rejection", async () => {
    mocks.streamAgentRun.mockRejectedValueOnce(new Error("network down"));
    const onDone = vi.fn();
    renderTurn({ onDone });
    await waitFor(() =>
      expect(screen.getByTestId("agent-turn-error")).toHaveTextContent(
        "运行失败"
      )
    );
    expect(onDone).toHaveBeenCalled();
  });

  it("restarts the stream after React 18 StrictMode double-mount cleanup", async () => {
    // StrictMode dev 先挂载→cleanup（abort）→再挂载。cleanup 必须重置 startedRef，
    // 否则第二次真实挂载不会重启 SSE 流（回归守卫：修复前第二次挂载 0 次调用）。
    const { unmount } = renderTurn();
    await waitStream(); // 第一次挂载启动了流
    unmount(); // StrictMode 模拟卸载 → cleanup abort
    const { container } = renderTurn(); // 第二次真实挂载
    await waitFor(() =>
      expect(mocks.streamAgentRun).toHaveBeenCalledTimes(2)
    );
    expect(container).toBeTruthy();
  });
});
