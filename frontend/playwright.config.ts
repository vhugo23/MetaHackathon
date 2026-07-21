import { defineConfig, devices } from "@playwright/test";

const PLAYWRIGHT_BASE_URL = process.env.PLAYWRIGHT_BASE_URL;

if (!PLAYWRIGHT_BASE_URL || PLAYWRIGHT_BASE_URL.trim().length === 0) {
  throw new Error(
    "PLAYWRIGHT_BASE_URL environment variable is required (e.g. http://127.0.0.1:4173) " +
      "and must not be blank. Refusing to silently fall back to localhost or any other default.",
  );
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  outputDir: "./test-results",
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : [["line"]],
  use: {
    baseURL: PLAYWRIGHT_BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
