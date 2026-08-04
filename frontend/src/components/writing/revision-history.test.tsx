import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listRevisions: vi.fn(),
  getRevision: vi.fn(),
  diffRevisions: vi.fn(),
  rollbackChapter: vi.fn(),
}));

vi.mock("@/lib/derivative-api", () => ({
  derivativeApi: {
    listRevisions: mocks.listRevisions,
    getRevision: mocks.getRevision,
    diffRevisions: mocks.diffRevisions,
    rollbackChapter: mocks.rollbackChapter,
  },
}));

import { RevisionHistory } from "./revision-history";
import type {
  DerivativeChapterView,
  DerivativeProjectView,
  DerivativeRevisionSummary,
} from "@/lib/derivative-api";

const H64 = "a".repeat(64);
const sha = (seed: string) => seed.repeat(64).slice(0, 64);

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

const chapter: DerivativeChapterView = {
  id: 10,
  project_id: 5,
  owner_id: 1,
  novel_id: 11,
  position: 0,
  title: "第一章",
  markdown: "## 开场\n最新内容",
  markdown_checksum: sha("d"),
  status: "draft",
  revision: 3,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
};

const rev3: DerivativeRevisionSummary = {
  id: 30,
  chapter_id: 10,
  project_id: 5,
  revision_number: 3,
  parent_revision_id: 20,
  kind: "autosave",
  content_checksum: sha("d"),
  actor_id: 1,
  reason: null,
  approval_state: "not_required",
  created_at: "2026-08-04T00:00:03Z",
};

const rev2: DerivativeRevisionSummary = {
  id: 20,
  chapter_id: 10,
  project_id: 5,
  revision_number: 2,
  parent_revision_id: 10,
  kind: "autosave",
  content_checksum: sha("c"),
  actor_id: 1,
  reason: null,
  approval_state: "not_required",
  created_at: "2026-08-04T00:00:02Z",
};

const rev1: DerivativeRevisionSummary = {
  id: 10,
  chapter_id: 10,
  project_id: 5,
  revision_number: 1,
  parent_revision_id: null,
  kind: "create",
  content_checksum: sha("b"),
  actor_id: 1,
  reason: null,
  approval_state: "not_required",
  created_at: "2026-08-04T00:00:01Z",
};

const REVISIONS = [rev3, rev2, rev1]; // newest first

const DIFF = {
  base_revision_id: 10,
  base_revision_number: 1,
  target_revision_id: 30,
  target_revision_number: 3,
  additions: 2,
  deletions: 1,
  hunks: [
    {
      old_start: 1,
      old_count: 1,
      new_start: 1,
      new_count: 2,
      lines: [
        { op: "delete", text: "旧内容" },
        { op: "add", text: "新内容A" },
        { op: "add", text: "新内容B" },
      ],
    },
  ],
};

function renderHistory(
  props: Partial<Parameters<typeof RevisionHistory>[0]> = {}
) {
  const onRecoverDraft = vi.fn();
  const onRollbackApplied = vi.fn();
  render(
    <RevisionHistory
      novelId={11}
      project={project}
      chapter={chapter}
      saveState="saved"
      onRecoverDraft={onRecoverDraft}
      onRollbackApplied={onRollbackApplied}
      {...props}
    />
  );
  return { onRecoverDraft, onRollbackApplied };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listRevisions.mockResolvedValue({
    data: { chapter_id: 10, project_id: 5, total: 3, items: REVISIONS },
  });
  mocks.getRevision.mockResolvedValue({
    data: { ...rev1, owner_id: 1, novel_id: 11, content: "## 开场\n旧草稿", updated_at: "" },
  });
  mocks.diffRevisions.mockResolvedValue({ data: DIFF });
});

afterEach(() => cleanup());

async function openPanel() {
  fireEvent.click(screen.getByTestId("revision-history-toggle"));
  await screen.findByTestId("revision-history-list");
}

