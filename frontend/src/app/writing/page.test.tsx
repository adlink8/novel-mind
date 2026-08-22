import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WritingPage from "./page";
import type { Novel } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  novelList: vi.fn(),
  listProjects: vi.fn(),
  listForks: vi.fn(),
  listChapters: vi.fn(),
  createProject: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    novelsApi: {
      list: mocks.novelList,
    },
  };
});

vi.mock("@/lib/derivative-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/derivative-api")>(
    "@/lib/derivative-api"
  );
  return {
    ...actual,
    derivativeApi: {
      listProjects: mocks.listProjects,
      listForks: mocks.listForks,
      listChapters: mocks.listChapters,
      createProject: mocks.createProject,
    },
  };
});

vi.mock("@/components/writing/markdown-editor", () => ({
  MarkdownEditor: () => <div data-testid="markdown-editor" />,
}));

vi.mock("@/components/writing/visual-review-panel", () => ({
  VisualReviewPanel: () => <div data-testid="visual-review-panel" />,
}));

vi.mock("@/components/writing/export-panel", () => ({
  ExportPanel: () => <div data-testid="export-panel" />,
}));

const novel: Novel = {
  id: 7,
  title: "雾城",
  author: null,
  description: null,
  genre: null,
  word_count: 10,
  chapter_count: 3,
  status: "ready",
  chunk_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const project = {
  id: 11,
  novel_id: 7,
  fork_id: 21,
  fork_key: "fork-a",
  name: "创作项目",
  status: "active" as const,
  created_at: "2026-01-01T00:00:00Z",
};

const fork = {
  id: 21,
  novel_id: 7,
  fork_key: "fork-a",
  source_version_id: 3,
  source_version_key: "v3",
  created_at: "2026-01-01T00:00:00Z",
};

const chapter = {
  id: 31,
  novel_id: 7,
  project_id: 11,
  title: "第一章",
  status: "draft" as const,
  order_index: 0,
  created_at: "2026-01-01T00:00:00Z",
};

describe("WritingPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    mocks.novelList.mockResolvedValue({ data: { items: [novel], total: 1 } });
    mocks.listProjects.mockResolvedValue({ data: { items: [project], total: 1 } });
    mocks.listForks.mockResolvedValue({ data: { forks: [fork], total: 1 } });
    mocks.listChapters.mockResolvedValue({ data: { items: [chapter], total: 1 } });
    mocks.createProject.mockResolvedValue({
      data: {
        project: { ...project, id: 12, name: "新项目" },
      },
    });
  });

  it("加载书架并展示原作选择器与状态文案", async () => {
    render(<WritingPage />);
    expect(await screen.findByRole("option", { name: "雾城" })).toBeInTheDocument();
    expect(await screen.findByText(/选择或创建 derivative project/)).toBeInTheDocument();
  });

  it("无项目时展示占位提示", async () => {
    mocks.listProjects.mockResolvedValue({ data: { items: [], total: 0 } });
    render(<WritingPage />);
    expect(
      await screen.findByText(/选择左侧项目，或用显式 Canon Fork 创建一个新的项目开始写作/)
    ).toBeInTheDocument();
  });

  it("自动选择首个项目并加载章节", async () => {
    render(<WritingPage />);
    expect(await screen.findByTestId("markdown-editor")).toBeInTheDocument();
    await waitFor(() => expect(mocks.listChapters).toHaveBeenCalledWith(7, 11));
    expect(screen.getByText("草稿就绪，可以开始写作")).toBeInTheDocument();
    expect(screen.getByTestId("visual-review-panel")).toBeInTheDocument();
    expect(screen.getByTestId("export-panel")).toBeInTheDocument();
  });

  it("项目列表加载失败展示错误状态", async () => {
    mocks.listProjects.mockRejectedValue(new Error("boom"));
    render(<WritingPage />);
    expect(
      await screen.findByText("项目或 fork 加载失败，请稍后重试")
    ).toBeInTheDocument();
  });

  it("创建项目：缺名称报错", async () => {
    render(<WritingPage />);
    await screen.findByText(/选择或创建 derivative project/);
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请填写项目名称");
  });

  it("创建项目：缺 fork 报错", async () => {
    render(<WritingPage />);
    await screen.findByText(/选择或创建 derivative project/);
    fireEvent.change(screen.getByLabelText("新项目名称"), {
      target: { value: "新项目" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "必须显式选择一个 Canon Fork"
    );
  });

  it("创建项目成功：选择 fork、填名、提交并选中新项目", async () => {
    render(<WritingPage />);
    await screen.findByText(/选择或创建 derivative project/);
    fireEvent.change(screen.getByLabelText("新项目名称"), {
      target: { value: "新项目" },
    });
    fireEvent.change(screen.getByLabelText("Canon Fork（显式选择）"), {
      target: { value: "21" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() =>
      expect(mocks.createProject).toHaveBeenCalledWith(7, {
        fork_id: 21,
        name: "新项目",
      })
    );
    expect(await screen.findByText("项目已创建并绑定到所选 fork")).toBeInTheDocument();
  });

  it("创建项目失败展示错误", async () => {
    mocks.createProject.mockRejectedValue(new Error("boom"));
    render(<WritingPage />);
    await screen.findByText(/选择或创建 derivative project/);
    fireEvent.change(screen.getByLabelText("新项目名称"), {
      target: { value: "新项目" },
    });
    fireEvent.change(screen.getByLabelText("Canon Fork（显式选择）"), {
      target: { value: "21" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("创建项目失败");
  });

  it("书架为空展示提示", async () => {
    mocks.novelList.mockResolvedValue({ data: { items: [], total: 0 } });
    render(<WritingPage />);
    expect(await screen.findByText("请先导入一本原作")).toBeInTheDocument();
  });
});
