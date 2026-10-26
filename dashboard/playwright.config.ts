import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  fullyParallel: true,
  workers: 3,
  use: { baseURL: "http://127.0.0.1:5187", trace: "retain-on-failure" },
  webServer: {
    command: "npm run dev -- --port 5187",
    url: "http://127.0.0.1:5187",
    reuseExistingServer: false,
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 960 },
      },
    },
    {
      name: "webkit",
      use: {
        ...devices["Desktop Safari"],
        viewport: { width: 1440, height: 960 },
      },
    },
    {
      name: "phone",
      use: { ...devices["iPhone 13"], defaultBrowserType: "webkit" },
    },
    {
      name: "dark",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 960 },
        colorScheme: "dark",
      },
    },
  ],
});
