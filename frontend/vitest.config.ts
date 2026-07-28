import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

/**
 * Coverage thresholds are locked in:
 * - .quality/coverage-policy.yml (D-09)
 * - src/__tests__/quality-thresholds.ts
 * - src/__tests__/coverage-policy.test.ts
 *
 * Local `test:coverage` generates LCOV/JSON artifacts without failing the
 * incomplete suite on full-tree coverage; the policy gate is fail-closed.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "src/__tests__/**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next"],
    // unit-equivalent default; browser e2e uses Playwright (timeout 60s in policy)
    testTimeout: 5000,
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "json", "html"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/__tests__/**",
        "src/**/README.md",
        "node_modules/**",
        ".next/**",
      ],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
