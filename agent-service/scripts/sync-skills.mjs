/**
 * 把 src/skills/ 下的非代码资源（SKILL.md / *.json / *.yaml）同步进 dist。
 *
 * tsc 只编译 .ts；skill 包资源此前靠手动 `cp -r src/skills/. dist/src/skills/`
 * 同步，漏同步会导致运行时加载旧 prompt（E2E 踩坑）。build 脚本调用本脚本
 * 使 `npm run build` 产出完整可运行的 dist。
 */

import { cpSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src", "skills");
const dest = join(root, "dist", "src", "skills");

if (!existsSync(src)) {
  console.error(`sync-skills: source ${src} missing`);
  process.exit(1);
}
cpSync(src, dest, { recursive: true });
console.log(`sync-skills: ${src} -> ${dest}`);
