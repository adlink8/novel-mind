/**
 * 书本视觉共享：色调/印章/尺寸，供书架（BookShelf）与选书条（NovelPickerStrip）复用。
 */

export const TONE_PAIRS: Array<[string, string]> = [
  ["#2d3431", "#59665f"],
  ["#51352f", "#9b5d47"],
  ["#27374d", "#526d82"],
  ["#443c68", "#766a9c"],
  ["#344d3f", "#6b806f"],
  ["#5d4935", "#9b7b58"],
];

export function hashTitle(title: string): number {
  let hash = 0;
  for (let i = 0; i < title.length; i++) {
    hash = title.charCodeAt(i) + ((hash << 5) - hash);
  }
  return Math.abs(hash);
}

export function toneOf(title: string): [string, string] {
  return TONE_PAIRS[hashTitle(title) % TONE_PAIRS.length];
}

/** 状态印章字（朱砂白文风格） */
export function sealChar(status: string): string {
  switch (status) {
    case "importing":
      return "入";
    case "chunking":
    case "embedding":
      return "索";
    case "analyzing":
      return "析";
    case "analyzed":
      return "线";
    case "ready":
    default:
      return "读";
  }
}

/** 书厚：按字数 36–60px */
export function bookThickness(wordCount: number): number {
  return Math.max(36, Math.min(60, 32 + Math.round(wordCount / 80000)));
}

/** 书高：按标题散列 176–228px，错落有致 */
export function bookHeight(title: string): number {
  return 176 + (hashTitle(title) % 5) * 13;
}
