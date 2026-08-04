import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  patchChapter: vi.fn(),
  getChapter: vi.fn(),
  createChapter: vi.fn(),
  reorderChapters: vi.fn(),
  deleteChapter: vi.fn(),
  listRevisions: vi.fn(),
  getRevision: vi.fn(),
  diffRevisions: vi.fn(),
  rollbackChapter: vi.fn(),
}));

vi.mock("@/lib/derivative-api", () => ({
  derivativeApi: {
    patchChapter: mocks.patchChapter,
    getChapter: mocks.getChapter,
    createChapter: mocks.createChapter,
    reorderChapters: mocks.reorderChapters,
    deleteChapter: mocks.deleteChapter,
    listRevisions: mocks.listRevisions,
    getRevision: mocks.getRevision,
    diffRevisions: mocks.diffRevisions,
    rollbackChapter: mocks.rollbackChapter,
  },
}));

import { MarkdownEditor } from "./markdown-editor";
import type { DerivativeChapterView, DerivativeProjectView } from "@/lib/derivative-api";

const H64 = "a".repeat(64);

const project: DerivativeProjectView = {
  id: 5,
  owner_id: 1,
  novel_id: 11,
  fork_id: 3,
  project_key: "my-story",
  name: "我的分支",
  description: null,
  status: "active",
  space: "fanfiction_canon",
  fork_key: "ff-branch",
  source_version_key: "original:1",
  source_snapshot_hash: H64,
  through_chapter: 3,
  full_book_authorized: false,
  cutoff_snapshot_hash: "c".repeat(64),
  scope_hash: H64,
  manifest_hash: H64,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
};

const chapterA: DerivativeChapterView = {
  id: 10,
  project_id: 5,
  owner_id: 1,
  novel_id: 11,
  position: 0,
  title: "第一章",
  markdown: "## 开场\n正文",
  markdown_checksum: "d".repeat(64),
  status: "draft",
  revision: 1,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
};

const chapterB: DerivativeChapterView = {
  ...chapterA,
  id: 11,
  position: 1,
  title: "第二章",
  markdown: "",
};

const serverChapterA: DerivativeChapterView = {
  ...chapterA,
  markdown: "## 开场\n服务器端更新",
  revision: 2,
  markdown_checksum: "e".repeat(64),
};

/** A real AxiosError-shaped rejection so `axios.isAxiosError` matches. */
function staleWriteError() {
  const err = new Error("Request failed with status code 409") as Error & {
    isAxiosError?: boolean;
    response?: unknown;
  };
  err.isAxiosError = true;
  err.response = {
    status: 409,
    data: {
      detail: `revision_conflict: stale write: chapter 10 is at revision 2 with checksum ${H64}; client sent base_revision 1`,
    },
  };
  return err;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.patchChapter.mockResolvedValue({ data: serverChapterA });
  mocks.getChapter.mockResolvedValue({ data: serverChapterA });
  mocks.createChapter.mockResolvedValue({
    data: {
      chapter: { ...chapterB, id: 12, title: "第 3 章" },
      scope: { project_id: 5 },
    },
  });
  mocks.reorderChapters.mockResolvedValue({ data: { items: [chapterB, chapterA] } });
  mocks.deleteChapter.mockResolvedValue({});
  mocks.listRevisions.mockResolvedValue({
    data: {
      chapter_id: 10,
      project_id: 5,
      total: 1,
      items: [
        {
          id: 30,
          chapter_id: 10,
          project_id: 5,
          revision_number: 2,
          parent_revision_id: 10,
          kind: "autosave",
          content_checksum: H64,
          actor_id: 1,
          reason: null,
          approval_state: "not_required",
          created_at: "2026-08-04T00:00:02Z",
        },
        {
          id: 10,
          chapter_id: 10,
          project_id: 5,
          revision_number: 1,
          parent_revision_id: null,
          kind: "create",
          content_checksum: H64,
          actor_id: 1,
          reason: null,
          approval_state: "not_required",
          created_at: "2026-08-04T00:00:01Z",
        },
      ],
    },
  });
  mocks.getRevision.mockResolvedValue({
    data: {
      ...chapterA,
      id: 10,
      content: "## 开场\n服务器端更新",
      updated_at: "2026-08-04T00:00:00Z",
    },
  });
  mocks.diffRevisions.mockResolvedValue({
    data: {
      base_revision_id: 10,
      base_revision_number: 1,
      target_revision_id: 30,
      target_revision_number: 2,
      additions: 1,
      deletions: 1,
      hunks: [
        {
          old_start: 1,
          old_count: 1,
          new_start: 1,
          new_count: 1,
          lines: [
            { op: "delete", text: "正文" },
            { op: "add", text: "服务器端更新" },
          ],
        },
      ],
    },
  });
});

afterEach(() => cleanup());

function renderEditor(chapters: DerivativeChapterView[] = [chapterA, chapterB]) {
  const onChaptersChange = vi.fn();
  render(
    <MarkdownEditor
      novelId={11}
      project={project}
      chapters={chapters}
      onChaptersChange={onChaptersChange}
    />
  );
  return onChaptersChange;
}

