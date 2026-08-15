/**
 * Deterministic in-memory `DataFs` for the data-lifecycle suites (plan 43-03).
 *
 * Fault injection mirrors the real fault classes the migration transaction must
 * survive (plan Task 3): denied app-data writes, denied backups/version writes,
 * low disk space (explicit INSUFFICIENT_SPACE), corrupt backup entries and
 * interrupted (aborted) migrations. All files live in an in-memory tree keyed
 * by forward-slash paths; `statFreeBytes` is configurable so the
 * insufficient-space gate is testable.
 */
import type { DataFs, FileStat } from "../../src/data/app-data-layout";

export interface FakeFaults {
  /** Every writeFile/mkdir/rename/copyFile throws. */
  denyAllWrites?: boolean;
  /** Only writes under `denyPathPrefix` throw. */
  denyPathPrefix?: string | null;
  /** Free bytes reported by statFreeBytes (defaults to MAX_SAFE_INTEGER). */
  freeBytes?: number;
  /** Corrupt `backups/<txn>/<relPath>` reads after backup (hash mismatch). */
  corruptBackupPath?: string | null;
}

interface FakeNode {
  children: Map<string, FakeNode>;
  content: Buffer | null;
  isDir: boolean;
  mtimeMs: number;
}

export class FakeDataFs implements DataFs {
  readonly writeLog: string[] = [];
  faults: FakeFaults = {};
  private readonly root: FakeNode = { children: new Map(), content: null, isDir: true, mtimeMs: 0 };
  private clock = 1_000_000;

  /** Seed a file at a path (forward or back slashes; creates parent dirs). */
  seed(path: string, content: string): void {
    const node = this.nodeFor(this.toRel(path), { createParents: true });
    node.content = Buffer.from(content, "utf8");
    node.isDir = false;
  }

  /** Seed a directory tree from `{ "C:/.../data/a.txt": "hello", ... }`. */
  seedTree(files: Record<string, string>): void {
    for (const [p, content] of Object.entries(files)) this.seed(p, content);
  }

  /** Paths of every file currently in the tree (forward slashes), sorted. */
  listFiles(): string[] {
    const result: string[] = [];
    const walk = (node: FakeNode, prefix: string) => {
      for (const [name, child] of [...node.children.entries()].sort()) {
        const full = prefix === "" ? name : `${prefix}/${name}`;
        if (child.isDir) walk(child, full);
        else result.push(full);
      }
    };
    walk(this.root, "");
    return result;
  }

  /** Read a file's content as UTF-8 string (throws when missing). */
  content(path: string): string {
    const node = this.nodeFor(this.toRel(path), { createParents: false });
    if (node.isDir || node.content === null) throw new Error(`not a file: ${path}`);
    return node.content.toString("utf8");
  }

  /** Number of copyFile calls performed. */
  copyCount(): number {
    return this.writeLog.filter((entry) => entry.startsWith("copy ")).length;
  }

  private nodeFor(path: string, opts: { createParents: boolean }): FakeNode {
    const segments = path.split("/").filter((s) => s.length > 0);
    let node = this.root;
    for (let i = 0; i < segments.length; i += 1) {
      const seg = segments[i] as string;
      let child = node.children.get(seg);
      if (child === undefined) {
        if (!opts.createParents) throw new Error(`path not found: ${path}`);
        child = { children: new Map(), content: null, isDir: true, mtimeMs: this.clock++ };
        node.children.set(seg, child);
      }
      if (i < segments.length - 1 && !child.isDir) {
        throw new Error(`parent is a file: ${path}`);
      }
      node = child;
    }
    return node;
  }

  private denyCheck(p: string): void {
    if (this.faults.denyAllWrites === true) {
      throw new Error(`write denied: ${p}`);
    }
    const denyPrefix = this.faults.denyPathPrefix;
    if (typeof denyPrefix === "string" && p.startsWith(denyPrefix)) {
      throw new Error(`write denied under ${denyPrefix}: ${p}`);
    }
  }

  private toRel(absOrRel: string): string {
    return absOrRel.replace(/\\/g, "/");
  }

