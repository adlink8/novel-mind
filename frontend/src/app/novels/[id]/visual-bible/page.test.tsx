import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NovelVisualBiblePage from "./page";

const mocks = vi.hoisted(() => ({
  listVersions: vi.fn(),
  getParams: vi.fn<() => { id?: string }>(() => ({ id: "7" })),
}));

vi.mock("next/navigation", () => ({
  useParams: () => mocks.getParams(),
}));

vi.mock("@/lib/visual-bible-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/visual-bible-api")>(
    "@/lib/visual-bible-api"
  );
  return {
    ...actual,
    visualBibleApi: {
      listVersions: mocks.listVersions,
    },
  };
});

vi.mock("@/components/visual-bible/entity-sheet", () => ({
  VisualBibleEntitySheet: ({
    novelId,
    versionId,
  }: {
    novelId: string;
    versionId: number;
  }) => <div data-testid="visual-bible-sheet">{novelId}:{versionId}</div>,
}));

const version = {
  id: 5,
  novel_id: 7,
  source: "active" as const,
  version_label: "v1",
  status: "candidate" as const,
  through_chapter: 3,
  created_at: "2026-01-01T00:00:00Z",
};

describe("NovelVisualBiblePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getParams.mockReturnValue({ id: "7" });
    mocks.listVersions.mockResolvedValue({ data: { items: [version], total: 1 } });
  });

  it("加载中展示 loading 态", () => {
    mocks.listVersions.mockReturnValue(new Promise(() => {}));
    render(<NovelVisualBiblePage />);
    expect(screen.getByTestId("visual-bible-loading")).toBeInTheDocument();
  });

  it("无 novelId 时返回 null", () => {
    mocks.getParams.mockReturnValue({});
    const { container } = render(<NovelVisualBiblePage />);
    expect(container.firstChild).toBeNull();
  });

  it("加载失败展示错误", async () => {
    mocks.listVersions.mockRejectedValue(new Error("boom"));
    render(<NovelVisualBiblePage />);
    expect(await screen.findByTestId("visual-bible-error")).toHaveTextContent("boom");
  });

  it("空列表展示 empty 态", async () => {
    mocks.listVersions.mockResolvedValue({ data: { items: [], total: 0 } });
    render(<NovelVisualBiblePage />);
    expect(await screen.findByTestId("visual-bible-empty")).toHaveTextContent(
      "暂无 Visual Bible 版本"
    );
  });

  it("展示最新版本的实体审查表", async () => {
    render(<NovelVisualBiblePage />);
    expect(await screen.findByTestId("visual-bible-sheet")).toHaveTextContent("7:5");
  });
});
