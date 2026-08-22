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

export interface GatewayRunContext {
  runToken: string;
  novelId: number;
}

/** 网关 baseUrl：`FASTAPI_BASE_URL` + `/api/gateway/v1`（25.2-02 契约）。 */
export function gatewayBaseUrl(): string {
  return `${config.fastApiBaseUrl}/api/gateway/v1`;
}

/**
 * 模型能力声明：env 可配，默认保持历史值。
 * 真实模型能力由 FastAPI 侧 AIModelConfig 决定；这里的声明驱动 pi 的
 * 截断/压缩与 reasoning 处理策略，必须与实际模型一致（NOVELMIND_MODEL_*）。
 */
const MODEL_CONTEXT_WINDOW = Number(process.env.NOVELMIND_MODEL_CONTEXT_WINDOW ?? 128_000);
const MODEL_MAX_OUTPUT_TOKENS = Number(process.env.NOVELMIND_MODEL_MAX_OUTPUT_TOKENS ?? 4_096);
const MODEL_REASONING = process.env.NOVELMIND_MODEL_REASONING === "1";

/** 唯一的逻辑 Model：cost 全零，仅文本输入。 */
export function buildGatewayModel(): Model<"openai-completions"> {
  return {
    id: GATEWAY_MODEL_ID,
    name: "NovelMind Gateway (reader-chat-default)",
    api: "openai-completions",
    provider: GATEWAY_PROVIDER_ID,
    baseUrl: gatewayBaseUrl(),
    reasoning: MODEL_REASONING,
    input: ["text"],
    // cost 权威在 FastAPI 价格快照；Pi 侧必须保持零（D-15 Anti-Pattern：双价目表）。
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: MODEL_CONTEXT_WINDOW,
    maxTokens: MODEL_MAX_OUTPUT_TOKENS,
  };
}

/** 构建 NovelMind 网关 provider（openai-completions API）。 */
export function createGatewayProvider(
  runContext?: GatewayRunContext,
): Provider<"openai-completions"> {
  const model = buildGatewayModel();
  return createProvider({
    id: GATEWAY_PROVIDER_ID,
    name: "NovelMind Gateway",
    baseUrl: gatewayBaseUrl(),
    headers: runContext
      ? {
          "X-NovelMind-Run-Token": runContext.runToken,
          "X-NovelMind-Novel-ID": String(runContext.novelId),
        }
      : undefined,
    auth: {
      apiKey: {
        name: "NovelMind Gateway token",
        // 唯一的 key 引用：config.novelmindGatewayToken（环境注入，fail-fast，绝不落日志）。
        resolve: async () => ({
          auth: {
            apiKey: config.novelmindGatewayToken,
            // ModelRuntime only forwards resolved auth headers to stream options;
            // provider.headers is catalog metadata and is not sufficient here.
            ...(runContext
              ? {
                  headers: {
                    "X-NovelMind-Run-Token": runContext.runToken,
                    "X-NovelMind-Novel-ID": String(runContext.novelId),
                  },
                }
              : {}),
          },
        }),
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
export async function buildGatewayModelRuntime(
  runContext?: GatewayRunContext,
): Promise<ModelRuntime> {
  const runtime = await ModelRuntime.create({ modelsPath: null });
  runtime.registerNativeProvider(createGatewayProvider(runContext));
  return runtime;
}
