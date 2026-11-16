import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.EDITARR_PRODUCTION_BASE_URL;
const certificateArg = process.env.EDITARR_PRODUCTION_CERTIFICATE_ARG;
if (!baseURL || !certificateArg) {
  throw new Error("Use pnpm test:e2e:production so Playwright receives an isolated TLS fixture.");
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "production.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  use: {
    baseURL,
    trace: "retain-on-failure",
    launchOptions: { args: [certificateArg] },
  },
  projects: [{ name: "production-chromium", use: { ...devices["Desktop Chrome"] } }],
});
