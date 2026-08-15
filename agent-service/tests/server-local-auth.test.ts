import { createHmac } from "node:crypto";
import { describe, expect, it, vi, beforeAll, afterAll } from "vitest";

/**
 * server local-auth gating (44-03 transport wiring).
 *
 * When NOVELMIND_LOCAL_AUTH_SECRET is configured (the Electron main injects it
 * into the owned agent-service environment), EVERY inbound run request must
 * present a valid `novelmind-agent-local` session token first — fail closed
 * (T-44-02-02 / D-44-04). The end-user JWT is forwarded from the second Bearer
 * segment so FastAPI owner checks keep working.
 *
 * The env MUST be set before the frozen config module is evaluated, so it is
 * assigned in `vi.hoisted` (runs before all static imports). Vitest isolates
 * module registries per test file, so the env does not leak into other suites.
 */
const hoistedEnv = vi.hoisted(() => {
  process.env.NOVELMIND_LOCAL_AUTH_SECRET = "test-server-local-auth-secret-0123456789abcdef";
  return { secret: process.env.NOVELMIND_LOCAL_AUTH_SECRET };
});

import { createApp } from "../src/server.js";

const SECRET = hoistedEnv.secret;

function b64url(value: Buffer): string {
  return value.toString("base64url");
}

/** Mint an agent-audience local session token (mirror of the main process). */
function mintSessionToken(overrides: { aud?: string } = {}): string {
  const now = Math.floor(Date.now() / 1000);
  const claims: Record<string, unknown> = {
    iss: "novelmind-desktop-main",
    aud: overrides.aud ?? "novelmind-agent-local",
    iat: now,
    exp: now + 300,
    jti: "jti-test-1",
    sid: "session-abc",
  };
  const header = b64url(Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const payload = b64url(Buffer.from(JSON.stringify(claims)));
  const signingInput = `${header}.${payload}`;
  const sig = createHmac("sha256", SECRET).update(signingInput, "utf8").digest("base64url");
  return `${signingInput}.${sig}`;
}

describe("agent-service local session gating (44-03)", () => {
  let server: ReturnType<typeof createApp>;
  let port: number;
  let fetchMock: ReturnType<typeof vi.fn>;
  let createSessionMock: ReturnType<typeof vi.fn>;

  function startServer() {
    createSessionMock.mockReset();
    server = createApp({
      fetchImpl: fetchMock as unknown as typeof fetch,
      createSessionImpl: createSessionMock as unknown as (opts: unknown) => Promise<never>,
    });
    return new Promise<void>((resolve) => {
      server.listen(0, () => {
        const addr = server.address();
        port = typeof addr === "object" && addr ? addr.port : 0;
        resolve();
      });
    });
  }

  beforeAll(() => {
    fetchMock = vi.fn();
    createSessionMock = vi.fn();
  });

  afterAll(() => {
    server?.close();
  });

  it("401 with zero upstream calls when the session token is missing (fail closed)", async () => {
    await startServer();
    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: {
        authorization: "Bearer end-user-jwt",
        "content-type": "application/json",
      },
      body: JSON.stringify({ question: "q" }),
    });
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("401 for an invalid session token (wrong audience), zero upstream calls", async () => {
    await startServer();
    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: {
        "x-local-auth-token": mintSessionToken({ aud: "novelmind-desktop-local" }),
        authorization: "Bearer end-user-jwt",
        "content-type": "application/json",
      },
      body: JSON.stringify({ question: "q" }),
    });
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts a valid session token and forwards the end-user JWT to FastAPI", async () => {
    await startServer();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ items: [{ id: 5, status: "active" }], total: 1 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: {
        "x-local-auth-token": mintSessionToken(),
        authorization: "Bearer end-user-jwt",
        "content-type": "application/json",
      },
      body: JSON.stringify({ question: "q" }),
    });
    // Skills-versions lookup runs first; the end-user JWT is forwarded as the
    // second Bearer segment (owner isolation stays on FastAPI).
    expect(res.status).not.toBe(401);
    const forwarded = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(forwarded?.headers).toMatchObject({ authorization: "Bearer end-user-jwt" });
  });
});
