import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { inNovel } = vi.hoisted(() => ({ inNovel: vi.fn() }));

vi.mock("@/lib/api", () => ({
  searchApi: { inNovel },
}));

vi.mock("@/lib/use-dismissable-layer", () => ({
  useDismissableLayer: () => ({
    present: true,
    closing: false,
  }),
}));

import { SearchPanel } from "./search-panel";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function result(content_snippet: string) {
  return {
    chunk_id: 1,
    chapter_id: 2,
    chapter_title: "第二章",
    content_snippet,
    score: 0.9,
  };
}

describe("SearchPanel", () => {
  beforeEach(() => {
    inNovel.mockReset();
  });

  it("does not let a slow previous query overwrite the latest query", async () => {
    const first = deferred<{ data: { results: ReturnType<typeof result>[] } }>();
    const second = deferred<{ data: { results: ReturnType<typeof result>[] } }>();
    inNovel.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(
      <SearchPanel
        novelId={1}
        isOpen
        onClose={vi.fn()}
      />,
    );

    await new Promise((resolve) => setTimeout(resolve, 150));
    const input = screen.getByPlaceholderText("搜索小说内容...");
    fireEvent.change(input, { target: { value: "旧查询" } });
    await waitFor(() => expect(inNovel).toHaveBeenCalledTimes(1));

    fireEvent.change(input, { target: { value: "新查询" } });
    await waitFor(() => expect(inNovel).toHaveBeenCalledTimes(2));

    first.resolve({ data: { results: [result("旧结果")] } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText("旧结果")).not.toBeInTheDocument();

    second.resolve({ data: { results: [result("新结果")] } });
    expect(await screen.findByText("新结果")).toBeInTheDocument();
  });
});
