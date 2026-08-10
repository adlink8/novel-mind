/**
 * Redacted credential status contract (Phase 44, plan 44-02).
 *
 * PURE module — no Node/Electron imports — so it crosses the main→renderer
 * trust boundary exactly like `bootstrap-contract.ts`. It carries ONLY boolean /
 * stable-state signals about OS-protected credential material: NEVER the
 * credentials, provider keys, session tokens or any encrypted blob (D-44-03 /
 * T-44-02-01).
 *
 * The renderer can render an honest "unavailable / needs rotation / ok" state
 * from this payload without ever touching the underlying secrets. The Electron
 * `safeStorage` guarantee is described accurately to the user as OS/user-bound
 * DPAPI protection — never as an absolute security boundary (44-RESEARCH).
 */
export type CredentialState =
  /** A decryptable value is stored for this namespace. */
  | "available"
  /** Nothing stored, or the OS protection backend is unusable this session. */
  | "unavailable"
  /** A stored blob exists but cannot be read back (corrupt / wrong format). */
  | "decrypt_failed"
  /** The OS key changed; re-encryption with the current key is required. */
  | "rotation_needed";

/**
 * The only credential surface the renderer may consume. Each field is a stable
 * state string — no values, no keys, no blob fragments (T-44-02-01).
 */
export interface RedactedCredentialStatus {
  /** Provider (LLM / embedding / image) credentials held by the main process. */
  provider: CredentialState;
  /** Local gateway / session material held by the main process. */
  localAuth: CredentialState;
  /** Whether the OS-backed protection (safeStorage) is usable this session. */
  storageAvailable: boolean;
}
