import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "operator-access.spec.ts",
  fullyParallel: true,
  use: { baseURL: "http://127.0.0.1:5185", trace: "on-first-retry" },
  webServer: {
    command: "pnpm build:operator && pnpm exec vite preview --config vite.operator.config.ts --host 127.0.0.1 --port 5185 --strictPort",
    url: "http://127.0.0.1:5185/admin/",
    reuseExistingServer: !process.env.CI,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});