  async mkdir(p: string, _opts?: { recursive?: boolean }): Promise<void> {
    const rel = this.toRel(p);
    this.denyCheck(rel);
    const segments = rel.split("/").filter((s) => s.length > 0);
    let node = this.root;
    for (const seg of segments) {
      let child = node.children.get(seg);
      if (child === undefined) {
        child = { children: new Map(), content: null, isDir: true, mtimeMs: this.clock++ };
        node.children.set(seg, child);
      } else if (!child.isDir) {
        throw new Error(`mkdir: not a directory: ${rel}`);
      }
      node = child;
    }
  }

  async writeFile(p: string, data: string): Promise<void> {
    const rel = this.toRel(p);
    this.writeLog.push(`write ${rel}`);
    this.denyCheck(rel);
    const node = this.nodeFor(rel, { createParents: true });
    node.content = Buffer.from(data, "utf8");
    node.isDir = false;
    node.mtimeMs = this.clock++;
  }

  async readFile(p: string): Promise<string> {
    return this.content(this.toRel(p));
  }

  async readBuffer(p: string): Promise<Buffer> {
    const rel = this.toRel(p);
    const corruptPrefix = this.faults.corruptBackupPath;
    if (typeof corruptPrefix === "string" && rel.includes(corruptPrefix)) {
      return Buffer.from("CORRUPTED", "utf8");
    }
    const node = this.nodeFor(rel, { createParents: false });
    if (node.isDir || node.content === null) throw new Error(`read: not a file: ${rel}`);
    return node.content;
  }

  async rename(oldPath: string, newPath: string): Promise<void> {
    const oldRel = this.toRel(oldPath);
    const newRel = this.toRel(newPath);
    this.writeLog.push(`rename ${oldRel} -> ${newRel}`);
    this.denyCheck(newRel);
    const node = this.nodeFor(oldRel, { createParents: false });
    this.nodeFor(newRel, { createParents: true });
    this.deleteNode(oldRel);
    const newNode = this.nodeFor(newRel, { createParents: true });
    newNode.content = node.content;
    newNode.isDir = node.isDir;
    newNode.mtimeMs = this.clock++;
  }

  async copyFile(src: string, dest: string): Promise<void> {
    const srcRel = this.toRel(src);
    const destRel = this.toRel(dest);
    this.writeLog.push(`copy ${srcRel} -> ${destRel}`);
    this.denyCheck(destRel);
    const node = this.nodeFor(srcRel, { createParents: false });
    if (node.isDir || node.content === null) throw new Error(`copy: not a file: ${srcRel}`);
    const destNode = this.nodeFor(destRel, { createParents: true });
    destNode.content = Buffer.from(node.content);
    destNode.isDir = false;
    destNode.mtimeMs = this.clock++;
  }

  async readdir(p: string): Promise<string[]> {
    const rel = this.toRel(p);
    const node = this.nodeFor(rel, { createParents: false });
    if (!node.isDir) throw new Error(`readdir: not a directory: ${rel}`);
    return [...node.children.keys()].sort();
  }

  async stat(p: string): Promise<FileStat> {
    const rel = this.toRel(p);
    const node = this.nodeFor(rel, { createParents: false });
    return {
      isDirectory: () => node.isDir,
      size: node.content === null ? 0 : node.content.length,
      mtimeMs: node.mtimeMs,
    };
  }

  async exists(p: string): Promise<boolean> {
    try {
      this.nodeFor(this.toRel(p), { createParents: false });
      return true;
    } catch {
      return false;
    }
  }

  async rm(p: string, opts: { recursive?: boolean; force?: boolean }): Promise<void> {
    const rel = this.toRel(p);
    this.writeLog.push(`rm ${rel}`);
    this.denyCheck(rel);
    try {
      this.nodeFor(rel, { createParents: false });
    } catch {
      if (opts.force === true) return;
      throw new Error(`rm: not found: ${rel}`);
    }
    this.deleteNode(rel);
  }

  async statFreeBytes(_p: string): Promise<number> {
    return this.faults.freeBytes ?? Number.MAX_SAFE_INTEGER;
  }

  private deleteNode(path: string): void {
    const segments = path.split("/").filter((s) => s.length > 0);
    let node = this.root;
    for (let i = 0; i < segments.length; i += 1) {
      const seg = segments[i] as string;
      const child = node.children.get(seg);
      if (child === undefined) return;
      if (i === segments.length - 1) {
        node.children.delete(seg);
        return;
      }
      node = child;
    }
  }
}
