/**
 * OS-protected credential store (Phase 44, plan 44-02, Task 1).
 *
 * Main-process only. Uses Electron's asynchronous `safeStorage` (Windows DPAPI
 * on Windows — user-bound OS protection, not a defense against every process
 * running as the same user; the UI contract states this honestly). Rules:
 *
 * - Encrypted blobs live ONLY under the app-data secrets root
 *   (`%APPDATA%/NovelMind/secrets`, D-44-03); paths are derived through the
 *   shared `containPath` traversal guard.
 * - `safeStorage` is injected so the module stays Electron-free and unit
 *   testable; the Electron main passes the real API after `app` ready.
 * - NEVER a plaintext fallback, renderer localStorage, or secret-bearing logs
 *   (T-44-02-01). When the OS backend is unavailable every write fails closed
 *   and every read reports `unavailable`.
 * - States distinguish unavailable / decrypt-failed (corrupt blob) /
 *   rotation-needed (OS key changed → re-encrypt with the current key heals
 *   it) / available. `rotate()` re-encrypts every stored blob.
 * - The store exposes ONLY redacted status to any caller outside main
 *   (`RedactedCredentialStatus` from ../shared/credential-status).
 */
import { containPath } from "../data/app-data-layout";
import type { AppDataPaths, DataFs } from "../data/app-data-layout";
import type { CredentialState, RedactedCredentialStatus } from "../shared/credential-status";

/** The two credential namespaces the status contract distinguishes. */
export const CREDENTIAL_NAMESPACES = ["provider", "local-auth"] as const;
export type CredentialNamespace = (typeof CREDENTIAL_NAMESPACES)[number];

export const CREDENTIAL_STORE_FORMAT_VERSION = 1 as const;

/**
 * Minimal safeStorage seam. Real implementation is Electron
 * `safeStorage` (main process, after `app` ready); tests inject fakes.
 */
export interface SafeStorage {
  /** Windows DPAPI available for this user session. */
  isEncryptionAvailable(): boolean;
  /**
   * Identifier of the OS key currently in effect. Used to detect blobs written
   * under an earlier key (rotation_needed) even when the old key is retained
   * and still decrypts. The real Electron wrapper returns a stable
   * session-scoped id (per-process nonce), so a blob written in a prior app
   * session is detected as needing re-encryption.
   */
  currentKeyId(): string;
  /** Encrypt a UTF-8 string into an opaque buffer. */
  encryptString(plainText: string): Buffer;
  /** Decrypt an opaque buffer back to the string; throws when it fails. */
  decryptString(encrypted: Buffer): string;
}

export type CredentialErrorCode =
  | "safe_storage_unavailable"
  | "invalid_namespace"
  | "invalid_key"
  | "not_found"
  | "decrypt_failed"
  | "write_failed"
  | "read_failed";

export interface CredentialError {
  code: CredentialErrorCode;
  message: string;
}

export type CredentialResult<T> = { ok: true; value: T } | { ok: false; error: CredentialError };

/** Namespace / key naming: letters, digits, `-`, `_`, `.`. Never separators. */
const NAME_PATTERN = /^[A-Za-z0-9._-]+$/;

function fail<T>(code: CredentialErrorCode, message: string): CredentialResult<T> {
  return { ok: false, error: { code, message } };
}

function isNameValid(name: string): boolean {
  return name.length > 0 && name.length <= 128 && NAME_PATTERN.test(name);
}

interface StoredBlob {
  v: typeof CREDENTIAL_STORE_FORMAT_VERSION;
  namespace: string;
  key: string;
  /** OS key identifier in effect when the blob was encrypted (rotation detection). */
  keyId: string;
  /** base64 of the safeStorage-encrypted bytes. */
  encrypted: string;
}

export interface CredentialStoreDeps {
  paths: AppDataPaths;
  fs: DataFs;
  safeStorage: SafeStorage;
}

export class CredentialStore {
  private readonly paths: AppDataPaths;
  private readonly fs: DataFs;
  private readonly safeStorage: SafeStorage;

  constructor(deps: CredentialStoreDeps) {
    this.paths = deps.paths;
    this.fs = deps.fs;
    this.safeStorage = deps.safeStorage;
  }

