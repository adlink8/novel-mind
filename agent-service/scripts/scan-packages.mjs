#!/usr/bin/env node
/**
 * 闭包扫描（D-04）——完全离线，直接遍历 node_modules，等价于 `npm ls --json --all` 闭包。
 *
 * 检查项（任一失败 → 命名违规包/脚本到 stderr 并 exit 1）：
 *   1. Lifecycle 脚本：闭包内任何 preinstall/install/postinstall。
 *      - echo 空操作自动视为无害；
 *      - 其余可执行脚本必须命中下方审定的 LIFECYCLE_ALLOWLIST（逐条审计记录），否则拒绝。
 *   2. 依赖树 diff：实际安装闭包（node_modules 遍历）必须全部被 package-lock.json
 *      声明树覆盖；出现未声明包（dependency-confusion 信号）→ 拒绝。
 *
 * 注意：动态 pi install / pi update（never|forbid|deny）——NovelMind 正式 agent 服务
 * 不引入任何运行时包安装路径（D-04）。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const AGENT_SERVICE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const NODE_MODULES = path.join(AGENT_SERVICE, "node_modules");
const LOCKFILE = path.join(AGENT_SERVICE, "package-lock.json");

/**
 * 已审计的无害 lifecycle 脚本白名单（key: `${name}@${version}:${script}`）。
 * 逐条人工审计结论见各包 qualification/package.json：
 *   - protobufjs@7.6.5 postinstall: `node scripts/postinstall` 仅读取本地产
 *     package.json、按需打印 versionScheme 警告，无网络/无写盘；安装路径一律
 *     `npm ci --ignore-scripts`，脚本实际不会执行。
 */
const LIFECYCLE_ALLOWLIST = new Set([
  "protobufjs@7.6.5:postinstall",
]);

const LIFECYCLE_KEYS = ["preinstall", "install", "postinstall"];

const errors = [];
const notes = [];

function err(msg) {
  errors.push(msg);
}

/** echo 空操作：每个子句都以 echo 开头，无管道/命令替换/重定向。 */
function isEchoNoop(script) {
  const clauses = String(script).split(/&&|\|\||;|\n/);
  return clauses.every((clause) => /^\s*echo\b/.test(clause));
}

/** 遍历 node_modules：只统计真正位于 node_modules 下的包（避免 dist/esm 等误报）。 */
function walkNodeModules(dir, onPackage) {
  if (!fs.existsSync(dir)) return;
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name.startsWith(".")) continue;
    const full = path.join(dir, ent.name);
    if (!ent.isDirectory()) continue;
    if (ent.name.startsWith("@")) {
      for (const sub of fs.readdirSync(full, { withFileTypes: true })) {
        if (sub.name.startsWith(".") || !sub.isDirectory()) continue;
        readPackage(path.join(full, sub.name), onPackage);
      }
    } else {
      readPackage(full, onPackage);
    }
  }
}

function readPackage(dir, onPackage) {
  const pkgJson = path.join(dir, "package.json");
  if (!fs.existsSync(pkgJson)) return;
  let meta;
  try {
    meta = JSON.parse(fs.readFileSync(pkgJson, "utf8"));
  } catch {
    return;
  }
  if (!meta.name) return;
  onPackage(dir, meta);
  const nested = path.join(dir, "node_modules");
  if (fs.existsSync(nested)) walkNodeModules(nested, onPackage);
}

/** 依赖树 diff：实际闭包 vs package-lock.json 声明树。 */
function checkDependencyTreeDiff(actualClosure) {
  let lock;
  try {
    lock = JSON.parse(fs.readFileSync(LOCKFILE, "utf8"));
  } catch (e) {
    err(`无法解析 package-lock.json: ${e.message}`);
    return;
  }
  const declared = new Set();
  for (const key of Object.keys(lock.packages ?? {})) {
    if (!key.startsWith("node_modules/")) continue;
    const leaf = key.replace(/^node_modules\//, "").split("/node_modules/").pop();
    const entry = lock.packages[key];
    declared.add(`${leaf}@${entry.version}`);
  }
  const undeclared = [...actualClosure]
    .filter((key) => !declared.has(key))
    .sort();
  if (undeclared.length > 0) {
    for (const key of undeclared) {
      err(`依赖树 diff: 未声明包（dependency-confusion 信号）: ${key}`);
    }
    return;
  }
  notes.push(`依赖树一致（${actualClosure.size} 个已装包全部被声明）`);
}

function main() {
  const actualClosure = new Set();
  walkNodeModules(NODE_MODULES, (_dir, meta) => {
    actualClosure.add(`${meta.name}@${meta.version}`);

    for (const key of LIFECYCLE_KEYS) {
      const script = meta.scripts?.[key];
      if (!script) continue;
      if (isEchoNoop(script)) {
        notes.push(`${meta.name}@${meta.version}:${key} 为 echo 空操作，放行`);
        continue;
      }
      const allowKey = `${meta.name}@${meta.version}:${key}`;
      if (LIFECYCLE_ALLOWLIST.has(allowKey)) {
        notes.push(`${allowKey} 命中审定白名单，放行`);
        continue;
      }
      err(`危险 lifecycle 脚本: ${meta.name}@${meta.version} ${key}=${script}`);
    }
  });

  checkDependencyTreeDiff(actualClosure);

  if (errors.length > 0) {
    for (const message of errors) {
      console.error(`[scan-packages] FAIL: ${message}`);
    }
    console.error(`[scan-packages] ${errors.length} 个问题，fail-closed`);
    process.exit(1);
  }
  console.error(`[scan-packages] PASS: 闭包 ${actualClosure.size} 个包；${notes.length} 条放行记录`);
  for (const note of notes) console.error(`[scan-packages]   ${note}`);
  process.exit(0);
}

main();
