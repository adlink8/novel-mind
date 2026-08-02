#!/usr/bin/env node
/**
 * packages.lock.json ↔ package-lock.json 一致性校验（D-04，fail-closed）。
 *
 * 逐项校验并命名违规包/字段；任何不一致 → 打印到 stderr 并 exit 1。
 * 规则：
 *   1. packages.lock.json 顶层 version=1、generated_by="25.3-01"。
 *   2. 每条目必须有 name/version（精确 pin，无 ^~ 范围）/source/verdict/installed。
 *   3. installed=true 条目：verdict 不得为 reject，必须携带完整 D-05 permission_manifest。
 *   4. 每个已装 runtime 依赖（package.json dependencies）在清单中必须有
 *      installed=true 且非 reject 的条目，version+integrity 与 package-lock.json 一致。
 *   5. 清单中 installed=true 条目的 version+integrity 与 package-lock.json 精确一致。
 *   6. adopt 条目必须存在可读的 qualification_report 文件。
 *   7. @earendil-works/* 在闭包内只能有一个版本（Pitfall 3 版本漂移）。
 * 纯 Node，离线可用；无自动修复、无交互——退出码即契约。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const AGENT_SERVICE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const GOV = path.join(AGENT_SERVICE, "packages.lock.json");
const NPM = path.join(AGENT_SERVICE, "package-lock.json");
const PKG_JSON = path.join(AGENT_SERVICE, "package.json");

const VERDICTS = new Set(["adopt", "fork", "pattern-only", "reject"]);
const SOURCES = new Set(["npm", "git"]);
const D05_FIELDS = [
  "network",
  "network_allowlist",
  "filesystem",
  "shell",
  "env",
  "secrets",
  "tools",
  "artifact_writes",
];
const EXACT_PIN_RE = /^[0-9]+(\.[0-9]+){0,3}([-+][0-9A-Za-z.\-]+)?$/;

const errors = [];
const notes = [];

function err(msg) {
  errors.push(msg);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    err(`无法解析 ${path.relative(AGENT_SERVICE, file)}: ${e.message}`);
    return null;
  }
}

function main() {
  const gov = readJson(GOV);
  const npm = readJson(NPM);
  const pkgJson = readJson(PKG_JSON);
  if (!gov || !npm || !pkgJson) {
    return finish();
  }

  // 1. 顶层
  if (gov.version !== 1) err("顶层 version != 1");
  if (gov.generated_by !== "25.3-01") err("顶层 generated_by != '25.3-01'");
  if (!Array.isArray(gov.packages)) err("顶层 packages 不是数组");

  const govByName = new Map();
  for (const entry of gov.packages ?? []) {
    govByName.set(entry.name, entry);

    // 2. 条目基本形状
    for (const field of ["name", "version", "source", "verdict", "installed"]) {
      if (entry[field] === undefined || entry[field] === null) {
        err(`[${entry.name ?? "<unnamed>"}] 缺少字段: ${field}`);
      }
    }
    if (entry.version !== undefined && !EXACT_PIN_RE.test(String(entry.version))) {
      err(`[${entry.name}] 版本不是精确 pin（含 ^/~ 或通配）: ${entry.version}`);
    }
    if (entry.source !== undefined && !SOURCES.has(entry.source)) {
      err(`[${entry.name}] source 非法: ${entry.source}`);
    }
    if (entry.verdict !== undefined && !VERDICTS.has(entry.verdict)) {
      err(`[${entry.name}] verdict 非法: ${entry.verdict}`);
    }
    if (typeof entry.installed !== "boolean") {
      err(`[${entry.name}] installed 必须是布尔值`);
    }

    // 3. installed=true 条目
    if (entry.installed === true) {
      if (entry.verdict === "reject") {
        err(`[${entry.name}] verdict=reject 但 installed=true`);
      }
      const pm = entry.permission_manifest;
      if (!pm || typeof pm !== "object") {
        err(`[${entry.name}] installed=true 缺少 permission_manifest`);
      } else {
        for (const field of D05_FIELDS) {
          if (!(field in pm)) err(`[${entry.name}] permission_manifest 缺少字段: ${field}`);
        }
        if (pm.filesystem !== "deny") err(`[${entry.name}] permission_manifest.filesystem 必须为 deny`);
        if (pm.shell !== "deny") err(`[${entry.name}] permission_manifest.shell 必须为 deny`);
        if (pm.env !== "deny") err(`[${entry.name}] permission_manifest.env 必须为 deny`);
        if (pm.secrets !== "named-only") err(`[${entry.name}] permission_manifest.secrets 必须为 named-only`);
        if (!["deny", "allowlist"].includes(pm.network)) err(`[${entry.name}] permission_manifest.network 必须为 deny|allowlist`);
        if (!Array.isArray(pm.network_allowlist)) err(`[${entry.name}] permission_manifest.network_allowlist 必须是数组`);
        if (!Array.isArray(pm.tools)) err(`[${entry.name}] permission_manifest.tools 必须是数组`);
        if (!Array.isArray(pm.artifact_writes)) err(`[${entry.name}] permission_manifest.artifact_writes 必须是数组`);
      }
    }

    // 6. adopt 必须存在 qualification_report
    if (entry.verdict === "adopt") {
      if (!entry.qualification_report) {
        err(`[${entry.name}] adopt 条目缺少 qualification_report`);
      } else {
        const reportPath = path.join(AGENT_SERVICE, entry.qualification_report);
        if (!fs.existsSync(reportPath)) {
          err(`[${entry.name}] qualification_report 文件不存在: ${entry.qualification_report}`);
        }
      }
    } else if (entry.qualification_report) {
      const reportPath = path.join(AGENT_SERVICE, entry.qualification_report);
      if (!fs.existsSync(reportPath)) {
        err(`[${entry.name}] qualification_report 文件不存在: ${entry.qualification_report}`);
      }
    }
  }

  // 5. installed=true 条目与 package-lock.json 的 version+integrity 一致性
  for (const entry of gov.packages ?? []) {
    if (entry.installed !== true) continue;
    const lockKey = `node_modules/${entry.name}`;
    const lockEntry = npm.packages?.[lockKey];
    if (!lockEntry) {
      err(`[${entry.name}] installed=true 但 package-lock.json 无该条目`);
      continue;
    }
    if (lockEntry.version !== entry.version) {
      err(`[${entry.name}] 版本不一致: manifest=${entry.version}, package-lock=${lockEntry.version}`);
    }
    if (!entry.integrity || entry.integrity !== lockEntry.integrity) {
      err(`[${entry.name}] integrity 不一致或缺失（manifest=${entry.integrity ?? "<缺失>"}）`);
    }
  }

  // 4. 每个已装 runtime 依赖都有非 reject 的清单条目
  for (const [name, spec] of Object.entries(pkgJson.dependencies ?? {})) {
    const entry = govByName.get(name);
    if (!entry) {
      err(`[${name}] 已装 runtime 依赖在 packages.lock.json 无条目`);
      continue;
    }
    if (entry.installed !== true) {
      err(`[${name}] runtime 依赖存在但 installed != true`);
    }
    if (entry.verdict === "reject") {
      err(`[${name}] runtime 依赖 verdict=reject`);
    }
    if (!entry.permission_manifest) {
      err(`[${name}] runtime 依赖缺少 permission_manifest`);
    }
    const lockKey = `node_modules/${name}`;
    const lockEntry = npm.packages?.[lockKey];
    if (lockEntry) {
      if (lockEntry.version !== entry.version) {
        err(`[${name}] 版本不一致: manifest=${entry.version}, package-lock=${lockEntry.version}`);
      }
      if (lockEntry.integrity !== entry.integrity) {
        err(`[${name}] integrity 不一致: manifest=${entry.integrity ?? "<缺失>"}`);
      }
    }
    if (spec !== entry.version) {
      err(`[${name}] package.json 声明 ${spec} 与 manifest ${entry.version} 不一致`);
    }
  }

  // 7. @earendil-works/* 单版本（Pitfall 3）——只看官方 scope 自身包，嵌套其下的普通依赖不算
  const earendilVersions = new Map();
  for (const [key, entry] of Object.entries(npm.packages ?? {})) {
    const leaf = key.replace(/^node_modules\//, "").split("/node_modules/").pop();
    if (!leaf.startsWith("@earendil-works/")) continue;
    if (!earendilVersions.has(leaf)) earendilVersions.set(leaf, entry.version);
    else if (earendilVersions.get(leaf) !== entry.version) {
      err(`[${leaf}] 闭包内存在多个版本: ${earendilVersions.get(leaf)} 与 ${entry.version}`);
    }
  }

  return finish();
}

function finish() {
  if (errors.length > 0) {
    for (const message of errors) {
      console.error(`[verify-lockfile] FAIL: ${message}`);
    }
    console.error(`[verify-lockfile] ${errors.length} 个不一致，fail-closed`);
    process.exit(1);
  }
  console.error(`[verify-lockfile] PASS: ${notes.length ? notes.join("; ") : "全部一致"}`);
  process.exit(0);
}

main();
