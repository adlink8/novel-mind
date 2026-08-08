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
