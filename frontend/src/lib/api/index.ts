/**
 * API 客户端 barrel。
 *
 * 保持与旧 `src/lib/api.ts` 完全相同的公共 import 面：
 * 所有 `from "@/lib/api"` 调用零改动即可拿到全部域 API、类型、常量与工具函数。
 */

export * from "./client";
export * from "./novels";
export * from "./extensions";
export * from "./analysis";
export * from "./timeline";
export * from "./characters";
export * from "./relationships";
export * from "./reader-chat";
export * from "./agent";
export * from "./fanfiction";
export * from "./ai-models";
export * from "./settings";
export * from "./usage";
export * from "./search";
export * from "./eval";

export { api } from "./client";
