/**
 * agent-service runtime configuration.
 *
 * Reads environment variables at startup and exports a frozen config object.
 * Token values are never logged (Security Domain V6).
 */

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`[agent-service] missing required env var: ${name}`);
  }
  return value;
}

export const config = Object.freeze({
  /** FastAPI NovelMind Core base URL (internal Tool RPC target). */
  fastApiBaseUrl: process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000",
  /** Gateway token for FastAPI Tool RPC calls. Required; fails fast if absent. */
  novelmindGatewayToken: requireEnv("NOVELMIND_GATEWAY_TOKEN"),
  /** HTTP listen port for the agent service. */
  port: Number(process.env.PORT ?? 3100),
  /** 问答按需分析（chat_backfill）queued-run poller 配置。 */
  pollEnabled: process.env.POLL_ENABLED !== "0",
  pollIntervalMs: Number(process.env.POLL_INTERVAL_MS ?? 2000),
  pollConcurrency: Number(process.env.POLL_CONCURRENCY ?? 3),
  pollTimeoutMs: Number(process.env.POLL_TIMEOUT_MS ?? 600_000),
});

export type AppConfig = typeof config;
