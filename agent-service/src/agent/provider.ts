/**
 * pi-ai 自定义 provider → NovelMind 网关（25.2-05 / D-15）。
 *
 * 所有模型调用都路由到 FastAPI 网关 `POST /api/gateway/v1/chat/completions`
 * （25.2-02，require_gateway_token）。agent-service 内不存在任何 provider key、
 * 路由表或价目表：认证只用 config 里冻结的 `NOVELMIND_GATEWAY_TOKEN`（fail-fast，
 * 绝不落日志），模型 cost 元数据全零（成本权威留在 FastAPI，D-15 rationale）。
 */

import { createProvider, type Model, type Provider } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { config } from "../config.js";

/** 自定义 provider id（仅 agent-service ↔ 网关之间使用，非浏览器可见）。 */
export const GATEWAY_PROVIDER_ID = "novelmind";

/** 唯一可调用模型的逻辑 id（FastAPI 侧映射到真实部署）。 */
export const GATEWAY_MODEL_ID = "reader-chat-default";

/** 网关 baseUrl：`FASTAPI_BASE_URL` + `/api/gateway/v1`（25.2-02 契约）。 */
export function gatewayBaseUrl(): string {
  return `${config.fastApiBaseUrl}/api/gateway/v1`;
}

/** 唯一的逻辑 Model：cost 全零，reasoning false，仅文本输入。 */
export function buildGatewayModel(): Model<"openai-completions"> {
  return {
    id: GATEWAY_MODEL_ID,
    name: "NovelMind Gateway (reader-chat-default)",
    api: "openai-completions",
    provider: GATEWAY_PROVIDER_ID,
    baseUrl: gatewayBaseUrl(),
    reasoning: false,
    input: ["text"],
    // cost 权威在 FastAPI 价格快照；Pi 侧必须保持零（D-15 Anti-Pattern：双价目表）。
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 4_096,
  };
}

/** 构建 NovelMind 网关 provider（openai-completions API）。 */
export function createGatewayProvider(): Provider<"openai-completions"> {
  const model = buildGatewayModel();
  return createProvider({
    id: GATEWAY_PROVIDER_ID,
    name: "NovelMind Gateway",
    baseUrl: gatewayBaseUrl(),
    auth: {
      apiKey: {
        name: "NovelMind Gateway token",
        // 唯一的 key 引用：config.novelmindGatewayToken（环境注入，fail-fast，绝不落日志）。
        resolve: async () => ({ auth: { apiKey: config.novelmindGatewayToken } }),
      },
    },
    models: [model],
    api: openAICompletionsApi(),
  });
}

/**
 * 构建 agent-service 唯一的 ModelRuntime：注册网关 provider 后返回。
 * `modelsPath: null` 使 ModelRuntime 不读磁盘 models.json（无本地路由表/价目表）。
 */
export async function buildGatewayModelRuntime(): Promise<ModelRuntime> {
  const runtime = await ModelRuntime.create({ modelsPath: null });
  runtime.registerNativeProvider(createGatewayProvider());
  return runtime;
}
