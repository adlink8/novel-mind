/**
 * Phase 34-02 — reader-safe inline illustration presentation (REQ-VIS-05,
 * D-34-01/D-34-02): hash-verified anchors, accessible captions, graceful
 * missing assets and interleaved flow layout (no DOM-index placement).
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { Chapter } from "@/lib/api";
import { ReaderContent } from "./reader-content";
import { IllustrationBlock } from "./illustration-block";
import { buildPageBlocks } from "./reader-content";
import {
  illustrationAssetBytesUrl,
  verifyAnchorAgainstChapter,
  type IllustrationAnchorView,
} from "@/lib/illustration-anchor";
import { sha256Hex } from "@/lib/reader-selection";

// Stub the shared axios client: any accidental fetch fails → explicit missing.
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn().mockRejectedValue(new Error("network unavailable in unit test")),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const H = (n: number) => String(n).repeat(64);

/** Code-point index of a substring (UTF-16 prefixes may differ on surrogates). */
function cpIndexOf(content: string, needle: string): number {
  const utf16Index = content.indexOf(needle);
  if (utf16Index < 0) return -1;
  return Array.from(content.slice(0, utf16Index)).length;
}

function cpLength(text: string): number {
  return Array.from(text).length;
}

function makeChapter(content: string): Chapter {
  return {
    id: 1,
    novel_id: 11,
    chapter_number: 1,
    title: "第一章 测试",
    content,
    word_count: content.length,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

/** Build a server-published valid anchor whose hashes replay from the content. */
async function makeValidAnchor(
  content: string,
  sourceStart: number,
  sourceEnd: number,
  over: Partial<IllustrationAnchorView> = {}
): Promise<IllustrationAnchorView> {
  const excerpt = Array.from(content).slice(sourceStart, sourceEnd).join("");
  const [anchorHash, contentHash] = await Promise.all([
    sha256Hex(excerpt),
    sha256Hex(content),
  ]);
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    chapter_id: 1,
    chapter_number: 1,
    anchor_key: "anchor-1",
    proposal_id: 1,
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(1),
    paragraph_start: 1,
    paragraph_end: 1,
    source_start: sourceStart,
    source_end: sourceEnd,
    excerpt,
    anchor_hash: anchorHash,
    chapter_content_hash: contentHash,
    published_asset_revision_id: 101,
    publish_manifest_hash: H(2),
    approval_request_id: 9,
    status: "valid",
    caption: "主角走进竹林",
    alt_text: "水墨竹林中的主角剪影",
    citation: "第一章 · 第 2 段",
    approved_by: "owner",
    approved_at: "2026-08-04T00:00:00Z",
    ...over,
  };
}

describe("illustrationAssetBytesUrl", () => {
  it("returns a bare owner-scoped path (axios baseURL supplies the /api prefix)", () => {
    expect(illustrationAssetBytesUrl(6, 3)).toBe(
      "/novels/6/illustrations/assets/3/bytes"
    );
    // Regression guard: a `/api`-prefixed path would double the shared axios
    // baseURL and 404 through the Next proxy.
    expect(illustrationAssetBytesUrl(6, 3)).not.toMatch(/^\/api\//);
  });
});

describe("verifyAnchorAgainstChapter", () => {
  it("accepts a valid published anchor that replays hash/range/content", async () => {
    const content = "第一段。\n\n第二段包含插图位置。\n\n第三段。";
    const start = cpIndexOf(content, "第二段");
    const end = start + cpLength("第二段包含插图位置。");
    const anchor = await makeValidAnchor(content, start, end);
    const result = await verifyAnchorAgainstChapter(anchor, content);
    expect(result.ok).toBe(true);
    expect(result.status).toBe("valid");
  });

  it("rejects a non-valid status (candidate must never render)", async () => {
    const content = "正文。";
    const anchor = await makeValidAnchor(content, 0, 2, {
      status: "proposed",
    });
    const result = await verifyAnchorAgainstChapter(anchor, content);
    expect(result.ok).toBe(false);
    expect(result.reasonCode).toBe("not_valid_status");
  });

  it("fails closed on a changed chapter content (stale → needs_repair)", async () => {
    const original = "第二段包含插图位置。";
    const anchor = await makeValidAnchor(original, 0, original.length);
    const edited = "第二段被改写，位置偏移了。";
    const result = await verifyAnchorAgainstChapter(anchor, edited);
    expect(result.ok).toBe(false);
    expect(result.status).toBe("needs_repair");
    expect(result.reasonCode).toBe("chapter_content_hash_mismatch");
  });

  it("fails closed on a source span that no longer replays the excerpt", async () => {
    const content = "第一段。\n\n第二段包含插图位置。\n\n第三段。";
    const anchor = await makeValidAnchor(
      content,
      0,
      Array.from("第一段。").length,
      { excerpt: "被篡改的excerpt" }
    );
    const result = await verifyAnchorAgainstChapter(anchor, content);
    expect(result.ok).toBe(false);
    expect(result.reasonCode).toBe("anchor_hash_mismatch");
  });

  it("fails closed on an out-of-bounds source range", async () => {
    const content = "短文。";
    const anchor = await makeValidAnchor(content, 0, content.length, {
      source_end: 9999,
      excerpt: "短文。",
    });
    const result = await verifyAnchorAgainstChapter(anchor, content);
    expect(result.ok).toBe(false);
    expect(result.reasonCode).toBe("source_range_out_of_bounds");
  });
});

describe("buildPageBlocks (interleaved flow layout)", () => {
  it("places the illustration after the paragraph containing the source start", () => {
    const pageText = "第一段。\n\n第二段有插图。\n\n第三段。";
    const pageCpStart = 0;
    const localStart = cpIndexOf(pageText, "第二段");
    const anchor = {
      id: 1,
      source_start: pageCpStart + localStart,
    } as IllustrationAnchorView;
    const blocks = buildPageBlocks({
      pageText,
      pageCpStart,
      highlightRange: null,
      anchors: [anchor],
      chapterContent: pageText,
    });
    const textKinds = blocks.map((b) => b.kind);
    expect(textKinds).toEqual(["text", "text", "illustration", "text"]);
    // textContent of text blocks still equals the page text (selection mapping).
    const joined = blocks
      .filter((b) => b.kind === "text")
      .map((b) => (b as { text: string }).text)
      .join("");
    expect(joined).toBe(pageText);
  });

  it("does not place anchors whose source start is outside this page", () => {
    const pageText = "第一页正文。";
    const blocks = buildPageBlocks({
      pageText,
      pageCpStart: 0,
      highlightRange: null,
      anchors: [
        { id: 1, source_start: 9999 } as IllustrationAnchorView,
        { id: 2, source_start: -1 } as IllustrationAnchorView,
      ],
      chapterContent: "第一页正文。第二页正文。",
    });
    expect(blocks.some((b) => b.kind === "illustration")).toBe(false);
  });

  it("applies the citation highlight only to the overlapping paragraph", () => {
    const pageText = "第一段。\n\n第二段高亮目标。\n\n第三段。";
    const pageCpStart = 0;
    const localStart = cpIndexOf(pageText, "高亮目标");
    const blocks = buildPageBlocks({
      pageText,
      pageCpStart,
      highlightRange: {
        sourceStart: localStart,
        sourceEnd: localStart + cpLength("高亮目标"),
      },
      anchors: [],
      chapterContent: pageText,
    });
    const highlighted = blocks
      .filter((b) => b.kind === "text")
      .map((b) => (b as { highlight: { start: number } | null }).highlight)
      .filter((h) => h !== null);
    expect(highlighted).toHaveLength(1);
  });
});

describe("IllustrationBlock", () => {
  it("renders the approved asset with accessible caption/alt (no innerHTML)", async () => {
    const content = "第二段包含插图位置。";
    const anchor = await makeValidAnchor(content, 0, content.length);
    render(
      <IllustrationBlock
        anchor={anchor}
        novelId={11}
        chapterContent={content}
        assetUrl="data:image/png;base64,iVBORw0KGgo="
      />
    );
    await waitFor(() => {
      expect(screen.getByTestId("illustration-block")).toHaveAttribute(
        "data-anchor-status",
        "valid"
      );
    });
    const img = await screen.findByTestId("illustration-image");
    expect(img).toHaveAttribute("alt", "水墨竹林中的主角剪影");
    expect(screen.getByTestId("illustration-caption")).toHaveTextContent(
      "主角走进竹林"
    );
    expect(screen.getByTestId("illustration-citation")).toHaveTextContent(
      "第一章 · 第 2 段"
    );
    // Caption/alt are plain React text — never dangerouslySetInnerHTML.
    const figure = screen.getByTestId("illustration-block");
    expect(figure.innerHTML).not.toContain("__html");
  });

  it("fetches the approved bytes via the shared axios client at the bare path", async () => {
    const content = "第二段包含插图位置。";
    const anchor = await makeValidAnchor(content, 0, content.length);
    const getMock = vi.mocked(api.get);
    // jsdom does not implement URL.createObjectURL/revokeObjectURL; stub them
    // so the happy path can reach a rendered image.
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    const createStub = vi.fn(() => "blob:mock-object-url");
    const revokeStub = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      writable: true,
      value: createStub,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: revokeStub,
    });
    try {
      getMock.mockResolvedValueOnce({
        data: new Blob(["jpg-bytes"], { type: "image/jpeg" }),
      });
      render(
        <IllustrationBlock
          anchor={anchor}
          novelId={11}
          chapterContent={content}
        />
      );
      await waitFor(() => {
        expect(getMock).toHaveBeenCalledWith(
          "/novels/11/illustrations/assets/101/bytes",
          { responseType: "blob" }
        );
      });
      const img = await screen.findByTestId("illustration-image");
      expect(img).toHaveAttribute("src", "blob:mock-object-url");
    } finally {
      Object.defineProperty(URL, "createObjectURL", {
        configurable: true,
        writable: true,
        value: originalCreate,
      });
      Object.defineProperty(URL, "revokeObjectURL", {
        configurable: true,
        writable: true,
        value: originalRevoke,
      });
    }
  });

  it("shows an explicit placeholder when the anchor is stale, never an image", async () => {
    const original = "第二段包含插图位置。";
    const anchor = await makeValidAnchor(original, 0, original.length);
    const edited = "第二段已被修改，位置偏移。";
    render(
      <IllustrationBlock
        anchor={anchor}
        novelId={11}
        chapterContent={edited}
        assetUrl="data:image/png;base64,iVBORw0KGgo="
      />
    );
    await waitFor(() => {
      expect(screen.getByTestId("illustration-block")).toHaveAttribute(
        "data-anchor-status",
        "needs_repair"
      );
    });
    expect(screen.getByTestId("illustration-block")).toHaveAttribute(
      "data-reason",
      "chapter_content_hash_mismatch"
    );
    expect(screen.getByTestId("illustration-placeholder")).toBeInTheDocument();
    expect(screen.queryByTestId("illustration-image")).not.toBeInTheDocument();
  });

  it("shows an explicit placeholder for a non-valid candidate anchor", async () => {
    const content = "正文。";
    const anchor = await makeValidAnchor(content, 0, 2, {
      status: "pending_approval",
    });
    render(
      <IllustrationBlock
        anchor={anchor}
        novelId={11}
        chapterContent={content}
      />
    );
    await waitFor(() => {
      expect(screen.getByTestId("illustration-block")).toHaveAttribute(
        "data-anchor-status",
        "pending_approval"
      );
    });
    expect(screen.queryByTestId("illustration-image")).not.toBeInTheDocument();
  });

  it("degrades gracefully when the approved binary is missing", async () => {
    const content = "第二段包含插图位置。";
    const anchor = await makeValidAnchor(content, 0, content.length);
    render(
      <IllustrationBlock
        anchor={anchor}
        novelId={11}
        chapterContent={content}
        assetFetcher={async () => {
          throw new Error("asset bytes missing");
        }}
      />
    );
    await waitFor(() => {
      expect(screen.getByTestId("illustration-missing")).toBeInTheDocument();
    });
    // Caption is retained on the accessible missing placeholder.
    expect(screen.getByTestId("illustration-caption")).toHaveTextContent(
      "主角走进竹林"
    );
    expect(screen.queryByTestId("illustration-image")).not.toBeInTheDocument();
  });
});

