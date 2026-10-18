import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  fullyParallel: false,
  reporter: [["list"], ["json", { outputFile: "test-results/weather-address-ui.json" }]],
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:24105",
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
