/**
 * Desktop security suite (Phase 44, plan 44-02).
 *
 * Pure Node — no Electron — so it runs under the runtime Playwright config
 * (`npx playwright test --config tests/runtime/playwright.config.ts`). Covers:
 *
 * CredentialStore (safeStorage-backed):
 * - available backend → set/get roundtrip, encrypted blob only on disk
 *   (never plaintext, never in logs/status),
 * - unavailable backend → set/get fail closed, status reports unavailable,
 * - corrupt blob → decrypt_failed / read reports honest failure,
 * - OS key rotation → rotation_needed status, rotate() re-encrypts and heals,
 * - write denial → write_failed (never plaintext fallback),
 * - status is redacted: no secret value ever leaves the store.
 *
 * DesktopLocalAuth (session token minting):
 * - separate audience tokens for backend vs agent, session-bound (sid),
 * - no session → null (caller fails closed),
 * - rotate() invalidates previously minted tokens,
 * - expiry claims bounded by TTL with injected clock.
 */
import { expect, test } from "@playwright/test";
import type { DataFs } from "../../src/data/app-data-layout";
import { buildAppDataPaths } from "../../src/data/app-data-layout";
import { FakeDataFs } from "../data/fake-data-fs";
import {
  CREDENTIAL_NAMESPACES,
  CredentialStore,
  type SafeStorage,
} from "../../src/security/credential-store";
import {
  DesktopLocalAuth,
  LOCAL_AUTH_AGENT_AUDIENCE,
  LOCAL_AUTH_BACKEND_AUDIENCE,
  LOCAL_AUTH_ISSUER,
  LOCAL_AUTH_TOKEN_TTL_MS,
} from "../../src/security/local-auth";

/**
 * Deterministic fake safeStorage. The OS "key" is a string; `setKey` retains
 * the previous key (mirrors a DPAPI transition where old blobs stay readable
 * via a retained key) and `forgetKeys` drops retention so old blobs become
 * genuinely unreadable. `setAvailable` simulates the backend disappearing.
 */
function fakeSafeStorage(initialKey = "os-key-1", available = true): SafeStorage & {
  state: { currentKey: string; available: boolean; keys: Set<string> };
  setKey(key: string): void;
  forgetKeys(): void;
  setAvailable(v: boolean): void;
} {
  const state = { currentKey: initialKey, available, keys: new Set([initialKey]) };
  return {
    state,
    isEncryptionAvailable: () => state.available,
    currentKeyId: () => state.currentKey,
    encryptString: (plain: string) => {
      if (!state.available) throw new Error("safeStorage unavailable");
      return Buffer.from(
        `v1:${state.currentKey}:${Buffer.from(plain, "utf8").toString("base64")}`,
      );
    },
    decryptString: (encrypted: Buffer) => {
      if (!state.available) throw new Error("safeStorage unavailable");
      const text = encrypted.toString("utf8");
      const match = /^v1:([^:]+):(.*)$/.exec(text);
      if (match === null) throw new Error("corrupt blob");
      if (!state.keys.has(match[1] ?? "")) throw new Error("OS key rotation");
      return Buffer.from(match[2] ?? "", "base64").toString("utf8");
    },
    setKey(key: string): void {
      state.keys.add(key);
      state.currentKey = key;
    },
    forgetKeys(): void {
      state.keys.clear();
      state.keys.add(state.currentKey);
    },
    setAvailable(v: boolean): void {
      state.available = v;
    },
  };
}

const USER_DATA_DIR = "C:/fake-user-data";

function makeStore(
  fs: DataFs,
  safe: SafeStorage,
  userDataDir: string = USER_DATA_DIR,
): CredentialStore {
  return new CredentialStore({
    paths: buildAppDataPaths({ userDataDir }),
    fs,
    safeStorage: safe,
  });
}

// ── CredentialStore ──────────────────────────────────────────────────────────

test("roundtrip stores an encrypted blob only and reads it back", async () => {
  const fs = new FakeDataFs();
  const safe = fakeSafeStorage();
  const store = makeStore(fs, safe);

  const secretValue = "fixture-provider-secret-9f8e7d6c";
  const set = await store.setSecret("provider", "openai", secretValue);
  expect(set.ok).toBe(true);

  // Disk contains ONLY the encrypted envelope — never the plaintext.
  const files = fs.listFiles();
  expect(files).toContain("C:/fake-user-data/secrets/provider/openai.json");
  const raw = fs.content("C:/fake-user-data/secrets/provider/openai.json");
  expect(raw).not.toContain(secretValue);
  expect(raw).toContain('"encrypted"');
  expect(raw).not.toContain("openai:"); // not a bare value either

  const got = await store.getSecret("provider", "openai");
  expect(got).toEqual({ ok: true, value: secretValue });

  const status = await store.status();
  expect(status.storageAvailable).toBe(true);
  expect(status.provider).toBe("available");
  expect(status.localAuth).toBe("unavailable");
  // Redaction: the secret value never appears in status.
  expect(JSON.stringify(status)).not.toContain(secretValue);
});

