import { describe, expect, it } from "vitest";

import {
  buildSelectionPayload,
  captureSelectionFromRange,
  chapterUtf16ToCodePoints,
  codePointLength,
  codePointSlice,
  codePointToUtf16Index,
  sha256Hex,
  splitPagesWithBases,
  utf16IndexToCodePoint,
} from "./reader-selection";

describe("splitPagesWithBases", () => {
  it("records UTF-16 bases across paginated CJK content", () => {
    const chapter = "甲".repeat(100) + "\n\n" + "乙".repeat(100) + "丙".repeat(50);
    const pages = splitPagesWithBases(chapter, 80);
    expect(pages.length).toBeGreaterThan(1);
    expect(pages[0].sourceStartUtf16).toBe(0);
    for (let i = 1; i < pages.length; i++) {
      expect(pages[i].sourceStartUtf16).toBe(
        pages[i - 1].sourceStartUtf16 + pages[i - 1].text.length
      );
    }
    expect(pages.map((p) => p.text).join("")).toBe(chapter);
  });

  it("returns single empty page for empty text", () => {
    expect(splitPagesWithBases("", 3500)).toEqual([
      { text: "", sourceStartUtf16: 0 },
    ]);
  });
});

describe("UTF-16 ↔ code-point conversion", () => {
  it("maps CJK 1:1", () => {
    const text = "林墨走进竹林";
    expect(utf16IndexToCodePoint(text, 2)).toBe(2);
    expect(codePointToUtf16Index(text, 2)).toBe(2);
    expect(codePointSlice(text, 0, 2)).toBe("林墨");
  });

  it("maps emoji / surrogate pairs to single code points", () => {
    const text = "ab😀cd"; // 😀 is one code point, two UTF-16 units
    expect(text.length).toBe(6); // UTF-16 length
    expect(codePointLength(text)).toBe(5);
    // Index after 'a','b' (utf16=2) → 2 code points
    expect(utf16IndexToCodePoint(text, 2)).toBe(2);
    // Index after emoji (utf16=4) → 3 code points (a,b,😀)
    expect(utf16IndexToCodePoint(text, 4)).toBe(3);
    expect(codePointSlice(text, 2, 3)).toBe("😀");
    expect(codePointToUtf16Index(text, 3)).toBe(4);
  });

  it("handles combining marks as separate code points (no NFC)", () => {
    // e + combining acute (U+0301) — two code points when not precomposed
    const text = "e\u0301x";
    expect(codePointLength(text)).toBe(3);
    expect(codePointSlice(text, 0, 2)).toBe("e\u0301");
    expect(utf16IndexToCodePoint(text, 2)).toBe(2);
  });

  it("preserves CRLF as two code points", () => {
    const text = "a\r\nb";
    expect(codePointLength(text)).toBe(4);
    expect(codePointSlice(text, 1, 3)).toBe("\r\n");
    expect(utf16IndexToCodePoint(text, 3)).toBe(3);
  });

  it("maps paginated selection through page base + conversion", () => {
    const page0 = "前缀内容".repeat(10); // 40 chars
    const page1 = "目标选区emoji😀尾";
    const chapter = page0 + page1;
    const pages = splitPagesWithBases(chapter, 40);
    expect(pages[0].text).toBe(page0);
    const base = pages[1]?.sourceStartUtf16 ?? page0.length;
    // Select "emoji😀" within page1: UTF-16 index of 'e' is 4 in page1
    const localStart = page1.indexOf("emoji");
    const localEnd = localStart + "emoji😀".length; // utf16 length
    const absStart = base + localStart;
    const absEnd = base + localEnd;
    const coords = chapterUtf16ToCodePoints(chapter, absStart, absEnd);
    expect(coords.selectionText).toBe("emoji😀");
    expect(codePointSlice(chapter, coords.sourceStart, coords.sourceEnd)).toBe(
      "emoji😀"
    );
  });
});

describe("sha256Hex + buildSelectionPayload", () => {
  it("matches known SHA-256 of UTF-8 text", async () => {
    // echo -n "hello" | sha256sum
    expect(await sha256Hex("hello")).toBe(
      "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    );
  });

  it("builds payload with exact offsets and hashes", async () => {
    const chapter = "第一章：阿宁走进竹林。";
    const coords = chapterUtf16ToCodePoints(chapter, 4, 8); // "阿宁走进"
    const payload = await buildSelectionPayload(42, chapter, coords);
    expect(payload.chapter_id).toBe(42);
    expect(payload.selection_text).toBe("阿宁走进");
    expect(payload.source_end).toBeGreaterThan(payload.source_start);
    expect(payload.selection_text_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(payload.chapter_content_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(payload.selection_text_hash).toBe(await sha256Hex("阿宁走进"));
    expect(payload.chapter_content_hash).toBe(await sha256Hex(chapter));
  });
});

describe("captureSelectionFromRange (jsdom)", () => {
  it("survives converting a Range on a page text node with base offset", () => {
    const pageText = "可见正文选区测试";
    const chapter = "隐藏前缀" + pageText;
    const root = document.createElement("div");
    root.textContent = pageText;
    document.body.appendChild(root);
    const textNode = root.firstChild as Text;
    const range = document.createRange();
    range.setStart(textNode, 4); // 选区
    range.setEnd(textNode, 6);
    const pageBase = "隐藏前缀".length;
    const coords = captureSelectionFromRange(root, range, pageBase, chapter);
    expect(coords).not.toBeNull();
    expect(coords!.selectionText).toBe("选区");
    expect(
      codePointSlice(chapter, coords!.sourceStart, coords!.sourceEnd)
    ).toBe("选区");
    root.remove();
  });

  it("returns null for collapsed empty selection", () => {
    const root = document.createElement("div");
    root.textContent = "abc";
    document.body.appendChild(root);
    const textNode = root.firstChild as Text;
    const range = document.createRange();
    range.setStart(textNode, 1);
    range.setEnd(textNode, 1);
    expect(captureSelectionFromRange(root, range, 0, "abc")).toBeNull();
    root.remove();
  });
});
