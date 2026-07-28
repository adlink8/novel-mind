/**
 * Coverage thresholds aligned with .quality/coverage-policy.yml (D-09).
 * Kept separate from vitest.config.ts to avoid mixed default/named export warnings.
 */
export const QUALITY_THRESHOLDS = {
  overall: { lines: 75, branches: 65 },
  critical: { lines: 85, branches: 75 }, // hooks / store / API
  diffCoverage: 90,
} as const;