describe("RevisionHistory", () => {
  it("re-echoes the namespace/fork/version lineage and autosave state", async () => {
    renderHistory();
    // Collapsed summary already carries the current revision + autosave state.
    expect(screen.getByTestId("revision-history-summary")).toHaveTextContent("当前 v3");
    expect(screen.getByTestId("revision-history-summary")).toHaveTextContent("saved");
    expect(screen.getByText("修订历史与恢复")).toBeInTheDocument();

    await openPanel();
    const lineage = screen.getByTestId("revision-history-lineage");
    expect(lineage).toHaveTextContent("fanfiction_canon");
    expect(lineage).toHaveTextContent("ff-branch");
    expect(lineage).toHaveTextContent("original:1");
    // The panel never offers an Original/Interpretation write entry point.
    expect(screen.queryByText("original_canon")).not.toBeInTheDocument();
    expect(screen.queryByText("user_interpretation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Original/i })).not.toBeInTheDocument();
    // No publish action is ever offered on this surface.
    expect(screen.queryByRole("button", { name: /发布/i })).not.toBeInTheDocument();
  });

  it("loads newest-first history only when the panel opens", async () => {
    renderHistory();
    expect(mocks.listRevisions).not.toHaveBeenCalled();

    await openPanel();
    expect(mocks.listRevisions).toHaveBeenCalledWith(11, 5, 10);

    const rows = screen.getAllByTestId("revision-row");
    expect(rows).toHaveLength(3);
    // Newest first: v3, v2, v1.
    expect(rows[0]).toHaveTextContent("v3");
    expect(rows[1]).toHaveTextContent("v2");
    expect(rows[2]).toHaveTextContent("v1");
    expect(rows[0]).toHaveTextContent("自动保存");
    expect(rows[2]).toHaveTextContent("创建");
  });

  it("renders a deterministic diff between two selected revisions", async () => {
    renderHistory();
    await openPanel();

    // Default selection (earliest -> head) fires the diff automatically.
    await waitFor(() =>
      expect(mocks.diffRevisions).toHaveBeenCalledWith(11, 5, 10, 10, 30)
    );
    expect(await screen.findByText("+2 / −1")).toBeInTheDocument();
    const diff = screen.getByTestId("revision-diff");
    expect(diff).toHaveTextContent("旧内容");
    expect(diff).toHaveTextContent("新内容A");
    expect(diff).toHaveTextContent("新内容B");
  });

  it("recovers the head draft without extra confirmation", async () => {
    const { onRecoverDraft } = renderHistory();
    await openPanel();

    const headRow = screen.getAllByTestId("revision-row")[0]; // v3 == head
    fireEvent.click(headRow.querySelector('[data-testid="revision-recover"]')!);

    await waitFor(() =>
      expect(onRecoverDraft).toHaveBeenCalledWith("## 开场\n最新内容")
    );
    // No explicit approval panel was shown for the head snapshot.
    expect(screen.queryByTestId("recover-confirm")).not.toBeInTheDocument();
  });

  it("requires confirmation to load a historical draft and fetches its content", async () => {
    const { onRecoverDraft } = renderHistory();
    await openPanel();

    const historicalRow = screen.getAllByTestId("revision-row")[2]; // v1
    fireEvent.click(historicalRow.querySelector('[data-testid="revision-recover"]')!);

    const confirm = await screen.findByTestId("recover-confirm");
    expect(confirm).toHaveTextContent("载入 v1 的草稿内容？");
    expect(onRecoverDraft).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /确认载入/ }));
    await waitFor(() => expect(mocks.getRevision).toHaveBeenCalledWith(11, 5, 10, 10));
    await waitFor(() =>
      expect(onRecoverDraft).toHaveBeenCalledWith("## 开场\n旧草稿")
    );
  });

  it("warns before recovering when the editor has unsaved changes", async () => {
    renderHistory({ saveState: "dirty" });
    await openPanel();

    const headRow = screen.getAllByTestId("revision-row")[0]; // v3 but editor is dirty
    fireEvent.click(headRow.querySelector('[data-testid="revision-recover"]')!);

    const confirm = await screen.findByTestId("recover-confirm");
    expect(confirm).toHaveTextContent(/丢弃/);
  });

  it("rollback requires explicit two-step approval and reports the server result", async () => {
    mocks.rollbackChapter.mockResolvedValue({
      data: {
        chapter: { ...chapter, revision: 4, markdown: "## 开场\n旧草稿" },
        revision: {
          ...rev3,
          id: 40,
          revision_number: 4,
          kind: "rollback",
          content: "## 开场\n旧草稿",
          approval_state: "approved",
        },
        target_revision_id: 10,
        message: "rollback restored the target as a new immutable child revision",
      },
    });
    const { onRollbackApplied } = renderHistory();
    await openPanel();

    // Clicking rollback only opens the confirmation — no API call yet.
    const historicalRow = screen.getAllByTestId("revision-row")[2]; // v1
    fireEvent.click(historicalRow.querySelector('[data-testid="revision-rollback"]')!);
    const confirm = await screen.findByTestId("rollback-confirm");
    expect(confirm).toHaveTextContent("回滚到 v1？");
    expect(mocks.rollbackChapter).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("回滚原因"), {
      target: { value: "恢复初稿" },
    });
    fireEvent.click(screen.getByTestId("rollback-confirm-button"));

    await waitFor(() =>
      expect(mocks.rollbackChapter).toHaveBeenCalledWith(11, 5, 10, {
        target_revision_id: 10,
        reason: "恢复初稿",
        base_revision: 3,
      })
    );
    // Success is shown from the actual server response, not fabricated.
    await screen.findByTestId("rollback-result");
    expect(screen.getByTestId("rollback-result")).toHaveTextContent("v4");
    await waitFor(() =>
      expect(onRollbackApplied).toHaveBeenCalledWith(
        expect.objectContaining({ revision: 4 }),
        "rollback restored the target as a new immutable child revision"
      )
    );
  });

  it("a stale 409 rollback surfaces the conflict and never applies success", async () => {
    const err = new Error("Request failed with status code 409") as Error & {
      isAxiosError?: boolean;
      response?: unknown;
    };
    err.isAxiosError = true;
    err.response = {
      status: 409,
      data: {
        detail: {
          code: "revision_conflict",
          message: "stale write",
          current_revision_number: 5,
        },
      },
    };
    mocks.rollbackChapter.mockRejectedValue(err);
    const { onRollbackApplied } = renderHistory();
    await openPanel();

    const historicalRow = screen.getAllByTestId("revision-row")[2]; // v1
    fireEvent.click(historicalRow.querySelector('[data-testid="revision-rollback"]')!);
    await screen.findByTestId("rollback-confirm");
    fireEvent.click(screen.getByTestId("rollback-confirm-button"));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/revision 5/));
    expect(onRollbackApplied).not.toHaveBeenCalled();
    // No fabricated success banner.
    expect(screen.queryByTestId("rollback-result")).not.toBeInTheDocument();
  });

  it("does not offer rollback controls on an archived (read-only) project", async () => {
    renderHistory({ readOnly: true });
    await openPanel();

    expect(screen.queryAllByTestId("revision-rollback")).toHaveLength(0);
    const recover = screen.getAllByTestId("revision-recover")[0];
    expect(recover).toBeDisabled();
  });

  it("no history is loaded when no chapter is selected", async () => {
    renderHistory({ chapter: null });
    fireEvent.click(screen.getByTestId("revision-history-toggle"));
    expect(await screen.findByText(/选择或新增一个章节/)).toBeInTheDocument();
    expect(mocks.listRevisions).not.toHaveBeenCalled();
  });
});
