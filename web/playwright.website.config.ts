import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "website.spec.ts",
  use: { baseURL: "http://127.0.0.1:8088" },
  webServer: {
    command: "python3 -m http.server 8088 --bind 127.0.0.1 --directory ../website/public",
    url: "http://127.0.0.1:8088",
    reuseExistingServer: true,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" } },
  ],
});