describe("MarkdownEditor", () => {
  it("shows the explicit fork/namespace/version/cutoff scope", async () => {
    renderEditor();
    // Namespace is sealed to Fanfiction Canon (D-36-03).
    expect(await screen.findByText("fanfiction_canon")).toBeInTheDocument();
    expect(screen.getByText("ff-branch")).toBeInTheDocument();
    expect(screen.getByText("original:1")).toBeInTheDocument();
    expect(screen.getByText(/第 3 章/)).toBeInTheDocument();
    // The cut-off hash prefix is echoed.
    expect(screen.getByText(/cccccccc…/)).toBeInTheDocument();
  });

  it("exposes no Original/Interpretation write entry point", async () => {
    renderEditor();
    await screen.findByText("fanfiction_canon");
    // The literal original_canon space and Original write labels never appear.
    expect(screen.queryByText("original_canon")).not.toBeInTheDocument();
    expect(screen.queryByText("user_interpretation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Original/i })).not.toBeInTheDocument();
  });

  it("lists the ordered chapter plan and marks a dirty edit", async () => {
    renderEditor();
    expect(await screen.findByText(/第一章/)).toBeInTheDocument();
    expect(screen.getByText(/第二章/)).toBeInTheDocument();

    const textbox = screen.getByRole("textbox", { name: "章节 Markdown 内容" });
    fireEvent.change(textbox, { target: { value: "## 开场\n新的正文" } });
    expect(screen.getByTestId("editor-save-state")).toHaveTextContent("dirty");
  });

  it("saves with the chapter base_revision (only allowed fields)", async () => {
    renderEditor();
    const textbox = await screen.findByRole("textbox", { name: "章节 Markdown 内容" });
    fireEvent.change(textbox, { target: { value: "## 开场\n新正文" } });

    const saveButton = screen.getByRole("button", { name: /保存/ });
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(mocks.patchChapter).toHaveBeenCalledWith(11, 5, 10, {
        title: "第一章",
        markdown: "## 开场\n新正文",
        base_revision: 1,
      })
    );
    // Save request never carries owner/project/revision/checksum fields.
    const sent = mocks.patchChapter.mock.calls[0][3];
    expect(sent).not.toHaveProperty("owner_id");
    expect(sent).not.toHaveProperty("novel_id");
    expect(sent).not.toHaveProperty("project_id");
    expect(sent).not.toHaveProperty("revision");
    expect(sent).not.toHaveProperty("markdown_checksum");
    expect(sent).not.toHaveProperty("space");
  });

  it("surfaces a stale-write conflict and can reload from the server", async () => {
    mocks.patchChapter.mockRejectedValue(staleWriteError());
    renderEditor();

    const textbox = await screen.findByRole("textbox", { name: "章节 Markdown 内容" });
    fireEvent.change(textbox, { target: { value: "旧标签的覆盖" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/revision 2/);
    expect(screen.getByTestId("editor-save-state")).toHaveTextContent("conflict");

    fireEvent.click(screen.getByRole("button", { name: /重新加载/ }));
    await waitFor(() =>
      expect(mocks.getChapter).toHaveBeenCalledWith(11, 5, 10)
    );
    await waitFor(() =>
      expect(screen.getByTestId("editor-save-state")).toHaveTextContent("idle")
    );
  });

  it("creates a chapter appended to the plan", async () => {
    const onChaptersChange = renderEditor();
    await screen.findByText(/第一章/);

    fireEvent.click(screen.getByRole("button", { name: /新增章节/ }));

    await waitFor(() =>
      expect(mocks.createChapter).toHaveBeenCalledWith(11, 5, {
        title: "第 3 章",
        markdown: "",
      })
    );
    expect(onChaptersChange).toHaveBeenCalled();
    const lastCall = onChaptersChange.mock.calls.at(-1)![0] as DerivativeChapterView[];
    expect(lastCall.map((c) => c.id)).toEqual([10, 11, 12]);
  });

  it("blocks editing for an archived project", async () => {
    renderEditor([chapterA]);
    await screen.findByText(/第一章/);
    // No archived state here — but the read-only surface must not offer a
    // publish action; the component never renders a publish control.
    expect(screen.queryByRole("button", { name: /发布/i })).not.toBeInTheDocument();
  });

  it("embeds the revision history/recovery/rollback panel (36-04)", async () => {
    // Render with the server-updated chapter (revision 2) so the history head
    // v2 matches the editor's CAS token.
    renderEditor([serverChapterA]);
    await screen.findByText(/第一章/);

    // The embedded panel is present and closed by default (no hidden fetches).
    expect(screen.getByTestId("revision-history")).toBeInTheDocument();
    expect(mocks.listRevisions).not.toHaveBeenCalled();
    expect(screen.getByTestId("revision-history-summary")).toHaveTextContent("当前 v2");

    // Opening it fetches the newest-first immutable history for the chapter.
    fireEvent.click(screen.getByTestId("revision-history-toggle"));
    await waitFor(() => expect(mocks.listRevisions).toHaveBeenCalledWith(11, 5, 10));
    const rows = await screen.findAllByTestId("revision-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("v2");
    // The non-head row offers an explicit two-step rollback control.
    expect(
      rows[1].querySelector('[data-testid="revision-rollback"]')
    ).not.toBeNull();
    // No publish action exists anywhere on the editor surface.
    expect(screen.queryByRole("button", { name: /发布/i })).not.toBeInTheDocument();
  });
});
