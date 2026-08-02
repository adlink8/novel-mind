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
});

export type AppConfig = typeof config;
