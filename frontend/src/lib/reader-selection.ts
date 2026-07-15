/**
 * Reader selection coordinates: DOM Range / page UTF-16 → chapter Unicode code points.
 *
 * Server authority is Python-style code-point half-open ranges on Chapter.content.
 * Page splitting retains each page's UTF-16 base so paginated selections stay exact.
 */

export type PageSlice = {
  text: string;
  /** Inclusive UTF-16 index of this page's first unit within chapter content. */
  sourceStartUtf16: number;
};

export type ChapterSelectionCoords = {
  sourceStart: number;
  sourceEnd: number;
  selectionText: string;
  startUtf16: number;
  endUtf16: number;
};

export type SelectionPayload = {
  chapter_id: number;
  source_start: number;
  source_end: number;
  selection_text: string;
  selection_text_hash: string;
  chapter_content_hash: string;
};

/** Split chapter content into pages while recording each page's UTF-16 base. */
export function splitPagesWithBases(text: string, size: number): PageSlice[] {
  if (!text) return [{ text: "", sourceStartUtf16: 0 }];
  if (text.length <= size) return [{ text, sourceStartUtf16: 0 }];

  const pages: PageSlice[] = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(start + size, text.length);
    if (end < text.length) {
      const window = text.slice(start, end);
      const breakAt = Math.max(
        window.lastIndexOf("\n\n"),
        window.lastIndexOf("\n")
      );
      if (breakAt > size * 0.4) {
        end = start + breakAt + 1;
      }
    }
    pages.push({ text: text.slice(start, end), sourceStartUtf16: start });
    start = end;
  }
  return pages;
}

/** Count Unicode code points (Python 3 str length) up to a UTF-16 index. */
export function utf16IndexToCodePoint(text: string, utf16Index: number): number {
  if (utf16Index <= 0) return 0;
  const clamped = Math.min(utf16Index, text.length);
  return Array.from(text.slice(0, clamped)).length;
}

/** Convert a code-point index to a UTF-16 index within text. */
export function codePointToUtf16Index(text: string, codePointIndex: number): number {
  if (codePointIndex <= 0) return 0;
  const chars = Array.from(text);
  if (codePointIndex >= chars.length) return text.length;
  return chars.slice(0, codePointIndex).join("").length;
}

/** Half-open code-point slice matching Python `text[start:end]`. */
export function codePointSlice(
  text: string,
  start: number,
  end: number
): string {
  return Array.from(text).slice(start, end).join("");
}

export function codePointLength(text: string): number {
  return Array.from(text).length;
}

/**
 * Walk text nodes under `root` and map a DOM Range to page-local UTF-16 offsets,
 * then add the page base to produce chapter-absolute UTF-16 indices.
 */
export function rangeToChapterUtf16(
  root: HTMLElement,
  range: Range,
  pageSourceStartUtf16: number
): { startUtf16: number; endUtf16: number; selectedText: string } | null {
  if (!root.contains(range.commonAncestorContainer)) return null;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let utf16Cursor = 0;
  let startUtf16: number | null = null;
  let endUtf16: number | null = null;

  let node = walker.nextNode() as Text | null;
  while (node) {
    const len = node.data.length;
    if (node === range.startContainer) {
      startUtf16 = utf16Cursor + range.startOffset;
    }
    if (node === range.endContainer) {
      endUtf16 = utf16Cursor + range.endOffset;
    }
    utf16Cursor += len;
    node = walker.nextNode() as Text | null;
  }

  // Collapse/empty selection or range outside pure text nodes.
  if (startUtf16 == null || endUtf16 == null) {
    const selectedText = range.toString();
    if (!selectedText) return null;
    // Fallback: search first occurrence of selected text within root textContent.
    const full = root.textContent ?? "";
    const idx = full.indexOf(selectedText);
    if (idx < 0) return null;
    startUtf16 = idx;
    endUtf16 = idx + selectedText.length;
  }

  if (endUtf16 <= startUtf16) return null;

  return {
    startUtf16: pageSourceStartUtf16 + startUtf16,
    endUtf16: pageSourceStartUtf16 + endUtf16,
    selectedText: range.toString(),
  };
}

/** Convert chapter-absolute UTF-16 half-open range to code-point offsets. */
export function chapterUtf16ToCodePoints(
  chapterContent: string,
  startUtf16: number,
  endUtf16: number
): ChapterSelectionCoords {
  const start = utf16IndexToCodePoint(chapterContent, startUtf16);
  const end = utf16IndexToCodePoint(chapterContent, endUtf16);
  const selectionText = codePointSlice(chapterContent, start, end);
  return {
    sourceStart: start,
    sourceEnd: end,
    selectionText,
    startUtf16,
    endUtf16,
  };
}

/** Build immutable selection coords from a live Range before the selection collapses. */
export function captureSelectionFromRange(
  root: HTMLElement,
  range: Range,
  pageSourceStartUtf16: number,
  chapterContent: string
): ChapterSelectionCoords | null {
  const mapped = rangeToChapterUtf16(root, range, pageSourceStartUtf16);
  if (!mapped) return null;
  const coords = chapterUtf16ToCodePoints(
    chapterContent,
    mapped.startUtf16,
    mapped.endUtf16
  );
  // Prefer exact chapter slice; if DOM text drifted, keep server-verifiable slice.
  if (!coords.selectionText) return null;
  return coords;
}

/** SHA-256 hex of UTF-8 bytes (matches backend content_sha256). */
export async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  if (typeof globalThis.crypto?.subtle?.digest === "function") {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }
  // Node / Vitest fallback without WebCrypto
  const { createHash } = await import("node:crypto");
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export async function buildSelectionPayload(
  chapterId: number,
  chapterContent: string,
  coords: ChapterSelectionCoords
): Promise<SelectionPayload> {
  const selection_text = coords.selectionText;
  const [selection_text_hash, chapter_content_hash] = await Promise.all([
    sha256Hex(selection_text),
    sha256Hex(chapterContent),
  ]);
  return {
    chapter_id: chapterId,
    source_start: coords.sourceStart,
    source_end: coords.sourceEnd,
    selection_text,
    selection_text_hash,
    chapter_content_hash,
  };
}

/** Presentation-only localStorage key for panel open/collapsed state (not conversation truth). */
export function readerChatPresentationKey(novelId: string | number): string {
  return `novelmind:reader-chat:ui:${novelId}`;
}

export type ReaderChatPresentation = {
  open?: boolean;
  collapsed?: boolean;
  activeConversationId?: number | null;
};

export function loadReaderChatPresentation(
  novelId: string | number
): ReaderChatPresentation {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(readerChatPresentationKey(novelId));
    if (!raw) return {};
    return JSON.parse(raw) as ReaderChatPresentation;
  } catch {
    return {};
  }
}

export function saveReaderChatPresentation(
  novelId: string | number,
  state: ReaderChatPresentation
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      readerChatPresentationKey(novelId),
      JSON.stringify(state)
    );
  } catch {
    /* ignore quota */
  }
}
