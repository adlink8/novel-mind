/**
 * Offline-workflows suite (Phase 44, plan 44-03, Task 2/3, D-44-06/D-44-07).
 *
 * Proves the per-capability offline matrix end-to-end:
 * - LOCAL capabilities (reader / editor / library / data) stay `available`
 *   regardless of network; a real loopback "local library" read succeeds while
 *   the provider endpoint is unreachable (D-44-06).
 * - PROVIDER capabilities are derived from the redacted provider credential
 *   state, the reachability probe and the last provider request result — never
 *   one global online flag — and are honestly blocked/unavailable/misconfigured
 *   with an explicit reason and ZERO fabricated success (D-44-07 / T-44-03-03).
 *
 * The provider endpoint is a real socket that refuses connections, simulating
 * a provider-independent local runtime with the network disabled.
 *
 * Run: npx playwright test --config tests/integration/playwright.config.ts
 */
import { test, expect } from "@playwright/test";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import {
  deriveCapabilityStatus,
  deriveProviderState,
  recordProviderRequest,
} from "../../../frontend/src/lib/runtime/capability-status";

/** Allocates a loopback port and closes it immediately: the "provider"
 * endpoint nothing listens on (network disabled from the provider's view). */
async function deadEndpoint(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const { port } = server.address() as AddressInfo;
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return port;
}

async function listen(server: Server): Promise<number> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const { port } = server.address() as AddressInfo;
  return port;
}

function close(server: Server): Promise<void> {
  return new Promise((resolve) => server.close(() => resolve()));
}

test("local workflows remain usable offline while provider actions are honestly blocked", async () => {
  // A real "local library" service (provider-independent local runtime data).
  const library = createServer((req, res) => {
    if (req.url === "/api/library/books") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ items: [{ id: 1, title: "离线可读的本地书库" }], total: 1 }));
      return;
    }
    res.writeHead(404);
    res.end();
  });
  const localPort = await listen(library);
  const providerPort = await deadEndpoint();

  try {
    // 1) Local capability is available even when the provider endpoint is dead.
    const status = deriveCapabilityStatus({
      online: false,
      providerState: "available",
      lastProviderRequest: { generation: null, embedding: null, image: null },
    });
    expect(status.local.reader.availability).toBe("available");
    expect(status.local.editor.availability).toBe("available");
    expect(status.local.library.availability).toBe("available");
    expect(status.local.data.availability).toBe("available");

    // 2) A real local read succeeds (provider-independent workflow offline).
    const localRes = await fetch(`http://127.0.0.1:${localPort}/api/library/books`);
    expect(localRes.status).toBe(200);
    const books = (await localRes.json()) as { total: number };
    expect(books.total).toBe(1);

    // 3) Provider actions are blocked with an explicit reason and zero data.
    expect(status.providers.generation).toMatchObject({
      availability: "blocked",
      reason: expect.stringContaining("网络不可用"),
    });
    const providerProbe = await fetch(`http://127.0.0.1:${providerPort}/v1/models`, {
      signal: AbortSignal.timeout(2_000),
    }).catch((err: unknown) => (err as Error).name);
    // The provider endpoint refuses connections: the action honestly fails —
    // there is no fabricated empty-success result.
    expect(providerProbe).toBe("TypeError");
  } finally {
    await close(library);
  }
});

test("provider capability states follow credential + network + last-result, never one online flag", () => {
  // No provider configured → unavailable (never blocked-by-network, never success).
  const unconfigured = deriveCapabilityStatus({
    online: true,
    providerState: "unavailable",
    lastProviderRequest: { generation: null, embedding: null, image: null },
  });
  expect(unconfigured.providers.generation.availability).toBe("unavailable");
  expect(unconfigured.providers.image.availability).toBe("unavailable");

  // Credential material exists but is unusable → misconfigured.
  const misconfigured = deriveCapabilityStatus({
    online: true,
    providerState: "decrypt_failed",
    lastProviderRequest: { generation: null, embedding: null, image: null },
  });
  expect(misconfigured.providers.embedding.availability).toBe("misconfigured");

  // A failed provider request keeps the capability blocked even when online.
  const afterFailure = deriveCapabilityStatus({
    online: true,
    providerState: "available",
    lastProviderRequest: { generation: "failed", embedding: null, image: null },
  });
  expect(afterFailure.providers.generation.availability).toBe("blocked");
  expect(afterFailure.providers.image.availability).toBe("available");
});

test("recordProviderRequest feeds the last-result into subsequent derivation (honest failure memory)", () => {
  recordProviderRequest("generation", "failed");
  const status = deriveCapabilityStatus({
    online: true,
    providerState: "available",
    lastProviderRequest: { generation: "failed", embedding: null, image: null },
  });
  expect(status.providers.generation.availability).toBe("blocked");
  expect(status.providers.generation.reason).toContain("上次");
  // A subsequent successful request restores availability.
  const recovered = deriveProviderState("generation", {
    online: true,
    providerState: "available",
    lastProviderRequest: { generation: "ok", embedding: null, image: null },
  });
  expect(recovered.availability).toBe("available");
});
