/**
 * Component log sinks under %APPDATA%/NovelMind/logs/{component} (Phase 43,
 * plan 43-02, D-43-05, T-43-02-02).
 *
 * Every runtime component writes to its own directory under the versioned
 * app-data root; installed application resources are never written to. Log
 * files are size-bounded and rotated (keep `maxFiles` rotated files), and every
 * line is redacted before it is persisted so tokens / provider keys / bearer
 * credentials never land on disk (T-43-02-02).
 */
import {
  createWriteStream,
  existsSync,
  mkdirSync,
  readdirSync,
  renameSync,
  statSync,
  unlinkSync,
  type WriteStream,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import type { RuntimeComponent } from "./types";

/** The versioned writable app-data root for the desktop runtime. */
export function appDataRoot(root?: string): string {
  if (root !== undefined) return root;
  return path.join(process.env.APPDATA ?? path.join(os.homedir(), ".novelmind"), "NovelMind");
}

/** Per-component log directory: <appDataRoot>/logs/<component>. */
export function componentLogDir(
  component: RuntimeComponent,
  root?: string,
): string {
  return path.join(appDataRoot(root), "logs", component);
}

/** Recursively creates a directory. Returns the input for chaining. */
export function ensureDir(dir: string): string {
  mkdirSync(dir, { recursive: true });
  return dir;
}

// ── Redaction (T-43-02-02) ───────────────────────────────────────────────────

const SECRET_PATTERNS: readonly RegExp[] = [
  // Authorization headers: "Bearer <jwt/opaque>"
  /\bBearer\s+[A-Za-z0-9._~+/=-]+/g,
  // JWT / opaque tokens: three dot segments or long base64-ish runs
  /\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b/g,
  // OpenAI-style provider keys: sk-proj-<long> and sk-<long>
  /\bsk-[A-Za-z0-9_-]{16,}\b/g,
  // Google API keys
  /\bAIza[0-9A-Za-z_-]{20,}\b/g,
  // key=value / key:value secret assignments (name only, value redacted)
  /\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|auth[_-]?token)\s*[=:]\s*[^\s,;"']+/gi,
  // UPPER_SNAKE env assignments: POSTGRES_PASSWORD=..., SECRET_KEY=...
  /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|ACCESS_?KEY|AUTH_TOKEN)\s*=\s*[^\s,;"']+/gi,
];

/**
 * Replaces every recognized secret shape in a log line with a fixed marker.
 * Safe for secrets in URLs, headers, env dumps and error messages.
 */
export function redactLine(line: string): string {
  let out = line;
  for (const pattern of SECRET_PATTERNS) {
    out = out.replace(pattern, "[REDACTED]");
  }
  return out;
}

export interface LogSink {
  write(stream: "stdout" | "stderr", line: string): void;
  close(): void;
}

export interface ComponentLoggerOptions {
  component: RuntimeComponent;
  /** App-data root. Defaults to %APPDATA%/NovelMind. */
  root?: string;
  /** Single log file size cap before rotation. Default 1 MiB. */
  maxBytes?: number;
  /** Number of rotated files kept (oldest pruned). Default 3. */
  maxFiles?: number;
}

/**
 * Bounded, redacted, rotating per-component log sink at
 * <appDataRoot>/logs/<component>/<component>.log.
 */
export class ComponentLogger implements LogSink {
  readonly dir: string;
  readonly logPath: string;
  private readonly maxBytes: number;
  private readonly maxFiles: number;
  private stream: WriteStream | null = null;

  constructor(options: ComponentLoggerOptions) {
    this.dir = ensureDir(componentLogDir(options.component, options.root));
    this.logPath = path.join(this.dir, `${options.component}.log`);
    this.maxBytes = options.maxBytes ?? 1024 * 1024;
    this.maxFiles = Math.max(1, options.maxFiles ?? 3);
  }

  /** Appends a redacted, timestamped line to the component log. */
  write(stream: "stdout" | "stderr", line: string): void {
    this.rotateIfNeeded();
    if (this.stream === null) {
      this.stream = createWriteStream(this.logPath, { flags: "a" });
    }
    this.stream.write(
      `${stream === "stderr" ? "ERR" : "OUT"} ${new Date().toISOString()} ${redactLine(line)}\n`,
    );
  }

  /** Flushes and closes the underlying stream. Subsequent writes reopen it. */
  async close(): Promise<void> {
    const stream = this.stream;
    this.stream = null;
    if (stream === null) return;
    if (stream.writableFinished) return;
    await new Promise<void>((resolve) => {
      const done = (): void => resolve();
      stream.once("finish", done);
      stream.once("error", done);
      stream.end();
    });
  }

  /** Size-based rotation: rename the active file, prune old rotations. */
  private rotateIfNeeded(): void {
    if (!existsSync(this.logPath)) return;
    let size = 0;
    try {
      size = statSync(this.logPath).size;
    } catch {
      return;
    }
    if (size < this.maxBytes) return;
    this.closeStream();
    const name = path.basename(this.logPath, ".log");
    const rotated = path.join(this.dir, `${name}.${Date.now()}.log`);
    try {
      renameSync(this.logPath, rotated);
    } catch {
      return; // best-effort rotation; the active file stays bounded next write
    }
    this.pruneRotations(name);
  }

  /** Synchronous best-effort close used by rotation. */
  private closeStream(): void {
    this.stream?.end();
    this.stream = null;
  }

  /** Keeps at most `maxFiles` rotated files for this component. */
  private pruneRotations(name: string): void {
    let files: string[];
    try {
      files = readdirSync(this.dir);
    } catch {
      return;
    }
    const prefix = `${name}.`;
    const rotated = files
      .filter((f) => f.startsWith(prefix) && f.endsWith(".log"))
      .sort();
    const excess = rotated.length - this.maxFiles;
    for (let i = 0; i < excess; i += 1) {
      const file = rotated[i];
      if (file === undefined) break;
      try {
        unlinkSync(path.join(this.dir, file));
      } catch {
        // prune is best-effort
      }
    }
  }
}
