import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import NovelIllustrationsPage from "./page";

const mocks = vi.hoisted(() => ({
  getParams: vi.fn<() => { id?: string }>(() => ({ id: "7" })),
}));

vi.mock("next/navigation", () => ({
  useParams: () => mocks.getParams(),
}));

vi.mock("@/components/illustrations/gallery", () => ({
  IllustrationGallery: ({ novelId }: { novelId: string }) => (
    <div data-testid="illustration-gallery">{novelId}</div>
  ),
}));

describe("NovelIllustrationsPage", () => {
  it("渲染插图画廊", () => {
    render(<NovelIllustrationsPage />);
    expect(screen.getByTestId("illustration-gallery")).toHaveTextContent("7");
  });

  it("无 novelId 时返回 null", () => {
    mocks.getParams.mockReturnValue({});
    const { container } = render(<NovelIllustrationsPage />);
    expect(container.firstChild).toBeNull();
  });
});
