import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NovelSceneSpecPage from "./page";

const mocks = vi.hoisted(() => ({
  listSpecs: vi.fn(),
  getParams: vi.fn<() => { id?: string }>(() => ({ id: "7" })),
  getSearchParams: vi.fn(() => new URLSearchParams()),
}));

vi.mock("next/navigation", () => ({
  useParams: () => mocks.getParams(),
  useSearchParams: () => mocks.getSearchParams(),
}));

vi.mock("@/lib/scene-spec-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/scene-spec-api")>(
    "@/lib/scene-spec-api"
  );
  return {
    ...actual,
    sceneSpecsApi: {
      listSpecs: mocks.listSpecs,
    },
  };
});

vi.mock("@/components/scene-spec/preview", () => ({
  SceneSpecPreview: ({ novelId, specId }: { novelId: string; specId: number }) => (
    <div data-testid="scene-spec-preview">{novelId}:{specId}</div>
  ),
}));

vi.mock("@/components/scene-spec/diff", () => ({
  PromptDiff: ({ novelId, revisionId }: { novelId: string; revisionId: number }) => (
    <div data-testid="scene-spec-diff">{novelId}:{revisionId}</div>
  ),
}));

const spec = {
  id: 3,
  novel_id: 7,
  source: "evidence" as const,
  version_id: 1,
  scene_number: 1,
  title: "雾中相遇",
  status: "pending" as const,
  created_at: "2026-01-01T00:00:00Z",
};

describe("NovelSceneSpecPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getParams.mockReturnValue({ id: "7" });
    mocks.getSearchParams.mockReturnValue(new URLSearchParams());
    mocks.listSpecs.mockResolvedValue({ data: { items: [spec], total: 1 } });
  });

  it("加载中展示 loading 态", () => {
    mocks.listSpecs.mockReturnValue(new Promise(() => {}));
    render(<NovelSceneSpecPage />);
    expect(screen.getByTestId("scene-spec-loading")).toBeInTheDocument();
  });

  it("无 novelId 时返回 null", () => {
    mocks.getParams.mockReturnValue({});
    const { container } = render(<NovelSceneSpecPage />);
    expect(container.firstChild).toBeNull();
  });

  it("加载失败展示错误", async () => {
    mocks.listSpecs.mockRejectedValue(new Error("boom"));
    render(<NovelSceneSpecPage />);
    expect(await screen.findByTestId("scene-spec-error")).toHaveTextContent("boom");
  });

  it("空列表展示 empty 态", async () => {
    mocks.listSpecs.mockResolvedValue({ data: { items: [], total: 0 } });
    render(<NovelSceneSpecPage />);
    expect(await screen.findByTestId("scene-spec-empty")).toHaveTextContent(
      "暂无 Scene Spec"
    );
  });

  it("展示最新 spec 的预览", async () => {
    render(<NovelSceneSpecPage />);
    expect(await screen.findByTestId("scene-spec-preview")).toHaveTextContent("7:3");
  });

  it("?diff= 查询切换为 PromptDiff", async () => {
    mocks.getSearchParams.mockReturnValue(new URLSearchParams("diff=11"));
    render(<NovelSceneSpecPage />);
    expect(await screen.findByTestId("scene-spec-diff")).toHaveTextContent("7:11");
  });
});
