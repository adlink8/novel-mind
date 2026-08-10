import { createHmac, randomBytes } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { describe, expect, it } from "vitest";
import {
  LOCAL_AUTH_AGENT_AUDIENCE,
  LOCAL_AUTH_ISSUER,
  buildLocalAuthHeader,
  describeLocalAuth,
  extractEndUserToken,
  requireLocalSession,
  verifyLocalSessionToken,
} from "../src/middleware/desktop-local-auth.js";

const TEST_SECRET = "test-agent-local-auth-secret-0123456789abcdef";

function b64url(value: Buffer): string {
  return value.toString("base64url");
}

function mintToken(opts: {
  aud?: string;
  iss?: string;
  secret?: string;
  expOffsetSec?: number;
  iatOffsetSec?: number;
  sid?: string;
  jti?: string;
  tamperSignature?: boolean;
} = {}): string {
  const now = Math.floor(Date.now() / 1000);
  const claims: Record<string, unknown> = {
    iss: opts.iss ?? LOCAL_AUTH_ISSUER,
    aud: opts.aud ?? LOCAL_AUTH_AGENT_AUDIENCE,
    iat: now + (opts.iatOffsetSec ?? 0),
    exp: now + (opts.expOffsetSec ?? 300),
    jti: opts.jti ?? "jti-test-1",
    sid: opts.sid ?? "session-abc",
  };
  const secret = opts.secret ?? TEST_SECRET;
  const header = b64url(Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const payload = b64url(Buffer.from(JSON.stringify(claims)));
  const signingInput = `${header}.${payload}`;
  const sig = opts.tamperSignature
    ? "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    : createHmac("sha256", secret).update(signingInput, "utf8").digest("base64url");
  return `${signingInput}.${sig}`;
}

/** Minimal IncomingMessage-compatible request stub. */
function makeReq(authorization: string | undefined, remoteAddress = "127.0.0.1"): IncomingMessage {
  const headers = authorization === undefined ? {} : { authorization };
  return {
    headers,
    socket: { remoteAddress },
  } as unknown as IncomingMessage;
}

/** Minimal ServerResponse stub that captures the status code. */
function makeRes(): { res: ServerResponse; status: () => number } {
  const state = { status: 200 };
  const res = {
    writeHead: (code: number) => {
      state.status = code;
      return res;
    },
    end: () => res,
  } as unknown as ServerResponse;
  return { res, status: () => state.status };
}

describe("verifyLocalSessionToken", () => {
  it("accepts a valid audience/expiry-bound token", () => {
    const token = mintToken();
    const verified = verifyLocalSessionToken(token, TEST_SECRET);
    expect(verified).toEqual({ sid: "session-abc", jti: "jti-test-1", aud: LOCAL_AUTH_AGENT_AUDIENCE });
  });

  it("rejects a token with the backend audience", () => {
    const token = mintToken({ aud: "novelmind-desktop-local" });
    expect(verifyLocalSessionToken(token, TEST_SECRET)).toBeNull();
  });

  it("rejects a wrong issuer", () => {
    const token = mintToken({ iss: "someone-else" });
    expect(verifyLocalSessionToken(token, TEST_SECRET)).toBeNull();
  });

  it("rejects an expired token", () => {
    const token = mintToken({ expOffsetSec: -600 });
    expect(verifyLocalSessionToken(token, TEST_SECRET)).toBeNull();
  });

  it("rejects a token minted with the wrong secret", () => {
    const token = mintToken({ secret: "another-agent-secret-0123456789abcdef" });
    expect(verifyLocalSessionToken(token, TEST_SECRET)).toBeNull();
  });

  it("rejects a tampered signature", () => {
    const token = mintToken({ tamperSignature: true });
    expect(verifyLocalSessionToken(token, TEST_SECRET)).toBeNull();
  });

  it("rejects a future iat beyond leeway", () => {
    const token = mintToken({ iatOffsetSec: 300 });
    expect(verifyLocalSessionToken(token, TEST_SECRET)).toBeNull();
  });

  it("rejects structurally invalid tokens and short secrets", () => {
    expect(verifyLocalSessionToken("not-a-jwt", TEST_SECRET)).toBeNull();
    expect(verifyLocalSessionToken("a.b", TEST_SECRET)).toBeNull();
    expect(verifyLocalSessionToken(mintToken(), "short")).toBeNull();
  });
});

describe("requireLocalSession", () => {
  it("returns the verified session for a valid loopback request", () => {
    const { res } = makeRes();
    const verified = requireLocalSession(makeReq(`Bearer ${mintToken()}`), res, TEST_SECRET);
    expect(verified).not.toBeNull();
  });

  it("fails closed when the secret is not configured", () => {
    const { res, status } = makeRes();
    const verified = requireLocalSession(makeReq(`Bearer ${mintToken()}`), res, "");
    expect(verified).toBeNull();
    expect(status()).toBe(401);
  });

  it("fails closed when the secret is missing entirely", () => {
    const { res, status } = makeRes();
    const verified = requireLocalSession(makeReq(`Bearer ${mintToken()}`), res, undefined);
    expect(verified).toBeNull();
    expect(status()).toBe(401);
  });

  it("fails closed on a missing Authorization header", () => {
    const { res, status } = makeRes();
    const verified = requireLocalSession(makeReq(undefined), res, TEST_SECRET);
    expect(verified).toBeNull();
    expect(status()).toBe(401);
  });

  it("fails closed on a non-Bearer header", () => {
    const { res, status } = makeRes();
    const verified = requireLocalSession(makeReq("Basic dXNlcjpwYXNz"), res, TEST_SECRET);
    expect(verified).toBeNull();
    expect(status()).toBe(401);
  });

  it("fails closed on an invalid token", () => {
    const { res, status } = makeRes();
    const verified = requireLocalSession(makeReq(`Bearer ${mintToken({ aud: "novelmind-desktop-local" })}`), res, TEST_SECRET);
    expect(verified).toBeNull();
    expect(status()).toBe(401);
  });

  it("fails closed on a non-loopback source even with a valid token", () => {
    const { res, status } = makeRes();
    const verified = requireLocalSession(
      makeReq(`Bearer ${mintToken()}`, "10.0.0.7"),
      res,
      TEST_SECRET,
    );
    expect(verified).toBeNull();
    expect(status()).toBe(401);
  });
});

describe("extractEndUserToken / describeLocalAuth", () => {
  it("extracts the end-user Bearer token for forwarding", () => {
    expect(extractEndUserToken(makeReq("Bearer end-user-jwt"))).toBe("end-user-jwt");
    expect(extractEndUserToken(makeReq("Bearer"))).toBeNull();
    expect(extractEndUserToken(makeReq(undefined))).toBeNull();
    expect(extractEndUserToken(makeReq("Basic xyz"))).toBeNull();
  });

  it("describes config without leaking the secret", () => {
    const label = describeLocalAuth(true);
    expect(label).toBe("configured");
    expect(label).not.toContain(TEST_SECRET);
  });
});

describe("buildLocalAuthHeader (44-03 transport header)", () => {
  it("prepends the session token and keeps the end-user JWT (space-separated)", () => {
    const header = buildLocalAuthHeader("end-user-jwt", "local-sess-token");
    expect(header).toBe("Bearer local-sess-token end-user-jwt");
  });

  it("passes the end-user JWT through when no session token exists (browser mode)", () => {
    expect(buildLocalAuthHeader("end-user-jwt", null)).toBe("Bearer end-user-jwt");
    expect(buildLocalAuthHeader("end-user-jwt", "")).toBe("Bearer end-user-jwt");
  });

  it("returns an empty header when neither credential exists", () => {
    expect(buildLocalAuthHeader(null, null)).toBe("");
    expect(buildLocalAuthHeader("", "")).toBe("");
  });
});