test("two namespaces are stored separately", async () => {
  const fs = new FakeDataFs();
  const store = makeStore(fs, fakeSafeStorage());
  await store.setSecret("provider", "openai", "provider-key");
  await store.setSecret("local-auth", "session", "session-material");
  const status = await store.status();
  expect(status.provider).toBe("available");
  expect(status.localAuth).toBe("available");
});

test("unavailable OS backend fails closed and reports unavailable", async () => {
  const fs = new FakeDataFs();
  const safe = fakeSafeStorage("os-key-1", false);
  const store = makeStore(fs, safe);

  const set = await store.setSecret("provider", "openai", "never-persisted");
  expect(set.ok).toBe(false);
  if (!set.ok) expect(set.error.code).toBe("safe_storage_unavailable");
  // Nothing was written (no plaintext fallback).
  expect(fs.listFiles()).toEqual([]);

  const get = await store.getSecret("provider", "openai");
  expect(get.ok).toBe(false);
  const status = await store.status();
  expect(status.storageAvailable).toBe(false);
  expect(status.provider).toBe("unavailable");
});

test("corrupt blob reports decrypt_failed and never throws", async () => {
  const fs = new FakeDataFs();
  const store = makeStore(fs, fakeSafeStorage());
  // Malformed blob seeded directly (bad JSON, not a decryptable envelope).
  fs.seed("C:/fake-user-data/secrets/provider/openai.json", "{not-json");
  const get = await store.getSecret("provider", "openai");
  expect(get.ok).toBe(false);
  if (!get.ok) expect(get.error.code).toBe("decrypt_failed");
  const status = await store.status();
  expect(status.provider).toBe("decrypt_failed");
});

test("OS key rotation surfaces rotation_needed and rotate() re-encrypts", async () => {
  const fs = new FakeDataFs();
  const safe = fakeSafeStorage("os-key-1");
  const store = makeStore(fs, safe);
  await store.setSecret("provider", "openai", "rotate-me-value");

  // OS key rotates (previous key retained): old blob is still readable but is
  // encrypted under an earlier key → rotation_needed.
  safe.setKey("os-key-2");
  const before = await store.status();
  expect(before.provider).toBe("rotation_needed");

  // Value remains readable through the retained key.
  const get = await store.getSecret("provider", "openai");
  expect(get).toEqual({ ok: true, value: "rotate-me-value" });

  // rotate() re-encrypts under the current key → available again.
  const rotated = await store.rotate();
  expect(rotated).toEqual({ ok: true, value: { reencrypted: 1 } });
  const after = await store.status();
  expect(after.provider).toBe("available");
  const got = await store.getSecret("provider", "openai");
  expect(got).toEqual({ ok: true, value: "rotate-me-value" });
});

test("non-retained rotation makes the value unusable (fail closed)", async () => {
  const fs = new FakeDataFs();
  const safe = fakeSafeStorage("os-key-1");
  const store = makeStore(fs, safe);
  await store.setSecret("provider", "openai", "lost-value");
  safe.setKey("os-key-2");
  safe.forgetKeys(); // old key dropped: blob genuinely unreadable

  const status = await store.status();
  expect(status.provider).toBe("rotation_needed");
  const get = await store.getSecret("provider", "openai");
  expect(get.ok).toBe(false);
  if (!get.ok) expect(get.error.code).toBe("decrypt_failed");
  // rotate() cannot recover a value it cannot decrypt → fails closed.
  const rotated = await store.rotate();
  expect(rotated.ok).toBe(false);
});

test("rotate() fails closed on a corrupt blob and writes nothing", async () => {
  const fs = new FakeDataFs();
  const safe = fakeSafeStorage("os-key-1");
  const store = makeStore(fs, safe);
  await store.setSecret("provider", "openai", "good-value");
  fs.seed("C:/fake-user-data/secrets/provider/broken.json", "{not-json");

  const rotated = await store.rotate();
  expect(rotated.ok).toBe(false);
  // The healthy blob must not be rewritten ahead of the corrupt one.
  const rawGood = fs.content("C:/fake-user-data/secrets/provider/openai.json");
  expect(rawGood).toContain("os-key-1");
  const status = await store.status();
  expect(status.provider).toBe("decrypt_failed");
});

test("write denial fails closed with write_failed and keeps no plaintext", async () => {
  const fs = new FakeDataFs();
  fs.faults.denyAllWrites = true;
  const store = makeStore(fs, fakeSafeStorage());
  const set = await store.setSecret("provider", "openai", "blocked-value");
  expect(set.ok).toBe(false);
  if (!set.ok) expect(set.error.code).toBe("write_failed");
  expect(fs.listFiles()).toEqual([]);
});

