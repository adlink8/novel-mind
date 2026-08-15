import { expect, test } from "@playwright/test";

import {
  CSP_DIRECTIVES,
  cspDirectivesForMode,
} from "../../src/main/security/csp";

test("development CSP permits React dev eval without weakening production", () => {
  const development = cspDirectivesForMode("development");

  expect(development).toContain("script-src 'self' 'unsafe-inline' 'unsafe-eval'");
  expect(CSP_DIRECTIVES).not.toContain("'unsafe-eval'");
  expect(cspDirectivesForMode("production")).toBe(CSP_DIRECTIVES);
});
