import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  testMatch:
    process.env.EA_FRONTEND_SCREENSHOTS_ONLY === "true"
      ? "screenshots.spec.ts"
      : "workflows.spec.ts",
  timeout: 480_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    [
      "json",
      { outputFile: "../var/test-results/phase7-browser/playwright.json" },
    ],
  ],
  outputDir: "../var/test-results/phase7-browser/artifacts",
  use: {
    baseURL: "http://127.0.0.1:5174",
    browserName: "chromium",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 960 },
    timezoneId: "Asia/Shanghai",
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
    trace: "off",
    video: "off",
    screenshot: "only-on-failure",
    launchOptions: { args: ["--disable-gpu"] },
  },
});
