import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import KeySceneSetPage from "./page";

const mocks = vi.hoisted(() => ({
  getParams: vi.fn<() => { id?: string; setId?: string }>(() => ({ id: "7", setId: "5" })),
}));

vi.mock("next/navigation", () => ({
  useParams: () => mocks.getParams(),
}));

vi.mock("@/components/key-scenes/review", () => ({
  KeySceneReviewWorkspace: ({
    novelId,
    setId,
  }: {
    novelId: string;
    setId: number;
  }) => <div data-testid="key-scene-workspace">{novelId}:{setId}</div>,
}));

describe("KeySceneSetPage", () => {
  it("渲染关键场景审查工作区", () => {
    render(<KeySceneSetPage />);
    expect(screen.getByTestId("key-scene-workspace")).toHaveTextContent("7:5");
  });

  it("无 novelId 或无效 setId 时返回 null", () => {
    mocks.getParams.mockReturnValue({ id: "7", setId: "abc" });
    const { container } = render(<KeySceneSetPage />);
    expect(container.firstChild).toBeNull();

    mocks.getParams.mockReturnValue({ setId: "5" });
    const { container: c2 } = render(<KeySceneSetPage />);
    expect(c2.firstChild).toBeNull();
  });
});