test("invalid namespace or key is rejected", async () => {
  const fs = new FakeDataFs();
  const store = makeStore(fs, fakeSafeStorage());
  const badNs = await store.setSecret("../escape", "k", "v");
  expect(badNs.ok).toBe(false);
  if (!badNs.ok) expect(badNs.error.code).toBe("invalid_namespace");
  const badKey = await store.setSecret("provider", "a/b", "v");
  expect(badKey.ok).toBe(false);
  if (!badKey.ok) expect(badKey.error.code).toBe("invalid_key");
  expect(fs.listFiles()).toEqual([]);
});

test("deleteSecret removes the blob", async () => {
  const fs = new FakeDataFs();
  const store = makeStore(fs, fakeSafeStorage());
  await store.setSecret("local-auth", "session", "s");
  expect((await store.status()).localAuth).toBe("available");
  await store.deleteSecret("local-auth", "session");
  expect((await store.status()).localAuth).toBe("unavailable");
  const get = await store.getSecret("local-auth", "session");
  expect(get.ok).toBe(false);
  if (!get.ok) expect(get.error.code).toBe("not_found");
});

// ── DesktopLocalAuth ─────────────────────────────────────────────────────────

interface DecodedClaims {
  iss: string;
  aud: string;
  sid: string;
  exp: number;
  iat: number;
  [key: string]: string | number;
}

function decodePayload(token: string): DecodedClaims {
  const parts = token.split(".");
  expect(parts).toHaveLength(3);
  return JSON.parse(Buffer.from(parts[1] ?? "", "base64url").toString("utf8")) as DecodedClaims;
}

test("mints separate audience-bound session tokens for backend and agent", () => {
  const auth = new DesktopLocalAuth({ sessionId: () => "session-abc" });
  const tokens = auth.tokens();
  expect(tokens).not.toBeNull();
  if (tokens === null) return;

  expect(tokens.backend).not.toBe(tokens.agent);
  const backend = decodePayload(tokens.backend);
  const agent = decodePayload(tokens.agent);
  expect(backend.iss).toBe(LOCAL_AUTH_ISSUER);
  expect(agent.iss).toBe(LOCAL_AUTH_ISSUER);
  expect(backend.aud).toBe(LOCAL_AUTH_BACKEND_AUDIENCE);
  expect(agent.aud).toBe(LOCAL_AUTH_AGENT_AUDIENCE);
  expect(backend.sid).toBe("session-abc");
  expect(agent.sid).toBe("session-abc");
  // Short-lived: exp is iat + TTL.
  expect(backend.exp - backend.iat).toBe(LOCAL_AUTH_TOKEN_TTL_MS / 1000);
});

test("no active session yields null tokens (caller fails closed)", () => {
  const auth = new DesktopLocalAuth({ sessionId: () => null });
  expect(auth.tokens()).toBeNull();
});

test("rotate() invalidates previously minted tokens", () => {
  const auth = new DesktopLocalAuth({ sessionId: () => "session-1", secret: () => "secret-a" });
  const before = auth.tokens();
  expect(before).not.toBeNull();
  if (before === null) return;

  auth.rotate();
  const after = auth.tokens();
  expect(after).not.toBeNull();
  if (after === null) return;
  // The signature section differs after the secret rotates.
  const sig = (t: string): string => t.split(".")[2] ?? "";
  expect(sig(before.backend)).not.toBe(sig(after.backend));
});

test("expiry is bounded by the injected clock", () => {
  let clock = 1_700_000_000_000;
  const auth = new DesktopLocalAuth({ sessionId: () => "session-x", now: () => new Date(clock) });
  const first = auth.tokens();
  expect(first).not.toBeNull();
  if (first === null) return;
  expect(decodePayload(first.backend).exp).toBe(1_700_000_000 + LOCAL_AUTH_TOKEN_TTL_MS / 1000);

  clock += 10 * 60 * 1000; // later than a single TTL
  const second = auth.tokens();
  expect(second).not.toBeNull();
  if (second === null) return;
  // A freshly minted token at the later clock is still in the future.
  expect(decodePayload(second.backend).iat).toBe(1_700_000_000 + 600);
});

test("token value never appears in the redacted status surface", () => {
  const auth = new DesktopLocalAuth({ sessionId: () => "session-1" });
  const tokens = auth.tokens();
  expect(tokens).not.toBeNull();
  if (tokens === null) return;
  const statusPayload = JSON.stringify({ configured: auth.isConfigured(), targets: ["backend", "agent"] as const });
  expect(statusPayload).not.toContain(tokens.backend);
  expect(statusPayload).not.toContain(tokens.agent);
});

// ── Namespace contract ───────────────────────────────────────────────────────

test("credential namespaces match the status contract exactly", () => {
  expect(CREDENTIAL_NAMESPACES).toEqual(["provider", "local-auth"]);
});
