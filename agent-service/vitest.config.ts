import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    // config.ts 在模块加载时 fail-fast 校验 gateway token；测试进程统一注入，
    // 免去每个测试文件手动设置。个别测试（如 scaffold）可在用例内删除后验证 fail-fast。
    env: {
      NOVELMIND_GATEWAY_TOKEN: "test-gateway-token",
      FASTAPI_BASE_URL: "http://127.0.0.1:8000",
    },
    include: ["tests/**/*.test.ts", "spikes/**/*.test.mjs"],
    exclude: ["node_modules", "dist"],
    testTimeout: 30000,
  },
});
