/**
 * 阅读进度本地持久化。
 *
 * Reader 页面把每本书的「读到的章节 + 章内百分比」写入 localStorage，
 * 下次打开同一本书时恢复；服务端 reading_progress 是整书章节位置的真相。
 * key = `novelmind:reading:<novelId>`，值为 `{ chapterId, chapterPercent, updatedAt }`。
 * 纯函数模块，无 React 依赖。
 */

export function getStorageKey(novelId: string): string {
  return `novelmind:reading:${novelId}`;
}

export function loadProgress(
  novelId: string
): { chapterId: number; chapterPercent?: number } | null {
  try {
    const raw = localStorage.getItem(getStorageKey(novelId));
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return null;
}

export function saveProgress(
  novelId: string,
  chapterId: number,
  chapterPercent: number
): void {
  try {
    localStorage.setItem(
      getStorageKey(novelId),
      JSON.stringify({
        chapterId,
        chapterPercent,
        updatedAt: Date.now(),
      })
    );
  } catch {
    /* ignore */
  }
}

export type ReadingProgressChapter = {
  id: number;
  chapter_number: number;
};

/**
 * Convert the persisted database chapter id to the one-based UI chapter scope.
 * The stored id is an identity, not the narrative/UI chapter number.  The
 * chapter list is already ordered by the API; sorting here also keeps this
 * helper safe for callers that provide an unsorted list.
 */
export function resolveReadingCutoffChapterNumber(
  chapters: ReadingProgressChapter[],
  progressChapterId: number | null
): number | null {
  const addressableChapters = chapters.filter(
    (chapter) => Number.isInteger(chapter.chapter_number) && chapter.chapter_number >= 1
  );
  if (!addressableChapters.length) return null;

  const ordered = [...addressableChapters].sort(
    (a, b) => a.chapter_number - b.chapter_number || a.id - b.id
  );
  const progressIndex =
    progressChapterId == null
      ? -1
      : ordered.findIndex((chapter) => chapter.id === progressChapterId);

  // Preserve the chapter number from the API. It is the narrative coordinate
  // used by backend range validation; array position is not a chapter number.
  return progressIndex >= 0
    ? ordered[progressIndex].chapter_number
    : ordered[0].chapter_number;
}
