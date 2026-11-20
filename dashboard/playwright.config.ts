import { defineConfig, devices } from "@playwright/test";

/** Smoke against a running reporting service that serves the built SPA (task 4.13).
 *
 *   E2E_BASE_URL=http://127.0.0.1:8000 E2E_EMAIL=admin@example.com npm run e2e
 *
 * E2E_EMAIL injects `X-Auth-Request-Email` for a local server whose
 * TRUSTED_PROXY_CIDRS admits the test runner; leave it unset against the deployed
 * host, where oauth2-proxy provides the identity (use a stored session instead). */
const email = process.env.E2E_EMAIL;

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000",
    extraHTTPHeaders: email ? { "X-Auth-Request-Email": email } : undefined,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, grepInvert: /@mobile/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, grep: /@mobile/ },
  ],
});
