import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listNovels: vi.fn(),
  listProjects: vi.fn(),
  createProject: vi.fn(),
  listChapters: vi.fn(),
  listRevisions: vi.fn(),
  listOverrides: vi.fn(),
  createOverride: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  novelsApi: { list: mocks.listNovels },
  fanfictionApi: {
    list: mocks.listProjects,
    create: mocks.createProject,
    chapters: mocks.listChapters,
    revisions: mocks.listRevisions,
    overrides: mocks.listOverrides,
    createOverride: mocks.createOverride,
  },
}));

import { CreativeProjectEditor } from "./creative-project-editor";

const project = {
  id: 7,
  novel_id: 11,
  title: "未命名分支",
  prompt: "",
  content: "",
  style_config: null,
  parent_chapter_id: null,
  word_count: 0,
  status: "draft" as const,
  model_used: null,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listNovels.mockResolvedValue({ data: { items: [{ id: 11, title: "原作" }] } });
  mocks.listProjects.mockResolvedValue({ data: [] });
  mocks.createProject.mockResolvedValue({ data: project });
  mocks.listChapters.mockResolvedValue({ data: [] });
  mocks.listRevisions.mockResolvedValue({ data: [] });
  mocks.listOverrides.mockResolvedValue({ data: [] });
});

afterEach(() => cleanup());

describe("CreativeProjectEditor", () => {
  it("creates a local project and exposes the Markdown editor boundary", async () => {
    render(<CreativeProjectEditor />);

    const createButton = await screen.findByRole("button", { name: "新建分支草稿" });
    await waitFor(() => expect(createButton).not.toBeDisabled());
    fireEvent.click(createButton);

    expect(await screen.findByRole("textbox", { name: "项目 Markdown 内容" })).toBeInTheDocument();
    expect(screen.getAllByText(/Fanfiction Canon/).length).toBeGreaterThan(0);
    expect(mocks.createProject).toHaveBeenCalledWith({ novel_id: 11, title: "未命名分支" });
  });
});