describe("ReaderContent anchor integration", () => {
  it("interleaves a valid anchor as a flow-layout figure inside the page", async () => {
    const content =
      "第一段说明。\n\n第二段插图锚点在此。\n\n第三段收尾。";
    const start = cpIndexOf(content, "第二段插图锚点在此。");
    const anchor = await makeValidAnchor(content, start, cpLength(content));
    render(<ReaderContent chapter={makeChapter(content)} anchors={[anchor]} />);
    await waitFor(() => {
      const figure = screen.getByTestId("illustration-block");
      expect(figure).toHaveAttribute("data-anchor-status", "valid");
    });
    // Flow layout: figure is a sibling of the paragraph, inside reader page.
    const figure = screen.getByTestId("illustration-block");
    expect(figure).toHaveAttribute("data-reader-illustration");
    expect(figure.querySelector("figcaption")).not.toBeNull();
    // Text nodes still concatenate to the page text (selection preserved).
    const pageText = screen.getByTestId("reader-page-text");
    expect(pageText.textContent).toContain("第二段插图锚点在此。");
  });

  it("never renders an approved asset for a stale anchor in the reader", async () => {
    const content = "第一段。\n\n第二段插图锚点在此。\n\n第三段。";
    const start = cpIndexOf(content, "第二段插图锚点在此。");
    const anchor = await makeValidAnchor(content, start, cpLength(content));
    const edited = "第一段。\n\n第二段插图锚点已被改写。\n\n第三段。";
    render(
      <ReaderContent
        chapter={makeChapter(edited)}
        anchors={[{ ...anchor, chapter_content_hash: H(9) }]}
      />
    );
    await waitFor(() => {
      const figure = screen.getByTestId("illustration-block");
      expect(figure).toHaveAttribute("data-anchor-status", "needs_repair");
    });
    expect(screen.queryByTestId("illustration-image")).not.toBeInTheDocument();
  });

  it("places anchors in scroll (long-page) mode as flow-layout figures", async () => {
    const content =
      "第一段。\n\n第二段插图锚点在此。\n\n第三段。\n\n第四段。\n\n第五段。";
    const start = cpIndexOf(content, "第二段插图锚点在此。");
    const anchor = await makeValidAnchor(content, start, cpLength(content));
    render(
      <ReaderContent
        chapter={makeChapter(content)}
        anchors={[anchor]}
        readingMode="scroll"
      />
    );
    expect(screen.getByText(/长页模式/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("illustration-block")).toHaveAttribute(
        "data-anchor-status",
        "valid"
      );
    });
    expect(screen.getByTestId("illustration-caption")).toHaveTextContent(
      "主角走进竹林"
    );
  });
});
