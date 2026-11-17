import { defineConfig, devices } from "@playwright/test";

const targetURL = process.env.EDITARR_WEBSITE_URL;
const localURL = "http://127.0.0.1:8088";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "website.spec.ts",
  use: { baseURL: targetURL ?? localURL },
  webServer: targetURL ? undefined : {
    command: "python3 -m http.server 8088 --bind 127.0.0.1 --directory ../website/public",
    url: localURL,
    reuseExistingServer: true,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" } },
  ],
});
