import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 60000,
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3000",
    headless: true,
  },
});
