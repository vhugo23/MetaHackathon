import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { configDefaults } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    // Playwright browser specs (e2e/**) are run by `playwright test`, never
    // by Vitest — both tools' default file-discovery globs would otherwise
    // match the same *.spec.ts files.
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