  /** Whether the OS-backed protection is usable this session. */
  isStorageAvailable(): boolean {
    return this.safeStorage.isEncryptionAvailable();
  }

  /**
   * Redacted, honest status for the renderer. Never any value. A decrypt
   * failure on a well-formed blob is reported as rotation_needed (re-encrypt
   * heals it); a malformed blob is decrypt_failed (needs re-creation).
   */
  async status(): Promise<RedactedCredentialStatus> {
    const storageAvailable = this.isStorageAvailable();
    if (!storageAvailable) {
      return { provider: "unavailable", localAuth: "unavailable", storageAvailable: false };
    }
    const provider = await this.namespaceState("provider");
    const localAuth = await this.namespaceState("local-auth");
    return { provider, localAuth, storageAvailable: true };
  }

  /**
   * Store a secret: encrypt NOW with the current safeStorage key and persist
   * only the encrypted blob. Fails closed (never plaintext, never a fallback)
   * when the OS backend is unavailable or the write fails.
   */
  async setSecret(namespace: string, key: string, value: string): Promise<CredentialResult<void>> {
    const nameCheck = this.validateName(namespace, key);
    if (!nameCheck.ok) return nameCheck;
    if (!this.isStorageAvailable()) {
      return fail("safe_storage_unavailable", "OS credential protection is unavailable");
    }
    const target = this.blobPath(namespace, key);
    const blob: StoredBlob = {
      v: CREDENTIAL_STORE_FORMAT_VERSION,
      namespace,
      key,
      keyId: this.safeStorage.currentKeyId(),
      encrypted: this.safeStorage.encryptString(value).toString("base64"),
    };
    try {
      await this.fs.mkdir(this.namespaceDir(namespace), { recursive: true });
      await this.fs.writeFile(target, JSON.stringify(blob));
      return { ok: true, value: undefined };
    } catch (cause) {
      return fail("write_failed", `could not persist secret: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
  }

  /** Read and decrypt a secret. Never returns plaintext to any caller's log. */
  async getSecret(namespace: string, key: string): Promise<CredentialResult<string>> {
    const nameCheck = this.validateName(namespace, key);
    if (!nameCheck.ok) return nameCheck;
    if (!this.isStorageAvailable()) {
      return fail("safe_storage_unavailable", "OS credential protection is unavailable");
    }
    const read = await this.readBlob(namespace, key);
    if (!read.ok) return read;
    try {
      const plain = this.safeStorage.decryptString(Buffer.from(read.value.encrypted, "base64"));
      return { ok: true, value: plain };
    } catch {
      // Well-formed blob but the current key cannot open it: the OS key rotated.
      return fail("decrypt_failed", "stored credential cannot be decrypted with the current key");
    }
  }

  async deleteSecret(namespace: string, key: string): Promise<CredentialResult<void>> {
    const nameCheck = this.validateName(namespace, key);
    if (!nameCheck.ok) return nameCheck;
    if (!this.isStorageAvailable()) {
      return fail("safe_storage_unavailable", "OS credential protection is unavailable");
    }
    try {
      if (!(await this.fs.exists(this.blobPath(namespace, key)))) {
        return { ok: false, error: { code: "not_found", message: "no stored credential for key" } };
      }
      await this.fs.rm(this.blobPath(namespace, key), { force: true });
      return { ok: true, value: undefined };
    } catch (cause) {
      return fail("write_failed", `could not delete secret: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
  }

  /**
   * Re-encrypt every stored blob with the current safeStorage key. Heals
   * `rotation_needed` states; fails closed on any unreadable blob (reported as
   * `decrypt_failed` — the caller must re-create or remove it).
   */
  async rotate(): Promise<CredentialResult<{ reencrypted: number }>> {
    if (!this.isStorageAvailable()) {
      return fail("safe_storage_unavailable", "OS credential protection is unavailable");
    }
    let reencrypted = 0;
    const namespaces = [this.namespaceDir("provider"), this.namespaceDir("local-auth")];
    for (const dir of namespaces) {
      let entries: string[] = [];
      try {
        if (await this.fs.exists(dir)) entries = await this.fs.readdir(dir);
      } catch {
        return fail("read_failed", "could not list credential directory");
      }
      for (const entry of entries) {
        if (!entry.endsWith(".json")) continue;
        const key = entry.slice(0, -".json".length);
        const read = await this.readBlob(this.namespaceOfDir(dir), key);
        if (!read.ok) return read;
        let plain: string;
        try {
          plain = this.safeStorage.decryptString(Buffer.from(read.value.encrypted, "base64"));
        } catch {
          return fail("decrypt_failed", `cannot re-encrypt ${key}: OS key cannot open the blob`);
        }
        const fresh: StoredBlob = {
          v: CREDENTIAL_STORE_FORMAT_VERSION,
          namespace: read.value.namespace,
          key,
          keyId: this.safeStorage.currentKeyId(),
          encrypted: this.safeStorage.encryptString(plain).toString("base64"),
        };
        try {
          await this.fs.writeFile(this.blobPath(read.value.namespace, key), JSON.stringify(fresh));
          reencrypted += 1;
        } catch {
          return fail("write_failed", `could not persist re-encrypted blob for ${key}`);
        }
      }
    }
    return { ok: true, value: { reencrypted } };
  }

  /** Decrypt every stored blob; any decrypt failure flips the namespace state. */
  async namespaceState(namespace: string): Promise<CredentialState> {
    if (!this.isStorageAvailable()) return "unavailable";
    const dir = this.namespaceDir(namespace);
    let entries: string[];
    try {
      if (!(await this.fs.exists(dir))) return "unavailable";
      entries = await this.fs.readdir(dir);
    } catch {
      return "unavailable";
    }
    const keys = entries.filter((e) => e.endsWith(".json"));
    if (keys.length === 0) return "unavailable";
    let sawRotationNeeded = false;
    for (const entry of keys) {
      const key = entry.slice(0, -".json".length);
      const read = await this.readBlob(namespace, key);
      if (!read.ok) {
        // Malformed blob (unreadable JSON/base64): genuinely cannot decrypt.
        return "decrypt_failed";
      }
      try {
        this.safeStorage.decryptString(Buffer.from(read.value.encrypted, "base64"));
      } catch {
        // Well-formed blob the current OS key cannot open: needs rotation.
        return "rotation_needed";
      }
      if (read.value.keyId !== this.safeStorage.currentKeyId()) {
        // Value is still decryptable (old key retained) but encrypted under an
        // earlier OS key: refresh is required.
        sawRotationNeeded = true;
      }
    }
    return sawRotationNeeded ? "rotation_needed" : "available";
  }

  private validateName(namespace: string, key: string): CredentialResult<void> {
    if (!CREDENTIAL_NAMESPACES.includes(namespace as CredentialNamespace)) {
      return fail("invalid_namespace", `unknown credential namespace: ${namespace}`);
    }
    if (!isNameValid(key)) {
      return fail("invalid_key", "credential key must match [A-Za-z0-9._-]{1,128}");
    }
    return { ok: true, value: undefined };
  }

  private namespaceDir(namespace: string): string {
    return containPath(this.paths.secrets, namespace);
  }

  private blobPath(namespace: string, key: string): string {
    return containPath(this.paths.secrets, namespace, `${key}.json`);
  }

  private namespaceOfDir(dir: string): string {
    return dir.endsWith("local-auth") ? "local-auth" : "provider";
  }

  private async readBlob(namespace: string, key: string): Promise<CredentialResult<StoredBlob>> {
    let raw: Buffer;
    try {
      raw = await this.fs.readBuffer(this.blobPath(namespace, key));
    } catch {
      return fail("not_found", "no stored credential for key");
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw.toString("utf8"));
    } catch {
      return fail("decrypt_failed", "stored credential blob is malformed");
    }
    const blob = parsed as Partial<StoredBlob>;
    if (
      blob?.v !== CREDENTIAL_STORE_FORMAT_VERSION ||
      typeof blob.encrypted !== "string" ||
      typeof blob.keyId !== "string"
    ) {
      return fail("decrypt_failed", "stored credential blob has an unknown format");
    }
    return { ok: true, value: blob as StoredBlob };
  }
}
