import { defineConfig } from "@playwright/test";

const baseURL =
  process.env.EA_KNOWLEDGE_BROWSER_URL ?? "http://127.0.0.1:15173";
const target = new URL(baseURL);
if (
  target.protocol !== "http:" ||
  !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) ||
  target.username ||
  target.password ||
  target.pathname !== "/" ||
  target.search ||
  target.hash
) {
  throw new Error("Knowledge browser checks require a plain loopback origin.");
}

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "knowledge-preview.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: "../var/test-results/knowledge-usability/browser/artifacts",
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile:
          "../var/test-results/knowledge-usability/browser/results.json",
      },
    ],
  ],
  use: {
    baseURL,
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    video: "off",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
    launchOptions: { args: ["--disable-gpu"] },
  },
});
