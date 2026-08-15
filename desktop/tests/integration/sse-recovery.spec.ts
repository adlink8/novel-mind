/**
 * SSE recovery/replay suite (Phase 44, plan 44-03, Task 1/3).
 *
 * Pure-Node Playwright tests that drive the renderer's SSE module
 * (`runAgentStream` / `streamAgentRun`) against a REAL mock agent-service
 * (`node:http` SSE stream) — no scripted fetch, no renderer — proving the
 * 44-03 honest terminal semantics end-to-end (D-44-05 / T-44-03-01/02):
 *
 * - disconnect + single reconnect resumes exactly once; the authoritative
 *   terminal (cancelled / failed) is preserved — never rewritten to success;
 * - external cancellation propagates as AbortError (cancelled stays cancelled);
 * - non-2xx / connection failures before any frame do NOT reconnect and never
 *   synthesize a success;
 * - duplicate event ids are dropped on replay (no second materialization);
 * - a silent stream end without a terminal resolves as `failed`, never success.
 *
 * Run: npx playwright test --config tests/integration/playwright.config.ts
 */
import { test, expect } from "@playwright/test";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import {
  runAgentStream,
  streamAgentRun,
  isRunTerminal,
  type AgentRunFrame,
} from "../../../frontend/src/lib/sse";

/** Frame writer: flush headers then stream SSE frames one at a time. */
function writeFrames(
  res: import("node:http").ServerResponse,
  frames: Array<AgentRunFrame | null>,
): void {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  res.flushHeaders();
  for (const frame of frames) {
    if (frame === null) {
      res.end(); // silent stream end: connection closes without a terminal frame
      return;
    }
    res.write(`data: ${JSON.stringify(frame)}\n\n`);
  }
  res.end();
}

async function listen(server: Server): Promise<number> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const { port } = server.address() as AddressInfo;
  return port;
}

function close(server: Server): Promise<void> {
  return new Promise((resolve) => server.close(() => resolve()));
}

function runFrame(status: "completed" | "cancelled" | "failed"): AgentRunFrame {
  return { type: "run_end", runId: "7", status };
}

function frame(url: string): string {
  return `${url}/agent/novels/1/runs`;
}

test("mid-stream disconnect reconnects exactly once and keeps the authoritative cancelled terminal", async () => {
  let connections = 0;
  const server = createServer((req, res) => {
    if (req.url !== "/agent/novels/1/runs") {
      res.writeHead(404);
      res.end();
      return;
    }
    connections += 1;
    if (connections === 1) {
      // First connection: emit a delta then reset the TCP connection (RST),
      // simulating a genuine mid-stream network drop. The reset is delayed so
      // the client reliably consumes the delta first (a race here would leave
      // zero frames dispatched and correctly disable reconnection).
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
      });
      res.flushHeaders();
      res.write(`data: ${JSON.stringify({ type: "delta", text: "hi" })}\n\n`);
      setTimeout(() => {
        const socket = res.socket;
        if (socket !== null && typeof (socket as unknown as { reset?: () => void }).reset === "function") {
          (socket as unknown as { reset: () => void }).reset();
        } else {
          socket?.destroy();
        }
      }, 50);
      return;
    }
    // Reconnect serves the authoritative cancelled terminal.
    writeFrames(res, [runFrame("cancelled")]);
  });
  const port = await listen(server);

  try {
    const seen: AgentRunFrame[] = [];
    const terminal = await runAgentStream({
      url: frame(`http://127.0.0.1:${port}`),
      body: { question: "q" },
      onEvent: (f) => seen.push(f),
    });
    expect(terminal).toBe("cancelled");
    expect(connections).toBe(2);
    // Exactly one reconnect: the dropped delta + the authoritative terminal.
    expect(seen.filter(isRunTerminal)).toEqual([runFrame("cancelled")]);
  } finally {
    await close(server);
  }
});

test("cancellation propagates as AbortError and never resolves as success", async () => {
  const ac = new AbortController();
  const server = createServer((_req, res) => {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
    });
    res.flushHeaders();
    res.write(`data: ${JSON.stringify({ type: "delta", text: "x" })}\n\n`);
    // Keep the stream open indefinitely: only the client's abort closes it.
  });
  const port = await listen(server);

  try {
    const promise = runAgentStream({
      url: frame(`http://127.0.0.1:${port}`),
      body: {},
      signal: ac.signal,
      onEvent: () => undefined,
    });
    ac.abort();
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
  } finally {
    await close(server);
  }
});

test("non-2xx response fails without reconnect and without dispatching frames", async () => {
  const server = createServer((_req, res) => {
    res.writeHead(401, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "unauthorized" } }));
  });
  const port = await listen(server);

  try {
    const onEvent = (frame: AgentRunFrame) => {
      throw new Error(`unexpected frame: ${JSON.stringify(frame)}`);
    };
    await expect(
      streamAgentRun(frame(`http://127.0.0.1:${port}`), {}, { onEvent }),
    ).rejects.toMatchObject({ status: 401 });
  } finally {
    await close(server);
  }
});

test("a silent stream end without a terminal resolves as failed (never success)", async () => {
  const server = createServer((_req, res) => {
    writeFrames(res, []);
  });
  const port = await listen(server);

  try {
    const terminal = await runAgentStream({
      url: frame(`http://127.0.0.1:${port}`),
      body: {},
      onEvent: () => undefined,
    });
    expect(terminal).toBe("failed");
  } finally {
    await close(server);
  }
});

test("duplicate event ids are dropped on replay (no second materialization)", async () => {
  const server = createServer((_req, res) => {
    const delta: AgentRunFrame = { type: "delta", text: "a", event_id: "evt-1" };
    writeFrames(res, [
      delta,
      { ...delta, text: "b" },
      runFrame("completed"),
    ]);
  });
  const port = await listen(server);

  try {
    const seen: AgentRunFrame[] = [];
    const terminal = await runAgentStream({
      url: frame(`http://127.0.0.1:${port}`),
      body: {},
      eventId: "run-1",
      onEvent: (f) => seen.push(f),
    });
    expect(terminal).toBe("completed");
    // delta (once) + run_end terminal = 2 frames; the duplicate event_id is dropped.
    expect(seen).toEqual([
      { type: "delta", text: "a", event_id: "evt-1" },
      { type: "run_end", runId: "7", status: "completed" },
    ]);
  } finally {
    await close(server);
  }
});
