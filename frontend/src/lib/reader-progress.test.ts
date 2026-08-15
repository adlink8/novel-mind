import { describe, expect, it } from "vitest";

import { resolveReadingCutoffChapterNumber } from "./reader-progress";

describe("resolveReadingCutoffChapterNumber", () => {
  it("maps database chapter id 599 to the first narrative chapter", () => {
    expect(
      resolveReadingCutoffChapterNumber(
        [
          { id: 599, chapter_number: 1 },
          { id: 600, chapter_number: 2 },
        ],
        599
      )
    ).toBe(1);
  });

  it("returns the persisted chapter's real narrative number", () => {
    expect(
      resolveReadingCutoffChapterNumber(
        [
          { id: 599, chapter_number: 17 },
          { id: 600, chapter_number: 19 },
        ],
        599
      )
    ).toBe(17);
  });

  it("uses the first real chapter number when progress is missing", () => {
    expect(
      resolveReadingCutoffChapterNumber(
        [
          { id: 600, chapter_number: 19 },
          { id: 599, chapter_number: 17 },
        ],
        null
      )
    ).toBe(17);
  });

  it("does not expose a legacy zero-numbered preface as the reading cutoff", () => {
    expect(
      resolveReadingCutoffChapterNumber(
        [
          { id: 598, chapter_number: 0 },
          { id: 599, chapter_number: 1 },
        ],
        598
      )
    ).toBe(1);
  });
});
